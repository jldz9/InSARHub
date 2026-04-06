# -*- coding: utf-8 -*-
"""
Vegetation feature extractor.

Source priority
---------------
1. MODIS MOD13Q1 16-day NDVI — ORNL DAAC REST API (batch via _ndvi.py).
2. Monthly climatology table — hardcoded in _ndvi.py, always available.

Features returned
-----------------
  ndvi_d1               : float  — NDVI on date1 acquisition date
  ndvi_d2               : float  — NDVI on date2 acquisition date
  delta_ndvi            : float  — ndvi_d2 - ndvi_d1 (signed)
  ndvi_max              : float  — max(ndvi_d1, ndvi_d2)
  growing_season        : int    — 1 if ndvi_max > 0.3, else 0
  veg_temporal          : float  — ndvi_max * dt_normalized
  ndvi_source           : str    — "modis" | "climatology"
"""

from __future__ import annotations

from insarhub.utils.pair_quality._ndvi import get_ndvi

_GROWING_THRESHOLD = 0.3


def get_veg_features(
    lat: float,
    lon: float,
    date1: str,
    date2: str,
    dt_normalized: float,
    ndvi_cache: dict,
) -> dict:
    ndvi1, src1 = get_ndvi(lat, lon, date1, ndvi_cache)
    ndvi2, src2 = get_ndvi(lat, lon, date2, ndvi_cache)

    source = src1 if src1 == src2 else ("modis" if "modis" in (src1, src2) else "climatology")
    ndvi_max = max(ndvi1, ndvi2)

    return {
        "ndvi_d1":        round(ndvi1, 4),
        "ndvi_d2":        round(ndvi2, 4),
        "delta_ndvi":     round(ndvi2 - ndvi1, 4),
        "ndvi_max":       round(ndvi_max, 4),
        "growing_season": int(ndvi_max > _GROWING_THRESHOLD),
        "veg_temporal":   round(ndvi_max * dt_normalized, 4),
        "ndvi_source":    source,
    }
