"""Repository interface + in-memory implementation for mod-citizen-portal.

Production target is Postgres with schema-per-tenant and Row-Level Security
(every table carries ``tenant_state_id``; see db/migrations/ and
docs/architecture/06-tenancy-security.md).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Protocol

from .domain import (
    BiometricVerification,
    ChannelAuth,
    ChannelSession,
    CivilServant,
    IdentityWallet,
    PayrollAudit,
    Petition,
    PortalAuditEvent,
    ServiceCatalogEntry,
    ServiceRequest,
    SettlementRecord,
    SsoSession,
    WalletBinding,
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
    def save_channel_auth(self, auth: ChannelAuth) -> ChannelAuth: ...
    def get_channel_auth(self, state_id: str, msisdn_hash: str) -> Optional[ChannelAuth]: ...
    def get_callback_seq(self, session_id: str) -> int: ...
    def set_callback_seq(self, session_id: str, seq: int) -> None: ...
    def get_session_token_hash(self, session_id: str) -> Optional[str]: ...
    def set_session_token_hash(self, session_id: str, token_hash: str) -> None: ...
    def save_binding(self, binding: WalletBinding) -> WalletBinding: ...
    def get_binding(self, state_id: str, msisdn_hash: str) -> Optional[WalletBinding]: ...
    def list_bindings(self, wallet_id: str) -> List[WalletBinding]: ...
    def append_audit(self, entry: PortalAuditEvent) -> PortalAuditEvent: ...
    def list_audit(self, state_id: Optional[str] = None) -> List[PortalAuditEvent]: ...
    def audit_tail_hash(self) -> str: ...
    def audit_count(self) -> int: ...


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
        self._channel_auth: Dict[str, ChannelAuth] = {}  # key: state_id|msisdn_hash
        # Replay/binding state survives session deletion (fail-closed).
        self._callback_seqs: Dict[str, int] = {}
        self._session_tokens: Dict[str, str] = {}
        self._bindings: Dict[str, WalletBinding] = {}  # key: state_id|msisdn_hash
        self._audit: List[PortalAuditEvent] = []

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

    # -- channel credentials (USSD/IVR PIN) -----------------------------------
    def save_channel_auth(self, auth: ChannelAuth) -> ChannelAuth:
        self._channel_auth[f"{auth.state_id}|{auth.msisdn_hash}"] = auth
        return auth

    def get_channel_auth(self, state_id: str, msisdn_hash: str) -> Optional[ChannelAuth]:
        return self._channel_auth.get(f"{state_id}|{msisdn_hash}")

    # -- webhook replay protection (per-session monotonic sequence + token) ---
    def get_callback_seq(self, session_id: str) -> int:
        return self._callback_seqs.get(session_id, 0)

    def set_callback_seq(self, session_id: str, seq: int) -> None:
        self._callback_seqs[session_id] = seq

    def get_session_token_hash(self, session_id: str) -> Optional[str]:
        return self._session_tokens.get(session_id)

    def set_session_token_hash(self, session_id: str, token_hash: str) -> None:
        self._session_tokens[session_id] = token_hash

    # -- wallet bindings (account recovery) ------------------------------------
    def save_binding(self, binding: WalletBinding) -> WalletBinding:
        self._bindings[f"{binding.state_id}|{binding.msisdn_hash}"] = binding
        return binding

    def get_binding(self, state_id: str, msisdn_hash: str) -> Optional[WalletBinding]:
        return self._bindings.get(f"{state_id}|{msisdn_hash}")

    def list_bindings(self, wallet_id: str) -> List[WalletBinding]:
        return [b for b in self._bindings.values() if b.wallet_id == wallet_id]

    # -- hash-chained audit (append-only by construction) ----------------------
    GENESIS_HASH = "0" * 64

    def append_audit(self, entry: PortalAuditEvent) -> PortalAuditEvent:
        if self._audit and entry.seq != self._audit[-1].seq + 1:
            raise ValueError("audit chain sequence violation (append-only)")
        if entry.prev_hash != self.audit_tail_hash():
            raise ValueError("audit chain prev_hash mismatch (append-only)")
        self._audit.append(entry)
        return entry

    def list_audit(self, state_id: Optional[str] = None) -> List[PortalAuditEvent]:
        if state_id is None:
            return list(self._audit)
        return [e for e in self._audit if e.state_id == state_id]

    def audit_count(self) -> int:
        """O(1) entry count — avoids copying the chain to derive a seq."""
        return len(self._audit)

    def audit_tail_hash(self) -> str:
        return self._audit[-1].entry_hash if self._audit else self.GENESIS_HASH
