"""Fail-closed adapter bindings for mod-agri-trace.

Two backend seams, both with deterministic fixtures by default and hard
fail-closed selection under ``SOS_AGRI_PROFILE=production`` (mirrors the
mod-erp-bridge ``build_adapter`` idiom):

- ``CommodityExchangeAdapter`` — price discovery against a commodity
  exchange (LCFE / AFEX seam via ``SOS_AGRI_EXCHANGE_URL``).
- ``WarehouseIoTAdapter`` — moisture/temperature telemetry ingest from
  agro-hub sensors (``SOS_AGRI_IOT_URL``).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol

from pydantic import BaseModel, Field


class AdapterUnavailableError(RuntimeError):
    """Raised when a production adapter is selected without configuration."""


class AdapterCallError(RuntimeError):
    """Raised when a live backend rejects or fails an adapter call."""


class PriceQuote(BaseModel):
    commodity: str
    tenant_state_id: str
    price_kobo_per_kg: int = Field(..., ge=0, description="Integer kobo per kg")
    currency: str = "NGN"
    source: str
    quoted_at: str


class TelemetryReading(BaseModel):
    reading_id: str
    tenant_state_id: str
    warehouse_id: str
    moisture_pct: float = Field(..., ge=0, le=100)
    temperature_c: float = Field(..., ge=-40, le=80)
    recorded_at: str


class CommodityExchangeAdapter(Protocol):
    """Price-discovery seam (LCFE/AFEX). Tenant-agnostic; the service layer
    supplies tenant and commodity context."""

    def get_price(self, tenant_state_id: str, commodity: str) -> PriceQuote: ...

    def health(self) -> bool: ...


class WarehouseIoTAdapter(Protocol):
    """Warehouse telemetry seam (moisture/temperature sensors)."""

    def ingest(self, tenant_state_id: str, warehouse_id: str,
               moisture_pct: float, temperature_c: float) -> TelemetryReading: ...

    def latest(self, tenant_state_id: str, warehouse_id: str) -> List[TelemetryReading]: ...

    def health(self) -> bool: ...


#: Deterministic fixture prices (integer kobo per kg). Fixed so replays and
#: golden tests are stable — the same commodity always yields the same quote.
FIXTURE_PRICES_KOBO_PER_KG: Dict[str, int] = {
    "yam": 62000,
    "cocoa": 340000,
    "tea": 180000,
    "rice": 89000,
    "sorghum": 41000,
    "maize": 38500,
    "cassava": 27000,
    "soybeans": 96000,
    "millet": 44000,
    "groundnut": 112000,
}

#: Fixed timestamp so fixture quotes are fully deterministic.
FIXTURE_QUOTED_AT = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat(timespec="seconds")


class FixtureCommodityExchange:
    """Deterministic fixture exchange — default local/test backend."""

    source = "fixture://exchange"

    def get_price(self, tenant_state_id: str, commodity: str) -> PriceQuote:
        key = commodity.lower()
        price = FIXTURE_PRICES_KOBO_PER_KG.get(key)
        if price is None:
            raise KeyError(f"no fixture price for commodity '{commodity}'")
        return PriceQuote(
            commodity=key, tenant_state_id=tenant_state_id,
            price_kobo_per_kg=price, currency="NGN", source=self.source,
            quoted_at=FIXTURE_QUOTED_AT,
        )

    def health(self) -> bool:
        return True


class FixtureWarehouseIoT:
    """In-memory fixture telemetry adapter — default local/test backend."""

    def __init__(self) -> None:
        self._readings: Dict[str, List[TelemetryReading]] = {}

    def ingest(self, tenant_state_id: str, warehouse_id: str,
               moisture_pct: float, temperature_c: float) -> TelemetryReading:
        key = f"{tenant_state_id}/{warehouse_id}"
        seq = len(self._readings.get(key, [])) + 1
        reading = TelemetryReading(
            reading_id=f"iot-{abs(hash((key, seq))) & 0xFFFFFFFF:08x}",
            tenant_state_id=tenant_state_id, warehouse_id=warehouse_id,
            moisture_pct=moisture_pct, temperature_c=temperature_c,
            recorded_at=FIXTURE_QUOTED_AT,
        )
        self._readings.setdefault(key, []).append(reading)
        return reading

    def latest(self, tenant_state_id: str, warehouse_id: str) -> List[TelemetryReading]:
        return list(self._readings.get(f"{tenant_state_id}/{warehouse_id}", []))

    def health(self) -> bool:
        return True


class HttpCommodityExchange:
    """Live exchange binding (LCFE/AFEX seam) via SOS_AGRI_EXCHANGE_URL."""

    def __init__(self, base_url: str) -> None:
        if not base_url:
            raise AdapterUnavailableError(
                "SOS_AGRI_EXCHANGE_URL is required for the production commodity-"
                "exchange adapter (fail-closed: refusing to guess an exchange)"
            )
        self.base_url = base_url.rstrip("/")

    def get_price(self, tenant_state_id: str, commodity: str) -> PriceQuote:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - defensive
            raise AdapterUnavailableError("httpx is required for the live exchange adapter") from exc
        resp = httpx.get(f"{self.base_url}/prices/{commodity.lower()}",
                         params={"tenant": tenant_state_id}, timeout=10.0)
        if resp.status_code != 200:
            raise AdapterCallError(
                f"exchange price lookup failed: HTTP {resp.status_code}"
            )
        data = resp.json()
        return PriceQuote(
            commodity=commodity.lower(), tenant_state_id=tenant_state_id,
            price_kobo_per_kg=int(data["price_kobo_per_kg"]),
            currency=data.get("currency", "NGN"),
            source=self.base_url,
            quoted_at=data.get("quoted_at", FIXTURE_QUOTED_AT),
        )

    def health(self) -> bool:
        try:
            import httpx

            return httpx.get(f"{self.base_url}/healthz", timeout=5.0).status_code == 200
        except Exception:
            return False


class HttpWarehouseIoT:
    """Live telemetry binding via SOS_AGRI_IOT_URL."""

    def __init__(self, base_url: str) -> None:
        if not base_url:
            raise AdapterUnavailableError(
                "SOS_AGRI_IOT_URL is required for the production warehouse-IoT "
                "adapter (fail-closed: refusing to guess a telemetry endpoint)"
            )
        self.base_url = base_url.rstrip("/")

    def ingest(self, tenant_state_id: str, warehouse_id: str,
               moisture_pct: float, temperature_c: float) -> TelemetryReading:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - defensive
            raise AdapterUnavailableError("httpx is required for the live IoT adapter") from exc
        resp = httpx.post(
            f"{self.base_url}/telemetry",
            json={"tenant_state_id": tenant_state_id, "warehouse_id": warehouse_id,
                  "moisture_pct": moisture_pct, "temperature_c": temperature_c},
            timeout=10.0,
        )
        if resp.status_code not in (200, 201):
            raise AdapterCallError(f"IoT ingest failed: HTTP {resp.status_code}")
        return TelemetryReading(**resp.json())

    def latest(self, tenant_state_id: str, warehouse_id: str) -> List[TelemetryReading]:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - defensive
            raise AdapterUnavailableError("httpx is required for the live IoT adapter") from exc
        resp = httpx.get(f"{self.base_url}/telemetry/{warehouse_id}",
                         params={"tenant": tenant_state_id}, timeout=10.0)
        if resp.status_code != 200:
            raise AdapterCallError(f"IoT read failed: HTTP {resp.status_code}")
        return [TelemetryReading(**r) for r in resp.json()]

    def health(self) -> bool:
        try:
            import httpx

            return httpx.get(f"{self.base_url}/healthz", timeout=5.0).status_code == 200
        except Exception:
            return False


def _profile(env: Dict[str, str]) -> str:
    return env.get("SOS_AGRI_PROFILE", "fixture").lower()


def build_exchange_adapter(env: Optional[Dict[str, str]] = None) -> CommodityExchangeAdapter:
    """Select the commodity-exchange adapter from ``SOS_AGRI_PROFILE``.

    Default ``fixture``/``local``/``test`` is the deterministic fixture.
    ``production``/``live`` fail closed at boot without SOS_AGRI_EXCHANGE_URL.
    """
    env = dict(os.environ if env is None else env)
    profile = _profile(env)
    if profile in ("fixture", "local", "test"):
        return FixtureCommodityExchange()
    if profile in ("production", "live"):
        return HttpCommodityExchange(env.get("SOS_AGRI_EXCHANGE_URL", ""))
    raise AdapterUnavailableError(
        f"unknown SOS_AGRI_PROFILE {profile!r}; expected fixture|local|test|production|live"
    )


def build_iot_adapter(env: Optional[Dict[str, str]] = None) -> WarehouseIoTAdapter:
    """Select the warehouse-IoT adapter (same fail-closed profile idiom)."""
    env = dict(os.environ if env is None else env)
    profile = _profile(env)
    if profile in ("fixture", "local", "test"):
        return FixtureWarehouseIoT()
    if profile in ("production", "live"):
        return HttpWarehouseIoT(env.get("SOS_AGRI_IOT_URL", ""))
    raise AdapterUnavailableError(
        f"unknown SOS_AGRI_PROFILE {profile!r}; expected fixture|local|test|production|live"
    )
