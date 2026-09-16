package splits

import (
	"crypto/sha256"
	"encoding/binary"
)

// Deterministic transfer/bill identifiers (idempotency).
//
// These mirror ledger/fundsflow/tigerbeetle_flows.py
// deterministic_transfer_id: a 128-bit ID is derived from the first 16
// bytes of a SHA-256 digest, interpreted big-endian. Deriving IDs (instead
// of minting random ones) makes retried submissions resolve onto the same
// ledger objects, so a crash/retry can never double-apply a chain.

// DeterministicID derives a 128-bit ID from sha256(key + "|" + leg).
// Same (key, leg) → same ID.
func DeterministicID(key, leg string) Uint128 {
	sum := sha256.Sum256([]byte(key + "|" + leg))
	return Uint128{
		Hi: binary.BigEndian.Uint64(sum[0:8]),
		Lo: binary.BigEndian.Uint64(sum[8:16]),
	}
}

// DeterministicIDFromRef derives a 128-bit ID from sha256(ref) — used for
// bill correlation IDs (BillID) that must be stable across webhook
// redeliveries and process restarts.
func DeterministicIDFromRef(ref string) Uint128 {
	sum := sha256.Sum256([]byte(ref))
	return Uint128{
		Hi: binary.BigEndian.Uint64(sum[0:8]),
		Lo: binary.BigEndian.Uint64(sum[8:16]),
	}
}

// ChainLegID derives the deterministic transfer ID for leg index i of the
// chain submitted under key.
func ChainLegID(key string, i int) Uint128 {
	return DeterministicID(key, "leg-"+itoa(i))
}

// ReversalLegID derives the deterministic transfer ID for the reversal of
// leg index i: sha256(key ‖ leg ‖ "reversal").
func ReversalLegID(key string, i int) Uint128 {
	return DeterministicID(key, "leg-"+itoa(i)+"|reversal")
}

// itoa is a dependency-free strconv.Itoa for small non-negative ints.
func itoa(i int) string {
	if i == 0 {
		return "0"
	}
	var buf [20]byte
	pos := len(buf)
	for i > 0 {
		pos--
		buf[pos] = byte('0' + i%10)
		i /= 10
	}
	return string(buf[pos:])
}
