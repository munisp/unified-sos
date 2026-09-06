package main

import "testing"

// TestTigerBeetleModeFailsClosedWithoutEnv asserts that selecting the
// production ledger backend without cluster configuration exits
// non-zero (fail-closed; the default build compiles the stub adapter).
func TestTigerBeetleModeFailsClosedWithoutEnv(t *testing.T) {
	t.Setenv("REV_CORE_LEDGER", "tigerbeetle")
	t.Setenv("TB_ADDRESSES", "")
	t.Setenv("TB_CLUSTER_ID", "")
	if code := run(); code == 0 {
		t.Fatal("run() = 0, want non-zero exit when tigerbeetle mode is unconfigured")
	}
}

// TestUnknownLedgerBackendFails asserts an invalid REV_CORE_LEDGER value
// exits non-zero.
func TestUnknownLedgerBackendFails(t *testing.T) {
	t.Setenv("REV_CORE_LEDGER", "sqlite")
	if code := run(); code == 0 {
		t.Fatal("run() = 0, want non-zero exit for unknown ledger backend")
	}
}
