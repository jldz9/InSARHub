# -*- coding: utf-8 -*-
"""
ERA5-Land weather feature extractor via Open-Meteo archive API.

Batch interface (preferred)
---------------------------
    fetch_weather_batch(lat, lon, dates) -> dict[date_str, dict]

Fetches the entire date range in a SINGLE API call, then slices per date.
For a 3-year S1 stack (~90 dates) this is ~2 requests total instead of 90.

Single-date interface (kept for backward compatibility)
-------------------------------------------------------
    fetch_weather(lat, lon, date) -> dict

Features returned per date
--------------------------
  temp_max      : float | None  °C
  temp_min      : float | None  °C
  precip        : float | None  mm (daily)
  precip_7day   : float | None  mm (7-day rolling sum ending on that date)
  snow_depth    : float | None  cm
  snowfall      : float | None  cm
  soil_moisture : float | None  m³/m³ (0–7 cm)
  et0           : float | None  mm (FAO-56 reference ET)

freeze_thaw(w1, w2) -> int   (cross-date derived feature)
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_URL = "https://archive-api.open-meteo.com/v1/archive"

_DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "snowfall_sum",
    "snow_depth_max",
    "soil_moisture_0_to_7cm_mean",
    "et0_fao_evapotranspiration",
]

_EMPTY: dict = {
    "temp_max":      None,
    "temp_min":      None,
    "precip":        None,
    "precip_3day":   None,
    "precip_7day":   None,
    "snow_depth":    None,
    "snowfall":      None,
    "soil_moisture": None,
    "et0":           None,
}


# ── Low-level range fetch ─────────────────────────────────────────────────────

def _fetch_range(lat: float, lon: float, start: str, end: str) -> dict[str, list]:
    """One HTTP request → raw daily dict keyed by variable name."""
    params = {
        "latitude":   f"{lat:.4f}",
        "longitude":  f"{lon:.4f}",
        "start_date": start,
        "end_date":   end,
        "daily":      ",".join(_DAILY_VARS),
        "timezone":   "UTC",
    }
    url = _URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        payload = json.loads(resp.read())
    return payload.get("daily", {})


def _extract_date(daily: dict, date: str, all_precip: list | None) -> dict:
    """Slice one date out of a pre-fetched daily dict."""
    dates = daily.get("time") or []
    try:
        idx = dates.index(date)
    except ValueError:
        return dict(_EMPTY)

    def v(key: str):
        vals = daily.get(key) or []
        return vals[idx] if idx < len(vals) else None

    # Rolling precipitation windows ending on this acquisition date.
    # The batch fetch prepends enough days so both windows are always available.
    precip_3day: float | None = None
    precip_7day: float | None = None
    if all_precip:
        w3 = all_precip[max(0, idx - 2): idx + 1]   # 3-day window: days -2, -1, 0
        w7 = all_precip[max(0, idx - 6): idx + 1]   # 7-day window
        c3 = [x for x in w3 if x is not None]
        c7 = [x for x in w7 if x is not None]
        precip_3day = round(sum(c3), 2) if c3 else None
        precip_7day = round(sum(c7), 2) if c7 else None

    return {
        "temp_max":      v("temperature_2m_max"),
        "temp_min":      v("temperature_2m_min"),
        "precip":        v("precipitation_sum"),
        "precip_3day":   precip_3day,
        "precip_7day":   precip_7day,
        "snow_depth":    v("snow_depth_max"),
        "snowfall":      v("snowfall_sum"),
        "soil_moisture": v("soil_moisture_0_to_7cm_mean"),
        "et0":           v("et0_fao_evapotranspiration"),
    }


# ── Batch interface (main path) ───────────────────────────────────────────────

def fetch_weather_batch(
    lat: float,
    lon: float,
    dates: list[str],
) -> dict[str, dict]:
    """Fetch ERA5-Land features for ALL dates in a single API call.

    Parameters
    ----------
    dates : list of ISO-8601 date strings (YYYY-MM-DD), any order

    Returns
    -------
    dict mapping each date string → weather feature dict.
    Dates missing from the response (future, API gap) get an _EMPTY dict.
    """
    if not dates:
        return {}

    sorted_dates = sorted(dates)
    earliest = datetime.fromisoformat(sorted_dates[0])
    latest   = datetime.fromisoformat(sorted_dates[-1])

    # Prepend 6 days so rolling-7-day precip is available for the earliest date
    fetch_start = (earliest - timedelta(days=6)).strftime("%Y-%m-%d")
    fetch_end   = latest.strftime("%Y-%m-%d")

    try:
        daily = _fetch_range(lat, lon, fetch_start, fetch_end)
    except Exception as exc:
        logger.warning("Weather batch fetch failed (%s → %s): %s",
                       fetch_start, fetch_end, exc)
        return {d: dict(_EMPTY) for d in dates}

    all_precip = daily.get("precipitation_sum")
    return {date: _extract_date(daily, date, all_precip) for date in dates}


# ── Single-date interface (backward compat / standalone use) ──────────────────

def fetch_weather(lat: float, lon: float, date: str) -> dict:
    """Return weather feature dict for a single *date* (YYYY-MM-DD)."""
    result = fetch_weather_batch(lat, lon, [date])
    return result.get(date, dict(_EMPTY))


# ── Cross-date derived feature ────────────────────────────────────────────────

def freeze_thaw(w1: dict, w2: dict) -> int:
    """Return 1 if temp_max crosses 0°C between the two dates."""
    t1 = w1.get("temp_max")
    t2 = w2.get("temp_max")
    if t1 is None or t2 is None:
        return 0
    return int((t1 < 0) != (t2 < 0))
