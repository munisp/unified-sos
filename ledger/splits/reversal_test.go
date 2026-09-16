package splits

import (
	"testing"
)

func TestDeterministicIDStableAndDistinct(t *testing.T) {
	a1 := DeterministicID("BILL-0001", "leg-0")
	a2 := DeterministicID("BILL-0001", "leg-0")
	if a1 != a2 {
		t.Fatal("same key+leg must derive the same ID")
	}
	if a1.IsZero() {
		t.Fatal("derived ID must be non-zero")
	}
	if DeterministicID("BILL-0001", "leg-1") == a1 {
		t.Fatal("different legs must derive different IDs")
	}
	if DeterministicID("BILL-0002", "leg-0") == a1 {
		t.Fatal("different keys must derive different IDs")
	}
	if DeterministicIDFromRef("BILL-0001") == DeterministicIDFromRef("BILL-0002") {
		t.Fatal("different bill refs must derive different bill IDs")
	}
	if ChainLegID("k", 0) != DeterministicID("k", "leg-0") {
		t.Fatal("ChainLegID scheme mismatch")
	}
	if ReversalLegID("k", 0) != DeterministicID("k", "leg-0|reversal") {
		t.Fatal("ReversalLegID must be sha256(key‖leg‖reversal)")
	}
}

func TestDeterministicChainIDs(t *testing.T) {
	_, plan, params, _ := newChainFixture(100_000)
	params.IdempotencyKey = "BILL-DET-1"
	chain1, err := BuildAtomicChain(plan, params)
	if err != nil {
		t.Fatal(err)
	}
	chain2, err := BuildAtomicChain(plan, params)
	if err != nil {
		t.Fatal(err)
	}
	for i := range chain1 {
		if chain1[i].ID != chain2[i].ID {
			t.Fatalf("leg %d: deterministic build must reproduce IDs", i)
		}
		if chain1[i].ID != ChainLegID("BILL-DET-1", i) {
			t.Fatalf("leg %d: ID not derived from the idempotency key", i)
		}
	}
}

func TestReversalChainMirrorSemantics(t *testing.T) {
	ledger, plan, params, payer := newChainFixture(100_000)
	params.IdempotencyKey = "BILL-REV-1"
	chain, err := ExecuteAtomicSplit(ledger, plan, params)
	if err != nil {
		t.Fatal(err)
	}
	crf := MustBuildAccountID(StateOgun, 0, ClassConsolidatedRevenueFund, Uint128{})

	reversal, err := ExecuteReversalChain(ledger, chain, params.IdempotencyKey)
	if err != nil {
		t.Fatal(err)
	}
	if len(reversal) != len(chain) {
		t.Fatalf("reversal legs: want %d got %d", len(chain), len(reversal))
	}
	for i, tr := range reversal {
		if tr.DebitAccountID != chain[i].CreditAccountID || tr.CreditAccountID != chain[i].DebitAccountID {
			t.Fatalf("leg %d not mirrored", i)
		}
		if tr.ID != ReversalLegID(params.IdempotencyKey, i) {
			t.Fatalf("leg %d reversal ID not deterministic", i)
		}
		wantLinked := i < len(reversal)-1
		if tr.Flags.Linked != wantLinked {
			t.Fatalf("leg %d Linked=%v want %v", i, tr.Flags.Linked, wantLinked)
		}
	}
	if got := ledger.MustAccount(crf).Balance(); got != 0 {
		t.Fatalf("CRF after reversal: %d", got)
	}
	if got := ledger.MustAccount(payer).Balance(); got != 100_000 {
		t.Fatalf("payer after reversal: want 100000 got %d", got)
	}

	// Replaying the reversal is a no-op (deterministic IDs already exist).
	if _, err := ExecuteReversalChain(ledger, chain, params.IdempotencyKey); err != nil {
		t.Fatalf("reversal replay: %v", err)
	}
	if got := ledger.MustAccount(payer).Balance(); got != 100_000 {
		t.Fatalf("reversal replay double-applied: payer %d", got)
	}
}

func TestSubmitChainIdempotentAcceptsReplay(t *testing.T) {
	ledger, plan, params, payer := newChainFixture(100_000)
	params.IdempotencyKey = "BILL-IDEM-1"
	chain, err := BuildAtomicChain(plan, params)
	if err != nil {
		t.Fatal(err)
	}
	if err := SubmitChainIdempotent(ledger, chain); err != nil {
		t.Fatal(err)
	}
	// Resubmission of the identical deterministic chain reports exists on
	// every leg and must be accepted as an already-committed retry.
	if err := SubmitChainIdempotent(ledger, chain); err != nil {
		t.Fatalf("idempotent resubmit: %v", err)
	}
	if got := ledger.MustAccount(payer).Balance(); got != 0 {
		t.Fatalf("resubmit double-applied: payer %d", got)
	}
}
