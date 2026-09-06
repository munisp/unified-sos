"""Ed25519/JWS digital title signing tests."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lands_app.signing import (
    SignatureError,
    build_title_payload,
    generate_keypair,
    sign_payload,
    verify_payload,
)


def _payload() -> dict:
    return build_title_payload(
        tenant_state_id="ogun",
        parcel_id="3f2504e0-4f89-41d3-9a0c-0305e82c3301",
        parcel_uin="OG-ABK-0001",
        owner_stin="STIN-OG-0001234",
        c_of_o_number="OGUN/COFO/2026/000001",
        area_sqm=12345.6,
        survey_plan_no="OG/SVY/OG-ABK-0001",
        issued_at="2026-02-01T09:00:00+00:00",
        workflow_id="cadastral-titling-OG-ABK-0001-deadbeef",
    )


def test_sign_verify_roundtrip():
    private, public = generate_keypair()
    token = sign_payload(_payload(), private)
    assert token.count(".") == 2  # compact JWS: header.payload.signature
    verified = verify_payload(token, public)
    assert verified.payload == _payload()
    assert verified.header["alg"] == "EdDSA"


def test_tampered_payload_fails_verification():
    private, public = generate_keypair()
    token = sign_payload(_payload(), private)
    other = sign_payload({**_payload(), "owner_stin": "STIN-FRAUD"}, private)
    # Swap in a payload signed for different data — signature mismatch.
    franken = ".".join([token.split(".")[0], other.split(".")[1], token.split(".")[2]])
    with pytest.raises(SignatureError):
        verify_payload(franken, public)


def test_wrong_key_fails_verification():
    private, _ = generate_keypair()
    _, wrong_public = generate_keypair()
    token = sign_payload(_payload(), private)
    with pytest.raises(SignatureError, match="verification failed"):
        verify_payload(token, wrong_public)


def test_malformed_tokens_rejected():
    _, public = generate_keypair()
    with pytest.raises(SignatureError):
        verify_payload("not-a-jws", public)
    with pytest.raises(SignatureError):
        verify_payload("a.b.c.d", public)


def test_signature_is_deterministic_for_same_payload_and_key():
    private, _ = generate_keypair()
    # Ed25519 is deterministic; canonical JSON makes the whole token stable —
    # a property the deed-verification audit trail relies on.
    assert sign_payload(_payload(), private) == sign_payload(_payload(), private)
