"""Fail-closed external evidence adapters for the conveyancing/legal layer.

Two seams, same idiom as :mod:`lands_app.risk`:

* **LandDocsAdapter** — verifies that an evidence document (deed, probate
  grant, death certificate, ...) exists and is verified in mod-land-docs.
  Selected via ``SOS_LANDS_DOCS_URL``; fixture default in dev.
* **TaxClearanceAdapter** — issues tax clearance certificates for transfers.
  Selected via ``SOS_LANDS_TAX_URL``; fixture default in dev.

Fail-closed: when ``SOS_LANDS_PROFILE=production`` and the corresponding URL
is unset, the ``*_from_env`` factory raises
:class:`~lands_app.risk.AdapterUnavailableError` at service boot rather than
silently falling back to the fixture. HTTP adapters raise the same error on
any transport failure instead of fabricating a verification.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Dict, Optional, Protocol

from .risk import AdapterUnavailableError

#: Default in-cluster URLs (deployment config).
DEFAULT_DOCS_URL = "http://mod-land-docs:8022/docs/v1/verify"
DEFAULT_TAX_URL = "http://mod-revenue-tax:8023/tax/v1/clearance"


@dataclass(frozen=True)
class DocumentVerification:
    """Result of a mod-land-docs evidence verification."""

    document_id: str
    verified: bool
    doc_type: str = ""
    detail: str = ""


class LandDocsAdapter(Protocol):
    """Adapter seam: verify an evidence document id against mod-land-docs."""

    def verify_document(
        self, *, tenant_state_id: str, document_id: str
    ) -> DocumentVerification: ...


class FixtureLandDocsAdapter:
    """Deterministic fixture docs adapter (default; local dev and tests).

    Any document id starting with ``doc-`` verifies; anything else fails.
    Tests may pre-register richer fixtures via :meth:`register_document`.
    """

    name = "fixture"

    def __init__(self) -> None:
        self._docs: Dict[str, str] = {}

    def register_document(self, document_id: str, doc_type: str = "DEED") -> None:
        self._docs[document_id] = doc_type

    def verify_document(
        self, *, tenant_state_id: str, document_id: str
    ) -> DocumentVerification:
        if document_id in self._docs:
            return DocumentVerification(
                document_id=document_id, verified=True,
                doc_type=self._docs[document_id], detail="fixture document verified",
            )
        if document_id.startswith("doc-"):
            digest = hashlib.sha256(
                f"{tenant_state_id}|{document_id}".encode("utf-8")
            ).hexdigest()[:12]
            return DocumentVerification(
                document_id=document_id, verified=True, doc_type="DEED",
                detail=f"fixture verification {digest}",
            )
        return DocumentVerification(
            document_id=document_id, verified=False,
            detail="document not found or unverified in land-docs registry",
        )


class HttpLandDocsAdapter:
    """Production adapter — GETs document verification from mod-land-docs.

    Fail-closed: transport/dependency failure raises
    :class:`AdapterUnavailableError` instead of fabricating a verification.
    """

    name = "http"

    def __init__(
        self,
        base_url: Optional[str] = None,
        environ: Optional[Dict[str, str]] = None,
        timeout_s: float = 5.0,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        self.base_url = base_url or env.get("SOS_LANDS_DOCS_URL") or DEFAULT_DOCS_URL
        self.timeout_s = timeout_s

    def verify_document(
        self, *, tenant_state_id: str, document_id: str
    ) -> DocumentVerification:  # pragma: no cover - requires live docs service
        try:
            import httpx
        except ImportError as exc:
            raise AdapterUnavailableError(
                "httpx is required for HttpLandDocsAdapter"
            ) from exc
        try:
            resp = httpx.post(
                self.base_url,
                json={"tenant_state_id": tenant_state_id, "document_id": document_id},
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            body = resp.json()
            return DocumentVerification(
                document_id=document_id,
                verified=bool(body["verified"]),
                doc_type=str(body.get("doc_type", "")),
                detail=str(body.get("detail", "")),
            )
        except Exception as exc:
            raise AdapterUnavailableError(
                f"land-docs adapter unavailable at {self.base_url}: {exc} "
                "(fail-closed: refusing to fabricate a document verification)"
            ) from exc


def docs_adapter_from_env(environ: Optional[Dict[str, str]] = None) -> LandDocsAdapter:
    """Build the land-docs adapter from environment (fixture default; fail-closed prod)."""
    env = environ if environ is not None else dict(os.environ)
    profile = env.get("SOS_LANDS_PROFILE", "dev")
    url = env.get("SOS_LANDS_DOCS_URL")
    if profile == "production":
        if not url:
            raise AdapterUnavailableError(
                "SOS_LANDS_DOCS_URL is required when SOS_LANDS_PROFILE=production "
                "(fail-closed: refusing to boot with the fixture docs adapter)"
            )
        return HttpLandDocsAdapter(url)
    if url:
        return HttpLandDocsAdapter(url)
    return FixtureLandDocsAdapter()


# ---------------------------------------------------------------------------
# Tax clearance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaxClearance:
    """Tax clearance certificate issued for a transfer."""

    clearance_id: str
    tenant_state_id: str
    parcel_id: str
    transfer_id: str
    detail: str = ""


class TaxClearanceAdapter(Protocol):
    """Adapter seam: issue a tax clearance certificate for a transfer."""

    def clear(
        self, *, tenant_state_id: str, parcel_id: str, transfer_id: str
    ) -> TaxClearance: ...


class FixtureTaxClearanceAdapter:
    """Deterministic fixture tax adapter (default; local dev and tests)."""

    name = "fixture"

    def clear(
        self, *, tenant_state_id: str, parcel_id: str, transfer_id: str
    ) -> TaxClearance:
        digest = hashlib.sha256(
            f"{tenant_state_id}|{parcel_id}|{transfer_id}".encode("utf-8")
        ).hexdigest()[:12]
        return TaxClearance(
            clearance_id=f"TCC-{tenant_state_id.upper()}-{digest}",
            tenant_state_id=tenant_state_id,
            parcel_id=parcel_id,
            transfer_id=transfer_id,
            detail="fixture tax clearance issued",
        )


class HttpTaxClearanceAdapter:
    """Production adapter — POSTs to the state tax clearance endpoint.

    Fail-closed: any failure raises :class:`AdapterUnavailableError` rather
    than registering a transfer without tax clearance.
    """

    name = "http"

    def __init__(
        self,
        base_url: Optional[str] = None,
        environ: Optional[Dict[str, str]] = None,
        timeout_s: float = 5.0,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        self.base_url = base_url or env.get("SOS_LANDS_TAX_URL") or DEFAULT_TAX_URL
        self.timeout_s = timeout_s

    def clear(
        self, *, tenant_state_id: str, parcel_id: str, transfer_id: str
    ) -> TaxClearance:  # pragma: no cover - requires live tax service
        try:
            import httpx
        except ImportError as exc:
            raise AdapterUnavailableError(
                "httpx is required for HttpTaxClearanceAdapter"
            ) from exc
        try:
            resp = httpx.post(
                self.base_url,
                json={
                    "tenant_state_id": tenant_state_id,
                    "parcel_id": parcel_id,
                    "transfer_id": transfer_id,
                },
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            body = resp.json()
            return TaxClearance(
                clearance_id=str(body["clearance_id"]),
                tenant_state_id=tenant_state_id,
                parcel_id=parcel_id,
                transfer_id=transfer_id,
                detail=str(body.get("detail", "")),
            )
        except Exception as exc:
            raise AdapterUnavailableError(
                f"tax clearance adapter unavailable at {self.base_url}: {exc} "
                "(fail-closed: refusing to fabricate a tax clearance)"
            ) from exc


def tax_adapter_from_env(environ: Optional[Dict[str, str]] = None) -> TaxClearanceAdapter:
    """Build the tax clearance adapter from environment (fixture default; fail-closed prod)."""
    env = environ if environ is not None else dict(os.environ)
    profile = env.get("SOS_LANDS_PROFILE", "dev")
    url = env.get("SOS_LANDS_TAX_URL")
    if profile == "production":
        if not url:
            raise AdapterUnavailableError(
                "SOS_LANDS_TAX_URL is required when SOS_LANDS_PROFILE=production "
                "(fail-closed: refusing to boot with the fixture tax adapter)"
            )
        return HttpTaxClearanceAdapter(url)
    if url:
        return HttpTaxClearanceAdapter(url)
    return FixtureTaxClearanceAdapter()
