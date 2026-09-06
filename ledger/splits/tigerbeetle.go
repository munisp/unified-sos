//go:build tigerbeetle

package splits

import (
	"encoding/binary"
	"fmt"
	"log"
	"os"
	"strconv"
	"strings"
	"sync"

	tb "github.com/tigerbeetle/tigerbeetle-go"
	tb_types "github.com/tigerbeetle/tigerbeetle-go/pkg/types"
)

// TigerBeetleLedger is the production LedgerClient adapter backed by a
// live TigerBeetle cluster via github.com/tigerbeetle/tigerbeetle-go.
// It is compiled only under the `tigerbeetle` build tag because the
// client links the native TigerBeetle C library; the default build uses
// the fail-closed stub in tigerbeetle_stub.go.
//
// The adapter preserves the in-memory fake's semantics:
//
//   - Linked/Pending flags map onto TigerBeetle transfer flags;
//   - per-transfer results map back onto the fake's error vocabulary
//     (exists, linked_event_failed, linked_event_chain_open,
//     exceeds_credits, ...);
//   - transport/consensus failures surface as error, never as a silent
//     partial commit.
type TigerBeetleLedger struct {
	client tbClient
	ledger uint32
	// seedSource funds SeedAccount credits via a transfer from this
	// bootstrap float account; zero means SeedAccount only creates the
	// account (production funding flows arrive as settlement transfers).
	seedSource Uint128

	mu      sync.Mutex
	lastErr error // most recent provisioning error (fail-closed record)
}

// tbClient is the subset of the tigerbeetle-go client the adapter uses
// (an interface so the adapter is testable without the native client).
type tbClient interface {
	CreateTransfers(transfers []tb_types.Transfer) ([]tb_types.TransferEventResult, error)
	CreateAccounts(accounts []tb_types.Account) ([]tb_types.AccountEventResult, error)
	Close()
}

// TBOption customises a TigerBeetleLedger.
type TBOption func(*TigerBeetleLedger)

// WithSeedAccount configures the bootstrap float account that funds
// SeedAccount credits. Without it SeedAccount only creates the account.
func WithSeedAccount(id Uint128) TBOption {
	return func(l *TigerBeetleLedger) { l.seedSource = id }
}

// NewTigerBeetleLedger connects to the cluster at addresses (host:port
// list) on the given cluster ID.
func NewTigerBeetleLedger(clusterID uint64, addresses []string, opts ...TBOption) (*TigerBeetleLedger, error) {
	if len(addresses) == 0 {
		return nil, fmt.Errorf("splits: tigerbeetle: at least one cluster address is required")
	}
	client, err := tb.NewClient(tb_types.ToUint128(clusterID), addresses)
	if err != nil {
		return nil, fmt.Errorf("splits: tigerbeetle: connect %v: %w", addresses, err)
	}
	l := &TigerBeetleLedger{client: client, ledger: LedgerNGSovereign}
	for _, o := range opts {
		o(l)
	}
	// The bootstrap float is the one account permitted to go negative:
	// it is the source of provisioning credits (SeedAccount).
	if !l.seedSource.IsZero() {
		l.createAccount(l.seedSource, false)
	}
	return l, nil
}

// NewTigerBeetleLedgerFromEnv builds the production adapter from
// environment configuration. It is FAIL-CLOSED: both TB_ADDRESSES
// (comma-separated host:port list) and TB_CLUSTER_ID must be set.
func NewTigerBeetleLedgerFromEnv() (LedgerClient, error) {
	rawAddrs := os.Getenv("TB_ADDRESSES")
	rawCluster := os.Getenv("TB_CLUSTER_ID")
	if rawAddrs == "" || rawCluster == "" {
		return nil, fmt.Errorf(
			"splits: tigerbeetle: TB_ADDRESSES and TB_CLUSTER_ID must both be set (TB_ADDRESSES=%q, TB_CLUSTER_ID=%q)",
			rawAddrs, rawCluster)
	}
	clusterID, err := strconv.ParseUint(rawCluster, 10, 64)
	if err != nil {
		return nil, fmt.Errorf("splits: tigerbeetle: invalid TB_CLUSTER_ID %q: %w", rawCluster, err)
	}
	var addresses []string
	for _, a := range strings.Split(rawAddrs, ",") {
		if a = strings.TrimSpace(a); a != "" {
			addresses = append(addresses, a)
		}
	}
	return NewTigerBeetleLedger(clusterID, addresses)
}

// Close releases the underlying cluster connection.
func (l *TigerBeetleLedger) Close() { l.client.Close() }

// Err returns the most recent provisioning error recorded by
// CreateAccount/SeedAccount (those methods satisfy the error-less
// accountProvisioner seam, so failures are recorded here and logged
// instead of swallowed).
func (l *TigerBeetleLedger) Err() error {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.lastErr
}

func (l *TigerBeetleLedger) recordErr(err error) {
	l.mu.Lock()
	l.lastErr = err
	l.mu.Unlock()
	log.Printf("splits: tigerbeetle: provisioning error: %v", err)
}

// uint128ToTB converts the package Uint128 (little-endian limb order)
// into the tigerbeetle-go ABI type.
func uint128ToTB(u Uint128) tb_types.Uint128 {
	var b [16]byte
	binary.LittleEndian.PutUint64(b[:8], u.Lo)
	binary.LittleEndian.PutUint64(b[8:], u.Hi)
	return tb_types.BytesToUint128(b)
}

// toTBTransfer maps a splits.Transfer onto the TigerBeetle wire type.
func (l *TigerBeetleLedger) toTBTransfer(tr Transfer) tb_types.Transfer {
	flags := tb_types.TransferFlags{
		Linked:  tr.Flags.Linked,
		Pending: tr.Flags.Pending,
	}.ToUint16()
	return tb_types.Transfer{
		ID:              uint128ToTB(tr.ID),
		DebitAccountID:  uint128ToTB(tr.DebitAccountID),
		CreditAccountID: uint128ToTB(tr.CreditAccountID),
		Amount:          tb_types.ToUint128(tr.Amount),
		Ledger:          l.ledgerOrDefault(tr.Ledger),
		Code:            tr.Code,
		Flags:           flags,
		UserData128:     uint128ToTB(tr.UserData),
	}
}

func (l *TigerBeetleLedger) ledgerOrDefault(ledger uint32) uint32 {
	if ledger != 0 {
		return ledger
	}
	return l.ledger
}

// mapTransferResult maps a TigerBeetle transfer result code back onto
// the fake's error vocabulary. Unknown codes are a hard error: silently
// relabeling a rejection would break the atomicity contract.
func mapTransferResult(r tb_types.CreateTransferResult) (TransferResultCode, error) {
	switch r {
	case tb_types.TransferOK:
		return ResultOK, nil
	case tb_types.TransferExists:
		return ResultExists, nil
	case tb_types.TransferIDMustNotBeZero,
		tb_types.TransferAmountMustNotBeZero:
		return ResultZeroAmount, nil
	case tb_types.TransferDebitAccountNotFound,
		tb_types.TransferCreditAccountNotFound:
		return ResultAccountNotFound, nil
	case tb_types.TransferAccountsMustBeDifferent:
		return ResultAccountsAreSame, nil
	case tb_types.TransferExceedsCredits,
		tb_types.TransferExceedsDebits:
		return ResultOverdraft, nil
	case tb_types.TransferLinkedEventChainOpen:
		return ResultLinkedEventChainOpen, nil
	case tb_types.TransferLinkedEventFailed:
		return ResultLinkedEventFailed, nil
	case tb_types.TransferPendingTransferNotFound:
		return ResultPendingTransferNotFound, nil
	default:
		return 0, fmt.Errorf("splits: tigerbeetle: unmapped transfer result %v", r)
	}
}

// CreateTransfers submits the batch to the cluster and maps per-transfer
// rejections onto the splits result vocabulary.
func (l *TigerBeetleLedger) CreateTransfers(transfers []Transfer) ([]TransferResult, error) {
	batch := make([]tb_types.Transfer, len(transfers))
	for i, tr := range transfers {
		batch[i] = l.toTBTransfer(tr)
	}
	res, err := l.client.CreateTransfers(batch)
	if err != nil {
		return nil, fmt.Errorf("splits: tigerbeetle: create transfers: %w", err)
	}
	out := make([]TransferResult, 0, len(res))
	for _, r := range res {
		code, err := mapTransferResult(r.Result)
		if err != nil {
			return out, err
		}
		out = append(out, TransferResult{Index: r.Index, Result: code})
	}
	return out, nil
}

// createAccount registers an account on the cluster. Re-creating an
// existing account is a no-op, matching the fake (the cluster replies
// AccountExists, which is treated as success). Business accounts are
// created with debits_must_not_exceed_credits so the cluster enforces
// the fake's overdraft rejection (exceeds_credits).
func (l *TigerBeetleLedger) createAccount(id Uint128, constrained bool) {
	parts := DecodeAccountID(id)
	acct := tb_types.Account{
		ID:     uint128ToTB(id),
		Ledger: l.ledger,
		Code:   parts.AccountClass,
		Flags: tb_types.AccountFlags{
			DebitsMustNotExceedCredits: constrained,
		}.ToUint16(),
	}
	res, err := l.client.CreateAccounts([]tb_types.Account{acct})
	if err != nil {
		l.recordErr(fmt.Errorf("create account %#v: %w", id, err))
		return
	}
	for _, r := range res {
		if r.Result != tb_types.AccountExists {
			l.recordErr(fmt.Errorf("create account %#v rejected: %v", id, r.Result))
		}
	}
}

// CreateAccount registers a business account (overdrafts rejected).
func (l *TigerBeetleLedger) CreateAccount(id Uint128) {
	l.createAccount(id, true)
}

// SeedAccount creates the account and, when a bootstrap float account
// is configured (WithSeedAccount), funds it with creditsPosted kobo via
// a posted transfer. Without a configured float the account is created
// unfunded — production funding arrives as settlement transfers.
func (l *TigerBeetleLedger) SeedAccount(id Uint128, creditsPosted uint64) {
	l.CreateAccount(id)
	if creditsPosted == 0 || l.seedSource.IsZero() {
		return
	}
	tr := Transfer{
		ID:              NewID(),
		DebitAccountID:  l.seedSource,
		CreditAccountID: id,
		Amount:          creditsPosted,
		Ledger:          l.ledger,
		Code:            uint16(DecodeAccountID(id).AccountClass),
	}
	results, err := l.CreateTransfers([]Transfer{tr})
	if err != nil {
		l.recordErr(fmt.Errorf("seed account %#v: %w", id, err))
		return
	}
	if len(results) != 0 {
		l.recordErr(fmt.Errorf("seed account %#v rejected: %v", id, results))
	}
}
