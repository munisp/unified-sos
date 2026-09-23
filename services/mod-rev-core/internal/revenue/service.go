package revenue

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math/big"
	"math/bits"
	"strconv"
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
// contract (tigerbeetle_transfer_pending_id). It is allocation-light
// (math/bits long division, no big.Int) because settlement renders one
// decimal ID per chain leg on the hot path; the output is byte-identical
// to the previous big.Int rendering (see uint128decimal_test.go).
func uint128Decimal(id splits.Uint128) string {
	if id.Hi == 0 {
		return strconv.FormatUint(id.Lo, 10)
	}
	const divisor = 10_000_000_000_000_000_000 // 1e19, largest power of 10 in uint64
	var digits [39]byte                        // 2^128-1 has 39 decimal digits
	pos := len(digits)
	hi, lo := id.Hi, id.Lo
	for hi != 0 {
		qHi := hi / divisor
		r := hi % divisor // r < divisor, safe for bits.Div64
		qLo, rem := bits.Div64(r, lo, divisor)
		// Emit the 19-digit group (least-significant group first).
		for i := 0; i < 19; i++ {
			pos--
			digits[pos] = byte('0' + rem%10)
			rem /= 10
		}
		hi, lo = qHi, qLo
	}
	// Most-significant remainder (hi == 0 here), no leading zeros.
	for lo > 0 {
		pos--
		digits[pos] = byte('0' + lo%10)
		lo /= 10
	}
	// Strip any leading zeros of the most-significant emitted group (kept
	// when the value is an exact multiple of 1e19^k and lo landed at 0).
	for pos < len(digits)-1 && digits[pos] == '0' {
		pos++
	}
	return string(digits[pos:])
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

// provisionAccounts creates the chart accounts used by a settlement chain
// and funds the payer clearing account with the inbound payment (fake
// ledger only; the production adapter provisions accounts at bootstrap).
func (s *Service) provisionAccounts(tenantID uint16, pack *splits.PolicyPack, amountKobo uint64, includeCredit bool) error {
	prov, ok := s.ledger.(accountProvisioner)
	if !ok {
		return nil
	}
	payer, err := splits.BuildAccountID(tenantID, 0, splits.ClassPayerClearing, splits.Uint128{})
	if err != nil {
		return internalError("LEDGER_ACCOUNT", "%v", err)
	}
	prov.CreateAccount(payer)
	for _, rule := range pack.InstantRules() {
		acct, err := splits.BuildAccountID(tenantID, 0, rule.AccountCode, splits.Uint128{})
		if err != nil {
			return internalError("LEDGER_ACCOUNT", "%v", err)
		}
		prov.CreateAccount(acct)
	}
	if includeCredit {
		credit, err := splits.BuildAccountID(tenantID, 0, splits.ClassTaxpayerCredit, splits.Uint128{})
		if err != nil {
			return internalError("LEDGER_ACCOUNT", "%v", err)
		}
		prov.CreateAccount(credit)
	}
	if amountKobo > 0 {
		prov.SeedAccount(payer, amountKobo)
	}
	return nil
}

// buildSettlementChain computes the split for grossKobo and builds the
// deterministic linked chain (plus, when excess > 0, a final leg routing
// the overpayment to the taxpayer credit-balance account 1002).
func (s *Service) buildSettlementChain(tenantID uint16, pack *splits.PolicyPack, chainKey string, billID splits.Uint128, grossKobo, excess uint64) (*splits.SplitPlan, []splits.Transfer, error) {
	params := splits.ChainParams{
		StateTenant:    tenantID,
		Ledger:         splits.LedgerNGSovereign,
		BillID:         billID,
		IdempotencyKey: chainKey,
	}
	plan, err := splits.ComputeSplit(grossKobo, pack.Rules)
	if err != nil {
		return nil, nil, internalError("SPLIT_COMPUTE_FAILED", "%v", err)
	}
	chain, err := splits.BuildAtomicChain(plan, params)
	if err != nil {
		return nil, nil, internalError("LEDGER_SPLIT_FAILED", "%v", err)
	}
	if excess > 0 {
		payer, err := splits.BuildAccountID(tenantID, 0, splits.ClassPayerClearing, splits.Uint128{})
		if err != nil {
			return nil, nil, internalError("LEDGER_ACCOUNT", "%v", err)
		}
		credit, err := splits.BuildAccountID(tenantID, 0, splits.ClassTaxpayerCredit, splits.Uint128{})
		if err != nil {
			return nil, nil, internalError("LEDGER_ACCOUNT", "%v", err)
		}
		chain, err = splits.AppendChainLeg(chain, splits.Transfer{
			ID:              splits.DeterministicID(chainKey, "leg-excess"),
			DebitAccountID:  payer,
			CreditAccountID: credit,
			Amount:          excess,
			Ledger:          splits.LedgerNGSovereign,
			Code:            140, // taxpayer credit memo
			UserData:        billID,
		})
		if err != nil {
			return nil, nil, internalError("LEDGER_SPLIT_FAILED", "%v", err)
		}
	}
	return plan, chain, nil
}

// splitLegViews renders the plan legs for the wire response.
func splitLegViews(plan *splits.SplitPlan) []SplitLegView {
	out := make([]SplitLegView, 0, len(plan.InstantLegs)+len(plan.MonthEndLegs))
	for _, legs := range [][]splits.Leg{plan.InstantLegs, plan.MonthEndLegs} {
		for _, leg := range legs {
			out = append(out, SplitLegView{
				Beneficiary: string(leg.Rule.Beneficiary),
				AccountCode: leg.Rule.AccountCode,
				AmountKobo:  leg.Amount,
				Timing:      string(leg.Rule.Timing),
			})
		}
	}
	return out
}

// SettleBill executes the statutory split for a paid bill (Clause 22.2).
//
// Crash/retry safety: the bill/transfer IDs are deterministic
// (sha256(bill_ref)) and a settlement record is persisted BEFORE the
// linked chain is submitted. A webhook redelivery replays the recorded
// response instead of executing a second split.
//
// Partial payments accumulate (PARTIALLY_PAID) and the split executes once
// the bill is fully paid; an overpayment routes the excess to the
// taxpayer credit-balance account (1002) in the same linked chain.
func (s *Service) SettleBill(state string, req *SettlementRequest) (*SettlementResponse, error) {
	a, ok := s.store.GetAssessmentByBill(state, req.BillReference)
	if !ok {
		return nil, notFound("BILL_NOT_FOUND", "bill %q not found in state %q", req.BillReference, state)
	}

	// Crash/retry replay: a recorded settlement for the same payment is
	// returned (COMMITTED) or resumed (SUBMITTED) without a fresh split.
	if rec, ok := s.store.GetSettlement(state, req.BillReference); ok && rec.AmountKobo == req.AmountKobo {
		switch rec.RecordState {
		case SettlementCommitted:
			resp := rec.Response
			return &resp, nil
		case SettlementSubmitted:
			return s.resumeSettlement(state, a, req, rec)
		}
	}

	if a.Status == StatusPaid {
		return nil, conflict("ALREADY_SETTLED", "bill %q is already settled", req.BillReference)
	}
	if a.Status != StatusIssued && a.Status != StatusPartiallyPaid {
		return nil, conflict("BILL_NOT_SETTLEABLE", "bill %q is %s", req.BillReference, a.Status)
	}
	if req.AmountKobo <= 0 {
		return nil, badRequest("INVALID_AMOUNT", "amount_kobo must be positive")
	}

	// Switch-side idempotency: a redelivered provider reference is a no-op.
	if req.ProviderReference != "" && !s.store.RecordPayment(state, req.ProviderReference) {
		return s.paymentProgress(a), nil
	}

	pack, ok := s.catalog.Lookup(state, a.RevenueHead)
	if !ok {
		return nil, internalError("POLICY_MISSING", "no split policy for %s/%s", state, a.RevenueHead)
	}
	tenantID, err := splits.StateTenantID(state)
	if err != nil {
		return nil, internalError("TENANT_UNKNOWN", "%v", err)
	}

	remaining := a.AmountDueKobo - a.AmountPaidKobo

	// Partial payment: funds accumulate in payer clearing; the split only
	// executes once the bill is fully paid.
	if req.AmountKobo < remaining {
		if err := s.provisionAccounts(tenantID, pack, uint64(req.AmountKobo), false); err != nil {
			return nil, err
		}
		updated, ok := s.store.ApplyPayment(state, a.AssessmentID, req.AmountKobo, s.now().Unix())
		if !ok {
			return nil, conflict("BILL_NOT_SETTLEABLE", "bill %q is %s", req.BillReference, a.Status)
		}
		return &SettlementResponse{
			BillReference:  a.BillReference,
			AssessmentID:   a.AssessmentID,
			Status:         updated.Status, // PARTIALLY_PAID
			AmountKobo:     req.AmountKobo,
			AmountPaidKobo: updated.AmountPaidKobo,
			PolicyID:       pack.PolicyID,
			SettledAt:      s.now().UTC().Format(time.RFC3339),
		}, nil
	}

	// Final payment: split the FULL amount due (exact-amount behaviour is
	// the special case AmountPaidKobo == 0 and no excess).
	excess := uint64(req.AmountKobo - remaining)
	gross := uint64(a.AmountDueKobo)
	billID := splits.DeterministicIDFromRef(req.BillReference)
	chainKey := req.BillReference
	now := s.now() // capture once: 3+ clock reads in this hot path otherwise

	if err := s.provisionAccounts(tenantID, pack, uint64(req.AmountKobo), excess > 0); err != nil {
		return nil, err
	}
	plan, chain, err := s.buildSettlementChain(tenantID, pack, chainKey, billID, gross, excess)
	if err != nil {
		return nil, err
	}

	resp := SettlementResponse{
		BillReference:         a.BillReference,
		AssessmentID:          a.AssessmentID,
		Status:                StatusPaid,
		AmountKobo:            req.AmountKobo,
		AmountPaidKobo:        a.AmountDueKobo,
		ExcessCreditedKobo:    excess,
		SplitLegs:             splitLegViews(plan),
		ClearingRemainderKobo: plan.ClearingRemainder,
		PolicyID:              pack.PolicyID,
		SettledAt:             now.UTC().Format(time.RFC3339),
	}
	rec := &SettlementRecord{
		TenantState:   state,
		BillReference: a.BillReference,
		AssessmentID:  a.AssessmentID,
		RevenueHead:   a.RevenueHead,
		ChainKey:      chainKey,
		GrossKobo:     gross,
		AmountKobo:    req.AmountKobo,
		ExcessKobo:    excess,
		BillID:        uint128Decimal(billID),
		RecordState:   SettlementSubmitted,
		Response:      resp,
		CreatedAt:     now,
	}
	rec.ChainTransferIDs = make([]string, 0, len(chain))
	for _, tr := range chain {
		rec.ChainTransferIDs = append(rec.ChainTransferIDs, uint128Decimal(tr.ID))
	}
	// Persist BEFORE chain submission: a crash here is resumed from the
	// record, never re-executed with fresh IDs.
	s.store.SaveSettlement(rec)

	if err := splits.SubmitChainIdempotent(s.ledger, chain); err != nil {
		return nil, internalError("LEDGER_SPLIT_FAILED", "atomic split rejected: %v", err)
	}
	rec.RecordState = SettlementCommitted
	s.store.SaveSettlement(rec)

	if _, ok := s.store.ApplyPayment(state, a.AssessmentID, remaining, now.Unix()); !ok {
		return nil, conflict("ALREADY_SETTLED", "bill %q was settled concurrently", req.BillReference)
	}
	out := rec.Response
	return &out, nil
}

// resumeSettlement completes a settlement whose record was persisted but
// whose commit marker was lost to a crash. The chain IDs are
// deterministic, so resubmission is a ledger no-op when the original
// chain did commit.
func (s *Service) resumeSettlement(state string, a *Assessment, req *SettlementRequest, rec *SettlementRecord) (*SettlementResponse, error) {
	pack, ok := s.catalog.Lookup(state, rec.RevenueHead)
	if !ok {
		return nil, internalError("POLICY_MISSING", "no split policy for %s/%s", state, rec.RevenueHead)
	}
	tenantID, err := splits.StateTenantID(state)
	if err != nil {
		return nil, internalError("TENANT_UNKNOWN", "%v", err)
	}
	if err := s.provisionAccounts(tenantID, pack, 0, rec.ExcessKobo > 0); err != nil {
		return nil, err
	}
	billID := splits.DeterministicIDFromRef(req.BillReference)
	_, chain, err := s.buildSettlementChain(tenantID, pack, rec.ChainKey, billID, rec.GrossKobo, rec.ExcessKobo)
	if err != nil {
		return nil, err
	}
	if err := splits.SubmitChainIdempotent(s.ledger, chain); err != nil {
		return nil, internalError("LEDGER_SPLIT_FAILED", "atomic split rejected: %v", err)
	}
	rec.RecordState = SettlementCommitted
	s.store.SaveSettlement(rec)
	if _, ok := s.store.ApplyPayment(state, a.AssessmentID, a.AmountDueKobo-a.AmountPaidKobo, s.now().Unix()); !ok && a.Status != StatusPaid {
		return nil, conflict("ALREADY_SETTLED", "bill %q was settled concurrently", req.BillReference)
	}
	resp := rec.Response
	return &resp, nil
}

// paymentProgress reports the accumulated payment position for a duplicate
// webhook delivery (same provider reference) without re-applying funds.
func (s *Service) paymentProgress(a *Assessment) *SettlementResponse {
	return &SettlementResponse{
		BillReference:  a.BillReference,
		AssessmentID:   a.AssessmentID,
		Status:         a.Status,
		AmountKobo:     0,
		AmountPaidKobo: a.AmountPaidKobo,
		SettledAt:      s.now().UTC().Format(time.RFC3339),
	}
}

// checkDualControl enforces four-eyes approval: officer and approver must
// be present and distinct actors.
func checkDualControl(officerID, approverID string) *APIError {
	if officerID == "" || approverID == "" {
		return badRequest("DUAL_CONTROL_REQUIRED", "officer_id and approver_id are required")
	}
	if officerID == approverID {
		return conflict("DUAL_CONTROL_VIOLATION",
			"officer %q cannot approve their own request; a distinct approver is required", officerID)
	}
	return nil
}

// currentChain rebuilds the deterministic chain that reflects the bill's
// CURRENT allocation (after any corrections) from its settlement record.
// includeExcess=false excludes the taxpayer-credit leg (used by
// corrections, which only re-allocate the revenue split).
func (s *Service) currentChain(state string, rec *SettlementRecord, includeExcess bool) (*splits.SplitPlan, []splits.Transfer, error) {
	pack, ok := s.catalog.Lookup(state, rec.RevenueHead)
	if !ok {
		return nil, nil, internalError("POLICY_MISSING", "no split policy for %s/%s", state, rec.RevenueHead)
	}
	tenantID, err := splits.StateTenantID(state)
	if err != nil {
		return nil, nil, internalError("TENANT_UNKNOWN", "%v", err)
	}
	excess := rec.ExcessKobo
	if !includeExcess {
		excess = 0
	}
	billID := splits.DeterministicIDFromRef(rec.BillReference)
	plan, chain, err := s.buildSettlementChain(tenantID, pack, rec.ChainKey, billID, rec.GrossKobo, excess)
	if err != nil {
		return nil, nil, err
	}
	// Provision accounts for the fake ledger (production: bootstrapped).
	if perr := s.provisionAccounts(tenantID, pack, 0, includeExcess && rec.ExcessKobo > 0); perr != nil {
		return nil, nil, perr
	}
	return plan, chain, nil
}

// Refund reverses a settled bill: the committed split chain is mirrored
// (debit/credit swapped) as one atomic linked chain, under dual control,
// with a hash-chained audit event.
func (s *Service) Refund(state string, req *RefundRequest) (*RefundResponse, error) {
	if err := checkDualControl(req.OfficerID, req.ApproverID); err != nil {
		return nil, err
	}
	a, ok := s.store.GetAssessmentByBill(state, req.BillReference)
	if !ok {
		return nil, notFound("BILL_NOT_FOUND", "bill %q not found in state %q", req.BillReference, state)
	}
	if a.Status != StatusPaid {
		return nil, conflict("BILL_NOT_REFUNDABLE", "bill %q is %s; only PAID bills can be refunded", req.BillReference, a.Status)
	}
	rec, ok := s.store.GetSettlement(state, req.BillReference)
	if !ok || rec.RecordState != SettlementCommitted {
		return nil, conflict("SETTLEMENT_RECORD_MISSING", "no committed settlement record for bill %q", req.BillReference)
	}
	_, chain, err := s.currentChain(state, rec, true)
	if err != nil {
		return nil, err
	}
	reversal, err := splits.ExecuteReversalChain(s.ledger, chain, rec.ChainKey)
	if err != nil {
		return nil, internalError("REFUND_CHAIN_FAILED", "%v", err)
	}
	if !s.store.SetStatus(state, a.AssessmentID, StatusRefunded) {
		return nil, internalError("STATUS_UPDATE_FAILED", "assessment %q missing", a.AssessmentID)
	}
	ev := s.store.AppendAudit(AuditEvent{
		TenantState:   state,
		Actor:         req.OfficerID + "+" + req.ApproverID,
		Action:        "REFUND",
		BillReference: req.BillReference,
		Detail:        fmt.Sprintf("reason=%q amount=%d", req.Reason, rec.GrossKobo),
		Timestamp:     s.now().UTC().Format(time.RFC3339),
	})
	ids := make([]string, 0, len(reversal))
	for _, tr := range reversal {
		ids = append(ids, uint128Decimal(tr.ID))
	}
	return &RefundResponse{
		BillReference:       a.BillReference,
		AssessmentID:        a.AssessmentID,
		Status:              StatusRefunded,
		RefundedKobo:        rec.GrossKobo + rec.ExcessKobo,
		ReversalTransferIDs: ids,
		AuditHash:           ev.Hash,
		RefundedAt:          s.now().UTC().Format(time.RFC3339),
	}, nil
}

// CorrectMisallocation fixes a payment split under the wrong revenue head:
// the wrong split is reversed and the correct split executed as ONE atomic
// linked chain (both legs audited), under dual control.
func (s *Service) CorrectMisallocation(state string, req *CorrectionRequest) (*CorrectionResponse, error) {
	if err := checkDualControl(req.OfficerID, req.ApproverID); err != nil {
		return nil, err
	}
	if req.CorrectRevenueHead == "" {
		return nil, badRequest("MISSING_REVENUE_HEAD", "correct_revenue_head is required")
	}
	a, ok := s.store.GetAssessmentByBill(state, req.BillReference)
	if !ok {
		return nil, notFound("BILL_NOT_FOUND", "bill %q not found in state %q", req.BillReference, state)
	}
	if a.Status != StatusPaid {
		return nil, conflict("BILL_NOT_CORRECTABLE", "bill %q is %s; only PAID bills can be corrected", req.BillReference, a.Status)
	}
	if req.CorrectRevenueHead == a.RevenueHead {
		return nil, badRequest("NO_OP_CORRECTION", "correct_revenue_head equals the current revenue head %q", a.RevenueHead)
	}
	newPack, ok := s.catalog.Lookup(state, req.CorrectRevenueHead)
	if !ok {
		return nil, badRequest("UNKNOWN_REVENUE_HEAD", "revenue_head %q is not catalogued for state %q (heads: %v)",
			req.CorrectRevenueHead, state, s.catalog.RevenueHeads(state))
	}
	fromHead := a.RevenueHead
	rec, ok := s.store.GetSettlement(state, req.BillReference)
	if !ok || rec.RecordState != SettlementCommitted {
		return nil, conflict("SETTLEMENT_RECORD_MISSING", "no committed settlement record for bill %q", req.BillReference)
	}
	tenantID, err := splits.StateTenantID(state)
	if err != nil {
		return nil, internalError("TENANT_UNKNOWN", "%v", err)
	}

	// Reverse the CURRENT (wrong) allocation and build the correct chain.
	// The taxpayer-credit (overpayment) leg is revenue-head agnostic and
	// is left untouched by the correction.
	_, wrongChain, err := s.currentChain(state, rec, false)
	if err != nil {
		return nil, err
	}
	reversal, err := splits.BuildReversalChain(wrongChain, rec.ChainKey)
	if err != nil {
		return nil, internalError("CORRECTION_CHAIN_FAILED", "%v", err)
	}
	correctionKey := rec.ChainKey + "|correction"
	billID := splits.DeterministicIDFromRef(req.BillReference)
	if err := s.provisionAccounts(tenantID, newPack, 0, false); err != nil {
		return nil, err
	}
	newPlan, correction, err := s.buildSettlementChain(tenantID, newPack, correctionKey, billID, rec.GrossKobo, 0)
	if err != nil {
		return nil, err
	}
	// One atomic batch: reversal legs + correction legs in a single chain.
	batch := append(reversal, correction...)
	for i := range batch[:len(batch)-1] {
		batch[i].Flags.Linked = true
	}
	if err := splits.SubmitChainIdempotent(s.ledger, batch); err != nil {
		return nil, internalError("CORRECTION_CHAIN_FAILED", "%v", err)
	}

	if !s.store.UpdateRevenueHead(state, a.AssessmentID, req.CorrectRevenueHead) {
		return nil, internalError("STATUS_UPDATE_FAILED", "assessment %q missing", a.AssessmentID)
	}
	// The settlement record now tracks the corrected allocation so a later
	// refund reverses the corrected chain, not the original one.
	rec.RevenueHead = req.CorrectRevenueHead
	rec.ChainKey = correctionKey
	rec.ChainTransferIDs = nil
	for _, tr := range correction {
		rec.ChainTransferIDs = append(rec.ChainTransferIDs, uint128Decimal(tr.ID))
	}
	rec.Response.PolicyID = newPack.PolicyID
	rec.Response.SplitLegs = splitLegViews(newPlan)
	s.store.SaveSettlement(rec)

	actors := req.OfficerID + "+" + req.ApproverID
	evRev := s.store.AppendAudit(AuditEvent{
		TenantState:   state,
		Actor:         actors,
		Action:        "CORRECTION_REVERSAL",
		BillReference: req.BillReference,
		Detail:        fmt.Sprintf("from=%q reason=%q", fromHead, req.Reason),
		Timestamp:     s.now().UTC().Format(time.RFC3339),
	})
	evFwd := s.store.AppendAudit(AuditEvent{
		TenantState:   state,
		Actor:         actors,
		Action:        "CORRECTION_SPLIT",
		BillReference: req.BillReference,
		Detail:        fmt.Sprintf("to=%q reason=%q", req.CorrectRevenueHead, req.Reason),
		Timestamp:     s.now().UTC().Format(time.RFC3339),
	})
	return &CorrectionResponse{
		BillReference:   a.BillReference,
		AssessmentID:    a.AssessmentID,
		FromRevenueHead: fromHead,
		ToRevenueHead:   req.CorrectRevenueHead,
		ReversalLegs:    legsFromChain(wrongChain),
		CorrectionLegs:  splitLegViews(newPlan),
		AuditHashes:     []string{evRev.Hash, evFwd.Hash},
		CorrectedAt:     s.now().UTC().Format(time.RFC3339),
	}, nil
}

// legsFromChain renders raw chain transfers for audit reporting.
func legsFromChain(chain []splits.Transfer) []SplitLegView {
	out := make([]SplitLegView, 0, len(chain))
	for _, tr := range chain {
		out = append(out, SplitLegView{
			Beneficiary: "REVERSAL_OF_MISALLOCATION",
			AccountCode: 0,
			AmountKobo:  tr.Amount,
			Timing:      "INSTANT",
		})
	}
	return out
}
