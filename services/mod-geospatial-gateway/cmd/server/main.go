// Command server runs the mod-geospatial-gateway HTTP service.
package main

import (
	"log"
	"net/http"
	"os"
	"time"

	"github.com/unified-sos/mod-geospatial-gateway/internal/geogateway"
)

func main() {
	addr := os.Getenv("GATEWAY_ADDR")
	if addr == "" {
		addr = ":8014"
	}
	svc := geogateway.NewLocalService(nil)
	handler := geogateway.NewHandler(svc)

	srv := &http.Server{
		Addr:              addr,
		Handler:           handler.Routes(),
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
