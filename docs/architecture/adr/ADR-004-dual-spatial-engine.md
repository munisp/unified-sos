# ADR-004: Spatial Analytics Architecture — Dual Engine (PostGIS + Apache Sedona)

**Status:** Accepted · **Domain:** Geospatial

## Decision

- **PostGIS** for low-latency transactional CRUD and live point-in-polygon verification.
- **Apache Sedona** (on Spark/DataFusion/Ray) for petabyte-scale distributed spatial joins, raster satellite NDVI extraction, and cadastral boundary reconciliation.

## Rationale

Single-engine approaches fail at subnational scale: standard PostGIS takes hours to join 2.5M building footprints against 800k cadastral parcels; Sedona completes 1.2M-polygon joins in 0.24 s but is not an OLTP system. The dual engine gives both.

## Tradeoff

Requires continuous asynchronous CDC from PostGIS to Delta Lake via Debezium/Kafka to keep the analytical layer current.
