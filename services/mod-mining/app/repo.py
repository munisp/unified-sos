"""Repository interface + in-memory implementation.

Production target is PostGIS-backed Postgres (per the module README); the
interface stays minimal so a SQL repository can drop in without touching the
service layer.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Protocol

from .models import Consignment, MineralSite


class MiningRepository(Protocol):
    def save_site(self, site: MineralSite) -> MineralSite: ...

    def get_site(self, site_id: str) -> Optional[MineralSite]: ...

    def save_consignment(self, consignment: Consignment) -> Consignment: ...

    def get_consignment(self, consignment_id: str) -> Optional[Consignment]: ...

    def list_consignments(self, state_id: Optional[str] = None) -> List[Consignment]: ...


class InMemoryMiningRepository:
    def __init__(self) -> None:
        self._sites: Dict[str, MineralSite] = {}
        self._consignments: Dict[str, Consignment] = {}

    def save_site(self, site: MineralSite) -> MineralSite:
        self._sites[site.site_id] = site
        return site

    def get_site(self, site_id: str) -> Optional[MineralSite]:
        return self._sites.get(site_id)

    def save_consignment(self, consignment: Consignment) -> Consignment:
        self._consignments[consignment.consignment_id] = consignment
        return consignment

    def get_consignment(self, consignment_id: str) -> Optional[Consignment]:
        return self._consignments.get(consignment_id)

    def list_consignments(self, state_id: Optional[str] = None) -> List[Consignment]:
        items = list(self._consignments.values())
        if state_id is not None:
            items = [c for c in items if c.state_id == state_id]
        return items
