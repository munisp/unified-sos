# Edge — Offline-First POS & Checkpoint Design

Subnational edge resilience is a system design goal: POS revenue terminals, transit weighbridges, and border toll gates must operate offline during rural connectivity drops (Taraba/Benue/Nasarawa corridors) and sync reliably on reconnection.

## Architecture

1. **Embedded SQLite/Rust edge daemon** on ruggedized Android POS terminals (solar-powered at remote checkpoints).
2. **Local cryptographic signing** — revenue tickets and digital tax receipts signed using the terminal's hardware secure element (SE).
3. **Local cache** — ≥ 5,000 signed transactions buffered offline (WP-05 acceptance).
4. **Sync** — asynchronous batch sync to the APISIX gateway via **mutual TLS** on cellular reconnect; Fluvio edge streaming pipeline with offline-sync reconciliation.

## SLOs

| Metric | Target |
|---|---|
| POS sync availability | 99.90% (offline-first) |
| Sync latency on connect | < 3 s |
| RPO / RTO | RPO < local / RTO < 1 min |
| Checkpoint e-manifest verification | < 5 s per vehicle |

## Hardware Profiles

- Ruggedized Android POS with BLE thermal printers + biometric validation (WP-08 M6.3)
- WIM edge IoT controllers with solar backup (WP-08 M6.1)
- ANPR camera corridors streaming via Fluvio
- Solar border kiosks (Kashimbila & Gembu, Taraba)
