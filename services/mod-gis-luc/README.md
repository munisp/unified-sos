# mod-gis-luc — Land Use Charge Valuation

**WP-06 / EPIC-06 · Lot 4 · RT-03**

Automated LUC calculation by matching Sedona satellite building footprints to cadastral parcels; AI property valuation feeds from the lakehouse (Ray AVM, target >92% R² vs certified surveyor valuations).

- **Spatial job:** [`geospatial/sedona/unassessed_property_join.sql`](../../geospatial/sedona/unassessed_property_join.sql)
- **Events:** `ng.sos.gis.unassessed_property_discovered`
- **Acceptance:** 1M-parcel/footprint join < 5 s; first 5,000 automated LUC bills at Milestone 3
- **Stack:** Sedona · DataFusion · Ray
- **Deploys:** Ogun, Benue, Lagos, Nasarawa
