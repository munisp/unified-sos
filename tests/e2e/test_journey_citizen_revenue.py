"""Journey 1 — citizen revenue end-to-end (Stage 7.B, in-process tier).

    portal service request (+ USSD walk) -> KYC case approved (fixtures)
    -> FSPIOP fixture payment -> ledger split posted to the in-memory
    TigerBeetle-fake (rev-core semantics) -> control-plane audit chain
    -> `sosctl audit verify-chain` logic -> transparency feed (redacted).

All services run in-process over httpx ASGI transports; with
``E2E_STACK=live`` the HTTP legs run against the compose stack instead.
"""

from __future__ import annotations

import importlib
import json
from uuid import uuid4

import pytest

from conftest import (
    STATE,
    TEST_TELCO_SECRET,
    TIMING_END_OF_MONTH,
    TIMING_INSTANT,
    TransferExistsError,
    build_control_plane,
    compute_split,
    load_service_app,
    register_service_alias,
)

#: Deterministic ledger account IDs for the journey (mirrors the
#: chart-of-accounts classes in ledger/chart-of-accounts.md).
ACCT_PAYER_CLEARING = 100_001
ACCT_STATE_REVENUE = 200_001
ACCT_MDA_SHARE = 200_002
ACCT_PLATFORM_FEE = 200_003

#: Gazetted split for the journey: 70% state IGR, 25% MDA, 5% platform
#: (INSTANT), plus a 100% END_OF_MONTH reporting leg excluded from the chain.
SPLIT_RULES = [
    (ACCT_STATE_REVENUE, 7_000, TIMING_INSTANT),
    (ACCT_MDA_SHARE, 2_500, TIMING_INSTANT),
    (ACCT_PLATFORM_FEE, 500, TIMING_INSTANT),
    (ACCT_STATE_REVENUE, 10_000, TIMING_END_OF_MONTH),
]

GROSS_KOBO = 123_456_789  # deliberately not BPS-aligned to exercise rounding


def _ussd_turn(portal_client, session_id: str, text: str) -> str:
    resp = portal_client.post(
        "/channels/ussd/callback",
        params={"state_id": STATE},
        data={"sessionId": session_id, "phoneNumber": "+2348012345678", "text": text},
        headers={"x-telco-secret": TEST_TELCO_SECRET},
    )
    assert resp.status_code == 200, resp.text
    return resp.text


def test_citizen_revenue_journey(
    portal_client, kyc_client, transparency_client, ledger, tmp_path
):
    # ------------------------------------------------------------------
    # 0. Control plane: tenant for the journey state (emits the audit chain).
    # ------------------------------------------------------------------
    cp_app, store, archive_root = build_control_plane("cp_j1", tmp_path)
    from conftest import SyncASGIClient

    cp_client = SyncASGIClient(cp_app)
    resp = cp_client.post(
        "/control/v1/tenants",
        json={"state": STATE, "tier": "hybrid"},
        headers={"authorization": "Bearer e2e-operator"},
    )
    assert resp.status_code == 202, resp.text
    tenant_id = resp.json()["tenant_id"]
    assert resp.json()["status"] == "active"

    # ------------------------------------------------------------------
    # 1. Citizen submits a service request via the portal.
    # ------------------------------------------------------------------
    resp = portal_client.post(
        "/citizen/v1/wallets", json={"state_id": STATE, "nin": "12345678901"}
    )
    assert resp.status_code == 201, resp.text
    wallet = resp.json()
    assert "12345678901" not in json.dumps(wallet)  # NIN never round-trips

    resp = portal_client.get("/citizen/v1/services", params={"state_id": STATE})
    assert resp.status_code == 200
    catalog = resp.json()
    assert catalog, "catalog must be seeded for the state"
    service_code = catalog[0]["service_code"]

    resp = portal_client.post(
        "/citizen/v1/service-requests",
        json={
            "state_id": STATE,
            "wallet_id": wallet["wallet_id"],
            "service_code": service_code,
            "form_payload": {"purpose": "e2e-journey"},
        },
    )
    assert resp.status_code == 201, resp.text
    service_request = resp.json()
    assert service_request["status"] == "SUBMITTED"

    # ------------------------------------------------------------------
    # 1b. The same request type is walkable over USSD (telco webhook).
    # ------------------------------------------------------------------
    session = f"s7e2e-{uuid4().hex[:8]}"
    turn = _ussd_turn(portal_client, session, "")
    assert turn.startswith("CON "), turn
    assert "Select a service category" in turn
    turn = _ussd_turn(portal_client, session, "1")  # first category
    assert turn.startswith("CON ") and "Select a service" in turn, turn
    turn = _ussd_turn(portal_client, session, "1")  # first service
    assert turn.startswith("CON ") and "Confirm" in turn, turn
    turn = _ussd_turn(portal_client, session, "1")  # confirm -> submit
    assert turn.startswith("END ") and "Reference:" in turn, turn
    ussd_ref = turn.split("Reference:")[1].split("\n")[0].strip()
    resp = portal_client.get(
        f"/citizen/v1/service-requests/{ussd_ref}", params={"state_id": STATE}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["service_code"]  # channel request persisted

    # ------------------------------------------------------------------
    # 2. KYC case created and approved (deterministic fixture adapters).
    # ------------------------------------------------------------------
    resp = kyc_client.post(
        "/kyc/v1/cases",
        json={
            "state_id": STATE,
            "subject_ref": wallet["wallet_id"],
            "actor": "e2e-journey",
            "required_documents": [],
            "liveness_required": False,
        },
    )
    assert resp.status_code == 201, resp.text
    case = resp.json()
    assert wallet["wallet_id"] not in json.dumps(case)  # subject pseudonymised

    resp = kyc_client.post(
        f"/kyc/v1/cases/{case['case_id']}/submit",
        json={"state_id": STATE, "actor": "e2e-journey"},
    )
    assert resp.status_code == 200, resp.text
    case = resp.json()
    if case["status"] != "APPROVED":  # low-risk auto-approve is on by default
        resp = kyc_client.post(
            f"/kyc/v1/cases/{case['case_id']}/review",
            json={
                "state_id": STATE,
                "decision": "APPROVE",
                "reviewer": "e2e-reviewer",
                "reason": "fixture evidence clean",
            },
        )
        assert resp.status_code == 200, resp.text
        case = resp.json()
    assert case["status"] == "APPROVED", case

    resp = kyc_client.get("/kyc-kyb/v1/audit/verify", params={"state_id": STATE})
    assert resp.status_code == 200 and resp.json()["valid"] is True

    # ------------------------------------------------------------------
    # 3. Assessment/payment via the FSPIOP fixture adapter.
    # ------------------------------------------------------------------
    root_alias = register_service_alias("switch", "mod-mobility-switch")
    fixtures = importlib.import_module(f"{root_alias}.app.adapters.fixtures")
    fspiop = fixtures.FixtureFspiopAdapter()

    transfer_id = f"e2e-{uuid4().hex[:12]}"
    party = fspiop.party_lookup("MSISDN", "8012345678")
    assert party["party"]["fspId"] == "fixture-fsp"
    quote = fspiop.quote(transfer_id, GROSS_KOBO, payer="citizen", payee="state-igr")
    assert int(quote["transferAmountMinor"]) == GROSS_KOBO
    prepared = fspiop.transfer_prepare(
        transfer_id, GROSS_KOBO, quote["condition"], "2026-12-31T00:00:00Z"
    )
    assert prepared["state"] == "PENDING"
    fulfilled = fspiop.transfer_fulfil(transfer_id, fulfilment=None)
    assert fulfilled["transferState"] == "COMMITTED"
    # Idempotent replay of the fulfilment returns the identical record.
    assert fspiop.transfer_fulfil(transfer_id, fulfilment=None) == fulfilled

    # ------------------------------------------------------------------
    # 4. Ledger split posted to the in-memory TigerBeetle-fake
    #    (rev-core REV_CORE_LEDGER=memory semantics, Go splits port).
    # ------------------------------------------------------------------
    for acct in (ACCT_PAYER_CLEARING, ACCT_STATE_REVENUE, ACCT_MDA_SHARE, ACCT_PLATFORM_FEE):
        ledger.create_account(acct)
    ledger.seed_account(ACCT_PAYER_CLEARING, GROSS_KOBO)  # settlement proceeds

    plan = compute_split(GROSS_KOBO, SPLIT_RULES)
    assert sum(plan["instant"].values()) + plan["remainder"] == GROSS_KOBO
    batch_id = 42
    ledger.post_atomic_split(batch_id, ACCT_PAYER_CLEARING, plan)
    for acct, amount in plan["instant"].items():
        assert ledger.balance(acct) == amount
    assert ledger.balance(ACCT_PAYER_CLEARING) == plan["remainder"]
    # Idempotency: replaying the same batch must be rejected, not double-post.
    with pytest.raises(TransferExistsError):
        ledger.post_atomic_split(batch_id, ACCT_PAYER_CLEARING, plan)

    # ------------------------------------------------------------------
    # 5. Control-plane audit chain: emitted, archived, and verified intact
    #    with the exact logic behind `sosctl audit verify-chain`.
    # ------------------------------------------------------------------
    import sosctl.audit as sosctl_audit

    errors = sosctl_audit.verify_tenant_chain(archive_root, tenant_id)
    assert errors == [], errors
    events = sosctl_audit.load_tenant_chain(archive_root, tenant_id)
    assert any(e["event_type"] == "ng.sos.tenant.provisioned" for e in events)
    assert len({e["event_hash"] for e in events}) == len(events)

    # ------------------------------------------------------------------
    # 6. Transparency feed exposes the public projection — with no PII.
    # ------------------------------------------------------------------
    resp = transparency_client.get(f"/transparency/v1/{STATE}/trust-fund/feed")
    assert resp.status_code == 200, resp.text
    feed = resp.json()
    assert feed["entries"], "fixture feed must be non-empty for the state"
    raw = json.dumps(feed)
    # Raw donor references are fixture seeds in mod-transparency's source;
    # the public feed must only carry their SHA-256 pseudonyms.
    for pii in ("donor-ref-alpha", "donor-ref-beta"):
        assert pii not in raw, f"PII leak in transparency feed: {pii}"
    for entry in feed["entries"]:
        assert set(entry) <= {
            "entry_id", "state_id", "kind", "amount_kobo", "occurred_at",
            "donor_alias_hash", "purpose_label", "cursor",
        }, f"unexpected field on public feed entry: {set(entry)}"
        assert entry["cursor"], "hash-chain cursor required on every entry"
