// Command server runs the mod-rev-core HTTP service (WP-05 / EPIC-05).
//
// Configuration (environment variables):
//
//	REV_CORE_ADDR        listen address (default ":8080")
//	REV_CORE_POLICY_DIR  directory of revenue-split policy packs that
//	                     override the embedded gazetted seeds (optional)
//	REV_CORE_LEDGER      ledger backend: "memory" (default, in-memory
//	                     fake) or "tigerbeetle" (production adapter —
//	                     documented stub pending cluster provisioning,
//	                     see ledger/README.md)
//	TB_ADDRESSES         comma-separated TigerBeetle cluster addresses
//	                     (used when REV_CORE_LEDGER=tigerbeetle)
package main

import (
	"log"
	"net/http"
	"os"

	"github.com/munisp/unified-sos/ledger/splits"
	"github.com/munisp/unified-sos/services/mod-rev-core/internal/revenue"
)

func main() {
	addr := getenv("REV_CORE_ADDR", ":8080")
	policyDir := os.Getenv("REV_CORE_POLICY_DIR")

	catalog, err := revenue.LoadPolicyCatalog(policyDir)
	if err != nil {
		log.Fatalf("mod-rev-core: policy catalog: %v", err)
	}

	var ledger splits.LedgerClient
	switch backend := getenv("REV_CORE_LEDGER", "memory"); backend {
	case "memory":
		ledger = splits.NewInMemoryLedger()
		log.Printf("mod-rev-core: using in-memory ledger (no TigerBeetle cluster configured)")
	case "tigerbeetle":
		// Production adapter: wrap github.com/tigerbeetle/tigerbeetle-go
		// behind splits.LedgerClient using TB_ADDRESSES. Blocked on
		// cluster provisioning (docs/delivery/90-day-playbook Days 31-60).
		log.Fatalf("mod-rev-core: tigerbeetle adapter not yet provisioned; TB_ADDRESSES=%q", os.Getenv("TB_ADDRESSES"))
	default:
		log.Fatalf("mod-rev-core: unknown REV_CORE_LEDGER %q (want memory|tigerbeetle)", backend)
	}

	svc := revenue.NewService(revenue.NewInMemoryStore(), catalog, ledger)
	handler := revenue.NewHandler(svc)

	log.Printf("mod-rev-core: listening on %s (policy_dir=%q)", addr, policyDir)
	if err := http.ListenAndServe(addr, handler.Routes()); err != nil {
		log.Fatalf("mod-rev-core: serve: %v", err)
	}
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
