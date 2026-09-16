"""Legal/conveyancing layer tests: transfers, encumbrances, lifecycle-aware
deed verification, subdivision/merger ownership locks, overlap on REGISTERED
children, probate transmissions, court orders, revocation + compensation,
titling segregation of duties, and tenant isolation on all new endpoints."""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lands_app.main import create_app
from lands_app.revocation import FixtureCompensationLedger
from tests.helpers import BASE_LAT, BASE_LON, registration_body, square_geojson

OGUN_H = {"X-State-Tenant": "ogun"}
LAGOS_H = {"X-State-Tenant": "lagos"}
NOW = datetime.now(timezone.utc)
FUTURE = (NOW + timedelta(days=30)).isoformat()
PAST = (NOW - timedelta(days=1)).isoformat()

TITLING_ACTORS = ("surveyor:t", "ministry:t", "ag:t", "governor:t")


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture()
def ledger() -> FixtureCompensationLedger:
    return FixtureCompensationLedger()


@pytest.fixture()
def ledger_client(ledger) -> TestClient:
    return TestClient(create_app(ledger_adapter=ledger))


def _register(client: TestClient, uin: str, lon0: float = BASE_LON, tenant: str = "ogun",
              lat0: float = BASE_LAT, **overrides) -> dict:
    body = registration_body(uin, square_geojson(lon0, lat0), **overrides)
    resp = client.post(f"/api/v1/states/{tenant}/cadastre/parcels", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _issue_title(client: TestClient, parcel: dict, tenant: str = "ogun") -> str:
    """Approve all titling stages (role-correct actors) → C-of-O number."""
    wf = parcel["titling_workflow_id"]
    for actor in TITLING_ACTORS:
        resp = client.post(
            f"/api/v1/states/{tenant}/cadastre/titling/{wf}/decisions",
            json={"approved": True, "actor": actor},
        )
        assert resp.status_code == 200, resp.text
    status = client.get(f"/api/v1/states/{tenant}/cadastre/titling/{wf}").json()
    assert status["status"] == "ISSUED"
    return status["c_of_o_number"]


def _issued(client, uin, lon0=BASE_LON, tenant="ogun", lat0=BASE_LAT, **overrides):
    parcel = _register(client, uin, lon0, tenant, lat0, **overrides)
    c_of_o = _issue_title(client, parcel, tenant)
    return parcel, c_of_o


def _verify(client, c_of_o: str, tenant: str = "ogun") -> dict:
    return client.post(
        f"/api/v1/states/{tenant}/cadastre/deeds/verify",
        json={"c_of_o_number": c_of_o},
    ).json()


def _issue_consent(client, parcel_id: str, tenant: str = "ogun",
                   expires_at: str = FUTURE) -> str:
    resp = client.post(
        f"/api/v1/states/{tenant}/cadastre/consents",
        json={"parcel_id": parcel_id, "expires_at": expires_at},
        headers={"X-State-Tenant": tenant},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["consent_id"]


def _apply_transfer(client, parcel, instrument, new_owner, tenant="ogun",
                    doc="doc-deed-1", transferor=None):
    owner = transferor or "STIN-OG-0001234"
    resp = client.post(
        f"/api/v1/states/{tenant}/cadastre/parcels/{parcel['parcel_id']}/transfers",
        json={
            "instrument_type": instrument,
            "transferor_stin": owner,
            "transferee_stin": new_owner,
            "evidence_document_id": doc,
        },
        headers={"X-State-Tenant": tenant},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["transfer_id"]


def _advance_transfer(client, tid, tenant="ogun", actor="registrar:t1", **kw):
    return client.post(
        f"/api/v1/states/{tenant}/cadastre/transfers/{tid}/advance",
        json={"actor": actor, **kw},
        headers={"X-State-Tenant": tenant},
    )


def _run_transfer(client, parcel, instrument, new_owner, tenant="ogun",
                  consent_id=None, **kw):
    """Full happy-path transfer; returns the final application dict."""
    tid = _apply_transfer(client, parcel, instrument, new_owner, tenant, **kw)
    assert _advance_transfer(client, tid, tenant).status_code == 200  # evidence
    resp = _advance_transfer(client, tid, tenant, consent_id=consent_id)  # consent
    assert resp.status_code == 200, resp.text
    assert _advance_transfer(client, tid, tenant).status_code == 200  # tax
    final = _advance_transfer(client, tid, tenant)  # registered
    assert final.status_code == 200, final.text
    body = final.json()
    assert body["stage"] == "REGISTERED"
    return body


def _add_encumbrance(client, parcel_id, tenant="ogun", **kw):
    body = {"type": "MORTGAGE", "instrument_hash": "a" * 64, "actor": "bank:t"}
    body.update(kw)
    return client.post(
        f"/api/v1/states/{tenant}/cadastre/parcels/{parcel_id}/encumbrances",
        json=body, headers={"X-State-Tenant": tenant},
    )


def _rect(lon0: float, lat0: float, width: float, height: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[[lon0, lat0], [lon0 + width, lat0],
                         [lon0 + width, lat0 + height], [lon0, lat0 + height],
                         [lon0, lat0]]],
    }


def _halves(uin_prefix: str, lon0: float = BASE_LON, lat0: float = BASE_LAT,
            size: float = 0.001) -> list[dict]:
    """Two side-by-side child specs exactly tiling a square parcel."""
    half = size / 2
    return [
        {"parcel_uin": f"{uin_prefix}-A", "boundary_geojson": _rect(lon0, lat0, half, size)},
        {"parcel_uin": f"{uin_prefix}-B", "boundary_geojson": _rect(lon0 + half, lat0, half, size)},
    ]


# ---------------------------------------------------------------------------
# 1. Transfer workflow — happy path per instrument
# ---------------------------------------------------------------------------


class TestTransferHappyPath:
    @pytest.mark.parametrize(
        "instrument,needs_consent",
        [("SALE", True), ("GIFT", False), ("ASSENT", False),
         ("COURT_ORDER", False), ("FORECLOSURE_SALE", True), ("PARTITION", True)],
    )
    def test_full_transfer_per_instrument(self, client, instrument, needs_consent):
        parcel, old_c_of_o = _issued(client, f"OG-TR-{instrument}")
        consent = (
            _issue_consent(client, parcel["parcel_id"]) if needs_consent else None
        )
        result = _run_transfer(
            client, parcel, instrument, "STIN-OG-9999", consent_id=consent
        )
        assert result["instrument_type"] == instrument
        assert result["transfer_jws"]
        assert result["previous_title_hash"]
        new_c_of_o = result["new_c_of_o_number"]
        assert new_c_of_o and new_c_of_o != old_c_of_o

        # Ownership updated atomically on the parcel row.
        updated = client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}"
        ).json()
        assert updated["c_of_o_number"] == new_c_of_o

        # TITLE_TRANSFERRED recorded in the hash-chained event log.
        history = client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/history",
            headers=OGUN_H,
        ).json()
        transferred = [
            e for e in history["chain_of_title"]
            if e["instrument"] == "TITLE_TRANSFERRED"
        ]
        assert transferred and new_c_of_o in str(transferred[0]["detail"])

        # New title verifies; the old title is REPLACED.
        assert _verify(client, new_c_of_o)["valid"] is True
        old = _verify(client, old_c_of_o)
        assert old["valid"] is False
        assert old["reference"] == new_c_of_o

    def test_sale_requires_consent(self, client):
        parcel, _ = _issued(client, "OG-TR-NOCONSENT")
        tid = _apply_transfer(client, parcel, "SALE", "STIN-OG-9999")
        assert _advance_transfer(client, tid).status_code == 200
        resp = _advance_transfer(client, tid)  # CONSENT without consent_id
        assert resp.status_code == 409


class TestTransferGuards:
    def test_transferor_must_match_current_owner(self, client):
        parcel, _ = _issued(client, "OG-TR-WRONGOR")
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/transfers",
            json={"instrument_type": "GIFT", "transferor_stin": "STIN-OG-OTHER",
                  "transferee_stin": "STIN-OG-9999",
                  "evidence_document_id": "doc-deed-1"},
            headers=OGUN_H,
        )
        assert resp.status_code == 409
        assert "owner" in resp.json()["detail"].lower()

    def test_unverified_evidence_document_fails_closed(self, client):
        parcel, _ = _issued(client, "OG-TR-BADDOC")
        tid = _apply_transfer(client, parcel, "GIFT", "STIN-OG-9999",
                              doc="not-a-real-doc")
        resp = _advance_transfer(client, tid)
        assert resp.status_code == 409
        assert "verification" in resp.json()["detail"].lower()

    def test_advance_beyond_registered_409(self, client):
        parcel, _ = _issued(client, "OG-TR-TERM")
        tid2 = _apply_transfer(client, parcel, "GIFT", "STIN-OG-8888")
        _advance_transfer(client, tid2)
        _advance_transfer(client, tid2)
        _advance_transfer(client, tid2)
        _advance_transfer(client, tid2)
        resp = _advance_transfer(client, tid2)
        assert resp.status_code == 409

    def test_unknown_transfer_404(self, client):
        resp = _advance_transfer(client, "transfer-ogun-999999")
        assert resp.status_code == 404


class TestConsentInstrument:
    def test_consent_expired_409(self, client):
        parcel, _ = _issued(client, "OG-CON-EXP")
        consent = _issue_consent(client, parcel["parcel_id"], expires_at=PAST)
        tid = _apply_transfer(client, parcel, "SALE", "STIN-OG-9999")
        _advance_transfer(client, tid)
        resp = _advance_transfer(client, tid, consent_id=consent)
        assert resp.status_code == 409
        assert "expired" in resp.json()["detail"].lower()

    def test_consent_single_use_409(self, client):
        parcel, _ = _issued(client, "OG-CON-1USE")
        consent = _issue_consent(client, parcel["parcel_id"])
        tid = _apply_transfer(client, parcel, "SALE", "STIN-OG-9999")
        _advance_transfer(client, tid)
        assert _advance_transfer(client, tid, consent_id=consent).status_code == 200
        # Second dealing with the same consent: consumed exactly once.
        parcel2, _ = _issued(client, "OG-CON-1USE-B", lon0=BASE_LON + 0.01)
        # consent is parcel-bound so reissue for parcel to isolate single-use
        tid2 = _apply_transfer(client, parcel2, "SALE", "STIN-OG-7777")
        _advance_transfer(client, tid2)
        resp = _advance_transfer(client, tid2, consent_id=consent)
        assert resp.status_code == 409  # wrong parcel (also still single-use)
        # Reuse on the same parcel after completion is impossible: consumed.
        consent_state = client.get(
            f"/api/v1/states/ogun/cadastre/consents/{consent}", headers=OGUN_H
        ).json()
        assert consent_state["consumed"] is True
        assert consent_state["consumed_by"] == tid

    def test_consent_wrong_parcel_409(self, client):
        parcel, _ = _issued(client, "OG-CON-WRONG")
        other, _ = _issued(client, "OG-CON-OTHER", lon0=BASE_LON + 0.01)
        consent = _issue_consent(client, other["parcel_id"])
        tid = _apply_transfer(client, parcel, "SALE", "STIN-OG-9999")
        _advance_transfer(client, tid)
        resp = _advance_transfer(client, tid, consent_id=consent)
        assert resp.status_code == 409

    def test_issue_consent_unknown_parcel_404(self, client):
        resp = client.post(
            "/api/v1/states/ogun/cadastre/consents",
            json={"parcel_id": "00000000-0000-0000-0000-000000000000",
                  "expires_at": FUTURE},
            headers=OGUN_H,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 2. Encumbrance register
# ---------------------------------------------------------------------------


class TestEncumbrances:
    def test_register_and_list(self, client):
        parcel, _ = _issued(client, "OG-ENC-LIST")
        resp = _add_encumbrance(client, parcel["parcel_id"], priority=2)
        assert resp.status_code == 201, resp.text
        enc = resp.json()
        assert enc["status"] == "ACTIVE"
        _add_encumbrance(client, parcel["parcel_id"], type="CAVEAT", priority=1)
        listing = client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/encumbrances",
            headers=OGUN_H,
        ).json()
        assert len(listing) == 2
        assert listing[0]["type"] == "CAVEAT"  # priority ordering

    def test_blocks_titling_decision_409(self, client):
        parcel = _register(client, "OG-ENC-TITLE")
        _add_encumbrance(client, parcel["parcel_id"])
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/titling/{parcel['titling_workflow_id']}/decisions",
            json={"approved": True, "actor": "surveyor:t"},
        )
        assert resp.status_code == 409

    def test_blocks_subdivision_409(self, client):
        parcel, _ = _issued(client, "OG-ENC-SUB")
        _add_encumbrance(client, parcel["parcel_id"])
        children = _halves("OG-ENC-SUB")
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/subdivide",
            json={"children": children}, headers=OGUN_H,
        )
        assert resp.status_code == 409

    def test_blocks_merger_409(self, client):
        p1, _ = _issued(client, "OG-ENC-MRG-A")
        p2, _ = _issued(client, "OG-ENC-MRG-B", lon0=BASE_LON + 0.001)
        _add_encumbrance(client, p1["parcel_id"])
        resp = client.post(
            "/api/v1/states/ogun/cadastre/parcels/merge",
            json={
                "parent_parcel_ids": [p1["parcel_id"], p2["parcel_id"]],
                "child": {"parcel_uin": "OG-ENC-MRG-C",
                          "boundary_geojson": {
                              "type": "Polygon",
                              "coordinates": [[[BASE_LON, BASE_LAT],
                                               [BASE_LON + 0.002, BASE_LAT],
                                               [BASE_LON + 0.002, BASE_LAT + 0.001],
                                               [BASE_LON, BASE_LAT + 0.001],
                                               [BASE_LON, BASE_LAT]]]}},
            },
            headers=OGUN_H,
        )
        assert resp.status_code == 409

    def test_blocks_transfer_409(self, client):
        parcel, _ = _issued(client, "OG-ENC-TRF")
        _add_encumbrance(client, parcel["parcel_id"])
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/transfers",
            json={"instrument_type": "GIFT", "transferor_stin": "STIN-OG-0001234",
                  "transferee_stin": "STIN-OG-9999",
                  "evidence_document_id": "doc-deed-1"},
            headers=OGUN_H,
        )
        assert resp.status_code == 409

    def test_release_workflow_unblocks_transfer(self, client):
        parcel, _ = _issued(client, "OG-ENC-REL")
        enc = _add_encumbrance(client, parcel["parcel_id"]).json()
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/encumbrances/{enc['encumbrance_id']}/release",
            json={"actor": "bank:t", "reason": "mortgage discharged"}, headers=OGUN_H,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "RELEASED"
        # Double release is illegal.
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/encumbrances/{enc['encumbrance_id']}/release",
            json={"actor": "bank:t"}, headers=OGUN_H,
        )
        assert resp.status_code == 409
        # Guard cleared: transfer now succeeds.
        result = _run_transfer(client, parcel, "GIFT", "STIN-OG-9999")
        assert result["stage"] == "REGISTERED"

    def test_withdraw_workflow(self, client):
        parcel, _ = _issued(client, "OG-ENC-WD")
        enc = _add_encumbrance(client, parcel["parcel_id"], type="CAVEAT").json()
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/encumbrances/{enc['encumbrance_id']}/withdraw",
            json={"actor": "claimant:t"}, headers=OGUN_H,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "WITHDRAWN"

    def test_expired_encumbrance_lapses(self, client):
        parcel, _ = _issued(client, "OG-ENC-EXP")
        enc = _add_encumbrance(client, parcel["parcel_id"], expires_at=PAST).json()
        listing = client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/encumbrances",
            headers=OGUN_H,
        ).json()
        assert listing == []  # ACTIVE list excludes the lapsed entry
        full = client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/encumbrances",
            params={"include_closed": True}, headers=OGUN_H,
        ).json()
        assert full[0]["status"] == "EXPIRED"


# ---------------------------------------------------------------------------
# 3. Lifecycle-aware verify_deed
# ---------------------------------------------------------------------------


class TestVerifyDeedLifecycle:
    def test_superseded_parcel_invalid(self, client):
        parcel, c_of_o = _issued(client, "OG-VD-SUP")
        # Subdivide: parent becomes SUPERSEDED.
        children = _halves("OG-VD-SUP")
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/subdivide",
            json={"children": children}, headers=OGUN_H,
        )
        assert resp.status_code == 201, resp.text
        result = _verify(client, c_of_o)
        assert result["valid"] is False
        assert result["parcel_status"] == "SUPERSEDED"

    def test_replaced_title_invalid_with_replacement_reference(self, client):
        parcel, old_c_of_o = _issued(client, "OG-VD-REPL")
        result = _run_transfer(client, parcel, "GIFT", "STIN-OG-9999")
        old = _verify(client, old_c_of_o)
        assert old["valid"] is False
        assert old["reference"] == result["new_c_of_o_number"]
        assert "REPLACED" in old["detail"]

    def test_revoked_title_invalid_with_revocation_reference(self, ledger_client):
        parcel, c_of_o = _issued(ledger_client, "OG-VD-REV")
        rev = ledger_client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/revocations",
            json={"public_purpose": "road expansion"}, headers=OGUN_H,
        ).json()
        rid = rev["revocation_id"]
        _advance_revocation_full(ledger_client, rid)
        result = _verify(ledger_client, c_of_o)
        assert result["valid"] is False
        assert result["parcel_status"] == "REVOKED"
        assert result["reference"] == rid


# ---------------------------------------------------------------------------
# 4. Subdivision/merger ownership lock
# ---------------------------------------------------------------------------


class TestOwnershipLock:
    def test_subdivision_owner_change_rejected_422(self, client):
        parcel, _ = _issued(client, "OG-OWN-SUB")
        children = _halves("OG-OWN-SUB")
        children[0]["owner_stin"] = "STIN-OG-SOMEONE-ELSE"
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/subdivide",
            json={"children": children}, headers=OGUN_H,
        )
        assert resp.status_code == 422

    def test_subdivision_children_inherit_parent_owner(self, client):
        parcel, _ = _issued(client, "OG-OWN-INH")
        children = _halves("OG-OWN-INH")
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/subdivide",
            json={"children": children}, headers=OGUN_H,
        )
        assert resp.status_code == 201, resp.text
        for child in resp.json()["children"]:
            record = client.get(
                f"/api/v1/states/ogun/cadastre/parcels/{child['parcel_id']}"
            ).json()
            assert record["status"] == "REGISTERED"
        history = client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{resp.json()['children'][0]['parcel_id']}/history",
            headers=OGUN_H,
        ).json()
        assert history["chain_of_title"][0]["owner_stin"] == "STIN-OG-0001234"

    def test_merger_differing_owners_422(self, client):
        p1, _ = _issued(client, "OG-OWN-MRG-A")
        p2, _ = _issued(client, "OG-OWN-MRG-B", lon0=BASE_LON + 0.001,
                        owner_stin="STIN-OG-5555555")
        resp = client.post(
            "/api/v1/states/ogun/cadastre/parcels/merge",
            json={
                "parent_parcel_ids": [p1["parcel_id"], p2["parcel_id"]],
                "child": {"parcel_uin": "OG-OWN-MRG-C",
                          "boundary_geojson": {
                              "type": "Polygon",
                              "coordinates": [[[BASE_LON, BASE_LAT],
                                               [BASE_LON + 0.002, BASE_LAT],
                                               [BASE_LON + 0.002, BASE_LAT + 0.001],
                                               [BASE_LON, BASE_LAT + 0.001],
                                               [BASE_LON, BASE_LAT]]]}},
            },
            headers=OGUN_H,
        )
        assert resp.status_code == 422
        assert "transfer" in resp.json()["detail"].lower()

    def test_merger_child_owner_override_rejected_422(self, client):
        p1, _ = _issued(client, "OG-OWN-MO-A")
        p2, _ = _issued(client, "OG-OWN-MO-B", lon0=BASE_LON + 0.001)
        resp = client.post(
            "/api/v1/states/ogun/cadastre/parcels/merge",
            json={
                "parent_parcel_ids": [p1["parcel_id"], p2["parcel_id"]],
                "child": {"parcel_uin": "OG-OWN-MO-C",
                          "owner_stin": "STIN-OG-5555555",
                          "boundary_geojson": {
                              "type": "Polygon",
                              "coordinates": [[[BASE_LON, BASE_LAT],
                                               [BASE_LON + 0.002, BASE_LAT],
                                               [BASE_LON + 0.002, BASE_LAT + 0.001],
                                               [BASE_LON, BASE_LAT + 0.001],
                                               [BASE_LON, BASE_LAT]]]}},
            },
            headers=OGUN_H,
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 5. Overlap status fix — subdivision children (REGISTERED) block overlaps
# ---------------------------------------------------------------------------


class TestOverlapRegisteredChildren:
    def test_overlap_against_subdivision_child_rejected(self, client):
        parcel, _ = _issued(client, "OG-OVL-PARENT")
        children = _halves("OG-OVL")
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/subdivide",
            json={"children": children}, headers=OGUN_H,
        )
        assert resp.status_code == 201, resp.text
        # A new registration overlapping child A (REGISTERED) must be rejected.
        resp = client.post(
            "/api/v1/states/ogun/cadastre/parcels",
            json=registration_body("OG-OVL-INTRUDER",
                                   square_geojson(BASE_LON + 0.0002, BASE_LAT, 0.0002)),
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 6. Probate / transmission
# ---------------------------------------------------------------------------


class TestTransmission:
    def test_multi_beneficiary_transmission(self, client):
        parcel, old_c_of_o = _issued(client, "OG-PROB-MULTI")
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/transmissions",
            json={"deceased_stin": "STIN-OG-0001234",
                  "death_certificate_doc_id": "doc-death-1",
                  "beneficiaries": ["STIN-OG-B1", "STIN-OG-B2"]},
            headers=OGUN_H,
        )
        assert resp.status_code == 201, resp.text
        case = resp.json()
        assert case["stage"] == "DEATH_REPORTED"
        tid_url = f"/api/v1/states/ogun/cadastre/transmissions/{case['transmission_id']}/advance"
        for actor in ("registrar:probate", "registrar:review", "ag:review", "registrar:register"):
            resp = client.post(tid_url, json={"actor": actor}, headers=OGUN_H)
            assert resp.status_code == 200, resp.text
        case = resp.json()
        assert case["stage"] == "TRANSMISSION_REGISTERED"
        assert case["transfer_id"]

        # Ownership updated through the transfer (ASSENT) code path.
        history = client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/history",
            headers=OGUN_H,
        ).json()
        transferred = [
            e for e in history["chain_of_title"]
            if e["instrument"] == "TITLE_TRANSFERRED"
        ][0]
        detail = str(transferred["detail"])
        assert "'instrument_type': 'ASSENT'" in detail
        assert "STIN-OG-B1+STIN-OG-B2" in detail
        # Old title replaced; the new title verifies.
        old = _verify(client, old_c_of_o)
        assert old["valid"] is False
        assert _verify(client, old["reference"])["valid"] is True

    def test_unverified_death_certificate_409(self, client):
        parcel, _ = _issued(client, "OG-PROB-BADDOC")
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/transmissions",
            json={"deceased_stin": "STIN-OG-0001234",
                  "death_certificate_doc_id": "forged-cert",
                  "beneficiaries": ["STIN-OG-B1"]},
            headers=OGUN_H,
        )
        assert resp.status_code == 409

    def test_deceased_must_match_owner_409(self, client):
        parcel, _ = _issued(client, "OG-PROB-WRONG")
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/transmissions",
            json={"deceased_stin": "STIN-OG-OTHER",
                  "death_certificate_doc_id": "doc-death-1",
                  "beneficiaries": ["STIN-OG-B1"]},
            headers=OGUN_H,
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 7. Court orders
# ---------------------------------------------------------------------------


def _file_order(client, parcel_id, order_type, tenant="ogun", **kw):
    body = {
        "parcel_id": parcel_id,
        "order_type": order_type,
        "order_number": "FHC/ABJ/2026/001",
        "court": "Federal High Court, Abeokuta",
        "instrument_hash": "b" * 64,
        "effective_date": "2026-02-01",
    }
    body.update(kw)
    resp = client.post(
        f"/api/v1/states/{tenant}/cadastre/court-orders",
        json=body, headers={"X-State-Tenant": tenant},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _approve_and_apply(client, order_id, tenant="ogun"):
    for role in ("registrar", "ag"):
        resp = client.post(
            f"/api/v1/states/{tenant}/cadastre/court-orders/{order_id}/approve",
            json={"actor": f"{role}:t", "role": role},
            headers={"X-State-Tenant": tenant},
        )
        assert resp.status_code == 200, resp.text
    return client.post(
        f"/api/v1/states/{tenant}/cadastre/court-orders/{order_id}/apply",
        json={"actor": "registrar:t"}, headers={"X-State-Tenant": tenant},
    )


class TestCourtOrders:
    def test_vest_title_via_transfer_path(self, client):
        parcel, old_c_of_o = _issued(client, "OG-CO-VEST")
        order = _file_order(client, parcel["parcel_id"], "VEST_TITLE",
                            new_owner_stin="STIN-OG-7777")
        assert order["status"] == "PENDING_APPROVALS"
        resp = _approve_and_apply(client, order["order_id"])
        assert resp.status_code == 200, resp.text
        applied = resp.json()
        assert applied["status"] == "APPLIED"
        assert applied["transfer_id"]
        history = client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/history",
            headers=OGUN_H,
        ).json()
        transferred = [
            e for e in history["chain_of_title"]
            if e["instrument"] == "TITLE_TRANSFERRED"
        ][0]
        detail = str(transferred["detail"])
        assert "'instrument_type': 'COURT_ORDER'" in detail
        assert "STIN-OG-7777" in detail
        assert _verify(client, old_c_of_o)["valid"] is False

    def test_apply_before_approvals_409(self, client):
        parcel, _ = _issued(client, "OG-CO-EARLY")
        order = _file_order(client, parcel["parcel_id"], "RECTIFY_OWNER",
                            new_owner_stin="STIN-OG-7777")
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/court-orders/{order['order_id']}/apply",
            json={"actor": "registrar:t"}, headers=OGUN_H,
        )
        assert resp.status_code == 409

    def test_duplicate_role_approval_409(self, client):
        parcel, _ = _issued(client, "OG-CO-DUP")
        order = _file_order(client, parcel["parcel_id"], "RECTIFY_OWNER",
                            new_owner_stin="STIN-OG-7777")
        body = {"actor": "registrar:t", "role": "registrar"}
        assert client.post(
            f"/api/v1/states/ogun/cadastre/court-orders/{order['order_id']}/approve",
            json=body, headers=OGUN_H,
        ).status_code == 200
        assert client.post(
            f"/api/v1/states/ogun/cadastre/court-orders/{order['order_id']}/approve",
            json=body, headers=OGUN_H,
        ).status_code == 409

    def test_boundary_rectification_preserves_snapshot(self, client):
        parcel, _ = _issued(client, "OG-CO-RECT")
        new_boundary = square_geojson(BASE_LON, BASE_LAT, 0.0012)
        order = _file_order(client, parcel["parcel_id"], "RECTIFY_BOUNDARY",
                            new_boundary_geojson=new_boundary)
        resp = _approve_and_apply(client, order["order_id"])
        assert resp.status_code == 200, resp.text
        updated = client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}"
        ).json()
        # Registry row reflects the rectified boundary (area changed).
        history = client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/history",
            headers=OGUN_H,
        ).json()
        rectified = [
            e for e in history["chain_of_title"]
            if e["instrument"] == "BOUNDARY_RECTIFIED"
        ][0]
        detail = str(rectified["detail"])
        assert "'pre_rectification_snapshot'" in detail
        # The snapshot preserves the pre-rectification boundary verbatim.
        assert str(square_geojson(BASE_LON, BASE_LAT)) in detail
        assert "'new_area_sqm'" in detail


# ---------------------------------------------------------------------------
# 8. Revocation + compensation
# ---------------------------------------------------------------------------


def _advance_revocation_full(client, rid, tenant="ogun"):
    """Drive a revocation through all stages; returns final case dict."""
    url = f"/api/v1/states/{tenant}/cadastre/revocations/{rid}/advance"
    resp = client.post(url, json={"actor": "ministry:verify"}, headers={"X-State-Tenant": tenant})
    assert resp.status_code == 200, resp.text  # PUBLIC_PURPOSE_VERIFIED
    resp = client.post(
        url,
        json={
            "actor": "valuer:assess",
            "line_items": [
                {"category": "LAND", "amount_kobo": 50_000_000},
                {"category": "IMPROVEMENTS", "amount_kobo": 20_000_000},
                {"category": "CROPS", "amount_kobo": 1_500_000},
                {"category": "DISTURBANCE", "amount_kobo": 500_000},
            ],
            "valuer": "valuer:assess",
        },
        headers={"X-State-Tenant": tenant},
    )
    assert resp.status_code == 200, resp.text  # COMPENSATION_ASSESSED
    resp = client.post(url, json={"actor": "governor:sign"}, headers={"X-State-Tenant": tenant})
    assert resp.status_code == 200, resp.text  # GOVERNOR_INSTRUMENT_SIGNED
    resp = client.post(url, json={"actor": "registrar:effect"}, headers={"X-State-Tenant": tenant})
    assert resp.status_code == 200, resp.text  # REVOCATION_EFFECTIVE
    return resp.json()


class TestRevocation:
    def test_full_chain_with_compensation_payout(self, ledger_client, ledger):
        parcel, c_of_o = _issued(ledger_client, "OG-REV-FULL")
        resp = ledger_client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/revocations",
            json={"public_purpose": "road expansion"}, headers=OGUN_H,
        )
        assert resp.status_code == 201, resp.text
        rid = resp.json()["revocation_id"]

        # Parcel is NOT revoked before the instrument is signed.
        mid = ledger_client.post(
            f"/api/v1/states/ogun/cadastre/revocations/{rid}/advance",
            json={"actor": "ministry:verify"}, headers=OGUN_H,
        )
        assert mid.status_code == 200
        assert ledger_client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}"
        ).json()["status"] == "ACTIVE"

        case = _advance_revocation_from(ledger_client, rid, already_verified=True)
        assert case["stage"] == "REVOCATION_EFFECTIVE"
        assert case["total_kobo"] == 72_000_000
        # Deterministic hold id: revocation_id|claimant|version.
        assert case["hold_id"] == f"{rid}|STIN-OG-0001234|1"
        # Two-phase: hold posted exactly once, nothing left held.
        assert case["hold_id"] in ledger.posted
        assert ledger.holds == {}
        # Instrument signed and chained to the original title hash.
        assert case["instrument_jws"]
        assert case["original_title_hash"]

        updated = ledger_client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}"
        ).json()
        assert updated["status"] == "REVOKED"

        history = ledger_client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/history",
            headers=OGUN_H,
        ).json()
        instruments = [e["instrument"] for e in history["chain_of_title"]]
        assert "TITLE_REVOKED" in instruments
        assert "COMPENSATION_PAID" in instruments

    def test_assessment_requires_items_and_valuer(self, ledger_client):
        parcel, _ = _issued(ledger_client, "OG-REV-NOITEMS")
        rid = ledger_client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/revocations",
            json={"public_purpose": "school"}, headers=OGUN_H,
        ).json()["revocation_id"]
        url = f"/api/v1/states/ogun/cadastre/revocations/{rid}/advance"
        assert ledger_client.post(
            url, json={"actor": "ministry:verify"}, headers=OGUN_H
        ).status_code == 200
        resp = ledger_client.post(url, json={"actor": "valuer:x"}, headers=OGUN_H)
        assert resp.status_code == 409  # no line items
        resp = ledger_client.post(
            url,
            json={"actor": "valuer:x",
                  "line_items": [{"category": "LAND", "amount_kobo": 100}]},
            headers=OGUN_H,
        )
        assert resp.status_code == 409  # no valuer

    def test_failed_payout_voids_hold_conservation(self):
        from lands_app.risk import AdapterUnavailableError

        class FailingLedger(FixtureCompensationLedger):
            def post(self, *, hold_id: str) -> None:
                raise AdapterUnavailableError("ledger down")

        ledger = FailingLedger()
        client = TestClient(create_app(ledger_adapter=ledger))
        parcel, _ = _issued(client, "OG-REV-VOID")
        rid = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/revocations",
            json={"public_purpose": "rail"}, headers=OGUN_H,
        ).json()["revocation_id"]
        url = f"/api/v1/states/ogun/cadastre/revocations/{rid}/advance"
        client.post(url, json={"actor": "ministry:verify"}, headers=OGUN_H)
        client.post(
            url,
            json={"actor": "valuer:x", "valuer": "valuer:x",
                  "line_items": [{"category": "LAND", "amount_kobo": 1000}]},
            headers=OGUN_H,
        )
        client.post(url, json={"actor": "governor:sign"}, headers=OGUN_H)
        resp = client.post(url, json={"actor": "registrar:effect"}, headers=OGUN_H)
        assert resp.status_code in (409, 500, 503)
        # Conservation: the hold was voided, not posted or left dangling.
        assert ledger.holds == {}
        assert ledger.posted == {}
        assert len(ledger.voided) == 1
        # Parcel remains ACTIVE (revocation did not take effect).
        assert client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}"
        ).json()["status"] == "ACTIVE"


def _advance_revocation_from(client, rid, already_verified=False, tenant="ogun"):
    url = f"/api/v1/states/{tenant}/cadastre/revocations/{rid}/advance"
    if not already_verified:
        client.post(url, json={"actor": "ministry:verify"}, headers={"X-State-Tenant": tenant})
    resp = client.post(
        url,
        json={
            "actor": "valuer:assess",
            "line_items": [
                {"category": "LAND", "amount_kobo": 50_000_000},
                {"category": "IMPROVEMENTS", "amount_kobo": 20_000_000},
                {"category": "CROPS", "amount_kobo": 1_500_000},
                {"category": "DISTURBANCE", "amount_kobo": 500_000},
            ],
            "valuer": "valuer:assess",
        },
        headers={"X-State-Tenant": tenant},
    )
    assert resp.status_code == 200, resp.text
    assert client.post(url, json={"actor": "governor:sign"}, headers={"X-State-Tenant": tenant}).status_code == 200
    resp = client.post(url, json={"actor": "registrar:effect"}, headers={"X-State-Tenant": tenant})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 9. Titling segregation of duties
# ---------------------------------------------------------------------------


class TestTitlingSegregationOfDuties:
    def test_same_actor_twice_409(self, client):
        parcel = _register(client, "OG-SOD-SAME")
        wf = parcel["titling_workflow_id"]
        url = f"/api/v1/states/ogun/cadastre/titling/{wf}/decisions"
        assert client.post(url, json={"approved": True, "actor": "surveyor:a"}).status_code == 200
        resp = client.post(url, json={"approved": True, "actor": "surveyor:a"})
        assert resp.status_code == 409
        assert "segregation" in resp.json()["detail"].lower()

    def test_role_mismatch_409(self, client):
        parcel = _register(client, "OG-SOD-ROLE")
        wf = parcel["titling_workflow_id"]
        url = f"/api/v1/states/ogun/cadastre/titling/{wf}/decisions"
        resp = client.post(url, json={"approved": True, "actor": "governor:x"})
        assert resp.status_code == 409
        assert "role" in resp.json()["detail"].lower()

    def test_role_correct_distinct_actors_issue_title(self, client):
        parcel = _register(client, "OG-SOD-OK")
        c_of_o = _issue_title(client, parcel)
        assert c_of_o.startswith("OGUN/COFO/")


# ---------------------------------------------------------------------------
# 10. Tenant isolation on all new endpoints
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_missing_tenant_header_400(self, client):
        parcel, _ = _issued(client, "OG-TEN-HDR")
        resp = client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/encumbrances"
        )
        assert resp.status_code == 400
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/transfers",
            json={"instrument_type": "GIFT", "transferor_stin": "STIN-OG-0001234",
                  "transferee_stin": "X", "evidence_document_id": "doc-1"},
            headers={"X-State-Tenant": "lagos"},  # header/path mismatch
        )
        assert resp.status_code == 400

    def test_encumbrance_tenant_isolation(self, client):
        parcel, _ = _issued(client, "OG-TEN-ENC")
        enc = _add_encumbrance(client, parcel["parcel_id"]).json()
        # Cross-tenant release attempt: not found.
        resp = client.post(
            f"/api/v1/states/lagos/cadastre/encumbrances/{enc['encumbrance_id']}/release",
            json={"actor": "bank:t"}, headers=LAGOS_H,
        )
        assert resp.status_code == 404

    def test_transfer_tenant_isolation(self, client):
        parcel, _ = _issued(client, "OG-TEN-TRF")
        tid = _apply_transfer(client, parcel, "GIFT", "STIN-OG-9999")
        resp = client.get(
            f"/api/v1/states/lagos/cadastre/transfers/{tid}", headers=LAGOS_H
        )
        assert resp.status_code == 404
        assert _advance_transfer(client, tid, tenant="lagos").status_code == 404

    def test_consent_tenant_isolation(self, client):
        parcel, _ = _issued(client, "OG-TEN-CON")
        consent = _issue_consent(client, parcel["parcel_id"])
        resp = client.get(
            f"/api/v1/states/lagos/cadastre/consents/{consent}", headers=LAGOS_H
        )
        assert resp.status_code == 404

    def test_transmission_tenant_isolation(self, client):
        parcel, _ = _issued(client, "OG-TEN-PROB")
        case = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/transmissions",
            json={"deceased_stin": "STIN-OG-0001234",
                  "death_certificate_doc_id": "doc-death-1",
                  "beneficiaries": ["STIN-OG-B1"]},
            headers=OGUN_H,
        ).json()
        resp = client.post(
            f"/api/v1/states/lagos/cadastre/transmissions/{case['transmission_id']}/advance",
            json={"actor": "registrar:x"}, headers=LAGOS_H,
        )
        assert resp.status_code == 404

    def test_court_order_tenant_isolation(self, client):
        parcel, _ = _issued(client, "OG-TEN-CO")
        order = _file_order(client, parcel["parcel_id"], "VEST_TITLE",
                            new_owner_stin="STIN-OG-7777")
        resp = client.post(
            f"/api/v1/states/lagos/cadastre/court-orders/{order['order_id']}/approve",
            json={"actor": "registrar:x", "role": "registrar"}, headers=LAGOS_H,
        )
        assert resp.status_code == 404

    def test_revocation_tenant_isolation(self, client):
        parcel, _ = _issued(client, "OG-TEN-REV")
        rid = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/revocations",
            json={"public_purpose": "road"}, headers=OGUN_H,
        ).json()["revocation_id"]
        resp = client.post(
            f"/api/v1/states/lagos/cadastre/revocations/{rid}/advance",
            json={"actor": "ministry:x"}, headers=LAGOS_H,
        )
        assert resp.status_code == 404

    def test_foreign_parcel_cannot_be_transferred_cross_tenant(self, client):
        parcel, _ = _issued(client, "OG-TEN-XFER")
        resp = client.post(
            f"/api/v1/states/lagos/cadastre/parcels/{parcel['parcel_id']}/transfers",
            json={"instrument_type": "GIFT", "transferor_stin": "STIN-OG-0001234",
                  "transferee_stin": "X", "evidence_document_id": "doc-1"},
            headers=LAGOS_H,
        )
        assert resp.status_code == 404
