"""Cryptographically signed digital title documents (e-C-of-O).

Titles are issued as compact **JWS** (RFC 7515) tokens using **Ed25519**
(``EdDSA``, RFC 8037) via the ``cryptography`` package (Apache-2.0/BSD dual
license — permissive per CONTRIBUTING.md SBOM policy).

Structure: ``base64url(header) . base64url(canonical_payload) . base64url(sig)``

* Header: ``{"alg": "EdDSA", "typ": "JWT", "kid": <key id>}``
* Payload: canonical JSON (sorted keys, no whitespace) of the title document —
  this canonicalisation is what makes signatures deterministic/verifiable.
* Verification recomputes the signing input and verifies the Ed25519 signature;
  payload tampering, key substitution, or truncation all fail closed.

Key custody in production: state signing keys live in OpenBao/Vault
(CONTRIBUTING.md rule: no secrets in repo); ``kid`` identifies the key version
so deeds remain verifiable across key rotation.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

JWS_ALGORITHM = "EdDSA"
JWS_TYPE = "JWT"


class SignatureError(ValueError):
    """Raised when a signed title fails structural or cryptographic checks."""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except (binascii.Error, ValueError) as exc:
        raise SignatureError(f"invalid base64url segment: {exc}") from exc


def canonical_json(payload: dict[str, Any]) -> bytes:
    """Deterministic JSON serialisation used as the JWS signing payload."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def generate_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Generate a fresh Ed25519 keypair (tests/dev; prod keys come from Vault)."""

    private = Ed25519PrivateKey.generate()
    return private, private.public_key()


def sign_payload(
    payload: dict[str, Any], private_key: Ed25519PrivateKey, kid: str = "sos-lands-dev-1"
) -> str:
    """Sign a title payload, returning a compact JWS token."""

    header = {"alg": JWS_ALGORITHM, "typ": JWS_TYPE, "kid": kid}
    signing_input = f"{_b64url_encode(canonical_json(header))}.{_b64url_encode(canonical_json(payload))}"
    signature = private_key.sign(signing_input.encode("ascii"))
    return f"{signing_input}.{_b64url_encode(signature)}"


@dataclass(frozen=True)
class VerifiedPayload:
    """Result of a successful JWS verification."""

    header: dict[str, Any]
    payload: dict[str, Any]
    kid: str


def verify_payload(token: str, public_key: Ed25519PublicKey) -> VerifiedPayload:
    """Verify a compact JWS token; raise SignatureError on any failure."""

    parts = token.split(".")
    if len(parts) != 3:
        raise SignatureError("JWS must have exactly 3 segments")
    header_b64, payload_b64, sig_b64 = parts

    header = json.loads(_b64url_decode(header_b64))
    if header.get("alg") != JWS_ALGORITHM:
        raise SignatureError(f"unsupported JWS alg {header.get('alg')!r}")

    signing_input = f"{header_b64}.{payload_b64}"
    try:
        public_key.verify(_b64url_decode(sig_b64), signing_input.encode("ascii"))
    except InvalidSignature as exc:
        raise SignatureError("Ed25519 signature verification failed") from exc

    payload = json.loads(_b64url_decode(payload_b64))
    # Defence in depth: re-serialise and ensure the payload was canonical, so a
    # reordered/whitespace-mutated token cannot pass with a recycled signature.
    if canonical_json(payload) != _b64url_decode(payload_b64):
        raise SignatureError("JWS payload is not in canonical form")
    return VerifiedPayload(header=header, payload=payload, kid=header.get("kid", ""))


def build_title_payload(
    *,
    tenant_state_id: str,
    parcel_id: str,
    parcel_uin: str,
    owner_stin: str,
    c_of_o_number: str,
    area_sqm: float,
    survey_plan_no: str,
    issued_at: str,
    workflow_id: str,
) -> dict[str, Any]:
    """Assemble the e-C-of-O title document that gets signed at issuance."""

    return {
        "doc_type": "E_C_OF_O",
        "issuer": f"ng.sos.{tenant_state_id}.lands-registry",
        "tenant_state_id": tenant_state_id,
        "parcel_id": parcel_id,
        "parcel_uin": parcel_uin,
        "owner_stin": owner_stin,
        "c_of_o_number": c_of_o_number,
        "area_sqm": area_sqm,
        "survey_plan_no": survey_plan_no,
        "issued_at": issued_at,
        "titling_workflow_id": workflow_id,
    }
