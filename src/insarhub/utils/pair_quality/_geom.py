# -*- coding: utf-8 -*-
"""
AOI geometry helpers for pair quality.

Three things, all to keep one definition of the AOI:

* :func:`wkt_centroid` — the location that keys the weather disk cache and the
  S1 coherence lookup.
* :func:`sample_grid_points` — the native-resolution (0.1°) sample grid weather
  is averaged over, so an AOI is represented by its area, not one cell.
* :func:`footprint_wkt_from_products` — the AOI recorded when the user drew
  none.

The DEM-derived terrain extractor that used to live here was removed with the
Copernicus GLO-30 pull: pair quality now fetches from Open-Meteo and the S1
global coherence dataset only.
"""

from __future__ import annotations

import logging
import re


logger = logging.getLogger(__name__)


def _wkt_bbox(wkt: str) -> tuple[float, float, float, float]:
    """Return (west, south, east, north) from a WKT POLYGON string."""
    coords = re.findall(r'(-?\d+\.?\d*)\s+(-?\d+\.?\d*)', wkt)
    if not coords:
        raise ValueError(f"No coordinates in WKT: {wkt!r}")
    lons = [float(c[0]) for c in coords]
    lats = [float(c[1]) for c in coords]
    return min(lons), min(lats), max(lons), max(lats)


def wkt_centroid(wkt: str) -> tuple[float, float]:
    """Return ``(lat, lon)`` for the centroid of a WKT geometry.

    This is *the* AOI centroid for the whole pair-quality subsystem.  It has
    to be, because the weather/snow disk cache is keyed by rounded lat/lon:
    two callers computing the centroid two different ways produce two
    different keys for one AOI, so neither ever sees the other's cached data
    and every date gets fetched twice.

    Shapely's true polygon centroid is authoritative.  The fallback averages
    the ring vertices, dropping the repeated closing vertex — a closed
    ``POLYGON`` repeats its first point, and counting it twice pulls the
    result toward that corner (~9 km on a typical Sentinel-1 AOI).
    """
    try:
        from shapely import wkt as _shapely_wkt

        centroid = _shapely_wkt.loads(wkt).centroid
        if not centroid.is_empty:
            return float(centroid.y), float(centroid.x)
    except Exception:
        pass

    coords = re.findall(r'(-?\d+\.?\d*)\s+(-?\d+\.?\d*)', wkt)
    if not coords:
        raise ValueError(f"No coordinates found in WKT: {wkt!r}")
    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]
    lons = [float(c[0]) for c in coords]
    lats = [float(c[1]) for c in coords]
    return sum(lats) / len(lats), sum(lons) / len(lons)


# The archive serves ERA5-Land on a 0.1° (~9 km) native grid; Open-Meteo does
# not expose anything finer for these variables.  Requesting points closer than
# one grid cell returns the same cell's data, so the sample grid is spaced at
# the native resolution rather than at a fixed count.  The default land
# cell-selection then matches each point to the cell with the closest elevation
# (90 m DEM), which is what lets a grid span an AOI's elevation range where a
# single centroid cannot.
DEFAULT_GRID_SPACING_DEG = 0.1

# One request carries all points.  Kept well below the archive's ~250-coordinate
# URL ceiling so a grid widens its spacing rather than failing with HTTP 414.
MAX_GRID_POINTS = 128


def sample_grid_points(
    wkt: str,
    spacing_deg: float = DEFAULT_GRID_SPACING_DEG,
    max_points: int = MAX_GRID_POINTS,
) -> list[tuple[float, float]]:
    """Return regular lat/lon sample points inside the AOI polygon.

    Points are cell centres of a grid at ``spacing_deg``, clipped to the
    polygon.  If that yields more than ``max_points`` the spacing is doubled
    until it fits, so a very large AOI degrades in resolution instead of
    producing a request the archive will reject.  Falls back to the centroid
    if the polygon cannot be parsed.
    """
    west, south, east, north = _wkt_bbox(wkt)
    spacing = float(spacing_deg)

    def _grid(step: float) -> list[tuple[float, float]]:
        n_lat = max(1, round((north - south) / step))
        n_lon = max(1, round((east - west) / step))
        return [
            (south + (i + 0.5) * (north - south) / n_lat,
             west + (j + 0.5) * (east - west) / n_lon)
            for i in range(n_lat)
            for j in range(n_lon)
        ]

    while True:
        points = _grid(spacing)
        if len(points) <= max_points or spacing > 5.0:
            break
        spacing *= 2

    try:
        from shapely import wkt as _shapely_wkt
        from shapely.geometry import Point

        poly = _shapely_wkt.loads(wkt)
        inside = [p for p in points if poly.contains(Point(p[1], p[0]))]
        if inside:
            points = inside
    except Exception:
        pass

    if not points:
        points = [wkt_centroid(wkt)]
    return points


def footprint_wkt_from_products(products) -> str | None:
    """Return the WKT union of ASFProduct footprints, or None on failure.

    Used to record an AOI when the user drew none, so pair quality has a
    region to fetch weather and coherence for. Both the CLI and the GUI route
    go through this one helper: the weather cache is keyed on the rounded
    centroid, so two callers deriving the AOI differently would produce two
    cache keys for one stack.
    """
    try:
        from shapely.geometry import shape as _shape
        from shapely.ops import unary_union
        geoms = [_shape(p.geometry) for p in products if getattr(p, "geometry", None)]
        if not geoms:
            return None
        return unary_union(geoms).wkt
    except Exception as exc:
        logger.warning("Could not compute footprint WKT: %s", exc)
        return None
