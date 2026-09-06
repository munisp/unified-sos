# mod-mining — Solid Minerals Custody & Levies

**WP-07 / EPIC-07 · Lot 5 · RT-03**

Digital mineral e-permit registry, weight-based levy calculation, IoT weighbridge telemetry, RFID truck manifests, quarry checkpoint scanners, artisanal miner biometric registry. Covers lithium spodumene, columbite, tantalite, gold, sapphire, barite.

- **Event contracts:** [`contracts/asyncapi/mining-events.yaml`](../../contracts/asyncapi/mining-events.yaml)
- **Acceptance:** levy computed instantly from tonnage + XRF assay grade, < 1% variance vs state fee schedules
- **HARD CONSTRAINT:** state-competent levies only — federal royalty lines separated by construction (see [`ledger/chart-of-accounts.md`](../../ledger/chart-of-accounts.md), class 5xxx)
- **Stack:** Rust · Kafka · PostGIS · ThingsBoard IoT · Sedona
- **Deploys:** Nasarawa (lithium), Osun (gold), Taraba (sapphire/barite), Ogun (quarry variant)

## Reference Implementation (Python / FastAPI)

This directory carries a compact, fully-tested Python reference of the module
(production target per the stack above):

- `app/models.py` — site, consignment (CREATED → WEIGHED → DISPATCHED → DELIVERED),
  weighbridge reading, assay record, levy assessment. `LevyLine` (state-competent,
  transfer code 110) is separated **by construction** from `FederalRoyaltyLine`
  (5xxx pass-through, reporting only).
- `app/levy.py` — tonnage + XRF assay grade levy computation; `SplitRule`
  rejects any beneficiary/account claiming a federal royalty share
  (`RoyaltyConstraintViolation`).
- `app/bus.py` — pluggable event bus: `InMemoryEventBus` (tests/local) and a
  documented `KafkaEventBus` adapter (Kafka/Fluvio via optional `aiokafka`).
  Dispatch publishes `ng.sos.mining.consignment_dispatched` per the AsyncAPI contract.
- `app/main.py` — FastAPI HTTP surface; `app/repo.py` — repository interface +
  in-memory impl (PostGIS/Postgres drop-in in production).

### Run / test

```bash
cd services/mod-mining
pip install fastapi httpx pydantic pytest uvicorn
python3 -m pytest -q          # full suite
uvicorn app.main:app --port 8001
```
