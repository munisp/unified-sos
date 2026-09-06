"""SOS Geospatial Job: Sentinel-2 NDVI Vegetation Change Detection.

WP-07 / EPIC-08 · mod-forestry · Acceptance M5.2: canopy disturbance > 0.5 ha
detected within 72 hours of satellite data ingest.

Detects illegal mining excavations (Nasarawa/Osun/Taraba) and rosewood
deforestation (Taraba) from Sentinel-2 10m L2A Cloud-Optimized GeoTIFFs,
running distributed on Ray + Apache Sedona Raster (TorchGeo models).
Statewide pass target: 8 minutes.
"""

from dataclasses import dataclass


@dataclass
class CanopyDisturbanceAlert:
    state_id: str
    scene_id: str
    disturbance_ha: float
    centroid_lat: float
    centroid_lon: float
    suspected_activity: str  # ILLEGAL_MINING | ROSEWOOD_LOGGING | UNKNOWN


def compute_ndvi_delta(scene_t0: str, scene_t1: str) -> "object":
    """Compute NDVI difference raster between two Sentinel-2 acquisitions.

    Runs as a Sedona Raster / Ray distributed task over COG tiles.
    NDVI = (B08 - B04) / (B08 + B04)
    """
    raise NotImplementedError("wire to Sedona RS_NDVI + Ray data pipeline")


def detect_disturbance(
    ndvi_delta: "object", min_area_ha: float = 0.5, ndvi_drop_threshold: float = -0.15
) -> list[CanopyDisturbanceAlert]:
    """Polygonize significant NDVI drops, filter >= min_area_ha, classify activity.

    Alerts are published to the SOC/enforcement dispatch and stored to the
    Silver->Gold lakehouse for MDA dashboards.
    """
    raise NotImplementedError("wire to TorchGeo classifier + PostGIS overlay vs gazetted forest reserves")
