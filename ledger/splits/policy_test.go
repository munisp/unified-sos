package splits

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// contractsDir locates the monorepo contracts/ directory relative to
// this test file (ledger/splits → repo root).
func contractsDir(t *testing.T) string {
	t.Helper()
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	return filepath.Join(filepath.Dir(thisFile), "..", "..", "contracts")
}

const validPolicy = `{
  "tenant_state_id": "ogun",
  "policy_id": "POL_REVENUE_SPLIT_LUC_2026",
  "revenue_head": "REV_LAND_USE_CHARGE",
  "effective_date": "2026-01-01",
  "gazette_reference": "Ogun State Land Use Charge Regulations",
  "statutory_split_rules": [
    {"beneficiary": "STATE_CONSOLIDATED_REVENUE_FUND", "tigerbeetle_account_code": 3001, "split_percentage": 75.0, "deduction_timing": "INSTANT"},
    {"beneficiary": "MDA_RETENTION_ACCOUNT", "tigerbeetle_account_code": 2010, "split_percentage": 15.0, "deduction_timing": "INSTANT"},
    {"beneficiary": "PPP_TECH_CONCESSIONAIRE_ESCROW", "tigerbeetle_account_code": 2099, "split_percentage": 8.0, "deduction_timing": "INSTANT"},
    {"beneficiary": "LOCAL_GOVERNMENT_SHARE_POOL", "tigerbeetle_account_code": 2020, "split_percentage": 2.0, "deduction_timing": "END_OF_MONTH"}
  ]
}`

func TestParsePolicyPackValid(t *testing.T) {
	pack, err := ParsePolicyPack([]byte(validPolicy))
	if err != nil {
		t.Fatalf("ParsePolicyPack: %v", err)
	}
	if pack.TenantStateID != "ogun" || pack.PolicyID != "POL_REVENUE_SPLIT_LUC_2026" {
		t.Fatalf("unexpected header: %+v", pack)
	}
	if got := len(pack.InstantRules()); got != 3 {
		t.Fatalf("InstantRules: want 3 got %d", got)
	}
	if got := len(pack.MonthEndRules()); got != 1 {
		t.Fatalf("MonthEndRules: want 1 got %d", got)
	}
	if pack.Rules[0].PercentageBPS != 7500 {
		t.Fatalf("75.0%% should normalise to 7500 bp, got %d", pack.Rules[0].PercentageBPS)
	}
}

func TestParsePolicyPackFractionalPercentage(t *testing.T) {
	doc := strings.Replace(validPolicy, `"split_percentage": 75.0`, `"split_percentage": 33.33`, 1)
	pack, err := ParsePolicyPack([]byte(doc))
	if err != nil {
		t.Fatalf("ParsePolicyPack: %v", err)
	}
	if pack.Rules[0].PercentageBPS != 3333 {
		t.Fatalf("33.33%% should normalise to 3333 bp, got %d", pack.Rules[0].PercentageBPS)
	}
}

func TestLoadExamplePolicyPack(t *testing.T) {
	path := filepath.Join(contractsDir(t), "policy-packs", "examples", "ogun-luc-2026.json")
	if _, err := os.Stat(path); err != nil {
		t.Skipf("contracts checkout not available: %v", err)
	}
	pack, err := LoadPolicyPackFile(path)
	if err != nil {
		t.Fatalf("LoadPolicyPackFile: %v", err)
	}
	if pack.RevenueHead != "REV_LAND_USE_CHARGE" {
		t.Fatalf("unexpected revenue head %q", pack.RevenueHead)
	}
	if pack.Guardrails == nil || pack.Guardrails.RevenueShareCeilingPct != 8 {
		t.Fatalf("guardrails not parsed: %+v", pack.Guardrails)
	}
}

func TestParsePolicyPackRejections(t *testing.T) {
	cases := map[string]string{
		"percentages over 100": strings.Replace(validPolicy,
			`"split_percentage": 75.0`, `"split_percentage": 90.0`, 1), // 90+15+8 = 113% INSTANT
		"percentage above 100 single leg": strings.Replace(validPolicy,
			`"split_percentage": 75.0`, `"split_percentage": 100.01`, 1),
		"negative percentage": strings.Replace(validPolicy,
			`"split_percentage": 75.0`, `"split_percentage": -1`, 1),
		"bad account code low": strings.Replace(validPolicy,
			`"tigerbeetle_account_code": 3001`, `"tigerbeetle_account_code": 999`, 1),
		"bad account code high": strings.Replace(validPolicy,
			`"tigerbeetle_account_code": 3001`, `"tigerbeetle_account_code": 10000`, 1),
		"unknown beneficiary": strings.Replace(validPolicy,
			`"STATE_CONSOLIDATED_REVENUE_FUND"`, `"VENDOR_PRIVATE_ACCOUNT"`, 1),
		"bad state tenant": strings.Replace(validPolicy,
			`"tenant_state_id": "ogun"`, `"tenant_state_id": "kano"`, 1),
		"bad policy id": strings.Replace(validPolicy,
			`"policy_id": "POL_REVENUE_SPLIT_LUC_2026"`, `"policy_id": "pol-lowercase"`, 1),
		"bad revenue head": strings.Replace(validPolicy,
			`"revenue_head": "REV_LAND_USE_CHARGE"`, `"revenue_head": "LAND_USE"`, 1),
		"bad timing": strings.Replace(validPolicy,
			`"deduction_timing": "END_OF_MONTH"`, `"deduction_timing": "QUARTERLY"`, 1),
		"too few rules": `{
		  "tenant_state_id": "ogun", "policy_id": "POL_X", "revenue_head": "REV_X",
		  "statutory_split_rules": [
		    {"beneficiary": "STATE_CONSOLIDATED_REVENUE_FUND", "tigerbeetle_account_code": 3001, "split_percentage": 100, "deduction_timing": "INSTANT"}
		  ]}`,
		"no instant legs": strings.Replace(validPolicy,
			`"deduction_timing": "INSTANT"`, `"deduction_timing": "END_OF_MONTH"`, -1),
		"excess precision": strings.Replace(validPolicy,
			`"split_percentage": 75.0`, `"split_percentage": 33.333`, 1),
		"malformed json": `{"tenant_state_id": `,
	}
	for name, doc := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := ParsePolicyPack([]byte(doc)); err == nil {
				t.Fatal("expected rejection, got nil error")
			}
		})
	}
}

func TestStateTenantIDMapping(t *testing.T) {
	for slug, want := range map[string]uint16{
		"lagos": StateLagos, "ogun": StateOgun, "osun": StateOsun,
		"benue": StateBenue, "nasarawa": StateNasarawa, "taraba": StateTaraba,
	} {
		got, err := StateTenantID(slug)
		if err != nil || got != want {
			t.Fatalf("StateTenantID(%q) = %d, %v; want %d", slug, got, err, want)
		}
	}
	if _, err := StateTenantID("kano"); err == nil {
		t.Fatal("expected error for unknown tenant")
	}
}
