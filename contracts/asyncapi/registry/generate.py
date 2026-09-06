#!/usr/bin/env python3
"""Deterministic generator for the AsyncAPI schema registry.

Parses ``contracts/asyncapi/*.yaml`` and emits, under
``contracts/asyncapi/registry/``:

- ``topics.json`` — one Kafka/Fluvio topic per AsyncAPI channel
  (channel name is the topic; the map is the single source of truth for
  :func:`eventbus.load_topic_map`).
- ``schemas/<channel>[.<message>].json`` — JSON Schema (draft 2020-12) for
  each channel's message payload, derived from the contract. AsyncAPI 2.x
  (``channels.<ch>.publish.message.payload``) and 3.0
  (``channels.<ch>.messages`` + ``components/messages``) are both supported.

Output is deterministic (sorted keys, stable ordering, no timestamps), so CI
can run ``--check`` to verify the committed registry matches the contracts.

Usage::

    python3 contracts/asyncapi/registry/generate.py           # write
    python3 contracts/asyncapi/registry/generate.py --check   # verify only
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

CONTRACTS_DIR = Path(__file__).resolve().parents[1]
REGISTRY_DIR = Path(__file__).resolve().parent

_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
_ID_BASE = "https://sos.ng/schemas/asyncapi"


def _safe_name(name: str) -> str:
    """Filesystem-safe artifact name for a channel/message (dots kept)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def _resolve_ref(doc: Dict[str, Any], ref: str) -> Dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"only local $ref supported, got {ref!r}")
    node: Any = doc
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _iter_channel_payloads(doc: Dict[str, Any]) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Yield (channel, message_name, payload_schema) for a contract document."""
    out: List[Tuple[str, str, Dict[str, Any]]] = []
    channels = doc.get("channels") or {}
    for channel in sorted(channels):
        body = channels[channel] or {}
        payloads: List[Tuple[str, Dict[str, Any]]] = []
        # AsyncAPI 2.x
        message = ((body.get("publish") or {}).get("message")) or (
            (body.get("subscribe") or {}).get("message")
        )
        if message:
            if "$ref" in message:
                message = _resolve_ref(doc, message["$ref"])
            payload = message.get("payload")
            if payload:
                payloads.append(("value", payload))
        # AsyncAPI 3.x
        for msg_name in sorted(body.get("messages") or {}):
            msg = body["messages"][msg_name]
            if "$ref" in msg:
                msg = _resolve_ref(doc, msg["$ref"])
            payload = msg.get("payload")
            if payload:
                payloads.append((_safe_name(msg_name), payload))
        if not payloads:
            # Channel with no inline payload: permissive envelope schema.
            payloads.append(("value", {"type": "object"}))
        for msg_name, payload in payloads:
            out.append((channel, msg_name, payload))
    return out


def _json_schema(contract: str, channel: str, message: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    suffix = "" if message == "value" else f"/{message}"
    file_stem = _safe_name(channel) + ("" if message == "value" else f".{message}")
    schema = {
        "$schema": _SCHEMA_DIALECT,
        "$id": f"{_ID_BASE}/{file_stem}.json",
        "title": f"{channel}{suffix}",
        "description": (
            f"AsyncAPI payload schema for channel {channel!r}"
            + ("" if message == "value" else f" message {message!r}")
            + f" (derived from contracts/asyncapi/{contract}; do not edit — "
            "regenerate with contracts/asyncapi/registry/generate.py)."
        ),
    }
    schema.update(payload)
    return schema


def _dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def build_registry(contracts_dir: Path = CONTRACTS_DIR) -> Dict[str, str]:
    """Return {relative_path: content} for every registry artifact."""
    artifacts: Dict[str, str] = {}
    channels_index: List[Dict[str, str]] = []
    for path in sorted(contracts_dir.glob("*.yaml")):
        docs = list(yaml.safe_load_all(path.read_text()))
        doc = next((d for d in docs if d and "asyncapi" in d), None)
        if doc is None:
            continue
        for channel, message, payload in _iter_channel_payloads(doc):
            name = _safe_name(channel)
            suffix = "" if message == "value" else f".{message}"
            schema_name = f"{name}{suffix}"
            artifacts[f"schemas/{schema_name}.json"] = _dumps(
                _json_schema(path.name, channel, message, payload)
            )
            channels_index.append(
                {
                    "channel": channel,
                    "topic": channel,
                    "contract": path.name,
                    "schema": f"schemas/{schema_name}.json",
                }
            )
    channels_index.sort(key=lambda e: (e["channel"], e["schema"]))
    artifacts["topics.json"] = _dumps(
        {
            "version": 1,
            "description": (
                "AsyncAPI channel -> topic map generated from contracts/asyncapi/*.yaml "
                "by contracts/asyncapi/registry/generate.py; do not edit by hand."
            ),
            "channels": channels_index,
        }
    )
    return artifacts


def _write(artifacts: Dict[str, str], registry_dir: Path) -> List[str]:
    written = []
    (registry_dir / "schemas").mkdir(parents=True, exist_ok=True)
    for rel, content in sorted(artifacts.items()):
        path = registry_dir / rel
        if not path.exists() or path.read_text() != content:
            path.write_text(content)
            written.append(rel)
    # Remove stale schema files no longer generated.
    expected = {rel for rel in artifacts if rel.startswith("schemas/")}
    for stale in sorted((registry_dir / "schemas").glob("*.json")):
        if f"schemas/{stale.name}" not in expected:
            stale.unlink()
            written.append(f"schemas/{stale.name} (removed)")
    return written


def _check(artifacts: Dict[str, str], registry_dir: Path) -> List[str]:
    diffs = []
    for rel, content in sorted(artifacts.items()):
        path = registry_dir / rel
        if not path.exists():
            diffs.append(f"{rel}: missing")
        elif path.read_text() != content:
            diffs.append(f"{rel}: out of date")
    expected = {rel for rel in artifacts if rel.startswith("schemas/")}
    schemas_dir = registry_dir / "schemas"
    if schemas_dir.exists():
        for extra in sorted(schemas_dir.glob("*.json")):
            if f"schemas/{extra.name}" not in expected:
                diffs.append(f"schemas/{extra.name}: stale (not generated)")
    return diffs


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed registry matches generator output (no writes)",
    )
    parser.add_argument("--contracts-dir", type=Path, default=CONTRACTS_DIR)
    parser.add_argument("--registry-dir", type=Path, default=REGISTRY_DIR)
    args = parser.parse_args(argv)

    artifacts = build_registry(args.contracts_dir)
    if args.check:
        diffs = _check(artifacts, args.registry_dir)
        if diffs:
            print("schema registry is out of date; run generate.py:", file=sys.stderr)
            for d in diffs:
                print(f"  {d}", file=sys.stderr)
            return 1
        print(f"OK: {len(artifacts)} artifact(s) match generator output")
        return 0
    written = _write(artifacts, args.registry_dir)
    print(f"registry: {len(artifacts)} artifact(s); {len(written)} file(s) changed")
    for rel in written:
        print(f"  wrote {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
