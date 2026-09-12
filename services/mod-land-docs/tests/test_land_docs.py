"""Tests for mod-land-docs — lifecycle, tenancy, versioning, duplicates,
hash-chain integrity, fixture OCR determinism, fail-closed adapters."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from landdocs_app.adapters import (
    AdapterUnavailableError,
    DocumentClassifier,
    FixtureOcrEngine,
    PaddleOcrEngine,
    S3ObjectStore,
    ocr_engine_from_env,
)
from landdocs_app.main import create_app

LAGOS = {"X-State-Tenant": "lagos"}
OGUN = {"X-State-Tenant": "ogun"}

DEED_BYTES = b"DEED OF ASSIGNMENT between seller and buyer, plot 42 block 7"
SCAN_BYTES = b"tiny"  # < 16 bytes -> low-confidence fixture extraction


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def register(client: TestClient, title: str = "Deed of Assignment — Plot 42",
             filename: str = "deed_of_assignment.pdf",
             content: bytes = DEED_BYTES, headers=LAGOS, **kw):
    body = {
        "title": title,
        "filename": filename,
        "content_base64": b64(content),
        "registered_by": "lands-officer-1",
        **kw,
    }
    resp = client.post("/api/v1/states/lagos/land-docs/documents", json=body,
                       headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def doc_id(payload) -> str:
    return payload["document"]["document_id"]


def full_lifecycle(client: TestClient, **reg_kw):
    payload = register(client, **reg_kw)
    did = doc_id(payload)
    r = client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/classify",
                    json={"actor": "clerk-1"}, headers=LAGOS)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/ocr",
                    json={"actor": "clerk-1"}, headers=LAGOS)
    assert r.status_code == 200, r.text
    return did, payload


# --- happy path ---------------------------------------------------------------

def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_metrics_endpoint(client):
    register(client)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "landdocs_documents_registered_total" in resp.text


def test_register_returns_document_and_empty_warnings(client):
    payload = register(client)
    doc = payload["document"]
    assert doc["status"] == "REGISTERED"
    assert doc["version"] == 1
    assert len(doc["content_hash"]) == 64
    assert payload["duplicate_warnings"] == []


def test_full_lifecycle_happy_path(client):
    did, _ = full_lifecycle(client)
    r = client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/verify",
                    json={"verifier": "director-lands", "reason": "matches registry"},
                    headers=LAGOS)
    assert r.status_code == 200
    assert r.json()["status"] == "VERIFIED"
    assert r.json()["verified_by"] == "director-lands"


def test_classify_assigns_deed_type(client):
    payload = register(client)
    did = doc_id(payload)
    r = client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/classify",
                    json={"actor": "clerk"}, headers=LAGOS)
    assert r.json()["doc_type"] == "DEED"
    assert r.json()["classifier_scores"]["DEED"] > 0


def test_ocr_extracts_fields_and_confidence(client):
    did, _ = full_lifecycle(client)
    r = client.get(f"/api/v1/states/lagos/land-docs/documents/{did}", headers=LAGOS)
    doc = r.json()["document"]
    assert doc["status"] == "OCR_EXTRACTED"
    assert 0.80 <= doc["ocr_confidence"] <= 0.99
    assert doc["extracted_fields"]["parcel_id"]
    assert doc["needs_manual_review"] is False


def test_reject_path(client):
    did, _ = full_lifecycle(client)
    r = client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/reject",
                    json={"actor": "director-lands", "reason": "forged survey"},
                    headers=LAGOS)
    assert r.status_code == 200
    assert r.json()["status"] == "REJECTED"
    assert r.json()["rejection_reason"] == "forged survey"


def test_archive_after_verify(client):
    did, _ = full_lifecycle(client)
    client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/verify",
                json={"verifier": "v", "reason": "ok"}, headers=LAGOS)
    from landdocs_app.domain import DocStatus  # noqa: F401
    store = client.app.state.store
    doc = store.archive_document("lagos", did, "archivist")
    assert doc.status.value == "ARCHIVED"


# --- illegal transitions ------------------------------------------------------

def test_verify_before_ocr_is_409(client):
    did = doc_id(register(client))
    r = client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/verify",
                    json={"verifier": "v", "reason": "ok"}, headers=LAGOS)
    assert r.status_code == 409


def test_ocr_before_classify_is_409(client):
    did = doc_id(register(client))
    r = client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/ocr",
                    json={"actor": "a"}, headers=LAGOS)
    assert r.status_code == 409


def test_verify_after_reject_is_409(client):
    did, _ = full_lifecycle(client)
    client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/reject",
                json={"actor": "a", "reason": "bad"}, headers=LAGOS)
    r = client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/verify",
                    json={"verifier": "v", "reason": "ok"}, headers=LAGOS)
    assert r.status_code == 409


def test_verify_requires_verifier_and_reason(client):
    did, _ = full_lifecycle(client)
    r = client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/verify",
                    json={"verifier": "", "reason": "ok"}, headers=LAGOS)
    assert r.status_code == 422


def test_reject_requires_reason(client):
    did, _ = full_lifecycle(client)
    r = client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/reject",
                    json={"actor": "a", "reason": ""}, headers=LAGOS)
    assert r.status_code == 422


# --- tenancy ------------------------------------------------------------------

def test_missing_tenant_header_is_400(client):
    resp = client.get("/api/v1/states/lagos/land-docs/documents")
    assert resp.status_code == 400
    assert "X-State-Tenant" in resp.json()["detail"]


def test_header_path_mismatch_is_400(client):
    resp = client.get("/api/v1/states/lagos/land-docs/documents", headers=OGUN)
    assert resp.status_code == 400


def test_cross_tenant_document_access_is_404(client):
    did = doc_id(register(client))
    r = client.get(f"/api/v1/states/ogun/land-docs/documents/{did}", headers=OGUN)
    assert r.status_code == 404


def test_cross_tenant_verify_is_404(client):
    did, _ = full_lifecycle(client)
    r = client.post(f"/api/v1/states/ogun/land-docs/documents/{did}/verify",
                    json={"verifier": "v", "reason": "ok"}, headers=OGUN)
    assert r.status_code == 404


def test_tenant_list_isolation(client):
    register(client)
    r = client.get("/api/v1/states/ogun/land-docs/documents", headers=OGUN)
    assert r.json() == []
    r = client.get("/api/v1/states/lagos/land-docs/documents", headers=LAGOS)
    assert len(r.json()) == 1


# --- filtering ----------------------------------------------------------------

def test_list_filters(client):
    did, _ = full_lifecycle(client, parcel_id="IKEJA-0001")
    register(client, title="Tax Clearance Cert", filename="tcc.pdf",
             content=b"tax clearance certificate body of bytes")
    docs = client.get("/api/v1/states/lagos/land-docs/documents",
                      params={"status": "OCR_EXTRACTED"}, headers=LAGOS).json()
    assert [d["document_id"] for d in docs] == [did]
    docs = client.get("/api/v1/states/lagos/land-docs/documents",
                      params={"parcel_id": "IKEJA-0001"}, headers=LAGOS).json()
    assert [d["document_id"] for d in docs] == [did]
    docs = client.get("/api/v1/states/lagos/land-docs/documents",
                      params={"doc_type": "DEED"}, headers=LAGOS).json()
    assert [d["document_id"] for d in docs] == [did]


# --- versioning ---------------------------------------------------------------

def test_new_version_supersedes_prior(client):
    did, _ = full_lifecycle(client)
    r = client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/versions",
                    json={"filename": "deed_v2.pdf",
                          "content_base64": b64(b"corrected deed of assignment"),
                          "actor": "clerk-2"}, headers=LAGOS)
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["version"] == 2
    assert new["status"] == "REGISTERED"
    prior = client.get(f"/api/v1/states/lagos/land-docs/documents/{did}",
                       headers=LAGOS).json()["document"]
    assert prior["status"] == "SUPERSEDED"
    assert prior["superseded_by"] == new["document_id"]


def test_version_numbers_monotonic(client):
    did = doc_id(register(client))
    v2 = client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/versions",
                     json={"filename": "v2.pdf",
                           "content_base64": b64(b"second version contents"),
                           "actor": "a"}, headers=LAGOS).json()
    v3 = client.post(
        f"/api/v1/states/lagos/land-docs/documents/{v2['document_id']}/versions",
        json={"filename": "v3.pdf",
              "content_base64": b64(b"third version contents"),
              "actor": "a"}, headers=LAGOS).json()
    assert v2["version"] == 2 and v3["version"] == 3
    assert v3["root_document_id"] == did


def test_versioning_superseded_doc_is_409(client):
    did = doc_id(register(client))
    client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/versions",
                json={"filename": "v2.pdf",
                      "content_base64": b64(b"second version contents!!"),
                      "actor": "a"}, headers=LAGOS)
    r = client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/versions",
                    json={"filename": "v3.pdf",
                          "content_base64": b64(b"third version contents!!!"),
                          "actor": "a"}, headers=LAGOS)
    assert r.status_code == 409


# --- duplicate detection -------------------------------------------------------

def test_duplicate_identical_content_hash(client):
    register(client, title="Deed of Assignment — Plot 42")
    payload = register(client, title="Different title entirely",
                       filename="other.pdf")
    warnings = payload["duplicate_warnings"]
    assert any(w["reason"] == "identical_content_hash" and w["score"] == 1.0
               for w in warnings)


def test_duplicate_fuzzy_title(client):
    register(client, title="Deed of Assignment — Plot 42",
             content=b"first unique content block")
    payload = register(client, title="Deed of Assignment — Plot 43",
                       content=b"second unique content block")
    warnings = payload["duplicate_warnings"]
    assert any(w["reason"] == "similar_title" and w["score"] >= 0.9
               for w in warnings)


def test_no_duplicate_for_distinct_docs(client):
    register(client, title="Deed of Assignment", content=b"aaaa aaa aaaa aaaa")
    payload = register(client, title="National Identity Card",
                       filename="nin.png", content=b"zzzz zzz zzzz zzzz")
    assert payload["duplicate_warnings"] == []


# --- hash-chained audit ---------------------------------------------------------

def test_audit_chain_grows_and_validates(client):
    did, _ = full_lifecycle(client)
    client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/verify",
                json={"verifier": "v", "reason": "ok"}, headers=LAGOS)
    audit = client.get(f"/api/v1/states/lagos/land-docs/documents/{did}/audit",
                       headers=LAGOS).json()
    assert audit["valid"] is True
    assert audit["entries"] == 4  # register, classify, ocr, verify
    assert audit["records"][0]["prev_hash"] == "0" * 64
    hashes = [r["event_hash"] for r in audit["records"]]
    prevs = [r["prev_hash"] for r in audit["records"][1:]]
    assert hashes[:-1] == prevs


def test_audit_tamper_detection(client):
    did, _ = full_lifecycle(client)
    store = client.app.state.store
    scope = store._scope("lagos")
    scope.audit[0]["detail"] = "tampered detail"  # attacker rewrite
    audit = client.get(f"/api/v1/states/lagos/land-docs/documents/{did}/audit",
                       headers=LAGOS).json()
    assert audit["valid"] is False
    assert audit["chain_errors"]


def test_get_document_includes_audit_chain(client):
    did, _ = full_lifecycle(client)
    body = client.get(f"/api/v1/states/lagos/land-docs/documents/{did}",
                      headers=LAGOS).json()
    assert body["chain_intact"] is True
    assert len(body["audit_chain"]) == 3


# --- fixture OCR determinism ----------------------------------------------------

def test_fixture_ocr_is_deterministic():
    engine = FixtureOcrEngine()
    a = engine.extract(DEED_BYTES, "deed.pdf")
    b = engine.extract(DEED_BYTES, "deed.pdf")
    assert a == b
    assert 0.80 <= a.confidence <= 0.99
    assert a.fields["parcel_id"] and a.fields["party_a"] != a.fields["party_b"] \
        or a.fields["party_a"]  # always populated


def test_low_confidence_sets_manual_review_flag(client):
    payload = register(client, title="Faded scan", filename="scan.tiff",
                       content=SCAN_BYTES)
    did = doc_id(payload)
    client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/classify",
                json={"actor": "a"}, headers=LAGOS)
    r = client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/ocr",
                    json={"actor": "a"}, headers=LAGOS)
    doc = r.json()
    assert doc["ocr_confidence"] < 0.7
    assert doc["needs_manual_review"] is True


# --- fail-closed adapters -------------------------------------------------------

def test_paddle_engine_requires_url():
    with pytest.raises(AdapterUnavailableError):
        PaddleOcrEngine(environ={})


def test_s3_store_requires_bucket():
    with pytest.raises(AdapterUnavailableError):
        S3ObjectStore(environ={})


def test_production_profile_fails_closed_without_ocr_url(monkeypatch):
    monkeypatch.setenv("SOS_LANDDOCS_PROFILE", "production")
    monkeypatch.delenv("SOS_LANDDOCS_OCR_URL", raising=False)
    with pytest.raises(AdapterUnavailableError):
        create_app()


def test_production_profile_boots_with_ocr_url(monkeypatch):
    monkeypatch.setenv("SOS_LANDDOCS_PROFILE", "production")
    monkeypatch.setenv("SOS_LANDDOCS_OCR_URL", "http://ocr-sidecar:8080/ocr")
    monkeypatch.setenv("SOS_LANDDOCS_OCR_ENGINE", "paddle")
    app = create_app()
    assert isinstance(app.state.store.ocr_engine, PaddleOcrEngine)


def test_unknown_engine_name_fails_closed():
    with pytest.raises(AdapterUnavailableError):
        ocr_engine_from_env({"SOS_LANDDOCS_OCR_ENGINE": "tesseract-xyz"})


def test_paddle_extract_sidecar_failure_raises():
    engine = PaddleOcrEngine(ocr_url="http://127.0.0.1:9/ocr", timeout_seconds=0.5)
    with pytest.raises(AdapterUnavailableError):
        engine.extract(b"some content bytes here", "deed.pdf")


# --- classifier unit ------------------------------------------------------------

def test_classifier_keywords():
    clf = DocumentClassifier()
    kind, scores = clf.classify("certificate_of_occupancy.pdf")
    assert kind == "C_OF_O" and scores["C_OF_O"] > 0
    kind, _ = clf.classify("scan001.bin")
    assert kind == "OTHER"


# --- events ---------------------------------------------------------------------

def test_events_published(client, bus):
    did, _ = full_lifecycle(client)
    client.post(f"/api/v1/states/lagos/land-docs/documents/{did}/verify",
                json={"verifier": "v", "reason": "ok"}, headers=LAGOS)
    topics = [e["topic"] for e in bus.published]
    assert "ng.sos.landdocs.document_registered" in topics
    assert "ng.sos.landdocs.ocr_completed" in topics
    assert "ng.sos.landdocs.document_verified" in topics


def test_duplicate_event_published(client, bus):
    register(client)
    register(client, title="Second upload, same bytes")
    topics = [e["topic"] for e in bus.published]
    assert "ng.sos.landdocs.duplicate_suspected" in topics
