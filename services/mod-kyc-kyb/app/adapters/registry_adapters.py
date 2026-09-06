"""Registry adapters: CAC / NIMC / sanctions seams plus deterministic fixture.

All registry responses are hashed; raw registry payloads are never returned.
"""
from __future__ import annotations

from typing import Dict, Optional

from ..domain import RegistryName, RegistryStatus, RegistryVerification
from .base import AdapterUnavailableError, make_verification


class CacRegistryAdapter:
    """Corporate Affairs Commission seam. Fail closed unless a client is wired."""

    def __init__(self, enabled: bool = False, client=None, timeout_s: float = 10.0):
        self.enabled = enabled
        self._client = client
        self.timeout_s = timeout_s

    def verify_company(self, legal_name: str, rc_number: str) -> RegistryVerification:
        if not self.enabled or self._client is None:
            raise AdapterUnavailableError(
                "CAC registry adapter unavailable: no client configured"
            )
        payload = self._client.lookup_company(legal_name, rc_number)  # pragma: no cover
        status = RegistryStatus(payload.get("status", "UNAVAILABLE"))  # pragma: no cover
        return make_verification(  # pragma: no cover
            RegistryName.CAC, status,
            ["legal_name", "rc_number"],
            float(payload.get("confidence", 0.0)), payload,
        )


class NimcAdapter:
    """National Identity Management Commission seam. Fail closed unless wired."""

    def __init__(self, enabled: bool = False, client=None, timeout_s: float = 10.0):
        self.enabled = enabled
        self._client = client
        self.timeout_s = timeout_s

    def verify_identity(
        self, nin: str, full_name: str, date_of_birth: Optional[str]
    ) -> RegistryVerification:
        if not self.enabled or self._client is None:
            raise AdapterUnavailableError(
                "NIMC registry adapter unavailable: no client configured"
            )
        payload = self._client.verify_nin(nin, full_name, date_of_birth)  # pragma: no cover
        status = RegistryStatus(payload.get("status", "UNAVAILABLE"))  # pragma: no cover
        return make_verification(  # pragma: no cover
            RegistryName.NIMC, status,
            ["nin", "full_name", "date_of_birth"],
            float(payload.get("confidence", 0.0)), payload,
        )


class SanctionsAdapter:
    """Sanctions screening seam. Fail closed unless wired."""

    def __init__(self, enabled: bool = False, client=None):
        self.enabled = enabled
        self._client = client

    def screen(self, subject_ref: str, name: str) -> RegistryVerification:
        if not self.enabled or self._client is None:
            raise AdapterUnavailableError(
                "Sanctions adapter unavailable: no client configured"
            )
        payload = self._client.screen(subject_ref, name)  # pragma: no cover
        hit = bool(payload.get("hit", False))  # pragma: no cover
        status = RegistryStatus.MATCH if hit else RegistryStatus.NOT_FOUND  # pragma: no cover
        return make_verification(  # pragma: no cover
            RegistryName.SANCTIONS, status, ["name", "subject_ref"],
            float(payload.get("confidence", 0.0)), payload,
        )


class FixtureRegistryAdapter:
    """Deterministic registry adapter for tests.

    ``fixtures`` maps a lookup key to ``(status, confidence, extra)``.
    """

    def __init__(self, fixtures: Optional[Dict[str, tuple]] = None):
        self.fixtures = fixtures or {}

    def _resolve(self, key: str, registry: RegistryName, fields: list) -> RegistryVerification:
        entry = self.fixtures.get(key)
        if entry is None:
            return make_verification(registry, RegistryStatus.NOT_FOUND, fields, 0.0, {"key": key})
        status, confidence, extra = entry
        payload = {"key": key, **(extra or {})}
        return make_verification(registry, RegistryStatus(status), fields, confidence, payload)

    def verify_company(self, legal_name: str, rc_number: str) -> RegistryVerification:
        return self._resolve(
            f"cac:{rc_number}", RegistryName.CAC, ["legal_name", "rc_number"]
        )

    def verify_identity(
        self, nin: str, full_name: str, date_of_birth: Optional[str]
    ) -> RegistryVerification:
        return self._resolve(
            f"nimc:{nin}", RegistryName.NIMC, ["nin", "full_name", "date_of_birth"]
        )

    def screen(self, subject_ref: str, name: str) -> RegistryVerification:
        return self._resolve(
            f"sanctions:{subject_ref}", RegistryName.SANCTIONS, ["name", "subject_ref"]
        )
