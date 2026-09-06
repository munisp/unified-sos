"""Deterministic synthetic Sentinel-2 band fixtures for NDVI change detection.

Builds a 60×60-pixel scene (600 m × 600 m = 36 ha at 10 m pixels) with:

* a healthy vegetation baseline (NDVI ≈ 0.6) at t0;
* a **compact deep clearing** at t1 (8×8 px = 0.64 ha, NDVI drop ≈ −0.5)
  emulating an illegal-mining excavation;
* a **broad moderate felling** at t1 (25×20 px = 5 ha, drop ≈ −0.25)
  emulating rosewood logging;
* a small 2×2 px (0.04 ha) drop below the 0.5 ha M5.2 threshold that must be
  filtered out;
* a vegetation *gain* patch that must never alert.

Arrays are DN-scale uint16-like reflectances (0–10000) as in Sentinel-2 L2A.
"""

from __future__ import annotations

import numpy as np

SHAPE = (60, 60)


def _healthy_scene(seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """(B04 red, B08 nir) for dense vegetation: NDVI ≈ (8000-2000)/10000 = 0.6."""
    rng = np.random.default_rng(seed)
    red = rng.normal(2000, 40, SHAPE)
    nir = rng.normal(8000, 80, SHAPE)
    return red, nir


def _bare(red: np.ndarray, nir: np.ndarray, r0: int, c0: int, h: int, w: int, *,
          red_level: float, nir_level: float) -> None:
    """Set a rectangular block to bare-soil reflectance (deep NDVI drop)."""
    red[r0:r0 + h, c0:c0 + w] = red_level
    nir[r0:r0 + h, c0:c0 + w] = nir_level


def build_scene_pair() -> dict[str, np.ndarray]:
    """Return {b04_t0, b08_t0, b04_t1, b08_t1} for the synthetic scene."""

    red0, nir0 = _healthy_scene()
    red1, nir1 = red0.copy(), nir0.copy()

    # Illegal mining excavation: 8×8 px = 0.64 ha, NDVI ~0.6 -> ~0.1 (drop -0.5).
    _bare(red1, nir1, 5, 5, 8, 8, red_level=4500, nir_level=5000)
    # Rosewood felling: 25×20 px = 5 ha, NDVI ~0.6 -> ~0.35 (drop -0.25).
    _bare(red1, nir1, 20, 30, 25, 20, red_level=3300, nir_level=6800)
    # Sub-threshold patch: 2×2 px = 0.04 ha — must be filtered out.
    _bare(red1, nir1, 50, 5, 2, 2, red_level=4500, nir_level=5000)
    # Vegetation gain (regrowth): NDVI rises — must never alert.
    red1[45:55, 45:55] = 1200
    nir1[45:55, 45:55] = 8800

    return {"b04_t0": red0, "b08_t0": nir0, "b04_t1": red1, "b08_t1": nir1}
