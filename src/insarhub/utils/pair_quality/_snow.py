# -*- coding: utf-8 -*-
"""
Open-Meteo ERA5 snow / temperature fetcher.

Queries the free Open-Meteo ERA5 archive API — no API key required.
Returns snow depth, snowfall, and 2 m temperature for a given
(latitude, longitude, date) triple.

Keys returned
-------------
  temp       : float | None  °C  — overpass-hour 2 m temperature
  temp_max   : float | None  °C  — daily max (backward compat / fallback)
  temp_min   : float | None  °C  — daily min
  snowfall   : float | None  cm  — daily snowfall sum
  snow_depth : float | None  m   — overpass-hour snow depth (ERA5 hourly units)

The overpass hour is imported from _weather.OVERPASS_HOUR so both modules
stay in sync.  Override _weather.OVERPASS_HOUR before the first call.
"""

from __future__ import annotations

import urllib.request
import urllib.parse
import json
import logging

logger = logging.getLogger(__name__)

_OPENMETEO_URL = "https://archive-api.open-meteo.com/v1/archive"

# Hourly variables: sliced at OVERPASS_HOUR UTC.
_HOURLY_VARS = "temperature_2m,snow_depth"
# Daily variables: kept for daily max/min (backward compat) and snowfall sum.
_DAILY_VARS  = "snowfall_sum,temperature_2m_max,temperature_2m_min"

_EMPTY = {
    "temp":       None,   # overpass-hour °C
    "temp_max":   None,   # daily max °C  (backward compat)
    "temp_min":   None,   # daily min °C  (backward compat)
    "snowfall":   None,   # daily cm
    "snow_depth": None,   # overpass-hour m
}


def _overpass_hour() -> int:
    """Return the configured overpass hour from _weather (lazy import to avoid cycles)."""
    try:
        from insarhub.utils.pair_quality._weather import OVERPASS_HOUR
        return OVERPASS_HOUR
    except Exception:
        return 17


def _find_hourly_idx(hourly_times: list[str], date: str, hour: int) -> int | None:
    target = f"{date}T{hour:02d}:00"
    try:
        return hourly_times.index(target)
    except ValueError:
        return None


def fetch_snow_batch(
    lat: float,
    lon: float,
    dates: list[str],
    date_hour: dict[str, int] | None = None,
) -> dict[str, dict]:
    """Fetch snow/temperature for ALL dates in a single API call.

    Parameters
    ----------
    date_hour : per-date UTC overpass hour from scene name parsing.
                Falls back to OVERPASS_HOUR from _weather when missing.

    Returns dict mapping each date string → feature dict.
    Dates missing from the response get all-None values.
    """
    if not dates:
        return {}

    default_hour = _overpass_hour()
    _dh = date_hour or {}
    sorted_dates = sorted(dates)

    params = {
        "latitude":   f"{lat:.4f}",
        "longitude":  f"{lon:.4f}",
        "start_date": sorted_dates[0],
        "end_date":   sorted_dates[-1],
        "daily":      _DAILY_VARS,
        "hourly":     _HOURLY_VARS,
        "timezone":   "UTC",
    }
    url = _OPENMETEO_URL + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            payload = json.loads(resp.read())
    except Exception as exc:
        logger.warning("Snow batch fetch failed: %s", exc)
        return {d: dict(_EMPTY) for d in dates}

    daily  = payload.get("daily", {})
    hourly = payload.get("hourly", {})
    daily_times  = daily.get("time") or []
    hourly_times = hourly.get("time") or []

    result = {}
    for date in dates:
        r = dict(_EMPTY)

        # Daily slice
        try:
            di = daily_times.index(date)
        except ValueError:
            di = None

        if di is not None:
            def dv(key: str, i: int = di) -> object:
                vals = daily.get(key) or []
                return vals[i] if i < len(vals) else None
            r["temp_max"] = dv("temperature_2m_max")
            r["temp_min"] = dv("temperature_2m_min")
            r["snowfall"] = dv("snowfall_sum")

        # Hourly slice at this date's exact overpass hour
        hi = _find_hourly_idx(hourly_times, date, _dh.get(date, default_hour))
        if hi is not None:
            def hv(key: str, i: int = hi) -> object:
                vals = hourly.get(key) or []
                return vals[i] if i < len(vals) else None
            r["temp"]       = hv("temperature_2m")
            r["snow_depth"] = hv("snow_depth")

        result[date] = r

    return result


def fetch_snow(
    lat: float,
    lon: float,
    date: str,
    overpass_hour: int | None = None,
) -> dict:
    """Return snow / temperature dict for *date* (ISO-8601: YYYY-MM-DD).

    Pass *overpass_hour* to use the exact scene acquisition hour.
    """
    dh = {date: overpass_hour} if overpass_hour is not None else None
    result = fetch_snow_batch(lat, lon, [date], date_hour=dh)
    return result.get(date, dict(_EMPTY))
