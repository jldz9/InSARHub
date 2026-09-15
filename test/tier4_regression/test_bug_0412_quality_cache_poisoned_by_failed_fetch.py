"""A failed weather/snow fetch was cached forever and read back as real data.

Fixed in:   0.4.2
Changelog:  "Fixed a failed weather/snow fetch being written to the quality
            cache as a full set of null readings."
Symptom:    One Open-Meteo timeout during pair selection, and the folder's
            pair-quality DB was written with `_complete: true` while every
            pair carried `temp_max: null`, `precip_3day: null` and zero snow,
            precipitation and freeze-thaw penalties. `snow_source` still
            reported `"openmeteo"`, so nothing in the output said the data
            was missing. The weather and snow cache sections have no TTL, so
            the nulls never expired and every later run read them back.
Root cause: On failure the batch fetchers returned `{date: dict(_EMPTY)}` --
            a dict of Nones, which is *truthy*. `seed_prefetch`'s `if feats:`
            guard and `prefetch_dates`' unconditional `cache.set()` both took
            it for data. A failure was indistinguishable from a genuine
            "no snow, no rain" reading.

The fix makes absence the signal: a date present in a batch result was
answered by the server and may be cached, a date absent from it was not.
That holds at both granularities -- a whole failed range, and a single date the
archive had no row for inside a range that otherwise succeeded.

Rewritten in 0.4.3
------------------
Pair quality now pulls from Open-Meteo and the S1 coherence dataset only, and
grades pairs by detected events rather than a weighted score. Three cases here
tested code that no longer exists and were re-pointed rather than deleted:

* the `_snow` / `_snow_modis` fetchers were folded into `_weather` -- there is
  now ONE fetcher, so "both fetchers must report failure the same way" became
  unrepresentable rather than untested.
* `seed_prefetch()` went with the `avoid_low_quality_days` filter that was its
  only producer. The guard it carried lives on in `_store_batch`, covered by
  `test_prefetch_dates_caches_nothing_when_the_fetch_fails` below.

The invariant itself is unchanged and still enforced: a failure is never
cached, at either granularity.
"""

from __future__ import annotations

import json

import pytest

BUG = {
    "id": "0412-quality-cache-poisoned-by-failed-fetch",
    "fixed_in": "0.4.2",
    "area": "pair_quality",
}

_DATES = ["2021-01-08", "2021-01-20", "2021-02-01"]
_LAT, _LON = 37.9726, -112.7392


@pytest.fixture(autouse=True)
def clean_archive():
    """No span carried between tests -- a cached range makes no request, so a
    stale one would decide these tests instead of the code under test."""
    from insarhub.utils.pair_quality import _archive

    _archive.clear()
    yield
    _archive.clear()
@pytest.fixture
def failing_http(monkeypatch):
    """Make every outbound pair-quality request fail, as a 429 storm would."""
    from insarhub.utils.pair_quality import _http

    def _boom(url, **kwargs):
        raise _http.FetchError("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(_http, "get_json", _boom)
    return _boom


# ── The fetcher reports failure instead of manufacturing nulls ───────────────

def test_weather_batch_returns_nothing_when_the_fetch_fails(failing_http):
    """The core of the bug: a failed range must not yield a reading per date.

    This is now the only fetcher — snow, rain, soil moisture, soil temperature
    and wind all come from this one call — so this single assertion covers
    what used to need three.
    """
    from insarhub.utils.pair_quality import _weather

    assert _weather.fetch_weather_batch(_LAT, _LON, _DATES) == {}, (
        "a failed fetch must return no dates -- returning an _EMPTY dict per "
        "date is what let callers cache the failure as data"
    )


def test_a_successful_fetch_still_returns_every_date(monkeypatch):
    """Guard against 'fixing' the above by always returning nothing."""
    from insarhub.utils.pair_quality import _weather

    monkeypatch.setattr(
        _weather, "_fetch_range",
        lambda lat, lon, start, end: (
            {"time": _DATES,
             "temperature_2m_max": [1.0, 2.0, 3.0],
             "temperature_2m_min": [-1.0, -2.0, -3.0],
             "precipitation_sum": [0.0, 5.0, 0.0],
             "snowfall_sum": [0.0, 0.0, 1.0],
             "snow_depth_max": [0.0, 0.0, 0.1],
             "et0_fao_evapotranspiration": [1.0, 1.0, 1.0]},
            {"time": [f"{d}T17:00" for d in _DATES],
             "temperature_2m": [0.5, 1.5, 2.5],
             "soil_moisture_0_to_7cm": [0.2, 0.3, 0.4]},
        ),
    )
    out = _weather.fetch_weather_batch(_LAT, _LON, _DATES)
    assert set(out) == set(_DATES)
    assert out["2021-01-20"]["precip"] == 5.0


# ── Nothing downstream mistakes a null reading for data ──────────────────────

def test_all_none_features_are_not_measurements():
    """The truthiness trap that made every guard downstream a no-op."""
    from insarhub.utils.pair_quality._cache import has_measurements

    empty = {"temp": None, "temp_max": None, "precip": None, "snow_depth": None}
    assert empty, "precondition: the failure dict is truthy, which is the trap"
    assert not has_measurements(empty)
    assert not has_measurements({})
    assert not has_measurements(None)
    assert has_measurements({"temp": None, "precip": 0.0})


def test_snow_source_alone_does_not_count_as_a_measurement():
    from insarhub.utils.pair_quality._cache import has_measurements

    assert not has_measurements({"snow_depth": None, "snow_source": "none"})
def test_prefetch_dates_caches_nothing_when_the_fetch_fails(tmp_path, failing_http, monkeypatch):
    """End to end: an outage must leave the folder's cache empty, not poisoned."""
    from insarhub.utils.pair_quality._cache import CacheManager
    from insarhub.utils.pair_quality._feature_assembler import FeatureAssembler

    monkeypatch.setattr(FeatureAssembler, "_load_coh_cache", lambda self: {})

    cache = CacheManager(tmp_path)
    assembler = FeatureAssembler(
        cache=cache, aoi_wkt="POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))",
        lat=_LAT, lon=_LON,
    )
    assembler.prefetch_dates(list(_DATES))
    cache.save()

    stored = json.loads((tmp_path / ".insarhub_quality_cache.json").read_text())
    assert not stored.get("weather"), "a failed fetch was written to the cache"
    assert assembler.missing_dates == set(_DATES), (
        "the assembler must record which dates it could not judge, so callers "
        "can report a degraded verdict instead of presenting it as complete"
    )


# ── Existing poisoned caches heal themselves ─────────────────────────────────

def test_loading_a_cache_discards_entries_with_no_data(tmp_path):
    """Folders scored during the outage must recover without manual deletion."""
    from insarhub.utils.pair_quality._cache import CACHE_FILE, _SCHEMA_VERSION, CacheManager

    (tmp_path / CACHE_FILE).write_text(json.dumps({
        "_schema_version": _SCHEMA_VERSION,
        "weather": {
            "37.973:-112.739:2021-01-08": {"temp": None, "temp_max": None, "precip": None},
            "37.973:-112.739:2021-01-20": {"temp": -4.0, "temp_max": -1.0, "precip": 2.0},
        },
        "snow_modis": {
            "37.973:-112.739:2021-01-08": {"snow_depth": None, "snow_source": "openmeteo"},
        },
        "s1_coherence": {"map": {"s1coh_pmaps:37.97:-112.74:winter:vv": {"shape": [2, 2]}}},
    }))

    cache = CacheManager(tmp_path)
    assert cache.get("weather", "37.973:-112.739:2021-01-08") is None
    assert cache.get("weather", "37.973:-112.739:2021-01-20")["precip"] == 2.0
    assert cache.get("snow_modis", "37.973:-112.739:2021-01-08") is None
    assert cache.get("s1_coherence", "map"), (
        "only weather/snow nulls are dropped -- coherence and NDVI results are "
        "expensive and must survive the cleanup"
    )


# ── The same trap one level down: a per-date gap in a successful response ────
#
# The original fix made a *total* failure return {}. But a request that
# succeeded while the archive simply had no row for one date still produced an
# all-None entry for it, which `prefetch_dates` cached unconditionally -- the
# identical bug at finer granularity, and just as permanent, since neither
# section has a TTL.

def _payload_missing(dates: list[str], drop: str) -> dict:
    """An archive response covering *dates* with *drop*'s row absent."""
    from insarhub.utils.pair_quality import _archive

    kept = [d for d in dates if d != drop]
    daily = {"time": kept}
    for var in _archive.DAILY_VARS:
        daily[var] = [1.0] * len(kept)

    times = [f"{d}T{h:02d}:00" for d in kept for h in range(24)]
    hourly = {"time": times}
    for var in _archive.HOURLY_VARS:
        hourly[var] = [2.0] * len(times)

    return {"daily": daily, "hourly": hourly}


@pytest.fixture
def archive_with_a_gap(monkeypatch):
    """The request succeeds, but the archive has no row for the middle date."""
    from insarhub.utils.pair_quality import _http

    monkeypatch.setattr(
        _http, "get_json",
        lambda url, **kwargs: _payload_missing(_DATES, _DATES[1]),
    )
    return _DATES[1]


def test_weather_omits_a_date_the_archive_had_no_row_for(archive_with_a_gap):
    from insarhub.utils.pair_quality import _weather

    out = _weather.fetch_weather_batch(_LAT, _LON, _DATES)

    assert archive_with_a_gap not in out, (
        "an unanswered date must be absent, not present with every field None "
        "-- absence is the only signal callers have that it is not a reading"
    )
    assert set(out) == {_DATES[0], _DATES[2]}, "answered dates must still arrive"
def test_prefetch_does_not_cache_a_date_the_archive_skipped(
    tmp_path, archive_with_a_gap, monkeypatch,
):
    """End to end: the gap date stays uncached and is reported as missing."""
    from insarhub.utils.pair_quality._cache import CacheManager
    from insarhub.utils.pair_quality._feature_assembler import FeatureAssembler

    monkeypatch.setattr(FeatureAssembler, "_load_coh_cache", lambda self: {})

    cache = CacheManager(tmp_path)
    assembler = FeatureAssembler(
        cache=cache, aoi_wkt="POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))",
        lat=_LAT, lon=_LON,
    )
    assembler.prefetch_dates(list(_DATES))
    cache.save()

    stored = json.loads((tmp_path / ".insarhub_quality_cache.json").read_text())
    gap_key = f"{_LAT:.3f}:{_LON:.3f}:{archive_with_a_gap}"
    assert gap_key not in stored.get("weather", {}), (
        "a date the archive never answered for was written to the cache, where "
        "it reads back forever as a real 'no snow, no rain' measurement"
    )

    # The answered dates are still cached -- the point is not to cache less.
    assert len(stored.get("weather", {})) == 2
    assert assembler.missing_dates == {archive_with_a_gap}
