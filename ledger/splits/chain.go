package splits

import (
	"errors"
	"fmt"
)

// Atomic linked-chain transfer builder.
//
// Every INSTANT leg debits the payer clearing account and credits the
// gazetted beneficiary account. All legs carry Flags.Linked except the
// final one, so TigerBeetle commits the chain atomically: if any leg is
// rejected, none commit (ledger/chart-of-accounts.md "Ledger Topology",
// ADR-002).

// defaultTransferCode derives a ledger transfer code from the beneficiary
// when the policy pack does not pin one explicitly.
func defaultTransferCode(b Beneficiary) uint16 {
	switch b {
	case BeneficiaryConsolidatedRevenueFund:
		return 101 // primary revenue leg
	case BeneficiaryMDARetention:
		return 102 // MDA Retention leg
	case BeneficiaryConcessionaireEscrow:
		return 103 // Concessionaire Share leg
	case BeneficiaryLocalGovernmentShare:
		return 102
	case BeneficiaryTransportUnion:
		return 130
	case BeneficiarySecurityTrustFund:
		return 101
	}
	return 100
}

// ChainParams carries the chart-of-accounts context for one settlement.
type ChainParams struct {
	// StateTenant is the state's tenant ID (e.g. StateOgun).
	StateTenant uint16
	// MDACategory scopes the payer clearing account to the collecting MDA
	// (zero for a state-level clearing account).
	MDACategory uint16
	// Entity identifies the collecting entity within the chart.
	Entity Uint128
	// BillID correlates the chain to the settled bill (stored as
	// UserData on every leg for audit).
	BillID Uint128
	// Ledger is the TigerBeetle ledger ID (LedgerNGSovereign).
	Ledger uint32
	// IdempotencyKey, when non-empty, makes every leg ID deterministic
	// (ChainLegID(key, i)) so a retried submission maps onto the same
	// transfer IDs instead of minting fresh ones (crash/retry safety).
	// When empty, legs get process-unique NewID() IDs.
	IdempotencyKey string
}

// accountID resolves a chart account code into a full 128-bit account ID
// under the chain's tenant/entity scope.
func (p ChainParams) accountID(class uint16) (Uint128, error) {
	return BuildAccountID(p.StateTenant, p.MDACategory, class, p.Entity)
}

// PayerClearingAccountID returns the 128-bit ID of the payer clearing
// account (class 1001) for this chain.
func (p ChainParams) PayerClearingAccountID() (Uint128, error) {
	return p.accountID(ClassPayerClearing)
}

// BuildAtomicChain converts the INSTANT legs of a SplitPlan into a linked
// atomic transfer batch. END_OF_MONTH legs and the clearing remainder are
// untouched — they never enter the instant chain.
func BuildAtomicChain(plan *SplitPlan, params ChainParams) ([]Transfer, error) {
	if plan == nil || len(plan.InstantLegs) == 0 {
		return nil, errors.New("splits: nothing to chain: no INSTANT legs")
	}
	payer, err := params.PayerClearingAccountID()
	if err != nil {
		return nil, fmt.Errorf("splits: payer clearing account: %w", err)
	}
	transfers := make([]Transfer, 0, len(plan.InstantLegs))
	for i, leg := range plan.InstantLegs {
		beneficiaryAcct, err := params.accountID(leg.Rule.AccountCode)
		if err != nil {
			return nil, fmt.Errorf("splits: leg %d (%s): %w", i, leg.Rule.Beneficiary, err)
		}
		code := leg.Rule.TransferCode
		if code == 0 {
			code = defaultTransferCode(leg.Rule.Beneficiary)
		}
		legID := NewID()
		if params.IdempotencyKey != "" {
			legID = ChainLegID(params.IdempotencyKey, i)
		}
		tr := Transfer{
			ID:              legID,
			DebitAccountID:  payer,
			CreditAccountID: beneficiaryAcct,
			Amount:          leg.Amount,
			Ledger:          params.Ledger,
			Code:            code,
			UserData:        params.BillID,
		}
		if i < len(plan.InstantLegs)-1 {
			tr.Flags.Linked = true // continue the atomic chain
		}
		transfers = append(transfers, tr)
	}
	return transfers, nil
}

// ExecuteAtomicSplit builds the linked chain for a settled bill and
// submits it. It returns an error (wrapping *ErrBatchRejected when the
// ledger rejected individual legs) if the chain did not commit in full;
// a nil error guarantees every INSTANT leg committed atomically.
func ExecuteAtomicSplit(client LedgerClient, plan *SplitPlan, params ChainParams) ([]Transfer, error) {
	if client == nil {
		return nil, errors.New("splits: nil LedgerClient")
	}
	transfers, err := BuildAtomicChain(plan, params)
	if err != nil {
		return nil, err
	}
	res, err := client.CreateTransfers(transfers)
	if err != nil {
		return nil, fmt.Errorf("splits: submit atomic chain: %w", err)
	}
	if len(res) > 0 {
		return nil, &ErrBatchRejected{Results: res}
	}
	return transfers, nil
}

// AppendChainLeg extends an existing linked chain with one more transfer:
// the current closing leg is marked Linked and tr closes the chain. The
// extended batch still commits atomically or not at all.
func AppendChainLeg(chain []Transfer, tr Transfer) ([]Transfer, error) {
	if len(chain) == 0 {
		return nil, errors.New("splits: cannot extend an empty chain")
	}
	tr.Flags.Linked = false
	chain[len(chain)-1].Flags.Linked = true
	return append(chain, tr), nil
}

// SubmitChainIdempotent submits a deterministic chain and treats a replay
// as success: if the ledger reports every failed leg as ResultExists, the
// chain (with these exact IDs) already committed and the retry is a no-op.
// Any other per-transfer failure is returned as *ErrBatchRejected.
func SubmitChainIdempotent(client LedgerClient, transfers []Transfer) error {
	if client == nil {
		return errors.New("splits: nil LedgerClient")
	}
	if len(transfers) == 0 {
		return errors.New("splits: nothing to submit")
	}
	res, err := client.CreateTransfers(transfers)
	if err != nil {
		return fmt.Errorf("splits: submit chain: %w", err)
	}
	var real []TransferResult
	for _, r := range res {
		if r.Result != ResultExists {
			real = append(real, r)
		}
	}
	if len(real) > 0 {
		return &ErrBatchRejected{Results: real}
	}
	return nil
}

// SettleGross is a convenience pipeline: compute the split from a policy
// pack and execute the atomic chain in one call.
func SettleGross(client LedgerClient, pack *PolicyPack, grossKobo uint64, params ChainParams) (*SplitPlan, []Transfer, error) {
	plan, err := ComputeSplit(grossKobo, pack.Rules)
	if err != nil {
		return nil, nil, err
	}
	transfers, err := ExecuteAtomicSplit(client, plan, params)
	if err != nil {
		return nil, nil, err
	}
	return plan, transfers, nil
}
