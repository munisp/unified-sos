"""GeoLibre project-authoring adapter.

Builds ``.geolibre.json`` project documents. When the ``geolibre`` Python
package is installed its project primitives are used; otherwise a
deterministic local fallback writes equivalent project JSON.

Redaction policy (per SPEC-GEOSPATIAL §3.4): no raw sensitive data to hosted
GeoLibre. Layer sources must reference self-hosted or object-storage URLs;
``web.geolibre.app`` / ``geolibre.app`` hosted endpoints and embedded
credentials are rejected. SENSITIVE datasets contribute metadata-only layers
(hashes + URIs, never raw geometry).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from ..domain import AdapterUnavailableError
from ..geometry import canonical_json, sha256_hex

#: Hosted GeoLibre origins that must never receive tenant data.
_BLOCKED_HOSTS = {"web.geolibre.app", "geolibre.app", "app.geolibre.app"}

#: URL schemes allowed for project layer sources (self-hosted / object storage).
_ALLOWED_SCHEMES = {"https", "s3", "gs", "file"}


class RedactionError(ValueError):
    pass


def validate_source_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise RedactionError(f"layer source scheme {parsed.scheme!r} is not allowed")
    host = (parsed.hostname or "").lower()
    if host in _BLOCKED_HOSTS or host.endswith(".geolibre.app"):
        raise RedactionError(f"hosted GeoLibre URL {host!r} is not allowed as a layer source")
    if parsed.username or parsed.password:
        raise RedactionError("layer source URLs must not embed credentials")


class LocalGeoLibreProjectBuilder:
    """Deterministic local fallback producing GeoLibre-compatible project JSON."""

    backend_name = "local-fallback"

    def build_project(self, *, name: str, layers: list[dict[str, Any]], redaction_level: str) -> dict[str, Any]:
        project = {
            "schema": "geolibre.project/v1",
            "generator": f"mod-geospatial/{self.backend_name}",
            "name": name,
            "redaction_level": redaction_level,
            "layers": layers,
        }
        project["project_hash"] = sha256_hex(canonical_json(project))
        return project


class GeoLibrePackageBuilder:
    """Production path using the optional ``geolibre`` package primitives."""

    backend_name = "geolibre"

    def __init__(self) -> None:
        try:
            import geolibre  # noqa: F401
        except ImportError as exc:
            raise AdapterUnavailableError("GeoLibrePackageBuilder requires the optional 'geolibre' package") from exc

    def build_project(self, *, name: str, layers: list[dict[str, Any]], redaction_level: str) -> dict[str, Any]:  # pragma: no cover
        import geolibre

        project = geolibre.Project(name=name)  # type: ignore[attr-defined]
        for layer in layers:
            project.add_layer(layer)
        doc = project.to_dict()
        doc["redaction_level"] = redaction_level
        doc["project_hash"] = sha256_hex(canonical_json(doc))
        return doc


def get_geolibre_builder(prefer_package: bool = False):
    """Factory: geolibre package when requested+installed, else the labeled
    deterministic local fallback. Fails closed in production if the package
    was explicitly required."""

    if prefer_package:
        from .base import is_production

        try:
            return GeoLibrePackageBuilder()
        except AdapterUnavailableError:
            if is_production():
                raise
    return LocalGeoLibreProjectBuilder()


def build_layer(
    *,
    layer_id: str,
    title: str,
    source_url: str,
    layer_type: str = "vector",
    sensitive: bool = False,
    geometry_hash: str | None = None,
) -> dict[str, Any]:
    """Build one redacted project layer. Sensitive layers carry only the
    source URI and geometry hash — never raw coordinates."""

    validate_source_url(source_url)
    layer: dict[str, Any] = {
        "id": layer_id,
        "title": title,
        "type": layer_type,
        "source": {"type": "url", "url": source_url},
        "redacted": sensitive,
    }
    if sensitive:
        layer["source"]["data"] = "REDACTED"
        if geometry_hash:
            layer["source"]["geometry_sha256"] = geometry_hash
    return layer
