//go:build !tigerbeetle

package splits

import (
	"fmt"
	"os"
)

// NewTigerBeetleLedgerFromEnv is the fail-closed stub compiled into
// default builds (the real adapter in tigerbeetle.go requires the
// `tigerbeetle` build tag and the native TigerBeetle client). It always
// returns an error so a default build can never silently fall back to a
// non-production ledger while REV_CORE_LEDGER=tigerbeetle.
func NewTigerBeetleLedgerFromEnv() (LedgerClient, error) {
	return nil, fmt.Errorf(
		"splits: tigerbeetle adapter not compiled in; rebuild with `-tags tigerbeetle` (TB_ADDRESSES=%q, TB_CLUSTER_ID=%q)",
		os.Getenv("TB_ADDRESSES"), os.Getenv("TB_CLUSTER_ID"))
}
