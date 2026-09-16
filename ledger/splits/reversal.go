package splits

import (
	"errors"
	"fmt"
)

// Reversal chains (refunds / corrections).
//
// A reversal mirrors a committed chain leg-for-leg with debit and credit
// accounts swapped, moving the money back to its source. The reversal is
// itself a single linked chain, so it commits atomically or not at all
// (same semantics as the forward chain). Reversal leg IDs are derived
// deterministically as sha256(key ‖ leg ‖ "reversal"), so a retried
// refund maps onto the same ledger objects and can never double-reverse.

// BuildReversalChain builds the mirror of originals: every leg's
// debit/credit accounts are swapped and IDs come from ReversalLegID. The
// chain must reverse in one batch; originals must be the exact transfers
// that committed (same order) for the amounts to line up.
func BuildReversalChain(originals []Transfer, key string) ([]Transfer, error) {
	if len(originals) == 0 {
		return nil, errors.New("splits: nothing to reverse: empty chain")
	}
	if key == "" {
		return nil, errors.New("splits: reversal requires a deterministic chain key")
	}
	out := make([]Transfer, len(originals))
	for i, tr := range originals {
		out[i] = Transfer{
			ID:              ReversalLegID(key, i),
			DebitAccountID:  tr.CreditAccountID,
			CreditAccountID: tr.DebitAccountID,
			Amount:          tr.Amount,
			Ledger:          tr.Ledger,
			Code:            tr.Code,
			UserData:        tr.UserData,
		}
		if i < len(originals)-1 {
			out[i].Flags.Linked = true
		}
	}
	return out, nil
}

// ExecuteReversalChain builds and submits the reversal of originals as one
// atomic linked chain. Submission is idempotent: a replay of an already
// committed reversal (same deterministic IDs) is accepted as a no-op.
func ExecuteReversalChain(client LedgerClient, originals []Transfer, key string) ([]Transfer, error) {
	reversal, err := BuildReversalChain(originals, key)
	if err != nil {
		return nil, err
	}
	if err := SubmitChainIdempotent(client, reversal); err != nil {
		return nil, fmt.Errorf("splits: reversal chain rejected: %w", err)
	}
	return reversal, nil
}
