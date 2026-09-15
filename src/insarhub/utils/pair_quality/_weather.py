# -*- coding: utf-8 -*-
"""
ERA5-Land weather feature extractor via Open-Meteo archive API.

Batch interface (preferred)
---------------------------
    fetch_weather_batch(lat, lon, dates) -> dict[date_str, dict]

Fetches the entire date range in a SINGLE API call, then slices per date.
A 3-year S1 stack (~90 dates) is one request, not ninety — and the same
request also carries _snow's variables, so the two modules cost one between
them.  See _archive, which owns the request and the span it downloaded.

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

import logging

from insarhub.utils.pair_quality import _archive

logger = logging.getLogger(__name__)

# Point-in-time hourly variables — sliced at OVERPASS_HOUR UTC.
_HOURLY_VARS = [
    "temperature_2m",
    "soil_temperature_0_to_7cm",
    "soil_moisture_0_to_7cm",
]

# Daily aggregate variables — kept for precip rolling sums, snowfall, et0,
# and as backward-compat fallback for temp (daily max/min).
_DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "rain_sum",
    "snowfall_sum",
    "snow_depth_max",
    "wind_gusts_10m_max",
    "et0_fao_evapotranspiration",
]

# UTC hour for overpass-hour slicing.
# S-1 descending ≈ 17, ascending ≈ 05.  Override before first call if needed.
OVERPASS_HOUR: int = 17

_EMPTY: dict = {
    "temp":          None,   # overpass-hour 2 m temperature (°C)
    "temp_max":      None,   # daily max °C — backward compat
    "temp_min":      None,   # daily min °C — backward compat
    "soil_temp":     None,   # overpass-hour 0–7 cm soil temperature (°C)
    "precip":        None,   # daily total precipitation, rain + SWE (mm)
    "rain":          None,   # daily liquid rain only (mm)
    "rain_3day":     None,   # 3-day rolling rain (mm)
    "precip_3day":   None,
    "precip_7day":   None,
    "snow_depth":    None,   # daily max snow depth (m)
    "snowfall":      None,   # daily snowfall (cm)
    "soil_moisture": None,   # overpass-hour 0–7 cm (m³/m³)
    "wind_gust":     None,   # daily max 10 m gust (km/h)
    "et0":           None,
}


# ── Low-level range fetch ─────────────────────────────────────────────────────

def _fetch_range(
    lat: float, lon: float, start: str, end: str
) -> tuple[dict[str, list], dict[str, list]]:
    """Return (daily_dict, hourly_dict) covering [start, end], keyed by variable.

    Goes through the shared archive client, which fetches ``_snow``'s variables
    in the same request as ours and serves a range it has already downloaded
    without touching the network.  The arrays may therefore cover more than
    was asked for — callers slice by date, so that is free.

    Raises ``_http.FetchError`` when the request fails after retries.
    """
    return _archive.get_range(lat, lon, start, end)


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

    # Rolling windows ending on this acquisition date.
    def _roll(series: list | None, days: int) -> float | None:
        if not series:
            return None
        window = [x for x in series[max(0, idx - days + 1): idx + 1] if x is not None]
        return round(sum(window), 2) if window else None

    precip_3day = _roll(all_precip, 3)
    precip_7day = _roll(all_precip, 7)
    rain_3day   = _roll(daily.get("rain_sum"), 3)

    return {
        "temp":          _hourly_val(hourly, date, "temperature_2m", overpass_hour),
        "temp_max":      v("temperature_2m_max"),
        "temp_min":      v("temperature_2m_min"),
        "soil_temp":     _hourly_val(hourly, date, "soil_temperature_0_to_7cm", overpass_hour),
        "precip":        v("precipitation_sum"),
        "rain":          v("rain_sum"),
        "rain_3day":     rain_3day,
        "precip_3day":   precip_3day,
        "precip_7day":   precip_7day,
        # Overpass-hour depth preferred over the daily max: wet-snow detection
        # pairs depth against temperature at the same instant, and a daily max
        # taken hours away from the pass can disagree with it.
        "snow_depth":    (_hourly_val(hourly, date, "snow_depth", overpass_hour)
                          if _hourly_val(hourly, date, "snow_depth", overpass_hour) is not None
                          else v("snow_depth_max")),
        "snowfall":      v("snowfall_sum"),
        "soil_moisture": _hourly_val(hourly, date, "soil_moisture_0_to_7cm", overpass_hour),
        "wind_gust":     v("wind_gusts_10m_max"),
        "et0":           v("et0_fao_evapotranspiration"),
    }


def _slice_dates(
    daily: dict,
    hourly: dict,
    dates: list[str],
    default_hour: int,
    date_hour: dict[str, int],
) -> dict[str, dict]:
    """Slice per-date feature dicts out of a fetched ``(daily, hourly)`` range.

    A date absent from ``daily["time"]`` was not answered and is omitted, so a
    caller can never mistake "the archive did not answer" for "no snow/rain".
    """
    all_precip = daily.get("precipitation_sum")
    answered = set(daily.get("time") or [])
    return {
        date: _extract_date(daily, hourly, date, all_precip, date_hour.get(date, default_hour))
        for date in dates
        if date in answered
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
    dict mapping each *answered* date string → weather feature dict.

    **A date absent from the result was not answered** — a total failure
    returns ``{}``, and a date the archive had no row for (future, API gap) is
    left out individually. Either way the contract is the same: a date present
    in the result came from the API and is safe to cache, a date absent from it
    must not be cached. Returning an _EMPTY dict per date instead is what let
    one timeout poison a folder's cache permanently, because the null values
    were indistinguishable from a real "no snow, no rain" reading.

    One request serves every date, and the shared archive client serves a
    range it already holds without any request at all.
    """
    if not dates:
        return {}

    default_hour = overpass_hour if overpass_hour is not None else OVERPASS_HOUR
    _dh = date_hour or {}

    sorted_dates = sorted(dates)
    fetch_start, fetch_end = sorted_dates[0], sorted_dates[-1]

    # The days of history that rolling-7-day precip needs before the earliest
    # date are prepended by _archive, for every caller alike — see PAD_DAYS.
    try:
        daily, hourly = _fetch_range(lat, lon, fetch_start, fetch_end)
    except Exception as exc:
        logger.warning("Weather batch fetch failed (%s → %s): %s — "
                       "pairs spanning these dates will be scored without "
                       "weather data", fetch_start, fetch_end, exc)
        return {}

    all_precip = daily.get("precipitation_sum")
    answered = set(daily.get("time") or [])
    return {
        date: _extract_date(daily, hourly, date, all_precip, _dh.get(date, default_hour))
        for date in dates
        if date in answered
    }


def fetch_weather_batch_points(
    points: list[tuple[float, float]],
    dates: list[str],
    overpass_hour: int | None = None,
    date_hour: dict[str, int] | None = None,
) -> dict[str, dict]:
    """Fetch the AOI-mean ERA5-Land features for all *dates* over *points*.

    Every sample point travels in a single archive request and the per-variable
    mean is taken across them, so an AOI is represented by its area rather than
    one centroid cell.  The contract matches :func:`fetch_weather_batch`: a date
    present in the result came from the archive; a date absent from it must not
    be cached.
    """
    if not dates:
        return {}

    default_hour = overpass_hour if overpass_hour is not None else OVERPASS_HOUR
    sorted_dates = sorted(dates)
    fetch_start, fetch_end = sorted_dates[0], sorted_dates[-1]

    try:
        daily, hourly = _archive.get_range_points(points, fetch_start, fetch_end)
    except Exception as exc:
        logger.warning("Weather batch fetch over %d point(s) failed (%s → %s): %s — "
                       "pairs spanning these dates will be scored without "
                       "weather data", len(points), fetch_start, fetch_end, exc)
        return {}

    return _slice_dates(daily, hourly, dates, default_hour, date_hour or {})


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
