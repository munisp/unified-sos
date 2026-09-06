"""Tests for mod-kyc-kyb. No external network; deterministic simulated adapters."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.adapters import (
    AdapterUnavailableError,
    DoclingAdapter,
    FixtureRegistryAdapter,
    LivenessEngine,
    PaddleOCRAdapter,
    SimulatedDocumentAIAdapter,
    SimulatedVLMAdapter,
    VLMAdapter,
    parse_ocr_lines,
)
from app.adapters.registry_adapters import CacRegistryAdapter, NimcAdapter, SanctionsAdapter
from app.domain import (
    DocumentType,
    ExtractionEngine,
    LivenessEvidence,
    RegistryStatus,
    ReviewDecision,
    SubjectType,
    TenantState,
    VerificationStatus,
    sha256_hex,
)
from app.main import create_app
from app.service import KycKybService, band_for_score, RiskBand

TENANT = "lagos"
OTHER = "ogun"


def make_service(**overrides) -> KycKybService:
    kwargs = dict(
        ocr_adapter=SimulatedDocumentAIAdapter(ExtractionEngine.PADDLEOCR),
        docling_adapter=SimulatedDocumentAIAdapter(ExtractionEngine.DOCLING),
        vlm_adapter=SimulatedVLMAdapter(),
        corporate_registry=FixtureRegistryAdapter(),
        identity_registry=FixtureRegistryAdapter(),
        sanctions=FixtureRegistryAdapter(),
        liveness_engine=LivenessEngine(),
    )
    kwargs.update(overrides)
    return KycKybService(**kwargs)


@pytest.fixture()
def service() -> KycKybService:
    return make_service()


@pytest.fixture()
def client(service) -> TestClient:
    return TestClient(create_app(service))


def hash_of(text: str) -> str:
    return sha256_hex(text)


def create_case(client, subject_ref="wallet-1", subject_type="CITIZEN_WALLET", state=TENANT):
    resp = client.post("/kyc/v1/cases", json={
        "state_id": state, "subject_ref": subject_ref, "subject_type": subject_type,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def attach(client, case_id, doc_type="NIN_SLIP", seed="doc-1", state=TENANT):
    resp = client.post(f"/kyc/v1/cases/{case_id}/documents", json={
        "state_id": state, "document_type": doc_type,
        "object_uri": f"s3://bucket/{seed}", "sha256": hash_of(seed),
        "uploaded_by": "agent-1",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def pass_liveness(client, case_id, state=TENANT, seed="bio-1"):
    ch = client.post(f"/kyc/v1/cases/{case_id}/liveness-challenges",
                     json={"state_id": state}).json()
    now = datetime.now(timezone.utc).isoformat()
    resp = client.post(f"/kyc/v1/liveness/{ch['challenge_id']}/evidence", json={
        "state_id": state, "challenge_nonce": ch["nonce"],
        "artifact_hashes": [hash_of(seed)],
        "motion_score": 0.95, "texture_score": 0.95, "depth_score": 0.95,
        "action_completion_score": 1.0, "device_attestation_score": 0.9,
        "captured_at": now,
    })
    assert resp.status_code == 200, resp.text
    return ch, resp.json()


# ------------------------------ KYC flow ------------------------------


def test_kyc_happy_path_auto_approve(client):
    case = create_case(client)
    cid = case["case_id"]
    art = attach(client, cid)
    ext = client.post(f"/kyc/v1/documents/{art['artifact_id']}/extract",
                      json={"state_id": TENANT})
    assert ext.status_code == 200
    assert ext.json()["consensus_score"] > 0.5
    _, live = pass_liveness(client, cid)
    assert live["passed"] is True
    resp = client.post(f"/kyc/v1/cases/{cid}/submit", json={"state_id": TENANT})
    body = resp.json()
    assert body["status"] == "APPROVED"
    assert body["risk_band"] == "LOW"
    assert body["risk_score"] == 0


def test_tenant_isolation_case_read_denied(client):
    case = create_case(client, state=TENANT)
    resp = client.get(f"/kyc/v1/cases/{case['case_id']}", params={"state_id": OTHER})
    assert resp.status_code == 403


def test_tenant_isolation_review_queue(client):
    case = create_case(client, subject_ref="risky")
    cid = case["case_id"]
    attach(client, cid)  # docs ok, liveness missing -> MEDIUM -> IN_REVIEW
    client.post(f"/kyc/v1/cases/{cid}/submit", json={"state_id": TENANT})
    q1 = client.get("/kyc/v1/review-queue", params={"state_id": TENANT}).json()
    q2 = client.get("/kyc/v1/review-queue", params={"state_id": OTHER}).json()
    assert any(t["case_id"] == cid for t in q1)
    assert all(t["case_id"] != cid for t in q2)


def test_missing_document_routes_to_review(client):
    case = create_case(client, subject_type="AGENT")  # requires NATIONAL_ID + POA
    cid = case["case_id"]
    attach(client, cid, doc_type="NATIONAL_ID")
    _, live = pass_liveness(client, cid)
    assert live["passed"]
    resp = client.post(f"/kyc/v1/cases/{cid}/submit", json={"state_id": TENANT})
    body = resp.json()
    assert body["status"] == "IN_REVIEW"
    assert "MISSING_DOCUMENTS" in body["decision_reason"]


def test_missing_liveness_routes_to_review(client):
    case = create_case(client)
    cid = case["case_id"]
    attach(client, cid)
    resp = client.post(f"/kyc/v1/cases/{cid}/submit", json={"state_id": TENANT})
    assert resp.json()["status"] == "IN_REVIEW"
    assert "LIVENESS_NOT_PASSED" in resp.json()["decision_reason"]


def test_consensus_mismatch_routes_to_review(client):
    svc = make_service(vlm_adapter=SimulatedVLMAdapter(mismatch_keys=["document_number"]))
    c = TestClient(create_app(svc))
    case = create_case(c)
    cid = case["case_id"]
    art = attach(c, cid)
    ext = c.post(f"/kyc/v1/documents/{art['artifact_id']}/extract",
                 json={"state_id": TENANT}).json()
    assert "document_number" in ext["mismatch_fields"]
    pass_liveness(c, cid)
    body = c.post(f"/kyc/v1/cases/{cid}/submit", json={"state_id": TENANT}).json()
    assert body["status"] == "IN_REVIEW"
    assert "EXTRACTION_MISMATCH" in body["decision_reason"]


def test_case_not_found(client):
    resp = client.get("/kyc/v1/cases/nope", params={"state_id": TENANT})
    assert resp.status_code == 404


def test_invalid_tenant_rejected(client):
    resp = client.post("/kyc/v1/cases", json={
        "state_id": "kano", "subject_ref": "x", "subject_type": "RESIDENT",
    })
    assert resp.status_code == 422


# ------------------------------ adapter fail-closed ------------------------------


def test_paddleocr_unavailable_fails_closed():
    adapter = PaddleOCRAdapter(enabled=True)
    case_art = None
    with pytest.raises(AdapterUnavailableError):
        adapter.extract(case_art, DocumentType.NIN_SLIP)


def test_docling_unavailable_fails_closed():
    adapter = DoclingAdapter(enabled=True)
    with pytest.raises(AdapterUnavailableError):
        adapter.extract(None, DocumentType.NIN_SLIP)


def test_vlm_unavailable_fails_closed():
    adapter = VLMAdapter(enabled=True, endpoint_url=None)
    with pytest.raises(AdapterUnavailableError):
        adapter.extract(None, DocumentType.NIN_SLIP, {})


def test_extract_records_unavailable_errors(client):
    svc = make_service(ocr_adapter=PaddleOCRAdapter(enabled=True),
                       docling_adapter=DoclingAdapter(enabled=True),
                       vlm_adapter=VLMAdapter(enabled=True))
    c = TestClient(create_app(svc))
    case = create_case(c)
    art = attach(c, case["case_id"])
    ext = c.post(f"/kyc/v1/documents/{art['artifact_id']}/extract",
                 json={"state_id": TENANT}).json()
    assert set(ext["errors"]) == {
        "PaddleOCRAdapter:unavailable", "DoclingAdapter:unavailable",
        "VLMAdapter:unavailable",
    }
    assert ext["engines"] == []


def test_registry_adapters_fail_closed():
    with pytest.raises(AdapterUnavailableError):
        CacRegistryAdapter().verify_company("Acme", "RC1")
    with pytest.raises(AdapterUnavailableError):
        NimcAdapter().verify_identity("123", "A B", None)
    with pytest.raises(AdapterUnavailableError):
        SanctionsAdapter().screen("s", "n")


def test_parse_ocr_lines_common_fields():
    lines = [
        "Surname: ADEYEMI",
        "First Name: CHUKS",
        "Date of Birth: 01/01/1990",
        "Expiry Date: 01/01/2030",
        "RC: 1234567",
        "Address: 12 Marina Lagos",
    ]
    fields = parse_ocr_lines(lines)
    assert fields["surname"] == "ADEYEMI"
    assert fields["first_name"] == "CHUKS"
    assert fields["date_of_birth"] == "01/01/1990"
    assert fields["expiry_date"] == "01/01/2030"
    assert fields["rc_number"] == "1234567"
    assert "Marina" in fields["address"]


# ------------------------------ liveness ------------------------------


def test_challenge_nonce_and_expiry(service):
    case = service.create_kyc_case(TENANT, "w1", SubjectType.CITIZEN_WALLET)
    ch = service.issue_liveness_challenge(TENANT, case.case_id)
    assert len(ch.nonce) == 32
    assert ch.expires_at > ch.created_at
    assert ch.action_sequence
    assert ch.max_attempts == 3


def test_tenant_action_sequence_configurable(service):
    from app.domain import LivenessAction
    service.liveness.TENANT_ACTIONS["lagos"] = [LivenessAction.SMILE]
    case = service.create_kyc_case(TENANT, "w1", SubjectType.CITIZEN_WALLET)
    ch = service.issue_liveness_challenge(TENANT, case.case_id)
    assert ch.action_sequence == [LivenessAction.SMILE]


def good_evidence(nonce, seed="e1", captured=None):
    return LivenessEvidence(
        challenge_nonce=nonce, artifact_hashes=[hash_of(seed)],
        motion_score=0.95, texture_score=0.95, depth_score=0.95,
        action_completion_score=1.0, device_attestation_score=0.9,
        captured_at=captured or datetime.now(timezone.utc),
    )


def test_liveness_pass_and_fail(service):
    case = service.create_kyc_case(TENANT, "w1", SubjectType.CITIZEN_WALLET)
    ch = service.issue_liveness_challenge(TENANT, case.case_id)
    res = service.submit_liveness_evidence(TENANT, ch.challenge_id, good_evidence(ch.nonce))
    assert res.passed and res.score >= 0.7

    ch2 = service.issue_liveness_challenge(TENANT, case.case_id)
    bad = LivenessEvidence(
        challenge_nonce=ch2.nonce, artifact_hashes=[hash_of("bad")],
        motion_score=0.05, texture_score=0.05, depth_score=0.05,
        action_completion_score=0.0,
        captured_at=datetime.now(timezone.utc),
    )
    res2 = service.submit_liveness_evidence(TENANT, ch2.challenge_id, bad)
    assert not res2.passed
    assert "PRINT_ARTIFACT_SUSPECTED" in res2.anti_spoof_flags
    assert any("score_below_threshold" in r for r in res2.reasons)


def test_liveness_nonce_mismatch(service):
    case = service.create_kyc_case(TENANT, "w1", SubjectType.CITIZEN_WALLET)
    ch = service.issue_liveness_challenge(TENANT, case.case_id)
    res = service.submit_liveness_evidence(TENANT, ch.challenge_id, good_evidence("wrong"))
    assert not res.passed and res.reasons == ["nonce_mismatch"]


def test_liveness_expired_challenge(service):
    engine = LivenessEngine(challenge_ttl_seconds=1)
    svc = make_service(liveness_engine=engine)
    case = svc.create_kyc_case(TENANT, "w1", SubjectType.CITIZEN_WALLET)
    ch = svc.issue_liveness_challenge(TENANT, case.case_id)
    ch.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    res = svc.submit_liveness_evidence(TENANT, ch.challenge_id, good_evidence(ch.nonce))
    assert not res.passed and res.reasons == ["challenge_expired"]


def test_liveness_artifact_hash_reuse_rejected(service):
    case = service.create_kyc_case(TENANT, "w1", SubjectType.CITIZEN_WALLET)
    ch1 = service.issue_liveness_challenge(TENANT, case.case_id)
    res1 = service.submit_liveness_evidence(TENANT, ch1.challenge_id, good_evidence(ch1.nonce, "shared"))
    assert res1.passed
    ch2 = service.issue_liveness_challenge(TENANT, case.case_id)
    res2 = service.submit_liveness_evidence(TENANT, ch2.challenge_id, good_evidence(ch2.nonce, "shared"))
    assert not res2.passed and res2.reasons == ["artifact_hash_reuse"]


def test_liveness_excessive_attempts_rejected(service):
    engine = LivenessEngine(max_attempts=2)
    svc = make_service(liveness_engine=engine)
    case = svc.create_kyc_case(TENANT, "w1", SubjectType.CITIZEN_WALLET)
    ch = svc.issue_liveness_challenge(TENANT, case.case_id)
    bad = lambda seed: LivenessEvidence(
        challenge_nonce=ch.nonce, artifact_hashes=[hash_of(seed)],
        motion_score=0.0, texture_score=0.5, depth_score=0.5,
        action_completion_score=0.0,
        captured_at=datetime.now(timezone.utc),
    )
    r1 = svc.submit_liveness_evidence(TENANT, ch.challenge_id, bad("a1"))
    assert not r1.passed and "max_attempts_exceeded" not in r1.reasons
    r2 = svc.submit_liveness_evidence(TENANT, ch.challenge_id, bad("a2"))
    assert not r2.passed and "max_attempts_exceeded" in r2.reasons
    r3 = svc.submit_liveness_evidence(TENANT, ch.challenge_id, bad("a3"))
    assert r3.reasons == ["challenge_already_concluded"]


def test_liveness_impossible_timestamp(service):
    case = service.create_kyc_case(TENANT, "w1", SubjectType.CITIZEN_WALLET)
    ch = service.issue_liveness_challenge(TENANT, case.case_id)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    res = service.submit_liveness_evidence(TENANT, ch.challenge_id, good_evidence(ch.nonce, "t1", future))
    assert not res.passed and res.reasons == ["impossible_timestamp"]


def test_passive_liveness_mode(service):
    case = service.create_kyc_case(TENANT, "w1", SubjectType.CITIZEN_WALLET)
    ch = service.issue_liveness_challenge(TENANT, case.case_id, mode="passive")
    assert ch.action_sequence == []
    res = service.submit_liveness_evidence(TENANT, ch.challenge_id, good_evidence(ch.nonce, "p1"))
    assert res.passed


def test_liveness_deterministic_scoring(service):
    ev = good_evidence("n", "s")
    s1, f1 = service.liveness.score(ev)
    s2, f2 = service.liveness.score(ev)
    assert s1 == s2 and f1 == f2
    assert abs(s1 - (0.25 * 0.95 + 0.2 * 1.0 + 0.2 * 0.95 + 0.2 * 0.95 + 0.1 * 0.9 + 0.05 * 0.5)) < 1e-3


# ------------------------------ KYB flow ------------------------------


def kyb_service(**kw):
    fixtures = {
        "cac:RC123": (RegistryStatus.MATCH.value, 0.99, {}),
        "cac:RCMISM": (RegistryStatus.MISMATCH.value, 0.4, {}),
    }
    kw.setdefault("corporate_registry", FixtureRegistryAdapter(fixtures))
    kw.setdefault("sanctions", FixtureRegistryAdapter(fixtures))
    return make_service(**kw)


def create_kyb(client, rc="RC123", name="Acme Ltd", state=TENANT):
    resp = client.post("/kyb/v1/cases", json={
        "state_id": state, "legal_name": name, "rc_number": rc,
        "business_type": "LIMITED_LIABILITY", "address": "1 Broad St",
        "tin": "TIN-1",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def attach_kyb(client, case_id, doc_type, seed, state=TENANT):
    resp = client.post(f"/kyb/v1/cases/{case_id}/documents", json={
        "state_id": state, "document_type": doc_type,
        "object_uri": f"s3://kyb/{seed}", "sha256": hash_of(seed),
        "uploaded_by": "compliance",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_kyb_happy_path_auto_approve(client):
    svc = client.app.state.service
    svc.corporate_registry = FixtureRegistryAdapter(
        {"cac:RC123": (RegistryStatus.MATCH.value, 0.99, {})})
    case = create_kyb(client)
    cid = case["case_id"]
    attach_kyb(client, cid, "CAC_CERTIFICATE", "cac-doc")
    attach_kyb(client, cid, "BENEFICIAL_OWNERSHIP_DECLARATION", "bo-doc")
    ver = client.post(f"/kyb/v1/cases/{cid}/registry-verification",
                      json={"state_id": TENANT})
    assert ver.status_code == 200
    statuses = {v["registry"]: v["status"] for v in ver.json()}
    assert statuses["CAC"] == "MATCH"
    resp = client.post(f"/kyb/v1/cases/{cid}/beneficial-owners", json={
        "state_id": TENANT, "owner_identity": "Owner One",
        "display_label": "O.O.", "ownership_percentage": 60.0,
    })
    assert resp.status_code == 200
    body = client.post(f"/kyb/v1/cases/{cid}/submit", json={"state_id": TENANT}).json()
    assert body["status"] == "APPROVED"
    assert body["risk_band"] == "LOW"


def test_kyb_cac_mismatch_routes_to_review():
    svc = kyb_service()
    c = TestClient(create_app(svc))
    case = create_kyb(c, rc="RCMISM")
    cid = case["case_id"]
    attach_kyb(c, cid, "CAC_CERTIFICATE", "d1")
    attach_kyb(c, cid, "BENEFICIAL_OWNERSHIP_DECLARATION", "d2")
    ver = c.post(f"/kyb/v1/cases/{cid}/registry-verification", json={"state_id": TENANT}).json()
    assert any(v["status"] == "MISMATCH" for v in ver)
    body = c.post(f"/kyb/v1/cases/{cid}/submit", json={"state_id": TENANT}).json()
    assert body["status"] == "IN_REVIEW"
    assert "REGISTRY_MISMATCH" in body["decision_reason"]


def test_kyb_cac_not_found_routes_to_review():
    svc = kyb_service()
    c = TestClient(create_app(svc))
    case = create_kyb(c, rc="RC-UNKNOWN")
    cid = case["case_id"]
    attach_kyb(c, cid, "CAC_CERTIFICATE", "d1")
    attach_kyb(c, cid, "BENEFICIAL_OWNERSHIP_DECLARATION", "d2")
    ver = c.post(f"/kyb/v1/cases/{cid}/registry-verification", json={"state_id": TENANT}).json()
    assert any(v["status"] == "NOT_FOUND" for v in ver)
    body = c.post(f"/kyb/v1/cases/{cid}/submit", json={"state_id": TENANT}).json()
    assert body["status"] == "IN_REVIEW"


def test_kyb_cac_unavailable_fail_closed():
    svc = kyb_service(corporate_registry=CacRegistryAdapter(enabled=False))
    c = TestClient(create_app(svc))
    case = create_kyb(c)
    cid = case["case_id"]
    attach_kyb(c, cid, "CAC_CERTIFICATE", "d1")
    attach_kyb(c, cid, "BENEFICIAL_OWNERSHIP_DECLARATION", "d2")
    c.post(f"/kyb/v1/cases/{cid}/beneficial-owners", json={
        "state_id": TENANT, "owner_identity": "Owner A", "display_label": "O.A.",
        "ownership_percentage": 100.0,
    })
    ver = c.post(f"/kyb/v1/cases/{cid}/registry-verification", json={"state_id": TENANT}).json()
    assert ver[0]["status"] == "UNAVAILABLE"
    body = c.post(f"/kyb/v1/cases/{cid}/submit", json={"state_id": TENANT}).json()
    assert body["status"] == "IN_REVIEW"
    assert "REGISTRY_UNAVAILABLE" in body["decision_reason"]


def test_kyb_sanctions_hit_prohibited_rejected():
    svc = kyb_service()
    c = TestClient(create_app(svc))
    case = create_kyb(c)
    cid = case["case_id"]
    attach_kyb(c, cid, "CAC_CERTIFICATE", "d1")
    attach_kyb(c, cid, "BENEFICIAL_OWNERSHIP_DECLARATION", "d2")
    svc.sanctions.fixtures[f"sanctions:{cid}"] = (RegistryStatus.MATCH.value, 0.99, {})
    c.post(f"/kyb/v1/cases/{cid}/registry-verification", json={"state_id": TENANT})
    body = c.post(f"/kyb/v1/cases/{cid}/submit", json={"state_id": TENANT}).json()
    assert body["risk_band"] == "PROHIBITED"
    assert body["status"] == "REJECTED"


def test_kyb_ownership_over_100_rejected(client):
    case = create_kyb(client)
    cid = case["case_id"]
    client.post(f"/kyb/v1/cases/{cid}/beneficial-owners", json={
        "state_id": TENANT, "owner_identity": "A", "display_label": "A",
        "ownership_percentage": 70.0,
    })
    resp = client.post(f"/kyb/v1/cases/{cid}/beneficial-owners", json={
        "state_id": TENANT, "owner_identity": "B", "display_label": "B",
        "ownership_percentage": 40.0,
    })
    assert resp.status_code == 409


def test_kyb_beneficial_owner_kyc_cross_reference(client):
    kyc_case = create_case(client)
    kyc_cid = kyc_case["case_id"]
    case = create_kyb(client)
    cid = case["case_id"]
    resp = client.post(f"/kyb/v1/cases/{cid}/beneficial-owners", json={
        "state_id": TENANT, "owner_identity": "Owner X", "display_label": "O.X.",
        "ownership_percentage": 51.0, "kyc_case_id": kyc_cid,
    })
    assert resp.status_code == 200
    assert resp.json()["beneficial_owners"][0]["kyc_case_id"] == kyc_cid
    bad = client.post(f"/kyb/v1/cases/{cid}/beneficial-owners", json={
        "state_id": TENANT, "owner_identity": "Owner Y", "display_label": "O.Y.",
        "ownership_percentage": 10.0, "kyc_case_id": "does-not-exist",
    })
    assert bad.status_code == 404


def test_kyb_tenant_isolation(client):
    case = create_kyb(client, state=TENANT)
    resp = client.get(f"/kyb/v1/cases/{case['case_id']}", params={"state_id": OTHER})
    assert resp.status_code == 403


# ------------------------------ review & audit ------------------------------


def test_reviewer_approve_and_reject(client):
    case = create_case(client)  # missing liveness => IN_REVIEW
    cid = case["case_id"]
    attach(client, cid)
    client.post(f"/kyc/v1/cases/{cid}/submit", json={"state_id": TENANT})
    resp = client.post(f"/kyc/v1/cases/{cid}/review", json={
        "state_id": TENANT, "decision": "APPROVE",
        "reviewer": "analyst-1", "reason": "manual override with supervisor sign-off",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "APPROVED"
    queue = client.get("/kyc/v1/review-queue", params={"state_id": TENANT}).json()
    assert all(t["case_id"] != cid for t in queue)

    case2 = create_case(client, subject_ref="w2")
    cid2 = case2["case_id"]
    attach(client, cid2)  # docs ok, liveness missing -> MEDIUM -> IN_REVIEW
    client.post(f"/kyc/v1/cases/{cid2}/submit", json={"state_id": TENANT})
    resp2 = client.post(f"/kyc/v1/cases/{cid2}/review", json={
        "state_id": TENANT, "decision": "REJECT",
        "reviewer": "analyst-2", "reason": "insufficient evidence",
    })
    assert resp2.json()["status"] == "REJECTED"
    # decided case cannot be re-reviewed
    again = client.post(f"/kyc/v1/cases/{cid2}/review", json={
        "state_id": TENANT, "decision": "APPROVE",
        "reviewer": "analyst-2", "reason": "retry",
    })
    assert again.status_code == 409


def test_audit_hash_chain_integrity(client):
    case = create_case(client)
    cid = case["case_id"]
    attach(client, cid)
    pass_liveness(client, cid)
    client.post(f"/kyc/v1/cases/{cid}/submit", json={"state_id": TENANT})
    audit = client.get("/kyc-kyb/v1/audit", params={"state_id": TENANT}).json()
    assert len(audit) >= 4
    prev = "GENESIS"
    for entry in audit:
        assert entry["prev_hash"] == prev
        prev = entry["entry_hash"]
    verify = client.get("/kyc-kyb/v1/audit/verify").json()
    assert verify["valid"] is True


def test_audit_chain_detects_tamper(client):
    create_case(client)
    svc = client.app.state.service
    svc.repo.audit[0].action = "TAMPERED"
    assert svc.verify_audit() is False


def test_no_raw_pii_in_api_payloads(client):
    case = create_case(client, subject_ref="nin-12345678901")
    cid = case["case_id"]
    attach(client, cid)
    payload = client.get(f"/kyc/v1/cases/{cid}", params={"state_id": TENANT}).json()
    raw = str(payload)
    assert "nin-12345678901" not in raw
    assert payload["subject_ref"] == sha256_hex(f"{TENANT}:nin-12345678901")

    kyb = create_kyb(client, name="Secret Holdings Limited")
    kpayload = client.get(f"/kyb/v1/cases/{kyb['case_id']}", params={"state_id": TENANT}).json()
    assert "Secret Holdings Limited" not in str(kpayload)
    assert "RC123" not in str(kpayload)


def test_registry_response_only_hashes(client):
    svc = client.app.state.service
    svc.corporate_registry = FixtureRegistryAdapter(
        {"cac:RC123": (RegistryStatus.MATCH.value, 0.99, {"raw_secret": "FULLREGISTRYDUMP"})})
    case = create_kyb(client)
    ver = client.post(f"/kyb/v1/cases/{case['case_id']}/registry-verification",
                      json={"state_id": TENANT}).json()
    assert "FULLREGISTRYDUMP" not in str(ver)
    assert all(len(v["response_hash"]) == 64 for v in ver)


# ------------------------------ misc ------------------------------


def test_risk_band_thresholds():
    assert band_for_score(0) == RiskBand.LOW
    assert band_for_score(25) == RiskBand.LOW
    assert band_for_score(26) == RiskBand.MEDIUM
    assert band_for_score(60) == RiskBand.MEDIUM
    assert band_for_score(61) == RiskBand.HIGH
    assert band_for_score(85) == RiskBand.HIGH
    assert band_for_score(86) == RiskBand.PROHIBITED


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200 and resp.json()["status"] == "ok"


def test_openapi_generation(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    for required in [
        "/kyc/v1/cases", "/kyc/v1/cases/{case_id}",
        "/kyc/v1/cases/{case_id}/documents",
        "/kyc/v1/documents/{artifact_id}/extract",
        "/kyc/v1/cases/{case_id}/liveness-challenges",
        "/kyc/v1/liveness/{challenge_id}/evidence",
        "/kyc/v1/cases/{case_id}/submit", "/kyc/v1/cases/{case_id}/review",
        "/kyc/v1/review-queue",
        "/kyb/v1/cases", "/kyb/v1/cases/{case_id}",
        "/kyb/v1/cases/{case_id}/documents",
        "/kyb/v1/cases/{case_id}/registry-verification",
        "/kyb/v1/cases/{case_id}/beneficial-owners",
        "/kyb/v1/cases/{case_id}/submit", "/kyb/v1/cases/{case_id}/review",
        "/kyb/v1/review-queue",
        "/kyc-kyb/v1/audit", "/kyc-kyb/v1/audit/verify", "/healthz",
    ]:
        assert required in paths, required


def test_double_submit_conflict(client):
    case = create_case(client)
    cid = case["case_id"]
    attach(client, cid)
    pass_liveness(client, cid)
    client.post(f"/kyc/v1/cases/{cid}/submit", json={"state_id": TENANT})
    resp = client.post(f"/kyc/v1/cases/{cid}/submit", json={"state_id": TENANT})
    assert resp.status_code == 409


def test_sanctions_hit_kyc_prohibited(service):
    case = service.create_kyc_case(TENANT, "bad-actor", SubjectType.AGENT)
    service.sanctions.fixtures[f"sanctions:{case.subject_ref}"] = (
        RegistryStatus.MATCH.value, 0.99, {})
    service.attach_document(TENANT, case.case_id, DocumentType.NATIONAL_ID,
                            "s3://x", hash_of("d"), "u")
    service.attach_document(TENANT, case.case_id, DocumentType.PROOF_OF_ADDRESS,
                            "s3://y", hash_of("e"), "u")
    ch = service.issue_liveness_challenge(TENANT, case.case_id)
    service.submit_liveness_evidence(TENANT, ch.challenge_id, good_evidence(ch.nonce, "z"))
    decided = service.submit_kyc_case(TENANT, case.case_id)
    assert decided.risk_band == RiskBand.PROHIBITED
    assert decided.status == VerificationStatus.REJECTED
