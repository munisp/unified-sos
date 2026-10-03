"""Postgres persistence for mod-waterways (migration 0010, schema ``waterways``).

Covers routes/trips/tickets/dredgers/surveys with the two integrity-critical
paths implemented first:

- **tickets** — idempotent insert on ``(tenant_state_id, idempotency_key)``;
  a replay with the same payload returns the stored ticket, a replay with a
  different payload raises :class:`TicketIdempotencyConflict` (HTTP 409).
- **surveys** — dedupe on ``(tenant_state_id, dedupe_key)``;
  :class:`SurveyDuplicateError` on re-ingest (prevents double royalty).

Selected by ``SOS_WATERWAYS_DSN``; ``SOS_WATERWAYS_PROFILE=production|live``
without a DSN fails closed at boot (mirrors build_sedona_adapter). ``psycopg``
(v3) is imported lazily so fixture-profile tests never touch the driver; a
duck-typed connection may be injected for tests.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from .domain import (
    Dredger,
    Route,
    Survey,
    SurveyDuplicateError,
    Ticket,
    TicketIdempotencyConflict,
    Trip,
    TripStatus,
)

#: Unique-violation SQLSTATE (psycopg raises; duck-typed fakes may set
#: ``sqlstate`` on the exception or return an empty RETURNING set).
PG_UNIQUE_VIOLATION = "23505"


class PostgresWaterwaysRepository:
    def __init__(self, dsn: str = "", conn=None) -> None:
        if not dsn and conn is None:
            raise ValueError("PostgresWaterwaysRepository requires a DSN or connection")
        self._dsn = dsn
        self._conn = conn

    def _connect(self):
        import psycopg  # lazy: fixture profile never imports the driver

        from psycopg.rows import dict_row

        return psycopg.connect(self._dsn, row_factory=dict_row, autocommit=True)

    def _execute(self, sql: str, params: tuple = ()):
        if self._conn is not None:
            return self._conn.execute(sql, params)
        with self._connect() as conn:
            return conn.execute(sql, params)

    # -- routes -----------------------------------------------------------------
    def save_route(self, route: Route) -> Route:
        self._execute(
            """
            INSERT INTO waterways.routes
                (route_id, tenant_state_id, name, origin_jetty,
                 destination_jetty, distance_km)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (route_id) DO NOTHING
            """,
            (route.route_id, route.tenant_state_id, route.name,
             route.origin_jetty, route.destination_jetty, route.distance_km),
        )
        return route

    def list_routes(self, tenant: str) -> List[Route]:
        rows = self._execute(
            "SELECT * FROM waterways.routes WHERE tenant_state_id = %s "
            "ORDER BY route_id",
            (tenant,),
        ).fetchall()
        return [Route(**{k: r[k] for k in (
            "route_id", "tenant_state_id", "name", "origin_jetty",
            "destination_jetty", "distance_km")}) for r in rows]

    # -- trips ------------------------------------------------------------------
    def save_trip(self, trip: Trip) -> Trip:
        self._execute(
            """
            INSERT INTO waterways.trips
                (trip_id, tenant_state_id, route_id, vessel, capacity,
                 departure, status, manifest_locked, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (trip_id) DO UPDATE SET
                status = EXCLUDED.status,
                manifest_locked = EXCLUDED.manifest_locked
            """,
            (trip.trip_id, trip.tenant_state_id, trip.route_id, trip.vessel,
             trip.capacity, trip.departure, trip.status.value,
             trip.manifest_locked, trip.created_at),
        )
        return trip

    def get_trip(self, tenant: str, trip_id: str) -> Optional[Trip]:
        row = self._execute(
            "SELECT * FROM waterways.trips "
            "WHERE tenant_state_id = %s AND trip_id = %s",
            (tenant, trip_id),
        ).fetchone()
        if row is None:
            return None
        return Trip(
            trip_id=row["trip_id"], tenant_state_id=row["tenant_state_id"],
            route_id=row["route_id"], vessel=row["vessel"],
            capacity=row["capacity"], departure=row["departure"],
            status=TripStatus(row["status"]),
            manifest_locked=row["manifest_locked"], created_at=row["created_at"],
        )

    # -- tickets (idempotent) -----------------------------------------------------
    def save_ticket_idempotent(
        self,
        ticket: Ticket,
        tenant: str,
        idempotency_key: Optional[str],
        request_digest: Optional[str],
    ) -> Ticket:
        """Insert a ticket under its idempotency key.

        Same key + same payload digest → replay the stored ticket. Same key +
        different digest → :class:`TicketIdempotencyConflict`.
        """
        if not idempotency_key:
            self._insert_ticket(ticket, idempotency_key, request_digest)
            return ticket
        row = self._execute(
            "SELECT ticket_id, request_hash FROM waterways.tickets "
            "WHERE tenant_state_id = %s AND idempotency_key = %s",
            (tenant, idempotency_key),
        ).fetchone()
        if row is not None:
            if row["request_hash"] != request_digest:
                raise TicketIdempotencyConflict(
                    f"Idempotency-Key '{idempotency_key}' replayed with a "
                    f"different payload")
            stored = self._execute(
                "SELECT * FROM waterways.tickets WHERE ticket_id = %s",
                (row["ticket_id"],),
            ).fetchone()
            return self._ticket_from_row(stored)
        self._insert_ticket(ticket, idempotency_key, request_digest)
        return ticket

    def _insert_ticket(self, ticket: Ticket, idem_key, digest) -> None:
        self._execute(
            """
            INSERT INTO waterways.tickets
                (ticket_id, tenant_state_id, trip_id, passenger_name,
                 fare_kobo, qr_ref, sold_at, idempotency_key, request_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (ticket.ticket_id, ticket.tenant_state_id, ticket.trip_id,
             ticket.passenger_name, ticket.fare_kobo, ticket.qr_ref,
             ticket.sold_at, idem_key, digest),
        )

    @staticmethod
    def _ticket_from_row(row) -> Ticket:
        return Ticket(
            ticket_id=row["ticket_id"], tenant_state_id=row["tenant_state_id"],
            trip_id=row["trip_id"], passenger_name=row["passenger_name"],
            fare_kobo=row["fare_kobo"], qr_ref=row["qr_ref"],
            sold_at=row["sold_at"],
        )

    def list_tickets(self, tenant: str, trip_id: Optional[str] = None) -> List[Ticket]:
        sql = "SELECT * FROM waterways.tickets WHERE tenant_state_id = %s"
        params = [tenant]
        if trip_id is not None:
            sql += " AND trip_id = %s"
            params.append(trip_id)
        sql += " ORDER BY sold_at"
        rows = self._execute(sql, tuple(params)).fetchall()
        return [self._ticket_from_row(r) for r in rows]

    def count_tickets(self, trip_id: str) -> int:
        row = self._execute(
            "SELECT COUNT(*) AS n FROM waterways.tickets WHERE trip_id = %s",
            (trip_id,),
        ).fetchone()
        return int(row["n"])

    # -- dredgers -----------------------------------------------------------------
    def save_dredger(self, dredger: Dredger) -> Dredger:
        self._execute(
            """
            INSERT INTO waterways.dredgers
                (dredger_id, tenant_state_id, vessel_name, license_no,
                 operator_kyb_ref, monthly_quota_m3, registered_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (dredger_id) DO NOTHING
            """,
            (dredger.dredger_id, dredger.tenant_state_id, dredger.vessel_name,
             dredger.license_no, dredger.operator_kyb_ref,
             dredger.monthly_quota_m3, dredger.registered_at),
        )
        return dredger

    def get_dredger(self, tenant: str, dredger_id: str) -> Optional[Dredger]:
        row = self._execute(
            "SELECT * FROM waterways.dredgers "
            "WHERE tenant_state_id = %s AND dredger_id = %s",
            (tenant, dredger_id),
        ).fetchone()
        if row is None:
            return None
        return Dredger(
            dredger_id=row["dredger_id"], tenant_state_id=row["tenant_state_id"],
            vessel_name=row["vessel_name"], license_no=row["license_no"],
            operator_kyb_ref=row["operator_kyb_ref"],
            monthly_quota_m3=float(row["monthly_quota_m3"]),
            registered_at=row["registered_at"],
        )

    # -- surveys (deduped) ------------------------------------------------------------
    def save_survey(self, survey: Survey, dedupe_key: str) -> Survey:
        """Insert a survey; a re-ingested ``dedupe_key`` raises
        :class:`SurveyDuplicateError` (the DB unique constraint is the
        authoritative guard against double royalty assessment)."""
        try:
            cur = self._execute(
                """
                INSERT INTO waterways.surveys
                    (survey_id, tenant_state_id, dredger_id, polygon,
                     volume_m3, surveyed_at, verified_volume_m3, month,
                     dedupe_key)
                VALUES (%s, %s, %s,
                        ST_GeomFromGeoJSON(%s), %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_state_id, dedupe_key) DO NOTHING
                RETURNING survey_id
                """,
                (survey.survey_id, survey.tenant_state_id, survey.dredger_id,
                 _polygon_geojson(survey.polygon), survey.volume_m3,
                 survey.surveyed_at, survey.verified_volume_m3, survey.month,
                 dedupe_key),
            )
            inserted = cur.fetchone() if hasattr(cur, "fetchone") else None
        except Exception as exc:  # unique violation without ON CONFLICT support
            if getattr(exc, "sqlstate", None) == PG_UNIQUE_VIOLATION:
                raise SurveyDuplicateError(
                    f"duplicate survey (dedupe key '{dedupe_key[:24]}…')") from exc
            raise
        if inserted is None:
            raise SurveyDuplicateError(
                f"duplicate survey (dedupe key '{dedupe_key[:24]}…') for dredger "
                f"'{survey.dredger_id}' in {survey.month} — already assessed")
        return survey

    def get_survey(self, tenant: str, survey_id: str) -> Optional[Survey]:
        row = self._execute(
            "SELECT * FROM waterways.surveys "
            "WHERE tenant_state_id = %s AND survey_id = %s",
            (tenant, survey_id),
        ).fetchone()
        if row is None:
            return None
        return Survey(
            survey_id=row["survey_id"], tenant_state_id=row["tenant_state_id"],
            dredger_id=row["dredger_id"], polygon=row.get("polygon") or [],
            volume_m3=float(row["volume_m3"]), surveyed_at=row["surveyed_at"],
            verified_volume_m3=(
                float(row["verified_volume_m3"])
                if row["verified_volume_m3"] is not None else None),
            month=row["month"],
        )

    def surveys_for_month(self, dredger_id: str, month: str) -> List[Survey]:
        rows = self._execute(
            "SELECT * FROM waterways.surveys "
            "WHERE dredger_id = %s AND month = %s ORDER BY surveyed_at",
            (dredger_id, month),
        ).fetchall()
        return [
            Survey(
                survey_id=r["survey_id"], tenant_state_id=r["tenant_state_id"],
                dredger_id=r["dredger_id"], polygon=r.get("polygon") or [],
                volume_m3=float(r["volume_m3"]), surveyed_at=r["surveyed_at"],
                verified_volume_m3=(
                    float(r["verified_volume_m3"])
                    if r["verified_volume_m3"] is not None else None),
                month=r["month"],
            )
            for r in rows
        ]


def _polygon_geojson(polygon: list) -> str:
    import json

    ring = list(polygon)
    if ring[0] != ring[-1]:
        ring = ring + [ring[0]]
    return json.dumps({"type": "Polygon", "coordinates": [ring]})


def build_waterways_repository(env: Optional[Dict[str, str]] = None):
    """Select persistence from ``SOS_WATERWAYS_DSN``/``SOS_WATERWAYS_PROFILE``.

    fixture|local|test (default) → ``None`` (the caller keeps the in-memory
    ``WaterwaysStore``); production|live without a DSN fails closed at boot.
    """
    env = dict(os.environ if env is None else env)
    dsn = env.get("SOS_WATERWAYS_DSN", "")
    if dsn:
        return PostgresWaterwaysRepository(dsn)
    profile = env.get("SOS_WATERWAYS_PROFILE", "fixture")
    if profile in ("production", "live"):
        raise RuntimeError(
            "SOS_WATERWAYS_PROFILE=production requires SOS_WATERWAYS_DSN "
            "(fail-closed: refusing to boot on the in-memory store)"
        )
    return None
