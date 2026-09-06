"""Tests for mod-erp-bridge. No external network; deterministic adapters."""
from __future__ import annotations

from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.adapters import (
    AdapterUnavailableError,
    ErpNextAdapter,
    ErpPushError,
    FixtureErpAdapter,
    IfmisExportAdapter,
    OdooAdapter,
    build_adapter,
)
from app.adapters.base import ErpAdapter
from app.domain import ErpReceipt, ErpBackend, JournalEntry, JournalLine, PushStatus
from app.main import create_app
from app.service import DEFAULT_COA_MAP, ErpBridgeService

TENANT = "lagos"
OTHER = "ogun"


def make_entry(entry_id="JE-1", source="evt-1", state=TENANT, amount=125000) -> JournalEntry:
    return JournalEntry(
        entry_id=entry_id,
        tenant_state_id=state,
        date=date(2025, 3, 1),
        memo="test entry",
        lines=[
            JournalLine(account_code="1000_CASH_TREASURY", debit_kobo=amount),
            JournalLine(account_code="3000_CRF", credit_kobo=amount),
        ],
        source_event_id=source,
    )


def make_service(**kwargs) -> ErpBridgeService:
    kwargs.setdefault("backoff_base_s", 0.0)
    return ErpBridgeService(adapter=kwargs.pop("adapter", FixtureErpAdapter()), **kwargs)


# ---------------- domain validation ----------------

def test_balanced_entry_validates():
    entry = make_entry()
    assert entry.hash and len(entry.hash) == 64


def test_unbalanced_entry_rejected():
    with pytest.raises(ValueError, match="unbalanced"):
        JournalEntry(
            entry_id="JE-BAD",
            tenant_state_id=TENANT,
            date=date(2025, 3, 1),
            lines=[
                JournalLine(account_code="1000_CASH_TREASURY", debit_kobo=100),
                JournalLine(account_code="3000_CRF", credit_kobo=99),
            ],
            source_event_id="evt-bad",
        )


def test_zero_amount_entry_rejected():
    with pytest.raises(ValueError, match="debit or a credit"):
        JournalLine(account_code="1000_CASH_TREASURY")


def test_two_sided_line_rejected():
    with pytest.raises(ValueError, match="both debit and credit"):
        JournalLine(account_code="1000_CASH_TREASURY", debit_kobo=5, credit_kobo=5)


# ---------------- fixture adapter determinism ----------------

def test_fixture_adapter_deterministic_receipts():
    adapter = FixtureErpAdapter()
    entry = make_entry()
    r1 = adapter.push_journal(entry, DEFAULT_COA_MAP)
    r2 = adapter.push_journal(make_entry(), DEFAULT_COA_MAP)
    assert r1.receipt_id == r2.receipt_id
    assert r1.posted_at == r2.posted_at
    assert r1.backend == ErpBackend.FIXTURE


# ---------------- service: ingest / dedupe ----------------

def test_dedupe_on_replay():
    service = make_service()
    first = service.ingest(make_entry())
    replay = service.ingest(make_entry(entry_id="JE-1-replay", source="evt-1"))
    assert first.status == PushStatus.PUSHED
    assert replay.status == PushStatus.DEDUPED
    # Only one real push reached the adapter.
    assert len(service.adapter.pushed) == 1
    assert len(service.list_journals(TENANT)) == 1


def test_coa_mapping_applied_and_updatable():
    service = make_service()
    coa = service.get_coa_mapping(TENANT)
    assert "1000_CASH_TREASURY" in coa.mapping
    service.set_coa_mapping(TENANT, {"1000_CASH_TREASURY": "ERP-CASH-001"})
    assert service.get_coa_mapping(TENANT).mapping == {"1000_CASH_TREASURY": "ERP-CASH-001"}


# ---------------- hash chain ----------------

def test_outbound_log_chain_verifies_and_detects_tamper():
    service = make_service()
    service.ingest(make_entry("JE-1", "evt-1"))
    service.ingest(make_entry("JE-2", "evt-2"))
    assert service.verify_outbound_log() == []
    # Tamper with a record's payload.
    service._outbound[0].entry_hash = "0" * 64
    errors = service.verify_outbound_log()
    assert errors, "tampering must break the chain"
    assert any("tampered" in e or "broken chain" in e for e in errors)


# ---------------- adapters fail-closed ----------------

def test_odoo_adapter_fail_closed_without_env():
    with pytest.raises(AdapterUnavailableError):
        OdooAdapter.from_env({})
    with pytest.raises(AdapterUnavailableError):
        OdooAdapter.from_env({"ODOO_URL": "http://x", "ODOO_DB": "db"})
    with pytest.raises(AdapterUnavailableError):
        build_adapter({"ERP_BACKEND": "odoo"})


def test_erpnext_adapter_fail_closed_without_env():
    with pytest.raises(AdapterUnavailableError):
        ErpNextAdapter.from_env({})
    with pytest.raises(AdapterUnavailableError):
        build_adapter({"ERP_BACKEND": "erpnext"})


def test_build_adapter_local_default():
    assert isinstance(build_adapter({}), FixtureErpAdapter)
    assert isinstance(build_adapter({"ERP_BACKEND": "ifmis_export"}), IfmisExportAdapter)
    with pytest.raises(AdapterUnavailableError):
        build_adapter({"ERP_BACKEND": "sap"})


# ---------------- Odoo against stubbed ServerProxy ----------------

class _FakeCommon:
    def authenticate(self, db, user, key, ctx):
        assert (db, user, key) == ("db", "user", "key")
        return 42


class _FakeModels:
    def __init__(self):
        self.created = None
        self.posted = None

    def execute_kw(self, db, uid, key, model, method, args):
        assert uid == 42
        if method == "create":
            self.created = args[0]
            return 777
        if method == "action_post":
            self.posted = args[0]
            return True
        raise AssertionError(method)


def test_odoo_adapter_pushes_move_via_stubbed_proxy():
    models = _FakeModels()

    def factory(url):
        return _FakeCommon() if url.endswith("/common") else models

    adapter = OdooAdapter("http://odoo:8069", "db", "user", "key", server_proxy_factory=factory)
    receipt = adapter.push_journal(make_entry(), {"1000_CASH_TREASURY": "ERP-CASH"})
    assert receipt.receipt_id == "ODOO-777"
    assert models.posted == [777]
    lines = models.created["line_ids"]
    assert lines[0][2]["account_code"] == "ERP-CASH"
    assert sum(l[2]["debit"] for l in lines) == sum(l[2]["credit"] for l in lines) == 1250.0
    assert adapter.health() is True


def test_odoo_adapter_auth_failure_raises_push_error():
    class _Deny:
        def authenticate(self, *a):
            return False

    adapter = OdooAdapter("http://odoo:8069", "db", "user", "bad", server_proxy_factory=lambda url: _Deny())
    with pytest.raises(ErpPushError):
        adapter.push_journal(make_entry(), {})


# ---------------- ERPNext via httpx MockTransport ----------------

def _erpnext_client(handler):
    return httpx.Client(
        base_url="http://erpnext:8000",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "token k:s"},
    )


def test_erpnext_adapter_success():
    def handler(request):
        if request.url.path == "/api/resource/Journal Entry":
            return httpx.Response(200, json={"data": {"name": "JV-0001"}})
        return httpx.Response(200, json={"message": "ok"})

    adapter = ErpNextAdapter("http://erpnext:8000", "k", "s", client=_erpnext_client(handler))
    receipt = adapter.push_journal(make_entry(), {})
    assert receipt.external_ref == "JV-0001"
    assert adapter.health() is True


def test_erpnext_adapter_401_fails():
    def handler(request):
        return httpx.Response(401, json={"message": "unauthorized"})

    adapter = ErpNextAdapter("http://erpnext:8000", "k", "bad", client=_erpnext_client(handler))
    with pytest.raises(ErpPushError, match="401"):
        adapter.push_journal(make_entry(), {})


# ---------------- IFMIS export ----------------

def test_ifmis_export_writes_deterministic_artifacts(tmp_path):
    adapter = IfmisExportAdapter(out_dir=str(tmp_path))
    entry = make_entry()
    r1 = adapter.push_journal(entry, {"1000_CASH_TREASURY": "ERP-CASH"})
    r2 = adapter.push_journal(make_entry(), {"1000_CASH_TREASURY": "ERP-CASH"})
    assert r1.receipt_id == r2.receipt_id
    csv_path = tmp_path / TENANT / "2025-03-01_JE-1.csv"
    json_path = csv_path.with_suffix(".json")
    assert csv_path.exists() and json_path.exists()
    body = csv_path.read_text()
    assert "ERP-CASH" in body and "125000" in body
    assert adapter.health() is True


# ---------------- retry -> dead-letter ----------------

class FlakyAdapter:
    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.calls = 0

    def push_journal(self, entry, account_map):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ErpPushError("backend down")
        return ErpReceipt(
            receipt_id=f"FLAKY-{entry.hash[:8]}",
            backend=ErpBackend.FIXTURE,
            external_ref="flaky://1",
            entry_hash=entry.hash,
        )

    def health(self):
        return True


def test_retry_then_dead_letter():
    adapter = FlakyAdapter(fail_times=99)
    service = make_service(adapter=adapter, max_attempts=3)
    record = service.ingest(make_entry())
    assert record.status == PushStatus.RETRY_PENDING
    assert service.retry_depth() == 1
    service.process_retries()  # attempt 2
    assert service.retry_depth() == 1
    records = service.process_retries()  # attempt 3 -> dead letter
    assert records[-1].status == PushStatus.DEAD_LETTERED
    assert service.retry_depth() == 0
    assert len(service.dead_letters(TENANT)) == 1


def test_retry_recovers_before_dead_letter():
    adapter = FlakyAdapter(fail_times=1)
    service = make_service(adapter=adapter, max_attempts=3)
    service.ingest(make_entry())
    records = service.process_retries()
    assert records[-1].status == PushStatus.PUSHED
    assert not service.dead_letters()


# ---------------- API: tenant isolation / 404 ----------------

def make_client() -> TestClient:
    return TestClient(create_app(service=make_service()))


def _payload(entry_id="JE-API-1", source="api-1"):
    return {
        "entry_id": entry_id,
        "date": "2025-03-01",
        "memo": "api push",
        "source_event_id": source,
        "lines": [
            {"account_code": "1000_CASH_TREASURY", "debit_kobo": 5000},
            {"account_code": "3000_CRF", "credit_kobo": 5000},
        ],
    }


def test_api_push_get_and_receipt():
    client = make_client()
    resp = client.post(f"/erp/v1/states/{TENANT}/journals", json=_payload())
    assert resp.status_code == 201, resp.text
    assert resp.json()["record"]["status"] == "PUSHED"
    detail = client.get(f"/erp/v1/states/{TENANT}/journals/JE-API-1")
    assert detail.status_code == 200
    assert detail.json()["receipt"]["backend"] == "fixture"
    replay = client.post(f"/erp/v1/states/{TENANT}/journals", json=_payload(entry_id="JE-API-2"))
    assert replay.json()["record"]["status"] == "DEDUPED"


def test_api_tenant_isolation_and_unknown_state_404():
    client = make_client()
    client.post(f"/erp/v1/states/{TENANT}/journals", json=_payload())
    # Other tenant cannot see lagos journals.
    assert client.get(f"/erp/v1/states/{OTHER}/journals").json() == []
    assert client.get(f"/erp/v1/states/{OTHER}/journals/JE-API-1").status_code == 404
    # Unknown state -> 404 on every surface.
    assert client.post("/erp/v1/states/kano/journals", json=_payload()).status_code == 404
    assert client.get("/erp/v1/states/kano/journals").status_code == 404
    assert client.get("/erp/v1/states/kano/coa-mapping").status_code == 404
    assert client.get("/erp/v1/states/kano/outbound-log/verify").status_code == 404


def test_api_coa_mapping_and_chain_verify_and_healthz():
    client = make_client()
    resp = client.put(
        f"/erp/v1/states/{TENANT}/coa-mapping", json={"mapping": {"X": "Y"}}
    )
    assert resp.status_code == 200
    assert client.get(f"/erp/v1/states/{TENANT}/coa-mapping").json()["mapping"] == {"X": "Y"}
    client.post(f"/erp/v1/states/{TENANT}/journals", json=_payload())
    verify = client.get(f"/erp/v1/states/{TENANT}/outbound-log/verify").json()
    assert verify["valid"] is True and verify["records"] == 1
    health = client.get("/healthz").json()
    assert health["status"] == "ok" and health["adapter_health"] is True


# ---------------- settlement event ingestion ----------------

def test_settlement_event_creates_balanced_journal():
    from _shared.eventbus import InMemoryEventBus

    bus = InMemoryEventBus()
    service = make_service(bus=bus)

    class Settlement(BaseModel):
        state_id: str
        bill_reference: str
        gross_amount_kobo: int
        splits: list
        tigerbeetle_transfer_ids: list
        timestamp: str

    payload = Settlement(
        state_id=TENANT,
        bill_reference="BILL-9",
        gross_amount_kobo=10000,
        splits=[
            {"beneficiary": "STATE_CONSOLIDATED_REVENUE_FUND", "amount_kobo": 7000},
            {"beneficiary": "PPP_CONCESSIONAIRE_ESCROW", "amount_kobo": 3000},
        ],
        tigerbeetle_transfer_ids=["1"],
        timestamp="2025-03-01T12:00:00Z",
    )
    bus.publish("ng.sos.payments.settlement_completed", payload)
    journals = service.list_journals(TENANT)
    assert len(journals) == 1
    entry = journals[0]
    assert sum(l.debit_kobo for l in entry.lines) == 10000
    # Replay of the same settlement is deduped.
    bus.publish("ng.sos.payments.settlement_completed", payload)
    assert len(service.list_journals(TENANT)) == 1
