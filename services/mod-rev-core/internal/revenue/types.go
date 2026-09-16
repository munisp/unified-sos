package revenue

import "time"

// Wire types mirror contracts/openapi/revenue-assessments.yaml.

// AssessmentRequest is the createTaxAssessment request body.
type AssessmentRequest struct {
	TaxpayerSTIN            string    `json:"taxpayer_stin"`
	MDACode                 string    `json:"mda_code"`
	RevenueHead             string    `json:"revenue_head"`
	TaxPeriodYear           int       `json:"tax_period_year"`
	GrossIncomeKobo         int64     `json:"gross_income_kobo"`
	AllowableDeductionsKobo int64     `json:"allowable_deductions_kobo"`
	CalculatedTaxKobo       int64     `json:"calculated_tax_kobo"`
	Metadata                *Metadata `json:"metadata,omitempty"`
}

// Metadata carries optional assessor context.
type Metadata struct {
	LGACode             string `json:"lga_code,omitempty"`
	AssessmentOfficerID string `json:"assessment_officer_id,omitempty"`
}

// AssessmentResponse is the createTaxAssessment 201 response body.
type AssessmentResponse struct {
	AssessmentID                 string `json:"assessment_id"`
	BillReference                string `json:"bill_reference"`
	TigerbeetleTransferPendingID string `json:"tigerbeetle_transfer_pending_id"`
	AmountDueKobo                int64  `json:"amount_due_kobo"`
	PaymentQRPayload             string `json:"payment_qr_payload"`
	CreatedAt                    string `json:"created_at"`
}

// SettlementRequest is the payments webhook body posted by the clearing
// switch (Mojaloop/NIBSS) once the payer's funds land in clearing.
type SettlementRequest struct {
	BillReference     string `json:"bill_reference"`
	AmountKobo        int64  `json:"amount_kobo"`
	Channel           string `json:"channel"`            // NIP, REMITA, POS, USSD, ...
	ProviderReference string `json:"provider_reference"` // switch-side idempotency handle
}

// SplitLegView reports one executed statutory split leg.
type SplitLegView struct {
	Beneficiary string `json:"beneficiary"`
	AccountCode uint16 `json:"tigerbeetle_account_code"`
	AmountKobo  uint64 `json:"amount_kobo"`
	Timing      string `json:"deduction_timing"`
}

// SettlementResponse confirms settlement and the executed split.
type SettlementResponse struct {
	BillReference         string         `json:"bill_reference"`
	AssessmentID          string         `json:"assessment_id"`
	Status                string         `json:"status"` // PAID or PARTIALLY_PAID
	AmountKobo            int64          `json:"amount_kobo"`
	AmountPaidKobo        int64          `json:"amount_paid_kobo"`
	ExcessCreditedKobo    uint64         `json:"excess_credited_kobo,omitempty"`
	SplitLegs             []SplitLegView `json:"split_legs"`
	ClearingRemainderKobo uint64         `json:"clearing_remainder_kobo"`
	PolicyID              string         `json:"policy_id"`
	SettledAt             string         `json:"settled_at"`
}

// Settlement lifecycle states for the persisted settlement record.
const (
	// SettlementSubmitted is persisted BEFORE the chain is submitted;
	// a replay of the same payment resumes/returns it instead of
	// re-executing a fresh split.
	SettlementSubmitted = "SUBMITTED"
	// SettlementCommitted means the linked chain committed.
	SettlementCommitted = "COMMITTED"
)

// SettlementRecord is the crash/retry guard persisted before the ledger
// chain is submitted: it pins the deterministic bill/transfer IDs and the
// response, so a webhook redelivery never executes a second split.
type SettlementRecord struct {
	TenantState      string
	BillReference    string
	AssessmentID     string
	RevenueHead      string // pack in force for the CURRENT allocation
	ChainKey         string // deterministic transfer-ID key (changes on correction)
	GrossKobo        uint64 // amount that was split (full amount due)
	AmountKobo       int64  // inbound payment that triggered settlement
	ExcessKobo       uint64 // overpayment routed to ClassTaxpayerCredit
	BillID           string // deterministic sha256(bill_ref) as decimal uint128
	ChainTransferIDs []string
	RecordState      string // SUBMITTED → COMMITTED
	Response         SettlementResponse
	CreatedAt        time.Time
}

// RefundRequest is the POST refunds body. Dual control: OfficerID and
// ApproverID must be present and distinct.
type RefundRequest struct {
	BillReference string `json:"bill_reference"`
	OfficerID     string `json:"officer_id"`
	ApproverID    string `json:"approver_id"`
	Reason        string `json:"reason"`
}

// RefundResponse confirms the executed reversal chain.
type RefundResponse struct {
	BillReference       string   `json:"bill_reference"`
	AssessmentID        string   `json:"assessment_id"`
	Status              string   `json:"status"` // REFUNDED
	RefundedKobo        uint64   `json:"refunded_kobo"`
	ReversalTransferIDs []string `json:"reversal_transfer_ids"`
	AuditHash           string   `json:"audit_hash"`
	RefundedAt          string   `json:"refunded_at"`
}

// CorrectionRequest is the POST corrections body: a misallocated payment
// is fixed by reversing the wrong split and executing the correct one as
// a single atomic linked chain. Dual control required.
type CorrectionRequest struct {
	BillReference      string `json:"bill_reference"`
	CorrectRevenueHead string `json:"correct_revenue_head"`
	OfficerID          string `json:"officer_id"`
	ApproverID         string `json:"approver_id"`
	Reason             string `json:"reason"`
}

// CorrectionResponse confirms the compensating chain.
type CorrectionResponse struct {
	BillReference   string         `json:"bill_reference"`
	AssessmentID    string         `json:"assessment_id"`
	FromRevenueHead string         `json:"from_revenue_head"`
	ToRevenueHead   string         `json:"to_revenue_head"`
	ReversalLegs    []SplitLegView `json:"reversal_legs"`
	CorrectionLegs  []SplitLegView `json:"correction_legs"`
	AuditHashes     []string       `json:"audit_hashes"`
	CorrectedAt     string         `json:"corrected_at"`
}

// AuditEvent is one link of the hash-chained audit trail for sensitive
// operations (refunds, corrections). Hash commits to PrevHash so the
// chain is tamper-evident.
type AuditEvent struct {
	Seq           uint64 `json:"seq"`
	TenantState   string `json:"tenant_state"`
	Actor         string `json:"actor"`
	Action        string `json:"action"`
	BillReference string `json:"bill_reference"`
	Detail        string `json:"detail"`
	Timestamp     string `json:"timestamp"`
	PrevHash      string `json:"prev_hash"`
	Hash          string `json:"hash"`
}

// ErrorResponse is the uniform error body for 4xx/5xx.
type ErrorResponse struct {
	Error APIErrorBody `json:"error"`
}

// APIErrorBody is the machine-readable error payload.
type APIErrorBody struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// Assessment is the stored domain record (balances never live here —
// ADR-002; TigerBeetle owns money).
type Assessment struct {
	AssessmentID                 string
	State                        string
	TaxpayerSTIN                 string
	MDACode                      string
	RevenueHead                  string
	TaxPeriodYear                int
	GrossIncomeKobo              int64
	AllowableDeductionsKobo      int64
	CalculatedTaxKobo            int64
	AmountDueKobo                int64
	AmountPaidKobo               int64 // accumulates across (partial) payments
	BillReference                string
	TigerbeetleTransferPendingID string
	Status                       string // ISSUED, PARTIALLY_PAID, PAID, REFUNDED, DISPUTED, CANCELLED
	IdempotencyKey               string
	RequestHash                  string
	Metadata                     *Metadata
	CreatedAt                    time.Time
	PaidAt                       *time.Time
}

// Taxpayer is the STIN registry record (db/migrations/0002 shape,
// in-memory).
type Taxpayer struct {
	STIN         string
	State        string
	TaxpayerType string
	DisplayName  string
	LGACode      string
	CreatedAt    time.Time
}

// Assessment lifecycle statuses.
const (
	StatusIssued        = "ISSUED"
	StatusPartiallyPaid = "PARTIALLY_PAID"
	StatusPaid          = "PAID"
	StatusRefunded      = "REFUNDED"
	StatusDisputed      = "DISPUTED"
	StatusCancelled     = "CANCELLED"
)

// Response converts the stored record to the contract response shape.
func (a *Assessment) Response() AssessmentResponse {
	return AssessmentResponse{
		AssessmentID:                 a.AssessmentID,
		BillReference:                a.BillReference,
		TigerbeetleTransferPendingID: a.TigerbeetleTransferPendingID,
		AmountDueKobo:                a.AmountDueKobo,
		PaymentQRPayload:             "https://pay." + a.State + ".gov.ng/pay/" + a.BillReference,
		CreatedAt:                    a.CreatedAt.UTC().Format(time.RFC3339),
	}
}
