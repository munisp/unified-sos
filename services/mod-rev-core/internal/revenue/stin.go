// Package revenue implements the domain layer of mod-rev-core
// (WP-05 / EPIC-05): the STIN taxpayer registry, policy-governed
// revenue-head catalog, assessment calculation, idempotent bill
// issuance, and settlement via the TigerBeetle statutory split engine.
package revenue

import (
	"fmt"
	"regexp"
)

// STIN — State Taxpayer Identification Number.
//
// Format: NG-{STATE3}-{YEAR}-{6 digits}, e.g. NG-NAS-2026-892104.
// The 3-letter state segment binds the STIN to a state tenant, which the
// service enforces against the path tenant on every request.
var stinPattern = regexp.MustCompile(`^NG-[A-Z]{3}-[0-9]{4}-[0-9]{6}$`)

// stateSTINPrefix maps state slugs to their STIN state segment.
var stateSTINPrefix = map[string]string{
	"lagos":    "LAG",
	"ogun":     "OGU",
	"osun":     "OSU",
	"benue":    "BEN",
	"nasarawa": "NAS",
	"taraba":   "TAR",
}

// StateSTINPrefix returns the 3-letter STIN segment for a state slug.
func StateSTINPrefix(state string) (string, bool) {
	p, ok := stateSTINPrefix[state]
	return p, ok
}

// ValidateSTIN checks the STIN format and that its state segment matches
// the request tenant (cross-tenant STINs are rejected, mirroring the
// contract's tenant-isolation rule).
func ValidateSTIN(stin, state string) error {
	if !stinPattern.MatchString(stin) {
		return fmt.Errorf("invalid STIN %q: want NG-{STATE3}-{YEAR}-{6 digits}", stin)
	}
	prefix, ok := stateSTINPrefix[state]
	if !ok {
		return fmt.Errorf("unknown state tenant %q", state)
	}
	if got := stin[3:6]; got != prefix {
		return fmt.Errorf("STIN state segment %q does not match tenant %q (%s)", got, state, prefix)
	}
	return nil
}
