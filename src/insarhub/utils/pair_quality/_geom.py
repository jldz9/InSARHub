# -*- coding: utf-8 -*-
"""
Terrain geometry feature extractor.

Uses dem_stitcher (already a project dependency) to fetch a Copernicus
GLO-30 DEM tile for the AOI bounding box, then derives terrain statistics
in-memory — no raster is written to disk.

Features returned (AOI-level, same for every pair in the folder)
----------------------------------------------------------------
  elevation_mean    : float | None  — mean elevation in m
  elevation_range   : float | None  — max − min elevation in m
  slope_mean        : float | None  — mean slope in degrees
  slope_p90         : float | None  — 90th-percentile slope in degrees
  roughness         : float | None  — std-dev of local relief (3×3 kernel) in m

All values are None when the DEM fetch fails; callers substitute neutral
values so the classifier can still run.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


def _wkt_bbox(wkt: str) -> tuple[float, float, float, float]:
    """Return (west, south, east, north) from a WKT POLYGON string."""
    coords = re.findall(r'(-?\d+\.?\d*)\s+(-?\d+\.?\d*)', wkt)
    if not coords:
        raise ValueError(f"No coordinates in WKT: {wkt!r}")
    lons = [float(c[0]) for c in coords]
    lats = [float(c[1]) for c in coords]
    return min(lons), min(lats), max(lons), max(lats)


def _slope_degrees(dem: np.ndarray, res_m: float = 30.0) -> np.ndarray:
    """Compute slope magnitude in degrees from a 2-D DEM array."""
    dy, dx = np.gradient(dem.astype(float), res_m)
    return np.degrees(np.arctan(np.sqrt(dx**2 + dy**2)))


def _roughness(dem: np.ndarray) -> np.ndarray:
    """Local relief (std-dev in a 3×3 neighbourhood) as a roughness proxy."""
    from numpy.lib.stride_tricks import sliding_window_view
    if dem.shape[0] < 3 or dem.shape[1] < 3:
        return np.array([dem.std()])
    windows = sliding_window_view(dem.astype(float), (3, 3))
    return windows.std(axis=(-2, -1))


def extract(aoi_wkt: str) -> dict:
    """Return terrain feature dict for the given AOI WKT.

    The GLO-30 DEM is fetched in-memory and not cached by this function —
    caching is handled by the CacheManager in pair_quality.py.
    """
    result: dict = {
        "elevation_mean":  None,
        "elevation_range": None,
        "slope_mean":      None,
        "slope_p90":       None,
        "roughness":       None,
    }

    try:
        import dem_stitcher

        west, south, east, north = _wkt_bbox(aoi_wkt)
        # Add a small buffer so edge pixels have valid neighbours for gradient
        buf = 0.05
        bbox = [west - buf, south - buf, east + buf, north + buf]

        dem_arr, _ = dem_stitcher.stitch_dem(
            bbox,
            dem_name="glo_30",
            dst_area_or_point="Point",
            dst_ellipsoidal_height=True,
        )

        # dem_stitcher returns shape (bands, rows, cols); squeeze to 2-D
        if dem_arr.ndim == 3:
            dem_arr = dem_arr[0]

        # Mask nodata (common fill values: -9999, -32768)
        dem_f = dem_arr.astype(float)
        dem_f[dem_f < -1000] = np.nan

        valid = dem_f[~np.isnan(dem_f)]
        if valid.size == 0:
            return result

        slope = _slope_degrees(np.nan_to_num(dem_f, nan=float(np.nanmean(dem_f))))
        rough = _roughness(np.nan_to_num(dem_f, nan=float(np.nanmean(dem_f))))

        result["elevation_mean"]  = round(float(np.nanmean(dem_f)), 1)
        result["elevation_range"] = round(float(np.nanmax(dem_f) - np.nanmin(dem_f)), 1)
        result["slope_mean"]      = round(float(np.nanmean(slope)), 2)
        result["slope_p90"]       = round(float(np.nanpercentile(slope, 90)), 2)
        result["roughness"]       = round(float(np.nanmean(rough)), 2)

    except Exception as exc:
        logger.warning("DEM terrain fetch failed: %s", exc)

    return result
