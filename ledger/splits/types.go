// Package splits implements the SOS atomic multi-party statutory revenue
// split library for the TigerBeetle financial ledger (ADR-002 / WP-03 /
// EP-TB-03 / Clause 22.2).
//
// A settled bill is split, directly at the ledger layer, into gazetted
// beneficiary legs (State Consolidated Revenue Fund, MDA retention,
// PPP concessionaire escrow, ...). INSTANT legs form a single LINKED
// atomic transfer chain: the entire chain commits or nothing does.
// END_OF_MONTH legs are excluded from the instant chain and swept later;
// any unallocated remainder stays in the payer clearing account.
//
// The package is transport-agnostic: it defines its own Transfer type and
// LedgerClient interface so unit tests run against an in-memory fake
// without a live TigerBeetle cluster. A production adapter maps these
// types onto github.com/tigerbeetle/tigerbeetle-go.
package splits

import (
	"errors"
	"fmt"
	"sync/atomic"
	"time"
)

// LedgerNGSovereign is the TigerBeetle ledger ID for the NG State
// Sovereign Ledger (ledger/chart-of-accounts.md).
const LedgerNGSovereign uint32 = 1

// Uint128 is a 128-bit unsigned integer in little-endian limb order
// (Lo = bits 0..63, Hi = bits 64..127), matching the TigerBeetle
// Uint128 ABI.
type Uint128 struct {
	Lo, Hi uint64
}

// IsZero reports whether u == 0.
func (u Uint128) IsZero() bool { return u.Lo == 0 && u.Hi == 0 }

// ToUint128 lifts a 64-bit value into a Uint128.
func ToUint128(v uint64) Uint128 { return Uint128{Lo: v} }

// idCounter provides process-unique, monotonically increasing IDs.
var idCounter atomic.Uint64

// NewID returns a fresh, non-zero 128-bit identifier. The high limb is
// seeded from the wall clock and the low limb from a process-wide atomic
// counter, guaranteeing uniqueness within a process run (sufficient for
// transfer/bill correlation IDs; TigerBeetle itself only requires IDs to
// be unique per object type).
func NewID() Uint128 {
	lo := idCounter.Add(1)
	hi := uint64(time.Now().UnixNano())
	return Uint128{Lo: lo, Hi: hi ^ 0x5AF5_0000_0000_0000}
}

// TransferFlags mirrors the TigerBeetle transfer flag set relevant to
// statutory splits.
type TransferFlags struct {
	// Linked chains this transfer with the next one in the batch: the
	// chain commits atomically or not at all.
	Linked bool
	// Pending reserves the amount without posting it (two-phase
	// transfer); used when a bill is issued but not yet settled.
	Pending bool
}

// Transfer is a single double-entry ledger movement of kobo between two
// accounts.
type Transfer struct {
	ID              Uint128
	DebitAccountID  Uint128
	CreditAccountID Uint128
	Amount          uint64 // kobo
	Ledger          uint32
	Code            uint16 // transfer code per ledger/chart-of-accounts.md
	Flags           TransferFlags
	// UserData carries the bill reference hash / assessment correlation.
	UserData Uint128
}

// TransferResultCode identifies why a single transfer in a batch was
// rejected. OK means the transfer committed.
type TransferResultCode uint32

const (
	ResultOK TransferResultCode = iota
	ResultExists
	ResultZeroAmount
	ResultAccountNotFound
	ResultAccountsAreSame
	ResultOverdraft
	ResultLinkedEventChainOpen
	ResultLinkedEventFailed
	ResultPendingTransferNotFound
)

// String renders a human-readable result code.
func (c TransferResultCode) String() string {
	switch c {
	case ResultOK:
		return "ok"
	case ResultExists:
		return "exists"
	case ResultZeroAmount:
		return "amount_must_be_non_zero"
	case ResultAccountNotFound:
		return "account_not_found"
	case ResultAccountsAreSame:
		return "accounts_are_same"
	case ResultOverdraft:
		return "exceeds_credits"
	case ResultLinkedEventChainOpen:
		return "linked_event_chain_open"
	case ResultLinkedEventFailed:
		return "linked_event_failed"
	case ResultPendingTransferNotFound:
		return "pending_transfer_not_found"
	default:
		return fmt.Sprintf("unknown(%d)", uint32(c))
	}
}

// TransferResult reports the per-transfer outcome of a CreateTransfers
// batch. Only failed transfers produce a result entry (TigerBeetle
// semantics).
type TransferResult struct {
	Index  uint32
	Result TransferResultCode
}

// LedgerClient is the minimal TigerBeetle client surface used by the
// split engine. The production implementation wraps tigerbeetle-go;
// tests use InMemoryLedger.
type LedgerClient interface {
	// CreateTransfers atomically applies a batch of transfers. Per-
	// transfer failures are returned as []TransferResult; transport or
	// consensus failures are returned as error.
	CreateTransfers(transfers []Transfer) ([]TransferResult, error)
}

// ErrBatchRejected wraps per-transfer failures into a single error.
type ErrBatchRejected struct {
	Results []TransferResult
}

func (e *ErrBatchRejected) Error() string {
	return fmt.Sprintf("atomic transfer batch rejected: %v", e.Results)
}

// IsBatchRejected reports whether err is an *ErrBatchRejected.
func IsBatchRejected(err error) bool {
	var br *ErrBatchRejected
	return errors.As(err, &br)
}
