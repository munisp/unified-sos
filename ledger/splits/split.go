package splits

import (
	"errors"
	"fmt"
	"math/bits"
)

// Split computation with deterministic kobo rounding.
//
// The gazetted INSTANT percentages define a "split pool": the portion of
// the gross payment that leaves the payer clearing account immediately.
// Each INSTANT leg except the last is floored to the kobo; the final
// INSTANT leg receives the pool remainder, so the legs always sum exactly
// to the pool (no rounding drift). When INSTANT percentages sum to less
// than 100%, the unallocated remainder stays in the payer clearing
// account until END_OF_MONTH sweeps, per the schema description.

// Leg is a computed split leg in exact kobo.
type Leg struct {
	Rule   SplitRule
	Amount uint64 // kobo
}

// SplitPlan is the full result of splitting a gross payment.
type SplitPlan struct {
	// InstantLegs join the atomic linked transfer chain at settlement.
	InstantLegs []Leg
	// MonthEndLegs are computed for reporting/reconciliation but are
	// excluded from the instant chain; they are swept at the end of the
	// clearing cycle.
	MonthEndLegs []Leg
	// ClearingRemainder stays in the payer clearing account
	// (gross - sum(InstantLegs)).
	ClearingRemainder uint64
	// GrossAmountKobo echoes the input for audit.
	GrossAmountKobo uint64
}

// muldivFloor computes floor(a * b / d) without overflow for a < 2^63
// and b <= TotalBasisPoints, using 128-bit intermediate arithmetic.
func muldivFloor(a, b, d uint64) (uint64, error) {
	if d == 0 {
		return 0, errors.New("splits: division by zero")
	}
	if a >= 1<<63 {
		return 0, fmt.Errorf("splits: amount %d exceeds safe muldiv range", a)
	}
	hi, lo := bits.Mul64(a, b)
	q, _ := bits.Div64(hi, lo, d) // hi < d guaranteed given the bounds above
	return q, nil
}

// ComputeSplit deterministically splits grossKobo according to the
// gazetted rules. It is a pure function: no ledger interaction.
func ComputeSplit(grossKobo uint64, rules []SplitRule) (*SplitPlan, error) {
	var instant, monthEnd []SplitRule
	for _, r := range rules {
		switch r.Timing {
		case TimingInstant:
			instant = append(instant, r)
		case TimingEndOfMonth:
			monthEnd = append(monthEnd, r)
		default:
			return nil, fmt.Errorf("splits: rule for %s has no timing", r.Beneficiary)
		}
	}
	if len(instant) == 0 {
		return nil, errors.New("splits: no INSTANT legs to execute")
	}

	var totalInstantBPS uint64
	for _, r := range instant {
		totalInstantBPS += uint64(r.PercentageBPS)
	}
	if totalInstantBPS == 0 || totalInstantBPS > TotalBasisPoints {
		return nil, fmt.Errorf("splits: INSTANT legs total %d bp (must be in (0, %d])", totalInstantBPS, TotalBasisPoints)
	}

	// The pool is the exact kobo amount that leaves clearing instantly.
	pool, err := muldivFloor(grossKobo, totalInstantBPS, TotalBasisPoints)
	if err != nil {
		return nil, err
	}

	plan := &SplitPlan{GrossAmountKobo: grossKobo}
	allocated := uint64(0)
	for i, r := range instant {
		var amount uint64
		if i == len(instant)-1 {
			// Last INSTANT leg absorbs the pool remainder so legs always
			// sum exactly to the pool regardless of floor rounding.
			amount = pool - allocated
		} else {
			amount, err = muldivFloor(grossKobo, uint64(r.PercentageBPS), TotalBasisPoints)
			if err != nil {
				return nil, err
			}
			allocated += amount
		}
		plan.InstantLegs = append(plan.InstantLegs, Leg{Rule: r, Amount: amount})
	}
	for _, r := range monthEnd {
		amount, err := muldivFloor(grossKobo, uint64(r.PercentageBPS), TotalBasisPoints)
		if err != nil {
			return nil, err
		}
		plan.MonthEndLegs = append(plan.MonthEndLegs, Leg{Rule: r, Amount: amount})
	}
	plan.ClearingRemainder = grossKobo - pool
	return plan, nil
}
