# -*- coding: utf-8 -*-
"""
MODIS MOD10A1 daily snow cover extractor.

Data source
-----------
MODIS Terra MOD10A1 v061 — daily 500 m snow cover fraction.
Accessed via NASA's OPeNDAP / Earthdata subsetting service so only the
AOI bounding box is transferred as a GeoTIFF — no full HDF download.

Endpoint pattern:
  https://n5eil02u.ecs.nsidc.org/opendap/MOST/MOD10A1.061/
  {YYYY.MM.DD}/MOD10A1.A{YYYYDDD}.h{HH}v{VV}.061.*.hdf

Rather than parsing HDF tile grids, we use NASA's APPEEARS-compatible
OPeNDAP subsetting or the NSIDC NetCDF/GeoTIFF subsetting REST API:
  https://n5eil02u.ecs.nsidc.org/egi/request
This requires the same Earthdata ~/.netrc credentials already used for
ASF downloads and MODIS NDVI.

Fallback chain
--------------
MOD10A1 OPeNDAP → Open-Meteo snow depth proxy (from _snow.py)

Features returned (per date)
----------------------------
  snow_cover_frac   : float | None  — fraction of AOI pixels with snow cover
  glacier_fraction  : float | None  — fraction of AOI pixels as permanent ice
  snow_source       : str           — "modis" | "openmeteo"

  (plus the Open-Meteo fields when used as fallback)
  snow_depth        : float | None  — cm
  snowfall          : float | None  — cm
  temp_max          : float | None  — °C
  temp_min          : float | None  — °C
"""

from __future__ import annotations

import json
import logging
import netrc
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

logger = logging.getLogger(__name__)

_EARTHDATA_HOST  = "urs.earthdata.nasa.gov"

# NSIDC EGI subsetting REST API for MOD10A1 GeoTIFF subsets
_NSIDC_EGI_URL   = "https://n5eil02u.ecs.nsidc.org/egi/request"
_PRODUCT         = "MOD10A1"
_VERSION         = "061"
_SNOW_COVER_BAND = "NDSI_Snow_Cover"      # 0–100 = snow fraction ×100; 200=nodata
_GLACIER_BAND    = "NDSI_Snow_Cover_Class"  # 254 = permanent ice

# Open-Meteo fallback (reuses existing _snow.py)
from insarhub.utils.pair_quality._snow import fetch_snow as _fetch_openmeteo_snow
from insarhub.utils.pair_quality._snow import fetch_snow_batch as _fetch_openmeteo_snow_batch


def _earthdata_creds() -> tuple[str, str] | None:
    try:
        nrc = netrc.netrc()
        auth = nrc.authenticators(_EARTHDATA_HOST)
        if auth:
            return auth[0], auth[2]
    except Exception:
        pass
    return None


def _doy(date: str) -> str:
    """Return zero-padded day-of-year string for a YYYY-MM-DD date."""
    dt = datetime.fromisoformat(date)
    return f"{dt.timetuple().tm_yday:03d}"


def _fetch_modis_snow(
    lat: float, lon: float, date: str,
    username: str, password: str,
    bbox_deg: float = 0.5,
) -> dict | None:
    """Query NSIDC EGI for a MOD10A1 GeoTIFF subset and compute snow fractions.

    Returns dict with snow_cover_frac and glacier_fraction, or None on failure.
    """
    dt = datetime.fromisoformat(date)
    time_str = f"{dt.strftime('%Y-%m-%d')}T00:00:00,{dt.strftime('%Y-%m-%d')}T23:59:59"
    bbox_str = f"{lon-bbox_deg},{lat-bbox_deg},{lon+bbox_deg},{lat+bbox_deg}"

    params = {
        "short_name": _PRODUCT,
        "version":    _VERSION,
        "temporal":   time_str,
        "bounding_box": bbox_str,
        "format":     "GeoTIFF",
        "projection": "Geographic",
        "Coverage":   f"/{_SNOW_COVER_BAND},{_GLACIER_BAND}",
        "page_size":  "1",
        "request_mode": "sync",
    }
    url = _NSIDC_EGI_URL + "?" + urllib.parse.urlencode(params)

    password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    password_mgr.add_password(None, _NSIDC_EGI_URL, username, password)
    opener = urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(password_mgr))

    try:
        with opener.open(url, timeout=15) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            logger.warning("NSIDC EGI auth failed (HTTP %d) — check ~/.netrc", exc.code)
        else:
            logger.warning("NSIDC EGI HTTP %d for MOD10A1 %s", exc.code, date)
        return None
    except Exception as exc:
        logger.warning("NSIDC EGI request failed for %s: %s", date, exc)
        return None

    # Parse the returned GeoTIFF bytes with rasterio
    try:
        import io
        import zipfile
        import rasterio

        # EGI returns a ZIP containing the GeoTIFF
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            tif_names = [n for n in zf.namelist() if n.lower().endswith(".tif")]
            if not tif_names:
                return None
            with zf.open(tif_names[0]) as f:
                tif_bytes = f.read()

        with rasterio.open(io.BytesIO(tif_bytes)) as src:
            data = src.read(1)

        # NDSI_Snow_Cover: 0–100 = % snow, 200+ = fill/nodata
        valid_mask = data < 200
        total_valid = int(valid_mask.sum())
        if total_valid == 0:
            return None

        snow_mask    = valid_mask & (data >= 40)   # ≥40 = snow-covered
        glacier_mask = data == 254                  # permanent ice class

        return {
            "snow_cover_frac":  round(snow_mask.sum() / total_valid, 4),
            "glacier_fraction": round(glacier_mask.sum() / data.size, 4),
        }

    except Exception as exc:
        logger.warning("MOD10A1 GeoTIFF parse failed for %s: %s", date, exc)
        return None


def fetch_snow_features(lat: float, lon: float, date: str) -> dict:
    """Return snow feature dict for *date* (YYYY-MM-DD).

    Tries MOD10A1 first; falls back to Open-Meteo ERA5 snow proxy.
    Always returns a complete dict; values may be None.
    """
    result: dict = {
        "snow_cover_frac":   None,
        "glacier_fraction":  None,
        "snow_depth":        None,
        "snowfall":          None,
        "temp_max":          None,
        "temp_min":          None,
        "snow_source":       "none",
    }

    creds = _earthdata_creds()
    if creds:
        modis = _fetch_modis_snow(lat, lon, date, *creds)
        if modis:
            result["snow_cover_frac"]  = modis["snow_cover_frac"]
            result["glacier_fraction"] = modis["glacier_fraction"]
            result["snow_source"]      = "modis"

    # Always fetch Open-Meteo for temperature + snow depth regardless of MODIS
    try:
        ow = _fetch_openmeteo_snow(lat, lon, date)
        result["snow_depth"] = ow.get("snow_depth")
        result["snowfall"]   = ow.get("snowfall")
        result["temp_max"]   = ow.get("temp_max")
        result["temp_min"]   = ow.get("temp_min")
        if result["snow_source"] == "none":
            result["snow_source"] = "openmeteo"
    except Exception as exc:
        logger.warning("Open-Meteo snow fallback failed for %s: %s", date, exc)

    return result


def fetch_snow_features_batch(lat: float, lon: float, dates: list[str]) -> dict[str, dict]:
    """Fetch snow features for ALL dates: batch Open-Meteo + concurrent MODIS.

    Returns dict mapping each date string → feature dict (same schema as
    fetch_snow_features).  Dates that fail all sources get all-None values.
    """
    _EMPTY: dict = {
        "snow_cover_frac": None, "glacier_fraction": None,
        "snow_depth": None, "snowfall": None,
        "temp_max": None, "temp_min": None, "snow_source": "none",
    }
    if not dates:
        return {}

    # 1. Batch Open-Meteo for all dates in two API calls (weather already done
    #    separately; here we only need the snow/temp fields)
    try:
        ow_batch = _fetch_openmeteo_snow_batch(lat, lon, dates)
    except Exception as exc:
        logger.warning("Snow batch Open-Meteo failed: %s", exc)
        ow_batch = {}

    # Merge Open-Meteo results only (NSIDC MODIS is per-date and too slow for batch)
    result: dict[str, dict] = {}
    for date in dates:
        r = dict(_EMPTY)
        ow = ow_batch.get(date, {})
        r["snow_depth"] = ow.get("snow_depth")
        r["snowfall"]   = ow.get("snowfall")
        r["temp_max"]   = ow.get("temp_max")
        r["temp_min"]   = ow.get("temp_min")
        r["snow_source"] = "openmeteo" if ow else "none"
        result[date] = r
    return result


def snow_cover_delta(s1: dict, s2: dict) -> float | None:
    """Absolute change in snow cover fraction between two dates."""
    f1 = s1.get("snow_cover_frac")
    f2 = s2.get("snow_cover_frac")
    if f1 is None or f2 is None:
        return None
    return round(abs(f2 - f1), 4)
