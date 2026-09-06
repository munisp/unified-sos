package splits

import "testing"

// This file defines the ledger contract suite: every LedgerClient
// implementation (the in-memory fake and the production TigerBeetle
// adapter) must satisfy identical observable semantics. The in-memory
// fake runs here on every build; the TigerBeetle run lives in
// contract_tigerbeetle_test.go behind `-tags "tigerbeetle integration"`.

// contractProvisioner is the account setup seam shared by the fake and
// the production adapter (CreateAccount/SeedAccount).
type contractProvisioner interface {
	CreateAccount(id Uint128)
	SeedAccount(id Uint128, creditsPosted uint64)
}

// LedgerFactory returns a fresh, empty ledger per subtest.
type LedgerFactory func(t *testing.T) LedgerClient

// RunLedgerContractTests runs the shared LedgerClient contract suite.
func RunLedgerContractTests(t *testing.T, factory LedgerFactory) {
	t.Helper()

	// contractAccounts are freshly provisioned per subtest. Entities are
	// unique per call so the suite is also safe to run against a shared
	// live cluster (accounts accumulate balance across runs otherwise).
	type contractAccounts struct {
		a, b, c Uint128
	}

	setup := func(t *testing.T, seed uint64) (LedgerClient, contractAccounts) {
		t.Helper()
		l := factory(t)
		prov, ok := l.(contractProvisioner)
		if !ok {
			t.Fatalf("contract ledger %T does not implement the account provisioning seam", l)
		}
		entity := NewID().Lo
		accts := contractAccounts{
			a: MustBuildAccountID(StateOsun, 0, ClassPayerClearing, EntityFromUint64(entity)),
			b: MustBuildAccountID(StateOsun, 0, ClassConsolidatedRevenueFund, EntityFromUint64(entity)),
			c: MustBuildAccountID(StateOsun, 0, ClassMDARetention, EntityFromUint64(entity)),
		}
		prov.SeedAccount(accts.a, seed)
		prov.CreateAccount(accts.b)
		prov.CreateAccount(accts.c)
		return l, accts
	}

	expectResults := func(t *testing.T, got []TransferResult, want []TransferResult) {
		t.Helper()
		if len(got) != len(want) {
			t.Fatalf("results = %v, want %v", got, want)
		}
		for i := range want {
			if got[i] != want[i] {
				t.Fatalf("results = %v, want %v", got, want)
			}
		}
	}

	t.Run("single transfer commits", func(t *testing.T) {
		l, accts := setup(t, 1_000)
		res, err := l.CreateTransfers([]Transfer{{
			ID: NewID(), DebitAccountID: accts.a, CreditAccountID: accts.b,
			Amount: 400, Ledger: LedgerNGSovereign, Code: 101,
		}})
		if err != nil {
			t.Fatalf("CreateTransfers: %v", err)
		}
		expectResults(t, res, nil)
	})

	t.Run("duplicate transfer ID rejected with exists", func(t *testing.T) {
		l, accts := setup(t, 1_000)
		tr := Transfer{
			ID: NewID(), DebitAccountID: accts.a, CreditAccountID: accts.b,
			Amount: 100, Ledger: LedgerNGSovereign, Code: 101,
		}
		if _, err := l.CreateTransfers([]Transfer{tr}); err != nil {
			t.Fatalf("first CreateTransfers: %v", err)
		}
		res, err := l.CreateTransfers([]Transfer{tr})
		if err != nil {
			t.Fatalf("second CreateTransfers: %v", err)
		}
		expectResults(t, res, []TransferResult{{Index: 0, Result: ResultExists}})
	})

	t.Run("zero amount rejected", func(t *testing.T) {
		l, accts := setup(t, 1_000)
		res, err := l.CreateTransfers([]Transfer{{
			ID: NewID(), DebitAccountID: accts.a, CreditAccountID: accts.b,
			Amount: 0, Ledger: LedgerNGSovereign, Code: 101,
		}})
		if err != nil {
			t.Fatalf("CreateTransfers: %v", err)
		}
		expectResults(t, res, []TransferResult{{Index: 0, Result: ResultZeroAmount}})
	})

	t.Run("unknown account rejected", func(t *testing.T) {
		l, accts := setup(t, 1_000)
		ghost := MustBuildAccountID(StateOsun, 0, ClassSecurityTrustFund, EntityFromUint64(NewID().Lo))
		res, err := l.CreateTransfers([]Transfer{{
			ID: NewID(), DebitAccountID: accts.a, CreditAccountID: ghost,
			Amount: 10, Ledger: LedgerNGSovereign, Code: 101,
		}})
		if err != nil {
			t.Fatalf("CreateTransfers: %v", err)
		}
		expectResults(t, res, []TransferResult{{Index: 0, Result: ResultAccountNotFound}})
	})

	t.Run("self-transfer rejected", func(t *testing.T) {
		l, accts := setup(t, 1_000)
		res, err := l.CreateTransfers([]Transfer{{
			ID: NewID(), DebitAccountID: accts.a, CreditAccountID: accts.a,
			Amount: 10, Ledger: LedgerNGSovereign, Code: 101,
		}})
		if err != nil {
			t.Fatalf("CreateTransfers: %v", err)
		}
		expectResults(t, res, []TransferResult{{Index: 0, Result: ResultAccountsAreSame}})
	})

	t.Run("overdraft rejected with exceeds_credits", func(t *testing.T) {
		l, accts := setup(t, 100)
		res, err := l.CreateTransfers([]Transfer{{
			ID: NewID(), DebitAccountID: accts.a, CreditAccountID: accts.b,
			Amount: 101, Ledger: LedgerNGSovereign, Code: 101,
		}})
		if err != nil {
			t.Fatalf("CreateTransfers: %v", err)
		}
		expectResults(t, res, []TransferResult{{Index: 0, Result: ResultOverdraft}})
	})

	t.Run("linked chain commits atomically", func(t *testing.T) {
		l, accts := setup(t, 1_000)
		res, err := l.CreateTransfers([]Transfer{
			{ID: NewID(), DebitAccountID: accts.a, CreditAccountID: accts.b,
				Amount: 600, Ledger: LedgerNGSovereign, Code: 101, Flags: TransferFlags{Linked: true}},
			{ID: NewID(), DebitAccountID: accts.a, CreditAccountID: accts.c,
				Amount: 300, Ledger: LedgerNGSovereign, Code: 102},
		})
		if err != nil {
			t.Fatalf("CreateTransfers: %v", err)
		}
		expectResults(t, res, nil)
	})

	t.Run("open linked chain rejected", func(t *testing.T) {
		l, accts := setup(t, 1_000)
		res, err := l.CreateTransfers([]Transfer{
			{ID: NewID(), DebitAccountID: accts.a, CreditAccountID: accts.b,
				Amount: 10, Ledger: LedgerNGSovereign, Code: 101},
			{ID: NewID(), DebitAccountID: accts.a, CreditAccountID: accts.c,
				Amount: 20, Ledger: LedgerNGSovereign, Code: 102, Flags: TransferFlags{Linked: true}},
		})
		if err != nil {
			t.Fatalf("CreateTransfers: %v", err)
		}
		expectResults(t, res, []TransferResult{{Index: 1, Result: ResultLinkedEventChainOpen}})
	})

	t.Run("linked chain failure cascades", func(t *testing.T) {
		l, accts := setup(t, 500)
		// Second leg overdraws; first (valid) leg must be rejected with
		// linked_event_failed and nothing must commit.
		res, err := l.CreateTransfers([]Transfer{
			{ID: NewID(), DebitAccountID: accts.a, CreditAccountID: accts.b,
				Amount: 100, Ledger: LedgerNGSovereign, Code: 101, Flags: TransferFlags{Linked: true}},
			{ID: NewID(), DebitAccountID: accts.a, CreditAccountID: accts.c,
				Amount: 999, Ledger: LedgerNGSovereign, Code: 102},
		})
		if err != nil {
			t.Fatalf("CreateTransfers: %v", err)
		}
		expectResults(t, res, []TransferResult{
			{Index: 0, Result: ResultLinkedEventFailed},
			{Index: 1, Result: ResultOverdraft},
		})
	})

	t.Run("pending transfer reserves without posting", func(t *testing.T) {
		l, accts := setup(t, 1_000)
		res, err := l.CreateTransfers([]Transfer{{
			ID: NewID(), DebitAccountID: accts.a, CreditAccountID: accts.b,
			Amount: 250, Ledger: LedgerNGSovereign, Code: 101,
			Flags: TransferFlags{Pending: true},
		}})
		if err != nil {
			t.Fatalf("CreateTransfers: %v", err)
		}
		expectResults(t, res, nil)
	})
}

// TestInMemoryLedgerContract runs the contract suite against the
// in-memory fake on every build (no cluster required).
func TestInMemoryLedgerContract(t *testing.T) {
	RunLedgerContractTests(t, func(t *testing.T) LedgerClient {
		t.Helper()
		return NewInMemoryLedger()
	})
}
