"""Postgres schema operator — per-tenant schema, RLS isolation policies, and
a per-tenant role, mirroring the RLS pattern in db/migrations (e.g.
``USING (tenant_state_id = current_setting('app.current_state_tenant'))``).

Fail-closed: requires the optional ``asyncpg`` dependency and ``CP_PG_DSN``.
Statements run on a fresh short-lived connection per call (sync wrapper over
asyncpg so the operator protocol stays synchronous).
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from .base import OperatorUnavailableError, ProvisionError

if TYPE_CHECKING:  # pragma: no cover
    from ..domain import ProvisionedResources, Tenant

try:  # optional dependency
    import asyncpg

    _ASYNCPG_AVAILABLE = True
except Exception:  # pragma: no cover - environment dependent
    asyncpg = None  # type: ignore[assignment]
    _ASYNCPG_AVAILABLE = False

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _check_ident(value: str, what: str) -> str:
    """Schema/role names go into DDL verbatim — validate to prevent injection."""
    if not _IDENT.match(value):
        raise ProvisionError(f"unsafe {what} identifier")
    return value


class PostgresOperator:
    name = "postgres"

    def __init__(self, dsn: str | None = None) -> None:
        if not _ASYNCPG_AVAILABLE:
            raise OperatorUnavailableError(
                "asyncpg is not installed (pip install asyncpg)"
            )
        if not dsn:
            raise OperatorUnavailableError("missing required configuration: CP_PG_DSN")
        self._dsn = dsn
        # tenant_id -> schema/role provisioned by this process (for rollback).
        self._provisioned: dict[str, tuple[str, str]] = {}

    # DDL mirrors db/migrations RLS patterns: ENABLE ROW LEVEL SECURITY +
    # tenant-isolation policy keyed on current_setting('app.current_state_tenant').
    _DDL = """
    CREATE SCHEMA IF NOT EXISTS {schema};
    CREATE ROLE {role} NOLOGIN;
    GRANT USAGE ON SCHEMA {schema} TO {role};
    ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT, INSERT, UPDATE, DELETE
        ON TABLES TO {role};
    DO $$
    DECLARE tbl RECORD;
    BEGIN
        FOR tbl IN
            SELECT t.tablename FROM pg_tables t
            WHERE t.schemaname = '{schema}'
              AND EXISTS (
                  SELECT 1 FROM information_schema.columns c
                  WHERE c.table_schema = t.schemaname AND c.table_name = t.tablename
                    AND c.column_name = 'tenant_state_id')
        LOOP
            EXECUTE format('ALTER TABLE {schema}.%I ENABLE ROW LEVEL SECURITY', tbl.tablename);
            EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON {schema}.%I', tbl.tablename);
            EXECUTE format(
                'CREATE POLICY tenant_isolation ON {schema}.%I FOR ALL TO {role} '
                'USING (tenant_state_id = current_setting(''app.current_state_tenant''))',
                tbl.tablename);
        END LOOP;
    END $$;
    """

    async def _provision_async(self, schema: str, role: str) -> None:
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.execute(self._DDL.format(schema=schema, role=role))
        finally:
            await conn.close()

    def provision(self, tenant: "Tenant", resources: "ProvisionedResources") -> str:
        schema = _check_ident(resources.postgres_schema, "schema")
        role = _check_ident(f"sos_tenant_{tenant.state}", "role")
        try:
            asyncio.run(self._provision_async(schema, role))
        except ProvisionError:
            raise
        except Exception as exc:
            raise ProvisionError(f"postgres schema/RLS provisioning failed: {exc}") from exc
        self._provisioned[tenant.tenant_id] = (schema, role)
        return "postgres-rls-applied"

    def decommission(self, tenant_id: str) -> None:
        target = self._provisioned.pop(tenant_id, None)
        if target is None:
            return None  # nothing this process provisioned; DBA runbook owns legacy
        schema, role = target

        async def _drop() -> None:
            conn = await asyncpg.connect(self._dsn)
            try:
                await conn.execute(
                    f"DROP SCHEMA IF EXISTS {schema} CASCADE; DROP ROLE IF EXISTS {role};"
                )
            finally:
                await conn.close()

        asyncio.run(_drop())
