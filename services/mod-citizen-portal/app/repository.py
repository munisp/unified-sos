"""Repository interface + in-memory implementation for mod-citizen-portal.

Production target is Postgres with schema-per-tenant and Row-Level Security
(every table carries ``tenant_state_id``; see db/migrations/ and
docs/architecture/06-tenancy-security.md).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Protocol

from .domain import (
    BiometricVerification,
    ChannelSession,
    CivilServant,
    IdentityWallet,
    PayrollAudit,
    Petition,
    ServiceCatalogEntry,
    ServiceRequest,
    SettlementRecord,
    SsoSession,
)


class CitizenPortalRepository(Protocol):
    def save_wallet(self, wallet: IdentityWallet) -> IdentityWallet: ...
    def get_wallet(self, wallet_id: str) -> Optional[IdentityWallet]: ...
    def save_sso_session(self, session: SsoSession) -> SsoSession: ...
    def get_sso_session(self, session_id: str) -> Optional[SsoSession]: ...
    def save_catalog_entry(self, entry: ServiceCatalogEntry) -> ServiceCatalogEntry: ...
    def list_catalog(self, state_id: str) -> List[ServiceCatalogEntry]: ...
    def get_catalog_entry(self, state_id: str, service_code: str) -> Optional[ServiceCatalogEntry]: ...
    def save_service_request(self, request: ServiceRequest) -> ServiceRequest: ...
    def get_service_request(self, request_id: str) -> Optional[ServiceRequest]: ...
    def save_petition(self, petition: Petition) -> Petition: ...
    def get_petition(self, petition_id: str) -> Optional[Petition]: ...
    def save_civil_servant(self, servant: CivilServant) -> CivilServant: ...
    def get_civil_servant(self, state_id: str, employee_no: str) -> Optional[CivilServant]: ...
    def list_civil_servants(self, state_id: str) -> List[CivilServant]: ...
    def save_biometric_verification(self, verification: BiometricVerification) -> BiometricVerification: ...
    def list_verifications(self, state_id: str, employee_no: str) -> List[BiometricVerification]: ...
    def save_payroll_audit(self, audit: PayrollAudit) -> PayrollAudit: ...
    def get_payroll_audit(self, audit_id: str) -> Optional[PayrollAudit]: ...
    def save_settlement(self, settlement: SettlementRecord) -> SettlementRecord: ...
    def list_settlements(self, state_id: str) -> List[SettlementRecord]: ...
    def save_channel_session(self, session: ChannelSession) -> ChannelSession: ...
    def get_channel_session(self, session_id: str) -> Optional[ChannelSession]: ...
    def delete_channel_session(self, session_id: str) -> None: ...


class InMemoryCitizenPortalRepository:
    def __init__(self) -> None:
        self._wallets: Dict[str, IdentityWallet] = {}
        self._sso: Dict[str, SsoSession] = {}
        self._catalog: Dict[str, ServiceCatalogEntry] = {}  # key: state_id|code
        self._requests: Dict[str, ServiceRequest] = {}
        self._petitions: Dict[str, Petition] = {}
        self._servants: Dict[str, CivilServant] = {}  # key: state_id|employee_no
        self._verifications: List[BiometricVerification] = []
        self._audits: Dict[str, PayrollAudit] = {}
        self._settlements: List[SettlementRecord] = []
        self._channel_sessions: Dict[str, ChannelSession] = {}

    def save_wallet(self, wallet: IdentityWallet) -> IdentityWallet:
        self._wallets[wallet.wallet_id] = wallet
        return wallet

    def get_wallet(self, wallet_id: str) -> Optional[IdentityWallet]:
        return self._wallets.get(wallet_id)

    def save_sso_session(self, session: SsoSession) -> SsoSession:
        self._sso[session.session_id] = session
        return session

    def get_sso_session(self, session_id: str) -> Optional[SsoSession]:
        return self._sso.get(session_id)

    def save_catalog_entry(self, entry: ServiceCatalogEntry) -> ServiceCatalogEntry:
        self._catalog[f"{entry.state_id}|{entry.service_code}"] = entry
        return entry

    def list_catalog(self, state_id: str) -> List[ServiceCatalogEntry]:
        return [e for k, e in self._catalog.items() if k.startswith(f"{state_id}|")]

    def get_catalog_entry(self, state_id: str, service_code: str) -> Optional[ServiceCatalogEntry]:
        return self._catalog.get(f"{state_id}|{service_code}")

    def save_service_request(self, request: ServiceRequest) -> ServiceRequest:
        self._requests[request.request_id] = request
        return request

    def get_service_request(self, request_id: str) -> Optional[ServiceRequest]:
        return self._requests.get(request_id)

    def save_petition(self, petition: Petition) -> Petition:
        self._petitions[petition.petition_id] = petition
        return petition

    def get_petition(self, petition_id: str) -> Optional[Petition]:
        return self._petitions.get(petition_id)

    def save_civil_servant(self, servant: CivilServant) -> CivilServant:
        self._servants[f"{servant.state_id}|{servant.employee_no}"] = servant
        return servant

    def get_civil_servant(self, state_id: str, employee_no: str) -> Optional[CivilServant]:
        return self._servants.get(f"{state_id}|{employee_no}")

    def list_civil_servants(self, state_id: str) -> List[CivilServant]:
        return [s for k, s in self._servants.items() if k.startswith(f"{state_id}|")]

    def save_biometric_verification(self, verification: BiometricVerification) -> BiometricVerification:
        self._verifications.append(verification)
        return verification

    def list_verifications(self, state_id: str, employee_no: str) -> List[BiometricVerification]:
        return [
            v
            for v in self._verifications
            if v.state_id == state_id and v.employee_no == employee_no
        ]

    def save_payroll_audit(self, audit: PayrollAudit) -> PayrollAudit:
        self._audits[audit.audit_id] = audit
        return audit

    def get_payroll_audit(self, audit_id: str) -> Optional[PayrollAudit]:
        return self._audits.get(audit_id)

    def save_settlement(self, settlement: SettlementRecord) -> SettlementRecord:
        self._settlements.append(settlement)
        return settlement

    def list_settlements(self, state_id: str) -> List[SettlementRecord]:
        return [s for s in self._settlements if s.state_id == state_id]

    def save_channel_session(self, session: ChannelSession) -> ChannelSession:
        self._channel_sessions[session.session_id] = session
        return session

    def get_channel_session(self, session_id: str) -> Optional[ChannelSession]:
        return self._channel_sessions.get(session_id)

    def delete_channel_session(self, session_id: str) -> None:
        self._channel_sessions.pop(session_id, None)
