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
  temp           : float | None  °C — overpass-hour 2 m temperature
  temp_max       : float | None  °C — daily max (backward compat / freeze_thaw fallback)
  temp_min       : float | None  °C — daily min
  precip         : float | None  mm (daily sum)
  precip_7day    : float | None  mm (7-day rolling sum ending on that date)
  snow_depth     : float | None  m  (daily max — ERA5 hourly snow_depth not fetched here)
  snowfall       : float | None  cm (daily sum)
  soil_moisture  : float | None  m³/m³ (0–7 cm) — overpass-hour value
  et0            : float | None  mm (FAO-56 reference ET, daily only)

freeze_thaw(w1, w2) -> int   (cross-date derived feature)

Overpass hour
-------------
OVERPASS_HOUR controls which UTC hour is used for point-in-time variables
(temp, soil_moisture).  Sentinel-1 descending passes ≈ 17:00 UTC; ascending
≈ 05:00 UTC.  Override at module level before the first call:

    import insarhub.utils.pair_quality._weather as _w
    _w.OVERPASS_HOUR = 5   # ascending-orbit AOI
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_URL = "https://archive-api.open-meteo.com/v1/archive"

# Point-in-time hourly variables — sliced at OVERPASS_HOUR UTC.
_HOURLY_VARS = [
    "temperature_2m",
    "soil_moisture_0_to_7cm",
]

# Daily aggregate variables — kept for precip rolling sums, snowfall, et0,
# and as backward-compat fallback for temp (daily max/min).
_DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "snowfall_sum",
    "snow_depth_max",
    "et0_fao_evapotranspiration",
]

# UTC hour for overpass-hour slicing.
# S-1 descending ≈ 17, ascending ≈ 05.  Override before first call if needed.
OVERPASS_HOUR: int = 17

_EMPTY: dict = {
    "temp":          None,   # overpass-hour 2 m temperature (°C)
    "temp_max":      None,   # daily max °C — backward compat
    "temp_min":      None,   # daily min °C — backward compat
    "precip":        None,
    "precip_3day":   None,
    "precip_7day":   None,
    "snow_depth":    None,
    "snowfall":      None,
    "soil_moisture": None,   # overpass-hour 0–7 cm (m³/m³)
    "et0":           None,
}


# ── Low-level range fetch ─────────────────────────────────────────────────────

def _fetch_range(
    lat: float, lon: float, start: str, end: str
) -> tuple[dict[str, list], dict[str, list]]:
    """One HTTP request → (daily_dict, hourly_dict) keyed by variable name."""
    params = {
        "latitude":   f"{lat:.4f}",
        "longitude":  f"{lon:.4f}",
        "start_date": start,
        "end_date":   end,
        "daily":      ",".join(_DAILY_VARS),
        "hourly":     ",".join(_HOURLY_VARS),
        "timezone":   "UTC",
    }
    url = _URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        payload = json.loads(resp.read())
    return payload.get("daily", {}), payload.get("hourly", {})


def _hourly_val(
    hourly: dict, date: str, variable: str, hour: int = OVERPASS_HOUR
) -> float | None:
    """Return *variable* value from ERA5 hourly output at *hour* UTC on *date*."""
    times = hourly.get("time") or []
    target = f"{date}T{hour:02d}:00"
    try:
        idx = times.index(target)
    except ValueError:
        return None
    vals = hourly.get(variable) or []
    return vals[idx] if idx < len(vals) else None


def _extract_date(
    daily: dict,
    hourly: dict,
    date: str,
    all_precip: list | None,
    overpass_hour: int = OVERPASS_HOUR,
) -> dict:
    """Slice one date out of pre-fetched daily/hourly dicts."""
    dates = daily.get("time") or []
    try:
        idx = dates.index(date)
    except ValueError:
        return dict(_EMPTY)

    def v(key: str):
        vals = daily.get(key) or []
        return vals[idx] if idx < len(vals) else None

    # Rolling precipitation windows ending on this acquisition date.
    precip_3day: float | None = None
    precip_7day: float | None = None
    if all_precip:
        w3 = all_precip[max(0, idx - 2): idx + 1]
        w7 = all_precip[max(0, idx - 6): idx + 1]
        c3 = [x for x in w3 if x is not None]
        c7 = [x for x in w7 if x is not None]
        precip_3day = round(sum(c3), 2) if c3 else None
        precip_7day = round(sum(c7), 2) if c7 else None

    return {
        "temp":          _hourly_val(hourly, date, "temperature_2m", overpass_hour),
        "temp_max":      v("temperature_2m_max"),
        "temp_min":      v("temperature_2m_min"),
        "precip":        v("precipitation_sum"),
        "precip_3day":   precip_3day,
        "precip_7day":   precip_7day,
        "snow_depth":    v("snow_depth_max"),
        "snowfall":      v("snowfall_sum"),
        "soil_moisture": _hourly_val(hourly, date, "soil_moisture_0_to_7cm", overpass_hour),
        "et0":           v("et0_fao_evapotranspiration"),
    }


# ── Batch interface (main path) ───────────────────────────────────────────────

def fetch_weather_batch(
    lat: float,
    lon: float,
    dates: list[str],
    overpass_hour: int | None = None,
    date_hour: dict[str, int] | None = None,
) -> dict[str, dict]:
    """Fetch ERA5-Land features for ALL dates in a single API call.

    Parameters
    ----------
    dates         : list of ISO-8601 date strings (YYYY-MM-DD), any order
    overpass_hour : fallback UTC hour when *date_hour* has no entry (default: OVERPASS_HOUR)
    date_hour     : per-date UTC hour, e.g. {\"2023-01-15\": 17} — from scene name parsing.
                    When provided, each date uses its exact overpass hour.

    Returns
    -------
    dict mapping each date string → weather feature dict.
    Dates missing from the response (future, API gap) get an _EMPTY dict.
    """
    if not dates:
        return {}

    default_hour = overpass_hour if overpass_hour is not None else OVERPASS_HOUR
    _dh = date_hour or {}

    sorted_dates = sorted(dates)
    earliest = datetime.fromisoformat(sorted_dates[0])
    latest   = datetime.fromisoformat(sorted_dates[-1])

    # Prepend 6 days so rolling-7-day precip is available for the earliest date.
    fetch_start = (earliest - timedelta(days=6)).strftime("%Y-%m-%d")
    fetch_end   = latest.strftime("%Y-%m-%d")

    try:
        daily, hourly = _fetch_range(lat, lon, fetch_start, fetch_end)
    except Exception as exc:
        logger.warning("Weather batch fetch failed (%s → %s): %s",
                       fetch_start, fetch_end, exc)
        return {d: dict(_EMPTY) for d in dates}

    all_precip = daily.get("precipitation_sum")
    return {
        date: _extract_date(daily, hourly, date, all_precip, _dh.get(date, default_hour))
        for date in dates
    }


# ── Single-date interface (backward compat / standalone use) ──────────────────

def fetch_weather(
    lat: float,
    lon: float,
    date: str,
    overpass_hour: int | None = None,
) -> dict:
    """Return weather feature dict for a single *date* (YYYY-MM-DD).

    Pass *overpass_hour* to use the exact scene acquisition hour instead of
    the module-level default.
    """
    dh = {date: overpass_hour} if overpass_hour is not None else None
    result = fetch_weather_batch(lat, lon, [date], date_hour=dh)
    return result.get(date, dict(_EMPTY))


# ── Cross-date derived feature ────────────────────────────────────────────────

def freeze_thaw(w1: dict, w2: dict) -> int:
    """Return 1 if temperature crosses 0 °C between the two acquisition dates.

    Prefers overpass-hour temperature (``temp``); falls back to daily max
    (``temp_max``) for entries fetched before the hourly upgrade.
    """
    def _t(w: dict) -> float | None:
        v = w.get("temp")
        return v if v is not None else w.get("temp_max")

    t1, t2 = _t(w1), _t(w2)
    if t1 is None or t2 is None:
        return 0
    return int((t1 < 0) != (t2 < 0))
