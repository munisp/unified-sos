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

#: Default in-cluster base URL of mod-land-docs (deployment config). The
#: verify path is ``POST /api/v1/states/{state_id}/land-docs/documents/
#: {document_id}/verify`` on the in-cluster service port (8000).
DEFAULT_DOCS_URL = "http://mod-land-docs:8000"

#: Path template for the mod-land-docs document verification transition.
DOCS_VERIFY_PATH = "/api/v1/states/{tenant_state_id}/land-docs/documents/{document_id}/verify"


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
    """Production adapter — verifies documents via mod-land-docs.

    Calls ``POST {base_url}/api/v1/states/{state_id}/land-docs/documents/
    {document_id}/verify`` (the mod-land-docs lifecycle transition). A 200
    response whose returned document status is VERIFIED counts as verified;
    404 counts as not found/unverified. Fail-closed: any other
    transport/dependency failure raises :class:`AdapterUnavailableError`
    instead of fabricating a verification.
    """

    name = "http"

    def __init__(
        self,
        base_url: Optional[str] = None,
        environ: Optional[Dict[str, str]] = None,
        timeout_s: float = 5.0,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        self.base_url = (base_url or env.get("SOS_LANDS_DOCS_URL")
                         or DEFAULT_DOCS_URL).rstrip("/")
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
        url = self.base_url + DOCS_VERIFY_PATH.format(
            tenant_state_id=tenant_state_id, document_id=document_id)
        try:
            resp = httpx.post(
                url,
                json={"verifier": "mod-gis-lands",
                      "reason": "conveyancing evidence verification"},
                headers={"X-State-Tenant": tenant_state_id},
                timeout=self.timeout_s,
            )
            if resp.status_code == 404:
                return DocumentVerification(
                    document_id=document_id, verified=False,
                    detail="document not found in land-docs registry",
                )
            # 409 = lifecycle transition not allowed from the current status
            # (e.g. already VERIFIED): fall back to reading the document so an
            # idempotent re-verify is not mistaken for an outage.
            if resp.status_code == 409:
                resp = httpx.get(
                    f"{self.base_url}/api/v1/states/{tenant_state_id}"
                    f"/land-docs/documents/{document_id}",
                    headers={"X-State-Tenant": tenant_state_id},
                    timeout=self.timeout_s,
                )
                if resp.status_code == 404:
                    return DocumentVerification(
                        document_id=document_id, verified=False,
                        detail="document not found in land-docs registry",
                    )
            resp.raise_for_status()
            body = resp.json() if resp.content else {}
            status_value = str(body.get("status", "")).upper()
            return DocumentVerification(
                document_id=document_id,
                verified=status_value == "VERIFIED",
                doc_type=str(body.get("doc_type", body.get("document_type", ""))),
                detail=f"land-docs status {status_value or 'unknown'}",
            )
        except AdapterUnavailableError:
            raise
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

    External-system seam: no in-cluster tax-clearance service ships with the
    platform, so there is NO default URL — an explicit ``SOS_LANDS_TAX_URL``
    (or constructor argument) is required and constructing without one
    raises :class:`AdapterUnavailableError` (fail-closed).

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
        self.base_url = base_url or env.get("SOS_LANDS_TAX_URL")
        if not self.base_url:
            raise AdapterUnavailableError(
                "SOS_LANDS_TAX_URL is required for HttpTaxClearanceAdapter "
                "(external-system seam; fail-closed: refusing to boot with "
                "an unconfigured tax-clearance endpoint)"
            )
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
