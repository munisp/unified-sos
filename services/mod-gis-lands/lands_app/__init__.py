"""mod-gis-lands — Cadastral Land Administration & e-C-of-O titling service.

WP-06 / EPIC-06. Implements contracts/openapi/cadastre-parcels.yaml against the
PostGIS schema in db/migrations/0001_cadastre.sql, with an in-memory repository
and local synchronous workflow runner so the full module is testable without a
live PostGIS/Temporal deployment (see app/repository.py and app/temporal_adapter.py).
"""

__version__ = "1.0.0"
