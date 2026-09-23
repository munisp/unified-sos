package geogateway

import "testing"

// TestEventHashMatchesCanonicalMapForm is a differential test pinning the
// struct-marshalled fast path to the original map-based canonical form
// (sorted keys), so hash-chain semantics are provably unchanged.
func TestEventHashMatchesCanonicalMapForm(t *testing.T) {
	log := NewAuditLog()
	details := []map[string]any{
		nil,
		{},
		{"job_id": "job-00000001"},
		{"html": "<b>&amp;</b>", "n": float64(42), "nested": map[string]any{"z": 1, "a": "x"}},
	}
	for i, d := range details {
		e := log.Record("geogateway.job_created", "lagos", "actor", d)
		if got, want := eventHash(&e), EventPayloadHash(e.payload(), e.PrevHash); got != want {
			t.Fatalf("event %d: eventHash = %q, want canonical map form %q", i, got, want)
		}
	}
	if errs := log.Verify(); len(errs) != 0 {
		t.Fatalf("chain should verify, got %v", errs)
	}
}

// BenchmarkAuditRecord measures the audit hash-chain append hot path.
func BenchmarkAuditRecord(b *testing.B) {
	log := NewAuditLog()
	detail := map[string]any{"job_id": "job-00000001", "tenant": "lagos"}
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		log.Record("geogateway.job_created", "lagos", "mod-geospatial-gateway", detail)
	}
}

// BenchmarkAuditRecordParallel measures append throughput under contention.
func BenchmarkAuditRecordParallel(b *testing.B) {
	log := NewAuditLog()
	detail := map[string]any{"job_id": "job-00000001"}
	b.ReportAllocs()
	b.RunParallel(func(pb *testing.PB) {
		for pb.Next() {
			log.Record("geogateway.job_created", "lagos", "mod-geospatial-gateway", detail)
		}
	})
}
