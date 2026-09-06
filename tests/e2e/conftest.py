"""Stage 7.B E2E integration harness — in-process multi-app loader.

Loads several FastAPI services into one pytest process over in-process
httpx ASGI transports, so cross-service journeys run with no network,
no Docker, and fully deterministic local wiring.

Key pieces
----------
- :func:`register_service_alias` / :func:`load_service_app` — import a
  service's modules under a **unique synthetic package name**
  (``s7e2e_<alias>``) rooted at the service directory.  Every service here
  ships a package literally named ``app``; the contracts generator
  (``contracts/openapi/generate_from_apps.py``) solves the collision by
  evicting ``sys.modules`` between imports, which breaks request-time
  relative imports once two apps coexist in one process.  Aliasing the
  package instead keeps all apps live simultaneously: relative imports
  resolve inside the alias, and absolute ``_shared`` imports still work
  because ``services/`` is on ``sys.path``.
- :class:`SyncASGIClient` — synchronous ``httpx.Client`` over an
  ``httpx.ASGITransport`` (same idiom as
  ``edge/edge-daemon/edge_daemon/gateway.py::SyncASGITransport``).
- :class:`InMemoryLedgerHarness` — Python mirror of the Go
  ``ledger/splits`` in-memory TigerBeetle fake (``InMemoryLedger``) +
  ``ComputeSplit`` used by mod-rev-core with ``REV_CORE_LEDGER=memory``.
  The Go toolchain is unavailable in this sandbox, so the in-process tier
  drives the identical split/transfer semantics (deterministic kobo
  rounding, idempotent transfer IDs, no-overdraft, in-batch staging)
  through this reference port; the live tier (``E2E_STACK=live``) points
  the same journeys at the real Go service via
  ``tests/e2e/docker-compose.integration.yaml``.
- Live-stack mode: when ``E2E_STACK=live`` the client fixtures build plain
  ``httpx.Client`` against the ``E2E_*_BASE_URL`` endpoints wired by the
  integration compose file instead of in-process apps.  Live mode without
  the required base URLs fails fast at fixture setup (fail-closed, same
  idiom as the service adapters).
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import types
from pathlib import Path

import httpx
import pytest

# ---------------------------------------------------------------------------
# Repo paths on sys.path (only packages with unique names may live here).
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = REPO_ROOT / "services"
EDGE_DAEMON_DIR = REPO_ROOT / "edge" / "edge-daemon"
SOSCTL_SRC = REPO_ROOT / "tools" / "sosctl" / "src"

for _path in (str(SERVICES_DIR), str(EDGE_DAEMON_DIR), str(SOSCTL_SRC)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

#: Default telco shared secret for the portal USSD/IVR webhooks (local-only).
TEST_TELCO_SECRET = "s7e2e-telco-secret"

#: Journey tenant state.
STATE = "lagos"


# ---------------------------------------------------------------------------
# Multi-app loader (aliased packages; see module docstring)
# ---------------------------------------------------------------------------


def register_service_alias(alias: str, service: str) -> str:
    """Map ``s7e2e_<alias>`` onto ``services/<service>/``; returns the alias root.

    Afterwards ``importlib.import_module("s7e2e_<alias>.app.main")`` imports
    the service's modules with relative imports contained inside the alias.
    """
    svc_dir = SERVICES_DIR / service
    if not svc_dir.is_dir():
        raise RuntimeError(f"unknown service directory: {svc_dir}")
    root_alias = f"s7e2e_{alias}"
    if root_alias not in sys.modules:
        parent = types.ModuleType(root_alias)
        parent.__path__ = [str(svc_dir)]  # type: ignore[attr-defined]
        sys.modules[root_alias] = parent
    return root_alias


def load_service_app(alias: str, service: str, package: str = "app.main",
                     factory: str = "create_app", **factory_kwargs):
    """Import ``service``'s FastAPI app under a unique synthetic package."""
    root_alias = register_service_alias(alias, service)
    module = importlib.import_module(f"{root_alias}.{package}")
    return getattr(module, factory)(**factory_kwargs)


# ---------------------------------------------------------------------------
# Synchronous client over an ASGI transport
# ---------------------------------------------------------------------------


class _SyncASGITransport(httpx.BaseTransport):
    """Sync wrapper around ``httpx.ASGITransport`` (edge-daemon idiom)."""

    def __init__(self, app) -> None:
        self._inner = httpx.ASGITransport(app=app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        async def _roundtrip() -> httpx.Response:
            resp = await self._inner.handle_async_request(request)
            chunks = [chunk async for chunk in resp.stream]
            return httpx.Response(
                status_code=resp.status_code,
                headers=resp.headers,
                content=b"".join(chunks),
                extensions=resp.extensions,
            )

        return asyncio.run(_roundtrip())


class SyncASGIClient(httpx.Client):
    """``httpx.Client`` talking to an in-process ASGI app."""

    def __init__(self, app, base_url: str = "http://e2e.local", **kwargs) -> None:
        super().__init__(transport=_SyncASGITransport(app), base_url=base_url, **kwargs)


# ---------------------------------------------------------------------------
# Live-stack mode (E2E_STACK=live; see docker-compose.integration.yaml)
# ---------------------------------------------------------------------------

LIVE = os.environ.get("E2E_STACK") == "live"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "e2e: Stage 7.B end-to-end journey (in-process tier; live stack when "
        "E2E_STACK=live)",
    )
    config.addinivalue_line(
        "markers",
        "e2e_live: runs only against the live integration stack "
        "(E2E_STACK=live; skip-gated otherwise)",
    )


def pytest_collection_modifyitems(items):
    for item in items:
        item.add_marker(pytest.mark.e2e)
        if "e2e_live" in item.keywords and not LIVE:
            item.add_marker(
                pytest.mark.skip(
                    reason="e2e_live: requires E2E_STACK=live against "
                    "tests/e2e/docker-compose.integration.yaml (CI/deployment-run)"
                )
            )


def _live_base_url(env_var: str) -> str:
    url = os.environ.get(env_var, "")
    if not url:
        pytest.fail(
            f"E2E_STACK=live requires {env_var} (fail-closed) — see "
            "tests/e2e/docker-compose.integration.yaml"
        )
    return url


def make_client(alias: str, service: str, env_var: str, **factory_kwargs) -> httpx.Client:
    """Live mode: HTTP client against the compose stack; otherwise in-process."""
    if LIVE:
        return httpx.Client(base_url=_live_base_url(env_var), timeout=10.0)
    return SyncASGIClient(load_service_app(alias, service, **factory_kwargs))


# ---------------------------------------------------------------------------
# Python reference port of ledger/splits (Go) in-memory TigerBeetle fake.
# Semantics mirrored: deterministic kobo rounding with the pool remainder on
# the last INSTANT leg; idempotent transfer IDs (replay -> exists error);
# no-overdraft with in-batch staged balances.
# ---------------------------------------------------------------------------

TOTAL_BASIS_POINTS = 10_000
TIMING_INSTANT = "INSTANT"
TIMING_END_OF_MONTH = "END_OF_MONTH"


class TransferExistsError(RuntimeError):
    """Raised on idempotent replay of an already-posted transfer ID."""


class TransferRejectedError(RuntimeError):
    """Raised on zero amount, same-account, unknown account, or overdraft."""


def compute_split(gross_kobo: int, rules: list[tuple[str, int, str]]) -> dict:
    """Mirror of ``ledger/splits.ComputeSplit`` for INSTANT/END_OF_MONTH rules.

    ``rules``: list of ``(beneficiary, percentage_bps, timing)``.
    Returns a plan dict with ``instant`` legs (last leg absorbs the pool
    rounding remainder, so legs always sum exactly to the pool).
    """
    instant = [(b, bps) for b, bps, t in rules if t == TIMING_INSTANT]
    month_end = [(b, bps) for b, bps, t in rules if t == TIMING_END_OF_MONTH]
    if not instant:
        raise ValueError("no INSTANT legs to execute")
    total_bps = sum(bps for _, bps in instant)
    if not 0 < total_bps <= TOTAL_BASIS_POINTS:
        raise ValueError(f"INSTANT legs total {total_bps} bp out of range")

    pool = gross_kobo * total_bps // TOTAL_BASIS_POINTS
    legs: dict[int, int] = {}
    allocated = 0
    for i, (beneficiary, bps) in enumerate(instant):
        if i == len(instant) - 1:
            amount = pool - allocated  # last leg absorbs rounding remainder
        else:
            amount = gross_kobo * bps // TOTAL_BASIS_POINTS
            allocated += amount
        legs[beneficiary] = amount
    return {
        "instant": legs,
        "month_end": {b: gross_kobo * bps // TOTAL_BASIS_POINTS for b, bps in month_end},
        "remainder": gross_kobo - pool,
        "gross": gross_kobo,
    }


class InMemoryLedgerHarness:
    """Mirror of ``ledger/splits.InMemoryLedger`` (TigerBeetle-fake semantics)."""

    def __init__(self) -> None:
        self._accounts: dict[int, dict[str, int]] = {}
        self._transfers: dict[int, dict] = {}

    def create_account(self, account_id: int) -> None:
        self._accounts.setdefault(account_id, {"credits": 0, "debits": 0})

    def seed_account(self, account_id: int, credits: int) -> None:
        self.create_account(account_id)
        self._accounts[account_id]["credits"] += credits

    def balance(self, account_id: int) -> int:
        acct = self._accounts[account_id]
        return acct["credits"] - acct["debits"]

    def create_transfers(self, transfers: list[dict]) -> None:
        """Post a batch atomically. Each transfer: id, debit, credit, amount.

        Raises on any rejection (id replay, zero amount, unknown account,
        overdraft against staged balances) — mirroring the Go fake where a
        failed batch member commits nothing.
        """
        staged_debit: dict[int, int] = {}
        staged_credit: dict[int, int] = {}
        for tr in transfers:
            if tr["id"] in self._transfers:
                raise TransferExistsError(f"transfer {tr['id']} already exists (replay)")
            if tr["amount"] <= 0 or tr["debit"] == tr["credit"]:
                raise TransferRejectedError(f"transfer {tr['id']} invalid")
            if tr["debit"] not in self._accounts or tr["credit"] not in self._accounts:
                raise TransferRejectedError(f"transfer {tr['id']}: account not found")
            available = (
                self._accounts[tr["debit"]]["credits"] + staged_credit.get(tr["debit"], 0)
            ) - (
                self._accounts[tr["debit"]]["debits"] + staged_debit.get(tr["debit"], 0)
            )
            if tr["amount"] > available:
                raise TransferRejectedError(f"transfer {tr['id']}: overdraft")
            staged_debit[tr["debit"]] = staged_debit.get(tr["debit"], 0) + tr["amount"]
            staged_credit[tr["credit"]] = staged_credit.get(tr["credit"], 0) + tr["amount"]
        for tr in transfers:
            self._accounts[tr["debit"]]["debits"] += tr["amount"]
            self._accounts[tr["credit"]]["credits"] += tr["amount"]
            self._transfers[tr["id"]] = dict(tr)

    def post_atomic_split(self, batch_id: int, clearing: int, plan: dict) -> None:
        """Post the INSTANT legs of a split plan as one atomic batch."""
        transfers = [
            {"id": batch_id * 1000 + i, "debit": clearing, "credit": acct, "amount": amount}
            for i, (acct, amount) in enumerate(plan["instant"].items())
        ]
        self.create_transfers(transfers)


# ---------------------------------------------------------------------------
# Service client fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ledger() -> InMemoryLedgerHarness:
    return InMemoryLedgerHarness()


@pytest.fixture()
def portal_client(monkeypatch) -> httpx.Client:
    monkeypatch.setenv("CITIZEN_PORTAL_TELCO_SECRET", TEST_TELCO_SECRET)
    client = make_client("portal", "mod-citizen-portal", "E2E_PORTAL_BASE_URL")
    yield client
    client.close()


@pytest.fixture()
def kyc_client() -> httpx.Client:
    client = make_client("kyc", "mod-kyc-kyb", "E2E_KYC_BASE_URL")
    yield client
    client.close()


@pytest.fixture()
def market_client() -> httpx.Client:
    client = make_client("market", "mod-market", "E2E_MARKET_BASE_URL")
    yield client
    client.close()


@pytest.fixture()
def transparency_client() -> httpx.Client:
    client = make_client("transparency", "mod-transparency", "E2E_TRANSPARENCY_BASE_URL")
    yield client
    client.close()


def build_control_plane(alias: str, tmp_path: Path, operators: dict | None = None):
    """Build an in-process control-plane (app, store, archive_root).

    Every audit event the store appends is mirrored to a
    ``LocalFileArchive`` under ``tmp_path`` — the same JSONL source
    ``sosctl audit verify-chain`` recomputes the hash chain from.
    """
    root_alias = register_service_alias(alias, "control-plane")
    domain = importlib.import_module(f"{root_alias}.app.domain")
    archive_mod = importlib.import_module(f"{root_alias}.app.audit_archive")
    main_mod = importlib.import_module(f"{root_alias}.app.main")

    archive_root = tmp_path / alias / "audit"
    store = domain.MetadataStore(
        operators=operators,
        provision_mode="sync",
        archive=archive_mod.LocalFileArchive(archive_root),
    )
    app = main_mod.create_app(store=store)
    return app, store, archive_root


@pytest.fixture()
def control_plane(tmp_path):
    """(client, store, archive_root) — in-process control-plane w/ file archive."""
    if LIVE:
        pytest.skip("control-plane store/archive access is in-process only; the "
                    "live tier verifies the chain from the archived volume")
    app, store, archive_root = build_control_plane("cp", tmp_path)
    client = SyncASGIClient(app)
    yield client, store, archive_root
    client.close()
