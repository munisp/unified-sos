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
//	REV_CORE_EBILLS      e-Bills notifier: "noop" (default, deterministic
//	                     local) or "nibss" (production NIBSS e-Bills client)
//	NIBSS_EBILLS_URL     NIBSS e-Bills gateway base URL (required when
//	                     REV_CORE_EBILLS=nibss — fails closed otherwise)
package main

import (
	"log"
	"net/http"
	"os"

	"github.com/munisp/unified-sos/ledger/splits"
	"github.com/munisp/unified-sos/services/mod-rev-core/internal/observability"
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

	notifier, err := revenue.NewEBillNotifierFromEnv()
	if err != nil {
		log.Fatalf("mod-rev-core: e-Bills notifier: %v", err)
	}

	svc := revenue.NewService(revenue.NewInMemoryStore(), catalog, ledger,
		revenue.WithEBillNotifier(notifier))
	handler := revenue.NewHandler(svc)

	// Stage 7.C: /metrics + request counters/histograms (stdlib-only).
	metrics := observability.New("mod-rev-core")
	root := http.NewServeMux()
	root.Handle("GET /metrics", metrics.Handler())
	root.Handle("/", metrics.Instrument(handler.Routes()))

	log.Printf("mod-rev-core: listening on %s (policy_dir=%q)", addr, policyDir)
	if err := http.ListenAndServe(addr, root); err != nil {
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
