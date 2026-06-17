# -*- coding: utf-8 -*-
"""
ERA5-Land supplementary feature extractor via CDS API.

Downloads ERA5-Land hourly NetCDF for a given AOI and date list, returning
variables not exposed by Open-Meteo.  Results are spatially averaged over
all ERA5-Land grid cells (0.1°) whose centres fall inside the AOI polygon.

Requires
--------
    pip install cdsapi xarray netcdf4 shapely
    ~/.cdsapirc with valid Copernicus CDS credentials:
        url: https://cds.climate.copernicus.eu/api
        key: <uid>:<api-key>

Variables returned per date
---------------------------
  dewpoint          float | None  °C     2 m dewpoint at overpass hour
  soil_moisture_7_28  float | None  m³/m³  7–28 cm soil moisture at overpass hour
  soil_moisture_28_100 float | None  m³/m³  28–100 cm soil moisture at overpass hour
  skin_temp         float | None  °C     skin temperature at overpass hour
  wind_speed        float | None  m/s    10 m wind speed at overpass hour
  net_radiation     float | None  J/m²   daily net radiation (solar + thermal)
  lai_high_veg      float | None  m²/m²  leaf area index, high vegetation (daily)
  lai_low_veg       float | None  m²/m²  leaf area index, low vegetation (daily)
  snowmelt          float | None  m      daily snowmelt water equivalent

Interface
---------
    from insarhub.utils.pair_quality._weather_era5 import fetch_era5_batch, fetch_era5

    feats = fetch_era5_batch(
        aoi_wkt   = "POLYGON((-120.6 37.2, ...))",
        dates     = ["2023-01-15", "2023-06-20"],
        cache_dir = Path("/data/era5_cache"),
        date_hour = {"2023-01-15": 17, "2023-06-20": 17},
    )
    # feats["2023-01-15"] -> {"dewpoint": -3.2, "soil_moisture_7_28": 0.31, ...}

Caching strategy
----------------
One NetCDF file per (year, month, AOI-hash, variable-set) stored in cache_dir.
Repeated calls for overlapping date ranges do not re-download.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ── CDS variable names ────────────────────────────────────────────────────────

# Hourly — sliced at overpass hour
_HOURLY_VARS = [
    "2m_dewpoint_temperature",
    "volumetric_soil_water_layer_2",   # 7–28 cm
    "volumetric_soil_water_layer_3",   # 28–100 cm
    "skin_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
]

# Daily accumulations — summed over 24 h (ERA5-Land stores these as hourly
# accumulated fluxes; we download all hours and sum to get daily totals)
_ACCUM_VARS = [
    "surface_net_solar_radiation",
    "surface_net_thermal_radiation",
    "snowmelt",
]

# Instantaneous daily — take value at overpass hour (slowly varying)
_DAILY_INSTANT_VARS = [
    "leaf_area_index_high_vegetation",
    "leaf_area_index_low_vegetation",
]

_ALL_VARS = _HOURLY_VARS + _ACCUM_VARS + _DAILY_INSTANT_VARS

_EMPTY: dict = {
    "dewpoint":            None,
    "soil_moisture_7_28":  None,
    "soil_moisture_28_100": None,
    "skin_temp":           None,
    "wind_speed":          None,
    "net_radiation":       None,
    "lai_high_veg":        None,
    "lai_low_veg":         None,
    "snowmelt":            None,
}

# Kelvin offset — ERA5-Land temps in K, convert to °C
_K = 273.15


# ── AOI helpers ───────────────────────────────────────────────────────────────

def _aoi_hash(wkt: str) -> str:
    return hashlib.sha256(wkt.encode()).hexdigest()[:8]


def _aoi_bbox(wkt: str) -> tuple[float, float, float, float]:
    """Return (west, south, east, north) from WKT."""
    import re
    coords = re.findall(r'(-?\d+\.?\d*)\s+(-?\d+\.?\d*)', wkt)
    lons = [float(c[0]) for c in coords]
    lats = [float(c[1]) for c in coords]
    return min(lons), min(lats), max(lons), max(lats)


def _grid_points_in_aoi(wkt: str) -> list[tuple[float, float]]:
    """Return ERA5-Land 0.1° grid centres inside the AOI polygon."""
    from shapely import wkt as _wkt
    from shapely.geometry import Point
    geom = _wkt.loads(wkt)
    w, s, e, n = geom.bounds
    lats = np.arange(math.ceil(s * 10) / 10, n + 0.05, 0.1)
    lons = np.arange(math.ceil(w * 10) / 10, e + 0.05, 0.1)
    return [
        (round(lat, 4), round(lon, 4))
        for lat in lats for lon in lons
        if geom.contains(Point(lon, lat))
    ]


# ── CDS download ──────────────────────────────────────────────────────────────

def _cache_path(cache_dir: Path, year: int, month: int, aoi_wkt: str) -> Path:
    tag = f"{year}{month:02d}_{_aoi_hash(aoi_wkt)}"
    return cache_dir / f"era5land_{tag}.nc"


def _download_month(
    year: int,
    month: int,
    aoi_wkt: str,
    out_path: Path,
) -> None:
    """Download one month of ERA5-Land data for the AOI bounding box."""
    import cdsapi
    w, s, e, n = _aoi_bbox(aoi_wkt)
    buf = 0.2
    area = [
        round(n + buf, 2),
        round(w - buf, 2),
        round(s - buf, 2),
        round(e + buf, 2),
    ]
    c = cdsapi.Client(quiet=True)
    c.retrieve(
        "reanalysis-era5-land",
        {
            "product_type": "reanalysis",
            "variable":     _ALL_VARS,
            "year":         str(year),
            "month":        f"{month:02d}",
            "day":          [f"{d:02d}" for d in range(1, 32)],
            "time":         [f"{h:02d}:00" for h in range(24)],
            "area":         area,
            "format":       "netcdf",
        },
        str(out_path),
    )
    logger.info("Downloaded ERA5-Land %d-%02d to %s", year, month, out_path)


# ── Extraction from NetCDF ────────────────────────────────────────────────────

def _extract_dates_from_nc(
    nc_path: Path,
    dates: list[str],
    date_hour: dict[str, int],
    aoi_wkt: str,
) -> dict[str, dict]:
    """Read ERA5-Land NetCDF, spatially average over AOI, return features per date."""
    import xarray as xr
    ds = xr.open_dataset(nc_path)

    # Identify spatial coords (ERA5-Land uses 'latitude'/'longitude')
    lat_name = "latitude" if "latitude" in ds.coords else "lat"
    lon_name = "longitude" if "longitude" in ds.coords else "lon"

    # Spatial mask: grid cells inside AOI
    points = _grid_points_in_aoi(aoi_wkt)
    if not points:
        # Fall back to bbox mean
        w, s, e, n = _aoi_bbox(aoi_wkt)
        ds_aoi = ds.sel(
            {lat_name: slice(n, s), lon_name: slice(w, e)}
        )
    else:
        aoi_lats = sorted({p[0] for p in points}, reverse=True)
        aoi_lons = sorted({p[1] for p in points})
        ds_aoi = ds.sel(
            {lat_name: aoi_lats, lon_name: aoi_lons},
            method="nearest",
        )

    # Spatial mean (collapse lat+lon)
    ds_mean = ds_aoi.mean(dim=[lat_name, lon_name], skipna=True)

    result: dict[str, dict] = {}
    for date in dates:
        hour = date_hour.get(date, 17)
        r = dict(_EMPTY)
        try:
            # Hourly slice at overpass hour
            t_hour = np.datetime64(f"{date}T{hour:02d}:00:00")
            pt = ds_mean.sel(time=t_hour, method="nearest")

            def _k2c(val: Any) -> float | None:
                v = float(val) if val is not None else None
                return round(v - _K, 2) if v is not None else None

            def _fv(da_name: str) -> float | None:
                if da_name not in ds_mean:
                    return None
                try:
                    return round(float(pt[da_name].values), 4)
                except Exception:
                    return None

            u = _fv("u10")
            v = _fv("v10")

            r["dewpoint"]            = _k2c(_fv("d2m"))
            r["soil_moisture_7_28"]  = _fv("swvl2")
            r["soil_moisture_28_100"] = _fv("swvl3")
            r["skin_temp"]           = _k2c(_fv("skt"))
            r["wind_speed"]          = round(math.sqrt(u**2 + v**2), 3) if (u and v) else None
            r["lai_high_veg"]        = _fv("lai_hv")
            r["lai_low_veg"]         = _fv("lai_lv")

            # Accumulated fluxes: sum all 24 h for the day
            t_day_start = np.datetime64(f"{date}T00:00:00")
            t_day_end   = np.datetime64(f"{date}T23:00:00")
            day_slice   = ds_mean.sel(time=slice(t_day_start, t_day_end))

            def _daily_sum(da_name: str) -> float | None:
                if da_name not in day_slice:
                    return None
                try:
                    return round(float(day_slice[da_name].sum(skipna=True).values), 4)
                except Exception:
                    return None

            ssr  = _daily_sum("ssr")
            str_ = _daily_sum("str")
            r["net_radiation"] = round(ssr + str_, 2) if (ssr is not None and str_ is not None) else None
            r["snowmelt"]      = _daily_sum("smlt")

        except Exception as exc:
            logger.warning("ERA5 extraction failed for %s: %s", date, exc)

        result[date] = r

    ds.close()
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_era5_batch(
    aoi_wkt: str,
    dates: list[str],
    date_hour: dict[str, int] | None = None,
    cache_dir: Path | str | None = None,
    overpass_hour: int = 17,
) -> dict[str, dict]:
    """Fetch ERA5-Land supplementary features for all dates.

    Parameters
    ----------
    aoi_wkt      : WKT polygon of the AOI — used for spatial averaging and download bbox
    dates        : list of ISO-8601 date strings (YYYY-MM-DD)
    date_hour    : per-date UTC overpass hour (from scene name).  Falls back to
                   *overpass_hour* when a date has no entry.
    cache_dir    : directory for monthly ERA5-Land NetCDF cache files.
                   Defaults to ~/.insarhub/era5_cache.
    overpass_hour: default UTC hour when date_hour has no entry (default 17)

    Returns
    -------
    dict mapping each date string → ERA5-Land feature dict.
    Missing dates / download failures get all-None dicts.
    """
    if not dates:
        return {}

    _dh = {d: date_hour.get(d, overpass_hour) if date_hour else overpass_hour
           for d in dates}

    cache_root = Path(cache_dir).expanduser() if cache_dir else \
        Path.home() / ".insarhub" / "era5_cache"
    cache_root.mkdir(parents=True, exist_ok=True)

    # Group dates by (year, month) for one download per month
    by_month: dict[tuple[int, int], list[str]] = defaultdict(list)
    for d in dates:
        y, m = int(d[:4]), int(d[5:7])
        by_month[(y, m)].append(d)

    result: dict[str, dict] = {d: dict(_EMPTY) for d in dates}

    for (year, month), month_dates in by_month.items():
        nc = _cache_path(cache_root, year, month, aoi_wkt)
        if not nc.exists():
            try:
                _download_month(year, month, aoi_wkt, nc)
            except Exception as exc:
                logger.warning(
                    "ERA5-Land download failed for %d-%02d: %s — returning None values",
                    year, month, exc,
                )
                continue
        try:
            feats = _extract_dates_from_nc(nc, month_dates, _dh, aoi_wkt)
            result.update(feats)
        except Exception as exc:
            logger.warning("ERA5-Land extraction failed for %d-%02d: %s", year, month, exc)

    return result


def fetch_era5(
    aoi_wkt: str,
    date: str,
    overpass_hour: int = 17,
    cache_dir: Path | str | None = None,
) -> dict:
    """Return ERA5-Land supplementary feature dict for a single *date*."""
    result = fetch_era5_batch(
        aoi_wkt,
        [date],
        date_hour={date: overpass_hour},
        cache_dir=cache_dir,
    )
    return result.get(date, dict(_EMPTY))
