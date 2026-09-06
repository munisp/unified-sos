# Architecture Documentation

The SOS multi-tenant reference architecture — synthesizing the SOS Architecture Blueprint (Enterprise Technical Specification, September 2026).

| Document | Scope |
|---|---|
| [00-executive-overview.md](00-executive-overview.md) | Macro-fiscal thesis, platform concept, target SLAs |
| [01-architecture-blueprint.md](01-architecture-blueprint.md) | Four-layer topology, control plane vs data plane, stack decomposition |
| [02-bounded-contexts.md](02-bounded-contexts.md) | Six business domains, capability map, canonical module inventory |
| [03-financial-core.md](03-financial-core.md) | TigerBeetle ledger kernel, Mojaloop switch, 128-bit chart of accounts, atomic statutory splits |
| [04-geospatial-engine.md](04-geospatial-engine.md) | Dual spatial architecture (PostGIS + Apache Sedona), vector tiles, remote sensing |
| [05-lakehouse-ai.md](05-lakehouse-ai.md) | Medallion lakehouse (Delta Lake/Flink/DataFusion), Ray AI valuation models |
| [06-tenancy-security.md](06-tenancy-security.md) | Tenancy tiers, isolation matrix, zero-trust pipeline, SOC stack |
| [07-resilience-dr.md](07-resilience-dr.md) | RPO/RTO objectives, disaster recovery, offline edge resiliency |
| [adr/](adr/) | Architecture Decision Records ADR-001 … ADR-007 |

## Target Architectural KPIs

| Metric | Target |
|---|---|
| TigerBeetle ledger throughput | > 1,000,000 TPS |
| Mojaloop 3-way split latency | < 50 ms |
| Sedona 1M-polygon spatial join | 0.24 sec |
| Core control-plane availability | 99.999% |
