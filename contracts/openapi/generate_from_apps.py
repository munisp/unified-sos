#!/usr/bin/env python3
"""Generate curated OpenAPI 3.1 contracts from the implemented FastAPI apps.

Contract-as-code: each YAML emitted under contracts/openapi/ is built from the
service's own FastAPI instance (``app.openapi()``) so the contract can never
drift from the implementation, then curated with program metadata (WP/EPIC
description, per-state sovereign server, bearerAuth scheme, tags).

Usage:  python3 contracts/openapi/generate_from_apps.py
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = REPO_ROOT / "services"
OUT_DIR = REPO_ROOT / "contracts" / "openapi"

HEADER = (
    "# GENERATED + CURATED from the implementation (contract-as-code).\n"
    "# Regenerate with: python3 contracts/openapi/generate_from_apps.py\n"
    "# Source: services/{service}/ (FastAPI app factory).\n"
)

STATES = ["lagos", "ogun", "osun", "benue", "nasarawa", "taraba"]

# service -> (package, factory call, curated info metadata)
SERVICES: dict[str, dict] = {
    "mod-gis-luc": {
        "package": "luc_app.main",
        "info": dict(
            title="SOS Land Use Charge Valuation API (mod-gis-luc)",
            description=(
                "WP-06 / EPIC-06 · Lot 4 · RT-03. Automated Land Use Charge "
                "calculation: Sedona satellite building footprints matched to "
                "cadastral parcels, AI property valuation (Ray AVM, >92% R² vs "
                "certified surveyor valuations), automated LUC bill issuance. "
                "Deploys: Ogun, Benue, Lagos, Nasarawa."
            ),
            tags=["luc", "gis", "valuation"],
        ),
    },
    "mod-mining": {
        "package": "app.main",
        "info": dict(
            title="SOS Solid Minerals Custody & Levies API (mod-mining)",
            description=(
                "WP-07 / EPIC-07 · Lot 5 · RT-03. Digital mineral e-permit "
                "registry, weight-based levy calculation, IoT weighbridge "
                "telemetry, RFID truck manifests, quarry checkpoint scanners, "
                "artisanal miner registry. Hard constraint: state-competent "
                "levies only — federal royalty lines separated by construction. "
                "Deploys: Nasarawa, Osun, Taraba, Ogun."
            ),
            tags=["mining", "levies", "consignments"],
        ),
    },
    "mod-forestry": {
        "package": "app.main",
        "info": dict(
            title="SOS Timber Provenance & Deforestation Alerts API (mod-forestry)",
            description=(
                "WP-07 / EPIC-08 · Lot 5 · RT-03. UHF RFID nail-tag log "
                "provenance, ranger GPS scanners, timber transit permits, and "
                "Sentinel-2 NDVI canopy change detection for illegal logging "
                "enforcement. Deploys: Taraba, Ogun, Osun, Benue."
            ),
            tags=["forestry", "provenance", "permits"],
        ),
    },
    "mod-market": {
        "package": "app.main",
        "info": dict(
            title="SOS Commercial Markets & Digital Stall Titling API (mod-market)",
            description=(
                "WP-12 / EPIC-14 · Lot 7 · RT-04. Digital market stall cadastre, "
                "concession leases, automated micro-tenancy billing, trader "
                "daily stallage micro-collection via USSD/POS with TigerBeetle "
                "escrow. Target: >95% collection rate. Deploys: all 6 states."
            ),
            tags=["markets", "stalls", "stallage"],
        ),
    },
    "mod-agri-waybill": {
        "package": "app.main",
        "info": dict(
            title="SOS Agribusiness Supply Chain & E-Waybill API (mod-agri-waybill)",
            description=(
                "WP-09 / EPIC-11 · Lot 6/9 · RT-04. Digital produce inspection "
                "and e-waybills, haulage checkpoint verification, agro-hub "
                "warehouse receipt tokenization, commodity collateral clearing, "
                "EUDR-compliant deforestation-free export provenance. Deploys: "
                "Benue, Taraba, Nasarawa, Osun."
            ),
            tags=["agriculture", "waybills", "warehouse-receipts"],
        ),
    },
    "mod-transport-wim": {
        "package": "app.main",
        "info": dict(
            title="SOS Weigh-in-Motion & Corridor Haulage API (mod-transport-wim)",
            description=(
                "WP-08 / EPIC-09 · Lot 6 · RT-04. High-speed WIM sensor "
                "ingestion, ANPR OCR pipelines, automated overload fine "
                "issuance, corridor tolls (fine issued <3s at >80 km/h; WIM "
                "accuracy ±3%). Deploys: Ogun, Lagos, Nasarawa, Benue."
            ),
            tags=["transport", "wim", "fines"],
        ),
    },
    "mod-health": {
        "package": "app.main",
        "info": dict(
            title="SOS Public Health Billing & Facility Operations API (mod-health)",
            description=(
                "WP-10 / EPIC-12 · Lot 7 · RT-04. Consolidated revenue across "
                "state tertiary and specialist hospitals, FHIR-compliant patient "
                "billing, drug inventory POS (revolving drug fund), automated "
                "SHIA/NHIS claims adjudication (<24h). Deploys: Nasarawa, Osun, "
                "Benue — extensible to all 6."
            ),
            tags=["health", "billing", "insurance-claims"],
        ),
    },
    "mod-education": {
        "package": "app.main",
        "info": dict(
            title="SOS Tertiary Consolidated Billing & Bursary API (mod-education)",
            description=(
                "WP-11 / EPIC-13 · Lot 7 · RT-04. Unifies tuition, departmental "
                "levies, and accommodation fees across state universities and "
                "polytechnics with automated disbursement, course-registration "
                "payment lock, and treasury↔CRF reconciliation. Deploys: Osun "
                "(lead), all 6."
            ),
            tags=["education", "bursary", "billing"],
        ),
    },
    "mod-police-cad": {
        "package": "app.main",
        "info": dict(
            title="SOS Public Safety & Emergency Dispatch CAD API (mod-police-cad)",
            description=(
                "WP-13 / EPIC-15 · Lot 8 · RT-04/05. Multi-agency incident "
                "reporting, computer-aided 112 dispatch, CCTV/drone stream "
                "metadata routing, patrol geofencing, publicly auditable "
                "trust-fund ledger. State-police modules gated by constitutional "
                "amendment (24-of-36 assemblies); community vigilante CAD "
                "operational immediately. Deploys: all 6."
            ),
            tags=["public-safety", "cad", "dispatch"],
        ),
    },
    "mod-safecity-vision": {
        "package": "app.main",
        "info": dict(
            title="SOS Safe-City AI/CV Camera Analytics API (mod-safecity-vision)",
            description=(
                "WP-13 / EPIC-15 companion · Safe-City camera-estate intelligence: "
                "face recognition vs watchlists (NDPA 2023 authorization-gated, "
                "HTTP 423 until certified warrant/DPO record), crowd density & "
                "stampede-risk alerting, anomaly detection (loitering, perimeter "
                "breach, object-left-behind, running), WebRTC/RTSP stream registry. "
                "Fail-closed CV engine seam (InsightFace) with deterministic "
                "fixtures. Deploys: Lagos L1 first; follows mod-police-cad rollout."
            ),
            tags=["public-safety", "cv", "analytics"],
        ),
    },
    "mod-agri-trace": {
        "package": "app.main",
        "info": dict(
            title="SOS Agri Commodity Traceability & Warehouse Receipts API (mod-agri-trace)",
            description=(
                "National Edition §3. Commodity aggregation, agro-hub warehouse "
                "receipts (issued/pledged/redeemed, hash-chained), crop "
                "traceability provenance chains. Adoptions: Benue (yam), Osun "
                "(cocoa), Taraba (tea), Kebbi (rice), Kano (grains)."
            ),
            tags=["agri", "traceability", "warehouse-receipts"],
        ),
    },
    "mod-border-transit": {
        "package": "app.main",
        "info": dict(
            title="SOS Cross-Border Cargo Transit API (mod-border-transit)",
            description=(
                "National Edition §3. Cross-border cargo RFID tracking, transit "
                "telematics with corridor geofencing, tamper alerts, transit "
                "levy assessment (integer kobo). Adoptions: Taraba, Borno, "
                "Katsina, Sokoto, Ogun, Cross River."
            ),
            tags=["border", "transit", "rfid"],
        ),
    },
    "mod-waterways": {
        "package": "app.main",
        "info": dict(
            title="SOS Inland Waterways API (mod-waterways)",
            description=(
                "National Edition §3. Ferry e-ticketing with capacity-enforced "
                "manifests, sand-dredging volumetric monitoring (Sedona seam), "
                "quota & royalty levies. Adoptions: Lagos, Bayelsa, Rivers, "
                "Benue, Delta, Kogi, Niger (LASWA/NIWA liaison)."
            ),
            tags=["waterways", "ferry", "dredging"],
        ),
    },
    "mod-mobility-switch": {
        "package": "app.main",
        "info": dict(
            title="SOS Multimodal Transit Clearing API (mod-mobility-switch)",
            description=(
                "WP-08 / EPIC-10 · Lot 6 · RT-04. Contactless multimodal "
                "transit switch (Cowry Gen 2 compatible): bus, rail, ferry "
                "clearing; commercial vehicle daily ticketing, park management, "
                "driver manifests, gazetted 3–8% union commission auto-splits. "
                "Deploys: Lagos, Ogun."
            ),
            tags=["mobility", "transit", "clearing"],
        ),
    },
    "mod-environment": {
        "package": "app.main",
        "info": dict(
            title="SOS Environmental Protection, Carbon Registry & Industrial Emissions API (mod-environment)",
            description=(
                "v3.0 / ENV-09 · Lot 5. Industrial IoT telemetry compliance "
                "(COMPLIANT/WARNING/VIOLATION evaluation against per-state "
                "limits; violation fines with state multipliers), effluent "
                "discharge & timber permit lifecycles, deforestation satellite "
                "surveillance with a <4h dispatch SLA (SEC-10 enforcement "
                "tickets), state carbon registry (issue/transfer/retire with "
                "unique serials; brokerage fee default 3% [DERIVED], "
                "state-overridable), and EIA workflow. Deploys: Lagos, Ogun, "
                "Taraba (+ Osun, Benue, Nasarawa via shared config)."
            ),
            tags=["environment", "carbon-registry", "compliance"],
        ),
    },
    "mod-citizen-portal": {
        "package": "app.main",
        "info": dict(
            title="SOS Unified Citizen Portal, Sovereign Identity SSO & Civil Service Clean-Up API (mod-citizen-portal)",
            description=(
                "v3.0 / CIT-11 · Lot 7. Citizen identity wallet over Keycloak "
                "OIDC multi-realm SSO (raw NIN reduced to a SHA-256 hash at "
                "creation; NIMC API seam), multi-MDA self-service catalog and "
                "requests (STANDARD/EXPEDITED priority; 70% state / 15% MDA / "
                "15% platform settlement default [DERIVED], state-overridable), "
                "e-petitions with public reference IDs, and civil-service "
                "biometric payroll audit with deterministic ghost-worker "
                "detection (₦500m+ recoverable target, Temporal workflow seam). "
                "Complements, not replaces, mod-identity. Deploys: all 6 states."
            ),
            tags=["citizen-portal", "sso", "payroll-audit"],
        ),
    },
    "mod-kyc-kyb": {
        "package": "app.main",
        "info": dict(
            title="SOS KYC/KYB, Document AI & Liveness Verification API (mod-kyc-kyb)",
            description=(
                "v3.0 / KYC-KYB · Lot 1/7. Sovereign, tenant-isolated identity "
                "verification for all SOS stakeholders (citizens, civil "
                "servants, MDA officers, field/POS agents, market traders, "
                "miners, transporters, health/education users, vendors, "
                "concessionaires, auditors, corporate entities): document AI "
                "extraction (PaddleOCR + Docling + VLM adjudication), "
                "active/passive liveness challenges, CAC/NIMC/tax/sanctions "
                "registry verification seams, deterministic risk scoring, and "
                "hash-chained audit. NDPA data-minimization: raw NIN, CAC "
                "documents, biometrics, images, and OCR text are never "
                "returned — object URI + SHA-256 + minimum fields + "
                "confidence only. Complements mod-identity and "
                "mod-citizen-portal. Deploys: all 6 states."
            ),
            tags=["kyc-kyb", "identity-verification", "document-ai"],
        ),
    },
    "mod-transparency": {
        "package": "app.main",
        "public": True,  # unauthenticated read-only transparency views
        "info": dict(
            title="SOS Public Transparency Views API (mod-transparency)",
            description=(
                "P2 Workstream E. Unauthenticated, tenant-scoped, read-only "
                "public projections: security trust-fund feed (donations in, "
                "disbursements out, kobo balances, per-entry hash-chain "
                "cursors; mod-police-cad), concession escrow settlement "
                "statements, and the hash-chained procurement audit log with "
                "public recomputed verification (mod-ppp-investment). "
                "Structural redaction: no donor, actor or concessionaire PII "
                "— identities are salted SHA-256 pseudonyms. Unknown tenant "
                "states return a generic 404 (no enumeration). Deploys: all 6."
            ),
            tags=["transparency", "trust-fund", "procurement-audit"],
        ),
    },
    "mod-erp-bridge": {
        "package": "app.main",
        "info": dict(
            title="SOS ERP Integration Bridge API (mod-erp-bridge)",
            description=(
                "v3.0 / ERP-BRIDGE. Tenant-isolated bridge to state ERP/PFM "
                "systems: balanced double-entry journal ingestion (integer "
                "kobo; API + ng.sos.payments.settlement_completed event "
                "subscription), per-state chart-of-accounts mapping, "
                "fail-closed ERP backends (Odoo XML-RPC, ERPNext REST, "
                "GIFMIS/IFMIS CSV+JSON export, deterministic fixture), "
                "source-event dedupe, retry with backoff and dead-letter, and "
                "a hash-chained outbound journal log with public "
                "re-verification. Unknown tenant states return 404. Deploys: "
                "all 6 states."
            ),
            tags=["erp-bridge", "journals", "coa-mapping"],
        ),
    },
    "mod-geospatial": {
        "package": "app.main",
        "info": dict(
            title="SOS Geospatial Orchestration, GeoLibre Projects & Lakehouse API (mod-geospatial)",
            description=(
                "Stage 5 / GEO-LND · Lots 4-6. Tenant-isolated geospatial "
                "dataset registration (PostGIS system of record), H3 indexing, "
                "deterministic processing jobs, GeoLibre self-hosted workbench "
                "project authoring (.geolibre.json artifacts; never the system "
                "of record; no raw PII or hosted share/collab endpoints), and "
                "lakehouse GeoParquet publication seams (Sedona analytical "
                "engine). Rust geometry-rs provides deterministic WKT "
                "validation; mod-geospatial-gateway (Go) provides the "
                "low-latency validation/command edge. Deploys: all 6 states."
            ),
            tags=["geospatial", "geolibre", "lakehouse"],
        ),
    },
}


def build_openapi(service: str, cfg: dict) -> dict:
    """Import the service's FastAPI app and dump a curated OpenAPI 3.1 spec."""
    svc_dir = str(SERVICES_DIR / service)
    sys.path.insert(0, svc_dir)
    # evict any previously imported same-named package ('app', 'luc_app')
    pkg_root = cfg["package"].split(".")[0]
    for mod in [m for m in list(sys.modules) if m == pkg_root or m.startswith(pkg_root + ".")]:
        del sys.modules[mod]
    try:
        module = importlib.import_module(cfg["package"])
        app = module.create_app()
        spec = app.openapi()
    finally:
        sys.path.remove(svc_dir)

    meta = cfg["info"]
    spec["openapi"] = "3.1.0"
    spec["info"] = {
        "title": meta["title"],
        "version": spec.get("info", {}).get("version", "1.0.0"),
        "description": meta["description"],
    }
    spec["servers"] = [
        {
            "url": "https://api.{state}.gov.ng/sos",
            "description": "Per-state sovereign data plane (state = " + "|".join(STATES) + ")",
        }
    ]
    spec.setdefault("components", {}).setdefault("securitySchemes", {})["bearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    spec["tags"] = [{"name": t} for t in meta["tags"]]

    # Curation guarantees: every operation has operationId, tags, and security.
    for path, item in spec.get("paths", {}).items():
        for method, op in item.items():
            if method not in ("get", "post", "put", "patch", "delete", "head", "options"):
                continue
            if "operationId" not in op:
                raise ValueError(f"{service}: {method.upper()} {path} missing operationId")
            op.setdefault("tags", meta["tags"][:1])
            if path.endswith("/health") or path.rstrip("/").endswith("/healthz"):
                continue  # liveness probes are unauthenticated
            if cfg.get("public"):
                op.setdefault("security", [])  # public unauthenticated surface
                continue
            op.setdefault("security", [{"bearerAuth": []}])
    return spec


def validate(spec: dict, service: str) -> None:
    assert spec["openapi"] == "3.1.0", service
    assert spec["info"]["title"] and spec["paths"], service
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method in ("get", "post", "put", "patch", "delete"):
                assert "operationId" in op, f"{service} {method} {path}"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for service, cfg in SERVICES.items():
        spec = build_openapi(service, cfg)
        validate(spec, service)
        body = yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=120)
        out = OUT_DIR / f"{service}.yaml"
        out.write_text(HEADER.format(service=service) + body)
        n_ops = sum(
            1 for item in spec["paths"].values() for m in item
            if m in ("get", "post", "put", "patch", "delete")
        )
        print(f"[ok] {service}: {len(spec['paths'])} paths, {n_ops} operations -> {out.name}")


if __name__ == "__main__":
    main()
