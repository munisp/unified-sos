# Operations runbooks (Stage 7.D)

| Runbook | Trigger |
|---|---|
| [ledger-imbalance.md](ledger-imbalance.md) | TigerBeetle debit/credit mismatch, `sosctl ledger verify` failure |
| [audit-chain-tamper-alert.md](audit-chain-tamper-alert.md) | Audit hash-chain break, WORM snapshot verification failure |
| [kyc-registry-outage.md](kyc-registry-outage.md) | NIMC/CAC live adapter failures |
| [payment-scheme-outage.md](payment-scheme-outage.md) | NIBSS e-Bills / FSPIOP scheme errors |
| [geospatial-job-backlog.md](geospatial-job-backlog.md) | KEDA max replicas + growing queue, join SLO breach |
| [tenant-provisioning-failure.md](tenant-provisioning-failure.md) | `sosctl tenant provision` / ArgoCD degradation |
| [state-rollout-template.md](state-rollout-template.md) | Template for onboarding any of the six reference states |

Cross-cutting: [../dr-runbook.md](../dr-runbook.md) (backup/DR) and
[../secrets-management.md](../secrets-management.md) (rotation + incidents).
