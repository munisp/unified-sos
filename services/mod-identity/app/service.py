"""Service layer for mod-identity — registry, consent, metered verification.

Tenant isolation rule: every operation takes an explicit ``state_id`` and
raises :class:`TenantIsolationError` when the target record belongs to a
different tenant. NDPA rule: no method on this service returns a ``Resident``
record to an API-consumer path; verification returns booleans only.
"""
from __future__ import annotations

import itertools
from datetime import datetime
from typing import Optional

from .models import (
    ApiConsumer,
    AuditEntry,
    ConsentGrant,
    Credential,
    Resident,
    SettlementLine,
    SettlementRecord,
    UsageRecord,
    VerificationProduct,
    VerificationResult,
)
from .repo import IdentityRepository


class NotFoundError(Exception):
    pass


class ConsentError(Exception):
    """No active consent grant for the (resident, consumer, purpose) tuple."""


class TenantIsolationError(Exception):
    """Cross-tenant access attempt."""


# Per-call list prices (integer kobo) — negotiating starting points [DERIVED],
# superseded by config/states/<state>/ policy packs in production.
PRODUCT_FEES_KOBO = {
    VerificationProduct.ADDRESS_VERIFICATION: 5_000,  # ₦50
    VerificationProduct.RESIDENCY_ATTESTATION: 10_000,  # ₦100
    VerificationProduct.KYC_ADJUNCT: 15_000,  # ₦150
}

# Revenue-share split [DERIVED]: 70% state TSA / 30% platform concessionaire.
STATE_SHARE_BPS = 7_000


class IdentityService:
    def __init__(self, repo: IdentityRepository) -> None:
        self.repo = repo
        self._ids = itertools.count(1)

    def _next_id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._ids):06d}"

    # -- audit -----------------------------------------------------------
    def _audit(self, action: str, state_id: str, actor_id: str, subject_id: str, details: str = "") -> AuditEntry:
        prev = self.repo.audit_tail_hash()
        seq = len(self.repo.list_audit()) + 1
        payload = f"{seq}|{action}|{state_id}|{actor_id}|{subject_id}|{details}"
        entry = AuditEntry(
            seq=seq,
            action=action,
            state_id=state_id,
            actor_id=actor_id,
            subject_id=subject_id,
            details=details,
            prev_hash=prev,
            entry_hash=AuditEntry.compute_hash(prev, payload),
        )
        return self.repo.append_audit(entry)

    # -- tenant guard ------------------------------------------------------
    @staticmethod
    def _require_tenant(record_state: str, state_id: str, what: str) -> None:
        if record_state != state_id:
            raise TenantIsolationError(f"{what} belongs to tenant {record_state!r}, not {state_id!r}")

    # -- registry ----------------------------------------------------------
    def register_resident(self, resident: Resident) -> Resident:
        saved = self.repo.save_resident(resident)
        self._audit("RESIDENT_REGISTERED", resident.state_id, "registry", resident.resident_id)
        return saved

    def issue_credential(self, credential: Credential) -> Credential:
        resident = self.repo.get_resident(credential.resident_id)
        if resident is None:
            raise NotFoundError(f"resident {credential.resident_id!r} not found")
        self._require_tenant(resident.state_id, credential.state_id, "resident")
        saved = self.repo.save_credential(credential)
        self._audit("CREDENTIAL_ISSUED", credential.state_id, "registry", credential.resident_id, credential.credential_type)
        return saved

    def register_consumer(self, consumer: ApiConsumer) -> ApiConsumer:
        saved = self.repo.save_consumer(consumer)
        self._audit("CONSUMER_REGISTERED", consumer.state_id, "registry", consumer.consumer_id)
        return saved

    # -- consent lifecycle (NDPA) -------------------------------------------
    def grant_consent(self, grant: ConsentGrant) -> ConsentGrant:
        resident = self.repo.get_resident(grant.resident_id)
        if resident is None:
            raise NotFoundError(f"resident {grant.resident_id!r} not found")
        self._require_tenant(resident.state_id, grant.state_id, "resident")
        consumer = self.repo.get_consumer(grant.consumer_id)
        if consumer is None or consumer.state_id != grant.state_id:
            raise NotFoundError(f"consumer {grant.consumer_id!r} not registered in tenant {grant.state_id!r}")
        if grant.expires_at <= grant.created_at:
            raise ValueError("consent grant must expire after creation")
        saved = self.repo.save_consent(grant)
        self._audit("CONSENT_GRANTED", grant.state_id, grant.consumer_id, grant.resident_id, grant.purpose.value)
        return saved

    def revoke_consent(self, grant_id: str, state_id: str, at: Optional[datetime] = None) -> ConsentGrant:
        grant = self.repo.get_consent(grant_id)
        if grant is None:
            raise NotFoundError(f"consent grant {grant_id!r} not found")
        self._require_tenant(grant.state_id, state_id, "consent grant")
        if grant.revoked_at is None:
            from .models import utcnow

            grant.revoked_at = at or utcnow()
            self.repo.save_consent(grant)
            self._audit("CONSENT_REVOKED", grant.state_id, grant.consumer_id, grant.resident_id, grant.purpose.value)
        return grant

    def _active_consent(self, state_id, resident_id, consumer_id, product) -> Optional[ConsentGrant]:
        grants = self.repo.find_consent(state_id, resident_id, consumer_id, product.value)
        active = [g for g in grants if g.is_active()]
        return active[-1] if active else None

    # -- metered verification -------------------------------------------------
    def verify(
        self,
        state_id: str,
        consumer_id: str,
        resident_id: str,
        product: VerificationProduct,
        claim: str = "",
    ) -> VerificationResult:
        """Run a metered verification call.

        Raises :class:`ConsentError` when no active grant exists — the denied
        attempt is still audit-logged. Returns a boolean attestation only.
        """
        consumer = self.repo.get_consumer(consumer_id)
        if consumer is None or not consumer.active:
            raise NotFoundError(f"consumer {consumer_id!r} not found or inactive")
        self._require_tenant(consumer.state_id, state_id, "consumer")
        resident = self.repo.get_resident(resident_id)
        if resident is None:
            raise NotFoundError(f"resident {resident_id!r} not found")
        self._require_tenant(resident.state_id, state_id, "resident")

        consent = self._active_consent(state_id, resident_id, consumer_id, product)
        if consent is None:
            self._audit("VERIFY_DENIED_NO_CONSENT", state_id, consumer_id, resident_id, product.value)
            raise ConsentError(
                f"no active consent for ({resident_id}, {consumer_id}, {product.value})"
            )

        if product is VerificationProduct.ADDRESS_VERIFICATION:
            attested = claim.strip().lower() == resident.address.strip().lower()
        else:  # RESIDENCY_ATTESTATION / KYC_ADJUNCT: attest registered + active
            attested = resident.active

        result = VerificationResult(
            result_id=self._next_id("VR"),
            state_id=state_id,
            consumer_id=consumer_id,
            product=product,
            attested=attested,
            consent_grant_id=consent.grant_id,
        )
        usage = UsageRecord(
            usage_id=self._next_id("U"),
            state_id=state_id,
            consumer_id=consumer_id,
            product=product,
            fee_kobo=PRODUCT_FEES_KOBO[product],
            result_id=result.result_id,
        )
        self.repo.save_usage(usage)
        self._audit("VERIFY_GRANTED", state_id, consumer_id, resident_id, f"{product.value}:{attested}")
        return result

    # -- settlement (TigerBeetle wiring) --------------------------------------
    def settle_consumer(self, state_id: str, consumer_id: str) -> SettlementRecord:
        """Settle all unsettled usage into a revenue-share record.

        Split [DERIVED]: 70% → account 3001 (State Consolidated Revenue Fund,
        transfer code 101), 30% → account 2099 (PPP Tech Concessionaire
        Escrow, transfer code 103). In production each line becomes one leg of
        a TigerBeetle two-phase transfer (ADR-002).
        """
        consumer = self.repo.get_consumer(consumer_id)
        if consumer is None:
            raise NotFoundError(f"consumer {consumer_id!r} not found")
        self._require_tenant(consumer.state_id, state_id, "consumer")
        usage = self.repo.list_usage(state_id, consumer_id, unsettled_only=True)
        if not usage:
            raise ValueError("no unsettled usage for consumer")
        total = sum(u.fee_kobo for u in usage)
        state_amt = total * STATE_SHARE_BPS // 10_000
        platform_amt = total - state_amt
        record = SettlementRecord(
            settlement_id=self._next_id("STL"),
            state_id=state_id,
            consumer_id=consumer_id,
            usage_ids=[u.usage_id for u in usage],
            total_kobo=total,
            lines=[
                SettlementLine(
                    beneficiary="STATE_CONSOLIDATED_REVENUE_FUND",
                    tigerbeetle_account_code=3001,
                    transfer_code=101,
                    amount_kobo=state_amt,
                ),
                SettlementLine(
                    beneficiary="PPP_TECH_CONCESSIONAIRE_ESCROW",
                    tigerbeetle_account_code=2099,
                    transfer_code=103,
                    amount_kobo=platform_amt,
                ),
            ],
        )
        saved = self.repo.save_settlement(record)
        self._audit("SETTLEMENT_RECORDED", state_id, consumer_id, record.settlement_id, f"total_kobo={total}")
        return saved

    # -- audit integrity -------------------------------------------------------
    def verify_audit_chain(self) -> bool:
        from .repo import InMemoryIdentityRepository

        prev = InMemoryIdentityRepository.GENESIS_HASH
        for entry in self.repo.list_audit():
            payload = f"{entry.seq}|{entry.action}|{entry.state_id}|{entry.actor_id}|{entry.subject_id}|{entry.details}"
            if entry.prev_hash != prev:
                return False
            if entry.entry_hash != AuditEntry.compute_hash(prev, payload):
                return False
            prev = entry.entry_hash
        return True
