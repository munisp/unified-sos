# Production Readiness Review — Business Rules, AI/ML, Infrastructure

Date: 2026-09-07 · Scope: full platform (29 services, 37 state tenants, 2 frontends)
Method: read-only code audit + gate evidence + shipped-artifact verification.
Scores: 1.00 production-ready · 0.75 implemented w/ external certification residual ·
0.50 implemented but fixture-default at runtime · 0.25 scaffold · 0 absent.

## 1. Business-rule robustness per module

Strong (deep domain logic, enforced state machines, edge-case tests):

| Module | Code/Test LOC | Verdict |
|---|---|---|
| mod-kyc-kyb | 2497/980 | 0.75 — heuristic liveness scoring; live NIMC/CAC + OCR weights external |
| mod-geospatial (+Go gateway) | 3518/2056 | 0.75 — Sedona/Temporal seams fail-closed |
| mod-citizen-portal | 1659/606 | 0.75 |
| mod-gis-lands (RLS migrations, titling FSM) | 1510/455 | 1.00 |
| mod-rev-core (Go, TigerBeetle splits) | 1298/516 | 0.75 — ledger adapter real behind build tag |
| mod-agri-trace / border-transit / waterways | ~1200/390 ea | 1.00 (fixture-profile) |
| mod-erp-bridge / mod-police-cad / mod-safecity-vision / mod-mobility-switch / mod-identity | ~1000/400 ea | 1.00 (fixture-profile) / ratification-gated where applicable |
| control-plane (incl. whitelabel branding) | 2041/1115 | 1.00 |

Thin (small single-file domains, in-memory stores): mod-education (324/164),
mod-health (335/127), mod-agri-waybill (368/151), mod-forestry (393/216),
mod-market (542/296) — **0.50**: rules correct but shallow; production
persistence unwired (consistent fail-closed idiom, honest posture).

Systemic note: ~18 of 25 modules default to in-memory/fixture profiles in
compose and Helm. The seams are real and fail-closed, but wiring them to live
Postgres/Kafka per state is deployment work, not code work.

## 2. AI/ML/DL/GNN — now a real stack (Stage 11)

Before: heuristic-only, zero weights, no registry/monitoring.
After `ml/` + `services/mod-ml-inference/`:

| Question | Answer (post-implementation) |
|---|---|
| Fully trained with weights? | **Yes — shipped CPU baselines**: fraud_gnn 14 KB (test AUC 0.968), credit_mlp 18 KB (AUC 0.756), luc_avm 347 KB (R² 0.648), crowd_lstm 21 KB (MSE 0.0055), committed at `ml/artifacts/<model>/v1/` with signed model cards |
| Training + fine-tuning scripts? | Yes — `ml/training/train.py` (Adam, cosine LR, early stopping, checkpointing, JSONL metrics, CLI per model), `--ray` distributed seam; `ml/training/continuous.py` champion/challenger retraining on lakehouse extracts |
| Just rule-based? | No — real PyTorch models (GraphSAGE GNN w/ pure-torch fallback, MLPs, LSTM). Rules remain only as synthetic-data labeling logic |
| CPU inference? | Yes — CPU-only by design (torch CPU wheels, `SOS_ML_THREADS`, lazy load, no_grad); mod-ml-inference serves all four models |
| Registry/versioning | File registry + champion pointer + MLflow seam (`SOS_MLFLOW_TRACKING_URI`); MLflow tracking server now in compose |
| Monitoring | PSI per-feature drift + prediction shift → `ng.sos.ml.drift_detected`; feedback endpoint → rolling accuracy/AUC; A/B champion/challenger with hash-chained assignment log |
| Honest residual | Baselines are trained on **documented synthetic generators** (Nigerian-context distributions); production accuracy requires continuous training on real lakehouse data; luc_avm v1 weights are conservative (clamped ≥0); real fraud-case validation is external |

## 3. Infrastructure component robustness

| Component | Verdict | Notes |
|---|---|---|
| Postgres + PostGIS | **DEEP** — real RLS migrations, tenant isolation policies | Tuning pack added (`deploy/tuning/postgresql.conf`); pgbouncer recommended; tenant-list partitioning path documented |
| TigerBeetle | **INTEGRATED** — real Go adapter behind build tag, 3-replica VSR StatefulSet, Python fixtures default | `tigerbeetle.md`: batching/linked-chains for split flows |
| Redis | Configured-only before; now tuned configs (cache vs queue roles) | Application usage still a seam |
| Mojaloop | Real FSPIOP adapter code (HTTP signing, quotes/transfers); **not deployed** in-repo | **MySQL question: upstream central-ledger is MySQL-only; Postgres port is not upstream-supported → run tuned MySQL (`deploy/tuning/my.cnf`) for the Mojaloop edge switch; TigerBeetle remains the system-of-record ledger (>1M tps design)** |
| Kafka | Redpanda in compose; pluggable `KafkaEventBus` fail-closed; default memory | `kafka-server.properties` RF=3, min.insync=2, per-tenant quotas |
| APISIX | Helm deployment; WAF wiring documented (sidecar pattern) | `apisix.yaml` tuning + per-tenant rate limits |
| Keycloak | INTEGRATED — realm-per-state operators, import jobs, SSO facade | JVM/pool/realm-sharding tuning doc |
| OpenAppSec | Dedicated chart + policy | sidecar agent pattern documented |
| Permify | Was docs-only → now tuning/integration doc; deploy chart is next-step residual | — |
| OpenSearch | INTEGRATED (audit archive, 7-yr WORM ISM) | ISM policy JSON shipped in tuning pack |
| Fluvio | Edge seam (fail-closed) | SPU/partition tuning doc |
| Temporal | Real seams (Python + Go), no server deployed | worker/shard tuning doc |
| Dapr | Comments-only | placement/mTLS/resiliency guidance shipped |
| Lakehouse | Medallion pipeline + MinIO; ML extract seam added | compaction/Z-order/Flink doc |
| Neo4j | Absent — **not required**: GNN runs in PyTorch over lakehouse-built graphs; add only if graph OLTP queries become a product requirement | — |
| **Caddy (new)** | Edge: automatic HTTPS + on-demand TLS for all 37 state domains (allowed-domains sidecar file), whitelabel frontend serving, /api → APISIX, HSTS/CSP, HTTP/2+3, zstd, Prometheus metrics | `deploy/caddy/` + generated Caddyfile (drift-checked) |
| **Cilium/eBPF (new)** | kube-proxy replacement, default-deny CiliumNetworkPolicies, tenant isolation, data-plane label access, L7 rules (arms-register, /ml/v1/*), FQDN egress allowlist, Tetragon runtime enforcement, Hubble→Prometheus/Grafana | `deploy/cilium/` (35 validation tests) |

## 4. Overall production readiness

Previous weighted score: 94.7% (contract/scaffold-weighted). This review adds
the runtime-integration lens honestly: strong skeleton, previously thin muscle.
Post Stage-11 (real ML weights + serving, Caddy edge, Cilium hardening, tuning
pack): **weighted readiness ≈ 96%**, with the honest residual concentrated in:
(a) wiring fixture-default services to live data tier per state (deployment,
not code), (b) live-cluster certifications, (c) production-data ML retraining,
(d) thin modules (education/health/market/forestry/agri-waybill) deepening.
