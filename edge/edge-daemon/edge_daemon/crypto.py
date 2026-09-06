"""Ed25519 signing for edge records.

.. note::

   **Hardware secure element stand-in.** Production POS terminals hold the
   device key inside a hardware SE (Android Keystore / StrongBox) and never
   export private key material; signing happens on-device via the SE API.
   This reference implementation uses a software Ed25519 key
   (``cryptography``) so the full protocol — canonical signing bytes,
   signature envelope, server-side verification — can be exercised and
   verified end-to-end in CI. Hardware-backed signers (PKCS#11 secure
   element — see ``edge_daemon.se_signer`` — or the Android Keystore/StrongBox
   shell under ``edge/android/``) implement the same :class:`Signer`
   contract; select via ``EDGE_SIGNER`` (:func:`get_signer`).
"""
from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

import os
from typing import Protocol, runtime_checkable

from .models import b64u, b64u_decode


class AdapterUnavailableError(RuntimeError):
    """Raised when an optional production adapter dependency is missing.

    Mirrors ``services/mod-kyc-kyb/app/adapters/base.py`` — selecting a
    hardware signer without its driver installed must fail closed, never
    fall back to software keys silently.
    """


@runtime_checkable
class Signer(Protocol):
    """Device signing contract.

    ``sign`` returns a base64url-encoded Ed25519 signature over the exact
    canonical signing bytes (see ``SignedRecord.signing_bytes``);
    ``public_key_b64`` returns the base64url raw Ed25519 public key.
    Server-side verification (:func:`verify`) is unchanged regardless of
    which implementation produced the signature.
    """

    device_id: str

    def sign(self, message: bytes) -> str: ...

    def public_key_b64(self) -> str: ...


class SoftwareSigner:
    """Device-bound Ed25519 signer (stand-in for hardware SE)."""

    def __init__(self, private_key: Ed25519PrivateKey, device_id: str) -> None:
        self._private_key = private_key
        self.device_id = device_id

    @classmethod
    def generate(cls, device_id: str) -> "SoftwareSigner":
        return cls(Ed25519PrivateKey.generate(), device_id)

    @classmethod
    def from_private_bytes(cls, raw: bytes, device_id: str) -> "SoftwareSigner":
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


# Backwards-compatible alias: existing callers/tests use ``DeviceSigner``.
DeviceSigner = SoftwareSigner


def get_signer(device_id: str, env: "os._Environ[str] | None" = None) -> Signer:
    """Resolve the device signer from configuration.

    ``EDGE_SIGNER`` selects the backend:

    - unset / ``software``: deterministic local default (:class:`SoftwareSigner`).
    - ``se``: hardware secure element via PKCS#11
      (:class:`edge_daemon.se_signer.SecureElementSigner`). Requires the
      ``python-pkcs11`` package and ``EDGE_SE_PKCS11_MODULE``; any other
      outcome raises :class:`AdapterUnavailableError` (fail closed).
    """
    env = os.environ if env is None else env
    backend = env.get("EDGE_SIGNER", "software").strip().lower()
    if backend in ("", "software"):
        return SoftwareSigner.generate(device_id)
    if backend == "se":
        from .se_signer import SecureElementSigner

        return SecureElementSigner.from_env(device_id, env)
    raise AdapterUnavailableError(
        f"EDGE_SIGNER={backend!r} is not a supported signer backend "
        "(supported: software, se)"
    )


def verify(public_key_b64: str, message: bytes, signature_b64: str) -> bool:
    """Verify an Ed25519 signature. Returns False (never raises) on failure."""
    try:
        key = Ed25519PublicKey.from_public_bytes(b64u_decode(public_key_b64))
        key.verify(b64u_decode(signature_b64), message)
        return True
    except (InvalidSignature, ValueError):
        return False
