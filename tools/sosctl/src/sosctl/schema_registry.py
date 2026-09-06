"""Schema registry operations for ``sosctl schema publish|check-compat``.

Publishes the generated AsyncAPI JSON Schemas
(``contracts/asyncapi/registry/schemas/*.json``) to a schema registry
(Confluent-compatible or Apicurio v2) and checks compatibility of the
committed schemas against the latest registered versions.

Fail-closed: ``SCHEMA_REGISTRY_URL`` must be set; the commands refuse to run
against an implicit endpoint. Registry flavour is selected with
``SCHEMA_REGISTRY_TYPE`` (``confluent`` default, or ``apicurio``).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# tools/sosctl/src/sosctl/schema_registry.py -> repo root is parents[4]
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_REGISTRY_DIR = REPO_ROOT / "contracts" / "asyncapi" / "registry"


class SchemaRegistryConfigError(RuntimeError):
    """Raised when registry configuration is missing/invalid (fail-closed)."""


class SchemaRegistryError(RuntimeError):
    """Raised on registry HTTP failures or incompatibility."""


def _registry_config() -> Tuple[str, str]:
    url = os.environ.get("SCHEMA_REGISTRY_URL", "").strip().rstrip("/")
    if not url:
        raise SchemaRegistryConfigError(
            "SCHEMA_REGISTRY_URL is required (fail-closed: refusing to use an "
            "implicit schema registry endpoint)"
        )
    kind = os.environ.get("SCHEMA_REGISTRY_TYPE", "confluent").strip().lower()
    if kind not in ("confluent", "apicurio"):
        raise SchemaRegistryConfigError(
            f"unknown SCHEMA_REGISTRY_TYPE {kind!r}; valid: confluent, apicurio"
        )
    return url, kind


def _request(method: str, url: str, body: Optional[Dict[str, Any]] = None) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/vnd.schemaregistry.v1+json")
    if data is not None:
        req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SchemaRegistryError(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SchemaRegistryError(f"{method} {url} failed: {exc.reason}") from exc


def load_committed_schemas(registry_dir: Path = DEFAULT_REGISTRY_DIR) -> List[Tuple[str, Dict[str, Any]]]:
    """Return [(subject, schema)] from the committed registry (sorted)."""
    schemas_dir = Path(registry_dir) / "schemas"
    if not schemas_dir.is_dir():
        raise SchemaRegistryConfigError(
            f"no generated schemas at {schemas_dir}; run "
            "contracts/asyncapi/registry/generate.py first"
        )
    out = []
    for path in sorted(schemas_dir.glob("*.json")):
        out.append((path.stem, json.loads(path.read_text())))
    if not out:
        raise SchemaRegistryConfigError(f"no schema files under {schemas_dir}")
    return out


# -- Confluent-compatible registry -----------------------------------------


def _confluent_publish(base: str, subject: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    return _request(
        "POST",
        f"{base}/subjects/{subject}/versions",
        {"schemaType": "JSON", "schema": json.dumps(schema, sort_keys=True)},
    )


def _confluent_check_compat(base: str, subject: str, schema: Dict[str, Any]) -> bool:
    try:
        resp = _request(
            "POST",
            f"{base}/compatibility/subjects/{subject}/versions/latest",
            {"schemaType": "JSON", "schema": json.dumps(schema, sort_keys=True)},
        )
    except SchemaRegistryError as exc:
        if "HTTP 404" in str(exc):  # subject not registered yet -> compatible
            return True
        raise
    return bool(resp.get("is_compatible", False))


# -- Apicurio v2 registry ----------------------------------------------------


def _apicurio_publish(base: str, subject: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    group = os.environ.get("SCHEMA_REGISTRY_GROUP", "sos")
    data = json.dumps(schema, sort_keys=True).encode("utf-8")
    url = f"{base}/apis/registry/v2/groups/{group}/artifacts?ifExists=RETURN_OR_UPDATE"
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Registry-ArtifactId", subject)
    req.add_header("X-Registry-ArtifactType", "JSON")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SchemaRegistryError(f"POST {url} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SchemaRegistryError(f"POST {url} failed: {exc.reason}") from exc


def _apicurio_check_compat(base: str, subject: str, schema: Dict[str, Any]) -> bool:
    """Apicurio compatibility: compare against the latest registered version.

    Full rule-based compatibility is enforced server-side on update; locally
    we treat an absent artifact as compatible and an identical latest version
    as compatible, otherwise defer to the server by attempting no mutation.
    """
    group = os.environ.get("SCHEMA_REGISTRY_GROUP", "sos")
    url = f"{base}/apis/registry/v2/groups/{group}/artifacts/{subject}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            latest = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return True
        detail = exc.read().decode("utf-8", errors="replace")
        raise SchemaRegistryError(f"GET {url} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SchemaRegistryError(f"GET {url} failed: {exc.reason}") from exc
    return json.dumps(latest, sort_keys=True) == json.dumps(schema, sort_keys=True)


# -- Public operations --------------------------------------------------------


def publish_schemas(registry_dir: Path = DEFAULT_REGISTRY_DIR) -> List[Tuple[str, str]]:
    """Publish every committed schema. Returns [(subject, result summary)]."""
    base, kind = _registry_config()
    results = []
    for subject, schema in load_committed_schemas(registry_dir):
        if kind == "confluent":
            resp = _confluent_publish(base, subject, schema)
            results.append((subject, f"version id={resp.get('id')}"))
        else:
            resp = _apicurio_publish(base, subject, schema)
            results.append((subject, f"version={resp.get('version', '?')}"))
    return results


def check_compatibility(registry_dir: Path = DEFAULT_REGISTRY_DIR) -> List[Tuple[str, bool]]:
    """Check committed schemas against the registry. Returns [(subject, ok)]."""
    base, kind = _registry_config()
    results = []
    for subject, schema in load_committed_schemas(registry_dir):
        if kind == "confluent":
            ok = _confluent_check_compat(base, subject, schema)
        else:
            ok = _apicurio_check_compat(base, subject, schema)
        results.append((subject, ok))
    return results
