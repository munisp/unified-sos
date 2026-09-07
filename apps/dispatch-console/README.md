# SOS Dispatch Console (dispatch-console)

Dispatcher-facing Safe-City console for mod-police-cad (112 emergency CAD):
incident queue, unit roster, live streams, trust-fund audit feed — and a real
mapping engine.

## Mapping engine

- **MapLibre GL** (`maplibre-gl`, pinned) replaces the old schematic SVG panel
  as the default renderer (`src/components/MapView.tsx` → `MapLibrePanel.tsx`):
  - incident markers with **clustering** (cluster circles + counts, click to
    zoom), unit markers, and **state geofence polygons** as GeoJSON layers
    (builders in `src/lib/mapLayers.ts`, unit-tested without WebGL);
  - **click-to-inspect popups** for incidents, units and geofences;
  - the original SVG panel remains as a **schematic fallback** — manual toggle,
    plus automatic fail-soft when WebGL/maplibre or live tiles are unavailable.
- **CesiumJS 3D cadastre** (`src/components/Cadastre3D.tsx`), feature-flagged
  by `VITE_ENABLE_3D=true`. Extruded building footprints / cadastral parcels
  from bundled per-state fixture GeoJSON (`public/geo/parcels-<state>.geojson`),
  with click parcel → attribute card (parcel UIN, LUC band, C-of-O status).
  **Cesium Ion is disabled**: `Ion.defaultAccessToken = ''`, no ion imagery or
  terrain — base layer off, ellipsoid terrain, static assets (Workers/Widgets/
  Assets/ThirdParty) copied same-origin to `/cesium` by `vite-plugin-static-copy`
  at build time. No ion token or external network required.

### Demo vs live mode

| Mode | Trigger | Behaviour |
| --- | --- | --- |
| Demo (default) | `VITE_MAP_STYLE_URL` unset/invalid | Inline bundled minimal style (`buildDemoStyle()` in `src/lib/mapcore.ts`, mirrored by `public/geo/demo-style.json`) + in-memory GeoJSON sources — **zero network**. |
| Live | `VITE_MAP_STYLE_URL=https://…/style.json` | Base style/tiles from the self-hosted **GeoLibre** stack (`deploy/geolibre/docker-compose.yaml`); unreachable tiles fail soft to the schematic panel. |

In production GeoLibre serves vector tiles/styles from PostGIS (system of
record per `db/migrations/0006_geospatial.sql`); mod-geospatial builds the
GeoLibre projects. The demo style/parcels are schematic stand-ins for those
datasets.

Both maplibre-gl and cesium are loaded via dynamic `import()` — they ship as
separate lazy chunks and never load in demo-fallback/3D-off paths.

## Env

- `VITE_API_BASE` — CAD API base; unset → fail-soft demo fixtures.
- `VITE_MAP_STYLE_URL` — GeoLibre style URL for live map mode.
- `VITE_ENABLE_3D` — `true` enables the Cesium 3D cadastre panel.

## Tests / build

`npm ci && npx vitest run && npm run build` — vitest runs in jsdom; maplibre and
cesium are guarded behind a WebGL probe so jsdom exercises the fallback paths.
