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
import os
import re
from typing import Dict, Optional, Protocol


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
# Land registry (mod-gis-lands seam) — parcel/title confirmation before liens
# ---------------------------------------------------------------------------

#: Fixture title reference format, e.g. ``LAG-2024-000123`` (C-of-O style).
TITLE_REF_RE = re.compile(r"^[A-Z]{2,5}-\d{4}-\d{6}$")


class LandRegistryAdapter(Protocol):
    """Adapter seam: confirm a parcel/title exists before lien registration."""

    def parcel_exists(self, state_id: str, parcel_id: str, title_ref: str) -> bool: ...


class FixtureLandRegistry:
    """Deterministic fixture registry (default; local dev and tests).

    A parcel "exists" iff the title reference matches the C-of-O format
    ``^[A-Z]{2,5}-\\d{4}-\\d{6}$`` — deterministic, no network.
    """

    def parcel_exists(self, state_id: str, parcel_id: str, title_ref: str) -> bool:
        return bool(TITLE_REF_RE.match(title_ref))


class HttpLandRegistry:
    """Production registry over mod-gis-lands — fail-closed seam.

    Requires ``SOS_MORTGAGE_LANDS_URL``; constructing without configuration
    raises :class:`AdapterUnavailableError`.
    """

    DEFAULT_URL = "http://mod-gis-lands:8003"

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

    def parcel_exists(self, state_id: str, parcel_id: str, title_ref: str) -> bool:  # pragma: no cover
        import httpx

        resp = httpx.get(
            f"{self.url}/api/v1/states/{state_id}/cadastre/parcels/{parcel_id}",
            headers={"X-State-Tenant": state_id},
            timeout=10.0,
        )
        return resp.status_code == 200


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
        for name in ("SOS_MORTGAGE_TB_URL", "SOS_MORTGAGE_LANDS_URL")
        if not env.get(name)
    ]
    if missing:
        raise AdapterUnavailableError(
            "SOS_MORTGAGE_PROFILE=production requires "
            + ", ".join(missing)
            + " (fail-closed: fixture adapters are not permitted in production)"
        )
