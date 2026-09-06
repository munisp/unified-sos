package splits

import (
	"errors"
	"testing"
)

// newChainFixture builds a funded fake ledger with the four Ogun chart
// accounts and a 65/20/15 split plan over gross.
func newChainFixture(gross uint64) (*InMemoryLedger, *SplitPlan, ChainParams, Uint128) {
	ledger := NewInMemoryLedger()
	params := ChainParams{StateTenant: StateOgun, Ledger: LedgerNGSovereign, BillID: NewID()}
	payer := MustBuildAccountID(StateOgun, 0, ClassPayerClearing, Uint128{})
	for _, class := range []uint16{ClassPayerClearing, ClassConsolidatedRevenueFund, ClassMDARetention, ClassConcessionaireEscrow} {
		ledger.CreateAccount(MustBuildAccountID(StateOgun, 0, class, Uint128{}))
	}
	ledger.SeedAccount(payer, gross)
	rules := []SplitRule{
		instantRule(BeneficiaryConsolidatedRevenueFund, ClassConsolidatedRevenueFund, 6500),
		instantRule(BeneficiaryMDARetention, ClassMDARetention, 2000),
		instantRule(BeneficiaryConcessionaireEscrow, ClassConcessionaireEscrow, 1500),
	}
	plan, err := ComputeSplit(gross, rules)
	if err != nil {
		panic(err)
	}
	return ledger, plan, params, payer
}

func TestBuildAtomicChainLinkedFlags(t *testing.T) {
	_, plan, params, payer := newChainFixture(100_000)
	chain, err := BuildAtomicChain(plan, params)
	if err != nil {
		t.Fatal(err)
	}
	if len(chain) != 3 {
		t.Fatalf("chain length: want 3 got %d", len(chain))
	}
	// All legs except the last are Linked; the last closes the chain.
	for i, tr := range chain {
		wantLinked := i < len(chain)-1
		if tr.Flags.Linked != wantLinked {
			t.Fatalf("leg %d: Linked=%v want %v", i, tr.Flags.Linked, wantLinked)
		}
		if tr.DebitAccountID != payer {
			t.Fatalf("leg %d: debit must be payer clearing", i)
		}
		if tr.UserData != params.BillID {
			t.Fatalf("leg %d: UserData must carry bill ID", i)
		}
	}
	// Leg amounts follow the plan.
	for i, leg := range plan.InstantLegs {
		if chain[i].Amount != leg.Amount {
			t.Fatalf("leg %d amount: want %d got %d", i, leg.Amount, chain[i].Amount)
		}
	}
}

func TestExecuteAtomicSplitHappyPath(t *testing.T) {
	ledger, plan, params, payer := newChainFixture(100_000)
	if _, err := ExecuteAtomicSplit(ledger, plan, params); err != nil {
		t.Fatal(err)
	}
	if got := ledger.MustAccount(payer).Balance(); got != 0 {
		t.Fatalf("payer clearing should be emptied, got %d", got)
	}
	crf := MustBuildAccountID(StateOgun, 0, ClassConsolidatedRevenueFund, Uint128{})
	if got := ledger.MustAccount(crf).Balance(); got != 65_000 {
		t.Fatalf("CRF balance: want 65000 got %d", got)
	}
}

func TestLinkedChainAtomicityOnFailure(t *testing.T) {
	// Underfund the payer clearing account so the final leg overdraws:
	// the whole chain must fail and NOTHING may commit.
	ledger := NewInMemoryLedger()
	params := ChainParams{StateTenant: StateOgun, Ledger: LedgerNGSovereign, BillID: NewID()}
	payer := MustBuildAccountID(StateOgun, 0, ClassPayerClearing, Uint128{})
	crf := MustBuildAccountID(StateOgun, 0, ClassConsolidatedRevenueFund, Uint128{})
	mda := MustBuildAccountID(StateOgun, 0, ClassMDARetention, Uint128{})
	for _, id := range []Uint128{payer, crf, mda} {
		ledger.CreateAccount(id)
	}
	ledger.SeedAccount(payer, 70_000) // enough for leg 1 only

	plan, err := ComputeSplit(100_000, []SplitRule{
		instantRule(BeneficiaryConsolidatedRevenueFund, ClassConsolidatedRevenueFund, 6500),
		instantRule(BeneficiaryMDARetention, ClassMDARetention, 3500),
	})
	if err != nil {
		t.Fatal(err)
	}
	_, err = ExecuteAtomicSplit(ledger, plan, params)
	if err == nil {
		t.Fatal("expected chain failure")
	}
	var rejected *ErrBatchRejected
	if !errors.As(err, &rejected) {
		t.Fatalf("want ErrBatchRejected, got %v", err)
	}
	// Every leg must be rejected: one with the underlying cause, the
	// other(s) with linked_event_failed.
	if len(rejected.Results) != 2 {
		t.Fatalf("want 2 per-transfer failures, got %v", rejected.Results)
	}
	// Nothing committed.
	if got := ledger.MustAccount(payer).Balance(); got != 70_000 {
		t.Fatalf("payer balance changed: %d", got)
	}
	if got := ledger.MustAccount(crf).Balance(); got != 0 {
		t.Fatalf("CRF credited despite chain failure: %d", got)
	}
}

func TestLinkedChainOpenRejected(t *testing.T) {
	ledger, plan, params, _ := newChainFixture(100_000)
	chain, err := BuildAtomicChain(plan, params)
	if err != nil {
		t.Fatal(err)
	}
	chain[len(chain)-1].Flags.Linked = true // corrupt: batch ends open
	res, err := ledger.CreateTransfers(chain)
	if err != nil {
		t.Fatal(err)
	}
	if len(res) == 0 {
		t.Fatal("expected linked_event_chain_open rejection")
	}
	found := false
	for _, r := range res {
		if r.Result == ResultLinkedEventChainOpen {
			found = true
		}
	}
	if !found {
		t.Fatalf("want linked_event_chain_open in %v", res)
	}
}

func TestUnknownAccountRejectsChain(t *testing.T) {
	ledger := NewInMemoryLedger()
	params := ChainParams{StateTenant: StateOgun, Ledger: LedgerNGSovereign, BillID: NewID()}
	payer := MustBuildAccountID(StateOgun, 0, ClassPayerClearing, Uint128{})
	ledger.CreateAccount(payer)
	ledger.SeedAccount(payer, 100_000)
	// CRF account deliberately NOT created.
	plan, err := ComputeSplit(100_000, []SplitRule{
		instantRule(BeneficiaryConsolidatedRevenueFund, ClassConsolidatedRevenueFund, 10_000),
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := ExecuteAtomicSplit(ledger, plan, params); err == nil {
		t.Fatal("expected rejection for missing beneficiary account")
	}
	if got := ledger.MustAccount(payer).Balance(); got != 100_000 {
		t.Fatalf("payer balance changed: %d", got)
	}
}

func TestFakeLedgerStandaloneTransferAndAudit(t *testing.T) {
	ledger := NewInMemoryLedger()
	a, b := Uint128{Lo: 1}, Uint128{Lo: 2}
	ledger.CreateAccount(a)
	ledger.CreateAccount(b)
	ledger.SeedAccount(a, 500)
	res, err := ledger.CreateTransfers([]Transfer{
		{ID: NewID(), DebitAccountID: a, CreditAccountID: b, Amount: 200, Ledger: LedgerNGSovereign, Code: 101},
		{ID: NewID(), DebitAccountID: a, CreditAccountID: b, Amount: 0, Ledger: LedgerNGSovereign, Code: 101},
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(res) != 1 || res[0].Index != 1 || res[0].Result != ResultZeroAmount {
		t.Fatalf("unexpected results: %v", res)
	}
	if got := ledger.MustAccount(b).Balance(); got != 200 {
		t.Fatalf("balance: want 200 got %d", got)
	}
	if got := len(ledger.Batches()); got != 1 {
		t.Fatalf("audit log: want 1 batch got %d", got)
	}
}

func TestDuplicateTransferIDRejected(t *testing.T) {
	ledger := NewInMemoryLedger()
	a, b := Uint128{Lo: 1}, Uint128{Lo: 2}
	ledger.CreateAccount(a)
	ledger.CreateAccount(b)
	ledger.SeedAccount(a, 500)
	tr := Transfer{ID: NewID(), DebitAccountID: a, CreditAccountID: b, Amount: 100, Ledger: LedgerNGSovereign, Code: 101}
	if res, err := ledger.CreateTransfers([]Transfer{tr}); err != nil || len(res) != 0 {
		t.Fatalf("first submit: res=%v err=%v", res, err)
	}
	res, err := ledger.CreateTransfers([]Transfer{tr})
	if err != nil || len(res) != 1 || res[0].Result != ResultExists {
		t.Fatalf("duplicate: res=%v err=%v", res, err)
	}
}
