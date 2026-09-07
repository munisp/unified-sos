"""Per-state whitelabel branding registry (WP tenant whitelabel).

Each of the 37 state tenants runs the platform as *its* product — its own
display name, portal title, colors, logo, locales and custom domain
(e.g. ``sos.lagosstate.gov.ng`` → "Lagos State One-Gov Portal").

Persistence model (GitOps-friendly):

* ``config/states/<state>/branding.json`` files in the repo are the source
  of truth and are loaded/merged at boot;
* runtime ``PUT`` updates are held as in-memory overrides layered on top of
  the seeded record;
* every update is appended to a hash-chained audit log
  (``services/_shared/hashchain.py``) and published as
  ``ng.sos.tenant.branding_updated`` (contracts/asyncapi/platform-events.yaml).

Data boundary: branding records are organisational metadata only (support
contacts are state switchboard numbers, never citizen PII).
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

# Shared canonical-JSON/SHA-256 hash-chain helpers (services/_shared),
# import-guarded like app/domain.py (minimal container images ship only the
# app package; then the registry still works, without chain helpers only the
# audit hash chain is unavailable and boot fails closed instead).
_SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(_SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICES_ROOT))
from _shared.hashchain import GENESIS_PREV_HASH, event_payload_hash, verify_event_chain  # noqa: E402

#: Locales the platform frontends support (en + Nigeria's three major languages).
SUPPORTED_LOCALES: frozenset[str] = frozenset({"en", "yo", "ha", "ig"})

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
#: Hostname-only custom domains (no scheme/path/port), e.g. sos.lagosstate.gov.ng.
_DOMAIN = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)

BRANDING_UPDATED_CHANNEL = "ng.sos.tenant.branding_updated"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_custom_domain(state: str) -> str:
    """Default vanity domain pattern; FCT has no 'state' suffix."""
    if state == "fct":
        return "sos.fct.gov.ng"
    return f"sos.{state.replace('_', '')}state.gov.ng"


class Branding(BaseModel):
    """A tenant's whitelabel branding record (metadata only)."""

    tenant_state_id: str = Field(..., pattern=r"^[a-z][a-z0-9_]{1,31}$")
    display_name: str = Field(..., min_length=2, max_length=120)
    portal_title: str = Field(..., min_length=2, max_length=160)
    tagline: str = Field(default="", max_length=200)
    primary_color: str = Field(..., description="Hex color #RRGGBB")
    secondary_color: str = Field(..., description="Hex color #RRGGBB")
    logo_url: str = Field(default="", max_length=300)
    favicon_url: str = Field(default="", max_length=300)
    support_email: str = Field(default="", max_length=200)
    support_phone: str = Field(default="", max_length=40)
    custom_domain: str = Field(..., description="Public vanity hostname")
    locales: list[str] = Field(..., min_length=1)
    default_locale: str = "en"
    pwa_theme_color: str = ""
    pwa_name: str = Field(default="", max_length=120)

    @field_validator("primary_color", "secondary_color", "pwa_theme_color")
    @classmethod
    def _hex_color(cls, value: str) -> str:
        if value == "":
            return value
        if not _HEX_COLOR.match(value):
            raise ValueError(f"'{value}' is not a #RRGGBB hex color")
        return value.lower()

    @field_validator("custom_domain")
    @classmethod
    def _domain(cls, value: str) -> str:
        if not _DOMAIN.match(value):
            raise ValueError(f"'{value}' is not a valid hostname")
        return value

    @field_validator("locales")
    @classmethod
    def _locales(cls, value: list[str]) -> list[str]:
        bad = [loc for loc in value if loc not in SUPPORTED_LOCALES]
        if bad:
            raise ValueError(
                f"unsupported locales {bad}; supported: {sorted(SUPPORTED_LOCALES)}"
            )
        return value

    @field_validator("default_locale")
    @classmethod
    def _default_locale(cls, value: str) -> str:
        if value not in SUPPORTED_LOCALES:
            raise ValueError(
                f"unsupported default_locale '{value}'; "
                f"supported: {sorted(SUPPORTED_LOCALES)}"
            )
        return value

    def model_post_init(self, __context: Any, /) -> None:
        if self.default_locale not in self.locales:
            raise ValueError(
                f"default_locale '{self.default_locale}' must be in locales {self.locales}"
            )


class BrandingAuditEntry(BaseModel):
    """One hash-chained branding audit record."""

    seq: int
    event_id: str
    state_id: str
    updated_by: str
    at: str
    prev_hash: str = GENESIS_PREV_HASH
    event_hash: str = ""


class BrandingRegistry:
    """Seeded-from-repo branding store with runtime overrides + audit chain."""

    def __init__(self, config_root: Path | None = None, event_bus: Any = None) -> None:
        self._lock = threading.Lock()
        #: Seeded records from config/states/<state>/branding.json (source of truth).
        self._seeded: dict[str, Branding] = {}
        #: Runtime overrides layered over seeds.
        self._overrides: dict[str, Branding] = {}
        #: custom_domain -> tenant_state_id (rebuilt on every mutation).
        self._domains: dict[str, str] = {}
        self._audit: list[BrandingAuditEntry] = []
        self._last_hash = GENESIS_PREV_HASH
        self._seq = 0
        if event_bus is None:
            try:
                from _shared.eventbus import InMemoryEventBus

                event_bus = InMemoryEventBus()
            except ImportError:  # pragma: no cover - minimal image
                event_bus = None
        self._bus = event_bus
        root = config_root or Path(
            os.environ.get(
                "SOS_STATES_CONFIG_ROOT",
                str(Path(__file__).resolve().parents[3] / "config" / "states"),
            )
        )
        self.load_seeds(root)

    # --- seeding ---------------------------------------------------------
    def load_seeds(self, config_root: Path) -> int:
        """Load/merge every ``<config_root>/<state>/branding.json`` at boot."""
        count = 0
        if not config_root.exists():
            return 0
        for path in sorted(config_root.glob("*/branding.json")):
            record = Branding(**json.loads(path.read_text()))
            if record.tenant_state_id != path.parent.name:
                raise ValueError(
                    f"{path}: tenant_state_id '{record.tenant_state_id}' does not "
                    f"match directory '{path.parent.name}'"
                )
            self._seeded[record.tenant_state_id] = record
            count += 1
        self._rebuild_domains()
        return count

    # --- reads -------------------------------------------------------------
    def get(self, state: str) -> Branding | None:
        """Effective branding: runtime override over seed. None if unknown."""
        return self._overrides.get(state) or self._seeded.get(state)

    def list_all(self) -> list[Branding]:
        states = sorted(set(self._seeded) | set(self._overrides))
        return [self.get(s) for s in states if self.get(s) is not None]  # type: ignore[misc]

    def verify_domain(self, domain: str) -> str | None:
        """Map a custom_domain to its registered tenant_state_id (gateway host routing)."""
        return self._domains.get(domain.strip().lower())

    # --- updates -------------------------------------------------------------
    def update(self, state: str, record: Branding, actor: str) -> BrandingAuditEntry:
        """Apply a runtime override, append to the hash chain, publish the event."""
        if record.tenant_state_id != state:
            raise ValueError(
                f"tenant_state_id '{record.tenant_state_id}' does not match path state '{state}'"
            )
        with self._lock:
            self._overrides[state] = record
            self._rebuild_domains()
            entry = self._append_audit_locked(state, actor)
        if self._bus is not None:
            self._bus.publish(
                BRANDING_UPDATED_CHANNEL,
                _BrandingUpdatedEvent(
                    state_id=state,
                    updated_by=actor,
                    entry_hash=entry.event_hash,
                    timestamp=entry.at,
                ),
            )
        return entry

    def _append_audit_locked(self, state: str, actor: str) -> BrandingAuditEntry:
        self._seq += 1
        entry = BrandingAuditEntry(
            seq=self._seq,
            event_id=f"bevt-{self._seq:06d}",
            state_id=state,
            updated_by=actor,
            at=_now(),
            prev_hash=self._last_hash,
        )
        entry.event_hash = event_payload_hash(
            entry.model_dump(exclude={"prev_hash", "event_hash"}), entry.prev_hash
        )
        self._last_hash = entry.event_hash
        self._audit.append(entry)
        return entry

    def _rebuild_domains(self) -> None:
        self._domains = {
            b.custom_domain.lower(): b.tenant_state_id for b in self.list_all()
        }

    # --- audit -------------------------------------------------------------
    def audit_entries(self) -> list[BrandingAuditEntry]:
        return list(self._audit)

    def verify_audit_chain(self) -> list[str]:
        """Recompute the branding audit chain; empty list means intact."""
        return verify_event_chain([e.model_dump() for e in self._audit])


class _BrandingUpdatedEvent(BaseModel):
    """Payload of the ng.sos.tenant.branding_updated AsyncAPI channel."""

    state_id: str
    updated_by: str
    entry_hash: str
    timestamp: str
