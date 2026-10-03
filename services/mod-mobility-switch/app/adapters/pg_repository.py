"""Postgres persistence for mod-mobility-switch (migration 0011).

Escrows (Mojaloop-style pending transfers, idempotent on ``transfer_id``) and
settlement batches. Selected by ``SOS_MOBILITY_DSN``; the existing
``SOS_PROFILE=production`` seam fails closed without a DSN (mirrors
``select_ledger_adapter``).

``psycopg`` (v3) is imported lazily so fixture-profile tests never import the
driver; a duck-typed connection may be injected for tests.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from ..domain import EscrowRecord, EscrowState, SettlementBatch, SettlementLeg


class AdapterUnavailableError(RuntimeError):
    """Fail-closed boot error (mirrors adapters.base.AdapterUnavailableError)."""


class PostgresEscrowRepository:
    """Pending-transfer escrow store over ``mobility.escrows``."""

    def __init__(self, dsn: str = "", conn=None) -> None:
        if not dsn and conn is None:
            raise ValueError("PostgresEscrowRepository requires a DSN or connection")
        self._dsn = dsn
        self._conn = conn

    def _connect(self):
        import psycopg  # lazy import

        from psycopg.rows import dict_row

        return psycopg.connect(self._dsn, row_factory=dict_row, autocommit=True)

    def _execute(self, sql: str, params: tuple = ()):
        if self._conn is not None:
            return self._conn.execute(sql, params)
        with self._connect() as conn:
            return conn.execute(sql, params)

    # -- escrows -------------------------------------------------------------
    def begin_escrow(self, escrow: EscrowRecord) -> tuple[EscrowRecord, bool]:
        """Idempotent prepare. Returns ``(record, created)``; a replay with
        identical terms returns the stored record, divergent terms raise."""
        row = self._execute(
            "SELECT * FROM mobility.escrows WHERE transfer_id = %s",
            (escrow.transfer_id,),
        ).fetchone()
        if row is not None:
            stored = self._from_row(row)
            if stored.batch_id != escrow.batch_id or stored.amount_kobo != escrow.amount_kobo:
                raise ValueError(
                    f"transfer_id '{escrow.transfer_id}' already escrowed with "
                    f"different terms")
            return stored, False
        self._execute(
            """
            INSERT INTO mobility.escrows
                (transfer_id, batch_id, amount_kobo, condition, state,
                 fulfilment, created_at, expires_at, completed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (escrow.transfer_id, escrow.batch_id, escrow.amount_kobo,
             escrow.condition, escrow.state.value, escrow.fulfilment,
             escrow.created_at, escrow.expires_at, escrow.completed_at),
        )
        return escrow, True

    def get_escrow(self, transfer_id: str) -> Optional[EscrowRecord]:
        row = self._execute(
            "SELECT * FROM mobility.escrows WHERE transfer_id = %s",
            (transfer_id,),
        ).fetchone()
        return self._from_row(row) if row else None

    def update_escrow_state(self, transfer_id: str, state: EscrowState,
                            fulfilment: Optional[str], completed_at: str) -> None:
        self._execute(
            "UPDATE mobility.escrows SET state = %s, fulfilment = %s, "
            "completed_at = %s WHERE transfer_id = %s",
            (state.value, fulfilment, completed_at, transfer_id),
        )

    def list_expired_pending(self, now_iso: str) -> List[EscrowRecord]:
        rows = self._execute(
            "SELECT * FROM mobility.escrows WHERE state = 'pending' "
            "AND expires_at <= %s",
            (now_iso,),
        ).fetchall()
        return [self._from_row(r) for r in rows]

    @staticmethod
    def _from_row(row) -> EscrowRecord:
        return EscrowRecord(
            transfer_id=row["transfer_id"], batch_id=row["batch_id"],
            amount_kobo=row["amount_kobo"], condition=row["condition"],
            state=EscrowState(row["state"]), fulfilment=row["fulfilment"],
            created_at=row["created_at"], expires_at=row["expires_at"],
            completed_at=row["completed_at"],
        )

    # -- settlement batches -----------------------------------------------------
    def save_batch(self, batch: SettlementBatch) -> SettlementBatch:
        self._execute(
            """
            INSERT INTO mobility.settlement_batches
                (batch_id, tenant_state_id, operator_id, record_count,
                 gross_kobo, legs, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (batch_id) DO NOTHING
            """,
            (batch.batch_id, batch.tenant_state_id, batch.operator_id,
             batch.record_count, batch.gross_kobo,
             json.dumps([l.model_dump() for l in batch.legs]), batch.created_at),
        )
        return batch

    def get_batch(self, batch_id: str) -> Optional[SettlementBatch]:
        row = self._execute(
            "SELECT * FROM mobility.settlement_batches WHERE batch_id = %s",
            (batch_id,),
        ).fetchone()
        return self._batch_from_row(row) if row else None

    def list_batches(self, tenant_state_id: str,
                     operator_id: Optional[str] = None) -> List[SettlementBatch]:
        sql = "SELECT * FROM mobility.settlement_batches WHERE tenant_state_id = %s"
        params = [tenant_state_id]
        if operator_id is not None:
            sql += " AND operator_id = %s"
            params.append(operator_id)
        sql += " ORDER BY created_at"
        rows = self._execute(sql, tuple(params)).fetchall()
        return [self._batch_from_row(r) for r in rows]

    @staticmethod
    def _batch_from_row(row) -> SettlementBatch:
        legs = row["legs"]
        if isinstance(legs, str):
            legs = json.loads(legs)
        return SettlementBatch(
            batch_id=row["batch_id"], tenant_state_id=row["tenant_state_id"],
            operator_id=row["operator_id"], record_count=row["record_count"],
            gross_kobo=row["gross_kobo"],
            legs=[SettlementLeg(**l) for l in legs],
            created_at=row["created_at"],
        )


def build_mobility_repository(env: Optional[Dict[str, str]] = None):
    """Select persistence from ``SOS_MOBILITY_DSN`` / ``SOS_PROFILE``.

    Dev/test → ``None`` (caller keeps the in-memory store);
    ``SOS_PROFILE=production`` without a DSN fails closed (same idiom as
    ``select_ledger_adapter``).
    """
    env = dict(os.environ if env is None else env)
    dsn = env.get("SOS_MOBILITY_DSN", "")
    if dsn:
        return PostgresEscrowRepository(dsn)
    if env.get("SOS_PROFILE") == "production":
        raise AdapterUnavailableError(
            "production profile requires SOS_MOBILITY_DSN")
    return None
