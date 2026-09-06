"""Adapter base types for mod-kyc-kyb."""
from __future__ import annotations

from typing import Dict, Optional, Protocol

from ..domain import (
    DocumentArtifact,
    DocumentType,
    ExtractionResult,
    RegistryStatus,
    RegistryVerification,
    RegistryName,
    sha256_hex,
    utcnow,
)


class AdapterUnavailableError(RuntimeError):
    """Raised when an optional production adapter dependency is missing."""


class DocumentAIAdapter(Protocol):
    def extract(
        self, artifact: DocumentArtifact, document_type: DocumentType
    ) -> ExtractionResult: ...


class CorporateRegistryAdapter(Protocol):
    def verify_company(
        self, legal_name: str, rc_number: str
    ) -> RegistryVerification: ...


class IdentityRegistryAdapter(Protocol):
    def verify_identity(
        self, nin: str, full_name: str, date_of_birth: Optional[str]
    ) -> RegistryVerification: ...


class SanctionsAdapter(Protocol):
    def screen(self, subject_ref: str, name: str) -> RegistryVerification: ...


def registry_response_hash(payload: Dict[str, object]) -> str:
    import json

    return sha256_hex(json.dumps(payload, sort_keys=True, default=str))


def make_verification(
    registry: RegistryName,
    status: RegistryStatus,
    fields_checked: list,
    confidence: float,
    payload: Dict[str, object],
) -> RegistryVerification:
    """Build a RegistryVerification storing only the hash of the payload."""
    return RegistryVerification(
        registry=registry,
        status=status,
        fields_checked=list(fields_checked),
        confidence=confidence,
        response_hash=registry_response_hash(payload),
        checked_at=utcnow(),
    )
