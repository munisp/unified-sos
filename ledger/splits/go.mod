module github.com/munisp/unified-sos/ledger/splits

go 1.23

// The production adapter (tigerbeetle.go) is behind the `tigerbeetle` build
// tag; its client dependency is pinned below and compile-verified in CI
// (live-deps-compile job: `go build -tags tigerbeetle ./...`). The default
// untagged build and `go test` remain hermetic. To bump the pin:
//
//	go mod edit -require=github.com/tigerbeetle/tigerbeetle-go@<version>
//	go mod tidy   # run in this module and in services/mod-rev-core

require github.com/tigerbeetle/tigerbeetle-go v0.16.11
