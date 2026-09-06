"""WIM sensor adapter: deterministic simulated frames + fail-closed serial."""
import os

import pytest

from app.wim_adapter import (
    AdapterUnavailableError,
    AxleReading,
    SerialWIMSensor,
    SimulatedWIMSensor,
    WIMSensorAdapter,
    get_wim_sensor,
)


def _env(**overrides):
    env = {"PATH": os.environ.get("PATH", "")}
    env.update(overrides)
    return env


def test_default_adapter_is_simulated():
    sensor = get_wim_sensor(env=_env())
    assert isinstance(sensor, SimulatedWIMSensor)
    assert isinstance(sensor, WIMSensorAdapter)


def test_simulated_sensor_is_deterministic():
    a = SimulatedWIMSensor(seed="repro")
    b = SimulatedWIMSensor(seed="repro")
    frames_a = [a.read_axle_weights() for _ in range(5)]
    frames_b = [b.read_axle_weights() for _ in range(5)]
    for fa, fb in zip(frames_a, frames_b):
        assert fa.axle_weights_kg == fb.axle_weights_kg
        assert fa.speed_kmh == fb.speed_kmh
        assert fa.corridor_id == fb.corridor_id


def test_simulated_sensor_frames_are_well_formed():
    sensor = SimulatedWIMSensor()
    for _ in range(10):
        r = sensor.read_axle_weights()
        assert isinstance(r, AxleReading)
        assert len(r.axle_weights_kg) == 3
        assert all(w > 0 for w in r.axle_weights_kg)
        assert r.speed_kmh > 0


def test_unknown_backend_fails_closed():
    with pytest.raises(AdapterUnavailableError):
        get_wim_sensor(env=_env(WIM_ADAPTER="laser-magic"))


def test_serial_backend_without_port_fails_closed():
    with pytest.raises(AdapterUnavailableError, match="WIM_SERIAL_PORT"):
        get_wim_sensor(env=_env(WIM_ADAPTER="serial"))


def test_serial_backend_without_pyserial_fails_closed():
    try:
        import serial  # noqa: F401

        pytest.skip("pyserial installed; missing-driver path not reachable")
    except ImportError:
        pass
    with pytest.raises(AdapterUnavailableError, match="pyserial"):
        get_wim_sensor(env=_env(WIM_ADAPTER="serial", WIM_SERIAL_PORT="/dev/ttyUSB0"))


def test_parse_frame_with_plate():
    r = SerialWIMSensor.parse_frame(
        "WIM,corridor-ogi-abk,wim-stn-04,6200.0,11500.0,11800.0,62.5,LAG-123-XY\n"
    )
    assert r.corridor_id == "corridor-ogi-abk"
    assert r.station_id == "wim-stn-04"
    assert r.axle_weights_kg == [6200.0, 11500.0, 11800.0]
    assert r.speed_kmh == 62.5
    assert r.vehicle_plate == "LAG-123-XY"


def test_parse_frame_without_plate():
    r = SerialWIMSensor.parse_frame("WIM,c1,s1,6000.0,10000.0,55.0")
    assert r.axle_weights_kg == [6000.0, 10000.0]
    assert r.vehicle_plate is None


def test_parse_frame_malformed():
    with pytest.raises(ValueError):
        SerialWIMSensor.parse_frame("NOPE,1,2,3")
    with pytest.raises(ValueError):
        SerialWIMSensor.parse_frame("WIM,only,two,fields")
