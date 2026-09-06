"""mod-transparency FastAPI surface (P2 Workstream E).

Public, read-only, unauthenticated, tenant-scoped transparency views over
the mod-police-cad trust fund and the mod-ppp-investment concession
escrow + procurement audit chain. Unknown tenant states return 404 with a
generic body — the API never enumerates which states exist.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status

from .domain import (
    KNOWN_STATE_IDS,
    ConcessionEscrowStatement,
    ProcurementAuditDigest,
    ProcurementAuditVerification,
    TrustFundFeed,
    verify_digest_chain,
)
from .sources import SourceUnavailableError, TransparencySource, default_source


def get_source(request: Request) -> TransparencySource:
    return request.app.state.source


def _require_known_state(state_id: str) -> None:
    if state_id not in KNOWN_STATE_IDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="resource not found")


def _errors(fn):
    try:
        return fn()
    except SourceUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=str(exc))


def create_app(source: Optional[TransparencySource] = None) -> FastAPI:
    app = FastAPI(
        title="SOS mod-transparency — Public Read-Only Transparency Views",
        version="0.1.0",
        description="Unauthenticated, tenant-scoped public projections of the "
                    "security trust fund, concession escrow statements and the "
                    "hash-chained procurement audit log. Structurally redacted: "
                    "no donor, actor or concessionaire PII; amounts in kobo; "
                    "hash-chain cursors on every feed.",
    )
    app.state.source = source or default_source()

    @app.get("/transparency/v1/{state_id}/trust-fund/feed",
             response_model=TrustFundFeed, tags=["transparency"])
    def trust_fund_feed(state_id: str, src: TransparencySource = Depends(get_source)):
        """Public trust-fund feed: donations in, disbursements out, balance,
        per-entry hash-chain cursors. Donor identities are pseudonymised."""
        _require_known_state(state_id)
        return _errors(lambda: src.trust_fund_feed(state_id))

    @app.get("/transparency/v1/{state_id}/escrow/statements",
             response_model=list[ConcessionEscrowStatement], tags=["transparency"])
    def escrow_statements(state_id: str, src: TransparencySource = Depends(get_source)):
        """Public concession escrow statements (monthly revenue-share
        settlements), contract identities pseudonymised."""
        _require_known_state(state_id)
        return _errors(lambda: src.escrow_statements(state_id))

    @app.get("/transparency/v1/{state_id}/procurement/audit",
             response_model=list[ProcurementAuditDigest], tags=["transparency"])
    def procurement_audit(state_id: str, src: TransparencySource = Depends(get_source)):
        """Redacted procurement audit digests for the state (actor/details
        removed; subject pseudonymised; hash chain preserved)."""
        _require_known_state(state_id)
        return _errors(lambda: src.procurement_audit(state_id))

    @app.get("/transparency/v1/{state_id}/procurement/audit/verify",
             response_model=ProcurementAuditVerification, tags=["transparency"])
    def procurement_audit_verify(state_id: str,
                                 src: TransparencySource = Depends(get_source)):
        """Recompute the state's procurement hash chain; reports tamper."""
        _require_known_state(state_id)
        digests = _errors(lambda: src.procurement_audit(state_id))
        valid, first_invalid = verify_digest_chain(digests)
        return ProcurementAuditVerification(
            state_id=state_id,
            chain_valid=valid,
            entries_checked=len(digests),
            head_hash=digests[-1].entry_hash if digests else "",
            first_invalid_seq=first_invalid,
        )

    @app.get("/healthz", tags=["meta"])
    def healthz():
        return {"status": "ok", "module": "mod-transparency"}

    return app


app = create_app()
