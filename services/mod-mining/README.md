# mod-mining — Solid Minerals Custody & Levies

**WP-07 / EPIC-07 · Lot 5 · RT-03**

Digital mineral e-permit registry, weight-based levy calculation, IoT weighbridge telemetry, RFID truck manifests, quarry checkpoint scanners, artisanal miner biometric registry. Covers lithium spodumene, columbite, tantalite, gold, sapphire, barite.

- **Event contracts:** [`contracts/asyncapi/mining-events.yaml`](../../contracts/asyncapi/mining-events.yaml)
- **Acceptance:** levy computed instantly from tonnage + XRF assay grade, < 1% variance vs state fee schedules
- **HARD CONSTRAINT:** state-competent levies only — federal royalty lines separated by construction (see [`ledger/chart-of-accounts.md`](../../ledger/chart-of-accounts.md), class 5xxx)
- **Stack:** Rust · Kafka · PostGIS · ThingsBoard IoT · Sedona
- **Deploys:** Nasarawa (lithium), Osun (gold), Taraba (sapphire/barite), Ogun (quarry variant)
