//go:build tigerbeetle && integration

package splits

import (
	"os"
	"strconv"
	"strings"
	"testing"
)

// TestTigerBeetleLedgerContract runs the shared contract suite against a
// live TigerBeetle cluster. It requires:
//
//	go test -tags "tigerbeetle integration" ./...
//
// with TB_ADDRESSES (comma-separated host:port) and TB_CLUSTER_ID set.
// Without TB_ADDRESSES the suite skips (hermetic CI default).
func TestTigerBeetleLedgerContract(t *testing.T) {
	rawAddrs := os.Getenv("TB_ADDRESSES")
	if rawAddrs == "" {
		t.Skip("TB_ADDRESSES not set; skipping live TigerBeetle contract run")
	}
	clusterID, err := strconv.ParseUint(os.Getenv("TB_CLUSTER_ID"), 10, 64)
	if err != nil {
		t.Fatalf("TB_CLUSTER_ID: %v", err)
	}
	var addresses []string
	for _, a := range strings.Split(rawAddrs, ",") {
		if a = strings.TrimSpace(a); a != "" {
			addresses = append(addresses, a)
		}
	}
	// Bootstrap float that funds SeedAccount credits on the test cluster
	// (the single account permitted to carry a negative balance).
	seedFloat := MustBuildAccountID(StateOsun, 0, ClassPayerClearing, EntityFromUint64(0))

	RunLedgerContractTests(t, func(t *testing.T) LedgerClient {
		t.Helper()
		l, err := NewTigerBeetleLedger(clusterID, addresses, WithSeedAccount(seedFloat))
		if err != nil {
			t.Fatalf("NewTigerBeetleLedger: %v", err)
		}
		t.Cleanup(l.Close)
		return l
	})
}
