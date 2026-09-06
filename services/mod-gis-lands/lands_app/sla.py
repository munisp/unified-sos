"""Per-state SLA instrumentation for the e-C-of-O titling workflow.

ADR-005: *"one workflow class, different SLA parameters"*. The stage topology
is shared code; only these clocks vary per state tenant. Provenance per
CONTRIBUTING.md rule 2:

* ``osun``   — 45-day C-of-O public commitment [LIVE] (docs/states/osun.md S3).
* ``benue``  — 60–90-day titling directive [LIVE]: warn at 60, breach at 90.
* ``lagos``  — consent-pipeline SLA [LIVE] (docs/states/lagos.md L3, consent
  ~2.5% + 1% stamp duty): the GOVERNOR_CONSENT stage is individually clocked.
* ``taraba`` — mandatory TAGIS clearance gateway for all land transactions
  [LIVE] (docs/states/taraba.md T1); the SURVEYOR_VALIDATION (TAGIS clearance)
  stage carries a tight clock. Day values are [DERIVED] negotiating defaults.
* ``ogun`` / ``nasarawa`` — platform default [DERIVED].
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# Stage keys (string values of titling.TitlingStage — kept as strings here so
# SLA packs stay plain configuration, per "policy packs, not binaries").
STAGE_SURVEYOR = "SURVEYOR_VALIDATION"
STAGE_MINISTRY = "MINISTRY_REVIEW"
STAGE_AG = "ATTORNEY_GENERAL_REVIEW"
STAGE_CONSENT = "GOVERNOR_CONSENT"


@dataclass(frozen=True)
class SLAConfig:
    """SLA clock configuration for one state tenant."""

    tenant_state_id: str
    #: Hard deadline for the whole application→issuance journey, in days.
    total_days: int
    #: Optional warning threshold (e.g. Benue warns at 60, breaches at 90).
    warn_days: int | None = None
    #: Per-stage deadlines in days; a stage absent here has no individual SLA.
    stage_days: dict[str, int] = field(default_factory=dict)
    provenance: str = "[DERIVED]"


SLA_REGISTRY: dict[str, SLAConfig] = {
    "osun": SLAConfig(
        tenant_state_id="osun",
        total_days=45,
        stage_days={STAGE_SURVEYOR: 10, STAGE_MINISTRY: 10, STAGE_AG: 10, STAGE_CONSENT: 15},
        provenance="[LIVE] Osun 45-day C-of-O commitment",
    ),
    "benue": SLAConfig(
        tenant_state_id="benue",
        total_days=90,
        warn_days=60,
        stage_days={STAGE_SURVEYOR: 20, STAGE_MINISTRY: 20, STAGE_AG: 20, STAGE_CONSENT: 30},
        provenance="[LIVE] Benue 60–90-day titling directive",
    ),
    "lagos": SLAConfig(
        tenant_state_id="lagos",
        total_days=90,
        stage_days={STAGE_SURVEYOR: 14, STAGE_MINISTRY: 21, STAGE_AG: 21, STAGE_CONSENT: 30},
        provenance="[LIVE] Lagos SLA-instrumented consent pipeline",
    ),
    "taraba": SLAConfig(
        tenant_state_id="taraba",
        total_days=60,
        stage_days={STAGE_SURVEYOR: 14, STAGE_MINISTRY: 15, STAGE_AG: 15, STAGE_CONSENT: 16},
        provenance="[LIVE] TAGIS clearance gateway; day values [DERIVED]",
    ),
    "ogun": SLAConfig(tenant_state_id="ogun", total_days=90),
    "nasarawa": SLAConfig(tenant_state_id="nasarawa", total_days=90),
}

_DEFAULT = SLAConfig(tenant_state_id="*", total_days=90)


def sla_for_state(tenant_state_id: str) -> SLAConfig:
    """Resolve the SLA pack for a tenant (falls back to the platform default)."""

    return SLA_REGISTRY.get(tenant_state_id, _DEFAULT)


@dataclass(frozen=True)
class StageBreach:
    stage: str
    elapsed_days: float
    allowed_days: int


@dataclass(frozen=True)
class SLAEvaluation:
    """Outcome of an SLA clock evaluation at a point in time."""

    tenant_state_id: str
    elapsed_days: float
    total_days_allowed: int
    total_breached: bool
    warn_threshold_reached: bool
    stage_breaches: tuple[StageBreach, ...]


def _days_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 86400.0


def evaluate_sla(
    config: SLAConfig,
    started_at: datetime,
    stage_intervals: dict[str, tuple[datetime, datetime | None]],
    now: datetime | None = None,
) -> SLAEvaluation:
    """Evaluate total + per-stage SLA clocks for a workflow instance.

    :param started_at: workflow (application) start timestamp.
    :param stage_intervals: stage value -> (entered_at, exited_at). A stage
        still open has ``exited_at is None`` and is clocked up to ``now``.
    :param now: evaluation instant (defaults to current UTC time).
    """

    now = now or datetime.now(timezone.utc)
    elapsed = _days_between(started_at, now)

    breaches: list[StageBreach] = []
    for stage, (entered, exited) in stage_intervals.items():
        allowed = config.stage_days.get(stage)
        if allowed is None:
            continue
        stage_elapsed = _days_between(entered, exited or now)
        if stage_elapsed > allowed:
            breaches.append(
                StageBreach(stage=stage, elapsed_days=stage_elapsed, allowed_days=allowed)
            )

    return SLAEvaluation(
        tenant_state_id=config.tenant_state_id,
        elapsed_days=elapsed,
        total_days_allowed=config.total_days,
        total_breached=elapsed > config.total_days,
        warn_threshold_reached=(
            config.warn_days is not None and elapsed > config.warn_days
        ),
        stage_breaches=tuple(breaches),
    )
