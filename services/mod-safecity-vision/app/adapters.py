"""Fail-closed AI/ML/DL/CV adapter seams for mod-safecity-vision.

Deterministic local fixtures are the default everywhere; production engines
sit behind adapter protocols that raise :class:`AdapterUnavailableError`
when their environment configuration is missing — mirroring the fail-closed
adapter idiom of ``services/_shared/eventbus`` and mod-kyc-kyb.

Selection (environment-driven, fail-closed)::

    SOS_VISION_FACE_ENGINE=fixture|insightface   (default: fixture)
    SOS_VISION_MODEL_DIR=/opt/models/buffalo_l   (required for insightface)

Embeddings are 128-dimensional vectors, integer-quantized (int8 range
[-128, 127]) for fixtures — deterministic, hash-derived, and exact.
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import Dict, Optional, Protocol, Sequence

EMBEDDING_DIM = 128


class AdapterUnavailableError(RuntimeError):
    """Raised when an optional production engine dependency/config is missing."""


Embedding = tuple[int, ...]


def quantize(vector: Sequence[float]) -> Embedding:
    """Integer-quantize a float vector to int8 range (fixture canonical form)."""
    return tuple(max(-128, min(127, int(round(v * 127.0)))) for v in vector)


def cosine_similarity(a: Sequence[int | float], b: Sequence[int | float]) -> float:
    """Cosine similarity between two embeddings (pure stdlib, exact)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class FaceEngineAdapter(Protocol):
    """Adapter seam: image reference -> 128-d embedding."""

    def embed(self, image_ref: str) -> Embedding: ...


class FixtureFaceEngine:
    """Deterministic fixture engine (default; local dev and tests).

    Embeddings are derived from SHA-256 of the image reference: the same
    image reference always yields the same embedding, and an image reference
    that embeds the enrolled ``subject_ref`` (e.g. ``"frame://<subject_ref>"``)
    matches with cosine similarity 1.0. All values are int8-quantized.
    """

    def embed(self, image_ref: str) -> Embedding:
        dims: list[int] = []
        counter = 0
        while len(dims) < EMBEDDING_DIM:
            block = hashlib.sha256(f"{image_ref}:{counter}".encode()).digest()
            dims.extend(b - 128 for b in block)  # int8 range
            counter += 1
        return tuple(dims[:EMBEDDING_DIM])


class InsightFaceEngine:
    """Production face engine (InsightFace/ArcFace) — fail-closed seam.

    Requires ``SOS_VISION_MODEL_DIR`` (and the optional ``insightface``
    package); constructing without configuration raises
    :class:`AdapterUnavailableError` rather than silently degrading.
    """

    def __init__(
        self,
        model_dir: Optional[str] = None,
        environ: Optional[Dict[str, str]] = None,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        model_dir = model_dir or env.get("SOS_VISION_MODEL_DIR")
        if not model_dir:
            raise AdapterUnavailableError(
                "SOS_VISION_MODEL_DIR is required for InsightFaceEngine "
                "(fail-closed: refusing to run an unconfigured biometric engine)"
            )
        try:
            import insightface  # optional dependency  # noqa: F401
        except ImportError as exc:
            raise AdapterUnavailableError(
                "insightface is required for InsightFaceEngine "
                "(pip install insightface onnxruntime)"
            ) from exc
        self.model_dir = model_dir

    def embed(self, image_ref: str) -> Embedding:  # pragma: no cover
        raise AdapterUnavailableError(
            "InsightFaceEngine inference is wired on the GPU image; the seam "
            "is config-gated here so central services never depend on it."
        )


def face_engine_from_env(environ: Optional[Dict[str, str]] = None) -> FaceEngineAdapter:
    """Build a face engine from ``SOS_VISION_FACE_ENGINE`` (default fixture).

    Fail-closed: unknown engine names raise instead of silently falling back.
    """
    env = environ if environ is not None else dict(os.environ)
    kind = env.get("SOS_VISION_FACE_ENGINE", "fixture")
    if kind == "fixture":
        return FixtureFaceEngine()
    if kind == "insightface":
        return InsightFaceEngine(environ=env)
    raise AdapterUnavailableError(f"unknown SOS_VISION_FACE_ENGINE {kind!r}")
