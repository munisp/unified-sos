"""CI verification of the NDVI change-detection threshold/polygonize logic
on the deterministic synthetic scene (fixtures/synthetic_ndvi.py)."""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fixtures.synthetic_ndvi import build_scene_pair
from local.ndvi_change_detection import (
    MIN_DISTURBANCE_HA,
    SuspectedActivity,
    compute_ndvi,
    compute_ndvi_delta_local,
    detect_disturbance_local,
)

ORIGIN_LAT, ORIGIN_LON = 8.0, 7.0  # synthetic scene origin (Nasarawa-ish)


@pytest.fixture()
def delta() -> np.ndarray:
    scene = build_scene_pair()
    ndvi0 = compute_ndvi(scene["b04_t0"], scene["b08_t0"])
    ndvi1 = compute_ndvi(scene["b04_t1"], scene["b08_t1"])
    return compute_ndvi_delta_local(ndvi0, ndvi1)


def test_ndvi_computation():
    red = np.array([[2000.0]])
    nir = np.array([[8000.0]])
    assert compute_ndvi(red, nir)[0, 0] == pytest.approx(0.6, abs=1e-6)
    # Zero-sum pixel (cloud/no-data guard) must not raise or produce NaN.
    assert compute_ndvi(np.array([[0.0]]), np.array([[0.0]]))[0, 0] == 0.0


def test_detects_mining_and_logging_alerts(delta):
    alerts = detect_disturbance_local(
        delta, state_id="nasarawa", scene_id="S2A_TEST_SCENE",
        origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON,
    )
    assert len(alerts) == 2

    mining = next(a for a in alerts if a.suspected_activity == SuspectedActivity.ILLEGAL_MINING)
    assert mining.disturbance_ha == pytest.approx(0.64, abs=1e-6)  # 8×8 px at 10 m
    assert mining.disturbance_ha >= MIN_DISTURBANCE_HA  # M5.2 gate

    logging_alert = next(a for a in alerts if a.suspected_activity == SuspectedActivity.ROSEWOOD_LOGGING)
    assert logging_alert.disturbance_ha == pytest.approx(5.0, abs=1e-6)  # 25×20 px

    # Alert centroids must fall inside the scene extent (600 m square).
    for a in alerts:
        assert ORIGIN_LON < a.centroid_lon < ORIGIN_LON + 0.0065
        assert ORIGIN_LAT - 0.0065 < a.centroid_lat < ORIGIN_LAT


def test_sub_threshold_and_gain_patches_filtered(delta):
    alerts = detect_disturbance_local(
        delta, state_id="nasarawa", scene_id="S2A_TEST_SCENE",
        origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON,
    )
    # The 0.04 ha clearing is below min_area_ha and the regrowth patch has a
    # positive delta — neither may alert.
    assert all(a.disturbance_ha >= MIN_DISTURBANCE_HA for a in alerts)
    assert len(alerts) == 2


def test_min_area_filter_is_configurable(delta):
    alerts = detect_disturbance_local(
        delta, state_id="taraba", scene_id="S2B_TEST_SCENE",
        origin_lat=ORIGIN_LAT, origin_lon=ORIGIN_LON, min_area_ha=0.03,
    )
    assert len(alerts) == 3  # the 0.04 ha patch now qualifies


def test_mismatched_scene_shapes_rejected():
    with pytest.raises(ValueError):
        compute_ndvi_delta_local(np.zeros((4, 4)), np.zeros((5, 5)))
