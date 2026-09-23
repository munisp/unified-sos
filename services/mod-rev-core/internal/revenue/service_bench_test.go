package revenue

import (
	"math/big"
	"math/rand"
	"testing"
	"time"

	"github.com/munisp/unified-sos/ledger/splits"
)

// TestUint128DecimalMatchesBigInt is a differential test pinning the
// allocation-light uint128Decimal rendering to the original big.Int
// implementation across edge cases and random values.
func TestUint128DecimalMatchesBigInt(t *testing.T) {
	want := func(id splits.Uint128) string {
		hi := new(big.Int).SetUint64(id.Hi)
		hi.Lsh(hi, 64)
		hi.Add(hi, new(big.Int).SetUint64(id.Lo))
		return hi.String()
	}
	cases := []splits.Uint128{
		{Hi: 0, Lo: 0},
		{Hi: 0, Lo: 1},
		{Hi: 0, Lo: ^uint64(0)},
		{Hi: 1, Lo: 0},
		{Hi: 0, Lo: 10_000_000_000_000_000_000},
		{Hi: ^uint64(0), Lo: ^uint64(0)},
		{Hi: 10_000_000_000_000_000_000, Lo: 0},
	}
	rng := rand.New(rand.NewSource(42))
	for i := 0; i < 10_000; i++ {
		cases = append(cases, splits.Uint128{Hi: rng.Uint64(), Lo: rng.Uint64()})
	}
	for _, c := range cases {
		if got, exp := uint128Decimal(c), want(c); got != exp {
			t.Fatalf("uint128Decimal(%+v) = %q, want %q", c, got, exp)
		}
	}
}

// newBenchService builds a service over the in-memory store + fake ledger
// with the embedded policy seeds and a pinned clock.
func newBenchService(b *testing.B) *Service {
	b.Helper()
	catalog, err := LoadPolicyCatalog("")
	if err != nil {
		b.Fatalf("LoadPolicyCatalog: %v", err)
	}
	fixed := time.Date(2026, 9, 6, 10, 14, 22, 0, time.UTC)
	return NewService(NewInMemoryStore(), catalog, splits.NewInMemoryLedger(),
		WithClock(func() time.Time { return fixed }))
}

var benchAssessmentReq = &AssessmentRequest{
	TaxpayerSTIN:            "NG-NAS-2026-892104",
	MDACode:                 "MDA-BIR-001",
	RevenueHead:             "REV_DIRECT_ASSESSMENT",
	TaxPeriodYear:           2026,
	GrossIncomeKobo:         1_200_000_000,
	AllowableDeductionsKobo: 240_000_000,
	CalculatedTaxKobo:       192_000_000,
}

// BenchmarkSettleBill measures the full settlement hot path (split
// computation, deterministic chain build, idempotent ledger submit) for a
// final exact-amount payment.
func BenchmarkSettleBill(b *testing.B) {
	svc := newBenchService(b)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		b.StopTimer()
		resp, _, err := svc.CreateAssessment("nasarawa", "", benchAssessmentReq)
		if err != nil {
			b.Fatalf("CreateAssessment: %v", err)
		}
		b.StartTimer()
		_, err = svc.SettleBill("nasarawa", &SettlementRequest{
			BillReference: resp.BillReference,
			AmountKobo:    resp.AmountDueKobo,
		})
		if err != nil {
			b.Fatalf("SettleBill: %v", err)
		}
	}
}

func BenchmarkUint128Decimal(b *testing.B) {
	id := splits.DeterministicIDFromRef("BILL-1234-5678-9012")
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = uint128Decimal(id)
	}
}

func BenchmarkDeterministicIDFromRef(b *testing.B) {
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = splits.DeterministicIDFromRef("BILL-1234-5678-9012")
	}
}

func BenchmarkDeterministicID(b *testing.B) {
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = splits.DeterministicID("BILL-1234-5678-9012", "leg-3")
	}
}
