"""Cross-verification of Android Keystore/StrongBox signing vectors.

``tests/fixtures/android_sig_vectors.json`` pins the byte-level contract an
Android (or any hardware SE) signer must implement to interoperate with the
server-side gateway verifier:

* canonical signing bytes — ``json.dumps({device_id, sequence, payload},
  sort_keys=True, separators=(",", ":"))`` UTF-8, identical to
  ``SignedRecord.signing_bytes()``;
* signature — raw Ed25519 over those bytes, base64url-encoded;
* public key — base64url raw 32-byte Ed25519 key.

Vectors were generated deterministically with the Python ``SoftwareSigner``
as the reference implementation (test-only key ``bytes(range(32))``). The
Kotlin shell under ``edge/android/`` reproduces the same canonicalization on
device; these vectors are the conformance check for that port.
"""
import json
import pathlib

import pytest

from edge_daemon.crypto import verify
from edge_daemon.models import SignedRecord, SyncedRecordValidator

FIXTURE = (
    pathlib.Path(__file__).parent / "fixtures" / "android_sig_vectors.json"
)


@pytest.fixture(scope="module")
def vectors():
    return json.loads(FIXTURE.read_text())["vectors"]


def test_vectors_present(vectors):
    assert len(vectors) >= 2


def test_android_signature_verifies_with_python_verifier(vectors):
    for v in vectors:
        msg = v["signing_bytes_utf8"].encode("utf-8")
        assert verify(v["public_key_b64"], msg, v["signature_b64"]), v["name"]


def test_vector_signing_bytes_match_canonicalization(vectors):
    """Rebuilding SignedRecord from the payload must reproduce the exact bytes."""
    for v in vectors:
        payload = SyncedRecordValidator(payload=v["payload"]).payload
        rec = SignedRecord(
            device_id=v["device_id"],
            sequence=v["sequence"],
            payload=payload,
            signature="",
            signer_public_key=v["public_key_b64"],
        )
        assert rec.signing_bytes().decode("utf-8") == v["signing_bytes_utf8"], v["name"]


def test_tampered_vector_fails(vectors):
    v = vectors[0]
    tampered = v["signing_bytes_utf8"].replace(v["device_id"], "POS-EVIL-001")
    assert not verify(
        v["public_key_b64"], tampered.encode("utf-8"), v["signature_b64"]
    )
