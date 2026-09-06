"""Domain service: WIM ingestion, overload detection, fines, ANPR, manifests."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .models import (
    ANPREvent,
    AxleViolation,
    CorridorConfig,
    EManifest,
    FineAssessment,
    ManifestVerification,
    OverloadVerdict,
    WeighbridgeEvent,
    WIMReading,
    utcnow,
)

TOPIC_WEIGHBRIDGE_READING = "ng.sos.mining.weighbridge_reading"  # shared envelope

#: ANPR ↔ WIM correlation window: a plate sighting this close (before/after)
#: to a WIM pass on the same corridor is considered the same vehicle.
ANPR_CORRELATION_WINDOW = timedelta(seconds=30)


class NotFoundError(KeyError):
    pass


class WIMService:
    def __init__(self, bus=None) -> None:
        self._corridors: Dict[str, CorridorConfig] = {}
        self._readings: Dict[str, WIMReading] = {}
        self._verdicts: Dict[str, OverloadVerdict] = {}
        self._fines: Dict[str, FineAssessment] = {}
        self._anpr: List[ANPREvent] = []
        self._manifests: Dict[str, EManifest] = {}
        self.bus = bus

    # -- corridor configuration -------------------------------------------------

    def configure_corridor(self, config: CorridorConfig) -> CorridorConfig:
        self._corridors[config.corridor_id] = config
        return config

    def get_corridor(self, corridor_id: str) -> CorridorConfig:
        cfg = self._corridors.get(corridor_id)
        if cfg is None:
            raise NotFoundError(f"corridor {corridor_id!r} not configured")
        return cfg

    # -- WIM ingestion & overload detection --------------------------------------

    def ingest_reading(self, reading: WIMReading) -> OverloadVerdict:
        """Assess a WIM reading against gazetted corridor limits.

        Axle pairs are evaluated against the tandem limit when consecutive
        axles are each above the single-axle limit (standard tandem-group
        heuristic); otherwise each axle is compared to the single-axle limit.
        GVW is always checked against the corridor GVW limit. Tolerance_pct is
        applied to all limits (instrument accuracy allowance).
        """
        cfg = self.get_corridor(reading.corridor_id)
        factor = 1 + cfg.tolerance_pct / 100
        single_limit = cfg.single_axle_limit_kg * factor
        tandem_limit = cfg.tandem_axle_limit_kg * factor
        gvw_limit = cfg.gvw_limit_kg * factor

        violations: List[AxleViolation] = []
        axles = reading.axle_weights_kg
        i = 0
        while i < len(axles):
            if (
                i + 1 < len(axles)
                and axles[i] > single_limit
                and axles[i + 1] > single_limit
                and axles[i] + axles[i + 1] > tandem_limit
            ):
                over = axles[i] + axles[i + 1] - tandem_limit
                violations.append(
                    AxleViolation(
                        axle_index=i,
                        weight_kg=axles[i] + axles[i + 1],
                        limit_kg=tandem_limit,
                        overload_kg=over,
                    )
                )
                i += 2
                continue
            if axles[i] > single_limit:
                violations.append(
                    AxleViolation(
                        axle_index=i,
                        weight_kg=axles[i],
                        limit_kg=single_limit,
                        overload_kg=axles[i] - single_limit,
                    )
                )
            i += 1

        gvw_over = max(0.0, reading.gvw_kg - gvw_limit)
        verdict = OverloadVerdict(
            reading_id=reading.reading_id,
            corridor_id=reading.corridor_id,
            gvw_kg=reading.gvw_kg,
            overload_detected=bool(violations) or gvw_over > 0,
            axle_violations=violations,
            gvw_overload_kg=gvw_over,
        )

        # ANPR correlation: attach plate identity if not already on the reading.
        if reading.vehicle_plate is None:
            reading.vehicle_plate = self._correlate_plate(reading)

        self._readings[reading.reading_id] = reading
        self._verdicts[reading.reading_id] = verdict
        self._publish_reading(cfg, reading, verdict)
        if verdict.overload_detected:
            self._assess_fine(cfg, reading, verdict)
        return verdict

    def _correlate_plate(self, reading: WIMReading) -> Optional[str]:
        best: Optional[ANPREvent] = None
        for ev in self._anpr:
            if ev.corridor_id != reading.corridor_id:
                continue
            delta = abs(ev.captured_at - reading.recorded_at)
            if delta <= ANPR_CORRELATION_WINDOW and (
                best is None or delta < abs(best.captured_at - reading.recorded_at)
            ):
                best = ev
        return best.vehicle_plate if best else None

    def _assess_fine(
        self, cfg: CorridorConfig, reading: WIMReading, verdict: OverloadVerdict
    ) -> FineAssessment:
        total_over = sum(v.overload_kg for v in verdict.axle_violations) + verdict.gvw_overload_kg
        amount = cfg.fine_base_kobo + int(total_over * cfg.fine_per_overload_kg_kobo)
        fine = FineAssessment(
            fine_id=f"FINE-{uuid.uuid4().hex[:12]}",
            reading_id=reading.reading_id,
            corridor_id=cfg.corridor_id,
            state_id=cfg.state_id,
            vehicle_plate=reading.vehicle_plate,
            total_overload_kg=total_over,
            amount_kobo=amount,
        )
        self._fines[fine.fine_id] = fine
        return fine

    def _publish_reading(
        self, cfg: CorridorConfig, reading: WIMReading, verdict: OverloadVerdict
    ) -> None:
        if self.bus is None:
            return
        self.bus.publish(
            TOPIC_WEIGHBRIDGE_READING,
            WeighbridgeEvent(
                state_id=cfg.state_id,
                station_id=reading.station_id,
                axle_weights_kg=reading.axle_weights_kg,
                gross_weight_kg=reading.gvw_kg,
                anpr_plate=reading.vehicle_plate,
                overload_detected=verdict.overload_detected,
            ),
        )

    # -- queries -------------------------------------------------------------------

    def get_verdict(self, reading_id: str) -> OverloadVerdict:
        verdict = self._verdicts.get(reading_id)
        if verdict is None:
            raise NotFoundError(f"reading {reading_id!r} not found")
        return verdict

    def list_fines(self, vehicle_plate: Optional[str] = None) -> List[FineAssessment]:
        fines = list(self._fines.values())
        return [f for f in fines if f.vehicle_plate == vehicle_plate] if vehicle_plate else fines

    def ingest_anpr(self, event: ANPREvent) -> ANPREvent:
        self.get_corridor(event.corridor_id)
        self._anpr.append(event)
        return event

    # -- e-manifest verification -----------------------------------------------------

    def register_manifest(self, manifest: EManifest) -> EManifest:
        self._manifests[manifest.manifest_id] = manifest
        return manifest

    def verify_manifest(self, manifest_id: str) -> ManifestVerification:
        """Checkpoint e-manifest verification (target < 5 s; in-process ≈ ms)."""
        manifest = self._manifests.get(manifest_id)
        if manifest is None:
            return ManifestVerification(
                manifest_id=manifest_id, found=False, valid=False, detail="unknown manifest"
            )
        return ManifestVerification(
            manifest_id=manifest_id,
            found=True,
            valid=manifest.valid,
            vehicle_plate=manifest.vehicle_plate,
            detail=None if manifest.valid else "manifest revoked",
        )
