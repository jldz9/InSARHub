"""Two centroid formulas gave one AOI two cache keys, so every date fetched twice.

Fixed in:   0.4.2
Changelog:  "Fixed the AOI centroid being computed two different ways, which
            gave the weather/snow cache two keys for one AOI."
Symptom:    A folder's `.insarhub_quality_cache.json` held two entries per
            acquisition date under two different `lat:lon` prefixes, ~9 km
            apart. Nothing seeded by pair selection was ever found by scoring,
            so every date was fetched from Open-Meteo twice per run -- half of
            the request volume behind the `429 Too Many Requests` storm.
Root cause: `tool._bad_scene_names` used shapely's true polygon centroid while
            `pair_quality._wkt_centroid` averaged the WKT vertices, counting a
            closed ring's repeated closing vertex twice. The cache key is
            `f"{lat:.3f}:{lon:.3f}:{date}"`, so the two never matched.

The centroid now has one definition, `_geom.wkt_centroid`.
"""

from __future__ import annotations

import re

BUG = {
    "id": "0412-aoi-centroid-cache-key-drift",
    "fixed_in": "0.4.2",
    "area": "pair_quality",
}

# The AOI from the report: a closed rectangle, so the ring repeats its first
# vertex and the old vertex-average was pulled toward that corner.
AOI = (
    "POLYGON ((-113.13686634498038 37.66879482828642, "
    "-112.34151046196189 37.66879482828642, "
    "-112.34151046196189 38.27647950177081, "
    "-113.13686634498038 38.27647950177081, "
    "-113.13686634498038 37.66879482828642))"
)


def _cache_key(lat: float, lon: float, date: str = "2021-01-08") -> str:
    """The weather/snow cache key, exactly as every call site builds it."""
    return f"{lat:.3f}:{lon:.3f}:{date}"


def _old_vertex_average(wkt: str) -> tuple[float, float]:
    """The pre-fix `_wkt_centroid`, kept here to prove the drift was real."""
    coords = re.findall(r'(-?\d+\.?\d*)\s+(-?\d+\.?\d*)', wkt)
    lons = [float(c[0]) for c in coords]
    lats = [float(c[1]) for c in coords]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def test_the_two_formulas_really_did_disagree():
    """Precondition: without this the rest of the file proves nothing."""
    from shapely import wkt as shapely_wkt

    true_centroid = shapely_wkt.loads(AOI).centroid
    old_lat, old_lon = _old_vertex_average(AOI)

    assert _cache_key(true_centroid.y, true_centroid.x) != _cache_key(old_lat, old_lon), (
        "the two formulas must differ at cache-key precision, or this bug "
        "could never have produced duplicate entries"
    )


def test_wkt_centroid_is_the_true_polygon_centroid():
    from shapely import wkt as shapely_wkt

    from insarhub.utils.pair_quality._geom import wkt_centroid

    expected = shapely_wkt.loads(AOI).centroid
    lat, lon = wkt_centroid(AOI)
    assert (round(lat, 9), round(lon, 9)) == (round(expected.y, 9), round(expected.x, 9))


def test_the_pair_quality_alias_delegates_rather_than_reimplementing():
    """One definition is the fix; a second copy would let them drift again."""
    from insarhub.utils.pair_quality._geom import wkt_centroid
    from insarhub.utils.pair_quality.pair_quality import _wkt_centroid

    assert _wkt_centroid(AOI) == wkt_centroid(AOI)


def test_both_call_sites_produce_one_cache_key():
    """The invariant that actually matters: seeding and scoring must collide."""
    from insarhub.utils.pair_quality._geom import wkt_centroid
    from insarhub.utils.pair_quality.pair_quality import _wkt_centroid

    seeded_lat, seeded_lon = wkt_centroid(AOI)        # tool._bad_scene_names
    scored_lat, scored_lon = _wkt_centroid(AOI)       # pair_quality._load_aoi

    assert _cache_key(seeded_lat, seeded_lon) == _cache_key(scored_lat, scored_lon)


def test_load_aoi_agrees_with_the_seeding_centroid(tmp_path):
    """Read the AOI back the way scoring does, from a real config file."""
    import json

    from insarhub.utils.pair_quality._geom import wkt_centroid
    from insarhub.utils.pair_quality.pair_quality import _load_aoi

    (tmp_path / "insarhub_config.json").write_text(json.dumps(
        {"downloader": {"type": "S1_SLC", "config": {"intersectsWith": AOI}}}
    ))

    lat, lon, wkt = _load_aoi(tmp_path)
    assert wkt == AOI
    assert _cache_key(lat, lon) == _cache_key(*wkt_centroid(AOI))


def test_fallback_ignores_a_rings_repeated_closing_vertex():
    """Without shapely the average must still not double-count the closing point."""
    import insarhub.utils.pair_quality._geom as geom

    square = "POLYGON ((0 0, 2 0, 2 2, 0 2, 0 0))"
    real_import = __import__

    def _no_shapely(name, *args, **kwargs):
        if name.startswith("shapely"):
            raise ImportError("shapely unavailable")
        return real_import(name, *args, **kwargs)

    import builtins

    builtins.__import__ = _no_shapely
    try:
        lat, lon = geom.wkt_centroid(square)
    finally:
        builtins.__import__ = real_import

    assert (lat, lon) == (1.0, 1.0), (
        "counting the repeated closing vertex would pull this to (0.8, 0.8)"
    )
