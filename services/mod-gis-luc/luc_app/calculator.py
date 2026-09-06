"""Policy-driven LUC calculator.

Pure functions over the tariff packs — no I/O, so valuation logic is trivially
unit-testable and reusable from both the FastAPI valuation-run endpoint and
the event consumer.
"""

from __future__ import annotations

from dataclasses import dataclass

from .tariffs import MAX_COMBINED_RELIEF_FRACTION, StateTariff


class TariffError(ValueError):
    """Unknown land use type or relief code for the state's tariff pack."""


@dataclass(frozen=True)
class ChargeComputation:
    """Itemised LUC computation for one chargeable area."""

    charge_area_sqm: float
    rate_kobo_per_sqm: int
    gross_amount_kobo: int
    relief_fraction: float
    net_amount_kobo: int


def relief_fraction(tariff: StateTariff, relief_codes: list[str]) -> float:
    """Combined statutory relief fraction (stacked, capped at 90%)."""

    total = 0.0
    for code in relief_codes:
        if code not in tariff.reliefs:
            raise TariffError(
                f"relief code {code!r} not available in {tariff.tenant_state_id} tariff pack"
            )
        total += tariff.reliefs[code]
    return min(total, MAX_COMBINED_RELIEF_FRACTION)


def compute_charge(
    tariff: StateTariff,
    *,
    land_use_type: str,
    area_sqm: float,
    relief_codes: list[str] | None = None,
) -> ChargeComputation:
    """Compute the annual LUC for one parcel/footprint.

    gross  = max(area × rate, statutory minimum)
    net    = gross × (1 − combined relief)
    Integer kobo throughout (TigerBeetle-compatible minor units).
    """

    try:
        rate = tariff.rates_kobo_per_sqm[land_use_type]
    except KeyError:
        raise TariffError(
            f"land use type {land_use_type!r} has no rate in {tariff.tenant_state_id} tariff pack"
        ) from None

    gross = max(round(area_sqm * rate), tariff.minimum_bill_kobo)
    relief = relief_fraction(tariff, relief_codes or [])
    net = round(gross * (1.0 - relief))
    return ChargeComputation(
        charge_area_sqm=area_sqm,
        rate_kobo_per_sqm=rate,
        gross_amount_kobo=gross,
        relief_fraction=relief,
        net_amount_kobo=net,
    )
