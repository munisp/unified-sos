module github.com/munisp/unified-sos/ledger/splits

go 1.23

// This module ships zero external dependencies so the default build and
// `go test` stay hermetic. The production adapter (tigerbeetle.go) is
// behind the `tigerbeetle` build tag and needs the Go client; before the
// first tagged build add the dependency and sums (requires network):
//
//	go mod edit -require=github.com/tigerbeetle/tigerbeetle-go@v0.16.11
//	go mod tidy   # run in this module and in services/mod-rev-core
