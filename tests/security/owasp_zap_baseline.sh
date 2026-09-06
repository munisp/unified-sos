#!/usr/bin/env bash
# Stage 3 — OWASP ZAP baseline scan, per-service runner.
#
# Gate: OWASP Top 10 100% block (docs/procurement/acceptance-framework.md).
#
# Targets come from (first non-empty wins):
#   1. SOS_ZAP_TARGETS — space/newline separated base URLs
#   2. tests/security/zap-targets.txt — one URL per line (# comments ok)
#
# Each target is scanned with the ZAP baseline (docker image
# zaproxy/zap-stable, zap-baseline.py). Reports land in
# tests/evidence/zap-<timestamp>/ per target. Exit non-zero if any target
# reports WARN or FAIL findings.
#
# Skip-gated: without docker or without targets the script exits 3 with an
# explicit SKIP marker so the gate runner records SKIPPED_NO_TOOL /
# SKIPPED_NO_CREDENTIALS rather than a silent pass.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUTDIR="${SOS_ZAP_EVIDENCE_DIR:-$REPO_ROOT/tests/evidence/zap-$STAMP}"
ZAP_IMAGE="${SOS_ZAP_IMAGE:-zaproxy/zap-stable}"

collect_targets() {
  if [[ -n "${SOS_ZAP_TARGETS:-}" ]]; then
    printf '%s\n' $SOS_ZAP_TARGETS
  elif [[ -f "$REPO_ROOT/tests/security/zap-targets.txt" ]]; then
    grep -vE '^\s*(#|$)' "$REPO_ROOT/tests/security/zap-targets.txt"
  fi
}

mapfile -t TARGETS < <(collect_targets)

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  echo "SKIPPED_NO_CREDENTIALS: no ZAP targets (set SOS_ZAP_TARGETS or tests/security/zap-targets.txt)"
  exit 3
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "SKIPPED_NO_TOOL: docker not installed (ZAP baseline runs in a container)"
  exit 3
fi

mkdir -p "$OUTDIR"
rc=0
for target in "${TARGETS[@]}"; do
  slug="$(printf '%s' "$target" | sed -E 's#^[a-z]+://##; s#[^A-Za-z0-9._-]+#_#g')"
  echo "== zap baseline: $target -> $OUTDIR/$slug"
  if ! docker run --rm -t \
      -v "$OUTDIR:/zap/wrk:rw" \
      "$ZAP_IMAGE" zap-baseline.py \
      -t "$target" \
      -r "$slug.html" -J "$slug.json" \
      -I -z "-configfile /zap/wrk/zap.conf"; then
    echo "FAIL: zap baseline reported findings for $target"
    rc=1
  fi
done
echo "evidence: $OUTDIR"
exit "$rc"
