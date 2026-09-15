"""Pair quality may reach exactly two hosts, and nothing else.

The subsystem used to pull from eight: Open-Meteo, the S1 global coherence
mosaics, ESA WorldCover, the Copernicus DEM, MODIS NDVI via ORNL, MODIS snow
via NSIDC, Sentinel-2 NDVI via CDSE, and FIRMS. Five of those needed
credentials that fail silently when absent — a folder would score against
whatever happened to be reachable, and nothing in the output said so. Two of
them (NDVI, fire) were also unreachable in the default configuration, so the
features they fed were permanently ``None`` while still appearing in the
weighted score as though they had been evaluated.

It is now Open-Meteo plus the S1 coherence mosaics. Both are anonymous, so a
pair-quality run needs no credentials at all and behaves the same on every
machine. That property is easy to lose one convenient import at a time, which
is what this file exists to prevent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[2] / "src" / "insarhub" / "utils" / "pair_quality"

ALLOWED_HOSTS = {
    "archive-api.open-meteo.com",
    "sentinel-1-global-coherence-earthbigdata.s3.us-west-2.amazonaws.com",
}

# Hosts in comments/docstrings that name a paper or a removed source are fine;
# only real request targets matter, so URLs are matched then filtered by
# whether the line looks like code.
_DOC_HOSTS = {"www.nature.com", "doi.org", "developers.google.com"}

_URL = re.compile(r"https?://([A-Za-z0-9._-]+)")


def _py_files() -> list[Path]:
    return sorted(p for p in PKG.glob("*.py"))


def test_the_package_exists_and_has_modules():
    assert _py_files(), f"no modules found under {PKG}"


def test_no_module_references_a_host_outside_the_allowed_two():
    offenders: dict[str, set[str]] = {}
    for path in _py_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            # Skip comments and doc prose — a citation is not a request.
            if stripped.startswith("#") or stripped.startswith("*"):
                continue
            for host in _URL.findall(line):
                if host in ALLOWED_HOSTS or host in _DOC_HOSTS:
                    continue
                offenders.setdefault(path.name, set()).add(host)
    assert not offenders, (
        f"pair quality must reach only {sorted(ALLOWED_HOSTS)}; found {offenders}. "
        f"Every added host is another credential that can fail silently and "
        f"another feature that reads as None without saying so."
    )


@pytest.mark.parametrize("module", [
    "_landcover",     # ESA WorldCover
    "_ndvi",          # MODIS NDVI via ORNL
    "_ndvi_cdse",     # Sentinel-2 NDVI via CDSE
    "_snow_modis",    # MODIS snow cover via NSIDC
    "_weather_era5",  # ERA5 via the CDS API
    "_lc_scorer",     # land-cover scorer, which carried the FIRMS fire check
    "_scorer",        # flat weighted scorer
    "_veg",           # NDVI-derived vegetation features
])
def test_removed_source_modules_stay_removed(module):
    assert not (PKG / f"{module}.py").exists(), (
        f"{module}.py is back. It was removed with its data source; "
        f"re-adding it re-adds a host and, for the credentialed ones, a "
        f"failure mode that produces None features without an error."
    )


def test_no_credential_machinery_remains():
    """No netrc, no API-key env vars. Both sources are anonymous."""
    offenders: dict[str, list[str]] = {}
    for path in _py_files():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for needle in ("netrc", "FIRMS_MAP_KEY", "cdsapi", ".cdsapirc",
                           "HTTPBasicAuthHandler", "HTTPPasswordMgr"):
                if needle in line:
                    offenders.setdefault(path.name, []).append(f"{i}: {needle}")
    assert not offenders, (
        f"credential handling found in pair quality: {offenders}. Both allowed "
        f"sources are anonymous; a credentialed source fails silently when the "
        f"credential is missing or unreadable."
    )


def test_the_archive_request_carries_every_variable_the_events_read():
    """A variable dropped from the merged list becomes None for every pair
    rather than failing, so the event that reads it silently stops firing."""
    from insarhub.utils.pair_quality import _archive

    required_hourly = {
        "temperature_2m",             # wet snow, freeze-thaw fallback
        "soil_temperature_0_to_7cm",  # freeze-thaw
        "soil_moisture_0_to_7cm",     # soil moisture change
        "snow_depth",                 # wet snow, deep snow
    }
    required_daily = {
        "rain_sum",                   # rain events (liquid only)
        "snowfall_sum",               # fresh snowfall
        "snow_depth_max",             # snow depth fallback
        "wind_gusts_10m_max",         # high wind
        "precipitation_sum",          # precip_3day context
    }
    assert required_hourly <= set(_archive.HOURLY_VARS)
    assert required_daily  <= set(_archive.DAILY_VARS)


def test_one_archive_request_serves_a_whole_stack(monkeypatch):
    """The rate-limit contract: a stack is one request, not one per date."""
    from datetime import date, timedelta

    from insarhub.utils.pair_quality import _archive, _http, _weather

    _archive.clear()
    calls: list[str] = []

    def _fake(url, **kwargs):
        from urllib.parse import parse_qs, urlsplit
        q = parse_qs(urlsplit(url).query)
        calls.append(url)
        d0, d1 = date.fromisoformat(q["start_date"][0]), date.fromisoformat(q["end_date"][0])
        days, d = [], d0
        while d <= d1:
            days.append(d.isoformat())
            d += timedelta(days=1)
        daily = {"time": days, **{v: [1.0] * len(days) for v in _archive.DAILY_VARS}}
        times = [f"{x}T{h:02d}:00" for x in days for h in range(24)]
        hourly = {"time": times, **{v: [1.0] * len(times) for v in _archive.HOURLY_VARS}}
        return {"daily": daily, "hourly": hourly}

    monkeypatch.setattr(_http, "get_json", _fake)
    try:
        dates = [(date(2021, 1, 5) + timedelta(days=12 * i)).isoformat() for i in range(60)]
        out = _weather.fetch_weather_batch(37.97, -112.74, dates)
        assert set(out) == set(dates)
        assert len(calls) == 1, f"60 dates cost {len(calls)} requests"
    finally:
        _archive.clear()


# ── The avoid_low_quality_days filter stays removed ──────────────────────────
#
# It dropped whole acquisitions before the network was built, on thresholds
# that could not fire: `snow_cover_frac` was never populated by the batch
# fetch, so the two snow criteria were dead and `snow_threshold` had no effect
# at any usable value — while `snow_threshold=0.0` flagged every date, because
# `0.0 >= 0.0`. Weather now informs the per-pair verdict instead of silently
# deleting scenes, and the user keeps the choice.

_SRC = Path(__file__).resolve().parents[2] / "src" / "insarhub"


def _sources(*globs: str) -> list[Path]:
    out: list[Path] = []
    for g in globs:
        out.extend(p for p in _SRC.rglob(g) if "node_modules" not in p.parts)
    return sorted(out)


@pytest.mark.parametrize("token", [
    "avoid_low_quality_days",
    "avoidLowQualityDays",
    "snow_threshold",
    "precip_mm_threshold",
    "_bad_scene_names",
    "seed_prefetch",
])
def test_filter_token_is_gone_everywhere(token):
    hits = {
        str(p.relative_to(_SRC)): i
        for p in _sources("*.py", "*.ts", "*.tsx", "*.json")
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1)
        if token in line
    }
    assert not hits, (
        f"{token!r} still present in {hits} — the filter was removed from the "
        f"CLI, the GUI and the Python API together, so a surviving reference "
        f"is a half-removal that will read as a supported option."
    )


def test_select_pairs_no_longer_accepts_the_filter():
    import inspect

    from insarhub.utils.tool import select_pairs

    params = inspect.signature(select_pairs).parameters
    for gone in ("avoid_low_quality_days", "snow_threshold", "precip_mm_threshold"):
        assert gone not in params


def test_select_pairs_returns_three_values_not_four():
    """The fourth was the weather prefetch the filter produced."""
    import inspect

    from insarhub.utils.tool import select_pairs

    src = inspect.getsource(select_pairs)
    assert "return pairs, baseline_group, scene_bperp" in src
    assert "prefetch" not in src


def test_the_cli_exposes_no_filter_flag():
    from insarhub.cli.main import create_parser

    text = create_parser().format_help()
    for flag in ("--avoid-low-quality-days", "--no-avoid-low-quality-days",
                 "--snow-threshold", "--precip-mm-threshold"):
        assert flag not in text, f"{flag} is still offered by the CLI"


def test_no_module_imports_a_deleted_sibling():
    """A dead import inside `try: ... except ImportError` degrades silently.

    `_coherence` kept importing `_landcover` after that module was deleted; the
    try/except swallowed it and a whole per-land-cover-class code path returned
    empty forever without a word in the log. Grep-for-hosts does not catch that
    shape, so check imports directly.
    """
    import re

    existing = {p.stem for p in PKG.glob("*.py")}
    offenders: dict[str, set[str]] = {}
    pattern = re.compile(r"(?:from\s+insarhub\.utils\.pair_quality(?:\s+import\s+|\.))(_\w+)")
    for path in _py_files():
        for match in pattern.findall(path.read_text(encoding="utf-8")):
            if match not in existing:
                offenders.setdefault(path.name, set()).add(match)
    assert not offenders, f"imports of deleted modules: {offenders}"


def test_the_cache_declares_only_sections_that_are_still_written():
    """A TTL for a section nothing writes is a claim about data that is gone."""
    from insarhub.utils.pair_quality import _cache

    live = {"weather", "s1_coherence"}
    assert set(_cache._TTL_DAYS) <= live, (
        f"_TTL_DAYS mentions sections no longer written: "
        f"{set(_cache._TTL_DAYS) - live}"
    )


# ── AOI sampling at the archive's native resolution ──────────────────────────

def test_sample_grid_uses_the_native_9km_resolution():
    """One point per 0.1° ERA5-Land cell, not a fixed count inside the AOI."""
    from insarhub.utils.pair_quality._geom import (
        DEFAULT_GRID_SPACING_DEG, MAX_GRID_POINTS, sample_grid_points,
    )

    assert DEFAULT_GRID_SPACING_DEG == 0.1

    aoi = "POLYGON ((-113.0 37.7, -111.9 37.7, -111.9 38.4, -113.0 38.4, -113.0 37.7))"
    points = sample_grid_points(aoi)

    assert 1 < len(points) <= MAX_GRID_POINTS
    assert all(37.7 <= lat <= 38.4 and -113.0 <= lon <= -111.9 for lat, lon in points)
    lats = sorted({round(lat, 6) for lat, _ in points})
    assert 0.09 <= lats[1] - lats[0] <= 0.11, "grid spacing must be the native resolution"


def test_multi_point_fetch_is_one_request_and_a_mean(monkeypatch):
    """All AOI points travel in one call and are averaged per variable."""
    from insarhub.utils.pair_quality import _archive, _http

    _archive.clear()
    calls: list[str] = []

    def _fake(url, **kwargs):
        calls.append(url)

        def _location(scale: float) -> dict:
            return {
                "latitude": scale, "longitude": scale,
                "daily": {"time": ["2023-02-03"],
                          **{v: [scale * 2] for v in _archive.DAILY_VARS}},
                "hourly": {"time": ["2023-02-03T17:00"],
                           **{v: [scale * 2] for v in _archive.HOURLY_VARS}},
            }

        return [_location(1.0), _location(3.0)]

    monkeypatch.setattr(_http, "get_json", _fake)
    try:
        daily, hourly = _archive.get_range_points(
            [(38.0, -112.4), (38.1, -112.3)], "2023-02-03", "2023-02-03"
        )
        assert len(calls) == 1, "all AOI points must travel in one request"
        assert daily["snow_depth_max"][0] == 4.0, "mean of 2.0 and 6.0"
        assert hourly["snow_depth"][0] == 4.0
    finally:
        _archive.clear()
