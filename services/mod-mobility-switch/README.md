# mod-mobility-switch — Multimodal Transit Clearing

**WP-08 / EPIC-10 · Lot 6 · RT-04**

Contactless multimodal transit switch (Cowry Gen 2 compatible) — bus, rail, ferry clearing; commercial vehicle daily ticketing, park management, driver manifests, and union revenue-collection automation with gazetted 3–8% union commission auto-splits.

- **Ledger:** TigerBeetle transit account pool; Mojaloop QR
- **Legal dependency:** LAMATA ticketing harmonization gazette; union commission auto-split rule
- **Stack:** Go · Redis · TigerBeetle · Mojaloop
- **Deploys:** Lagos, Ogun

## Reference implementation (Python/FastAPI)

`app/` provides: per-state fare table config (`PUT/GET /mobility/v1/fares/{state}`,
gazetted union commission band 3–8%), tap/ticket clearing records priced from
the table (`POST /mobility/v1/clearing`, wildcard route fallback), operator
settlement batches (`POST /mobility/v1/settlements/{state}/{operator}`) with
TigerBeetle split legs — transfer code 140 (transit ticketing), account 4002
Transport Union Commission Pool, 3001 State CRF, 2010 operator retention;
legs always sum to gross — and a Cowry-compatible card bridge stub
(`POST /mobility/v1/cowry/authorize`, offline-capable interface contract).

```bash
pip install -e services/mod-mobility-switch[dev]
uvicorn app.main:app --app-dir services/mod-mobility-switch --port 8012
cd services/mod-mobility-switch && python3 -m pytest
```
