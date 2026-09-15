# -*- coding: utf-8 -*-
"""
One Open-Meteo ERA5 archive client for the whole pair-quality subsystem.

Why this exists
---------------
Several call sites wanted ERA5 data for the same AOI over the same acquisition
dates during a single pair-selection run — a pre-filter that has since been
removed, the background ``PairQualityDB`` build, and the on-demand
``/api/pair-quality`` scorer — and each of them asked *twice*, because
``_weather`` and ``_snow`` were separate modules issuing separate requests to
the same endpoint for an almost identical variable list.  Six requests for one
answer is what earned the ``429 {"reason": "Too many concurrent requests"}``.

Two rules follow from that, and this module exists to enforce both:

**Fetch in bulk.**  The archive endpoint charges per *request*, not per day,
so a range costs the same as a single date.  Every caller therefore goes
through one request covering the union of what anyone needs — both modules'
variables together (:data:`DAILY_VARS` / :data:`HOURLY_VARS`), spanning the
whole stack.  A 3-year stack is one request, not ninety, and not one hundred
and eighty.

**Never fetch the same data twice.**  ERA5 is immutable historical reanalysis:
a day's values do not change, so a day fetched once is a day fetched forever.
Each location keeps the single widening span it has already downloaded, and a
request that falls inside that span makes no network call at all.  A request
that overhangs it re-fetches the *union* — one request that also covers
everything in between, so the store converges on one span per AOI rather than
growing a pile of adjacent fragments.

The span cache is deliberately process-level rather than per-folder: the same
AOI scored through three different entry points is the case that hurt, and
those three share a process, not a ``CacheManager``.

Failures raise :class:`_http.FetchError` and leave the store untouched — a
failed range must stay un-cached so the next attempt retries it.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import urllib.parse
from collections import OrderedDict
from datetime import date as _date
from datetime import timedelta

from insarhub.utils.pair_quality import _http

logger = logging.getLogger(__name__)

URL = "https://archive-api.open-meteo.com/v1/archive"

# The union of what _weather and _snow consume, fetched together in one
# request.  Keep this a superset of both modules' needs: a variable missing
# here silently becomes None everywhere downstream.
#
#   _weather daily : temperature_2m_max/min, precipitation_sum, rain_sum,
#                    snowfall_sum, snow_depth_max, wind_gusts_10m_max, et0
#   _snow    daily : snowfall_sum, temperature_2m_max/min
DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    # Rain separated from total precipitation on purpose. `precipitation_sum`
    # is rain + snow water equivalent, so on a winter day it is mostly snow —
    # scoring it as "heavy rain" double-counts the same weather that the snow
    # events already flag. Measured at a Utah site in January: precipitation
    # peaked at 4.2 mm while rain peaked at 0.8 mm.
    "rain_sum",
    "snowfall_sum",
    "snow_depth_max",
    # Canopy motion between passes decorrelates vegetated scenes.
    "wind_gusts_10m_max",
    "et0_fao_evapotranspiration",
]
#   _weather hourly: temperature_2m, soil_temperature_0_to_7cm,
#                    soil_moisture_0_to_7cm
#   _snow    hourly: temperature_2m, snow_depth
HOURLY_VARS = [
    "temperature_2m",
    # Freeze/thaw is a property of the ground, not the air: soil permittivity
    # falls from ~15-25 to ~4-5 on freezing, and that dielectric change is what
    # decorrelates. Air temperature is a poor stand-in — sampled at 13:00 UTC
    # over 86 consecutive overpasses at a Utah site, air and soil disagreed on
    # frozen/thawed state on 59% of days, and air crossed 0 degC 24 times
    # against the soil's 13.
    "soil_temperature_0_to_7cm",
    "soil_moisture_0_to_7cm",
    "snow_depth",
]

# How many AOIs to keep spans for.  One run works a single AOI; a long-lived
# GUI process walks a handful of folders.  Each entry is a few MB of floats.
MAX_LOCATIONS: int = 4

# Never widen a span beyond this.  Two runs on wildly separate epochs should
# not turn into one request for everything between them.
MAX_SPAN_DAYS: int = 366 * 15

# Days of history prepended to every request.  Two reasons, and the second is
# the load-bearing one:
#   * _weather's rolling precipitation windows need up to 6 days before the
#     earliest acquisition date, or the first date of a stack scores with a
#     truncated sum.
#   * applying it *here* rather than in _weather means _weather and _snow ask
#     for byte-identical spans.  When _weather padded and _snow did not,
#     whichever ran second overhung the other's span by these six days and
#     triggered a second request for a range already downloaded -- the exact
#     re-fetch this module exists to prevent, reintroduced by call order.
PAD_DAYS: int = 6


class _Span:
    """The contiguous range already downloaded for one location."""

    __slots__ = ("start", "end", "daily", "hourly")

    def __init__(self, start: str, end: str, daily: dict, hourly: dict):
        self.start = start
        self.end = end
        self.daily = daily
        self.hourly = hourly

    def covers(self, start: str, end: str) -> bool:
        return self.start <= start and end <= self.end


_spans: OrderedDict[str, _Span] = OrderedDict()
_spans_guard = threading.Lock()

_loc_locks: dict[str, threading.Lock] = {}
_loc_locks_guard = threading.Lock()

# Number of HTTP requests this module has actually issued.  Read by callers
# that report "remote fetches" and by the tests that hold this module to its
# one-request promise.
request_count: int = 0
_count_guard = threading.Lock()


def _key(lat: float, lon: float) -> str:
    """Location key at the precision the request itself uses."""
    return f"{lat:.4f}:{lon:.4f}"


def _loc_lock(key: str) -> threading.Lock:
    with _loc_locks_guard:
        lock = _loc_locks.get(key)
        if lock is None:
            lock = _loc_locks[key] = threading.Lock()
        return lock


def _span_days(start: str, end: str) -> int:
    return (_date.fromisoformat(end) - _date.fromisoformat(start)).days + 1


def clear() -> None:
    """Forget every downloaded span. For tests and force-refresh."""
    global request_count
    with _spans_guard:
        _spans.clear()
    with _count_guard:
        request_count = 0


def _fetch(lat: float, lon: float, start: str, end: str) -> tuple[dict, dict]:
    """One HTTP request for the merged variable set. Raises FetchError."""
    global request_count

    params = {
        "latitude":   f"{lat:.4f}",
        "longitude":  f"{lon:.4f}",
        "start_date": start,
        "end_date":   end,
        "daily":      ",".join(DAILY_VARS),
        "hourly":     ",".join(HOURLY_VARS),
        "timezone":   "UTC",
    }
    url = URL + "?" + urllib.parse.urlencode(params)
    logger.info("Open-Meteo archive: fetching %s → %s (%d days) in one request",
                start, end, _span_days(start, end))
    payload = _http.get_json(url, timeout=60)
    with _count_guard:
        request_count += 1
    return payload.get("daily", {}) or {}, payload.get("hourly", {}) or {}


def _points_key(points: list[tuple[float, float]]) -> str:
    """Stable span-store key for a sample point set."""
    joined = ";".join(f"{lat:.4f},{lon:.4f}" for lat, lon in points)
    return f"grid:{hashlib.sha1(joined.encode()).hexdigest()[:12]}"


def _fetch_points(
    points: list[tuple[float, float]], start: str, end: str
) -> list[dict]:
    """One request for every point. Returns the raw per-location payload list."""
    global request_count

    params = {
        "latitude":   ",".join(f"{lat:.4f}" for lat, _ in points),
        "longitude":  ",".join(f"{lon:.4f}" for _, lon in points),
        "start_date": start,
        "end_date":   end,
        "daily":      ",".join(DAILY_VARS),
        "hourly":     ",".join(HOURLY_VARS),
        "timezone":   "UTC",
    }
    url = URL + "?" + urllib.parse.urlencode(params)
    logger.info("Open-Meteo archive: fetching %s → %s (%d days, %d points) in one request",
                start, end, _span_days(start, end), len(points))
    payload = _http.get_json(url, timeout=90)
    with _count_guard:
        request_count += 1
    if isinstance(payload, dict):
        return [payload]
    return payload


def _mean_section(locations: list[dict], section: str) -> dict:
    """Element-wise mean of one daily/hourly section across locations.

    ``None`` values are skipped so a cell the model left blank does not drag
    the mean to zero. The time axis is identical for every location in one
    request, so the first present one is authoritative.
    """
    sections = [loc.get(section) or {} for loc in locations]
    times = next((s.get("time") for s in sections if s.get("time")), None)
    if not times:
        return {}

    out: dict = {"time": list(times)}
    variables = {k for s in sections for k in s if k != "time"}
    for var in variables:
        columns = [s.get(var) for s in sections if s.get(var)]
        if not columns:
            continue
        values: list = []
        for i in range(len(times)):
            total, count = 0.0, 0
            for column in columns:
                if i < len(column) and column[i] is not None:
                    total += column[i]
                    count += 1
            values.append(total / count if count else None)
        out[var] = values
    return out


def _average_locations(locations: list[dict]) -> tuple[dict, dict]:
    """Collapse per-location payloads into one mean location."""
    return _mean_section(locations, "daily"), _mean_section(locations, "hourly")


def _get_range_impl(key: str, start: str, end: str, fetch) -> tuple[dict, dict]:
    """Return ``(daily, hourly)`` for *key*, fetching ``fetch(start, end)`` if needed."""
    start = (_date.fromisoformat(start) - timedelta(days=PAD_DAYS)).isoformat()
    if end < start:
        raise ValueError(f"end {end!r} precedes start {start!r}")

    with _spans_guard:
        span = _spans.get(key)
        if span is not None and span.covers(start, end):
            _spans.move_to_end(key)
            return span.daily, span.hourly

    # Serialise per location so three callers asking at once make one request
    # rather than three identical ones.
    with _loc_lock(key):
        with _spans_guard:
            span = _spans.get(key)
            if span is not None and span.covers(start, end):
                _spans.move_to_end(key)
                return span.daily, span.hourly

        # Widen to the union so the span converges on one range per AOI
        # instead of a pile of adjacent fragments, each needing its own
        # request. Skip the widening if it would balloon the range.
        fetch_start, fetch_end = start, end
        if span is not None:
            union_start = min(span.start, start)
            union_end   = max(span.end, end)
            if _span_days(union_start, union_end) <= MAX_SPAN_DAYS:
                fetch_start, fetch_end = union_start, union_end

        daily, hourly = fetch(fetch_start, fetch_end)

        with _spans_guard:
            _spans[key] = _Span(fetch_start, fetch_end, daily, hourly)
            _spans.move_to_end(key)
            while len(_spans) > MAX_LOCATIONS:
                _spans.popitem(last=False)
        return daily, hourly


def get_range(lat: float, lon: float, start: str, end: str) -> tuple[dict, dict]:
    """Return ``(daily, hourly)`` arrays covering ``[start, end]`` for one point.

    Both are the raw Open-Meteo dicts — ``{"time": [...], "<var>": [...]}`` —
    so callers slice a date by indexing ``daily["time"]`` as they always have.
    The arrays may span *more* than was asked for; that is the point.

    ``PAD_DAYS`` of history is prepended for every caller, so two callers
    asking about the same dates ask for the same span.

    Raises
    ------
    _http.FetchError
        Nothing is cached when this happens, so the next call retries.
    """
    return _get_range_impl(_key(lat, lon), start, end, lambda s, e: _fetch(lat, lon, s, e))


def get_range_points(
    points: list[tuple[float, float]], start: str, end: str
) -> tuple[dict, dict]:
    """Return the AOI-mean ``(daily, hourly)`` for a set of sample points.

    All points travel in one request and are averaged per variable per
    timestep. The result has the same shape as :func:`get_range`, so every
    downstream slicer is unchanged. A single point delegates to :func:`get_range`.
    """
    if not points:
        raise ValueError("get_range_points requires at least one point")
    if len(points) == 1:
        lat, lon = points[0]
        return get_range(lat, lon, start, end)

    return _get_range_impl(
        _points_key(points), start, end,
        lambda s, e: _average_locations(_fetch_points(points, s, e)),
    )
