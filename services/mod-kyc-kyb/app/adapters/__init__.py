"""Adapters package."""
from .base import AdapterUnavailableError
from .paddleocr_adapter import PaddleOCRAdapter, SimulatedDocumentAIAdapter, parse_ocr_lines
from .docling_adapter import DoclingAdapter
from .vlm_adapter import VLMAdapter, SimulatedVLMAdapter
from .registry_adapters import (
    CacRegistryAdapter,
    NimcAdapter,
    SanctionsAdapter,
    FixtureRegistryAdapter,
)
from .registry_clients import CacClient, NimcClient, SanctionsClient
from .liveness import (
    BiometricDeviceAdapter,
    HardwareLivenessAdapter,
    LivenessEngine,
    get_liveness_adapter,
)

__all__ = [
    "AdapterUnavailableError",
    "PaddleOCRAdapter",
    "SimulatedDocumentAIAdapter",
    "parse_ocr_lines",
    "DoclingAdapter",
    "VLMAdapter",
    "SimulatedVLMAdapter",
    "CacRegistryAdapter",
    "NimcAdapter",
    "SanctionsAdapter",
    "FixtureRegistryAdapter",
    "CacClient",
    "NimcClient",
    "SanctionsClient",
    "LivenessEngine",
    "HardwareLivenessAdapter",
    "BiometricDeviceAdapter",
    "get_liveness_adapter",
]
