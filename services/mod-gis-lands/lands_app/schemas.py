"""Pydantic request/response schemas mirroring contracts/openapi/cadastre-parcels.yaml.

Keep these models aligned with the OpenAPI contract — the contract is canonical
(CONTRIBUTING.md rule 5: "Open contracts").
"""

from __future__ import annotations

import enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StateId(str, enum.Enum):
    """Tenancy enum — must match components.parameters.StateId in the contract."""

    LAGOS = "lagos"
    OGUN = "ogun"
    OSUN = "osun"
    BENUE = "benue"
    NASARAWA = "nasarawa"
    TARABA = "taraba"


class LandUseType(str, enum.Enum):
    RESIDENTIAL = "RESIDENTIAL"
    COMMERCIAL = "COMMERCIAL"
    INDUSTRIAL = "INDUSTRIAL"
    AGRI = "AGRI"


class TitleType(str, enum.Enum):
    UNREGISTERED = "UNREGISTERED"
    C_OF_O = "C_OF_O"
    GOV_CONSENT = "GOV_CONSENT"
    R_OF_O = "R_OF_O"


class ParcelStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    PENDING = "PENDING"
    REVOKED = "REVOKED"
    REGISTERED = "REGISTERED"  # derived parcel (subdivision/merger child)
    SUPERSEDED = "SUPERSEDED"  # replaced by subdivision/merger children; never deleted


class ParcelRegistration(BaseModel):
    """Contract schema: ParcelRegistration (boundary_geojson is an EPSG:4326 GeoJSON Polygon)."""

    lga_id: str
    parcel_uin: str = Field(description="Unique Identification Number")
    owner_stin: str
    land_use_type: LandUseType
    survey_plan_no: str
    beacon_count: int = Field(ge=3)
    area_sqm: float = Field(gt=0)
    boundary_geojson: dict[str, Any] = Field(
        description="GeoJSON Polygon, WGS84 (EPSG:4326)"
    )


class Parcel(BaseModel):
    """Contract schema: Parcel (response)."""

    parcel_id: UUID
    parcel_uin: str
    title_type: TitleType = TitleType.UNREGISTERED
    c_of_o_number: Optional[str] = None
    status: str = ParcelStatus.ACTIVE.value
    titling_workflow_id: str = Field(
        description="Temporal CadastralTitlingWorkflow ID (Surveyor → Town Planning → AG → Governor signature)"
    )


class ParcelRecord(BaseModel):
    """Internal persistence record — full row mirror of cadastre.parcels (0001_cadastre.sql)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    parcel_id: UUID
    tenant_state_id: str
    lga_id: str
    parcel_uin: str
    owner_stin: str
    land_use_type: LandUseType
    survey_plan_no: str
    beacon_count: int
    area_sqm: float
    title_type: TitleType = TitleType.UNREGISTERED
    c_of_o_number: Optional[str] = None
    status: ParcelStatus = ParcelStatus.ACTIVE
    boundary_geojson: dict[str, Any]
    titling_workflow_id: str
    parent_parcel_ids: list[UUID] = Field(
        default_factory=list,
        description="Lineage: parent parcel(s) this parcel was subdivided/merged from",
    )


class DeedVerificationRequest(BaseModel):
    """Contract schema: verifyDeed request body."""

    c_of_o_number: str
    parcel_uin: Optional[str] = None


class DeedVerificationResult(BaseModel):
    """Deed verification result with signature chain (contract: verifyDeed 200)."""

    valid: bool
    c_of_o_number: str
    parcel_uin: Optional[str] = None
    tenant_state_id: str
    title_type: Optional[TitleType] = None
    signature_chain: list[str] = Field(
        default_factory=list,
        description="Ordered JWS (Ed25519/EdDSA) tokens: registry issuance signature, governor consent countersignature",
    )
    detail: str = ""
