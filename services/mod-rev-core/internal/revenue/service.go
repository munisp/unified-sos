package revenue

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math/big"
	"time"

	"github.com/munisp/unified-sos/ledger/splits"
)

// Service implements the mod-rev-core use cases over a Store, a
// PolicyCatalog, and a splits.LedgerClient.
type Service struct {
	store   Store
	catalog *PolicyCatalog
	ledger  splits.LedgerClient
	ebills  EBillNotifier
	now     func() time.Time
}

// Option customises a Service (used by tests).
type Option func(*Service)

// WithClock pins the assessment clock.
func WithClock(now func() time.Time) Option {
	return func(s *Service) { s.now = now }
}

// WithEBillNotifier injects the e-Bills gateway seam (NIBSS in production;
// NoopEBillNotifier is the deterministic default).
func WithEBillNotifier(n EBillNotifier) Option {
	return func(s *Service) { s.ebills = n }
}

// NewService wires the domain service. ledger must not be nil; use
// splits.NewInMemoryLedger() when no TigerBeetle cluster is available.
func NewService(store Store, catalog *PolicyCatalog, ledger splits.LedgerClient, opts ...Option) *Service {
	s := &Service{store: store, catalog: catalog, ledger: ledger, ebills: NoopEBillNotifier{}, now: func() time.Time { return time.Now().UTC() }}
	for _, o := range opts {
		o(s)
	}
	return s
}

// accountProvisioner is implemented by ledgers that can create/fund
// accounts on demand (the in-memory fake; the production adapter
// provisions chart-of-accounts accounts at bootstrap instead).
type accountProvisioner interface {
	CreateAccount(id splits.Uint128)
	SeedAccount(id splits.Uint128, creditsPosted uint64)
}

// uint128Decimal renders a 128-bit ID as a decimal string per the
// contract (tigerbeetle_transfer_pending_id).
func uint128Decimal(id splits.Uint128) string {
	hi := new(big.Int).SetUint64(id.Hi)
	hi.Lsh(hi, 64)
	hi.Add(hi, new(big.Int).SetUint64(id.Lo))
	return hi.String()
}

// requestHash fingerprints the request payload for idempotency-conflict
// detection.
func requestHash(req *AssessmentRequest) string {
	canonical, _ := json.Marshal(req)
	sum := sha256.Sum256(canonical)
	return hex.EncodeToString(sum[:])
}

// newBillReference mints a BILL-XXXX-XXXX-XXXX reference (12 random
// decimal digits, collision-checked by the store).
func newBillReference() (string, error) {
	var b [6]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	n := new(big.Int).SetBytes(b[:]).Uint64() % 1_000_000_000_000
	return fmt.Sprintf("BILL-%04d-%04d-%04d", n/100_000_000, (n/10_000)%10_000, n%10_000), nil
}

// validateAssessmentRequest enforces the contract invariants. Unknown
// revenue heads and amount mismatches are 400s per the OpenAPI contract.
func (s *Service) validateAssessmentRequest(state string, req *AssessmentRequest) *APIError {
	if err := ValidateSTIN(req.TaxpayerSTIN, state); err != nil {
		return badRequest("INVALID_STIN", "%v", err)
	}
	if req.MDACode == "" {
		return badRequest("MISSING_MDA_CODE", "mda_code is required")
	}
	if _, ok := s.catalog.Lookup(state, req.RevenueHead); !ok {
		return badRequest("UNKNOWN_REVENUE_HEAD", "revenue_head %q is not catalogued for state %q (heads: %v)",
			req.RevenueHead, state, s.catalog.RevenueHeads(state))
	}
	if req.TaxPeriodYear < 2020 {
		return badRequest("INVALID_TAX_PERIOD", "tax_period_year must be >= 2020")
	}
	if req.GrossIncomeKobo < 0 || req.AllowableDeductionsKobo < 0 || req.CalculatedTaxKobo < 0 {
		return badRequest("NEGATIVE_AMOUNT", "amounts must be >= 0")
	}
	if req.AllowableDeductionsKobo > req.GrossIncomeKobo {
		return badRequest("AMOUNT_MISMATCH", "allowable_deductions_kobo exceeds gross_income_kobo")
	}
	// The assessed tax can never exceed the net assessable income.
	if req.CalculatedTaxKobo > req.GrossIncomeKobo-req.AllowableDeductionsKobo {
		return badRequest("AMOUNT_MISMATCH",
			"calculated_tax_kobo (%d) exceeds net assessable income (%d)",
			req.CalculatedTaxKobo, req.GrossIncomeKobo-req.AllowableDeductionsKobo)
	}
	return nil
}

// CreateAssessment validates, registers the taxpayer, and idempotently
// issues a bill. created=false means the response is a replay of an
// earlier request with the same Idempotency-Key.
func (s *Service) CreateAssessment(state, idempotencyKey string, req *AssessmentRequest) (*AssessmentResponse, bool, error) {
	if err := s.validateAssessmentRequest(state, req); err != nil {
		return nil, false, err
	}
	hash := requestHash(req)

	// Idempotent replay: same key + same payload → same bill.
	if idempotencyKey != "" {
		if prev, ok := s.store.GetByIdempotencyKey(state, idempotencyKey); ok {
			if prev.RequestHash != hash {
				return nil, false, conflict("IDEMPOTENCY_CONFLICT",
					"Idempotency-Key %q was already used with a different payload", idempotencyKey)
			}
			resp := prev.Response()
			return &resp, false, nil
		}
	}

	// Register the taxpayer on first sight (STIN registry).
	s.store.UpsertTaxpayer(&Taxpayer{
		STIN:      req.TaxpayerSTIN,
		State:     state,
		LGACode:   metadataLGA(req),
		CreatedAt: s.now(),
	})

	billRef, err := newBillReference()
	if err != nil {
		return nil, false, internalError("BILL_MINT_FAILED", "could not mint bill reference: %v", err)
	}
	prefix, _ := StateSTINPrefix(state)
	seq := s.store.NextSequence(state)
	pendingID := splits.NewID() // reserved for the settlement pending transfer

	a := &Assessment{
		AssessmentID:                 fmt.Sprintf("ASM-%s-%d-%07d", prefix, req.TaxPeriodYear, seq),
		State:                        state,
		TaxpayerSTIN:                 req.TaxpayerSTIN,
		MDACode:                      req.MDACode,
		RevenueHead:                  req.RevenueHead,
		TaxPeriodYear:                req.TaxPeriodYear,
		GrossIncomeKobo:              req.GrossIncomeKobo,
		AllowableDeductionsKobo:      req.AllowableDeductionsKobo,
		CalculatedTaxKobo:            req.CalculatedTaxKobo,
		AmountDueKobo:                req.CalculatedTaxKobo,
		BillReference:                billRef,
		TigerbeetleTransferPendingID: uint128Decimal(pendingID),
		Status:                       StatusIssued,
		IdempotencyKey:               idempotencyKey,
		RequestHash:                  hash,
		Metadata:                     req.Metadata,
		CreatedAt:                    s.now(),
	}
	// Issue the bill through the e-Bills seam (Noop locally; NIBSS in
	// production). Fail closed: a gateway error aborts the assessment.
	gatewayRef, err := s.ebills.IssueBill(a)
	if err != nil {
		return nil, false, internalError("EBILL_ISSUE_FAILED", "e-Bills gateway: %v", err)
	}
	a.BillReference = gatewayRef
	if err := s.store.CreateAssessment(a); err != nil {
		return nil, false, conflict("BILL_REFERENCE_CONFLICT", "bill reference collision; retry")
	}
	resp := a.Response()
	return &resp, true, nil
}

func metadataLGA(req *AssessmentRequest) string {
	if req.Metadata == nil {
		return ""
	}
	return req.Metadata.LGACode
}

// GetAssessment fetches one assessment within the tenant boundary.
func (s *Service) GetAssessment(state, assessmentID string) (*AssessmentResponse, error) {
	a, ok := s.store.GetAssessment(state, assessmentID)
	if !ok {
		return nil, notFound("ASSESSMENT_NOT_FOUND", "assessment %q not found in state %q", assessmentID, state)
	}
	resp := a.Response()
	return &resp, nil
}

// SettleBill executes the statutory split for a paid bill: it computes
// the gazetted split from the state's policy pack and submits the atomic
// linked transfer chain to the ledger (Clause 22.2 — the concessionaire
// share is distributed strictly via ledger execution).
func (s *Service) SettleBill(state string, req *SettlementRequest) (*SettlementResponse, error) {
	a, ok := s.store.GetAssessmentByBill(state, req.BillReference)
	if !ok {
		return nil, notFound("BILL_NOT_FOUND", "bill %q not found in state %q", req.BillReference, state)
	}
	if a.Status == StatusPaid {
		// Idempotent webhook redelivery: same outcome, no double split.
		return nil, conflict("ALREADY_SETTLED", "bill %q is already settled", req.BillReference)
	}
	if a.Status != StatusIssued {
		return nil, conflict("BILL_NOT_SETTLEABLE", "bill %q is %s", req.BillReference, a.Status)
	}
	if req.AmountKobo != a.AmountDueKobo {
		return nil, badRequest("AMOUNT_MISMATCH", "payment of %d kobo does not cover amount due %d kobo",
			req.AmountKobo, a.AmountDueKobo)
	}

	pack, ok := s.catalog.Lookup(state, a.RevenueHead)
	if !ok {
		return nil, internalError("POLICY_MISSING", "no split policy for %s/%s", state, a.RevenueHead)
	}
	tenantID, err := splits.StateTenantID(state)
	if err != nil {
		return nil, internalError("TENANT_UNKNOWN", "%v", err)
	}
	params := splits.ChainParams{
		StateTenant: tenantID,
		Ledger:      splits.LedgerNGSovereign,
		BillID:      splits.NewID(),
	}

	// Provision chart accounts and fund the payer clearing account with
	// the inbound payment before the split executes (fake ledger; the
	// production adapter provisions accounts at bootstrap).
	if prov, ok := s.ledger.(accountProvisioner); ok {
		payer, err := params.PayerClearingAccountID()
		if err != nil {
			return nil, internalError("LEDGER_ACCOUNT", "%v", err)
		}
		prov.CreateAccount(payer)
		for _, rule := range pack.InstantRules() {
			acct, err := splits.BuildAccountID(tenantID, 0, rule.AccountCode, splits.Uint128{})
			if err != nil {
				return nil, internalError("LEDGER_ACCOUNT", "%v", err)
			}
			prov.CreateAccount(acct)
		}
		prov.SeedAccount(payer, uint64(req.AmountKobo))
	}

	plan, _, err := splits.SettleGross(s.ledger, pack, uint64(req.AmountKobo), params)
	if err != nil {
		return nil, internalError("LEDGER_SPLIT_FAILED", "atomic split rejected: %v", err)
	}
	if !s.store.MarkPaid(state, a.AssessmentID, s.now().Unix()) {
		return nil, conflict("ALREADY_SETTLED", "bill %q was settled concurrently", req.BillReference)
	}

	resp := &SettlementResponse{
		BillReference:         a.BillReference,
		AssessmentID:          a.AssessmentID,
		Status:                StatusPaid,
		AmountKobo:            req.AmountKobo,
		ClearingRemainderKobo: plan.ClearingRemainder,
		PolicyID:              pack.PolicyID,
		SettledAt:             s.now().UTC().Format(time.RFC3339),
	}
	for _, leg := range plan.InstantLegs {
		resp.SplitLegs = append(resp.SplitLegs, SplitLegView{
			Beneficiary: string(leg.Rule.Beneficiary),
			AccountCode: leg.Rule.AccountCode,
			AmountKobo:  leg.Amount,
			Timing:      string(leg.Rule.Timing),
		})
	}
	for _, leg := range plan.MonthEndLegs {
		resp.SplitLegs = append(resp.SplitLegs, SplitLegView{
			Beneficiary: string(leg.Rule.Beneficiary),
			AccountCode: leg.Rule.AccountCode,
			AmountKobo:  leg.Amount,
			Timing:      string(leg.Rule.Timing),
		})
	}
	return resp, nil
}
