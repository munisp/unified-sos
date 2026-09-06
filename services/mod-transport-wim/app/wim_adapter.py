"""WIM sensor adapters — hardware seam for weigh-in-motion sites.

Adapter seam mirroring the mod-kyc-kyb fail-closed idiom: the local
deterministic :class:`SimulatedWIMSensor` is the default for dev/CI; the
production :class:`SerialWIMSensor` (piezo / bending-plate controller over
RS-232/RS-485 via pyserial) is selected explicitly and fails closed with
:class:`AdapterUnavailableError` when its driver or port configuration is
missing.

Serial frame contract (one reading per line, ASCII, ``\\n``-terminated)::

    WIM,<corridor_id>,<station_id>,<axle_kg_1>,...,<axle_kg_n>,<speed_kmh>[,<plate>]

Example::

    WIM,corridor-ogi-abk,wim-stn-04,6200.0,11500.0,11800.0,62.5,LAG-123-XY

Fine-collection note: the fine/levy collection path
(:class:`~app.models.FineAssessment`, ledger transfer code 120) is an
interface-only seam — this module only *produces* readings; assessment and
collection remain in :class:`~app.service.WIMService` and the ledger
adapter. No collection hardware is integrated here.

Selection (environment):

- ``WIM_ADAPTER=simulated`` (default) — deterministic local sensor.
- ``WIM_ADAPTER=serial`` — requires ``pyserial`` installed and
  ``WIM_SERIAL_PORT`` set (e.g. ``/dev/ttyUSB0``); optional
  ``WIM_SERIAL_BAUD`` (default 9600). Any missing piece fails closed.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime
from typing import List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .models import utcnow


class AdapterUnavailableError(RuntimeError):
    """Raised when an optional production adapter dependency is missing."""


class AxleReading(BaseModel):
    """Raw axle reading straight from a WIM sensor (pre-assessment)."""

    corridor_id: str
    station_id: str
    axle_weights_kg: List[float]
    speed_kmh: float = Field(ge=0)
    vehicle_plate: Optional[str] = None
    recorded_at: datetime = Field(default_factory=utcnow)


@runtime_checkable
class WIMSensorAdapter(Protocol):
    """Hardware seam: one call yields the next vehicle's axle reading."""

    def read_axle_weights(self) -> AxleReading: ...


class SimulatedWIMSensor:
    """Deterministic local sensor for dev/CI — never use in production.

    Readings are derived from a SHA-256 chain over (seed, tick) so repeated
    runs produce identical frames. Default calibration produces a laden
    3-axle truck under typical single-axle limits.
    """

    def __init__(
        self,
        corridor_id: str = "corridor-sim",
        station_id: str = "wim-sim-01",
        seed: str = "simulated-wim",
    ) -> None:
        self.corridor_id = corridor_id
        self.station_id = station_id
        self.seed = seed
        self._tick = 0

    def _draw(self, label: str, lo: float, hi: float) -> float:
        h = hashlib.sha256(f"{self.seed}:{self._tick}:{label}".encode()).digest()
        frac = int.from_bytes(h[:4], "big") / 0xFFFFFFFF
        return round(lo + frac * (hi - lo), 1)

    def read_axle_weights(self) -> AxleReading:
        self._tick += 1
        axles = [
            self._draw("axle-0", 5500.0, 6500.0),
            self._draw("axle-1", 9500.0, 11500.0),
            self._draw("axle-2", 9500.0, 11500.0),
        ]
        return AxleReading(
            corridor_id=self.corridor_id,
            station_id=self.station_id,
            axle_weights_kg=axles,
            speed_kmh=self._draw("speed", 40.0, 80.0),
        )


class SerialWIMSensor:
    """Production serial sensor (pyserial). Fails closed when unavailable."""

    def __init__(
        self, port: str, baud: int = 9600, timeout_s: float = 5.0
    ) -> None:
        if not port:
            raise AdapterUnavailableError(
                "SerialWIMSensor unavailable: WIM_SERIAL_PORT is not set"
            )
        try:
            import serial  # type: ignore
        except Exception as exc:
            raise AdapterUnavailableError(
                "SerialWIMSensor unavailable: pyserial is not installed"
            ) from exc
        try:
            self._serial = serial.Serial(  # pragma: no cover - hardware
                port=port, baudrate=baud, timeout=timeout_s
            )
        except Exception as exc:  # pragma: no cover - hardware
            raise AdapterUnavailableError(
                f"SerialWIMSensor unavailable: cannot open {port!r}: {exc}"
            ) from exc

    @staticmethod
    def parse_frame(line: str) -> AxleReading:
        """Parse one ``WIM,...`` ASCII frame (module docstring documents format)."""
        parts = line.strip().split(",")
        if len(parts) < 6 or parts[0] != "WIM":
            raise ValueError(f"malformed WIM frame: {line!r}")
        corridor_id, station_id = parts[1], parts[2]
        tail = parts[3:]
        plate: Optional[str] = None
        try:
            float(tail[-1])
        except ValueError:
            plate = tail.pop() or None
        if len(tail) < 3:
            raise ValueError(f"malformed WIM frame (need >=1 axle + speed): {line!r}")
        *axles, speed = (float(x) for x in tail)
        return AxleReading(
            corridor_id=corridor_id,
            station_id=station_id,
            axle_weights_kg=list(axles),
            speed_kmh=speed,
            vehicle_plate=plate,
        )

    def read_axle_weights(self) -> AxleReading:
        line = self._serial.readline().decode("ascii", "replace")  # pragma: no cover
        if not line:  # pragma: no cover - hardware timeout
            raise AdapterUnavailableError("SerialWIMSensor: read timed out")
        return self.parse_frame(line)  # pragma: no cover


def get_wim_sensor(env: "os._Environ[str] | None" = None) -> WIMSensorAdapter:
    """Resolve the WIM sensor adapter from ``WIM_ADAPTER``; fail closed."""
    env = os.environ if env is None else env
    backend = env.get("WIM_ADAPTER", "simulated").strip().lower()
    if backend in ("", "simulated"):
        return SimulatedWIMSensor(
            corridor_id=env.get("WIM_SIM_CORRIDOR", "corridor-sim"),
            station_id=env.get("WIM_SIM_STATION", "wim-sim-01"),
        )
    if backend == "serial":
        return SerialWIMSensor(
            port=env.get("WIM_SERIAL_PORT", ""),
            baud=int(env.get("WIM_SERIAL_BAUD", "9600")),
        )
    raise AdapterUnavailableError(
        f"WIM_ADAPTER={backend!r} is not a supported sensor backend "
        "(supported: simulated, serial)"
    )
