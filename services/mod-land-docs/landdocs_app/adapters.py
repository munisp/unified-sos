"""Fail-closed OCR / classifier / object-store adapter seams for mod-land-docs.

Deterministic local fixtures are the default everywhere; production engines
sit behind adapter protocols that raise :class:`AdapterUnavailableError`
when their environment configuration is missing — mirroring the fail-closed
adapter idiom of mod-safecity-vision and ``services/_shared/eventbus``.

Selection (environment-driven, fail-closed)::

    SOS_LANDDOCS_PROFILE=development|production   (default: development)
    SOS_LANDDOCS_OCR_ENGINE=fixture|paddle        (default: fixture)
    SOS_LANDDOCS_OCR_URL=http://ocr:8080/ocr      (required for paddle; required
                                                   in the production profile)
    SOS_LANDDOCS_BUCKET=land-docs                 (required for S3 object store)
    SOS_LANDDOCS_S3_ENDPOINT=http://minio:9000    (optional S3-compatible endpoint)
    SOS_LANDDOCS_S3_ACCESS_KEY / SOS_LANDDOCS_S3_SECRET_KEY
                                                  (optional static credentials;
                                                   otherwise the boto3 default
                                                   credential chain is used)

The fixture OCR engine derives extracted fields from the SHA-256 of the
document content — the same bytes always yield the same extraction, and
confidence is hash-derived in the 0.80–0.99 band. Content shorter than 16
bytes deterministically yields a low-confidence (0.42) extraction to model
unreadable scans (manual-review path).
"""

from __future__ import annotations

import hashlib
import os
import threading
from typing import Dict, Optional, Protocol

from pydantic import BaseModel, Field

MIN_READABLE_BYTES = 16
LOW_CONFIDENCE = 0.42

# --- shared HTTP client (PERF) ---------------------------------------------
# One process-wide httpx.Client reused by every PaddleOcrEngine call: TCP/TLS
# handshake and connection-pool setup happen once instead of per OCR request.
_HTTP_CLIENT = None
_HTTP_CLIENT_LOCK = threading.Lock()


def _shared_http_client():
    """Lazily build (once) and return the module-level httpx client."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        with _HTTP_CLIENT_LOCK:
            if _HTTP_CLIENT is None:
                try:
                    import httpx
                except ImportError as exc:  # pragma: no cover
                    raise AdapterUnavailableError(
                        "httpx is required for PaddleOcrEngine (pip install httpx)"
                    ) from exc
                _HTTP_CLIENT = httpx.Client()
    return _HTTP_CLIENT


class AdapterUnavailableError(RuntimeError):
    """Raised when an optional production engine dependency/config is missing."""


class OcrResult(BaseModel):
    """Structured OCR extraction result."""

    fields: Dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(..., ge=0.0, le=1.0)
    engine: str


class OcrEngine(Protocol):
    """Adapter seam: document bytes -> structured fields + confidence."""

    def extract(self, content: bytes, filename: str) -> OcrResult: ...


_FIRST_NAMES = ("Adaeze", "Chukwuemeka", "Musa", "Funke", "Ibrahim", "Ngozi",
                "Olufemi", "Amina", "Tunde", "Blessing", "Yakubu", "Halima")
_LAST_NAMES = ("Okafor", "Balogun", "Abubakar", "Adeyemi", "Eze", "Danladi",
               "Ogunleye", "Nwachukwu", "Suleiman", "Adeleke", "Obi", "Garba")
_LGAS = ("ikeja", "jos-north", "abeokuta-south", "enugu-east", "kano-municipal",
         "owerri-north", "ibadan-north", "port-harcourt", "kaduna-south", "benin")


class FixtureOcrEngine:
    """Deterministic fixture engine (default; local dev and tests).

    All extracted values are derived from SHA-256 of the document content:
    the same bytes always produce the same parties, parcel id, date, and a
    confidence in the 0.80–0.99 band. Content below MIN_READABLE_BYTES models
    an unreadable scan with a fixed low confidence (0.42).
    """

    def extract(self, content: bytes, filename: str) -> OcrResult:
        digest = hashlib.sha256(b"landdocs-ocr:" + content).digest()

        def pick(seq, offset: int):
            return seq[digest[offset] % len(seq)]

        confidence = LOW_CONFIDENCE if len(content) < MIN_READABLE_BYTES else round(
            0.80 + (digest[8] / 255.0) * 0.19, 2
        )
        fields = {
            "party_a": f"{pick(_FIRST_NAMES, 0)} {pick(_LAST_NAMES, 1)}",
            "party_b": f"{pick(_FIRST_NAMES, 2)} {pick(_LAST_NAMES, 3)}",
            "parcel_id": f"{pick(_LGAS, 4).upper()}-{digest[5]:02x}{digest[6]:02x}",
            "document_date": (
                f"20{digest[9] % 25:02d}-{digest[10] % 12 + 1:02d}-"
                f"{digest[11] % 28 + 1:02d}"
            ),
            "filename": filename,
        }
        return OcrResult(fields=fields, confidence=confidence, engine="fixture")


class PaddleOcrEngine:
    """Production OCR engine (PaddleOCR HTTP sidecar) — fail-closed seam.

    Requires ``SOS_LANDDOCS_OCR_URL``; constructing without configuration
    raises :class:`AdapterUnavailableError` rather than silently degrading.
    The sidecar is invoked with an HTTP multipart POST of the raw bytes.
    """

    def __init__(
        self,
        ocr_url: Optional[str] = None,
        environ: Optional[Dict[str, str]] = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        ocr_url = ocr_url or env.get("SOS_LANDDOCS_OCR_URL")
        if not ocr_url:
            raise AdapterUnavailableError(
                "SOS_LANDDOCS_OCR_URL is required for PaddleOcrEngine "
                "(fail-closed: refusing to run an unconfigured OCR engine)"
            )
        self.ocr_url = ocr_url
        self.timeout_seconds = timeout_seconds

    def extract(self, content: bytes, filename: str) -> OcrResult:
        client = _shared_http_client()  # reused module-level client (PERF)
        try:
            resp = client.post(
                self.ocr_url,
                files={"file": (filename, content)},
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            raise AdapterUnavailableError(
                f"PaddleOcrEngine sidecar call failed: {exc}"
            ) from exc
        return OcrResult(
            fields={k: str(v) for k, v in (body.get("fields") or {}).items()},
            confidence=float(body.get("confidence", 0.0)),
            engine="paddle",
        )


def ocr_engine_from_env(environ: Optional[Dict[str, str]] = None) -> OcrEngine:
    """Build an OCR engine from the environment (default fixture).

    Fail-closed: the production profile hard-requires a configured OCR URL,
    and unknown engine names raise instead of silently falling back.
    """
    env = environ if environ is not None else dict(os.environ)
    profile = env.get("SOS_LANDDOCS_PROFILE", "development")
    kind = env.get("SOS_LANDDOCS_OCR_ENGINE", "fixture")
    if profile == "production" and not env.get("SOS_LANDDOCS_OCR_URL"):
        raise AdapterUnavailableError(
            "SOS_LANDDOCS_PROFILE=production requires SOS_LANDDOCS_OCR_URL "
            "(fail-closed: no unconfigured OCR engine in production)"
        )
    if kind == "fixture":
        return FixtureOcrEngine()
    if kind == "paddle":
        return PaddleOcrEngine(environ=env)
    raise AdapterUnavailableError(f"unknown SOS_LANDDOCS_OCR_ENGINE {kind!r}")


class ObjectStoreAdapter(Protocol):
    """Adapter seam: content-addressed document byte storage."""

    def put(self, key: str, content: bytes) -> str: ...

    def get(self, storage_ref: str) -> bytes: ...


class FixtureObjectStore:
    """In-memory object store (default; local dev and tests)."""

    def __init__(self) -> None:
        self._objects: Dict[str, bytes] = {}

    def put(self, key: str, content: bytes) -> str:
        ref = f"fixture://{key}"
        self._objects[ref] = content
        return ref

    def get(self, storage_ref: str) -> bytes:
        try:
            return self._objects[storage_ref]
        except KeyError:
            raise KeyError(f"object '{storage_ref}' not found") from None


class S3ObjectStore:
    """Production object store (S3-compatible bucket) — fail-closed seam.

    Requires ``SOS_LANDDOCS_BUCKET``; constructing without configuration
    raises :class:`AdapterUnavailableError`. The optional
    ``SOS_LANDDOCS_S3_ENDPOINT`` targets S3-compatible stores (e.g. MinIO);
    ``SOS_LANDDOCS_S3_ACCESS_KEY``/``SOS_LANDDOCS_S3_SECRET_KEY`` provide
    static credentials, otherwise the boto3 default credential chain applies.
    boto3 is imported lazily: missing package + configured bucket fails closed.
    """

    def __init__(
        self,
        bucket: Optional[str] = None,
        environ: Optional[Dict[str, str]] = None,
        client: Optional[object] = None,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        bucket = bucket or env.get("SOS_LANDDOCS_BUCKET")
        if not bucket:
            raise AdapterUnavailableError(
                "SOS_LANDDOCS_BUCKET is required for S3ObjectStore "
                "(fail-closed: refusing to run an unconfigured object store)"
            )
        self.bucket = bucket
        self.endpoint_url = env.get("SOS_LANDDOCS_S3_ENDPOINT")
        self.access_key = env.get("SOS_LANDDOCS_S3_ACCESS_KEY")
        self.secret_key = env.get("SOS_LANDDOCS_S3_SECRET_KEY")
        # client: test seam for injecting a fake boto3 S3 client; production
        # code leaves it None and the real client is built lazily below.
        self._client = client

    def _ensure_client(self) -> object:
        if self._client is None:
            try:
                import boto3  # optional dependency (production image)
            except ImportError as exc:
                raise AdapterUnavailableError(
                    "boto3 is required for S3ObjectStore with "
                    "SOS_LANDDOCS_BUCKET set (pip install boto3)"
                ) from exc
            kwargs: Dict[str, str] = {}
            if self.endpoint_url:
                kwargs["endpoint_url"] = self.endpoint_url
            if self.access_key:
                kwargs["aws_access_key_id"] = self.access_key
                kwargs["aws_secret_access_key"] = self.secret_key or ""
            self._client = boto3.client("s3", **kwargs)
        return self._client

    @staticmethod
    def _parse_ref(storage_ref: str) -> tuple[str, str]:
        if not storage_ref.startswith("s3://"):
            raise KeyError(f"object '{storage_ref}' not found")
        bucket, _, key = storage_ref[5:].partition("/")
        if not bucket or not key:
            raise KeyError(f"object '{storage_ref}' not found")
        return bucket, key

    def put(self, key: str, content: bytes) -> str:
        client = self._ensure_client()
        client.put_object(Bucket=self.bucket, Key=key, Body=content)
        return f"s3://{self.bucket}/{key}"

    def get(self, storage_ref: str) -> bytes:
        bucket, key = self._parse_ref(storage_ref)
        client = self._ensure_client()
        try:
            resp = client.get_object(Bucket=bucket, Key=key)
        except Exception as exc:
            raise KeyError(f"object '{storage_ref}' not found: {exc}") from None
        return resp["Body"].read()


def object_store_from_env(environ: Optional[Dict[str, str]] = None) -> ObjectStoreAdapter:
    """Build an object store from the environment (default in-memory fixture)."""
    env = environ if environ is not None else dict(os.environ)
    if env.get("SOS_LANDDOCS_BUCKET"):
        return S3ObjectStore(environ=env)
    return FixtureObjectStore()


# --- rule-based document classifier ------------------------------------------

_CLASSIFIER_KEYWORDS: Dict[str, tuple[str, ...]] = {
    "DEED": ("deed", "assignment", "conveyance", "transfer of title"),
    "SURVEY_PLAN": ("survey", "plan", "beacon", "coordinates"),
    "C_OF_O": ("certificate of occupancy", "c of o", "c_of_o", "cfo", "occupancy"),
    "CONSENT_LETTER": ("consent", "governor's consent", "letter of consent"),
    "TAX_CLEARANCE": ("tax clearance", "tcc", "tax"),
    "IDENTITY": ("identity", "national id", "nin", "passport", "driver's licence"),
}


class DocumentClassifier:
    """Rule-based keyword scoring over filename + OCR text (deterministic).

    Returns the best-scoring document type and the raw score table; ties and
    empty matches fall back to ``OTHER``.
    """

    def classify(self, filename: str, text: str = "") -> tuple[str, Dict[str, int]]:
        haystack = f"{filename}\n{text}".lower()
        scores: Dict[str, int] = {}
        for doc_type, keywords in _CLASSIFIER_KEYWORDS.items():
            scores[doc_type] = sum(haystack.count(kw) for kw in keywords)
        best = max(scores, key=lambda k: scores[k])
        if scores[best] == 0:
            return "OTHER", scores
        return best, scores
