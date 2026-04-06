# -*- coding: utf-8 -*-
"""
NDVI data layer: MODIS MOD13Q1 (primary) + monthly climatology (fallback).

Primary source
--------------
ORNL DAAC MODIS Web Service — MOD13Q1 v061, 16-day NDVI.
Supports batch fetching: one API call for the entire date range,
then slice per acquisition date using the nearest preceding composite.

Fallback source
---------------
Hard-coded monthly NDVI climatology table keyed by 10° latitude band
and calendar month (1–12).
"""

from __future__ import annotations

import json
import logging
import netrc
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_ORNL_URL       = "https://modis.ornl.gov/rst/api/v1/"
_EARTHDATA_HOST = "urs.earthdata.nasa.gov"
_PRODUCT        = "MOD13Q1"
_NDVI_BAND      = "250m 16 days NDVI"
_NDVI_SCALE     = 0.0001
_NDVI_FILL      = -28672

_CLIMATOLOGY: list[tuple[tuple[int, int], list[float]]] = [
    ((-90, -70), [0.03, 0.05, 0.08, 0.05, 0.03, 0.02, 0.02, 0.02, 0.03, 0.04, 0.03, 0.02]),
    ((-70, -55), [0.10, 0.12, 0.10, 0.07, 0.04, 0.03, 0.03, 0.04, 0.06, 0.09, 0.10, 0.10]),
    ((-55, -40), [0.42, 0.42, 0.38, 0.32, 0.25, 0.20, 0.19, 0.22, 0.28, 0.35, 0.40, 0.42]),
    ((-40, -23), [0.52, 0.52, 0.50, 0.46, 0.40, 0.34, 0.33, 0.36, 0.42, 0.48, 0.51, 0.52]),
    ((-23,  23), [0.55, 0.55, 0.57, 0.62, 0.65, 0.64, 0.62, 0.61, 0.60, 0.57, 0.55, 0.54]),
    (( 23,  35), [0.22, 0.24, 0.32, 0.42, 0.52, 0.56, 0.55, 0.51, 0.46, 0.37, 0.28, 0.22]),
    (( 35,  50), [0.12, 0.14, 0.22, 0.38, 0.56, 0.65, 0.66, 0.61, 0.52, 0.38, 0.22, 0.13]),
    (( 50,  62), [0.07, 0.07, 0.10, 0.22, 0.44, 0.60, 0.63, 0.55, 0.38, 0.20, 0.09, 0.07]),
    (( 62,  70), [0.04, 0.04, 0.05, 0.10, 0.30, 0.50, 0.55, 0.44, 0.24, 0.09, 0.04, 0.04]),
    (( 70,  90), [0.02, 0.02, 0.03, 0.05, 0.12, 0.22, 0.26, 0.20, 0.10, 0.04, 0.02, 0.02]),
]


def climatology_ndvi(lat: float, month: int) -> float:
    for (lo, hi), monthly in _CLIMATOLOGY:
        if lo <= lat < hi:
            return monthly[month - 1]
    return 0.03


def _earthdata_creds() -> Optional[tuple[str, str]]:
    try:
        nrc = netrc.netrc()
        auth = nrc.authenticators(_EARTHDATA_HOST)
        if auth:
            return auth[0], auth[2]
    except Exception:
        pass
    return None


def _to_modis_date(dt: datetime) -> str:
    doy = dt.timetuple().tm_yday
    return f"A{dt.year}{doy:03d}"


# ── Batch fetch (main path) ───────────────────────────────────────────────────

def fetch_modis_ndvi_batch(
    lat: float, lon: float,
    start_date: str, end_date: str,
    username: str, password: str,
) -> dict[str, float]:
    """Fetch all MOD13Q1 16-day NDVI composites in [start_date, end_date].

    Returns dict mapping MODIS A-date string → NDVI float (0–1).
    Returns empty dict on failure.
    """
    start_dt = datetime.fromisoformat(start_date)
    end_dt   = datetime.fromisoformat(end_date)
    params = {
        "latitude":     f"{lat:.4f}",
        "longitude":    f"{lon:.4f}",
        "startDate":    _to_modis_date(start_dt),
        "endDate":      _to_modis_date(end_dt),
        "kmAboveBelow": "0",
        "kmLeftRight":  "0",
    }
    url = _ORNL_URL + f"{_PRODUCT}/subset?" + urllib.parse.urlencode(params)

    password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    password_mgr.add_password(None, _ORNL_URL, username, password)
    opener = urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(password_mgr))

    try:
        with opener.open(url, timeout=30) as resp:
            payload = json.loads(resp.read())
    except Exception as exc:
        logger.warning("MODIS batch fetch failed (%s → %s): %s", start_date, end_date, exc)
        return {}

    result: dict[str, float] = {}
    for subset in payload.get("subset", []):
        if _NDVI_BAND not in subset.get("band", ""):
            continue
        cal_date = subset.get("calendar_date", "")  # "YYYY-MM-DD" from ORNL
        modis_date = subset.get("modis_date", "")   # "AYYYYDDD"
        key = modis_date or cal_date
        if not key:
            continue
        values = [v for v in (subset.get("data") or []) if v > _NDVI_FILL]
        if values:
            ndvi = sum(values) / len(values) * _NDVI_SCALE
            result[key] = max(0.0, min(1.0, ndvi))

    return result


def _nearest_ndvi(modis_map: dict[str, float], target_date: str) -> Optional[float]:
    """Return the NDVI from the composite whose date is closest to target_date."""
    if not modis_map:
        return None
    target_dt = datetime.fromisoformat(target_date)

    # Convert MODIS A-dates to datetime for comparison
    def _parse(key: str) -> datetime:
        if key.startswith("A") and len(key) == 8:
            return datetime.strptime(key, "A%Y%j")
        try:
            return datetime.fromisoformat(key[:10])
        except ValueError:
            return datetime(1970, 1, 1)

    candidates = [(abs((_parse(k) - target_dt).days), v)
                  for k, v in modis_map.items()]
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1] if candidates else None


# ── Public interfaces ─────────────────────────────────────────────────────────

def get_ndvi_batch(
    lat: float, lon: float,
    dates: list[str],
    cache: dict,
    source: str = "auto",
) -> None:
    """Prefetch NDVI for all dates in one session and populate cache.

    Parameters
    ----------
    source : "auto" | "modis" | "cdse"
        "auto"  — use CDSE (Sentinel-2 10m) if credentials available, else MODIS.
        "modis" — ORNL DAAC MOD13Q1 250m 16-day composite (requires NASA Earthdata).
        "cdse"  — Copernicus Data Space Sentinel-2 L2A 10m (requires CDSE account).

    After calling this, get_ndvi() for any date in dates hits only the cache.
    """
    if not dates:
        return
    sorted_dates = sorted(dates)

    uncached = [d for d in dates
                if f"ndvi:{lat:.3f}:{lon:.3f}:{d}" not in cache]
    if not uncached:
        return

    # ── CDSE path ─────────────────────────────────────────────────────────────
    use_cdse = False
    if source == "cdse":
        use_cdse = True
    elif source == "auto":
        from insarhub.utils.pair_quality._ndvi_cdse import cdse_creds_available
        use_cdse = cdse_creds_available()

    cdse_map: dict[str, float] = {}
    if use_cdse:
        from insarhub.utils.pair_quality._ndvi_cdse import fetch_cdse_ndvi_batch
        cdse_map = fetch_cdse_ndvi_batch(lat, lon, uncached)

    # ── MODIS path (primary or fallback) ─────────────────────────────────────
    still_missing = [d for d in uncached if d not in cdse_map]
    modis_map: dict[str, float] = {}
    if still_missing and source != "cdse":
        creds = _earthdata_creds()
        if creds:
            modis_map = fetch_modis_ndvi_batch(
                lat, lon, sorted_dates[0], sorted_dates[-1], *creds,
            )

    # ── Populate cache ────────────────────────────────────────────────────────
    for date in uncached:
        cache_key = f"ndvi:{lat:.3f}:{lon:.3f}:{date}"
        if date in cdse_map:
            cache[cache_key] = {"ndvi": cdse_map[date], "source": "cdse"}
        else:
            ndvi = _nearest_ndvi(modis_map, date) if modis_map else None
            if ndvi is not None:
                cache[cache_key] = {"ndvi": ndvi, "source": "modis"}
            else:
                dt = datetime.fromisoformat(date)
                cache[cache_key] = {
                    "ndvi":   climatology_ndvi(lat, dt.month),
                    "source": "climatology",
                }


def get_ndvi(lat: float, lon: float, date: str, cache: dict,
             source: str = "auto") -> tuple[float, str]:
    """Return (ndvi, source) for the given date. Hits cache first."""
    cache_key = f"ndvi:{lat:.3f}:{lon:.3f}:{date}"
    if cache_key in cache:
        val = cache[cache_key]
        return val["ndvi"], val["source"]

    # Single-date fallback — run batch for just this date
    get_ndvi_batch(lat, lon, [date], cache, source=source)
    if cache_key in cache:
        val = cache[cache_key]
        return val["ndvi"], val["source"]

    # Final fallback to climatology
    dt = datetime.fromisoformat(date)
    ndvi = climatology_ndvi(lat, dt.month)
    cache[cache_key] = {"ndvi": ndvi, "source": "climatology"}
    return ndvi, "climatology"
