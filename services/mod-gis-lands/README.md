# mod-gis-lands — Cadastral Land Administration & Titling

**WP-06 / EPIC-06 · Lot 4 · RT-03**

End-to-end digital land registry: parcel storage/validation (UTM Minna Datum EPSG:26391/26392/26393 + WGS84 EPSG:4326), topological overlap checking against gazetted reserves/setbacks/existing titles, multi-stage e-C-of-O approval via Temporal (`CadastralTitlingWorkflow`: Surveyor → Town Planning → Attorney General → Governor digital signature), cryptographically signed digital titles.

- **API contract:** [`contracts/openapi/cadastre-parcels.yaml`](../../contracts/openapi/cadastre-parcels.yaml)
- **Schema:** [`db/migrations/0001_cadastre.sql`](../../db/migrations/0001_cadastre.sql)
- **Acceptance:** C-of-O from 18 months to < 14 days; zero overlapping polygons; sub-meter precision
- **Stack:** PostGIS · Apache Sedona · Temporal · Python
- **State systems integrated:** NAGIS · BENGIS · TAGIS · LASGIS · OLARMS · OGIS
