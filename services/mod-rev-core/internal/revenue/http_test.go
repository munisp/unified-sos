package revenue

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/munisp/unified-sos/ledger/splits"
)

// newTestServer builds a service over the in-memory store + fake ledger
// with the embedded policy seeds and a pinned clock.
func newTestServer(t *testing.T) (*httptest.Server, *splits.InMemoryLedger) {
	t.Helper()
	catalog, err := LoadPolicyCatalog("")
	if err != nil {
		t.Fatalf("LoadPolicyCatalog: %v", err)
	}
	ledger := splits.NewInMemoryLedger()
	fixed := time.Date(2026, 9, 6, 10, 14, 22, 0, time.UTC)
	svc := NewService(NewInMemoryStore(), catalog, ledger, WithClock(func() time.Time { return fixed }))
	srv := httptest.NewServer(NewHandler(svc).Routes())
	t.Cleanup(srv.Close)
	return srv, ledger
}

// contractExample is the exact request example from
// contracts/openapi/revenue-assessments.yaml.
const contractExample = `{
  "taxpayer_stin": "NG-NAS-2026-892104",
  "mda_code": "MDA-BIR-001",
  "revenue_head": "REV_DIRECT_ASSESSMENT",
  "tax_period_year": 2026,
  "gross_income_kobo": 1200000000,
  "allowable_deductions_kobo": 240000000,
  "calculated_tax_kobo": 192000000,
  "metadata": {"lga_code": "LGA-KARU", "assessment_officer_id": "USR-OFF-410"}
}`

func post(t *testing.T, url, idemKey, body string) *httptest.ResponseRecorder {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, url, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	if idemKey != "" {
		req.Header.Set("Idempotency-Key", idemKey)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	rec := httptest.NewRecorder()
	rec.Code = resp.StatusCode
	var buf bytes.Buffer
	buf.ReadFrom(resp.Body)
	rec.Body = &buf
	return rec
}

func createContractAssessment(t *testing.T, srv *httptest.Server, idemKey string) (*httptest.ResponseRecorder, AssessmentResponse) {
	t.Helper()
	rec := post(t, srv.URL+"/api/v1/states/nasarawa/revenue/assessments", idemKey, contractExample)
	var resp AssessmentResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode response: %v (body=%s)", err, rec.Body.String())
	}
	return rec, resp
}

func TestCreateAssessmentHappyPath(t *testing.T) {
	srv, _ := newTestServer(t)
	rec, resp := createContractAssessment(t, srv, "idem-happy-1")

	if rec.Code != http.StatusCreated {
		t.Fatalf("status: want 201 got %d (%s)", rec.Code, rec.Body.String())
	}
	if resp.AssessmentID != "ASM-NAS-2026-0000001" {
		t.Fatalf("assessment_id: got %q", resp.AssessmentID)
	}
	if !strings.HasPrefix(resp.BillReference, "BILL-") {
		t.Fatalf("bill_reference: got %q", resp.BillReference)
	}
	if resp.TigerbeetleTransferPendingID == "" || resp.TigerbeetleTransferPendingID == "0" {
		t.Fatalf("pending transfer ID missing: %q", resp.TigerbeetleTransferPendingID)
	}
	if resp.AmountDueKobo != 192000000 {
		t.Fatalf("amount_due_kobo: got %d", resp.AmountDueKobo)
	}
	wantQR := "https://pay.nasarawa.gov.ng/pay/" + resp.BillReference
	if resp.PaymentQRPayload != wantQR {
		t.Fatalf("payment_qr_payload: want %q got %q", wantQR, resp.PaymentQRPayload)
	}
	if resp.CreatedAt != "2026-09-06T10:14:22Z" {
		t.Fatalf("created_at: got %q", resp.CreatedAt)
	}
}

func TestCreateAssessmentValidationErrors(t *testing.T) {
	srv, _ := newTestServer(t)
	url := srv.URL + "/api/v1/states/nasarawa/revenue/assessments"

	cases := map[string]struct {
		url  string
		body string
		want int
		code string
	}{
		"bad stin format": {url, strings.Replace(contractExample,
			"NG-NAS-2026-892104", "NAS-2026-892104", 1), 400, "INVALID_STIN"},
		"cross-tenant stin": {srv.URL + "/api/v1/states/ogun/revenue/assessments",
			contractExample, 400, "INVALID_STIN"},
		"unknown state": {srv.URL + "/api/v1/states/kano/revenue/assessments",
			contractExample, 400, "INVALID_STATE_TENANT"},
		"unknown revenue head": {url, strings.Replace(contractExample,
			"REV_DIRECT_ASSESSMENT", "REV_MAGIC_TAX", 1), 400, "UNKNOWN_REVENUE_HEAD"},
		"amount mismatch": {url, strings.Replace(contractExample,
			`"calculated_tax_kobo": 192000000`, `"calculated_tax_kobo": 999999999`, 1), 400, "AMOUNT_MISMATCH"},
		"deductions exceed gross": {url, strings.Replace(contractExample,
			`"allowable_deductions_kobo": 240000000`, `"allowable_deductions_kobo": 2400000000`, 1), 400, "AMOUNT_MISMATCH"},
		"early tax period": {url, strings.Replace(contractExample,
			`"tax_period_year": 2026`, `"tax_period_year": 2019`, 1), 400, "INVALID_TAX_PERIOD"},
		"malformed json": {url, `{"taxpayer_stin":`, 400, "MALFORMED_JSON"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			rec := post(t, tc.url, "", tc.body)
			if rec.Code != tc.want {
				t.Fatalf("status: want %d got %d (%s)", tc.want, rec.Code, rec.Body.String())
			}
			var er ErrorResponse
			if err := json.Unmarshal(rec.Body.Bytes(), &er); err != nil {
				t.Fatalf("decode error body: %v", err)
			}
			if er.Error.Code != tc.code {
				t.Fatalf("error code: want %s got %s", tc.code, er.Error.Code)
			}
		})
	}
}

func TestIdempotentBillIssuance(t *testing.T) {
	srv, _ := newTestServer(t)

	rec1, resp1 := createContractAssessment(t, srv, "idem-key-42")
	if rec1.Code != http.StatusCreated {
		t.Fatalf("first: %d", rec1.Code)
	}
	// Same key + same payload → 200 replay with the same bill.
	rec2, resp2 := createContractAssessment(t, srv, "idem-key-42")
	if rec2.Code != http.StatusOK {
		t.Fatalf("replay: want 200 got %d (%s)", rec2.Code, rec2.Body.String())
	}
	if resp1.BillReference != resp2.BillReference || resp1.AssessmentID != resp2.AssessmentID {
		t.Fatalf("replay must return same bill: %q vs %q", resp1.BillReference, resp2.BillReference)
	}

	// Same key + different payload → 409.
	different := strings.Replace(contractExample, `"calculated_tax_kobo": 192000000`, `"calculated_tax_kobo": 190000000`, 1)
	rec3 := post(t, srv.URL+"/api/v1/states/nasarawa/revenue/assessments", "idem-key-42", different)
	if rec3.Code != http.StatusConflict {
		t.Fatalf("conflicting reuse: want 409 got %d (%s)", rec3.Code, rec3.Body.String())
	}

	// No key → always a fresh bill.
	_, resp4 := createContractAssessment(t, srv, "")
	if resp4.BillReference == resp1.BillReference {
		t.Fatal("unkeyed request must mint a new bill reference")
	}
}

func TestGetAssessment(t *testing.T) {
	srv, _ := newTestServer(t)
	_, created := createContractAssessment(t, srv, "")

	rec, err := http.Get(srv.URL + "/api/v1/states/nasarawa/revenue/assessments/" + created.AssessmentID)
	if err != nil {
		t.Fatal(err)
	}
	rec.Body.Close()
	if rec.StatusCode != http.StatusOK {
		t.Fatalf("get: want 200 got %d", rec.StatusCode)
	}

	// Cross-tenant read is invisible (tenant isolation).
	rec2, err := http.Get(srv.URL + "/api/v1/states/ogun/revenue/assessments/" + created.AssessmentID)
	if err != nil {
		t.Fatal(err)
	}
	rec2.Body.Close()
	if rec2.StatusCode != http.StatusNotFound {
		t.Fatalf("cross-tenant get: want 404 got %d", rec2.StatusCode)
	}
}

func TestHealthz(t *testing.T) {
	srv, _ := newTestServer(t)
	resp, err := http.Get(srv.URL + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("healthz: %d", resp.StatusCode)
	}
	var body map[string]string
	json.NewDecoder(resp.Body).Decode(&body)
	if body["status"] != "ok" {
		t.Fatalf("healthz body: %v", body)
	}
}

// settle posts the payments webhook for a bill.
func settle(t *testing.T, srv *httptest.Server, state, billRef string, amount int64) *httptest.ResponseRecorder {
	t.Helper()
	body := fmt.Sprintf(`{"bill_reference": %q, "amount_kobo": %d, "channel": "NIP", "provider_reference": "NIBSS-TEST-1"}`, billRef, amount)
	return post(t, srv.URL+"/api/v1/states/"+state+"/revenue/payments/webhook", "", body)
}

func TestSettlementExecutesStatutorySplit(t *testing.T) {
	srv, ledger := newTestServer(t)
	_, a := createContractAssessment(t, srv, "")

	rec := settle(t, srv, "nasarawa", a.BillReference, a.AmountDueKobo)
	if rec.Code != http.StatusOK {
		t.Fatalf("settle: want 200 got %d (%s)", rec.Code, rec.Body.String())
	}
	var resp SettlementResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.Status != StatusPaid || resp.AssessmentID != a.AssessmentID {
		t.Fatalf("settlement response: %+v", resp)
	}

	// Nasarawa REV_DIRECT_ASSESSMENT seed: 68% CRF / 20% MDA / 10%
	// concessionaire INSTANT + 2% LG END_OF_MONTH.
	const gross = 192_000_000
	want := map[string]uint64{
		"STATE_CONSOLIDATED_REVENUE_FUND": gross * 6800 / 10000,
		"MDA_RETENTION_ACCOUNT":           gross * 2000 / 10000,
		"PPP_TECH_CONCESSIONAIRE_ESCROW":  gross * 1000 / 10000,
		"LOCAL_GOVERNMENT_SHARE_POOL":     gross * 200 / 10000,
	}
	var instantTotal uint64
	instant, monthEnd := 0, 0
	for _, leg := range resp.SplitLegs {
		if leg.AmountKobo != want[leg.Beneficiary] {
			t.Fatalf("leg %s: want %d got %d", leg.Beneficiary, want[leg.Beneficiary], leg.AmountKobo)
		}
		if leg.Timing == "INSTANT" {
			instant++
			instantTotal += leg.AmountKobo
		} else {
			monthEnd++
		}
	}
	if instant != 3 || monthEnd != 1 {
		t.Fatalf("legs: %d instant %d month-end", instant, monthEnd)
	}
	if instantTotal+resp.ClearingRemainderKobo != gross {
		t.Fatalf("instant %d + remainder %d != gross %d", instantTotal, resp.ClearingRemainderKobo, gross)
	}
	if resp.ClearingRemainderKobo != gross*200/10000 {
		t.Fatalf("remainder: want %d got %d", gross*200/10000, resp.ClearingRemainderKobo)
	}

	// Ledger balances reflect the executed chain.
	tenant := splits.StateNasarawa
	crf := splits.MustBuildAccountID(tenant, 0, splits.ClassConsolidatedRevenueFund, splits.Uint128{})
	if got := ledger.MustAccount(crf).Balance(); got != int64(want["STATE_CONSOLIDATED_REVENUE_FUND"]) {
		t.Fatalf("CRF ledger balance: want %d got %d", want["STATE_CONSOLIDATED_REVENUE_FUND"], got)
	}
	payer := splits.MustBuildAccountID(tenant, 0, splits.ClassPayerClearing, splits.Uint128{})
	if got := ledger.MustAccount(payer).Balance(); got != int64(resp.ClearingRemainderKobo) {
		t.Fatalf("clearing balance: want %d got %d", resp.ClearingRemainderKobo, got)
	}
	// LG pool is month-end: no instant credit.
	lg := splits.MustBuildAccountID(tenant, 0, splits.ClassLocalGovernmentSharePool, splits.Uint128{})
	if acct := ledger.GetAccount(lg); acct != nil && acct.Balance() != 0 {
		t.Fatalf("month-end leg must not settle instantly, balance %d", acct.Balance())
	}

	// Double settlement is rejected (no double split).
	if rec := settle(t, srv, "nasarawa", a.BillReference, a.AmountDueKobo); rec.Code != http.StatusConflict {
		t.Fatalf("re-settle: want 409 got %d", rec.Code)
	}
}

func TestSettlementValidation(t *testing.T) {
	srv, _ := newTestServer(t)
	_, a := createContractAssessment(t, srv, "")

	if rec := settle(t, srv, "nasarawa", a.BillReference, a.AmountDueKobo-1); rec.Code != http.StatusBadRequest {
		t.Fatalf("underpayment: want 400 got %d (%s)", rec.Code, rec.Body.String())
	}
	if rec := settle(t, srv, "nasarawa", "BILL-0000-0000-0000", 100); rec.Code != http.StatusNotFound {
		t.Fatalf("unknown bill: want 404 got %d", rec.Code)
	}
	if rec := settle(t, srv, "ogun", a.BillReference, a.AmountDueKobo); rec.Code != http.StatusNotFound {
		t.Fatalf("cross-tenant settle: want 404 got %d", rec.Code)
	}
}
