package revenue

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/munisp/unified-sos/ledger/splits"
)

// settleRef posts the payments webhook with an explicit provider reference
// (distinct references per logical payment, as a real switch would send).
func settleRef(t *testing.T, srv *httptest.Server, state, billRef string, amount int64, providerRef string) *httptest.ResponseRecorder {
	t.Helper()
	body := fmt.Sprintf(`{"bill_reference": %q, "amount_kobo": %d, "channel": "NIP", "provider_reference": %q}`,
		billRef, amount, providerRef)
	return post(t, srv.URL+"/api/v1/states/"+state+"/revenue/payments/webhook", "", body)
}

func decodeSettlement(t *testing.T, rec *httptest.ResponseRecorder) SettlementResponse {
	t.Helper()
	var resp SettlementResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode settlement: %v (body=%s)", err, rec.Body.String())
	}
	return resp
}

func payerAcct(tenant uint16) splits.Uint128 {
	return splits.MustBuildAccountID(tenant, 0, splits.ClassPayerClearing, splits.Uint128{})
}

func TestPartialPaymentThenFullSettlement(t *testing.T) {
	srv, ledger := newTestServer(t)
	_, a := createContractAssessment(t, srv, "")
	const due = 192_000_000

	// First instalment: no split yet, bill partially paid.
	rec := settleRef(t, srv, "nasarawa", a.BillReference, 100_000_000, "PROV-P1")
	if rec.Code != http.StatusOK {
		t.Fatalf("partial: want 200 got %d (%s)", rec.Code, rec.Body.String())
	}
	p1 := decodeSettlement(t, rec)
	if p1.Status != StatusPartiallyPaid || p1.AmountPaidKobo != 100_000_000 || len(p1.SplitLegs) != 0 {
		t.Fatalf("partial response: %+v", p1)
	}
	crf := splits.MustBuildAccountID(splits.StateNasarawa, 0, splits.ClassConsolidatedRevenueFund, splits.Uint128{})
	if acct := ledger.GetAccount(crf); acct != nil && acct.Balance() != 0 {
		t.Fatalf("no split may execute on a partial payment, CRF balance %d", acct.Balance())
	}

	// Duplicate redelivery of the first instalment is a no-op.
	dup := settleRef(t, srv, "nasarawa", a.BillReference, 100_000_000, "PROV-P1")
	if dup.Code != http.StatusOK {
		t.Fatalf("duplicate partial: %d (%s)", dup.Code, dup.Body.String())
	}
	if got := ledger.MustAccount(payerAcct(splits.StateNasarawa)).Balance(); got != 100_000_000 {
		t.Fatalf("duplicate partial double-applied funds: clearing %d", got)
	}

	// Final instalment settles the FULL assessed amount.
	rec = settleRef(t, srv, "nasarawa", a.BillReference, due-100_000_000, "PROV-P2")
	if rec.Code != http.StatusOK {
		t.Fatalf("final: want 200 got %d (%s)", rec.Code, rec.Body.String())
	}
	p2 := decodeSettlement(t, rec)
	if p2.Status != StatusPaid || p2.AmountPaidKobo != due {
		t.Fatalf("final response: %+v", p2)
	}
	if got := ledger.MustAccount(crf).Balance(); got != due*6800/10000 {
		t.Fatalf("CRF balance: want %d got %d", due*6800/10000, got)
	}
	if got := ledger.MustAccount(payerAcct(splits.StateNasarawa)).Balance(); got != due*200/10000 {
		t.Fatalf("clearing remainder: want %d got %d", due*200/10000, got)
	}
}

func TestOverpaymentRoutesExcessToTaxpayerCredit(t *testing.T) {
	srv, ledger := newTestServer(t)
	_, a := createContractAssessment(t, srv, "")
	const due = 192_000_000
	const excess = 50_000

	rec := settleRef(t, srv, "nasarawa", a.BillReference, due+excess, "PROV-O1")
	if rec.Code != http.StatusOK {
		t.Fatalf("overpay: want 200 got %d (%s)", rec.Code, rec.Body.String())
	}
	resp := decodeSettlement(t, rec)
	if resp.Status != StatusPaid || resp.ExcessCreditedKobo != excess {
		t.Fatalf("overpay response: %+v", resp)
	}
	credit := splits.MustBuildAccountID(splits.StateNasarawa, 0, splits.ClassTaxpayerCredit, splits.Uint128{})
	if got := ledger.MustAccount(credit).Balance(); got != excess {
		t.Fatalf("taxpayer credit balance: want %d got %d", excess, got)
	}
	// The split itself still covers exactly the amount due.
	crf := splits.MustBuildAccountID(splits.StateNasarawa, 0, splits.ClassConsolidatedRevenueFund, splits.Uint128{})
	if got := ledger.MustAccount(crf).Balance(); got != due*6800/10000 {
		t.Fatalf("CRF balance: want %d got %d", due*6800/10000, got)
	}
	if got := ledger.MustAccount(payerAcct(splits.StateNasarawa)).Balance(); got != due*200/10000 {
		t.Fatalf("clearing remainder: want %d got %d", due*200/10000, got)
	}
}

func refundBill(t *testing.T, srv *httptest.Server, state, billRef, officer, approver string) *httptest.ResponseRecorder {
	t.Helper()
	body := fmt.Sprintf(`{"bill_reference": %q, "officer_id": %q, "approver_id": %q, "reason": "taxpayer overassessed"}`,
		billRef, officer, approver)
	return post(t, srv.URL+"/api/v1/states/"+state+"/revenue/refunds", "", body)
}

func TestRefundReversesSettlementChain(t *testing.T) {
	srv, ledger := newTestServer(t)
	_, a := createContractAssessment(t, srv, "")
	const due = 192_000_000
	settleRef(t, srv, "nasarawa", a.BillReference, due, "PROV-R1")

	// Dual control: officer cannot approve their own refund (409).
	if rec := refundBill(t, srv, "nasarawa", a.BillReference, "USR-OFF-1", "USR-OFF-1"); rec.Code != http.StatusConflict {
		t.Fatalf("same-actor refund: want 409 got %d (%s)", rec.Code, rec.Body.String())
	}
	// Missing approver → 400.
	if rec := refundBill(t, srv, "nasarawa", a.BillReference, "USR-OFF-1", ""); rec.Code != http.StatusBadRequest {
		t.Fatalf("missing approver: want 400 got %d", rec.Code)
	}

	rec := refundBill(t, srv, "nasarawa", a.BillReference, "USR-OFF-1", "USR-APP-9")
	if rec.Code != http.StatusOK {
		t.Fatalf("refund: want 200 got %d (%s)", rec.Code, rec.Body.String())
	}
	var resp RefundResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.Status != StatusRefunded || resp.RefundedKobo != due || resp.AuditHash == "" {
		t.Fatalf("refund response: %+v", resp)
	}
	if len(resp.ReversalTransferIDs) != 3 {
		t.Fatalf("reversal legs: %v", resp.ReversalTransferIDs)
	}
	// Beneficiaries are clawed back; funds return to payer clearing.
	crf := splits.MustBuildAccountID(splits.StateNasarawa, 0, splits.ClassConsolidatedRevenueFund, splits.Uint128{})
	if got := ledger.MustAccount(crf).Balance(); got != 0 {
		t.Fatalf("CRF balance after refund: %d", got)
	}
	if got := ledger.MustAccount(payerAcct(splits.StateNasarawa)).Balance(); got != due {
		t.Fatalf("payer clearing after refund: want %d got %d", due, got)
	}
	// A refunded bill cannot be refunded or settled again.
	if rec := refundBill(t, srv, "nasarawa", a.BillReference, "USR-OFF-1", "USR-APP-9"); rec.Code != http.StatusConflict {
		t.Fatalf("double refund: want 409 got %d", rec.Code)
	}
}

func TestRefundRequiresPaidBill(t *testing.T) {
	srv, _ := newTestServer(t)
	_, a := createContractAssessment(t, srv, "")
	if rec := refundBill(t, srv, "nasarawa", a.BillReference, "USR-OFF-1", "USR-APP-9"); rec.Code != http.StatusConflict {
		t.Fatalf("refund of unpaid bill: want 409 got %d (%s)", rec.Code, rec.Body.String())
	}
}

const ogunAssessment = `{
  "taxpayer_stin": "NG-OGU-2026-445566",
  "mda_code": "MDA-BIR-001",
  "revenue_head": "REV_DIRECT_ASSESSMENT",
  "tax_period_year": 2026,
  "gross_income_kobo": 200000000,
  "allowable_deductions_kobo": 0,
  "calculated_tax_kobo": 100000000,
  "metadata": {"lga_code": "LGA-ABK", "assessment_officer_id": "USR-OFF-410"}
}`

func TestCorrectionReallocatesAtomically(t *testing.T) {
	srv, ledger := newTestServer(t)
	rec, a := createAssessmentBody(t, srv, "ogun", ogunAssessment)
	if rec.Code != http.StatusCreated {
		t.Fatalf("create: %d (%s)", rec.Code, rec.Body.String())
	}
	const due = 100_000_000
	settleRef(t, srv, "ogun", a.BillReference, due, "PROV-C1")

	correct := func(officer, approver, head string) *httptest.ResponseRecorder {
		body := fmt.Sprintf(`{"bill_reference": %q, "correct_revenue_head": %q, "officer_id": %q, "approver_id": %q, "reason": "misallocated head"}`,
			a.BillReference, head, officer, approver)
		return post(t, srv.URL+"/api/v1/states/ogun/revenue/corrections", "", body)
	}

	// Dual control enforced (409 on same actor).
	if rec := correct("USR-OFF-1", "USR-OFF-1", "REV_LAND_USE_CHARGE"); rec.Code != http.StatusConflict {
		t.Fatalf("same-actor correction: want 409 got %d (%s)", rec.Code, rec.Body.String())
	}
	// Unknown target head → 400.
	if rec := correct("USR-OFF-1", "USR-APP-9", "REV_MAGIC_TAX"); rec.Code != http.StatusBadRequest {
		t.Fatalf("unknown head: want 400 got %d", rec.Code)
	}

	batchesBefore := len(ledger.Batches())
	rec = correct("USR-OFF-1", "USR-APP-9", "REV_LAND_USE_CHARGE")
	if rec.Code != http.StatusOK {
		t.Fatalf("correction: want 200 got %d (%s)", rec.Code, rec.Body.String())
	}
	var resp CorrectionResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.FromRevenueHead != "REV_DIRECT_ASSESSMENT" || resp.ToRevenueHead != "REV_LAND_USE_CHARGE" {
		t.Fatalf("correction response: %+v", resp)
	}
	if len(resp.AuditHashes) != 2 {
		t.Fatalf("both correction legs must be audited: %v", resp.AuditHashes)
	}
	// Reversal + correct split committed as ONE atomic batch.
	if got := len(ledger.Batches()); got != batchesBefore+1 {
		t.Fatalf("correction must be one batch: %d → %d", batchesBefore, got)
	}
	// LUC split now holds: 75% CRF / 15% MDA / 8% escrow, 2% remainder.
	crf := splits.MustBuildAccountID(splits.StateOgun, 0, splits.ClassConsolidatedRevenueFund, splits.Uint128{})
	if got := ledger.MustAccount(crf).Balance(); got != due*7500/10000 {
		t.Fatalf("CRF after correction: want %d got %d", due*7500/10000, got)
	}
	mda := splits.MustBuildAccountID(splits.StateOgun, 0, splits.ClassMDARetention, splits.Uint128{})
	if got := ledger.MustAccount(mda).Balance(); got != due*1500/10000 {
		t.Fatalf("MDA after correction: want %d got %d", due*1500/10000, got)
	}
	if got := ledger.MustAccount(payerAcct(splits.StateOgun)).Balance(); got != due*200/10000 {
		t.Fatalf("clearing remainder after correction: want %d got %d", due*200/10000, got)
	}
	// Correcting to the (now current) head is a no-op rejection.
	if rec := correct("USR-OFF-1", "USR-APP-9", "REV_LAND_USE_CHARGE"); rec.Code != http.StatusBadRequest {
		t.Fatalf("no-op correction: want 400 got %d", rec.Code)
	}
	// A refund after correction reverses the CORRECTED allocation.
	rec = refundBill(t, srv, "ogun", a.BillReference, "USR-OFF-2", "USR-APP-7")
	if rec.Code != http.StatusOK {
		t.Fatalf("refund after correction: want 200 got %d (%s)", rec.Code, rec.Body.String())
	}
	if got := ledger.MustAccount(crf).Balance(); got != 0 {
		t.Fatalf("CRF after refund-of-corrected: %d", got)
	}
}

func createAssessmentBody(t *testing.T, srv *httptest.Server, state, body string) (*httptest.ResponseRecorder, AssessmentResponse) {
	t.Helper()
	rec := post(t, srv.URL+"/api/v1/states/"+state+"/revenue/assessments", "", body)
	var resp AssessmentResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode assessment: %v (body=%s)", err, rec.Body.String())
	}
	return rec, resp
}
