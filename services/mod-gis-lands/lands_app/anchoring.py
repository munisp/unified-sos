"""Title-hash anchoring — blockchain-style immutable anchor seam.

Every issued/updated title payload can be *anchored*: the anchor stores
``sha256(canonical_payload)`` chained to the previous anchor's hash
(``sha256(payload_hash ‖ prev_anchor_hash)``), forming an append-only
merkle chain whose head is the published merkle root. Any later mutation of
the title payload — or tampering with the anchor chain itself — is detected
by recomputation (``verify``).

Adapter selection (fail-closed idiom, mirroring mod-safecity-vision)::

    SOS_LANDS_PROFILE=dev|production      (default: dev)
    SOS_LANDS_ANCHOR_URL=<anchor service>  (required in production)

``FixtureAnchor`` (in-memory) is the default in dev/tests; in production the
missing ``SOS_LANDS_ANCHOR_URL`` raises :class:`AdapterUnavailableError` at
boot rather than silently anchoring into volatile memory.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Protocol

from .risk import AdapterUnavailableError

#: Default in-cluster URL of the anchoring service (deployment config).
DEFAULT_ANCHOR_URL = "http://mod-anchor-notary:8031/v1/anchors"

#: prev-anchor hash of the genesis anchor in every tenant chain.
GENESIS_PREV_HASH = "0" * 64


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def payload_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over the canonical JSON of the anchored title payload."""
    return hashlib.sha256(_canonical(payload)).hexdigest()


@dataclass(frozen=True)
class AnchorRecord:
    """One append-only anchor: title payload hash chained to its predecessor."""

    anchor_id: str
    tenant_state_id: str
    parcel_id: str
    payload_hash: str
    prev_anchor_hash: str
    anchor_hash: str
    merkle_root: str
    anchored_at: str

    def as_dict(self) -> dict:
        return {
            "anchor_id": self.anchor_id,
            "tenant_state_id": self.tenant_state_id,
            "parcel_id": self.parcel_id,
            "payload_hash": self.payload_hash,
            "prev_anchor_hash": self.prev_anchor_hash,
            "anchor_hash": self.anchor_hash,
            "merkle_root": self.merkle_root,
            "anchored_at": self.anchored_at,
        }


class AnchorAdapter(Protocol):
    """Adapter seam: anchor a title payload; verify an anchor later."""

    def anchor(
        self, *, tenant_state_id: str, parcel_id: str, payload: dict[str, Any]
    ) -> AnchorRecord: ...

    def get(self, tenant_state_id: str, anchor_id: str) -> Optional[AnchorRecord]: ...

    def verify(
        self, *, tenant_state_id: str, anchor_id: str, payload: dict[str, Any]
    ) -> bool: ...


class FixtureAnchor:
    """In-memory append-only merkle-chain anchor (default; dev/tests).

    Tenant-scoped like the repository (RLS equivalent): anchors are stored
    and looked up per ``tenant_state_id``, so chains never leak across states.
    """

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # tenant_state_id -> anchor_id -> AnchorRecord
        self._anchors: dict[str, dict[str, AnchorRecord]] = {}
        self._order: dict[str, list[str]] = {}
        self._seq = itertools.count(1)

    def anchor(
        self, *, tenant_state_id: str, parcel_id: str, payload: dict[str, Any]
    ) -> AnchorRecord:
        chain = self._order.setdefault(tenant_state_id, [])
        store = self._anchors.setdefault(tenant_state_id, {})
        phash = payload_hash(payload)
        prev = store[chain[-1]].anchor_hash if chain else GENESIS_PREV_HASH
        anchor_hash = hashlib.sha256(f"{phash}{prev}".encode("ascii")).hexdigest()
        # Merkle root: hash of the ordered concatenation of all anchor hashes.
        root = hashlib.sha256(
            "".join([*(store[a].anchor_hash for a in chain), anchor_hash]).encode("ascii")
        ).hexdigest()
        record = AnchorRecord(
            anchor_id=f"anchor-{tenant_state_id}-{next(self._seq):06d}",
            tenant_state_id=tenant_state_id,
            parcel_id=parcel_id,
            payload_hash=phash,
            prev_anchor_hash=prev,
            anchor_hash=anchor_hash,
            merkle_root=root,
            anchored_at=self._clock().isoformat(),
        )
        store[record.anchor_id] = record
        chain.append(record.anchor_id)
        return record

    def get(self, tenant_state_id: str, anchor_id: str) -> Optional[AnchorRecord]:
        return self._anchors.get(tenant_state_id, {}).get(anchor_id)

    def _chain_intact(self, tenant_state_id: str, up_to: str) -> bool:
        """Recompute the chain from genesis to ``up_to`` inclusive."""
        store = self._anchors.get(tenant_state_id, {})
        prev = GENESIS_PREV_HASH
        for anchor_id in self._order.get(tenant_state_id, []):
            record = store[anchor_id]
            if record.prev_anchor_hash != prev:
                return False
            if record.anchor_hash != hashlib.sha256(
                f"{record.payload_hash}{record.prev_anchor_hash}".encode("ascii")
            ).hexdigest():
                return False
            prev = record.anchor_hash
            if anchor_id == up_to:
                return True
        return False

    def verify(
        self, *, tenant_state_id: str, anchor_id: str, payload: dict[str, Any]
    ) -> bool:
        """Recompute payload hash + chain linkage; True only if both match."""
        record = self.get(tenant_state_id, anchor_id)
        if record is None:
            return False
        if payload_hash(payload) != record.payload_hash:
            return False
        return self._chain_intact(tenant_state_id, anchor_id)


class HttpAnchorAdapter:
    """Production anchor service seam (SOS_LANDS_ANCHOR_URL) — fail-closed."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        environ: Optional[Dict[str, str]] = None,
        timeout_s: float = 5.0,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        self.base_url = base_url or env.get("SOS_LANDS_ANCHOR_URL") or DEFAULT_ANCHOR_URL
        self.timeout_s = timeout_s

    def anchor(self, *, tenant_state_id: str, parcel_id: str, payload):  # pragma: no cover
        raise AdapterUnavailableError(
            "HttpAnchorAdapter is wired on the notary-service image; the seam "
            f"is config-gated here (SOS_LANDS_ANCHOR_URL={self.base_url})."
        )

    def get(self, tenant_state_id: str, anchor_id: str):  # pragma: no cover
        raise AdapterUnavailableError("HttpAnchorAdapter seam is config-gated")

    def verify(self, *, tenant_state_id: str, anchor_id: str, payload):  # pragma: no cover
        raise AdapterUnavailableError("HttpAnchorAdapter seam is config-gated")


def anchor_adapter_from_env(environ: Optional[Dict[str, str]] = None) -> AnchorAdapter:
    """Build the anchor adapter from environment (fixture default; fail-closed prod)."""
    env = environ if environ is not None else dict(os.environ)
    profile = env.get("SOS_LANDS_PROFILE", "dev")
    url = env.get("SOS_LANDS_ANCHOR_URL")
    if profile == "production":
        if not url:
            raise AdapterUnavailableError(
                "SOS_LANDS_ANCHOR_URL is required when SOS_LANDS_PROFILE=production "
                "(fail-closed: refusing to boot with the in-memory fixture anchor)"
            )
        return HttpAnchorAdapter(url)
    if url:
        return HttpAnchorAdapter(url)
    return FixtureAnchor()
