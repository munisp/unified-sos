package splits

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"regexp"
)

// Policy-pack loader for contracts/policy-packs/revenue-split.schema.json.
//
// Policy packs are the declarative, gazetted split formulas injected per
// state without recompiling services (CONTRIBUTING.md rule 1). Percentages
// are normalised to basis points (1 bp = 0.01%) so downstream kobo
// arithmetic is exact integer math.

// BasisPointsPerPercent converts whole percentages to basis points.
const BasisPointsPerPercent = 100

// TotalBasisPoints is 100% in basis points.
const TotalBasisPoints = 100 * BasisPointsPerPercent

// DeductionTiming controls when a split leg executes.
type DeductionTiming string

const (
	// TimingInstant legs join the atomic linked chain at settlement.
	TimingInstant DeductionTiming = "INSTANT"
	// TimingEndOfMonth legs are excluded from the instant chain and swept
	// at the end of the clearing cycle.
	TimingEndOfMonth DeductionTiming = "END_OF_MONTH"
)

// Beneficiary identifies a gazetted split destination.
type Beneficiary string

const (
	BeneficiaryConsolidatedRevenueFund Beneficiary = "STATE_CONSOLIDATED_REVENUE_FUND"
	BeneficiaryMDARetention            Beneficiary = "MDA_RETENTION_ACCOUNT"
	BeneficiaryConcessionaireEscrow    Beneficiary = "PPP_TECH_CONCESSIONAIRE_ESCROW"
	BeneficiaryLocalGovernmentShare    Beneficiary = "LOCAL_GOVERNMENT_SHARE_POOL"
	BeneficiaryTransportUnion          Beneficiary = "TRANSPORT_UNION_COMMISSION"
	BeneficiarySecurityTrustFund       Beneficiary = "SECURITY_TRUST_FUND"
)

var (
	policyIDPattern    = regexp.MustCompile(`^POL_[A-Z0-9_]+$`)
	revenueHeadPattern = regexp.MustCompile(`^REV_[A-Z0-9_]+$`)
)

// ValidStateTenant reports whether s is one of the six SOS pilot states.
func ValidStateTenant(s string) bool {
	switch s {
	case "lagos", "ogun", "osun", "benue", "nasarawa", "taraba":
		return true
	}
	return false
}

// StateTenantID maps a state slug to its chart-of-accounts tenant ID.
func StateTenantID(state string) (uint16, error) {
	switch state {
	case "lagos":
		return StateLagos, nil
	case "ogun":
		return StateOgun, nil
	case "osun":
		return StateOsun, nil
	case "benue":
		return StateBenue, nil
	case "nasarawa":
		return StateNasarawa, nil
	case "taraba":
		return StateTaraba, nil
	}
	return 0, fmt.Errorf("splits: unknown state tenant %q", state)
}

// SplitRule is one statutory split leg. PercentageBPS is the gazetted
// share in basis points (e.g. 33.33% = 3333).
type SplitRule struct {
	Beneficiary       Beneficiary
	BeneficiaryDetail string
	AccountCode       uint16 // chart-of-accounts class code (1000..9999)
	PercentageBPS     uint32
	Timing            DeductionTiming
	TransferCode      uint16 // ledger transfer code; zero → derive from beneficiary
}

// Guardrails captures the concession guardrail block (Clause 22.2
// procurement ceilings) for audit; enforcement of step-down schedules is
// a clearing-cycle concern, not an instant-split concern.
type Guardrails struct {
	RevenueShareCeilingPct float64         `json:"revenue_share_ceiling_pct"`
	IRRCapPct              float64         `json:"irr_cap_pct"`
	StepDownSchedule       []StepDownEntry `json:"step_down_schedule"`
}

// StepDownEntry is one rung of the concessionaire step-down schedule.
type StepDownEntry struct {
	UpToCumulativeIGRKobo uint64  `json:"up_to_cumulative_igr_kobo"`
	SharePct              float64 `json:"share_pct"`
}

// PolicyPack is a parsed and validated revenue-split policy pack.
type PolicyPack struct {
	TenantStateID    string
	PolicyID         string
	RevenueHead      string
	EffectiveDate    string
	GazetteReference string
	Rules            []SplitRule
	Guardrails       *Guardrails
}

// policyPackJSON mirrors the wire format of revenue-split.schema.json.
// Percentages decode as json.Number to preserve decimal precision.
type policyPackJSON struct {
	TenantStateID    string          `json:"tenant_state_id"`
	PolicyID         string          `json:"policy_id"`
	RevenueHead      string          `json:"revenue_head"`
	EffectiveDate    string          `json:"effective_date"`
	GazetteReference string          `json:"gazette_reference"`
	Rules            []splitRuleJSON `json:"statutory_split_rules"`
	Guardrails       *Guardrails     `json:"concession_guardrails"`
}

type splitRuleJSON struct {
	Beneficiary       string      `json:"beneficiary"`
	BeneficiaryDetail string      `json:"beneficiary_detail"`
	AccountCode       int64       `json:"tigerbeetle_account_code"`
	SplitPercentage   json.Number `json:"split_percentage"`
	DeductionTiming   string      `json:"deduction_timing"`
}

// ParsePolicyPack parses and validates a policy pack document against the
// rules of contracts/policy-packs/revenue-split.schema.json.
func ParsePolicyPack(data []byte) (*PolicyPack, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	dec.DisallowUnknownFields()
	var raw policyPackJSON
	if err := dec.Decode(&raw); err != nil {
		return nil, fmt.Errorf("splits: invalid policy pack JSON: %w", err)
	}

	if !ValidStateTenant(raw.TenantStateID) {
		return nil, fmt.Errorf("splits: invalid tenant_state_id %q", raw.TenantStateID)
	}
	if !policyIDPattern.MatchString(raw.PolicyID) {
		return nil, fmt.Errorf("splits: invalid policy_id %q (want ^POL_[A-Z0-9_]+$)", raw.PolicyID)
	}
	if !revenueHeadPattern.MatchString(raw.RevenueHead) {
		return nil, fmt.Errorf("splits: invalid revenue_head %q (want ^REV_[A-Z0-9_]+$)", raw.RevenueHead)
	}
	if len(raw.Rules) < 2 {
		return nil, fmt.Errorf("splits: statutory_split_rules needs at least 2 legs, got %d", len(raw.Rules))
	}

	pack := &PolicyPack{
		TenantStateID:    raw.TenantStateID,
		PolicyID:         raw.PolicyID,
		RevenueHead:      raw.RevenueHead,
		EffectiveDate:    raw.EffectiveDate,
		GazetteReference: raw.GazetteReference,
		Guardrails:       raw.Guardrails,
	}

	var instantBPS uint64
	for i, r := range raw.Rules {
		rule, err := parseRule(r)
		if err != nil {
			return nil, fmt.Errorf("splits: rule %d: %w", i, err)
		}
		if rule.Timing == TimingInstant {
			instantBPS += uint64(rule.PercentageBPS)
		}
		pack.Rules = append(pack.Rules, rule)
	}
	if instantBPS == 0 {
		return nil, fmt.Errorf("splits: policy %s has no INSTANT legs", raw.PolicyID)
	}
	if instantBPS > TotalBasisPoints {
		return nil, fmt.Errorf("splits: INSTANT legs sum to %.2f%% > 100%%", float64(instantBPS)/BasisPointsPerPercent)
	}
	return pack, nil
}

func parseRule(r splitRuleJSON) (SplitRule, error) {
	var rule SplitRule
	switch Beneficiary(r.Beneficiary) {
	case BeneficiaryConsolidatedRevenueFund,
		BeneficiaryMDARetention,
		BeneficiaryConcessionaireEscrow,
		BeneficiaryLocalGovernmentShare,
		BeneficiaryTransportUnion,
		BeneficiarySecurityTrustFund:
		rule.Beneficiary = Beneficiary(r.Beneficiary)
	default:
		return rule, fmt.Errorf("unknown beneficiary %q", r.Beneficiary)
	}
	if r.AccountCode < 1000 || r.AccountCode > 9999 {
		return rule, fmt.Errorf("tigerbeetle_account_code %d out of range [1000, 9999]", r.AccountCode)
	}
	rule.AccountCode = uint16(r.AccountCode)
	switch DeductionTiming(r.DeductionTiming) {
	case TimingInstant, TimingEndOfMonth:
		rule.Timing = DeductionTiming(r.DeductionTiming)
	default:
		return rule, fmt.Errorf("unknown deduction_timing %q", r.DeductionTiming)
	}
	pct, err := r.SplitPercentage.Float64()
	if err != nil {
		return rule, fmt.Errorf("invalid split_percentage %q", r.SplitPercentage.String())
	}
	if pct < 0 || pct > 100 {
		return rule, fmt.Errorf("split_percentage %.4f out of range [0, 100]", pct)
	}
	// Percentages must be exact at 0.01% (1 bp) precision so kobo
	// arithmetic stays deterministic integer math.
	scaled := pct * BasisPointsPerPercent
	if math.Abs(scaled-math.Round(scaled)) > 1e-9 {
		return rule, fmt.Errorf("split_percentage %q exceeds 0.01%% precision", r.SplitPercentage.String())
	}
	rule.PercentageBPS = uint32(math.Round(scaled))
	rule.BeneficiaryDetail = r.BeneficiaryDetail
	return rule, nil
}

// LoadPolicyPackFile reads and validates a policy pack from disk.
func LoadPolicyPackFile(path string) (*PolicyPack, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("splits: read policy pack: %w", err)
	}
	return ParsePolicyPack(data)
}

// InstantRules returns only the INSTANT legs, in gazetted order.
func (p *PolicyPack) InstantRules() []SplitRule {
	var out []SplitRule
	for _, r := range p.Rules {
		if r.Timing == TimingInstant {
			out = append(out, r)
		}
	}
	return out
}

// MonthEndRules returns only the END_OF_MONTH legs, in gazetted order.
func (p *PolicyPack) MonthEndRules() []SplitRule {
	var out []SplitRule
	for _, r := range p.Rules {
		if r.Timing == TimingEndOfMonth {
			out = append(out, r)
		}
	}
	return out
}
