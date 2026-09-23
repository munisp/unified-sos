package splits

import "testing"

// Benchmarks for the deterministic (sha256-based) ID derivation used on the
// settlement hot path. Semantics are unchanged; these guard against
// performance regressions.

func BenchmarkDeterministicID(b *testing.B) {
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = DeterministicID("BILL-1234-5678-9012", "leg-3")
	}
}

func BenchmarkDeterministicIDFromRef(b *testing.B) {
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = DeterministicIDFromRef("BILL-1234-5678-9012")
	}
}

func BenchmarkChainLegID(b *testing.B) {
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = ChainLegID("BILL-1234-5678-9012", i%8)
	}
}
