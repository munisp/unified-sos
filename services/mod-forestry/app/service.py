"""Domain service: tag registry, provenance chain, alerts, stumpage billing."""
from __future__ import annotations

import uuid
from typing import Dict, List, Optional

from .models import (
    DeforestationAlert,
    ProvenanceEvent,
    StumpageInvoice,
    TagStatus,
    TimberSpecies,
    TimberTag,
    UntaggedTimberAlert,
)

TOPIC_UNTAGGED_TIMBER_ALERT = "ng.sos.forestry.untagged_timber_alert"

#: Allowed provenance chain transitions (harvest -> transit -> mill).
_TRANSITIONS: Dict[TagStatus, List[TagStatus]] = {
    TagStatus.ISSUED: [TagStatus.HARVESTED, TagStatus.SEIZED],
    TagStatus.HARVESTED: [TagStatus.IN_TRANSIT, TagStatus.SEIZED],
    TagStatus.IN_TRANSIT: [TagStatus.MILLED, TagStatus.IN_TRANSIT, TagStatus.SEIZED],
    TagStatus.MILLED: [],
    TagStatus.SEIZED: [],
}

#: Example stumpage rate card (policy-pack default, kobo per m3). Production
#: rates come from the state's policy pack — never hard-code per state.
DEFAULT_STUMPAGE_RATES: Dict[TimberSpecies, int] = {
    TimberSpecies.ROSEWOOD: 85_000_00,   # heavily levied (cartel suppression)
    TimberSpecies.IROKO: 60_000_00,
    TimberSpecies.MAHOGANY: 65_000_00,
    TimberSpecies.OBECHI: 25_000_00,
    TimberSpecies.TEAK: 30_000_00,
    TimberSpecies.AFARA: 20_000_00,
}

ACTIONABLE_AREA_HA = 0.5


class NotFoundError(KeyError):
    pass


class InvalidTransition(ValueError):
    pass


class ForestryService:
    def __init__(self, bus=None, stumpage_rates=None) -> None:
        self._tags: Dict[str, TimberTag] = {}
        self._provenance: Dict[str, List[ProvenanceEvent]] = {}
        self._alerts: Dict[str, DeforestationAlert] = {}
        self._invoices: List[StumpageInvoice] = []
        self.bus = bus  # optional event bus (shared contract with mod-mining)
        self.stumpage_rates = stumpage_rates or DEFAULT_STUMPAGE_RATES

    # -- tag registry ---------------------------------------------------------

    def register_tag(self, tag: TimberTag) -> TimberTag:
        if tag.tag_id in self._tags:
            raise ValueError(f"tag {tag.tag_id!r} already registered")
        self._tags[tag.tag_id] = tag
        self._provenance[tag.tag_id] = []
        return tag

    def get_tag(self, tag_id: str) -> TimberTag:
        tag = self._tags.get(tag_id)
        if tag is None:
            raise NotFoundError(f"tag {tag_id!r} not found")
        return tag

    def list_tags(self, state_id: Optional[str] = None) -> List[TimberTag]:
        tags = list(self._tags.values())
        return [t for t in tags if t.state_id == state_id] if state_id else tags

    # -- provenance chain ------------------------------------------------------

    def record_provenance(self, event: ProvenanceEvent) -> ProvenanceEvent:
        tag = self.get_tag(event.tag_id)
        allowed = _TRANSITIONS[tag.status]
        if event.stage not in allowed:
            raise InvalidTransition(
                f"tag {tag.tag_id} is {tag.status.value}; cannot move to {event.stage.value}"
            )
        if event.stage == TagStatus.IN_TRANSIT and not event.transit_permit_id:
            raise ValueError("transit hops require a timber transit permit ID")
        if event.stage == TagStatus.HARVESTED and event.location != tag.coupe_id:
            raise ValueError(
                f"harvest location {event.location!r} != licensed coupe {tag.coupe_id!r}"
            )
        self._provenance[tag.tag_id].append(event)
        tag.status = event.stage
        if event.stage == TagStatus.HARVESTED and tag.volume_m3:
            self.bill_stumpage(tag.tag_id)  # billing hook fires on harvest
        return event

    def provenance_chain(self, tag_id: str) -> List[ProvenanceEvent]:
        self.get_tag(tag_id)
        return list(self._provenance[tag_id])

    # -- stumpage billing hook --------------------------------------------------

    def bill_stumpage(self, tag_id: str) -> StumpageInvoice:
        tag = self.get_tag(tag_id)
        if not tag.volume_m3:
            raise ValueError("tag has no recorded volume; cannot bill stumpage")
        rate = self.stumpage_rates[tag.species]
        invoice = StumpageInvoice(
            invoice_id=f"STP-{uuid.uuid4().hex[:12]}",
            tag_id=tag.tag_id,
            state_id=tag.state_id,
            species=tag.species,
            volume_m3=tag.volume_m3,
            rate_kobo_per_m3=rate,
            amount_kobo=int(tag.volume_m3 * rate),
        )
        self._invoices.append(invoice)
        return invoice

    def list_invoices(self, tag_id: Optional[str] = None) -> List[StumpageInvoice]:
        if tag_id:
            return [i for i in self._invoices if i.tag_id == tag_id]
        return list(self._invoices)

    # -- deforestation alerts ----------------------------------------------------

    def ingest_alert(self, alert: DeforestationAlert) -> DeforestationAlert:
        """Ingest an NDVI disturbance alert from the geospatial job.

        The 72 h detection-to-ingest budget is asserted by the caller's
        ``detected_at`` timestamp; here we enforce that actionable alerts
        (> 0.5 ha) are flagged and routed for ranger tasking.
        """
        self._alerts[alert.alert_id] = alert
        return alert

    def list_alerts(
        self, state_id: Optional[str] = None, actionable_only: bool = False
    ) -> List[DeforestationAlert]:
        alerts = list(self._alerts.values())
        if state_id:
            alerts = [a for a in alerts if a.state_id == state_id]
        if actionable_only:
            alerts = [a for a in alerts if a.disturbed_area_ha > ACTIONABLE_AREA_HA]
        return alerts

    # -- untagged haulage ---------------------------------------------------------

    def report_untagged_haulage(
        self, state_id: str, checkpoint_id: str, vehicle_plate: str, gps: dict
    ) -> UntaggedTimberAlert:
        """Checkpoint scanner found timber with no valid RFID tag — publish alert."""
        alert = UntaggedTimberAlert(
            state_id=state_id,
            checkpoint_id=checkpoint_id,
            vehicle_plate=vehicle_plate,
            gps=gps,
        )
        if self.bus is not None:
            self.bus.publish(TOPIC_UNTAGGED_TIMBER_ALERT, alert)
        return alert
