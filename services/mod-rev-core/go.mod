module github.com/munisp/unified-sos/services/mod-rev-core

go 1.23

require github.com/munisp/unified-sos/ledger/splits v0.0.0

// Monorepo-local dependency on the ledger split library (WP-03).
replace github.com/munisp/unified-sos/ledger/splits => ../../ledger/splits
