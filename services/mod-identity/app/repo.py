"""Repository interface + in-memory implementation for mod-identity.

Production target is Postgres with schema-per-tenant and Row-Level Security
(every table carries ``tenant_state_id``; see db/migrations/ and
docs/architecture/06-tenancy-security.md). The audit store is append-only by
construction — the interface exposes no update or delete for ``AuditEntry``.
"""
from __future__ import annotations

import hashlib
from typing import Dict, List, Optional, Protocol

from .models import (
    ApiConsumer,
    AuditEntry,
    ConsentGrant,
    Credential,
    Resident,
    SettlementRecord,
    UsageRecord,
)


class IdentityRepository(Protocol):
    def save_resident(self, resident: Resident) -> Resident: ...
    def get_resident(self, resident_id: str) -> Optional[Resident]: ...
    def save_credential(self, credential: Credential) -> Credential: ...
    def save_consumer(self, consumer: ApiConsumer) -> ApiConsumer: ...
    def get_consumer(self, consumer_id: str) -> Optional[ApiConsumer]: ...
    def save_consent(self, grant: ConsentGrant) -> ConsentGrant: ...
    def get_consent(self, grant_id: str) -> Optional[ConsentGrant]: ...
    def find_consent(
        self, state_id: str, resident_id: str, consumer_id: str, purpose: str
    ) -> List[ConsentGrant]: ...
    def save_usage(self, usage: UsageRecord) -> UsageRecord: ...
    def list_usage(self, state_id: str, consumer_id: str, unsettled_only: bool = False) -> List[UsageRecord]: ...
    def save_settlement(self, settlement: SettlementRecord) -> SettlementRecord: ...
    def append_audit(self, entry: AuditEntry) -> AuditEntry: ...
    def list_audit(self, state_id: Optional[str] = None) -> List[AuditEntry]: ...
    def audit_tail_hash(self) -> str: ...


class InMemoryIdentityRepository:
    GENESIS_HASH = hashlib.sha256(b"mod-identity-audit-genesis").hexdigest()

    def __init__(self) -> None:
        self._residents: Dict[str, Resident] = {}
        self._credentials: Dict[str, Credential] = {}
        self._consumers: Dict[str, ApiConsumer] = {}
        self._consents: Dict[str, ConsentGrant] = {}
        self._usage: Dict[str, UsageRecord] = {}
        self._settlements: Dict[str, SettlementRecord] = {}
        self._audit: List[AuditEntry] = []

    def save_resident(self, resident: Resident) -> Resident:
        self._residents[resident.resident_id] = resident
        return resident

    def get_resident(self, resident_id: str) -> Optional[Resident]:
        return self._residents.get(resident_id)

    def save_credential(self, credential: Credential) -> Credential:
        self._credentials[credential.credential_id] = credential
        return credential

    def save_consumer(self, consumer: ApiConsumer) -> ApiConsumer:
        self._consumers[consumer.consumer_id] = consumer
        return consumer

    def get_consumer(self, consumer_id: str) -> Optional[ApiConsumer]:
        return self._consumers.get(consumer_id)

    def save_consent(self, grant: ConsentGrant) -> ConsentGrant:
        self._consents[grant.grant_id] = grant
        return grant

    def get_consent(self, grant_id: str) -> Optional[ConsentGrant]:
        return self._consents.get(grant_id)

    def find_consent(self, state_id, resident_id, consumer_id, purpose) -> List[ConsentGrant]:
        return [
            g
            for g in self._consents.values()
            if g.state_id == state_id
            and g.resident_id == resident_id
            and g.consumer_id == consumer_id
            and g.purpose.value == purpose
        ]

    def save_usage(self, usage: UsageRecord) -> UsageRecord:
        self._usage[usage.usage_id] = usage
        return usage

    def list_usage(self, state_id, consumer_id, unsettled_only=False) -> List[UsageRecord]:
        items = [
            u
            for u in self._usage.values()
            if u.state_id == state_id and u.consumer_id == consumer_id
        ]
        if unsettled_only:
            items = [u for u in items if u.settlement_id is None]
        return items

    def save_settlement(self, settlement: SettlementRecord) -> SettlementRecord:
        self._settlements[settlement.settlement_id] = settlement
        for uid in settlement.usage_ids:
            if uid in self._usage:
                self._usage[uid].settlement_id = settlement.settlement_id
        return settlement

    def append_audit(self, entry: AuditEntry) -> AuditEntry:
        if self._audit and entry.seq != self._audit[-1].seq + 1:
            raise ValueError("audit chain sequence violation (append-only)")
        expected_prev = self.audit_tail_hash()
        if entry.prev_hash != expected_prev:
            raise ValueError("audit chain prev_hash mismatch (append-only)")
        self._audit.append(entry)
        return entry

    def list_audit(self, state_id: Optional[str] = None) -> List[AuditEntry]:
        if state_id is None:
            return list(self._audit)
        return [e for e in self._audit if e.state_id == state_id]

    def audit_tail_hash(self) -> str:
        return self._audit[-1].entry_hash if self._audit else self.GENESIS_HASH
