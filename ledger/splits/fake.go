package splits

import (
	"fmt"
	"sync"
)

// InMemoryLedger is a deterministic, in-memory LedgerClient fake for
// unit tests and local development. It implements the linked-chain
// semantics that matter to the split engine:
//
//   - accounts must exist before they are debited/credited;
//   - debits may not drive an account negative;
//   - Linked transfers form contiguous chains; a failure anywhere in a
//     chain rejects the whole chain (linked_event_failed cascade);
//   - a batch may not end with a Linked transfer (linked_event_chain_open);
//   - transfer IDs must be unique (exists).
//
// Only chains that validate in full are applied, mirroring TigerBeetle's
// atomic commit of linked batches.
type InMemoryLedger struct {
	mu        sync.Mutex
	accounts  map[Uint128]*Account
	transfer  map[Uint128]Transfer
	submitted [][]Transfer // audit log of every batch received
}

// Account is a ledger account with posted/pending double-entry balances.
type Account struct {
	ID             Uint128
	DebitsPosted   uint64
	CreditsPosted  uint64
	DebitsPending  uint64
	CreditsPending uint64
}

// Balance returns CreditsPosted - DebitsPosted. It is never negative in
// a healthy ledger.
func (a *Account) Balance() int64 { return int64(a.CreditsPosted) - int64(a.DebitsPosted) }

// NewInMemoryLedger returns an empty fake ledger.
func NewInMemoryLedger() *InMemoryLedger {
	return &InMemoryLedger{
		accounts: make(map[Uint128]*Account),
		transfer: make(map[Uint128]Transfer),
	}
}

// CreateAccount registers an account. Re-creating an existing account is
// a no-op.
func (l *InMemoryLedger) CreateAccount(id Uint128) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if _, ok := l.accounts[id]; !ok {
		l.accounts[id] = &Account{ID: id}
	}
}

// SeedAccount registers an account pre-funded with creditsPosted kobo —
// e.g. the payer clearing account funded by the inbound payment before
// the split executes.
func (l *InMemoryLedger) SeedAccount(id Uint128, creditsPosted uint64) {
	l.mu.Lock()
	defer l.mu.Unlock()
	acct, ok := l.accounts[id]
	if !ok {
		acct = &Account{ID: id}
		l.accounts[id] = acct
	}
	acct.CreditsPosted += creditsPosted
}

// GetAccount returns a copy of the account, or nil if unknown.
func (l *InMemoryLedger) GetAccount(id Uint128) *Account {
	l.mu.Lock()
	defer l.mu.Unlock()
	acct, ok := l.accounts[id]
	if !ok {
		return nil
	}
	cp := *acct
	return &cp
}

// Batches returns the audit log of submitted transfer batches.
func (l *InMemoryLedger) Batches() [][]Transfer {
	l.mu.Lock()
	defer l.mu.Unlock()
	out := make([][]Transfer, len(l.submitted))
	copy(out, l.submitted)
	return out
}

// CreateTransfers validates and applies a batch with linked-chain
// semantics. The full batch is staged first; if any linked chain fails,
// none of its transfers are applied.
func (l *InMemoryLedger) CreateTransfers(transfers []Transfer) ([]TransferResult, error) {
	l.mu.Lock()
	defer l.mu.Unlock()

	l.submitted = append(l.submitted, append([]Transfer(nil), transfers...))

	results := make([]TransferResult, len(transfers))
	failed := make([]bool, len(transfers))

	mark := func(i int, code TransferResultCode) {
		results[i] = TransferResult{Index: uint32(i), Result: code}
		failed[i] = true
	}

	// Staged balance deltas, so validation is order-independent within
	// the batch.
	stagedDebit := make(map[Uint128]uint64)
	stagedCredit := make(map[Uint128]uint64)

	for i, tr := range transfers {
		switch {
		case tr.ID.IsZero():
			mark(i, ResultZeroAmount) // zero ID is as invalid as zero amount
		case tr.Amount == 0:
			mark(i, ResultZeroAmount)
		case tr.DebitAccountID == tr.CreditAccountID:
			mark(i, ResultAccountsAreSame)
		default:
			if _, dup := l.transfer[tr.ID]; dup {
				mark(i, ResultExists)
				break
			}
			dr, drOK := l.accounts[tr.DebitAccountID]
			_, crOK := l.accounts[tr.CreditAccountID]
			if !drOK || !crOK {
				mark(i, ResultAccountNotFound)
				break
			}
			if tr.Amount > dr.CreditsPosted+stagedCredit[tr.DebitAccountID]-
				(dr.DebitsPosted+stagedDebit[tr.DebitAccountID]) {
				mark(i, ResultOverdraft)
				break
			}
			stagedDebit[tr.DebitAccountID] += tr.Amount
			stagedCredit[tr.CreditAccountID] += tr.Amount
		}
		// Linked-chain structural validation.
		if tr.Flags.Linked && i == len(transfers)-1 {
			mark(i, ResultLinkedEventChainOpen)
		}
	}

	// Cascade failures through linked chains: if any member of a chain
	// fails, every member of that chain fails with linked_event_failed,
	// and nothing in the chain commits.
	for i := 0; i < len(transfers); {
		j := i
		for j < len(transfers) && transfers[j].Flags.Linked {
			j++
		}
		chainFailed := false
		for k := i; k <= j && k < len(transfers); k++ {
			if failed[k] {
				chainFailed = true
				break
			}
		}
		if chainFailed {
			for k := i; k <= j && k < len(transfers); k++ {
				if !failed[k] {
					mark(k, ResultLinkedEventFailed)
				}
			}
		}
		i = j + 1
	}

	// Apply only transfers outside failed chains (standalone failures do
	// not roll back unrelated unlinked transfers, matching TigerBeetle).
	var out []TransferResult
	for i, tr := range transfers {
		if failed[i] {
			out = append(out, results[i])
			continue
		}
		dr := l.accounts[tr.DebitAccountID]
		cr := l.accounts[tr.CreditAccountID]
		if tr.Flags.Pending {
			dr.DebitsPending += tr.Amount
			cr.CreditsPending += tr.Amount
		} else {
			dr.DebitsPosted += tr.Amount
			cr.CreditsPosted += tr.Amount
		}
		l.transfer[tr.ID] = tr
	}
	return out, nil
}

// MustAccount is a test helper returning the account or panicking.
func (l *InMemoryLedger) MustAccount(id Uint128) *Account {
	acct := l.GetAccount(id)
	if acct == nil {
		panic(fmt.Sprintf("splits: account %#v not found", id))
	}
	return acct
}
