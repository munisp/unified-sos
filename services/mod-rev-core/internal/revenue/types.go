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
	Status                string         `json:"status"` // PAID
	AmountKobo            int64          `json:"amount_kobo"`
	SplitLegs             []SplitLegView `json:"split_legs"`
	ClearingRemainderKobo uint64         `json:"clearing_remainder_kobo"`
	PolicyID              string         `json:"policy_id"`
	SettledAt             string         `json:"settled_at"`
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
	BillReference                string
	TigerbeetleTransferPendingID string
	Status                       string // ISSUED, PAID, DISPUTED, CANCELLED
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
	StatusIssued    = "ISSUED"
	StatusPaid      = "PAID"
	StatusDisputed  = "DISPUTED"
	StatusCancelled = "CANCELLED"
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
