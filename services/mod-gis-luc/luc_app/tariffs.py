"""Per-state LUC tariff policy packs.

CONTRIBUTING.md rule 1: state-specific rates are *policy packs*, never
hard-coded behaviour — this module is the built-in default registry; in
deployment the same dataclasses are hydrated from ``config/states/<state>/``
JSON packs (see contracts/policy-packs/examples/ogun-luc-2026.json for the
companion revenue-split pack that governs how collected LUC is split).

All rates are **kobo per m² per annum** [DERIVED] — negotiating starting
points pending each state's gazetted LUC schedule [GAP].
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Relief codes (statutory relief categories common across state LUC laws).
RELIEF_OWNER_OCCUPIER_RESIDENTIAL = "OWNER_OCCUPIER_RESIDENTIAL"
RELIEF_PENSIONER = "PENSIONER"
RELIEF_AGRICULTURAL_HOLDING = "AGRICULTURAL_HOLDING"

#: Combined statutory relief is capped so a bill can never be fully relieved
#: away by stacking categories.
MAX_COMBINED_RELIEF_FRACTION = 0.9


@dataclass(frozen=True)
class StateTariff:
    """Tariff policy for one state tenant and effective year."""

    tenant_state_id: str
    effective_year: int
    #: land_use_type -> annual charge rate in kobo per square metre.
    rates_kobo_per_sqm: dict[str, int]
    #: relief code -> fractional reduction (0.0–1.0) of the gross charge.
    reliefs: dict[str, float] = field(default_factory=dict)
    #: Statutory minimum annual bill in kobo (applies before reliefs).
    minimum_bill_kobo: int = 0
    #: Land use assumed for unregistered encroachment findings (no title data).
    default_land_use_type: str = "RESIDENTIAL"
    gazette_reference: str = "[GAP] awaiting gazetted schedule"
    provenance: str = "[DERIVED]"


TARIFF_REGISTRY: dict[str, StateTariff] = {
    "ogun": StateTariff(
        tenant_state_id="ogun",
        effective_year=2026,
        rates_kobo_per_sqm={
            "RESIDENTIAL": 120, "COMMERCIAL": 400, "INDUSTRIAL": 300, "AGRI": 25,
        },
        reliefs={RELIEF_OWNER_OCCUPIER_RESIDENTIAL: 0.25, RELIEF_PENSIONER: 0.5, RELIEF_AGRICULTURAL_HOLDING: 0.6},
        minimum_bill_kobo=500_000,  # ₦5,000 floor
        gazette_reference="Ogun State Land Use Charge Regulations (LUC Reform enforcement)",
    ),
    "lagos": StateTariff(
        tenant_state_id="lagos",
        effective_year=2026,
        rates_kobo_per_sqm={
            "RESIDENTIAL": 200, "COMMERCIAL": 650, "INDUSTRIAL": 500, "AGRI": 40,
        },
        reliefs={RELIEF_OWNER_OCCUPIER_RESIDENTIAL: 0.25, RELIEF_PENSIONER: 0.5},
        minimum_bill_kobo=1_000_000,  # ₦10,000 floor
    ),
    "benue": StateTariff(
        tenant_state_id="benue",
        effective_year=2026,
        rates_kobo_per_sqm={
            "RESIDENTIAL": 60, "COMMERCIAL": 180, "INDUSTRIAL": 120, "AGRI": 10,
        },
        reliefs={RELIEF_OWNER_OCCUPIER_RESIDENTIAL: 0.3, RELIEF_AGRICULTURAL_HOLDING: 0.7},
        minimum_bill_kobo=200_000,  # ₦2,000 floor
        default_land_use_type="AGRI",
    ),
    "nasarawa": StateTariff(
        tenant_state_id="nasarawa",
        effective_year=2026,
        rates_kobo_per_sqm={
            "RESIDENTIAL": 80, "COMMERCIAL": 250, "INDUSTRIAL": 180, "AGRI": 15,
        },
        reliefs={RELIEF_OWNER_OCCUPIER_RESIDENTIAL: 0.25},
        minimum_bill_kobo=300_000,
    ),
}


def tariff_for_state(tenant_state_id: str, year: int | None = None) -> StateTariff:
    """Resolve the tariff pack for a tenant.

    :raises KeyError: if the state has no LUC tariff pack (module not deployed
        there — mod-gis-luc deploys to Ogun, Benue, Lagos, Nasarawa).
    """
    try:
        return TARIFF_REGISTRY[tenant_state_id]
    except KeyError:
        raise KeyError(
            f"no LUC tariff pack for state {tenant_state_id!r} "
            f"(deployed: {sorted(TARIFF_REGISTRY)})"
        ) from None
