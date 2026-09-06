"""Domain service: site registry + consignment lifecycle orchestration."""
from __future__ import annotations

from .bus import EventBus
from .levy import DEFAULT_POLICY, LevyPolicy, assess_levy
from .models import (
    AssayRecord,
    Consignment,
    ConsignmentDispatchedEvent,
    ConsignmentStatus,
    MineralSite,
    MineralType,
    WeighbridgeReading,
    utcnow,
)
from .repo import MiningRepository

TOPIC_CONSIGNMENT_DISPATCHED = "ng.sos.mining.consignment_dispatched"


class NotFoundError(KeyError):
    pass


class InvalidStateTransition(ValueError):
    pass


class MiningService:
    def __init__(
        self,
        repo: MiningRepository,
        bus: EventBus,
        policy: LevyPolicy = DEFAULT_POLICY,
    ) -> None:
        self.repo = repo
        self.bus = bus
        self.policy = policy

    # -- site registry -----------------------------------------------------

    def register_site(self, site: MineralSite) -> MineralSite:
        return self.repo.save_site(site)

    # -- consignment lifecycle ----------------------------------------------

    def create_consignment(self, consignment: Consignment) -> Consignment:
        site = self.repo.get_site(consignment.site_id)
        if site is None or not site.active:
            raise NotFoundError(f"mineral site {consignment.site_id!r} unknown/inactive")
        if consignment.mineral_type not in site.minerals:
            raise ValueError(
                f"site {site.site_id} is not licensed for {consignment.mineral_type.value}"
            )
        if consignment.status != ConsignmentStatus.CREATED:
            raise InvalidStateTransition("new consignments must start as CREATED")
        return self.repo.save_consignment(consignment)

    def record_weighbridge(
        self, consignment_id: str, reading: WeighbridgeReading
    ) -> Consignment:
        con = self._get(consignment_id)
        self._require(con, ConsignmentStatus.CREATED)
        reading.validate_weights()
        con.weighbridge = reading
        con.status = ConsignmentStatus.WEIGHED
        return self.repo.save_consignment(con)

    def attach_assay(self, consignment_id: str, assay: AssayRecord) -> Consignment:
        con = self._get(consignment_id)
        self._require(con, ConsignmentStatus.WEIGHED)
        if (
            con.mineral_type == MineralType.LITHIUM_SPODUMENE
            and assay.lithium_oxide_grade_pct is None
        ):
            raise ValueError("lithium consignments require Li2O grade before dispatch")
        con.assay = assay
        return self.repo.save_consignment(con)

    def dispatch(self, consignment_id: str) -> Consignment:
        """Assess the state levy, mark DISPATCHED, publish the AsyncAPI event."""
        con = self._get(consignment_id)
        self._require(con, ConsignmentStatus.WEIGHED)
        con.levy = assess_levy(con, self.policy)
        con.status = ConsignmentStatus.DISPATCHED
        con.dispatched_at = utcnow()
        self.repo.save_consignment(con)

        assert con.weighbridge is not None and con.levy is not None
        event = ConsignmentDispatchedEvent(
            state_id=con.state_id,
            consignment_id=con.consignment_id,
            mine_lease_id=con.mine_lease_id,
            mineral_type=con.mineral_type,
            gross_weight_kg=con.weighbridge.gross_weight_kg,
            tare_weight_kg=con.weighbridge.tare_weight_kg,
            net_weight_kg=con.weighbridge.net_weight_kg,
            lithium_oxide_grade_pct=(
                con.assay.lithium_oxide_grade_pct if con.assay else None
            ),
            truck_registration=con.truck_registration,
            rfid_seal_id=con.rfid_seal_id,
            royalty_due_kobo=con.levy.total_kobo,
            destination_corridor=con.destination_corridor,
        )
        self.bus.publish(TOPIC_CONSIGNMENT_DISPATCHED, event)
        return con

    def record_delivery(self, consignment_id: str) -> Consignment:
        con = self._get(consignment_id)
        self._require(con, ConsignmentStatus.DISPATCHED)
        con.status = ConsignmentStatus.DELIVERED
        con.delivered_at = utcnow()
        return self.repo.save_consignment(con)

    # -- helpers -------------------------------------------------------------

    def _get(self, consignment_id: str) -> Consignment:
        con = self.repo.get_consignment(consignment_id)
        if con is None:
            raise NotFoundError(f"consignment {consignment_id!r} not found")
        return con

    @staticmethod
    def _require(con: Consignment, status: ConsignmentStatus) -> None:
        if con.status != status:
            raise InvalidStateTransition(
                f"consignment {con.consignment_id} is {con.status.value}, "
                f"expected {status.value}"
            )
