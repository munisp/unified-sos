"""Authorization gate for biometric (face-recognition) processing.

Legal basis: the **Nigeria Data Protection Act 2023 (NDPA)** classifies
biometric data as *sensitive personal data* — its processing requires an
explicit lawful basis (NDPA 2023 §§ 25, 30; consent alone is insufficient
for state surveillance). Face-recognition enroll/match endpoints therefore
run only against a **certified authorization record** (a judicial warrant
reference or a Data Protection Officer approval reference, with an expiry
timestamp) loaded from a certified source — **never from tenant config**.

Design mirrors the ``RatificationGate`` philosophy of mod-police-cad:

- default CLOSED (fail-closed): no authorization source configured, record
  missing, or record expired → :class:`GatedError`;
- the API layer maps :class:`GatedError` to **HTTP 423 Locked** with the
  legal-basis message;
- crowd-monitoring and anomaly-detection endpoints are NOT gated (no
  biometric processing).

Production wiring: ``SOS_VISION_AUTHORIZATIONS_FILE`` points at a JSON file
provisioned by the governance control plane from the certified warrant/DPO
registry. The file is a list of records::

    [{"ref": "WRT-LAG-2026-0142", "kind": "warrant",
      "tenant_state_id": "lagos", "expires_at": "2027-01-01T00:00:00+00:00"}]
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

#: Legal-basis message returned with HTTP 423 while the gate is closed.
LEGAL_BASIS_MESSAGE = (
    "Face-recognition biometric processing is gated: under the Nigeria Data "
    "Protection Act 2023 (NDPA) biometric data is sensitive personal data and "
    "requires an explicit lawful basis (NDPA 2023 ss. 25, 30). A certified "
    "authorization record — judicial warrant reference or Data Protection "
    "Officer approval reference with a valid expiry — must be loaded from the "
    "certified authorization source before enroll/match endpoints operate. "
    "Crowd-monitoring and anomaly-detection endpoints remain available."
)


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


@dataclass(frozen=True)
class AuthorizationRecord:
    """One certified authorization for biometric processing in a tenant."""

    ref: str  # warrant ref or DPO approval ref
    kind: str  # "warrant" | "dpo_approval"
    tenant_state_id: str
    expires_at: str  # ISO-8601

    def valid(self, tenant_state_id: str, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return (
            self.tenant_state_id == tenant_state_id
            and self.kind in {"warrant", "dpo_approval"}
            and _parse_ts(self.expires_at) > now
        )


class GatedError(Exception):
    """Raised when a biometric-gated capability is invoked without authority."""

    def __init__(self, legal_basis: str) -> None:
        self.legal_basis = legal_basis
        super().__init__(legal_basis)


@dataclass
class AuthorizationGate:
    """Gate over face-recognition enroll/match, keyed by tenant.

    Records come from a certified source (JSON file provisioned out-of-band);
    an absent/invalid source means the gate is closed for every tenant.
    """

    records: tuple[AuthorizationRecord, ...] = ()
    legal_basis: str = LEGAL_BASIS_MESSAGE

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "AuthorizationGate":
        env = environ if environ is not None else dict(os.environ)
        path = env.get("SOS_VISION_AUTHORIZATIONS_FILE")
        if not path:
            return cls()  # fail-closed: no certified source configured
        p = Path(path)
        if not p.exists():
            return cls()  # fail-closed: certified source missing
        raw = json.loads(p.read_text())
        records = tuple(
            AuthorizationRecord(
                ref=r["ref"],
                kind=r["kind"],
                tenant_state_id=r["tenant_state_id"],
                expires_at=r["expires_at"],
            )
            for r in raw
        )
        return cls(records=records)

    def authorized(self, tenant_state_id: str, now: datetime | None = None) -> bool:
        return any(r.valid(tenant_state_id, now) for r in self.records)

    def check(self, tenant_state_id: str) -> None:
        """Raise :class:`GatedError` when the tenant lacks a valid record."""
        if not self.authorized(tenant_state_id):
            raise GatedError(self.legal_basis)
