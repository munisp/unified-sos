package geogateway

// Hash-chained audit tests for mod-geospatial-gateway (audit.go + verify endpoint).

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAuditChainValid(t *testing.T) {
	log := NewAuditLog()
	e1 := log.Record("geogateway.job_created", "lagos", "mod-geospatial-gateway", map[string]any{"job_id": "job-00000001"})
	e2 := log.Record("geogateway.job_created", "lagos", "mod-geospatial-gateway", map[string]any{"job_id": "job-00000002"})
	if e1.PrevHash != GenesisPrevHash {
		t.Fatalf("genesis prev_hash = %q, want %q", e1.PrevHash, GenesisPrevHash)
	}
	if e2.PrevHash != e1.EventHash {
		t.Fatalf("broken chain link: e2.prev_hash = %q, want %q", e2.PrevHash, e1.EventHash)
	}
	if errs := log.Verify(); len(errs) != 0 {
		t.Fatalf("chain should verify, got %v", errs)
	}
	if got := len(log.Events("lagos")); got != 2 {
		t.Fatalf("tenant-scoped events = %d, want 2", got)
	}
	if got := len(log.Events("other")); got != 0 {
		t.Fatalf("other tenant events = %d, want 0", got)
	}
}

func TestAuditTamperDetected(t *testing.T) {
	log := NewAuditLog()
	log.Record("geogateway.job_created", "lagos", "mod-geospatial-gateway", nil)
	log.Record("geogateway.job_created", "lagos", "mod-geospatial-gateway", nil)
	// Tamper with the first event's detail in place.
	log.mu.Lock()
	log.events[0].Detail = map[string]any{"tampered": true}
	log.mu.Unlock()
	if errs := log.Verify(); len(errs) == 0 {
		t.Fatal("tampered chain must not verify")
	}
}

func TestAuditVerifyEndpoint(t *testing.T) {
	svc := NewLocalService(nil)
	routes := NewHandler(svc).Routes()

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/states/lagos/audit/verify", nil)
	routes.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("verify endpoint status = %d, want 200", rec.Code)
	}
	var body map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode verify response: %v", err)
	}
	if body["valid"] != true || body["entries"] != float64(0) {
		t.Fatalf("empty log verify = %v, want {valid:true, entries:0}", body)
	}

	// Mutating op (job creation) appends an audit entry.
	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodPost, "/v1/states/lagos/geospatial/jobs",
		strings.NewReader(`{"job_type": "H3_AGGREGATION", "parameters": {"resolution": "8"}}`))
	req.Header.Set("Content-Type", "application/json")
	routes.ServeHTTP(rec, req)
	if rec.Code != http.StatusAccepted {
		t.Fatalf("create job status = %d (%s), want 202", rec.Code, rec.Body.String())
	}

	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodGet, "/api/v1/states/lagos/audit/verify", nil)
	routes.ServeHTTP(rec, req)
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode verify response: %v", err)
	}
	if body["valid"] != true || body["entries"] != float64(1) {
		t.Fatalf("post-mutation verify = %v, want {valid:true, entries:1}", body)
	}
}
