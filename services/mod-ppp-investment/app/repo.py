"""Repository interface + in-memory implementation for mod-ppp-investment.

Production target: Postgres schema-per-tenant with RLS (`tenant_state_id` on
all tables, db/migrations/); the audit store is append-only by construction
(no update/delete on the interface).
"""
from __future__ import annotations

import hashlib
from typing import Dict, List, Optional, Protocol

from .models import (
    AuditEntry,
    ConcessionContract,
    DocumentSet,
    Evaluation,
    KPIRecord,
    Project,
    Proposal,
    SettlementStatement,
)


class PPPRepository(Protocol):
    def save_project(self, project: Project) -> Project: ...
    def get_project(self, project_id: str) -> Optional[Project]: ...
    def list_projects(self, state_id: Optional[str] = None) -> List[Project]: ...
    def save_proposal(self, proposal: Proposal) -> Proposal: ...
    def get_proposal(self, proposal_id: str) -> Optional[Proposal]: ...
    def list_proposals(self, state_id: Optional[str] = None, project_id: Optional[str] = None) -> List[Proposal]: ...
    def save_evaluation(self, evaluation: Evaluation) -> Evaluation: ...
    def get_evaluation(self, proposal_id: str) -> Optional[Evaluation]: ...
    def save_documentset(self, ds: DocumentSet) -> DocumentSet: ...
    def get_documentset(self, documentset_id: str) -> Optional[DocumentSet]: ...
    def save_contract(self, contract: ConcessionContract) -> ConcessionContract: ...
    def get_contract(self, contract_id: str) -> Optional[ConcessionContract]: ...
    def save_kpi(self, kpi: KPIRecord) -> KPIRecord: ...
    def list_kpis(self, contract_id: str) -> List[KPIRecord]: ...
    def save_statement(self, st: SettlementStatement) -> SettlementStatement: ...
    def list_statements(self, contract_id: str) -> List[SettlementStatement]: ...
    def append_audit(self, entry: AuditEntry) -> AuditEntry: ...
    def list_audit(self, state_id: Optional[str] = None) -> List[AuditEntry]: ...
    def audit_tail_hash(self) -> str: ...


class InMemoryPPPRepository:
    GENESIS_HASH = hashlib.sha256(b"mod-ppp-investment-audit-genesis").hexdigest()

    def __init__(self) -> None:
        self._projects: Dict[str, Project] = {}
        self._proposals: Dict[str, Proposal] = {}
        self._evaluations: Dict[str, Evaluation] = {}
        self._documentsets: Dict[str, DocumentSet] = {}
        self._contracts: Dict[str, ConcessionContract] = {}
        self._kpis: Dict[str, KPIRecord] = {}
        self._statements: Dict[str, SettlementStatement] = {}
        self._audit: List[AuditEntry] = []

    def save_project(self, p): self._projects[p.project_id] = p; return p
    def get_project(self, pid): return self._projects.get(pid)
    def list_projects(self, state_id=None):
        items = list(self._projects.values())
        return [p for p in items if p.state_id == state_id] if state_id else items

    def save_proposal(self, p): self._proposals[p.proposal_id] = p; return p
    def get_proposal(self, pid): return self._proposals.get(pid)
    def list_proposals(self, state_id=None, project_id=None):
        items = list(self._proposals.values())
        if state_id: items = [p for p in items if p.state_id == state_id]
        if project_id: items = [p for p in items if p.project_id == project_id]
        return items

    def save_evaluation(self, e): self._evaluations[e.proposal_id] = e; return e
    def get_evaluation(self, proposal_id): return self._evaluations.get(proposal_id)

    def save_documentset(self, ds): self._documentsets[ds.documentset_id] = ds; return ds
    def get_documentset(self, dsid): return self._documentsets.get(dsid)

    def save_contract(self, c): self._contracts[c.contract_id] = c; return c
    def get_contract(self, cid): return self._contracts.get(cid)

    def save_kpi(self, k): self._kpis[k.kpi_id] = k; return k
    def list_kpis(self, contract_id):
        return [k for k in self._kpis.values() if k.contract_id == contract_id]

    def save_statement(self, st): self._statements[st.statement_id] = st; return st
    def list_statements(self, contract_id):
        return [s for s in self._statements.values() if s.contract_id == contract_id]

    def append_audit(self, entry: AuditEntry) -> AuditEntry:
        if self._audit and entry.seq != self._audit[-1].seq + 1:
            raise ValueError("audit chain sequence violation (append-only)")
        if entry.prev_hash != self.audit_tail_hash():
            raise ValueError("audit chain prev_hash mismatch (append-only)")
        self._audit.append(entry)
        return entry

    def list_audit(self, state_id=None):
        if state_id is None: return list(self._audit)
        return [e for e in self._audit if e.state_id == state_id]

    def audit_tail_hash(self):
        return self._audit[-1].entry_hash if self._audit else self.GENESIS_HASH
