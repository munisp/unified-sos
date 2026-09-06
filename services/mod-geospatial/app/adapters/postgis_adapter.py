"""PostGIS adapter seam (OLTP system of record).

The concrete repository seam lives in ``app.repository.PostGISGeospatialRepository``;
this module provides the low-level connection helper used by production
deployments. It fails closed without a DSN or driver and never falls back to
an unauthenticated store.
"""

from __future__ import annotations

import os
from typing import Any

from ..domain import AdapterUnavailableError

DSN_ENV = "GEOSPATIAL_POSTGIS_DSN"


class PostGISConnectionFactory:
    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or os.environ.get(DSN_ENV)
        if not self._dsn:
            raise AdapterUnavailableError(f"{DSN_ENV} is not set; PostGIS adapter fails closed")
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:
            raise AdapterUnavailableError("PostGIS adapter requires the optional 'psycopg' package") from exc

    def connect(self, tenant_state_id: str) -> Any:  # pragma: no cover - production seam
        import psycopg

        conn = psycopg.connect(self._dsn)
        # RLS: every connection is pinned to one tenant (db/migrations conventions).
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_state_tenant', %s, true)", (tenant_state_id,))
        return conn
