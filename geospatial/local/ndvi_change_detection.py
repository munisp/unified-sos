"""Local verification runner for ``sedona/ndvi_change_detection.py``.

The production job runs distributed on Ray + Apache Sedona Raster over
Sentinel-2 10 m L2A Cloud-Optimized GeoTIFFs, with a TorchGeo classifier for
activity attribution (acceptance M5.2: canopy disturbance > 0.5 ha flagged
within 72 h; statewide pass in 8 min).

This local runner executes the *same threshold-and-polygonize logic* on small
NumPy arrays (synthetic band rasters) so CI can verify detection behaviour:

    NDVI = (B08 - B04) / (B08 + B04)
    disturbance mask: NDVI(t1) - NDVI(t0) <= -0.15
    connected components >= 0.5 ha -> CanopyDisturbanceAlert

Production notes: raster IO (COG tiling), atmospheric QA masking (SCL band),
and TorchGeo activity classification are cluster-side concerns; the heuristic
classifier below is a documented stand-in for local verification only.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import numpy as np

#: Sentinel-2 L2A pixel size (metres) for the 10 m bands used for NDVI.
SENTINEL2_PIXEL_M = 10.0

#: Default thresholds — mirror the production job's signature.
MIN_DISTURBANCE_HA = 0.5
NDVI_DROP_THRESHOLD = -0.15

_EPS = 1e-9


class SuspectedActivity(str, enum.Enum):
    ILLEGAL_MINING = "ILLEGAL_MINING"
    ROSEWOOD_LOGGING = "ROSEWOOD_LOGGING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CanopyDisturbanceAlert:
    """Same schema as the production job's alert dataclass."""

    state_id: str
    scene_id: str
    disturbance_ha: float
    centroid_lat: float
    centroid_lon: float
    suspected_activity: SuspectedActivity


def compute_ndvi(b04_red: np.ndarray, b08_nir: np.ndarray) -> np.ndarray:
    """NDVI = (B08 − B04) / (B08 + B04), guarded against zero-sum pixels."""

    red = b04_red.astype(np.float64)
    nir = b08_nir.astype(np.float64)
    return (nir - red) / (nir + red + _EPS)


def compute_ndvi_delta_local(ndvi_t0: np.ndarray, ndvi_t1: np.ndarray) -> np.ndarray:
    """NDVI difference raster (t1 − t0); negative values = vegetation loss.

    Local analogue of the production ``compute_ndvi_delta`` (Sedona RS_NDVI).
    """

    if ndvi_t0.shape != ndvi_t1.shape:
        raise ValueError("scene rasters must share shape")
    return ndvi_t1.astype(np.float64) - ndvi_t0.astype(np.float64)


def _label_components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """4-connectivity connected-component labelling (pure NumPy BFS).

    Production uses Sedona Raster's polygonize; for the small CI rasters a BFS
    over boolean pixels is exact and dependency-free.
    """

    rows, cols = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    components: list[list[tuple[int, int]]] = []
    for r in range(rows):
        for c in range(cols):
            if not mask[r, c] or visited[r, c]:
                continue
            stack = [(r, c)]
            visited[r, c] = True
            component: list[tuple[int, int]] = []
            while stack:
                pr, pc = stack.pop()
                component.append((pr, pc))
                for nr, nc in ((pr - 1, pc), (pr + 1, pc), (pr, pc - 1), (pr, pc + 1)):
                    if 0 <= nr < rows and 0 <= nc < cols and mask[nr, nc] and not visited[nr, nc]:
                        visited[nr, nc] = True
                        stack.append((nr, nc))
            components.append(component)
    return components


def classify_activity(component_pixels: list[tuple[int, int]], ndvi_delta: np.ndarray) -> SuspectedActivity:
    """LOCAL-ONLY heuristic stand-in for the production TorchGeo classifier.

    Deep, compact drops are typical of mining excavations; broad moderate
    drops of rosewood felling. Anything else is UNKNOWN pending review.
    """

    deltas = [ndvi_delta[r, c] for r, c in component_pixels]
    mean_drop = float(np.mean(deltas))
    size = len(component_pixels)
    if mean_drop <= -0.45 and size <= 400:  # ≤ 4 ha at 10 m pixels, very deep drop
        return SuspectedActivity.ILLEGAL_MINING
    if size > 400 and mean_drop <= -0.2:
        return SuspectedActivity.ROSEWOOD_LOGGING
    return SuspectedActivity.UNKNOWN


def detect_disturbance_local(
    ndvi_delta: np.ndarray,
    *,
    state_id: str,
    scene_id: str,
    origin_lat: float,
    origin_lon: float,
    pixel_size_m: float = SENTINEL2_PIXEL_M,
    min_area_ha: float = MIN_DISTURBANCE_HA,
    ndvi_drop_threshold: float = NDVI_DROP_THRESHOLD,
) -> list[CanopyDisturbanceAlert]:
    """Threshold the NDVI delta, polygonize (component-label), filter by area.

    Pixel centres are georeferenced with a simple north-up affine at the given
    scene origin (production uses the COG's geotransform via Sedona Raster).
    """

    mask = ndvi_delta <= ndvi_drop_threshold
    pixel_area_ha = (pixel_size_m**2) / 10_000.0
    # metres-per-degree approximation valid for small local scenes.
    deg_per_px_lat = pixel_size_m / 111_320.0
    deg_per_px_lon = pixel_size_m / (111_320.0 * np.cos(np.radians(origin_lat)) + _EPS)

    alerts: list[CanopyDisturbanceAlert] = []
    for component in _label_components(mask):
        area_ha = len(component) * pixel_area_ha
        if area_ha < min_area_ha:
            continue
        rows = [r for r, _ in component]
        cols = [c for _, c in component]
        centroid_r = float(np.mean(rows))
        centroid_c = float(np.mean(cols))
        alerts.append(
            CanopyDisturbanceAlert(
                state_id=state_id,
                scene_id=scene_id,
                disturbance_ha=round(area_ha, 4),
                centroid_lat=origin_lat - (centroid_r + 0.5) * deg_per_px_lat,
                centroid_lon=origin_lon + (centroid_c + 0.5) * deg_per_px_lon,
                suspected_activity=classify_activity(component, ndvi_delta),
            )
        )
    return alerts
