"""Fail-closed adapter seams for mod-mortgage.

Deterministic local fixtures are the default everywhere; production engines
sit behind adapter protocols that raise :class:`AdapterUnavailableError`
when their environment configuration is missing — mirroring the fail-closed
adapter idiom of ``services/_shared/eventbus``, mod-safecity-vision and
``ledger/fundsflow/tigerbeetle_flows.py``.

Selection (environment-driven, fail-closed)::

    SOS_MORTGAGE_PROFILE=production      → boot requires the real seams below
    SOS_MORTGAGE_CREDIT_ENGINE=fixture|http   (default: fixture)
    SOS_MORTGAGE_CREDIT_URL=http://mod-ml-inference:8021/ml/v1/credit/score
    SOS_MORTGAGE_LEDGER=fixture|tigerbeetle   (default: fixture)
    SOS_MORTGAGE_TB_URL=...                    (required for tigerbeetle)
    SOS_MORTGAGE_LANDS=fixture|http           (default: fixture)
    SOS_MORTGAGE_LANDS_URL=http://mod-gis-lands:8003

All money amounts are integer kobo — never floats.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Protocol

from pydantic import BaseModel


class AdapterUnavailableError(RuntimeError):
    """Raised when an optional production engine dependency/config is missing."""


class LedgerError(RuntimeError):
    """Raised on illegal transfer state transitions or insufficient funds."""


def deterministic_transfer_id(idempotency_key: str, leg: str) -> int:
    """Deterministic 128-bit transfer ID from (idempotency_key, leg).

    Same (key, leg) → same ID, so retries map onto TigerBeetle's native
    idempotent-create semantics and never double-apply (mirrors
    ``ledger/fundsflow/tigerbeetle_flows.deterministic_transfer_id``).
    """
    digest = hashlib.sha256(f"{idempotency_key}|{leg}".encode("utf-8")).digest()
    return int.from_bytes(digest[:16], "big")


def deterministic_account_id(*parts: str) -> int:
    """Deterministic 128-bit ledger account ID derived from stable parts."""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:16], "big")


# ---------------------------------------------------------------------------
# Credit scoring (mod-ml-inference credit_mlp seam)
# ---------------------------------------------------------------------------


class CreditScoringAdapter(Protocol):
    """Adapter seam: applicant reference -> credit score in [300, 850]."""

    def score(self, applicant_id: str) -> int: ...


class FixtureCreditScorer:
    """Deterministic fixture scorer (default; local dev and tests).

    The score is derived from SHA-256 of the applicant id: the same
    applicant always scores the same, in the inclusive range [300, 850].
    """

    MIN_SCORE = 300
    MAX_SCORE = 850

    def score(self, applicant_id: str) -> int:
        digest = hashlib.sha256(f"credit:{applicant_id}".encode()).digest()
        span = self.MAX_SCORE - self.MIN_SCORE + 1
        return self.MIN_SCORE + int.from_bytes(digest[:8], "big") % span


class HttpCreditScorer:
    """Production scorer over mod-ml-inference — fail-closed seam.

    Requires ``SOS_MORTGAGE_CREDIT_URL``; constructing without configuration
    raises :class:`AdapterUnavailableError` rather than silently degrading.
    """

    DEFAULT_URL = "http://mod-ml-inference:8021/ml/v1/credit/score"

    def __init__(
        self,
        url: Optional[str] = None,
        environ: Optional[Dict[str, str]] = None,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        self.url = url or env.get("SOS_MORTGAGE_CREDIT_URL")
        if not self.url:
            raise AdapterUnavailableError(
                "SOS_MORTGAGE_CREDIT_URL is required for HttpCreditScorer "
                "(fail-closed: refusing to score with an unconfigured engine)"
            )
        try:
            import httpx  # optional dependency  # noqa: F401
        except ImportError as exc:
            raise AdapterUnavailableError(
                "httpx is required for HttpCreditScorer (pip install httpx)"
            ) from exc

    def score(self, applicant_id: str) -> int:  # pragma: no cover - network seam
        import httpx

        resp = httpx.post(self.url, json={"applicant_id": applicant_id}, timeout=10.0)
        resp.raise_for_status()
        score = int(resp.json()["score"])
        if not FixtureCreditScorer.MIN_SCORE <= score <= FixtureCreditScorer.MAX_SCORE:
            raise AdapterUnavailableError(f"credit score {score} out of range")
        return score


def credit_scorer_from_env(environ: Optional[Dict[str, str]] = None) -> CreditScoringAdapter:
    """Build a credit scorer from ``SOS_MORTGAGE_CREDIT_ENGINE`` (default fixture)."""
    env = environ if environ is not None else dict(os.environ)
    kind = env.get("SOS_MORTGAGE_CREDIT_ENGINE", "fixture")
    if kind == "fixture":
        return FixtureCreditScorer()
    if kind == "http":
        return HttpCreditScorer(environ=env)
    raise AdapterUnavailableError(f"unknown SOS_MORTGAGE_CREDIT_ENGINE {kind!r}")


# ---------------------------------------------------------------------------
# Mortgage ledger (TigerBeetle two-phase hold → post/void seam)
# ---------------------------------------------------------------------------


class MortgageLedgerAdapter(Protocol):
    """Two-phase transfer seam: PENDING hold, then POST (commit) or VOID.

    A posted transfer can never be re-voided and a voided one can never be
    posted; ids are deterministic 128-bit integers so replays are no-ops.
    All amounts are integer kobo (conservation of value).
    """

    def hold(
        self, transfer_id: int, debit_account: int, credit_account: int,
        amount_kobo: int, idempotency_key: str,
    ) -> None: ...

    def post(self, transfer_id: int) -> None: ...

    def void(self, transfer_id: int) -> None: ...

    def balance(self, account: int) -> int: ...


class FixtureLedgerAdapter:
    """Deterministic in-memory two-phase ledger (default; local dev/tests).

    Replicates the hold → post/void semantics of
    ``ledger/fundsflow/tigerbeetle_flows.InMemoryTBClient``: holds reserve
    funds (PENDING), posts move them (POSTED), voids release the hold
    (VOIDED). Idempotent by deterministic transfer id.
    """

    #: Deterministic opening float for fixture accounts (dev/test only).
    DEFAULT_TREASURY_FLOAT_KOBO = 10**15

    def __init__(self, opening_treasury_balance_kobo: int = DEFAULT_TREASURY_FLOAT_KOBO) -> None:
        self._opening = opening_treasury_balance_kobo
        self.transfers: Dict[int, dict] = {}
        self.balances: Dict[int, int] = {}
        # test hooks
        self.fail_on_post: bool = False
        self.fail_on_hold: bool = False

    def _treasury_seeded(self, account: int) -> None:
        """Fixture accounts are lazily seeded with the deterministic opening
        float so dev/test flows never strand on synthetic balances."""
        if account not in self.balances:
            self.balances[account] = self._opening

    def fund(self, account: int, amount_kobo: int) -> None:
        """Fixture helper: credit an account (dev/test seeding)."""
        self.balances[account] = self.balances.get(account, 0) + amount_kobo

    def _held(self, account: int) -> int:
        return sum(
            t["amount_kobo"]
            for t in self.transfers.values()
            if t["state"] == "PENDING" and t["debit_account"] == account
        )

    def hold(
        self, transfer_id: int, debit_account: int, credit_account: int,
        amount_kobo: int, idempotency_key: str,
    ) -> None:
        if transfer_id in self.transfers:
            return  # idempotent replay
        if self.fail_on_hold:
            raise LedgerError("injected failure creating hold")
        if amount_kobo <= 0:
            raise LedgerError("transfer amount must be positive (integer kobo)")
        self._treasury_seeded(debit_account)
        if self.balances[debit_account] - self._held(debit_account) < amount_kobo:
            raise LedgerError("insufficient funds for hold")
        self.transfers[transfer_id] = {
            "id": transfer_id,
            "debit_account": debit_account,
            "credit_account": credit_account,
            "amount_kobo": amount_kobo,
            "idempotency_key": idempotency_key,
            "state": "PENDING",
        }

    def post(self, transfer_id: int) -> None:
        t = self.transfers.get(transfer_id)
        if t is None:
            raise LedgerError(f"unknown pending transfer {transfer_id}")
        if t["state"] == "VOIDED":
            raise LedgerError("cannot post a voided transfer")
        if self.fail_on_post:
            raise LedgerError("injected failure posting pending transfer")
        if t["state"] == "POSTED":
            return  # idempotent no-op
        t["state"] = "POSTED"
        self._treasury_seeded(t["credit_account"])
        self.balances[t["debit_account"]] -= t["amount_kobo"]
        self.balances[t["credit_account"]] += t["amount_kobo"]

    def void(self, transfer_id: int) -> None:
        t = self.transfers.get(transfer_id)
        if t is None:
            raise LedgerError(f"unknown pending transfer {transfer_id}")
        if t["state"] == "POSTED":
            raise LedgerError("cannot void a posted transfer — use a reversal flow")
        t["state"] = "VOIDED"

    def balance(self, account: int) -> int:
        self._treasury_seeded(account)
        return self.balances[account]


class TigerBeetleLedgerAdapter:
    """Production TigerBeetle seam (``SOS_MORTGAGE_TB_URL``) — fail-closed.

    Constructing without configuration raises
    :class:`AdapterUnavailableError` rather than silently degrading to an
    in-memory ledger in production.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        environ: Optional[Dict[str, str]] = None,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        self.url = url or env.get("SOS_MORTGAGE_TB_URL")
        if not self.url:
            raise AdapterUnavailableError(
                "SOS_MORTGAGE_TB_URL is required for TigerBeetleLedgerAdapter "
                "(fail-closed: refusing to run an unconfigured ledger)"
            )
        try:
            import tigerbeetle  # optional dependency  # noqa: F401
        except ImportError as exc:
            raise AdapterUnavailableError(
                "tigerbeetle is required for TigerBeetleLedgerAdapter "
                "(pip install tigerbeetle)"
            ) from exc

    def hold(self, *args, **kwargs):  # pragma: no cover - wired on the TB image
        raise AdapterUnavailableError(
            "TigerBeetleLedgerAdapter transfers are wired on the ledger image; "
            "the seam is config-gated here so the service never degrades."
        )

    post = hold
    void = hold

    def balance(self, account: int) -> int:  # pragma: no cover
        raise AdapterUnavailableError("TigerBeetleLedgerAdapter is config-gated")


def ledger_from_env(environ: Optional[Dict[str, str]] = None) -> MortgageLedgerAdapter:
    """Build a ledger from ``SOS_MORTGAGE_LEDGER`` (default fixture)."""
    env = environ if environ is not None else dict(os.environ)
    kind = env.get("SOS_MORTGAGE_LEDGER", "fixture")
    if kind == "fixture":
        return FixtureLedgerAdapter()
    if kind == "tigerbeetle":
        return TigerBeetleLedgerAdapter(environ=env)
    raise AdapterUnavailableError(f"unknown SOS_MORTGAGE_LEDGER {kind!r}")


# ---------------------------------------------------------------------------
# Land registry (mod-gis-lands seam) — fail-closed title verification
# ---------------------------------------------------------------------------

#: Title reference format, e.g. ``LAG-2024-000123`` (C-of-O style).
TITLE_REF_RE = re.compile(r"^[A-Z]{2,5}-\d{4}-\d{6}$")


class TitleSnapshot(BaseModel):
    """Point-in-time title truth from the land registry.

    ``verified=False`` means the registry could not attest one or more
    fields (missing title_ref / owner / encumbrance info, e.g. an older
    mod-gis-lands that predates lifecycle-aware verification): strict
    (production) adapters fail closed on UNVERIFIED snapshots; the fixture
    is lenient so local dev/tests can run without a full registry.
    """

    status: str = "unverified"  # current | unverified | not_found | revoked | suspended
    current_title_ref: Optional[str] = None
    owner_id: Optional[str] = None
    has_blocking_encumbrance: Optional[bool] = None
    verified: bool = False

    def title_hash(self) -> str:
        body = {
            "status": self.status,
            "current_title_ref": self.current_title_ref,
            "owner_id": self.owner_id,
            "has_blocking_encumbrance": self.has_blocking_encumbrance,
        }
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class LandRegistryAdapter(Protocol):
    """Adapter seam: fail-closed title verification before lien registration.

    ``strict`` adapters (production/HTTP) treat UNVERIFIED snapshots as a
    hard failure; the fixture (``strict=False``) allows them.
    """

    strict: bool = True

    def title_snapshot(self, state_id: str, parcel_id: str) -> TitleSnapshot: ...


class FixtureLandRegistry:
    """Deterministic fixture registry (default; local dev and tests).

    Parcels are seeded explicitly via :meth:`register` (strict snapshot with
    full title/owner/encumbrance attestation). Unseeded parcels yield a
    deterministic UNVERIFIED snapshot — allowed here (``strict=False``) but
    rejected by strict production adapters. A seeded parcel whose title ref
    is malformed reports ``not_found`` (fail-closed format check).
    """

    strict = False

    def __init__(self) -> None:
        self._parcels: Dict[str, Dict[str, Any]] = {}

    def register(
        self, parcel_id: str, title_ref: str, owner_id: str,
        has_blocking_encumbrance: bool = False, status: str = "current",
    ) -> None:
        """Fixture helper: seed a parcel's title record (dev/test)."""
        self._parcels[parcel_id] = {
            "title_ref": title_ref,
            "owner_id": owner_id,
            "has_blocking_encumbrance": has_blocking_encumbrance,
            "status": status,
        }

    def title_snapshot(self, state_id: str, parcel_id: str) -> TitleSnapshot:
        rec = self._parcels.get(parcel_id)
        if rec is None:
            # deterministic UNVERIFIED snapshot — lenient in fixture only
            return TitleSnapshot(status="unverified", verified=False)
        if not TITLE_REF_RE.match(rec["title_ref"]):
            return TitleSnapshot(status="not_found", verified=True)
        return TitleSnapshot(
            status=rec["status"],
            current_title_ref=rec["title_ref"],
            owner_id=rec["owner_id"],
            has_blocking_encumbrance=rec["has_blocking_encumbrance"],
            verified=True,
        )


class HttpLandRegistry:
    """Production registry over mod-gis-lands — fail-closed seam.

    Requires ``SOS_MORTGAGE_LANDS_URL``; constructing without configuration
    raises :class:`AdapterUnavailableError`. Strict: any verification field
    the registry cannot attest (older mod-gis-lands without lifecycle-aware
    verification/encumbrance fields) yields an UNVERIFIED snapshot, and the
    domain layer fails closed rather than registering a lien on it.
    """

    DEFAULT_URL = "http://mod-gis-lands:8003"
    strict = True

    def __init__(
        self,
        url: Optional[str] = None,
        environ: Optional[Dict[str, str]] = None,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        self.url = (url or env.get("SOS_MORTGAGE_LANDS_URL") or "").rstrip("/")
        if not self.url:
            raise AdapterUnavailableError(
                "SOS_MORTGAGE_LANDS_URL is required for HttpLandRegistry "
                "(fail-closed: refusing to register liens against an "
                "unverified title)"
            )
        try:
            import httpx  # optional dependency  # noqa: F401
        except ImportError as exc:
            raise AdapterUnavailableError(
                "httpx is required for HttpLandRegistry (pip install httpx)"
            ) from exc

    def title_snapshot(self, state_id: str, parcel_id: str) -> TitleSnapshot:  # pragma: no cover - network seam
        import httpx

        resp = httpx.get(
            f"{self.url}/api/v1/states/{state_id}/cadastre/parcels/{parcel_id}",
            headers={"X-State-Tenant": state_id},
            timeout=10.0,
        )
        if resp.status_code == 404:
            return TitleSnapshot(status="not_found", verified=True)
        resp.raise_for_status()
        body = resp.json() if resp.content else {}
        if not isinstance(body, dict):
            return TitleSnapshot(status="unverified", verified=False)
        # Defensive parse: a teammate is concurrently adding lifecycle-aware
        # verification + encumbrance fields to mod-gis-lands — treat any
        # absent field as UNVERIFIED (fail closed in production).
        title_ref = body.get("title_ref") or body.get("c_of_o_number")
        owner_id = body.get("owner_id") or body.get("owner")
        encumbrance = body.get("has_blocking_encumbrance")
        if encumbrance is None and isinstance(body.get("encumbrances"), list):
            encumbrance = any(
                isinstance(e, dict) and e.get("blocking", True) for e in body["encumbrances"]
            ) or bool(body["encumbrances"])
        status = body.get("status") or body.get("lifecycle_status")
        verified = bool(title_ref) and bool(owner_id) and encumbrance is not None
        return TitleSnapshot(
            status=str(status) if status else ("current" if verified else "unverified"),
            current_title_ref=str(title_ref) if title_ref else None,
            owner_id=str(owner_id) if owner_id else None,
            has_blocking_encumbrance=bool(encumbrance) if encumbrance is not None else None,
            verified=verified,
        )


def land_registry_from_env(environ: Optional[Dict[str, str]] = None) -> LandRegistryAdapter:
    """Build a land registry from ``SOS_MORTGAGE_LANDS`` (default fixture)."""
    env = environ if environ is not None else dict(os.environ)
    kind = env.get("SOS_MORTGAGE_LANDS", "fixture")
    if kind == "fixture":
        return FixtureLandRegistry()
    if kind == "http":
        return HttpLandRegistry(environ=env)
    raise AdapterUnavailableError(f"unknown SOS_MORTGAGE_LANDS {kind!r}")


# ---------------------------------------------------------------------------
# Land title transfer (foreclosure sale) — fail-closed seam
# ---------------------------------------------------------------------------


class LandTitleTransferAdapter(Protocol):
    """Adapter seam: transfer a parcel's title to the purchaser at SOLD.

    Fail-closed: if the transfer cannot be recorded by the land registry,
    the sale is NOT recorded (the domain layer aborts the transition).
    """

    def transfer_title(
        self, state_id: str, parcel_id: str, from_owner_id: str,
        to_owner_id: str, reference: str,
    ) -> str: ...


class FixtureTitleTransfer:
    """Deterministic fixture title-transfer registry (default; dev/tests).

    Records every transfer; ``fail_next`` is a crash-injection hook for
    tests (the sale must then NOT be recorded).
    """

    def __init__(self) -> None:
        self.transfers: List[Dict[str, str]] = []
        self.fail_next: bool = False

    def transfer_title(
        self, state_id: str, parcel_id: str, from_owner_id: str,
        to_owner_id: str, reference: str,
    ) -> str:
        if self.fail_next:
            self.fail_next = False
            raise AdapterUnavailableError("injected title transfer failure")
        digest = hashlib.sha256(
            f"title-transfer:{state_id}:{parcel_id}:{to_owner_id}:{reference}".encode()
        ).hexdigest()[:16]
        record = {
            "transfer_ref": f"tt-{digest}",
            "state_id": state_id,
            "parcel_id": parcel_id,
            "from_owner_id": from_owner_id,
            "to_owner_id": to_owner_id,
            "reference": reference,
        }
        self.transfers.append(record)
        return record["transfer_ref"]


class HttpTitleTransfer:
    """Production title transfer over mod-gis-lands — fail-closed seam.

    Requires ``SOS_MORTGAGE_LANDS_URL``; constructing without configuration
    raises :class:`AdapterUnavailableError`.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        environ: Optional[Dict[str, str]] = None,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        self.url = (url or env.get("SOS_MORTGAGE_LANDS_URL") or "").rstrip("/")
        if not self.url:
            raise AdapterUnavailableError(
                "SOS_MORTGAGE_LANDS_URL is required for HttpTitleTransfer "
                "(fail-closed: refusing to sell without a title-transfer path)"
            )
        try:
            import httpx  # optional dependency  # noqa: F401
        except ImportError as exc:
            raise AdapterUnavailableError(
                "httpx is required for HttpTitleTransfer (pip install httpx)"
            ) from exc

    def transfer_title(
        self, state_id: str, parcel_id: str, from_owner_id: str,
        to_owner_id: str, reference: str,
    ) -> str:  # pragma: no cover - network seam
        import httpx

        resp = httpx.post(
            f"{self.url}/api/v1/states/{state_id}/cadastre/parcels/{parcel_id}/title-transfers",
            headers={"X-State-Tenant": state_id},
            json={
                "from_owner_id": from_owner_id,
                "to_owner_id": to_owner_id,
                "reference": reference,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        body = resp.json() if resp.content else {}
        ref = body.get("transfer_ref") if isinstance(body, dict) else None
        return str(ref or reference)


def title_transfer_from_env(environ: Optional[Dict[str, str]] = None) -> LandTitleTransferAdapter:
    """Build a title-transfer adapter from ``SOS_MORTGAGE_LANDS`` (default fixture)."""
    env = environ if environ is not None else dict(os.environ)
    kind = env.get("SOS_MORTGAGE_LANDS", "fixture")
    if kind == "fixture":
        return FixtureTitleTransfer()
    if kind == "http":
        return HttpTitleTransfer(environ=env)
    raise AdapterUnavailableError(f"unknown SOS_MORTGAGE_LANDS {kind!r}")


# ---------------------------------------------------------------------------
# Production profile guard (fail-closed boot)
# ---------------------------------------------------------------------------


def require_production_config(environ: Optional[Dict[str, str]] = None) -> None:
    """Hard boot failure when ``SOS_MORTGAGE_PROFILE=production`` and a real
    adapter is unconfigured (fail-closed, mirroring mod-ml-inference)."""
    env = environ if environ is not None else dict(os.environ)
    if env.get("SOS_MORTGAGE_PROFILE") != "production":
        return
    missing = [
        name
        for name in ("SOS_MORTGAGE_TB_URL", "SOS_MORTGAGE_LANDS_URL",
                     "SOS_MORTGAGE_OUTBOX_DSN")
        if not env.get(name)
    ]
    if missing:
        raise AdapterUnavailableError(
            "SOS_MORTGAGE_PROFILE=production requires "
            + ", ".join(missing)
            + " (fail-closed: fixture adapters are not permitted in production)"
        )
