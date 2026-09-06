package revenue

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestNoopEBillNotifierReturnsLocalReference(t *testing.T) {
	a := &Assessment{BillReference: "BILL-0001-0002-0003"}
	ref, err := NoopEBillNotifier{}.IssueBill(a)
	if err != nil {
		t.Fatalf("noop notifier: %v", err)
	}
	if ref != a.BillReference {
		t.Fatalf("noop notifier rewrote the reference: %q", ref)
	}
}

func TestHTTPEBillNotifierIssuesBill(t *testing.T) {
	var got ebillIssueRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/bills" || r.Method != http.MethodPost {
			t.Errorf("unexpected call: %s %s", r.Method, r.URL.Path)
		}
		if err := json.NewDecoder(r.Body).Decode(&got); err != nil {
			t.Errorf("decode request: %v", err)
		}
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(ebillIssueResponse{BillReference: "NIBSS-REF-1"})
	}))
	defer srv.Close()

	n := &HTTPEBillNotifier{BaseURL: srv.URL, Client: srv.Client()}
	ref, err := n.IssueBill(&Assessment{
		BillReference: "BILL-LOCAL-1", AmountDueKobo: 125000,
		TaxpayerSTIN: "N1234567890", MDACode: "MOT", RevenueHead: "PAYE",
	})
	if err != nil {
		t.Fatalf("IssueBill: %v", err)
	}
	if ref != "NIBSS-REF-1" {
		t.Fatalf("gateway reference not adopted: %q", ref)
	}
	if got.AmountMinor != 125000 || got.Currency != "NGN" {
		t.Fatalf("gateway payload mismatch: %+v", got)
	}
}

func TestHTTPEBillNotifierFailsClosed(t *testing.T) {
	// No URL configured.
	if _, err := (&HTTPEBillNotifier{}).IssueBill(&Assessment{}); err == nil {
		t.Fatal("expected error without NIBSS_EBILLS_URL")
	}
	// Gateway non-2xx.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
	}))
	defer srv.Close()
	n := &HTTPEBillNotifier{BaseURL: srv.URL, Client: srv.Client()}
	if _, err := n.IssueBill(&Assessment{BillReference: "B"}); err == nil {
		t.Fatal("expected error on gateway 502")
	}
	// Empty ack reference.
	srvOK := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(ebillIssueResponse{})
	}))
	defer srvOK.Close()
	nOK := &HTTPEBillNotifier{BaseURL: srvOK.URL, Client: srvOK.Client()}
	if _, err := nOK.IssueBill(&Assessment{BillReference: "B"}); err == nil {
		t.Fatal("expected error on empty ack reference")
	}
}

func TestNewEBillNotifierFromEnv(t *testing.T) {
	t.Setenv("REV_CORE_EBILLS", "")
	if _, err := NewEBillNotifierFromEnv(); err != nil {
		t.Fatalf("default should resolve to noop: %v", err)
	}
	t.Setenv("REV_CORE_EBILLS", "nibss")
	t.Setenv("NIBSS_EBILLS_URL", "")
	if _, err := NewEBillNotifierFromEnv(); err == nil {
		t.Fatal("nibss mode without NIBSS_EBILLS_URL must fail closed")
	}
	t.Setenv("NIBSS_EBILLS_URL", "http://ebills.example")
	if _, err := NewEBillNotifierFromEnv(); err != nil {
		t.Fatalf("nibss mode with URL: %v", err)
	}
	t.Setenv("REV_CORE_EBILLS", "bogus")
	if _, err := NewEBillNotifierFromEnv(); err == nil {
		t.Fatal("unknown mode must fail closed")
	}
}
