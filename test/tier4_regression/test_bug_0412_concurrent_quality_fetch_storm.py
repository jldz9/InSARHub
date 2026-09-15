"""Three scorers hit Open-Meteo at once, got 429, and never retried.

Fixed in:   0.4.2
Changelog:  "Fixed pair-quality scoring stampeding rate-limited APIs."
Symptom:    Selecting pairs in the GUI logged repeated `Snow batch fetch
            failed` / `Weather batch fetch failed` warnings for one identical
            date range, first as read timeouts and then as
            `HTTP Error 429: Too Many Requests` -- the archive endpoint
            answers a burst with `{"reason": "Too many concurrent requests"}`.
            One run also lost a decay map to
            `Unable to open .../S1_coherence_decay_summer_vv.tif to obtain
            file list`.
Root cause: Three code paths scored the same folder concurrently and none knew
            about the others: the `avoid_low_quality_days` filter, the
            background `PairQualityDB` build, and `/api/pair-quality` falling
            to its slow path because the stack file had no scores *yet*. Every
            request went straight to `urlopen` with no retry, no backoff and no
            concurrency cap, and `_fetch_season_decay_maps` did a check-then-act
            `if not tif.exists()` from an eight-thread pool.

Requests now funnel through `_http` (capped, retried, de-duplicated) and
scoring a folder is serialised by a per-folder lock.

The other half of the fix is not making the requests at all. `_archive` is the
one client for the Open-Meteo archive: it asks for every variable the event
detector needs, over the whole stack span, in a single request, and keeps the
span so that any later caller wanting a date inside it makes no request. The
volume that overwhelmed the endpoint -- two modules x three callers x one
request each -- collapses to one.

Rewritten in 0.4.3
------------------
`_snow` merged into `_weather`, so "the two modules must share one request"
became structurally guaranteed rather than something to assert. The cases that
tested it now assert the property that still has teeth: one request must carry
every variable the events need, and the span must be identical no matter who
asks first. See the sibling file on cache poisoning for the same note.
"""

from __future__ import annotations

import threading
import urllib.error
from datetime import date as _date
from datetime import timedelta as _timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

BUG = {
    "id": "0412-concurrent-quality-fetch-storm",
    "fixed_in": "0.4.2",
    "area": "pair_quality",
}

URL = "https://archive-api.open-meteo.com/v1/archive?latitude=37.97"


@pytest.fixture(autouse=True)
def fast_and_clean(monkeypatch):
    """No real sleeping, and no memo carried between tests."""
    from insarhub.utils.pair_quality import _archive, _http

    monkeypatch.setattr(_http, "BACKOFF_BASE", 0.001)
    monkeypatch.setattr(_http, "BACKOFF_CAP", 0.002)
    _http.clear_cache()
    _http._semaphores.clear()
    _archive.clear()
    yield
    _http.clear_cache()
    _http._semaphores.clear()
    _archive.clear()


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(URL, code, "Too Many Requests", {}, None)


# ── Retry and backoff ────────────────────────────────────────────────────────

def test_a_429_is_retried_rather_than_abandoned(monkeypatch):
    """The core of the bug: one 429 used to cost a whole stack its weather."""
    from insarhub.utils.pair_quality import _http

    calls = []

    def _flaky(url, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise _http_error(429)
        return {"ok": True}

    monkeypatch.setattr(_http, "_request_once", _flaky)
    assert _http.get_json(URL) == {"ok": True}
    assert len(calls) == 3, "must keep trying across transient 429s"


def test_a_read_timeout_is_retried(monkeypatch):
    from insarhub.utils.pair_quality import _http

    calls = []

    def _flaky(url, timeout):
        calls.append(url)
        if len(calls) < 2:
            raise TimeoutError("The read operation timed out")
        return {"ok": True}

    monkeypatch.setattr(_http, "_request_once", _flaky)
    assert _http.get_json(URL) == {"ok": True}
    assert len(calls) == 2


def test_a_client_error_is_not_retried(monkeypatch):
    """Retrying a 400 just burns the rate limit that 429 was complaining about."""
    from insarhub.utils.pair_quality import _http

    calls = []

    def _bad_request(url, timeout):
        calls.append(url)
        raise _http_error(400)

    monkeypatch.setattr(_http, "_request_once", _bad_request)
    with pytest.raises(_http.FetchError):
        _http.get_json(URL)
    assert len(calls) == 1


def test_exhausting_retries_raises_rather_than_returning_empty_data(monkeypatch):
    from insarhub.utils.pair_quality import _http

    monkeypatch.setattr(_http, "MAX_ATTEMPTS", 2)
    monkeypatch.setattr(_http, "_request_once",
                        lambda url, timeout: (_ for _ in ()).throw(_http_error(429)))
    with pytest.raises(_http.FetchError):
        _http.get_json(URL)


# ── De-duplication and concurrency cap ───────────────────────────────────────

def test_identical_concurrent_requests_collapse_into_one_fetch(monkeypatch):
    """The three scorers asked for the same immutable ERA5 range at once."""
    from insarhub.utils.pair_quality import _http

    started = threading.Barrier(8, timeout=10)
    calls = []
    lock = threading.Lock()

    def _counted(url, timeout):
        with lock:
            calls.append(url)
        return {"ok": True}

    monkeypatch.setattr(_http, "_request_once", _counted)

    def _worker():
        started.wait()
        return _http.get_json(URL)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert len(calls) == 1, f"8 identical requests caused {len(calls)} fetches"


def test_different_urls_are_not_collapsed(monkeypatch):
    """Guard against 'fixing' de-duplication by caching one answer for all."""
    from insarhub.utils.pair_quality import _http

    monkeypatch.setattr(_http, "_request_once", lambda url, timeout: {"url": url})
    assert _http.get_json(URL + "&a=1")["url"].endswith("a=1")
    assert _http.get_json(URL + "&a=2")["url"].endswith("a=2")


def test_never_more_than_max_concurrent_requests_in_flight(monkeypatch):
    """Patches the transport only, so the real semaphore does the limiting."""
    from insarhub.utils.pair_quality import _http

    peak = 0
    live = 0
    lock = threading.Lock()

    class _FakeResponse:
        def __enter__(self):
            nonlocal peak, live
            with lock:
                live += 1
                peak = max(peak, live)
            threading.Event().wait(0.02)
            return self

        def __exit__(self, *exc):
            nonlocal live
            with lock:
                live -= 1
            return False

        def read(self):
            return b'{"ok": true}'

    monkeypatch.setattr(_http.urllib.request, "urlopen",
                        lambda url, timeout=None: _FakeResponse())

    threads = [threading.Thread(target=_http.get_json, args=(f"{URL}&i={i}",))
               for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert live == 0
    assert 0 < peak <= _http.MAX_CONCURRENT, (
        f"{peak} requests were in flight at once; the archive endpoint rejects "
        f"bursts with 429 long before any daily quota is reached"
    )


def test_a_failure_is_remembered_only_briefly(monkeypatch):
    """Repeat callers fail fast instead of each grinding a full retry chain."""
    from insarhub.utils.pair_quality import _http

    calls = []
    monkeypatch.setattr(_http, "MAX_ATTEMPTS", 1)

    def _always_429(url, timeout):
        calls.append(url)
        raise _http_error(429)

    monkeypatch.setattr(_http, "_request_once", _always_429)

    for _ in range(4):
        with pytest.raises(_http.FetchError):
            _http.get_json(URL)

    assert len(calls) == 1, "the negative memo must absorb repeat callers"
    assert _http.NEGATIVE_TTL <= 300, (
        "it must also be short -- the next run has to retry, or a transient "
        "outage becomes a permanent one"
    )


# ── Decay-map writes ─────────────────────────────────────────────────────────

def test_concurrent_decay_map_writes_do_not_collide(tmp_path):
    """Eight Phase-2 threads all found the TIF missing and raced into GDAL."""
    pytest.importorskip("rasterio")
    import numpy as np

    from insarhub.utils.pair_quality import _coherence

    maps = {
        "shape": (4, 4),
        "gamma_inf": np.full((4, 4), 0.25, dtype=np.float32),
        "gamma0": np.full((4, 4), 0.8, dtype=np.float32),
        "tau": np.full((4, 4), 20.0, dtype=np.float32),
        "valid": np.ones((4, 4), dtype=bool),
        "transform": [0.01, 0.0, -113.0, 0.0, -0.01, 38.3],
    }

    errors: list[str] = []
    ready = threading.Barrier(8, timeout=10)

    def _writer():
        ready.wait()
        try:
            _coherence._save_decay_maps_if_absent(maps, tmp_path, "summer", "vv")
        except Exception as exc:               # pragma: no cover - reported below
            errors.append(str(exc))

    threads = [threading.Thread(target=_writer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors, errors
    tif = _coherence._decay_maps_tif_path(tmp_path, "summer", "vv")
    assert tif.exists()

    import rasterio

    with rasterio.open(tif) as src:
        assert src.count == 3 and src.tags().get("season") == "summer"

    leftovers = [p.name for p in tmp_path.iterdir() if p.name != tif.name]
    assert not leftovers, f"temporary files were left behind: {leftovers}"


# ── Per-folder serialisation ─────────────────────────────────────────────────

def test_only_one_scorer_runs_against_a_folder_at_a_time(tmp_path):
    from insarhub.utils.pair_quality._db import building, is_building

    peak = 0
    live = 0
    lock = threading.Lock()
    ready = threading.Barrier(4, timeout=10)

    def _scorer():
        nonlocal peak, live
        ready.wait()
        with building(tmp_path):
            with lock:
                live += 1
                peak = max(peak, live)
            threading.Event().wait(0.02)
            with lock:
                live -= 1

    threads = [threading.Thread(target=_scorer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert peak == 1, (
        "two scorers on one folder duplicate every remote fetch and write the "
        "same cache file and decay maps at the same time"
    )
    assert not is_building(tmp_path), "the in-progress flag must be cleared"


def test_different_folders_are_not_serialised_against_each_other(tmp_path):
    from insarhub.utils.pair_quality._db import build_lock

    assert build_lock(tmp_path / "a") is not build_lock(tmp_path / "b")
    assert build_lock(tmp_path / "a") is build_lock(tmp_path / "a")


def test_the_in_progress_flag_clears_even_when_scoring_raises(tmp_path):
    from insarhub.utils.pair_quality._db import building, is_building

    with pytest.raises(RuntimeError):
        with building(tmp_path):
            raise RuntimeError("scoring blew up")
    assert not is_building(tmp_path), (
        "a stuck flag would make /api/pair-quality wait forever on a build "
        "that already died"
    )


# ── Bulk fetching: one request, not one per date and not one per module ──────
#
# These hold the request *volume* down, which is the half of the fix that stops
# the 429 happening rather than surviving it. They patch `_http.get_json`, so
# everything above it -- `_archive`'s span store, `_weather`, `_snow` -- is the
# real code, and every call that reaches the fake is a call that would have hit
# the network.

_LAT, _LON = 37.9726, -112.7392

# A 3-year stack's worth of acquisition dates, 12 days apart.
_STACK_DATES = [
    (_date(2021, 1, 8) + _timedelta(days=12 * i)).isoformat() for i in range(90)
]


def _archive_payload(start: str, end: str) -> dict:
    """An Open-Meteo archive response covering [start, end], merged variables."""
    from insarhub.utils.pair_quality import _archive

    days, day = [], _date.fromisoformat(start)
    last = _date.fromisoformat(end)
    while day <= last:
        days.append(day.isoformat())
        day += _timedelta(days=1)

    daily = {"time": days}
    for var in _archive.DAILY_VARS:
        daily[var] = [1.0] * len(days)

    times = [f"{d}T{h:02d}:00" for d in days for h in range(24)]
    hourly = {"time": times}
    for var in _archive.HOURLY_VARS:
        hourly[var] = [2.0] * len(times)

    return {"daily": daily, "hourly": hourly}


@pytest.fixture
def recorded_requests(monkeypatch):
    """Record every request that reaches the network, answering it for real."""
    from insarhub.utils.pair_quality import _http

    seen: list[tuple[str, str]] = []

    def _fake_get_json(url, **kwargs):
        query = parse_qs(urlsplit(url).query)
        start, end = query["start_date"][0], query["end_date"][0]
        seen.append((start, end))
        return _archive_payload(start, end)

    monkeypatch.setattr(_http, "get_json", _fake_get_json)
    return seen


def test_a_whole_stack_costs_one_request(recorded_requests):
    """90 acquisition dates, one request -- the range costs the same as a day."""
    from insarhub.utils.pair_quality import _weather

    out = _weather.fetch_weather_batch(_LAT, _LON, _STACK_DATES)

    assert set(out) == set(_STACK_DATES)
    assert len(recorded_requests) == 1, (
        f"{len(recorded_requests)} requests for one stack; the archive endpoint "
        f"charges per request, so a stack must be fetched as one range"
    )


def test_one_request_carries_every_variable_the_events_need(recorded_requests):
    """The quiet doubling was two modules asking the same host separately.

    `_weather` and `_snow` each built their own URL against the same archive
    for the same location and an almost identical variable list, so every
    scorer made two requests where one would do. They are one module now, and
    what has to stay true is that the single request carries everything — a
    variable dropped from the merged list silently becomes None for every pair
    rather than failing loudly.
    """
    from insarhub.utils.pair_quality import _archive, _weather

    out = _weather.fetch_weather_batch(_LAT, _LON, _STACK_DATES)

    assert len(recorded_requests) == 1
    feats = out[_STACK_DATES[0]]
    # Every field an event rule reads must be populated, not merely present.
    for field in ("temp", "soil_temp", "soil_moisture", "snow_depth",
                  "snowfall", "rain_3day", "precip_3day", "wind_gust"):
        assert feats.get(field) is not None, (
            f"{field} came back None -- the event that reads it can never fire"
        )
    # And each must be backed by a variable actually requested.
    for var in ("soil_temperature_0_to_7cm", "soil_moisture_0_to_7cm",
                "snow_depth", "temperature_2m"):
        assert var in _archive.HOURLY_VARS
    for var in ("rain_sum", "snowfall_sum", "wind_gusts_10m_max",
                "precipitation_sum"):
        assert var in _archive.DAILY_VARS


def test_repeat_callers_over_one_aoi_share_one_request(recorded_requests):
    """The DB build and the API scorer, one after another over one AOI."""
    from insarhub.utils.pair_quality import _weather

    for _ in range(3):
        assert _weather.fetch_weather_batch(_LAT, _LON, _STACK_DATES)

    assert len(recorded_requests) == 1, (
        f"three callers over one AOI made {len(recorded_requests)} requests; "
        f"ERA5 is immutable, so the second and third must be free"
    )


def test_a_date_inside_a_downloaded_span_is_never_re_fetched(recorded_requests):
    """The rule: data already held is not fetched again, at any granularity."""
    from insarhub.utils.pair_quality import _weather

    _weather.fetch_weather_batch(_LAT, _LON, _STACK_DATES)
    before = len(recorded_requests)

    for date in _STACK_DATES[::7]:
        assert _weather.fetch_weather(_LAT, _LON, date)["precip"] is not None

    assert len(recorded_requests) == before, (
        "single-date lookups re-fetched a range already downloaded"
    )


def test_an_overhanging_range_re_fetches_the_union_not_a_fragment(recorded_requests):
    """Widening keeps one span per AOI instead of a pile of adjacent pieces."""
    from insarhub.utils.pair_quality import _weather

    _weather.fetch_weather_batch(_LAT, _LON, ["2022-01-10", "2022-06-10"])
    _weather.fetch_weather_batch(_LAT, _LON, ["2021-03-01", "2022-03-01"])

    assert len(recorded_requests) == 2
    start, end = recorded_requests[-1]
    assert start <= "2021-02-23" and end >= "2022-06-10", (
        f"the second request fetched {start}..{end}; it must cover the union, "
        f"or the dates in between stay missing and get fetched again later"
    )

    # And now everything in that union is free.
    before = len(recorded_requests)
    _weather.fetch_weather_batch(_LAT, _LON, ["2021-09-09", "2022-05-05"])
    assert len(recorded_requests) == before


def test_a_failed_range_is_not_remembered_as_downloaded(monkeypatch):
    """A failure must leave the span store empty so the next run retries it."""
    from insarhub.utils.pair_quality import _archive, _http

    calls = []

    def _boom(url, **kwargs):
        calls.append(url)
        raise _http.FetchError("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(_http, "get_json", _boom)

    for _ in range(2):
        with pytest.raises(_http.FetchError):
            _archive.get_range(_LAT, _LON, "2021-01-01", "2021-12-31")

    assert len(calls) == 2, "a failed range must stay retryable"


def test_concurrent_callers_over_one_range_make_one_request(monkeypatch):
    """The real shape of the bug: three scorers starting at the same moment."""
    from insarhub.utils.pair_quality import _archive, _http

    calls = []
    lock = threading.Lock()
    ready = threading.Barrier(6, timeout=10)

    def _fake_get_json(url, **kwargs):
        query = parse_qs(urlsplit(url).query)
        with lock:
            calls.append(url)
        return _archive_payload(query["start_date"][0], query["end_date"][0])

    monkeypatch.setattr(_http, "get_json", _fake_get_json)

    def _worker():
        ready.wait()
        _archive.get_range(_LAT, _LON, "2021-01-01", "2021-12-31")

    threads = [threading.Thread(target=_worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert len(calls) == 1, f"6 simultaneous callers made {len(calls)} requests"


def test_open_meteo_is_held_to_one_request_at_a_time(monkeypatch):
    """The endpoint's complaint was *concurrent* requests, not their total."""
    from insarhub.utils.pair_quality import _http

    assert _http.HOST_LIMITS.get("archive-api.open-meteo.com") == 1

    peak = live = 0
    lock = threading.Lock()

    class _FakeResponse:
        def __enter__(self):
            nonlocal peak, live
            with lock:
                live += 1
                peak = max(peak, live)
            threading.Event().wait(0.02)
            return self

        def __exit__(self, *exc):
            nonlocal live
            with lock:
                live -= 1
            return False

        def read(self):
            return b'{"ok": true}'

    monkeypatch.setattr(_http.urllib.request, "urlopen",
                        lambda url, timeout=None: _FakeResponse())

    threads = [threading.Thread(target=_http.get_json, args=(f"{URL}&i={i}",))
               for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert peak == 1, f"{peak} archive requests were in flight at once"


def test_an_unrelated_host_is_not_queued_behind_open_meteo(monkeypatch):
    """The cap is per host -- S3 coherence must not wait on a weather request."""
    from insarhub.utils.pair_quality import _http

    slow = _http._host_semaphore("https://archive-api.open-meteo.com/v1/archive")
    other = _http._host_semaphore("https://sentinel1-coherence.s3.amazonaws.com/x")
    assert slow is not other

    slow.acquire()
    try:
        assert other.acquire(timeout=1), (
            "a request to another host blocked behind the Open-Meteo gate"
        )
        other.release()
    finally:
        slow.release()


def test_judging_a_stack_makes_one_request_and_says_so(tmp_path, recorded_requests, monkeypatch):
    """The assembler's whole run, end to end: prefetch, then per-pair lookups.

    `remote_fetch_count` is asserted too because `QualityResult.cached` is
    derived from it being zero. Counting call sites instead of requests made
    the count -- and so the flag -- report fetches that never happened.
    """
    from insarhub.utils.pair_quality._cache import CacheManager
    from insarhub.utils.pair_quality._feature_assembler import FeatureAssembler

    monkeypatch.setattr(FeatureAssembler, "_load_coh_cache", lambda self: {})

    assembler = FeatureAssembler(
        cache=CacheManager(tmp_path), aoi_wkt="POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))",
        lat=_LAT, lon=_LON,
    )
    assembler.prefetch_dates(list(_STACK_DATES))

    assert len(recorded_requests) == 1, (
        f"prefetching one stack made {len(recorded_requests)} requests"
    )
    assert assembler.remote_fetch_count == 1

    # Every per-pair lookup now comes from memory.
    for date in _STACK_DATES:
        assert assembler.get_weather(date)["precip"] is not None

    assert len(recorded_requests) == 1, "judging re-fetched dates it already had"
    assert assembler.remote_fetch_count == 1


def test_a_second_assembler_over_the_same_aoi_fetches_nothing(tmp_path, recorded_requests, monkeypatch):
    """The background DB build and the API scorer, each with its own cache."""
    from insarhub.utils.pair_quality._cache import CacheManager
    from insarhub.utils.pair_quality._feature_assembler import FeatureAssembler

    monkeypatch.setattr(FeatureAssembler, "_load_coh_cache", lambda self: {})

    def _score(folder):
        folder.mkdir(exist_ok=True)
        a = FeatureAssembler(
            cache=CacheManager(folder), aoi_wkt="POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))",
            lat=_LAT, lon=_LON,
        )
        a.prefetch_dates(list(_STACK_DATES))
        return a

    first  = _score(tmp_path / "build")
    second = _score(tmp_path / "api")

    assert len(recorded_requests) == 1, (
        f"two scorers over one AOI made {len(recorded_requests)} requests; the "
        f"span store is process-level precisely because they do not share a "
        f"CacheManager"
    )
    assert first.remote_fetch_count == 1
    assert second.remote_fetch_count == 0, (
        "the second scorer reported a remote fetch it never made"
    )


def test_rolling_window_padding_is_applied_in_exactly_one_place(recorded_requests):
    """Padding must live in the archive client, not in a caller.

    Rolling 3- and 7-day windows need history before the earliest acquisition.
    While that padding lived in `_weather` and not in `_snow`, the two asked
    for spans differing by exactly those six days, and whichever ran second
    overhung the other's and triggered a second request for a range already
    downloaded. Callers now pass the bare date range and `_archive.PAD_DAYS`
    widens it once, so asking twice for the same dates -- by any route -- can
    never produce two spans.
    """
    from insarhub.utils.pair_quality import _archive, _weather

    _weather.fetch_weather_batch(_LAT, _LON, _STACK_DATES)
    _archive.get_range(_LAT, _LON, _STACK_DATES[0], _STACK_DATES[-1])

    assert len(recorded_requests) == 1, (
        f"{len(recorded_requests)} requests for one date range: {recorded_requests}"
    )
    start, end = recorded_requests[0]
    assert start < _STACK_DATES[0], (
        "no history was prepended, so the earliest date's rolling windows are "
        "computed from a truncated series"
    )
    assert _archive.PAD_DAYS >= 6, "a 7-day rolling window needs 6 days of history"
