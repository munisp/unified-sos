// Command atomic-split is the SOS reference demo of the atomic
// multi-party statutory revenue split (ADR-002 / WP-03 / EP-TB-03),
// upgraded from the original ledger/splits/atomic_split.go sketch.
//
// Example: a 100,000-kobo land-title payment split instantly into
//
//	65% State Consolidated Revenue Fund (TSA)
//	20% MDA Retention Account
//	15% PPP Concessionaire Escrow
//
// The three legs form a LINKED atomic chain: all commit or none do.
// Clause 22.2: the vendor share is distributed strictly via ledger
// execution; no vendor ever touches un-split gross state funds.
//
// The demo runs against the in-memory fake ledger so it needs no live
// TigerBeetle cluster; swap splits.NewInMemoryLedger for the
// tigerbeetle-go adapter in production.
package main

import (
	"fmt"
	"log"

	"github.com/munisp/unified-sos/ledger/splits"
)

func main() {
	ledger := splits.NewInMemoryLedger()

	params := splits.ChainParams{
		StateTenant: splits.StateOgun,
		Ledger:      splits.LedgerNGSovereign,
		BillID:      splits.NewID(),
	}
	payer := splits.MustBuildAccountID(splits.StateOgun, 0, splits.ClassPayerClearing, splits.Uint128{})
	crf := splits.MustBuildAccountID(splits.StateOgun, 0, splits.ClassConsolidatedRevenueFund, splits.Uint128{})
	mda := splits.MustBuildAccountID(splits.StateOgun, 0, splits.ClassMDARetention, splits.Uint128{})
	escrow := splits.MustBuildAccountID(splits.StateOgun, 0, splits.ClassConcessionaireEscrow, splits.Uint128{})

	for _, id := range []splits.Uint128{payer, crf, mda, escrow} {
		ledger.CreateAccount(id)
	}

	const grossKobo = 100_000
	ledger.SeedAccount(payer, grossKobo) // inbound payment landed in clearing

	// Example rules: 65% CRF / 20% MDA retention / 15% concessionaire
	// escrow (percentages in basis points: 100% = 10_000 bp).
	rules := []splits.SplitRule{
		{Beneficiary: splits.BeneficiaryConsolidatedRevenueFund, AccountCode: splits.ClassConsolidatedRevenueFund, PercentageBPS: 6500, Timing: splits.TimingInstant},
		{Beneficiary: splits.BeneficiaryMDARetention, AccountCode: splits.ClassMDARetention, PercentageBPS: 2000, Timing: splits.TimingInstant},
		{Beneficiary: splits.BeneficiaryConcessionaireEscrow, AccountCode: splits.ClassConcessionaireEscrow, PercentageBPS: 1500, Timing: splits.TimingInstant},
	}

	plan, err := splits.ComputeSplit(grossKobo, rules)
	if err != nil {
		log.Fatalf("compute split: %v", err)
	}
	if _, err := splits.ExecuteAtomicSplit(ledger, plan, params); err != nil {
		log.Fatalf("atomic transfer batch failed: %v", err)
	}

	fmt.Printf("settled %d kobo; clearing remainder: %d kobo\n", grossKobo, plan.ClearingRemainder)
	for _, leg := range plan.InstantLegs {
		fmt.Printf("  %-34s acct %d -> %d kobo\n", leg.Rule.Beneficiary, leg.Rule.AccountCode, leg.Amount)
	}
	fmt.Printf("balances: clearing=%d crf=%d mda=%d escrow=%d\n",
		ledger.MustAccount(payer).Balance(),
		ledger.MustAccount(crf).Balance(),
		ledger.MustAccount(mda).Balance(),
		ledger.MustAccount(escrow).Balance())
}
