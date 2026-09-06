"""Ed25519 signing for edge records.

.. note::

   **Hardware secure element stand-in.** Production POS terminals hold the
   device key inside a hardware SE (Android Keystore / StrongBox) and never
   export private key material; signing happens on-device via the SE API.
   This reference implementation uses a software Ed25519 key
   (``cryptography``) so the full protocol — canonical signing bytes,
   signature envelope, server-side verification — can be exercised and
   verified end-to-end in CI. Swapping in an SE-backed signer only requires
   reimplementing :class:`DeviceSigner.sign`.
"""
from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .models import b64u, b64u_decode


class DeviceSigner:
    """Device-bound Ed25519 signer (stand-in for hardware SE)."""

    def __init__(self, private_key: Ed25519PrivateKey, device_id: str) -> None:
        self._private_key = private_key
        self.device_id = device_id

    @classmethod
    def generate(cls, device_id: str) -> "DeviceSigner":
        return cls(Ed25519PrivateKey.generate(), device_id)

    @classmethod
    def from_private_bytes(cls, raw: bytes, device_id: str) -> "DeviceSigner":
        return cls(Ed25519PrivateKey.from_private_bytes(raw), device_id)

    def private_bytes(self) -> bytes:
        """Export raw private key bytes (reference impl only — an SE never does this)."""
        from cryptography.hazmat.primitives import serialization

        return self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    @property
    def public_key(self) -> Ed25519PublicKey:
        key = self._private_key.public_key
        # cryptography >= 44 exposes public_key as a property; older as a method
        return key() if callable(key) else key

    def public_key_b64(self) -> str:
        from cryptography.hazmat.primitives import serialization

        raw = self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return b64u(raw)

    def sign(self, message: bytes) -> str:
        """Sign ``message`` and return a base64url signature."""
        return b64u(self._private_key.sign(message))


def verify(public_key_b64: str, message: bytes, signature_b64: str) -> bool:
    """Verify an Ed25519 signature. Returns False (never raises) on failure."""
    try:
        key = Ed25519PublicKey.from_public_bytes(b64u_decode(public_key_b64))
        key.verify(b64u_decode(signature_b64), message)
        return True
    except (InvalidSignature, ValueError):
        return False
