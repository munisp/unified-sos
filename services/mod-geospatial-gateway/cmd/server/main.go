// Command server runs the mod-geospatial-gateway HTTP service.
package main

import (
	"log"
	"net/http"
	"os"
	"time"

	"github.com/unified-sos/mod-geospatial-gateway/internal/geogateway"
	"github.com/unified-sos/mod-geospatial-gateway/internal/observability"
)

func main() {
	addr := os.Getenv("GATEWAY_ADDR")
	if addr == "" {
		addr = ":8014"
	}
	svc := geogateway.NewLocalService(nil)
	handler := geogateway.NewHandler(svc)

	// Stage 7.C: /metrics + request counters/histograms (stdlib-only).
	metrics := observability.New("mod-geospatial-gateway")
	root := http.NewServeMux()
	root.Handle("GET /metrics", metrics.Handler())
	root.Handle("/", metrics.Instrument(handler.Routes()))

	srv := &http.Server{
		Addr:              addr,
		Handler:           root,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	log.Printf("mod-geospatial-gateway listening on %s (local deterministic mode)", addr)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("server error: %v", err)
	}
}
