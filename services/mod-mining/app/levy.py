"""Levy computation and the federal-royalty hard constraint.

Rates are **policy-pack inputs**, never hard-coded per state
(CONTRIBUTING.md rule 1): callers pass a :class:`LevyPolicy`; the default
below is an example pack used by the test-suite only.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Dict, List

from .models import (
    AssayRecord,
    Consignment,
    LevyAssessment,
    LevyLine,
    MineralType,
)

#: Beneficiary names and account classes that are federal royalty territory.
FEDERAL_BENEFICIARY_TOKENS = ("FEDERAL", "ROYALTY", "FEDERATION")
FEDERAL_ACCOUNT_CLASS_MIN = 5000  # 5xxx = Federal Pass-Through


class RoyaltyConstraintViolation(ValueError):
    """A split rule or levy line attempted to claim a federal royalty share."""


@dataclass(frozen=True)
class SplitRule:
    """A statutory split rule for a state levy line."""

    beneficiary: str
    tigerbeetle_account_code: int
    share_bps: int  # basis points of the levy line

    def __post_init__(self) -> None:
        # HARD CONSTRAINT enforced by construction.
        upper = self.beneficiary.upper()
        if any(tok in upper for tok in FEDERAL_BENEFICIARY_TOKENS):
            raise RoyaltyConstraintViolation(
                f"split rule beneficiary {self.beneficiary!r} claims a federal "
                "royalty share — royalties accrue to the federation account only"
            )
        if self.tigerbeetle_account_code >= FEDERAL_ACCOUNT_CLASS_MIN:
            raise RoyaltyConstraintViolation(
                f"split rule targets 5xxx federal pass-through account "
                f"{self.tigerbeetle_account_code} — prohibited in state splits"
            )
        if not 0 < self.share_bps <= 10_000:
            raise ValueError("share_bps must be in (0, 10000]")


@dataclass(frozen=True)
class LevyPolicy:
    """Per-mineral state levy rates (policy-pack example, kobo per kg)."""

    rate_kobo_per_kg: Dict[MineralType, int] = field(
        default_factory=lambda: {
            MineralType.LITHIUM_SPODUMENE: 2_000,
            MineralType.COLUMBITE: 3_500,
            MineralType.TANTALITE: 4_000,
            MineralType.GOLD_ORE: 5_000,
            MineralType.GRANITE: 250,
            MineralType.SAPPHIRE: 6_000,
            MineralType.BARITE: 300,
        }
    )
    #: Assay-grade uplift (bps of levy per Li2O pct point) for spodumene.
    lithium_grade_uplift_bps_per_pct: int = 100
    #: Split applied to the state levy (defaults to consolidated revenue fund).
    split_rules: tuple = (
        SplitRule(
            beneficiary="STATE_CONSOLIDATED_REVENUE_FUND",
            tigerbeetle_account_code=3001,
            share_bps=10_000,
        ),
    )
    #: Federal royalty estimate rate for reporting parity (never split).
    federal_royalty_rate_kobo_per_kg: int = 500


DEFAULT_POLICY = LevyPolicy()


def assess_levy(
    consignment: Consignment,
    policy: LevyPolicy = DEFAULT_POLICY,
) -> LevyAssessment:
    """Compute the state-competent levy from tonnage + assay grade."""
    if consignment.weighbridge is None:
        raise ValueError("cannot assess levy before weighbridge reading")
    net_kg = consignment.weighbridge.net_weight_kg
    base = int(net_kg * policy.rate_kobo_per_kg[consignment.mineral_type])

    uplift = 0
    assay: AssayRecord | None = consignment.assay
    if (
        consignment.mineral_type == MineralType.LITHIUM_SPODUMENE
        and assay is not None
        and assay.lithium_oxide_grade_pct
    ):
        uplift = int(
            base * assay.lithium_oxide_grade_pct * policy.lithium_grade_uplift_bps_per_pct / 10_000
        )
    gross = base + uplift

    lines: List[LevyLine] = []
    allocated = 0
    for i, rule in enumerate(policy.split_rules):
        if i == len(policy.split_rules) - 1:
            amount = gross - allocated  # remainder to last line (no dust)
        else:
            amount = gross * rule.share_bps // 10_000
            allocated += amount
        lines.append(
            LevyLine(
                beneficiary=rule.beneficiary,
                tigerbeetle_account_code=rule.tigerbeetle_account_code,
                amount_kobo=amount,
            )
        )

    return LevyAssessment(
        assessment_id=f"LEV-{uuid.uuid4().hex[:12]}",
        consignment_id=consignment.consignment_id,
        state_id=consignment.state_id,
        lines=lines,
        total_kobo=gross,
        federal_royalty_reference_kobo=int(
            net_kg * policy.federal_royalty_rate_kobo_per_kg
        ),
    )
