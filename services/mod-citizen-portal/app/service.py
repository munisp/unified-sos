"""Service layer for mod-citizen-portal (CIT-11).

Tenant isolation rule: every read/advance takes an explicit ``state_id``
and raises :class:`TenantIsolationError` when the target record belongs to a
different tenant. Privacy rule: raw NINs and raw biometrics never reach the
repository — only hashes.
"""
from __future__ import annotations

import itertools
from datetime import datetime
from typing import Dict, List, Optional

from .domain import (
    DEFAULT_SPLIT_BPS,
    DEFAULT_SSO_SESSION_TTL,
    BiometricVerification,
    CivilServant,
    GhostRule,
    GhostWorkerFinding,
    IdentityWallet,
    PayrollAudit,
    Petition,
    PetitionStatus,
    Priority,
    RequestStatus,
    ServiceCatalogEntry,
    ServiceCategory,
    ServiceRequest,
    ServantStatus,
    SettlementLine,
    SettlementRecord,
    SsoSession,
    SsoSessionStatus,
    StatusEvent,
    TemporalWorkflowRef,
    hash_nin,
    utcnow,
)
from .repository import CitizenPortalRepository


class NotFoundError(Exception):
    pass


class TenantIsolationError(Exception):
    """Cross-tenant access attempt."""


class InvalidTransitionError(Exception):
    """Illegal status transition."""


class WalletSuspendedError(Exception):
    pass


# Allowed service-request workflow transitions.
REQUEST_TRANSITIONS = {
    RequestStatus.SUBMITTED: {RequestStatus.IN_REVIEW},
    RequestStatus.IN_REVIEW: {RequestStatus.APPROVED, RequestStatus.REJECTED},
    RequestStatus.APPROVED: {RequestStatus.COMPLETED},
    RequestStatus.REJECTED: set(),
    RequestStatus.COMPLETED: set(),
}

PETITION_TRANSITIONS = {
    PetitionStatus.SUBMITTED: {PetitionStatus.UNDER_REVIEW},
    PetitionStatus.UNDER_REVIEW: {PetitionStatus.RESOLVED, PetitionStatus.REJECTED},
    PetitionStatus.RESOLVED: set(),
    PetitionStatus.REJECTED: set(),
}

# Seed catalog [DERIVED] — one entry per mandatory category; states extend
# via seed data / config packs in production.
DEFAULT_CATALOG = [
    ("REV-TAX-ID", "Tax ID & PAYE registration", ServiceCategory.REVENUE, "Board of Internal Revenue", 0, 50_000),
    ("LAND-COFO", "Certificate of Occupancy", ServiceCategory.LANDS, "Lands Bureau", 500_000, 1_500_000),
    ("HLT-PHC-REG", "Primary healthcare registration", ServiceCategory.HEALTH, "Ministry of Health", 0, 20_000),
    ("EDU-SCH-TRANS", "School transfer & records", ServiceCategory.EDUCATION, "Ministry of Education", 10_000, 50_000),
    ("MKT-STALL", "Market stall allocation", ServiceCategory.MARKET, "Market Development Authority", 100_000, 250_000),
]

SMARTCARD_FEE_KOBO = 150_000  # ₦1,500 smartcard issuance [DERIVED]


class CitizenPortalService:
    def __init__(
        self,
        repo: CitizenPortalRepository,
        state_splits: Optional[Dict[str, Dict[str, int]]] = None,
    ) -> None:
        self.repo = repo
        self._ids = itertools.count(1)
        # Per-state settlement split overrides (bps); default 70/15/15.
        self._state_splits = state_splits or {}

    def _next_id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._ids):06d}"

    @staticmethod
    def _require_tenant(record_state: str, state_id: str, what: str) -> None:
        if record_state != state_id:
            raise TenantIsolationError(f"{what} belongs to tenant {record_state!r}, not {state_id!r}")

    # -- settlement -------------------------------------------------------
    def _split(self, state_id: str) -> Dict[str, int]:
        return self._state_splits.get(state_id, DEFAULT_SPLIT_BPS)

    def _settle(self, state_id: str, source: str, reference_id: str, gross_kobo: int) -> SettlementRecord:
        split = self._split(state_id)
        lines: List[SettlementLine] = []
        allocated = 0
        items = list(split.items())
        for i, (payee, bps) in enumerate(items):
            if i < len(items) - 1:
                amount = gross_kobo * bps // 10_000
                allocated += amount
            else:  # final line takes the rounding remainder
                amount = gross_kobo - allocated
            lines.append(SettlementLine(payee=payee, share_bps=bps, amount_kobo=amount))
        record = SettlementRecord(
            settlement_id=self._next_id("STL"),
            state_id=state_id,
            source=source,
            reference_id=reference_id,
            gross_kobo=gross_kobo,
            lines=lines,
        )
        return self.repo.save_settlement(record)

    # -- wallets / SSO -----------------------------------------------------
    def create_wallet(self, state_id: str, nin: str) -> IdentityWallet:
        """Hash the NIN immediately; the raw value never reaches the repo."""
        wallet = IdentityWallet(
            wallet_id=self._next_id("WLT"),
            state_id=state_id,
            nin_hash=hash_nin(nin),
            keycloak_realm=f"sos-{state_id}",
        )
        self.repo.save_wallet(wallet)
        self._settle(state_id, "SMARTCARD_FEE", wallet.wallet_id, SMARTCARD_FEE_KOBO)
        return wallet

    def get_wallet(self, wallet_id: str, state_id: str) -> IdentityWallet:
        wallet = self.repo.get_wallet(wallet_id)
        if wallet is None:
            raise NotFoundError(f"wallet {wallet_id!r} not found")
        self._require_tenant(wallet.state_id, state_id, "wallet")
        return wallet

    def create_sso_session(self, state_id: str, wallet_id: str, redirect_uri: str, scopes: Optional[List[str]] = None) -> SsoSession:
        wallet = self.get_wallet(wallet_id, state_id)
        from .domain import WalletStatus

        if wallet.status is not WalletStatus.ACTIVE:
            raise WalletSuspendedError(f"wallet {wallet_id!r} is {wallet.status.value}")
        session = SsoSession(
            session_id=self._next_id("SSO"),
            state_id=state_id,
            wallet_id=wallet_id,
            realm=wallet.keycloak_realm,
            client_id=wallet.keycloak_client_id,
            redirect_uri=redirect_uri,
            scopes=scopes or ["openid", "profile"],
            expires_at=utcnow() + DEFAULT_SSO_SESSION_TTL,
        )
        return self.repo.save_sso_session(session)

    # -- service catalog / requests ----------------------------------------
    def ensure_catalog(self, state_id: str) -> List[ServiceCatalogEntry]:
        """Seed the default catalog on first access (configurable by seed data)."""
        existing = self.repo.list_catalog(state_id)
        if existing:
            return existing
        for code, name, category, mda, base, expedited in DEFAULT_CATALOG:
            self.repo.save_catalog_entry(
                ServiceCatalogEntry(
                    service_code=code,
                    state_id=state_id,
                    name=name,
                    category=category,
                    mda=mda,
                    base_fee_kobo=base,
                    expedited_fee_kobo=expedited,
                )
            )
        return self.repo.list_catalog(state_id)

    def submit_service_request(
        self,
        state_id: str,
        wallet_id: str,
        service_code: str,
        form_payload: Dict[str, str],
        priority: Priority,
    ) -> ServiceRequest:
        self.get_wallet(wallet_id, state_id)
        self.ensure_catalog(state_id)
        entry = self.repo.get_catalog_entry(state_id, service_code)
        if entry is None or not entry.active:
            raise NotFoundError(f"service {service_code!r} not offered in state {state_id!r}")
        fee = entry.expedited_fee_kobo if priority is Priority.EXPEDITED else entry.base_fee_kobo
        request = ServiceRequest(
            request_id=self._next_id("REQ"),
            state_id=state_id,
            wallet_id=wallet_id,
            service_code=service_code,
            form_payload=form_payload,
            priority=priority,
            fee_kobo=fee,
            timeline=[StatusEvent(status=RequestStatus.SUBMITTED.value, note="request submitted")],
        )
        self.repo.save_service_request(request)
        if priority is Priority.EXPEDITED and fee > 0:
            self._settle(state_id, "EXPEDITED_FEE", request.request_id, fee)
        return request

    def get_service_request(self, request_id: str, state_id: str) -> ServiceRequest:
        request = self.repo.get_service_request(request_id)
        if request is None:
            raise NotFoundError(f"service request {request_id!r} not found")
        self._require_tenant(request.state_id, state_id, "service request")
        return request

    def advance_service_request(self, request_id: str, state_id: str, to_status: RequestStatus, note: str = "") -> ServiceRequest:
        request = self.get_service_request(request_id, state_id)
        allowed = REQUEST_TRANSITIONS[request.status]
        if to_status not in allowed:
            raise InvalidTransitionError(f"cannot move service request {request.status.value} -> {to_status.value}")
        request.status = to_status
        request.timeline.append(StatusEvent(status=to_status.value, note=note))
        return self.repo.save_service_request(request)

    # -- petitions -----------------------------------------------------------
    def submit_petition(self, state_id: str, wallet_id: str, title: str, body: str) -> Petition:
        self.get_wallet(wallet_id, state_id)
        petition = Petition(
            petition_id=self._next_id("PET"),
            reference_id=f"PET-{state_id.upper()}-{next(self._ids):06d}",
            state_id=state_id,
            wallet_id=wallet_id,
            title=title,
            body=body,
            timeline=[StatusEvent(status=PetitionStatus.SUBMITTED.value, note="petition received")],
        )
        return self.repo.save_petition(petition)

    def advance_petition(self, petition_id: str, state_id: str, to_status: PetitionStatus, note: str = "") -> Petition:
        petition = self.repo.get_petition(petition_id)
        if petition is None:
            raise NotFoundError(f"petition {petition_id!r} not found")
        self._require_tenant(petition.state_id, state_id, "petition")
        allowed = PETITION_TRANSITIONS[petition.status]
        if to_status not in allowed:
            raise InvalidTransitionError(f"cannot move petition {petition.status.value} -> {to_status.value}")
        petition.status = to_status
        petition.timeline.append(StatusEvent(status=to_status.value, note=note))
        return self.repo.save_petition(petition)

    # -- civil-service clean-up ----------------------------------------------
    def register_civil_servant(self, servant: CivilServant) -> CivilServant:
        return self.repo.save_civil_servant(servant)

    def record_biometric_verification(self, state_id: str, employee_no: str, liveness_passed: bool, verified: bool) -> BiometricVerification:
        servant = self.repo.get_civil_servant(state_id, employee_no)
        if servant is None:
            raise NotFoundError(f"civil servant {employee_no!r} not found in state {state_id!r}")
        verification = BiometricVerification(
            verification_id=self._next_id("BVR"),
            state_id=state_id,
            employee_no=employee_no,
            liveness_passed=liveness_passed,
            verified=verified and liveness_passed,
        )
        return self.repo.save_biometric_verification(verification)

    def _is_verified(self, state_id: str, employee_no: str) -> bool:
        return any(v.verified and v.liveness_passed for v in self.repo.list_verifications(state_id, employee_no))

    def run_payroll_audit(self, state_id: str) -> PayrollAudit:
        """Deterministic ghost-worker detection over the state's payroll.

        Rules (recoverable = monthly salary_kobo of the flagged records):
        1. ACTIVE staff with no successful biometric verification.
        2. Duplicate biometric template hashes among ACTIVE staff (all but
           the alphabetically-first employee_no are ghosts).
        3. Duplicate salary-account hashes among ACTIVE staff (same rule).
        4. INACTIVE / RETIRED staff still on the payroll.
        """
        servants = self.repo.list_civil_servants(state_id)
        active = sorted((s for s in servants if s.status is ServantStatus.ACTIVE), key=lambda s: s.employee_no)
        still_paid = sorted(
            (s for s in servants if s.status in (ServantStatus.INACTIVE, ServantStatus.RETIRED)),
            key=lambda s: s.employee_no,
        )
        findings: List[GhostWorkerFinding] = []

        unverified = [s for s in active if not self._is_verified(state_id, s.employee_no)]
        if unverified:
            findings.append(
                GhostWorkerFinding(
                    finding_id=self._next_id("GWF"),
                    state_id=state_id,
                    rule=GhostRule.UNVERIFIED_BIOMETRIC,
                    employee_nos=[s.employee_no for s in unverified],
                    recoverable_kobo=sum(s.salary_kobo for s in unverified),
                    detail="active staff with no successful biometric verification",
                )
            )

        def duplicate_groups(key):
            groups: Dict[str, List[CivilServant]] = {}
            for s in active:
                groups.setdefault(key(s), []).append(s)
            return [g for g in groups.values() if len(g) > 1]

        for rule, key_fn in (
            (GhostRule.DUPLICATE_BIOMETRIC, lambda s: s.biometric_template_hash),
            (GhostRule.DUPLICATE_SALARY_ACCOUNT, lambda s: s.bank_account_hash),
        ):
            for group in duplicate_groups(key_fn):
                ghosts = group[1:]  # keep the alphabetically-first, flag the rest
                findings.append(
                    GhostWorkerFinding(
                        finding_id=self._next_id("GWF"),
                        state_id=state_id,
                        rule=rule,
                        employee_nos=[s.employee_no for s in group],
                        recoverable_kobo=sum(s.salary_kobo for s in ghosts),
                        detail=f"{len(ghosts)} duplicate record(s) sharing the same hash",
                    )
                )

        if still_paid:
            findings.append(
                GhostWorkerFinding(
                    finding_id=self._next_id("GWF"),
                    state_id=state_id,
                    rule=GhostRule.INACTIVE_STILL_PAID,
                    employee_nos=[s.employee_no for s in still_paid],
                    recoverable_kobo=sum(s.salary_kobo for s in still_paid),
                    detail="inactive/retired staff still drawing salary",
                )
            )

        findings.sort(key=lambda f: (f.rule.value, f.finding_id))
        audit = PayrollAudit(
            audit_id=self._next_id("AUD"),
            state_id=state_id,
            findings=findings,
            total_recoverable_kobo=sum(f.recoverable_kobo for f in findings),
            temporal_workflow=TemporalWorkflowRef(workflow_id=self._next_id("TMP")),
        )
        return self.repo.save_payroll_audit(audit)

    def get_payroll_audit(self, audit_id: str, state_id: str) -> PayrollAudit:
        audit = self.repo.get_payroll_audit(audit_id)
        if audit is None:
            raise NotFoundError(f"payroll audit {audit_id!r} not found")
        self._require_tenant(audit.state_id, state_id, "payroll audit")
        return audit
