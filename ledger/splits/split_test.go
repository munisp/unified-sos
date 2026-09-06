package splits

import "testing"

func instantRule(b Beneficiary, code uint16, bps uint32) SplitRule {
	return SplitRule{Beneficiary: b, AccountCode: code, PercentageBPS: bps, Timing: TimingInstant}
}

func sumLegs(legs []Leg) uint64 {
	var total uint64
	for _, l := range legs {
		total += l.Amount
	}
	return total
}

func TestComputeSplitSimple(t *testing.T) {
	// 65/20/15 summing to exactly 100%: entire gross leaves clearing.
	rules := []SplitRule{
		instantRule(BeneficiaryConsolidatedRevenueFund, 3001, 6500),
		instantRule(BeneficiaryMDARetention, 2010, 2000),
		instantRule(BeneficiaryConcessionaireEscrow, 2099, 1500),
	}
	plan, err := ComputeSplit(100_000, rules)
	if err != nil {
		t.Fatal(err)
	}
	want := []uint64{65_000, 20_000, 15_000}
	for i, l := range plan.InstantLegs {
		if l.Amount != want[i] {
			t.Fatalf("leg %d: want %d got %d", i, want[i], l.Amount)
		}
	}
	if plan.ClearingRemainder != 0 {
		t.Fatalf("remainder: want 0 got %d", plan.ClearingRemainder)
	}
}

func TestComputeSplitThirdsRoundExactly(t *testing.T) {
	// 33.33/33.33/33.34 over an awkward gross: legs must sum to the
	// gross with zero rounding drift (the last leg absorbs the pool
	// remainder).
	rules := []SplitRule{
		instantRule(BeneficiaryConsolidatedRevenueFund, 3001, 3333),
		instantRule(BeneficiaryMDARetention, 2010, 3333),
		instantRule(BeneficiaryConcessionaireEscrow, 2099, 3334),
	}
	for _, gross := range []uint64{1, 3, 101, 999, 100_001, 9_999_999_999} {
		plan, err := ComputeSplit(gross, rules)
		if err != nil {
			t.Fatal(err)
		}
		if sumLegs(plan.InstantLegs) != gross {
			t.Fatalf("gross %d: legs sum to %d", gross, sumLegs(plan.InstantLegs))
		}
		if plan.ClearingRemainder != 0 {
			t.Fatalf("gross %d: remainder %d", gross, plan.ClearingRemainder)
		}
		// First legs are floored; last leg takes the remainder.
		wantFirst := gross * 3333 / 10000
		if plan.InstantLegs[0].Amount != wantFirst {
			t.Fatalf("gross %d leg0: want %d got %d", gross, wantFirst, plan.InstantLegs[0].Amount)
		}
	}
}

func TestComputeSplitRemainderStaysInClearing(t *testing.T) {
	// 75+15+8 = 98% INSTANT, 2% END_OF_MONTH: 2% of gross must stay in
	// the payer clearing account and the month-end leg must not enter
	// the instant chain.
	rules := []SplitRule{
		instantRule(BeneficiaryConsolidatedRevenueFund, 3001, 7500),
		instantRule(BeneficiaryMDARetention, 2010, 1500),
		instantRule(BeneficiaryConcessionaireEscrow, 2099, 800),
		{Beneficiary: BeneficiaryLocalGovernmentShare, AccountCode: 2020, PercentageBPS: 200, Timing: TimingEndOfMonth},
	}
	plan, err := ComputeSplit(1_000_003, rules) // odd kobo to stress rounding
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.InstantLegs) != 3 || len(plan.MonthEndLegs) != 1 {
		t.Fatalf("legs: %d instant %d month-end", len(plan.InstantLegs), len(plan.MonthEndLegs))
	}
	if sumLegs(plan.InstantLegs)+plan.ClearingRemainder != 1_000_003 {
		t.Fatalf("instant + remainder != gross: %d + %d", sumLegs(plan.InstantLegs), plan.ClearingRemainder)
	}
	wantRemainder := uint64(1_000_003) - (1_000_003*9800)/10000
	if plan.ClearingRemainder != wantRemainder {
		t.Fatalf("remainder: want %d got %d", wantRemainder, plan.ClearingRemainder)
	}
}

func TestComputeSplitDeterministic(t *testing.T) {
	rules := []SplitRule{
		instantRule(BeneficiaryConsolidatedRevenueFund, 3001, 3333),
		instantRule(BeneficiaryMDARetention, 2010, 3333),
		instantRule(BeneficiaryConcessionaireEscrow, 2099, 3334),
	}
	first, err := ComputeSplit(777_777_777, rules)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 100; i++ {
		again, err := ComputeSplit(777_777_777, rules)
		if err != nil {
			t.Fatal(err)
		}
		for j := range first.InstantLegs {
			if first.InstantLegs[j].Amount != again.InstantLegs[j].Amount {
				t.Fatalf("non-deterministic leg %d", j)
			}
		}
	}
}

func TestComputeSplitErrors(t *testing.T) {
	if _, err := ComputeSplit(100, nil); err == nil {
		t.Fatal("no rules should fail")
	}
	if _, err := ComputeSplit(100, []SplitRule{
		{Beneficiary: BeneficiaryLocalGovernmentShare, AccountCode: 2020, PercentageBPS: 500, Timing: TimingEndOfMonth},
	}); err == nil {
		t.Fatal("month-end only should fail")
	}
	if _, err := ComputeSplit(100, []SplitRule{
		instantRule(BeneficiaryConsolidatedRevenueFund, 3001, 6000),
		instantRule(BeneficiaryMDARetention, 2010, 6000),
	}); err == nil {
		t.Fatal("INSTANT total > 100% should fail")
	}
}
