"""Audit hash-chain tests (P1 workstream 4): genesis, continuity, tamper
detection, and chain survival across process restarts via LocalFileArchive."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))
from _shared.hashchain import GENESIS_PREV_HASH, verify_event_chain  # noqa: E402

from app.audit_archive import LocalFileArchive  # noqa: E402
from app.domain import AuditEvent, MetadataStore, TenantCreate  # noqa: E402


def _events_dicts(store: MetadataStore, tenant_id: str) -> list[dict]:
    return [e.model_dump() for e in store.audit_events() if e.tenant_id == tenant_id]


def test_genesis_event_anchors_tenant_chain(tmp_path) -> None:
    store = MetadataStore(archive=LocalFileArchive(tmp_path))
    tenant = store.create_tenant(TenantCreate(state="ogun", tier="shared"))

    chain = _events_dicts(store, tenant.tenant_id)
    genesis = chain[0]
    assert genesis["event_type"] == "ng.sos.audit.genesis"
    assert genesis["prev_hash"] == GENESIS_PREV_HASH
    assert genesis["event_hash"]
    # Exactly one genesis event per tenant, even after more appends.
    store.suspend_tenant(tenant.tenant_id, "check", actor="tester")
    chain = _events_dicts(store, tenant.tenant_id)
    assert [e["event_type"] for e in chain].count("ng.sos.audit.genesis") == 1
    assert verify_event_chain(chain) == []


def test_hash_chain_links_and_verify_clean(tmp_path) -> None:
    store = MetadataStore(archive=LocalFileArchive(tmp_path))
    tenant = store.create_tenant(TenantCreate(state="osun", tier="shared"))
    store.suspend_tenant(tenant.tenant_id, "audit test", actor="tester")

    chain = _events_dicts(store, tenant.tenant_id)
    assert len(chain) >= 3
    for prev, cur in zip(chain, chain[1:]):
        assert cur["prev_hash"] == prev["event_hash"]
    assert verify_event_chain(chain) == []


def test_tampered_event_is_detected(tmp_path) -> None:
    store = MetadataStore(archive=LocalFileArchive(tmp_path))
    tenant = store.create_tenant(TenantCreate(state="benue", tier="shared"))
    store.suspend_tenant(tenant.tenant_id, "reason", actor="tester")

    archive = LocalFileArchive(tmp_path)
    chain = archive.read_all(tenant.tenant_id)
    assert verify_event_chain(chain) == []

    # Mutate one archived event in place (attacker edits the JSONL).
    chain[1]["detail"] = {"forged": True}
    errors = verify_event_chain(chain)
    assert errors and "tampered" in errors[0]

    # Deleting an event also breaks prev_hash continuity.
    truncated = chain[:1] + chain[2:]
    assert verify_event_chain(truncated)


def test_tamper_detected_from_archive_file_bytes(tmp_path) -> None:
    """Rewrite the on-disk JSONL with a forged actor; reload + verify fails."""
    store = MetadataStore(archive=LocalFileArchive(tmp_path))
    tenant = store.create_tenant(TenantCreate(state="lagos", tier="dedicated"))

    path = tmp_path / f"{tenant.tenant_id}.jsonl"
    lines = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    lines[-1]["actor"] = "mallory"
    path.write_text("".join(json.dumps(l, sort_keys=True, separators=(",", ":")) + "\n"
                            for l in lines))

    reloaded = LocalFileArchive(tmp_path).read_all(tenant.tenant_id)
    assert verify_event_chain(reloaded)


def test_chain_continuity_across_restart_via_archive_reload(tmp_path) -> None:
    """Process 'restart': first store writes the archive; a fresh store (new
    in-memory state) reads the archive and the full chain still verifies."""
    store1 = MetadataStore(archive=LocalFileArchive(tmp_path))
    tenant = store1.create_tenant(TenantCreate(state="nasarawa", tier="shared"))
    store1.suspend_tenant(tenant.tenant_id, "pre-restart", actor="tester")

    # Restart: new MetadataStore, empty memory; archive is the source of truth.
    store2 = MetadataStore(archive=LocalFileArchive(tmp_path))
    assert store2.audit_events() == []
    reloaded = store2._archive.read_all(tenant.tenant_id)
    assert [e["event_type"] for e in reloaded][0] == "ng.sos.audit.genesis"
    assert verify_event_chain(reloaded) == []

    # In-memory events and archived events agree hash-for-hash.
    assert reloaded == _events_dicts(store1, tenant.tenant_id)


def test_audit_event_model_roundtrip() -> None:
    event = AuditEvent(seq=1, event_id="evt-000001", event_type="t",
                       tenant_id="tn-x", actor="a", detail={}, at="now")
    assert event.prev_hash == GENESIS_PREV_HASH
