# -*- coding: utf-8 -*-
"""
Open-Meteo ERA5 snow / temperature fetcher.

Queries the free Open-Meteo ERA5 archive API — no API key required.
Returns daily snow depth, snowfall, and 2 m temperature for a given
(latitude, longitude, date) triple.
"""

from __future__ import annotations

import urllib.request
import urllib.parse
import json
import logging

logger = logging.getLogger(__name__)

_OPENMETEO_URL = "https://archive-api.open-meteo.com/v1/archive"

_VARIABLES = ",".join([
    "snowfall_sum",
    "snow_depth_max",
    "temperature_2m_max",
    "temperature_2m_min",
])


def fetch_snow_batch(lat: float, lon: float, dates: list[str]) -> dict[str, dict]:
    """Fetch snow/temperature for ALL dates in a single API call.

    Returns dict mapping each date string → feature dict.
    Dates missing from the response get all-None values.
    """
    _EMPTY = {"snowfall": None, "snow_depth": None, "temp_max": None, "temp_min": None}
    if not dates:
        return {}
    sorted_dates = sorted(dates)
    params = {
        "latitude":   f"{lat:.4f}",
        "longitude":  f"{lon:.4f}",
        "start_date": sorted_dates[0],
        "end_date":   sorted_dates[-1],
        "daily":      _VARIABLES,
        "timezone":   "UTC",
    }
    url = _OPENMETEO_URL + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            payload = json.loads(resp.read())
    except Exception as exc:
        logger.warning("Snow batch fetch failed: %s", exc)
        return {d: dict(_EMPTY) for d in dates}

    daily = payload.get("daily", {})
    time_list = daily.get("time") or []

    result = {}
    for date in dates:
        try:
            idx = time_list.index(date)
        except ValueError:
            result[date] = dict(_EMPTY)
            continue

        def v(key: str, i: int = idx) -> object:
            vals = daily.get(key) or []
            return vals[i] if i < len(vals) else None

        result[date] = {
            "snowfall":   v("snowfall_sum"),
            "snow_depth": v("snow_depth_max"),
            "temp_max":   v("temperature_2m_max"),
            "temp_min":   v("temperature_2m_min"),
        }
    return result


def fetch_snow(lat: float, lon: float, date: str) -> dict:
    """Return snow / temperature dict for *date* (ISO-8601: YYYY-MM-DD).

    Keys returned (all may be None on missing data):
        snowfall    – total snowfall in cm
        snow_depth  – max snow depth in cm
        temp_max    – maximum 2 m air temperature in °C
        temp_min    – minimum 2 m air temperature in °C

    Raises on HTTP / network errors; caller should catch and treat as
    missing data.
    """
    params = {
        "latitude":   f"{lat:.4f}",
        "longitude":  f"{lon:.4f}",
        "start_date": date,
        "end_date":   date,
        "daily":      _VARIABLES,
        "timezone":   "UTC",
    }
    url = _OPENMETEO_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=20) as resp:
        payload = json.loads(resp.read())

    daily = payload.get("daily", {})

    def first(key: str):
        vals = daily.get(key) or []
        return vals[0] if vals else None

    return {
        "snowfall":   first("snowfall_sum"),
        "snow_depth": first("snow_depth_max"),
        "temp_max":   first("temperature_2m_max"),
        "temp_min":   first("temperature_2m_min"),
    }
