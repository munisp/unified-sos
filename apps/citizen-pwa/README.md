# SOS Citizen (citizen-pwa)

Citizen-facing PWA for the unified-sos platform — the first frontend in the repo.
It consumes:

- **mod-citizen-portal** — service catalog (11 categories), identity wallets, service
  requests, USSD/IVR channel parity (`contracts/openapi/mod-citizen-portal.yaml`).
- **mod-transparency** — public, unauthenticated, tenant-scoped trust-fund feed, escrow
  statements and hash-chained procurement audit verification
  (`contracts/openapi/mod-transparency.yaml`).
- **mod-gis-lands** — C-of-O / deed verification
  (`contracts/openapi/cadastre-parcels.yaml` → `POST /api/v1/states/{state_id}/cadastre/deeds/verify`).

Supported states (fixed list, no tenant enumeration): lagos, ogun, osun, benue, nasarawa, taraba.

## Features

- Installable PWA (vite-plugin-pwa, injectManifest service worker `src/sw.ts`):
  offline app shell, network-first API reads, and an **offline POST queue**
  (IndexedDB in the SW + localStorage in-app, replayed on `online` / sync).
- Push-notification seam (`registerPushSeam` in `src/main.tsx`, `push` handler in SW).
- Fail-soft **demo mode**: when `VITE_API_BASE_URL` is unset or unreachable, deterministic
  fixtures mirroring the portal's `DEFAULT_CATALOG` are served and a banner is shown.
- Low-saturation warm palette, mobile-first, semantic HTML, skip link, focus rings.
- Naira formatting with kobo→naira conversion (`src/lib/format.ts`).
- i18n seam: English complete; `yo`, `ha`, `ig` stubbed with English fallback (`src/lib/i18n.ts`).
- USSD parity: every screen shows the state's USSD shortcode as a feature-phone alternative.
- Screens: state selector, service catalog by category, service request flow with offline
  queue, "My requests" status tracking + sync, payments (stub FSPIOP quote → confirm),
  transparency dashboard (trust fund, escrow, audit chain with green/red badge),
  C-of-O / deed verification, profile with wallet + KYC status seam.

## Develop

```bash
npm install
cp .env.example .env   # optional; set VITE_API_BASE_URL
npm run dev
```

## Build & test

```bash
npm test          # vitest smoke tests
npm run build     # tsc + vite build → dist/
npm run preview   # serve the production build
```

## Deploy

Serve `dist/` from any static host (HTTPS required for installability and service workers).
Set `VITE_API_BASE_URL` at build time to the API gateway origin
(e.g. `https://api.lagos.gov.ng/sos`). Unknown/unreachable APIs degrade to demo mode;
unknown tenants return the backend's generic 404.

| Variable | Purpose |
| --- | --- |
| `VITE_API_BASE_URL` | Base URL of the SOS API gateway. Empty/unreachable → demo mode. |
