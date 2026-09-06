"""AsyncAPI contract tests (F-053 / Stage 1 FAT).

Blocking, deterministic, fully local. Emits real event payloads through the
mod-mining and mod-forestry services wired to the shared in-memory event bus
(the same fixtures their own suites use) and validates each emitted envelope
against both:

- the committed AsyncAPI YAML channel payloads (contracts/asyncapi/*.yaml), and
- the generated registry JSON Schemas (contracts/asyncapi/registry/schemas/).

Every topic a service publishes must resolve in the generated topic registry —
no silent skips.
"""
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from apputil import REPO_ROOT, load_service_app

REGISTRY_DIR = Path(REPO_ROOT) / "contracts" / "asyncapi" / "registry"
ASYNCAPI_DIR = Path(REPO_ROOT) / "contracts" / "asyncapi"

MINING_FIXTURES = {
    "site": {
        "site_id": "SITE-NAS-KOKO-01",
        "state_id": "nasarawa",
        "mine_lease_id": "ML-NAS-KOKO-04",
        "operator_name": "Koko Lithium Ltd",
        "minerals": ["LITHIUM_SPODUMENE", "TANTALITE"],
    },
    "consignment": {
        "consignment_id": "MIN-NAS-2026-0819",
        "state_id": "nasarawa",
        "site_id": "SITE-NAS-KOKO-01",
        "mine_lease_id": "ML-NAS-KOKO-04",
        "mineral_type": "LITHIUM_SPODUMENE",
        "truck_registration": "NSR-412-XA",
        "rfid_seal_id": "RFID-881290-09",
        "destination_corridor": "PORT_LAGOS_APAPA",
    },
    "weighbridge": {
        "station_id": "WB-KOKO-01",
        "gross_weight_kg": 32_450.0,
        "tare_weight_kg": 12_100.0,
        "axle_weights_kg": [8_100, 8_050, 8_200, 8_100],
        "anpr_plate": "NSR-412-XA",
    },
    "assay": {"assay_id": "ASY-0001", "lab_id": "LAB-JOS-02", "lithium_oxide_grade_pct": 5.85},
}


def _load_registry():
    topics = json.loads((REGISTRY_DIR / "topics.json").read_text())
    return {entry["channel"]: entry for entry in topics["channels"]}


def _channel_payload_schema(channel: str) -> dict:
    """Extract the payload JSON Schema for a channel from the AsyncAPI YAMLs."""
    for contract in sorted(ASYNCAPI_DIR.glob("*.yaml")):
        doc = next(
            d for d in yaml.safe_load_all(contract.read_text()) if d and "asyncapi" in d
        )
        node = (doc.get("channels") or {}).get(channel)
        if node:
            op = node.get("publish") or node.get("subscribe") or {}
            return op["message"]["payload"]
    raise AssertionError(f"channel {channel!r} not found in contracts/asyncapi/*.yaml")


def _assert_valid_envelope(topic: str, payload: dict):
    """Validate one emitted bus envelope against YAML payload + registry schema."""
    registry = _load_registry()
    assert topic in registry, f"topic {topic!r} not in generated registry topics.json"
    entry = registry[topic]
    assert entry["topic"] == topic

    # 1. Cross-check against the committed AsyncAPI YAML payload schema.
    yaml_schema = _channel_payload_schema(topic)
    Draft202012Validator(yaml_schema).validate(payload)

    # 2. Cross-check against the generated registry JSON Schema.
    registry_schema = json.loads((REGISTRY_DIR / entry["schema"]).read_text())
    Draft202012Validator(registry_schema).validate(payload)


def _dispatch_mining_consignment():
    mining = load_service_app("mod-mining")
    from app.bus import InMemoryEventBus  # noqa: E402 — service-dir import

    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    bus = InMemoryEventBus()
    app = mining.create_app(bus=bus)
    fx = MINING_FIXTURES
    with TestClient(app) as c:
        assert c.post("/sites", json=fx["site"]).status_code == 201
        assert c.post("/consignments", json=fx["consignment"]).status_code == 201
        cid = fx["consignment"]["consignment_id"]
        assert c.post(f"/consignments/{cid}/weighbridge", json=fx["weighbridge"]).status_code == 200
        assert c.post(f"/consignments/{cid}/assay", json=fx["assay"]).status_code == 200
        assert c.post(f"/consignments/{cid}/dispatch").status_code == 200
    return bus


def _report_untagged_haulage():
    mining = load_service_app("mod-mining")  # provides the app.bus shim
    from app.bus import InMemoryEventBus  # noqa: E402 — shared bus via shim

    forestry = load_service_app("mod-forestry")
    from app.service import ForestryService  # noqa: E402 — forestry service

    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    bus = InMemoryEventBus()
    app = forestry.create_app(ForestryService(bus=bus))
    report = {
        "state_id": "taraba",
        "checkpoint_id": "CP-TAR-GEMBU-02",
        "vehicle_plate": "TRB-552-QA",
        "gps": {"lat": 6.85, "lon": 10.95},
    }
    with TestClient(app) as c:
        r = c.post("/alerts/untagged-haulage", json=report)
        assert r.status_code == 201, r.text
    return bus


def test_mining_consignment_dispatched_envelope_matches_contract():
    bus = _dispatch_mining_consignment()
    emitted = [e for e in bus.published if e["topic"] == "ng.sos.mining.consignment_dispatched"]
    assert emitted, "dispatch published no ng.sos.mining.consignment_dispatched event"
    for envelope in emitted:
        _assert_valid_envelope(envelope["topic"], envelope["payload"])


def test_forestry_untagged_timber_alert_envelope_matches_contract():
    bus = _report_untagged_haulage()
    emitted = [e for e in bus.published if e["topic"] == "ng.sos.forestry.untagged_timber_alert"]
    assert emitted, "untagged haulage published no ng.sos.forestry.untagged_timber_alert event"
    for envelope in emitted:
        _assert_valid_envelope(envelope["topic"], envelope["payload"])


def test_every_published_topic_is_registry_mapped():
    """No service in this suite may publish to an unregistered topic."""
    buses = [_dispatch_mining_consignment(), _report_untagged_haulage()]
    registry = _load_registry()
    topics = {e["topic"] for bus in buses for e in bus.published}
    assert topics, "no events emitted — fixtures regressed"
    assert topics <= set(registry), f"unregistered topics: {topics - set(registry)}"
