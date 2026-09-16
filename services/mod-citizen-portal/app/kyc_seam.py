"""KYC verification seam for account recovery — optional, fail-closed.

Wallet rebind (phone/NIN lost) requires a *verified* KYC case from
mod-kyc-kyb before the portal migrates a wallet binding. The client is
selected by ``SOS_PORTAL_KYC_URL``:

- unset → :class:`FixturePortalKycClient` (deterministic default for
  tests/local runs; only refs in its verified set pass, everything else is
  treated as unverified — fail-closed);
- set   → :class:`HttpPortalKycClient` against the live mod-kyc-kyb API.
  Any connection/HTTP failure raises :class:`PortalKycUnavailableError`
  (fail-closed: recovery is denied when the KYC service cannot be reached).
"""
from __future__ import annotations

import os
from typing import FrozenSet, Optional, Protocol

from .channels.base import AdapterUnavailableError

#: Deterministic fixture: the only case ref verified out of the box.
FIXTURE_VERIFIED_REFS: FrozenSet[str] = frozenset({"KYC-VERIFIED-FIXTURE"})


class PortalKycUnavailableError(AdapterUnavailableError):
    """The KYC verification service could not be reached (fail-closed)."""


class PortalKycClient(Protocol):
    def is_verified(self, kyc_case_ref: str) -> bool: ...


class FixturePortalKycClient:
    """Deterministic fixture KYC client (default when no URL configured)."""

    def __init__(self, verified_refs: Optional[FrozenSet[str]] = None) -> None:
        self.verified_refs = frozenset(
            verified_refs if verified_refs is not None else FIXTURE_VERIFIED_REFS
        )

    def is_verified(self, kyc_case_ref: str) -> bool:
        return kyc_case_ref in self.verified_refs


class HttpPortalKycClient:
    """Live client against mod-kyc-kyb; any failure is fail-closed."""

    def __init__(self, base_url: str, tenant_state_id: str = "lagos", timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.tenant_state_id = tenant_state_id
        self.timeout = timeout

    def is_verified(self, kyc_case_ref: str) -> bool:
        try:
            import httpx
        except ImportError as exc:
            raise PortalKycUnavailableError(
                "httpx not installed; live KYC verification unavailable (fail-closed)"
            ) from exc
        try:
            resp = httpx.get(
                f"{self.base_url}/kyc/v1/cases/{kyc_case_ref}",
                params={"state_id": self.tenant_state_id},
                timeout=self.timeout,
            )
        except Exception as exc:
            raise PortalKycUnavailableError(f"KYC service unreachable: {exc}") from exc
        if resp.status_code == 404:
            return False  # unknown case ref is simply unverified
        if resp.status_code != 200:
            raise PortalKycUnavailableError(
                f"KYC service returned HTTP {resp.status_code} (fail-closed)"
            )
        return resp.json().get("status") == "APPROVED"


def build_portal_kyc_client(environ: Optional[dict] = None) -> PortalKycClient:
    """Select the KYC client from ``SOS_PORTAL_KYC_URL`` (fixture default)."""
    env = environ if environ is not None else dict(os.environ)
    url = env.get("SOS_PORTAL_KYC_URL", "").strip()
    if url:
        return HttpPortalKycClient(url)
    extra = {
        ref.strip()
        for ref in env.get("SOS_PORTAL_KYC_FIXTURE_VERIFIED", "").split(",")
        if ref.strip()
    }
    return FixturePortalKycClient(FIXTURE_VERIFIED_REFS | extra)
