#!/usr/bin/env python3
"""Per-tenant Postgres backup orchestration (Stage 7.D ops readiness).

The SOS platform is schema-per-tenant (db/migrations): each state tenant
owns one Postgres schema guarded by Row-Level Security. Backups therefore
run **one pg_dump per tenant schema** so a restore (or a leak) can be
scoped to a single tenant.

Modes
-----
* ``--dry-run`` (**default**): print the deterministic backup plan —
  sorted tenant list, dump file names, pg_dump argv — without touching a
  database, the network, or the filesystem beyond the output directory.
  Running it twice produces byte-identical stdout.
* ``--execute``: run pg_dump per tenant schema, compute a SHA-256 over
  each dump, and write ``manifest.json`` (see below). Requires a DSN and
  the ``pg_dump`` binary — fail-closed when either is missing.

Encrypted upload
----------------
Set ``SOS_BACKUP_S3_BUCKET`` to upload the dumps + manifest to S3/MinIO
(boto3, optional dependency). Upload **requires** server-side encryption
with a KMS key (``S3_KMS_KEY_ID``); a bucket configured without a KMS key
is a hard failure, never a silent downgrade. ``S3_ENDPOINT_URL`` selects
MinIO. When the bucket variable is unset the upload step is reported as
an explicit SKIP — in ``SOS_ENV=production`` that skip is a hard failure.

Manifest
--------
``manifest.json`` (canonical JSON, sorted keys) records, per tenant:
``schema``, ``dump_file``, ``bytes``, ``sha256`` (of the dump file), and
``tables`` — a map of table name to ``{rows, sha256}`` captured from the
source schema at backup time. ``restore_verify.py`` replays a dump into
a scratch database and fails unless every table's row count and content
hash matches the manifest. The manifest is also the artefact the DR gate
(tests/gates/dr_drill.py) keys off.

Environment (execute mode)
--------------------------
SOS_BACKUP_PGDSN          libpq DSN for the tenant cluster (required)
SOS_BACKUP_TENANTS        comma-separated tenant ids (default: the six
                          launch states)
SOS_BACKUP_S3_BUCKET      bucket for encrypted upload (optional locally,
                          required in production)
S3_ENDPOINT_URL           MinIO endpoint (optional)
S3_KMS_KEY_ID             KMS key for SSE-KMS (required when bucket set)
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TENANTS = ("benue", "lagos", "nasarawa", "ogun", "osun", "taraba")

ENV_DSN = "SOS_BACKUP_PGDSN"
ENV_TENANTS = "SOS_BACKUP_TENANTS"
ENV_BUCKET = "SOS_BACKUP_S3_BUCKET"
ENV_KMS_KEY = "S3_KMS_KEY_ID"
ENV_ENDPOINT = "S3_ENDPOINT_URL"

DUMP_SUFFIX = ".dump.pgc"


class BackupConfigError(RuntimeError):
    """Fail-closed configuration error — missing DSN/tool/KMS material."""


def tenant_schema(tenant: str) -> str:
    """Postgres schema name for a tenant (matches db/migrations naming)."""
    return f"tenant_{tenant}"


def resolve_tenants(env: dict) -> list[str]:
    raw = env.get(ENV_TENANTS, "").strip()
    tenants = [t.strip() for t in raw.split(",") if t.strip()] if raw else list(DEFAULT_TENANTS)
    for t in tenants:
        if not t.replace("-", "").replace("_", "").isalnum():
            raise BackupConfigError(f"invalid tenant id {t!r}")
    return sorted(set(tenants))


def build_plan(tenants: list[str], outdir: Path) -> list[dict]:
    """Deterministic backup plan: one entry per tenant, sorted by schema."""
    plan = []
    for tenant in sorted(tenants):
        schema = tenant_schema(tenant)
        plan.append({
            "tenant": tenant,
            "schema": schema,
            "dump_file": f"{schema}{DUMP_SUFFIX}",
            "pg_dump_argv": [
                "pg_dump", "--format=custom", "--compress=6",
                f"--schema={schema}",
                "--file", str(outdir / f"{schema}{DUMP_SUFFIX}"),
                "<dsn>",
            ],
        })
    return plan


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(entries: list[dict], outdir: Path, *, tool_version: str = "1") -> Path:
    """Write manifest.json as canonical JSON (sorted keys, stable order)."""
    manifest = {
        "manifest_version": tool_version,
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "entries": sorted(entries, key=lambda e: e["schema"]),
    }
    path = outdir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return path


def verify_manifest(outdir: Path) -> list[str]:
    """Re-hash every dump listed in manifest.json; return mismatch errors.

    Pure filesystem check — used by the dry-run path of restore_verify.py
    and by the DR gate to prove backups are intact without a database.
    """
    manifest_path = outdir / "manifest.json"
    if not manifest_path.is_file():
        return [f"no manifest.json in {outdir}"]
    manifest = json.loads(manifest_path.read_text())
    errors: list[str] = []
    for entry in manifest.get("entries", []):
        dump = outdir / entry["dump_file"]
        if not dump.is_file():
            errors.append(f"{entry['schema']}: missing dump {entry['dump_file']}")
            continue
        digest = sha256_file(dump)
        if digest != entry.get("sha256"):
            errors.append(f"{entry['schema']}: sha256 mismatch (manifest {entry.get('sha256')}, actual {digest})")
        size = dump.stat().st_size
        if entry.get("bytes") is not None and size != entry["bytes"]:
            errors.append(f"{entry['schema']}: size mismatch (manifest {entry.get('bytes')}, actual {size})")
    return errors


def upload_to_s3(entries: list[dict], outdir: Path, env: dict) -> str:
    """Upload dumps + manifest with SSE-KMS. Fail-closed on any gap."""
    try:
        import boto3  # optional dependency
    except ImportError as exc:
        raise BackupConfigError(
            "boto3 is not installed but SOS_BACKUP_S3_BUCKET is set — "
            "install boto3 or unset the bucket (fail-closed)"
        ) from exc
    bucket = env.get(ENV_BUCKET, "").strip()
    if not bucket:
        raise BackupConfigError(f"{ENV_BUCKET} is empty")
    kms_key = env.get(ENV_KMS_KEY, "").strip()
    if not kms_key:
        raise BackupConfigError(
            f"{ENV_KMS_KEY} must be set when {ENV_BUCKET} is set — "
            "unencrypted backups are not permitted (fail-closed)"
        )
    kwargs: dict = {}
    endpoint = env.get(ENV_ENDPOINT, "").strip()
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    client = boto3.client("s3", **kwargs)
    prefix = dt.datetime.now(dt.timezone.utc).strftime("postgres/%Y/%m/%d/")
    files = [outdir / e["dump_file"] for e in entries] + [outdir / "manifest.json"]
    for f in files:
        client.upload_file(
            str(f), bucket, prefix + f.name,
            ExtraArgs={"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": kms_key},
        )
    return f"s3://{bucket}/{prefix}"


def table_fingerprints(dsn: str, schema: str) -> dict[str, dict]:
    """Row count + content SHA-256 per table in a schema (stable order).

    Captured at backup time into the manifest and recomputed by
    restore_verify.py after restoring into a scratch database — the
    hash check that proves a backup actually restores.
    """
    if shutil.which("psql") is None:
        raise BackupConfigError("psql binary not found on PATH (fail-closed)")

    def psql(sql: str, binary: bool = False):
        proc = subprocess.run(["psql", "-X", "-A", "-t", dsn, "-c", sql],
                              capture_output=True)
        if proc.returncode != 0:
            raise BackupConfigError(f"psql failed for schema {schema}: {proc.stderr.decode().strip()}")
        return proc.stdout if binary else proc.stdout.decode().strip()

    tables = [t for t in psql(
        f"SELECT tablename FROM pg_tables WHERE schemaname = '{schema}' ORDER BY tablename"
    ).splitlines() if t]
    fps: dict[str, dict] = {}
    for table in tables:
        rows = int(psql(f"SELECT count(*) FROM \"{schema}\".\"{table}\"") or "0")
        dump = psql(
            f"COPY (SELECT * FROM \"{schema}\".\"{table}\" ORDER BY 1) TO STDOUT", binary=True)
        fps[table] = {"rows": rows, "sha256": hashlib.sha256(dump).hexdigest()}
    return fps


def execute(plan: list[dict], outdir: Path, env: dict) -> Path:
    dsn = env.get(ENV_DSN, "").strip()
    if not dsn:
        raise BackupConfigError(f"{ENV_DSN} is required for --execute (fail-closed)")
    if shutil.which("pg_dump") is None:
        raise BackupConfigError("pg_dump binary not found on PATH (fail-closed)")
    outdir.mkdir(parents=True, exist_ok=True)
    entries = []
    for item in plan:
        argv = item["pg_dump_argv"][:-1] + [dsn]
        proc = subprocess.run(argv, capture_output=True, text=True)
        if proc.returncode != 0:
            raise BackupConfigError(
                f"pg_dump failed for schema {item['schema']}: {proc.stderr.strip()}"
            )
        dump = outdir / item["dump_file"]
        entries.append({
            "tenant": item["tenant"],
            "schema": item["schema"],
            "dump_file": item["dump_file"],
            "bytes": dump.stat().st_size,
            "sha256": sha256_file(dump),
            "tables": table_fingerprints(dsn, item["schema"]),
        })
    manifest = write_manifest(entries, outdir)
    bucket = env.get(ENV_BUCKET, "").strip()
    if bucket:
        dest = upload_to_s3(entries, outdir, env)
        print(f"uploaded {len(entries) + 1} objects to {dest}")
    elif env.get("SOS_ENV", "").lower() == "production":
        raise BackupConfigError(
            f"{ENV_BUCKET} unset in production — backups must leave the cluster (fail-closed)"
        )
    else:
        print("SKIP: SOS_BACKUP_S3_BUCKET unset — dumps remain local (allowed outside production)")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execute", action="store_true",
                        help="run pg_dump + manifest + optional upload (default is dry-run)")
    parser.add_argument("--outdir", type=Path, default=Path("backups/postgres"),
                        help="output directory for dumps + manifest.json")
    parser.add_argument("--tenants", default=None,
                        help="comma-separated tenant ids (overrides SOS_BACKUP_TENANTS)")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    if args.tenants:
        env[ENV_TENANTS] = args.tenants
    try:
        tenants = resolve_tenants(env)
        plan = build_plan(tenants, args.outdir)
        if not args.execute:
            print("DRY-RUN postgres backup plan (no database touched):")
            for item in plan:
                print(f"  {item['schema']} -> {item['dump_file']}")
                print(f"    argv: {' '.join(item['pg_dump_argv'])}")
            print(f"plan: {len(plan)} tenant schema(s); re-run with --execute to run pg_dump")
            return 0
        manifest = execute(plan, args.outdir, env)
        print(f"manifest: {manifest}")
        return 0
    except BackupConfigError as exc:
        print(f"FAIL-CLOSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
