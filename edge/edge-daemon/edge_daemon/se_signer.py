"""PKCS#11 secure-element signer for POS terminals.

Production POS terminals hold the device Ed25519 key inside a hardware
secure element (SE). The private key never leaves the token; signing
happens on-token via a PKCS#11 module (e.g. a vendor ``.so`` for an HSM,
smartcard, or TPM bridge) using the ``EdDSA`` mechanism.

Fail-closed policy (mirrors ``mod-kyc-kyb`` adapter idiom): selecting the
SE backend without the ``python-pkcs11`` driver installed, without
``EDGE_SE_PKCS11_MODULE`` configured, or without the labelled key on the
token raises :class:`~edge_daemon.crypto.AdapterUnavailableError` — the
daemon never silently falls back to a software key.

Configuration (environment):

- ``EDGE_SIGNER=se``               — select this backend
- ``EDGE_SE_PKCS11_MODULE``        — path to the vendor PKCS#11 module (required)
- ``EDGE_SE_SLOT``                 — slot index (default ``0``)
- ``EDGE_SE_KEY_LABEL``            — key label on the token (default ``edge-device``)
- ``EDGE_SE_PIN``                  — token PIN (optional; some SEs are PIN-free
                                     for signing after provisioning)
"""
from __future__ import annotations

import os
from typing import Optional

from .crypto import AdapterUnavailableError
from .models import b64u

try:  # optional dependency — production SEs only
    import pkcs11  # type: ignore
    from pkcs11 import Attribute, KeyType, Mechanism, ObjectClass  # type: ignore

    _PKCS11_AVAILABLE = True
except Exception:  # pragma: no cover - environment dependent
    pkcs11 = None  # type: ignore
    Attribute = KeyType = Mechanism = ObjectClass = None  # type: ignore
    _PKCS11_AVAILABLE = False

DEFAULT_KEY_LABEL = "edge-device"


class SecureElementSigner:
    """Ed25519 signer backed by a PKCS#11 secure element.

    Implements the :class:`edge_daemon.crypto.Signer` protocol. The private
    key is non-exportable by construction; only :meth:`sign` and
    :meth:`public_key_b64` cross the token boundary.
    """

    def __init__(
        self,
        device_id: str,
        module_path: str,
        *,
        slot: int = 0,
        key_label: str = DEFAULT_KEY_LABEL,
        pin: Optional[str] = None,
    ) -> None:
        if not _PKCS11_AVAILABLE:
            raise AdapterUnavailableError(
                "SecureElementSigner unavailable: python-pkcs11 is not installed"
            )
        if not module_path:
            raise AdapterUnavailableError(
                "SecureElementSigner unavailable: EDGE_SE_PKCS11_MODULE is not set"
            )
        self.device_id = device_id
        self.key_label = key_label
        try:  # pragma: no cover - requires hardware token
            lib = pkcs11.lib(module_path)
            slots = lib.get_slots()
            token = list(slots)[slot].get_token()
            self._session = token.open(user_pin=pin) if pin else token.open()
            keys = self._session.get_objects(
                {
                    Attribute.CLASS: ObjectClass.PRIVATE_KEY,
                    Attribute.KEY_TYPE: KeyType.ED25519,
                    Attribute.LABEL: key_label,
                }
            )
            self._private_key = next(iter(keys), None)
            if self._private_key is None:
                raise AdapterUnavailableError(
                    f"SecureElementSigner unavailable: no Ed25519 key labelled "
                    f"{key_label!r} on PKCS#11 slot {slot}"
                )
            pubs = self._session.get_objects(
                {
                    Attribute.CLASS: ObjectClass.PUBLIC_KEY,
                    Attribute.KEY_TYPE: KeyType.ED25519,
                    Attribute.LABEL: key_label,
                }
            )
            self._public_key = next(iter(pubs), None)
            if self._public_key is None:
                raise AdapterUnavailableError(
                    f"SecureElementSigner unavailable: no public key labelled "
                    f"{key_label!r} on PKCS#11 slot {slot}"
                )
        except AdapterUnavailableError:
            raise
        except Exception as exc:  # pragma: no cover - hardware/transport errors
            raise AdapterUnavailableError(
                f"SecureElementSigner unavailable: PKCS#11 initialisation failed: {exc}"
            ) from exc

    @classmethod
    def from_env(
        cls, device_id: str, env: "os._Environ[str] | None" = None
    ) -> "SecureElementSigner":
        """Build from ``EDGE_SE_*`` configuration; fails closed on any gap."""
        env = os.environ if env is None else env
        module_path = env.get("EDGE_SE_PKCS11_MODULE", "")
        slot = int(env.get("EDGE_SE_SLOT", "0"))
        key_label = env.get("EDGE_SE_KEY_LABEL", DEFAULT_KEY_LABEL)
        pin = env.get("EDGE_SE_PIN") or None
        return cls(
            device_id, module_path, slot=slot, key_label=key_label, pin=pin
        )

    def public_key_b64(self) -> str:
        """base64url raw 32-byte Ed25519 public key (same format as SoftwareSigner)."""
        # PKCS#11 EC_POINT for Ed25519 is the raw 32-byte key, per RFC 8032 /
        # PKCS#11 v3.0 curve profiles (no DER OCTET STRING wrapper for EdDSA).
        raw = bytes(self._public_key[Attribute.EC_POINT])  # pragma: no cover
        if len(raw) > 32 and raw[0] == 0x04:  # tolerate DER-wrapped modules
            raw = raw[-32:]  # pragma: no cover
        return b64u(raw)  # pragma: no cover

    def sign(self, message: bytes) -> str:
        """Sign ``message`` on-token via the EdDSA mechanism; returns base64url."""
        sig = self._private_key.sign(message, mechanism=Mechanism.EDDSA)  # pragma: no cover
        return b64u(bytes(sig))  # pragma: no cover
