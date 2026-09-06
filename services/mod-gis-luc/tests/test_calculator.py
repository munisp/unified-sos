"""Unit tests for the policy-driven LUC calculator and tariff packs."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from luc_app.calculator import TariffError, compute_charge, relief_fraction
from luc_app.tariffs import (
    MAX_COMBINED_RELIEF_FRACTION,
    RELIEF_OWNER_OCCUPIER_RESIDENTIAL,
    RELIEF_PENSIONER,
    tariff_for_state,
)


OGUN = tariff_for_state("ogun")  # RESIDENTIAL 120 kobo/m², min ₦5,000


def test_gross_is_area_times_rate():
    c = compute_charge(OGUN, land_use_type="RESIDENTIAL", area_sqm=10_000.0)
    assert c.gross_amount_kobo == 1_200_000  # 10,000 m² × 120 kobo
    assert c.net_amount_kobo == 1_200_000


def test_statutory_minimum_applies():
    c = compute_charge(OGUN, land_use_type="AGRI", area_sqm=100.0)  # 2,500 kobo raw
    assert c.gross_amount_kobo == OGUN.minimum_bill_kobo == 500_000


def test_single_relief_applied():
    c = compute_charge(
        OGUN, land_use_type="RESIDENTIAL", area_sqm=10_000.0,
        relief_codes=[RELIEF_OWNER_OCCUPIER_RESIDENTIAL],
    )
    assert c.relief_fraction == 0.25
    assert c.net_amount_kobo == 900_000


def test_stacked_reliefs_capped_at_90_percent():
    frac = relief_fraction(OGUN, [RELIEF_OWNER_OCCUPIER_RESIDENTIAL, RELIEF_PENSIONER])
    assert frac == 0.75
    capped = relief_fraction(
        OGUN, [RELIEF_PENSIONER, RELIEF_PENSIONER]  # stacking beyond the cap
    )
    assert capped == MAX_COMBINED_RELIEF_FRACTION == 0.9


def test_unknown_land_use_and_relief_rejected():
    with pytest.raises(TariffError):
        compute_charge(OGUN, land_use_type="ORCHARD", area_sqm=100.0)
    with pytest.raises(TariffError):
        relief_fraction(OGUN, ["NO_SUCH_RELIEF"])


def test_undeployed_state_has_no_tariff():
    with pytest.raises(KeyError):
        tariff_for_state("taraba")


def test_per_state_rates_differ():
    # Same parcel bills differently per state policy pack (policy, not code).
    lagos = compute_charge(tariff_for_state("lagos"), land_use_type="COMMERCIAL", area_sqm=5_000.0)
    benue = compute_charge(tariff_for_state("benue"), land_use_type="COMMERCIAL", area_sqm=5_000.0)
    assert lagos.gross_amount_kobo == 3_250_000
    assert benue.gross_amount_kobo == 900_000
