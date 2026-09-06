// Package main is the SOS reference implementation of the atomic multi-party
// statutory revenue split on TigerBeetle (ADR-002 / WP-03 / EP-TB-03).
//
// Example: a 100,000-kobo land-title payment split instantly into
//   65% State Consolidated Revenue Fund (TSA)
//   20% MDA Retention Account
//   15% PPP Concessionaire Escrow
//
// The three legs form a LINKED atomic chain: all commit or none do.
// Clause 22.2: the vendor share is distributed strictly via ledger execution;
// no vendor ever touches un-split gross state funds.
package main

import (
	"context"
	"log"

	tb "github.com/tigerbeetle/tigerbeetle-go"
	tb_types "github.com/tigerbeetle/tigerbeetle-go/pkg/types"
)

// Account codes per ledger/chart-of-accounts.md. In production these are
// derived from the state's gazetted policy pack (contracts/policy-packs/).
type SplitRule struct {
	AccountCode uint64
	Percentage  uint64 // basis: 100 = 100%
	Code        uint16 // transfer code
}

const (
	payerClearingAccount uint64 = 1001
	ledgerNGSovereign    uint32 = 1
)

// ExecuteRevenueSplit performs the atomic statutory split for a settled bill.
func ExecuteRevenueSplit(client tb.Client, billID tb_types.Uint128, grossAmountKobo uint64, rules []SplitRule) error {
	transfers := make([]tb_types.Transfer, 0, len(rules))
	allocated := uint64(0)

	for i, rule := range rules {
		var amount uint64
		if i == len(rules)-1 {
			// Last leg takes the remainder to avoid rounding drift.
			amount = grossAmountKobo - allocated
		} else {
			amount = (grossAmountKobo * rule.Percentage) / 100
			allocated += amount
		}

		flags := tb_types.TransferFlags{}
		if i < len(rules)-1 {
			flags.Linked = true // continue the atomic chain
		}

		transfers = append(transfers, tb_types.Transfer{
			ID:              tb_types.ID(),
			DebitAccountID:  tb_types.ToUint128(payerClearingAccount),
			CreditAccountID: tb_types.ToUint128(rule.AccountCode),
			Amount:          tb_types.ToUint128(amount),
			Ledger:          ledgerNGSovereign,
			Code:            rule.Code,
			Flags:           flags.ToUint16(),
		})
	}

	res, err := client.CreateTransfers(transfers)
	if err != nil || len(res) > 0 {
		log.Fatalf("Atomic transfer batch failed: %v %v", err, res)
	}
	return nil
}

func main() {
	// Example rules: 65% CRF / 20% MDA retention / 15% concessionaire escrow.
	rules := []SplitRule{
		{AccountCode: 3001, Percentage: 65, Code: 101}, // State Consolidated Revenue Fund (TSA)
		{AccountCode: 2010, Percentage: 20, Code: 102}, // MDA Retention
		{AccountCode: 2099, Percentage: 15, Code: 103}, // PPP Concessionaire Escrow
	}
	_ = rules
	// client := ... // tb client initialized per-state cluster endpoints
	// _ = ExecuteRevenueSplit(client, tb_types.ID(), 100_000, rules)
	_ = context.Background()
}
