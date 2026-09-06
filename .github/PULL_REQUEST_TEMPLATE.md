<!--
SOS Pull Request — review per CODEOWNERS. Ledger/settlement changes require
2 approvals including a Core Financial pod maintainer (CONTRIBUTING.md).
-->

## Summary

<!-- What and why. Link the issue / work package (WP-xx). -->

## Change classification

- [ ] Shared-core change (services/, contracts/, ledger/, db/)
- [ ] State policy pack (config/states/<state>/) — compliance note attached
- [ ] Infrastructure / tenancy (infra/) — validator output attached
- [ ] CI / governance (.github/)

## Mandatory checklist

- [ ] **80/20 rule honored** — no state-specific rates/splits hard-coded; state
      variation lives in `config/states/` policy packs
- [ ] **Contracts updated** — API/event changes include OpenAPI 3.1 / AsyncAPI
      updates in `contracts/` in this PR
- [ ] **Tests** — new behavior covered; `go test` / `pytest` pass locally
- [ ] **Money correctness** — monetary movement goes through TigerBeetle
      (no SQL `UPDATE balance` patterns, ADR-002)
- [ ] **Tenant isolation** — new state-data tables include `tenant_state_id` + RLS
- [ ] **No secrets** — no tokens/credentials; OpenBao/Vault patterns only
- [ ] **Licenses** — new dependencies are MIT/Apache-2.0/BSD/ISC/PostgreSQL only

## Validation evidence

<!-- Paste outputs, e.g.:
     python3 config/states/validate_packs.py  → VALIDATION PASSED
     python3 infra/tests/validate_infra.py    → VALIDATION PASSED
-->

## Provenance tags

<!-- Material figures tagged [LIVE] / [DERIVED] / [GAP] where applicable. -->

## Acceptance stage impact

<!-- Does this feed FAT, Performance/Stress, Security/Pen-Test, SAT or UAT?
     See .github/workflows/README.md for the gate mapping. -->
