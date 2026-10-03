"""Postgres repository for mod-identity (migration 0007, schema ``identity``).

Selected by the DSN seam ``SOS_IDENTITY_DSN`` / ``SOS_IDENTITY_PROFILE``
(fixture|local|test → in-memory; production|live → fail-closed without a DSN),
mirroring the ``SOS_WATERWAYS_PROFILE`` idiom in mod-waterways.

``psycopg`` (v3) is imported lazily inside :meth:`_connect` so fixture-profile
tests never require the driver. An already-open connection (or any duck-typed
object with psycopg3-style ``execute``/cursor semantics returning dict rows)
may be injected for tests.

NDPA boundary: raw NIN never touches the database — the repo persists only
``nin_hash`` (SHA-256) and ``nin_tail`` (last three characters, enough to
reconstruct the masked display form).
"""
from __future__ import annotations

import hashlib
import os
from typing import Dict, List, Optional

from .models import (
    ApiConsumer,
    AuditEntry,
    ConsentGrant,
    Credential,
    GuardianLink,
    Resident,
    ResidentStatus,
    SettlementLine,
    SettlementRecord,
    UsageRecord,
    VerificationProduct,
)
from .repo import InMemoryIdentityRepository, IdentityRepository


def _nin_hash(nin: str) -> str:
    return hashlib.sha256(nin.encode()).hexdigest()


def _masked_nin(tail: str) -> str:
    return f"********{tail}"


class PostgresIdentityRepository:
    """IdentityRepository over PostgreSQL (schema ``identity``, RLS-aware).

    Every statement carries ``tenant_state_id``; the session must set
    ``app.current_state_tenant`` (see db/migrations/0001-0007 policies).
    """

    GENESIS_HASH = InMemoryIdentityRepository.GENESIS_HASH

    def __init__(self, dsn: str = "", conn=None) -> None:
        if not dsn and conn is None:
            raise ValueError("PostgresIdentityRepository requires a DSN or connection")
        self._dsn = dsn
        self._conn = conn

    # -- connection plumbing ---------------------------------------------------
    def _connect(self):
        import psycopg  # lazy: fixture-profile deployments never import this

        from psycopg.rows import dict_row

        return psycopg.connect(self._dsn, row_factory=dict_row, autocommit=True)

    def _execute(self, sql: str, params: tuple = ()):
        if self._conn is not None:
            return self._conn.execute(sql, params)
        with self._connect() as conn:
            return conn.execute(sql, params)

    # -- residents ---------------------------------------------------------------
    def save_resident(self, resident: Resident) -> Resident:
        self._execute(
            """
            INSERT INTO identity.residents
                (resident_id, tenant_state_id, nin_hash, nin_tail, full_name,
                 address, registered_at, active, status, date_of_birth)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (resident_id) DO UPDATE SET
                full_name = EXCLUDED.full_name,
                address = EXCLUDED.address,
                active = EXCLUDED.active,
                status = EXCLUDED.status
            """,
            (
                resident.resident_id, resident.state_id, _nin_hash(resident.nin),
                resident.nin[-3:], resident.full_name, resident.address,
                resident.registered_at, resident.active, resident.status.value,
                resident.date_of_birth,
            ),
        )
        return resident

    def get_resident(self, resident_id: str) -> Optional[Resident]:
        row = self._execute(
            "SELECT * FROM identity.residents WHERE resident_id = %s",
            (resident_id,),
        ).fetchone()
        if row is None:
            return None
        return Resident(
            resident_id=row["resident_id"],
            state_id=row["tenant_state_id"],
            nin=_masked_nin(row["nin_tail"]),
            full_name=row["full_name"],
            address=row["address"],
            registered_at=row["registered_at"],
            active=row["active"],
            status=ResidentStatus(row["status"]),
            date_of_birth=row["date_of_birth"],
        )

    # -- credentials / consumers ---------------------------------------------------
    def save_credential(self, credential: Credential) -> Credential:
        self._execute(
            """
            INSERT INTO identity.credentials
                (credential_id, tenant_state_id, resident_id, credential_type,
                 issued_at, revoked_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (credential_id) DO UPDATE SET revoked_at = EXCLUDED.revoked_at
            """,
            (
                credential.credential_id, credential.state_id,
                credential.resident_id, credential.credential_type,
                credential.issued_at, credential.revoked_at,
            ),
        )
        return credential

    def save_consumer(self, consumer: ApiConsumer) -> ApiConsumer:
        self._execute(
            """
            INSERT INTO identity.api_consumers
                (consumer_id, tenant_state_id, name, active, registered_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (consumer_id) DO UPDATE SET
                name = EXCLUDED.name, active = EXCLUDED.active
            """,
            (consumer.consumer_id, consumer.state_id, consumer.name,
             consumer.active, consumer.registered_at),
        )
        return consumer

    def get_consumer(self, consumer_id: str) -> Optional[ApiConsumer]:
        row = self._execute(
            "SELECT * FROM identity.api_consumers WHERE consumer_id = %s",
            (consumer_id,),
        ).fetchone()
        if row is None:
            return None
        return ApiConsumer(
            consumer_id=row["consumer_id"], state_id=row["tenant_state_id"],
            name=row["name"], active=row["active"],
            registered_at=row["registered_at"],
        )

    # -- consent -------------------------------------------------------------------
    def save_consent(self, grant: ConsentGrant) -> ConsentGrant:
        self._execute(
            """
            INSERT INTO identity.consent_grants
                (grant_id, tenant_state_id, resident_id, consumer_id, purpose,
                 created_at, expires_at, revoked_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (grant_id) DO UPDATE SET revoked_at = EXCLUDED.revoked_at
            """,
            (grant.grant_id, grant.state_id, grant.resident_id, grant.consumer_id,
             grant.purpose.value, grant.created_at, grant.expires_at, grant.revoked_at),
        )
        return grant

    def _grant_from_row(self, row) -> ConsentGrant:
        return ConsentGrant(
            grant_id=row["grant_id"], state_id=row["tenant_state_id"],
            resident_id=row["resident_id"], consumer_id=row["consumer_id"],
            purpose=VerificationProduct(row["purpose"]),
            created_at=row["created_at"], expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
        )

    def get_consent(self, grant_id: str) -> Optional[ConsentGrant]:
        row = self._execute(
            "SELECT * FROM identity.consent_grants WHERE grant_id = %s",
            (grant_id,),
        ).fetchone()
        return self._grant_from_row(row) if row else None

    def find_consent(self, state_id: str, resident_id: str, consumer_id: str,
                     purpose: str) -> List[ConsentGrant]:
        rows = self._execute(
            """
            SELECT * FROM identity.consent_grants
            WHERE tenant_state_id = %s AND resident_id = %s
              AND consumer_id = %s AND purpose = %s
            ORDER BY created_at
            """,
            (state_id, resident_id, consumer_id, purpose),
        ).fetchall()
        return [self._grant_from_row(r) for r in rows]

    # -- guardian links --------------------------------------------------------------
    def save_guardian_link(self, link: GuardianLink) -> GuardianLink:
        self._execute(
            """
            INSERT INTO identity.guardian_links
                (tenant_state_id, resident_id, guardian_resident_id,
                 kyc_case_ref, expires_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (link.state_id, link.resident_id, link.guardian_resident_id,
             link.kyc_case_ref, link.expires_at, link.created_at),
        )
        return link

    def find_guardian_links(self, state_id: str, resident_id: str) -> List[GuardianLink]:
        rows = self._execute(
            """
            SELECT * FROM identity.guardian_links
            WHERE tenant_state_id = %s AND resident_id = %s
            ORDER BY created_at
            """,
            (state_id, resident_id),
        ).fetchall()
        return [
            GuardianLink(
                state_id=r["tenant_state_id"], resident_id=r["resident_id"],
                guardian_resident_id=r["guardian_resident_id"],
                kyc_case_ref=r["kyc_case_ref"], expires_at=r["expires_at"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # -- usage / settlements ------------------------------------------------------------
    def save_usage(self, usage: UsageRecord) -> UsageRecord:
        self._execute(
            """
            INSERT INTO identity.usage_records
                (usage_id, tenant_state_id, consumer_id, product, fee_kobo,
                 result_id, recorded_at, settlement_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (usage_id) DO UPDATE SET
                settlement_id = EXCLUDED.settlement_id
            """,
            (usage.usage_id, usage.state_id, usage.consumer_id,
             usage.product.value, usage.fee_kobo, usage.result_id,
             usage.recorded_at, usage.settlement_id),
        )
        return usage

    def list_usage(self, state_id: str, consumer_id: str,
                   unsettled_only: bool = False) -> List[UsageRecord]:
        sql = (
            "SELECT * FROM identity.usage_records "
            "WHERE tenant_state_id = %s AND consumer_id = %s"
        )
        params = [state_id, consumer_id]
        if unsettled_only:
            sql += " AND settlement_id IS NULL"
        sql += " ORDER BY recorded_at"
        rows = self._execute(sql, tuple(params)).fetchall()
        return [
            UsageRecord(
                usage_id=r["usage_id"], state_id=r["tenant_state_id"],
                consumer_id=r["consumer_id"],
                product=VerificationProduct(r["product"]),
                fee_kobo=r["fee_kobo"], result_id=r["result_id"],
                recorded_at=r["recorded_at"], settlement_id=r["settlement_id"],
            )
            for r in rows
        ]

    def save_settlement(self, settlement: SettlementRecord) -> SettlementRecord:
        import json

        self._execute(
            """
            INSERT INTO identity.settlement_records
                (settlement_id, tenant_state_id, consumer_id, usage_ids,
                 total_kobo, lines, settled_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (settlement_id) DO NOTHING
            """,
            (settlement.settlement_id, settlement.state_id, settlement.consumer_id,
             list(settlement.usage_ids), settlement.total_kobo,
             json.dumps([l.model_dump() for l in settlement.lines]),
             settlement.settled_at),
        )
        for uid in settlement.usage_ids:
            self._execute(
                "UPDATE identity.usage_records SET settlement_id = %s "
                "WHERE usage_id = %s",
                (settlement.settlement_id, uid),
            )
        return settlement

    # -- append-only audit --------------------------------------------------------------
    def append_audit(self, entry: AuditEntry) -> AuditEntry:
        tail = self.audit_tail_hash()
        count = self.audit_count()
        if entry.seq != count + 1:
            raise ValueError("audit chain sequence violation (append-only)")
        if entry.prev_hash != tail:
            raise ValueError("audit chain prev_hash mismatch (append-only)")
        self._execute(
            """
            INSERT INTO identity.audit_entries
                (tenant_state_id, seq, action, actor_id, subject_id, details,
                 prev_hash, entry_hash, at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (entry.state_id, entry.seq, entry.action, entry.actor_id,
             entry.subject_id, entry.details, entry.prev_hash, entry.entry_hash,
             entry.at),
        )
        return entry

    def list_audit(self, state_id: Optional[str] = None) -> List[AuditEntry]:
        if state_id is None:
            rows = self._execute(
                "SELECT * FROM identity.audit_entries ORDER BY seq").fetchall()
        else:
            rows = self._execute(
                "SELECT * FROM identity.audit_entries "
                "WHERE tenant_state_id = %s ORDER BY seq",
                (state_id,),
            ).fetchall()
        return [
            AuditEntry(
                seq=r["seq"], action=r["action"], state_id=r["tenant_state_id"],
                actor_id=r["actor_id"], subject_id=r["subject_id"],
                details=r["details"], prev_hash=r["prev_hash"],
                entry_hash=r["entry_hash"], at=r["at"],
            )
            for r in rows
        ]

    def audit_tail_hash(self) -> str:
        row = self._execute(
            "SELECT entry_hash FROM identity.audit_entries "
            "ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["entry_hash"] if row else self.GENESIS_HASH

    def audit_count(self) -> int:
        row = self._execute(
            "SELECT COUNT(*) AS n FROM identity.audit_entries").fetchone()
        return int(row["n"])


def build_identity_repository(env: Optional[Dict[str, str]] = None) -> IdentityRepository:
    """Select the repository from ``SOS_IDENTITY_DSN`` / ``SOS_IDENTITY_PROFILE``.

    fixture|local|test (default) → in-memory. production|live without
    ``SOS_IDENTITY_DSN`` fails closed at boot (same idiom as
    ``SOS_WATERWAYS_PROFILE`` in mod-waterways).
    """
    env = dict(os.environ if env is None else env)
    dsn = env.get("SOS_IDENTITY_DSN", "")
    if dsn:
        return PostgresIdentityRepository(dsn)
    profile = env.get("SOS_IDENTITY_PROFILE", "fixture")
    if profile in ("production", "live"):
        raise RuntimeError(
            "SOS_IDENTITY_PROFILE=production requires SOS_IDENTITY_DSN "
            "(fail-closed: refusing to boot on the in-memory repository)"
        )
    return InMemoryIdentityRepository()
