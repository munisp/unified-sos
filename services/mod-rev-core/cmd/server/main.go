// Command server runs the mod-rev-core HTTP service (WP-05 / EPIC-05).
//
// Configuration (environment variables):
//
//	REV_CORE_ADDR        listen address (default ":8080")
//	REV_CORE_POLICY_DIR  directory of revenue-split policy packs that
//	                     override the embedded gazetted seeds (optional)
//	REV_CORE_LEDGER      ledger backend: "memory" (default, in-memory
//	                     fake) or "tigerbeetle" (production adapter;
//	                     requires a binary built with `-tags tigerbeetle`
//	                     and fails closed otherwise)
//	TB_ADDRESSES         comma-separated TigerBeetle cluster addresses
//	                     (required when REV_CORE_LEDGER=tigerbeetle)
//	TB_CLUSTER_ID        TigerBeetle cluster ID (required when
//	                     REV_CORE_LEDGER=tigerbeetle)
package main

import (
	"log"
	"net/http"
	"os"

	"github.com/munisp/unified-sos/ledger/splits"
	"github.com/munisp/unified-sos/services/mod-rev-core/internal/revenue"
)

func main() {
	os.Exit(run())
}

func run() int {
	addr := getenv("REV_CORE_ADDR", ":8080")
	policyDir := os.Getenv("REV_CORE_POLICY_DIR")

	catalog, err := revenue.LoadPolicyCatalog(policyDir)
	if err != nil {
		log.Printf("mod-rev-core: policy catalog: %v", err)
		return 1
	}

	var ledger splits.LedgerClient
	switch backend := getenv("REV_CORE_LEDGER", "memory"); backend {
	case "memory":
		ledger = splits.NewInMemoryLedger()
		log.Printf("mod-rev-core: using in-memory ledger (no TigerBeetle cluster configured)")
	case "tigerbeetle":
		// Fail-closed: no cluster configuration (or a binary built
		// without `-tags tigerbeetle`) is a hard startup error.
		l, err := splits.NewTigerBeetleLedgerFromEnv()
		if err != nil {
			log.Printf("mod-rev-core: tigerbeetle ledger: %v", err)
			return 1
		}
		ledger = l
		log.Printf("mod-rev-core: using TigerBeetle cluster at %q", os.Getenv("TB_ADDRESSES"))
	default:
		log.Printf("mod-rev-core: unknown REV_CORE_LEDGER %q (want memory|tigerbeetle)", backend)
		return 1
	}

	svc := revenue.NewService(revenue.NewInMemoryStore(), catalog, ledger)
	handler := revenue.NewHandler(svc)

	log.Printf("mod-rev-core: listening on %s (policy_dir=%q)", addr, policyDir)
	if err := http.ListenAndServe(addr, handler.Routes()); err != nil {
		log.Printf("mod-rev-core: serve: %v", err)
		return 1
	}
	return 0
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
