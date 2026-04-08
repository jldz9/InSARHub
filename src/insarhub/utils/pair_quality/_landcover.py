# -*- coding: utf-8 -*-
"""
ESA WorldCover 2021 land cover feature extractor.

Data source
-----------
ESA WorldCover 10 m v200 (2021), distributed as Cloud-Optimized GeoTIFFs
on AWS S3 with public HTTPS access — no authentication required.

Tile URL pattern:
  https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/
  ESA_WorldCover_10m_2021_v200_{tile}_Map.tif

where {tile} is a 6-character code like "N36W113" derived from the AOI
bounding box lower-left corner at 3° resolution.

Implementation uses rasterio's /vsicurl/ virtual filesystem for windowed
reads — only the AOI pixels are transferred, never the full ~400 MB tile.

ESA WorldCover class codes
--------------------------
  10  Tree cover          30  Grassland         60  Bare/sparse veg
  20  Shrubland           40  Cropland          70  Snow/ice
  50  Built-up            80  Water             90  Herbaceous wetland
  95  Mangroves           100 Moss/lichen

Features returned (AOI-level)
-----------------------------
  lc_forest_fraction   : float — class 10
  lc_shrub_fraction    : float — class 20
  lc_grass_fraction    : float — class 30
  lc_crop_fraction     : float — class 40
  lc_urban_fraction    : float — class 50
  lc_bare_fraction     : float — class 60
  lc_snow_fraction     : float — class 70
  lc_water_fraction    : float — class 80
  lc_dominant_class    : int   — most common class code, -1 if unknown
"""

from __future__ import annotations

import logging
import re

import numpy as np

logger = logging.getLogger(__name__)

_BASE_URL = (
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com"
    "/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
)

# Mapping: class code → feature key
_CLASS_FEATURES: dict[int, str] = {
    10: "lc_forest_fraction",
    20: "lc_shrub_fraction",
    30: "lc_grass_fraction",
    40: "lc_crop_fraction",
    50: "lc_urban_fraction",
    60: "lc_bare_fraction",
    70: "lc_snow_fraction",
    80: "lc_water_fraction",
}

_NULL_RESULT: dict = {
    "lc_forest_fraction": None,
    "lc_shrub_fraction":  None,
    "lc_grass_fraction":  None,
    "lc_crop_fraction":   None,
    "lc_urban_fraction":  None,
    "lc_bare_fraction":   None,
    "lc_snow_fraction":   None,
    "lc_water_fraction":  None,
    "lc_dominant_class":  None,
}


def _tile_name(west: float, south: float) -> str:
    """Return WorldCover tile code for the lower-left corner of a 3° cell."""
    # Tile grid is 3° × 3°, aligned to multiples of 3
    tile_lat = int(south // 3) * 3
    tile_lon = int(west // 3) * 3
    lat_str = f"N{abs(tile_lat):02d}" if tile_lat >= 0 else f"S{abs(tile_lat):02d}"
    lon_str = f"E{abs(tile_lon):03d}" if tile_lon >= 0 else f"W{abs(tile_lon):03d}"
    return lat_str + lon_str


def _wkt_bbox(wkt: str) -> tuple[float, float, float, float]:
    coords = re.findall(r'(-?\d+\.?\d*)\s+(-?\d+\.?\d*)', wkt)
    if not coords:
        raise ValueError(f"No coordinates in WKT: {wkt!r}")
    lons = [float(c[0]) for c in coords]
    lats = [float(c[1]) for c in coords]
    return min(lons), min(lats), max(lons), max(lats)


def read_pixels(aoi_wkt: str) -> tuple:
    """Return (pixel_array, window_transform, crs) for WorldCover over the AOI.

    Used by coherence per-class separation — returns the raw uint8 class array
    so callers can reproject it to the coherence grid and group pixel values.
    Returns (None, None, None) on any failure.
    """
    try:
        import rasterio
        from rasterio.windows import from_bounds

        west, south, east, north = _wkt_bbox(aoi_wkt)
        tile = _tile_name(west, south)
        url  = "/vsicurl/" + _BASE_URL.format(tile=tile)

        with rasterio.open(url) as src:
            window       = from_bounds(west, south, east, north, src.transform)
            data         = src.read(1, window=window)
            win_transform = src.window_transform(window)
            crs          = src.crs

        return data, win_transform, crs
    except Exception as exc:
        logger.debug("WorldCover pixel read failed: %s", exc)
        return None, None, None


def extract(aoi_wkt: str) -> dict:
    """Return land cover feature dict for the given AOI WKT.

    Uses a windowed rasterio read via /vsicurl/ — no tile is saved to disk.
    Returns _NULL_RESULT on any failure so the classifier can degrade gracefully.
    """
    try:
        import rasterio
        from rasterio.windows import from_bounds

        west, south, east, north = _wkt_bbox(aoi_wkt)
        tile = _tile_name(west, south)
        url  = "/vsicurl/" + _BASE_URL.format(tile=tile)

        with rasterio.open(url) as src:
            window = from_bounds(west, south, east, north, src.transform)
            data = src.read(1, window=window)

        if data.size == 0:
            return dict(_NULL_RESULT)

        total  = data.size
        counts = {cls: int(np.sum(data == cls)) for cls in _CLASS_FEATURES}
        result: dict = {feat: round(counts[cls] / total, 4)
                        for cls, feat in _CLASS_FEATURES.items()}

        # Dominant class (most frequent, ignoring 0 = nodata)
        valid_counts = {cls: counts[cls] for cls in _CLASS_FEATURES if counts[cls] > 0}
        result["lc_dominant_class"] = max(valid_counts, key=valid_counts.get) if valid_counts else -1

        return result

    except Exception as exc:
        logger.warning("WorldCover fetch failed: %s", exc)
        return dict(_NULL_RESULT)
