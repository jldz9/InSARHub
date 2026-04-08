# -*- coding: utf-8 -*-
"""
Feature assembler — collects outputs from all extractors into a single
FeatureVector dict for one interferogram pair.

The assembler is the only place that coordinates calls to the individual
extractor modules.  It handles:
  - AOI-level features (geometry, landcover): fetched once per folder,
    cached in CacheManager, reused for every pair.
  - Date-level features (weather, snow): fetched once per unique date,
    cached, then looked up per pair.
  - Pair-level features (baselines, vegetation): computed per pair.

None values in the returned dict mean a feature was unavailable.  The
classifier in _classifier.py substitutes neutral values before scoring.
"""

from __future__ import annotations

import logging
from pathlib import Path

from insarhub.utils.pair_quality._cache import CacheManager, aoi_hash
from insarhub.utils.pair_quality import _baselines, _landcover
from insarhub.utils.pair_quality import _weather, _snow_modis, _veg
from insarhub.utils.pair_quality._ndvi import get_ndvi_batch
from insarhub.utils.pair_quality import _coherence

logger = logging.getLogger(__name__)


class FeatureAssembler:
    """Assemble feature vectors for all pairs in one folder.

    Parameters
    ----------
    cache        : CacheManager for this folder
    aoi_wkt      : WKT polygon of the AOI (used for geom + landcover)
    lat, lon     : AOI centroid coordinates
    ndvi_cache   : mutable dict passed through to _ndvi.get_ndvi
    """

    def __init__(
        self,
        cache: CacheManager,
        aoi_wkt: str,
        lat: float,
        lon: float,
        skip_ndvi: bool = False,
    ):
        self._cache     = cache
        self._wkt       = aoi_wkt
        self._lat       = lat
        self._lon       = lon
        self._skip_ndvi = skip_ndvi
        # AOI-level features — fetch once, reuse for all pairs
        self._lc_feats:   dict      = self._get_landcover()

        # Per-date caches built up as pairs are assembled
        self._weather_cache:  dict[str, dict] = {}
        self._snow_cache:     dict[str, dict] = {}
        self.remote_fetch_count: int = 0   # incremented on every network fetch

        # Coherence cache: {cache_key → {level_str: coh_val}}
        # Populated lazily / via prefetch_coherence()
        self._coh_cache: dict = self._load_coh_cache()

        # NDVI cache: {"ndvi:<lat>:<lon>:<date>": {"ndvi": float, "source": str}}
        # Persisted to CacheManager so expensive MODIS fetches survive across runs
        self._ndvi_cache: dict = self._load_ndvi_cache()

    # ── AOI-level helpers ─────────────────────────────────────────────────────

    def _load_coh_cache(self) -> dict:
        """Load S1 coherence entries from CacheManager into a flat dict."""
        cached = self._cache.get("s1_coherence", "map") or {}
        return dict(cached)

    def _save_coh_cache(self) -> None:
        """Persist the in-memory coherence cache back to CacheManager."""
        if self._coh_cache:
            self._cache.set("s1_coherence", "map", dict(self._coh_cache))

    def _load_ndvi_cache(self) -> dict:
        """Load NDVI entries from CacheManager into the in-memory dict."""
        cached = self._cache.get("ndvi", "map") or {}
        return dict(cached)

    def _save_ndvi_cache(self) -> None:
        """Persist the in-memory NDVI cache back to CacheManager."""
        if self._ndvi_cache:
            self._cache.set("ndvi", "map", dict(self._ndvi_cache))

    def _get_landcover(self) -> dict:
        key = aoi_hash(self._wkt)
        cached = self._cache.get("landcover", key)
        if cached:
            return cached
        feats = _landcover.extract(self._wkt)
        self._cache.set("landcover", key, feats)
        return feats

    # ── Date-level helpers ────────────────────────────────────────────────────

    def _get_weather(self, date: str) -> dict:
        if date in self._weather_cache:
            return self._weather_cache[date]
        cache_key = f"{self._lat:.3f}:{self._lon:.3f}:{date}"
        cached = self._cache.get("weather", cache_key)
        if cached:
            self._weather_cache[date] = cached
            return cached
        feats = _weather.fetch_weather(self._lat, self._lon, date)
        self._cache.set("weather", cache_key, feats)
        self._weather_cache[date] = feats
        self.remote_fetch_count += 1
        return feats

    def _get_snow(self, date: str) -> dict:
        if date in self._snow_cache:
            return self._snow_cache[date]
        cache_key = f"{self._lat:.3f}:{self._lon:.3f}:{date}"
        cached = self._cache.get("snow_modis", cache_key)
        if cached:
            self._snow_cache[date] = cached
            return cached
        feats = _snow_modis.fetch_snow_features(self._lat, self._lon, date)
        self._cache.set("snow_modis", cache_key, feats)
        self._snow_cache[date] = feats
        self.remote_fetch_count += 1
        return feats

    # ── Batch prefetch ────────────────────────────────────────────────────────

    def prefetch_dates(self, dates: list[str]) -> None:
        """Batch-fetch weather and snow for all unique dates not already cached.

        Call this once with all unique acquisition dates before the pair loop
        so that assemble() hits only the in-memory cache.
        """
        uncached_weather: list[str] = []
        uncached_snow:    list[str] = []

        for date in dates:
            cache_key = f"{self._lat:.3f}:{self._lon:.3f}:{date}"
            if date not in self._weather_cache:
                cached = self._cache.get("weather", cache_key)
                if cached:
                    self._weather_cache[date] = cached
                else:
                    uncached_weather.append(date)
            if date not in self._snow_cache:
                cached = self._cache.get("snow_modis", cache_key)
                if cached:
                    self._snow_cache[date] = cached
                else:
                    uncached_snow.append(date)

        if uncached_weather:
            logger.info("Batch-fetching weather for %d dates …", len(uncached_weather))
            batch = _weather.fetch_weather_batch(self._lat, self._lon, uncached_weather)
            for date, feats in batch.items():
                cache_key = f"{self._lat:.3f}:{self._lon:.3f}:{date}"
                self._cache.set("weather", cache_key, feats)
                self._weather_cache[date] = feats
            self.remote_fetch_count += 1

        if uncached_snow:
            logger.info("Batch-fetching snow for %d dates …", len(uncached_snow))
            batch = _snow_modis.fetch_snow_features_batch(self._lat, self._lon, uncached_snow)
            for date, feats in batch.items():
                cache_key = f"{self._lat:.3f}:{self._lon:.3f}:{date}"
                self._cache.set("snow_modis", cache_key, feats)
                self._snow_cache[date] = feats
            self.remote_fetch_count += 1

        # NDVI — skipped when coherence_aware=True (S3 COG is the primary signal;
        # NDVI is only needed as a fallback when S3 is unreachable, and in that
        # case the climatology table provides it without a network call).
        if not self._skip_ndvi:
            ndvi_uncached = [d for d in dates
                             if f"ndvi:{self._lat:.3f}:{self._lon:.3f}:{d}" not in self._ndvi_cache]
            if ndvi_uncached:
                logger.info("Batch-fetching MODIS NDVI for %d dates …", len(ndvi_uncached))
                get_ndvi_batch(self._lat, self._lon, ndvi_uncached, self._ndvi_cache)
                self._save_ndvi_cache()   # persist — NDVI is the slowest fetch
                self.remote_fetch_count += 1

    def prefetch_coherence(self, pairs: list[tuple[str, str]]) -> None:
        """Prefetch S1 global coherence COH maps for all unique seasons in pairs.

        Call this once with all (date1, date2) pairs before the pair loop.
        Populates self._coh_cache so assemble() never hits S3 per pair.

        Fetches both overall and per-LC-class COH maps so that the first pair
        in the scoring loop is a pure cache hit with no S3 latency.
        """
        needed: set[str] = set()
        for d1, d2 in pairs:
            for _, season in _coherence.split_by_season(d1, d2, self._lat):
                needed.add(season)

        if not needed:
            return

        dirty = False  # track whether we need to re-persist the cache

        # ── Overall COH maps ─────────────────────────────────────────────────
        seasons_to_fetch = [
            s for s in needed
            if f"s1coh:{self._lat:.2f}:{self._lon:.2f}:{s}:vv" not in self._coh_cache
        ]
        if seasons_to_fetch:
            logger.info("S1 coherence: fetching %d overall season(s) from S3 …",
                        len(seasons_to_fetch))
            for season in seasons_to_fetch:
                _coherence._fetch_season_coh_map(
                    self._wkt, self._lat, self._lon, season, "vv", self._coh_cache,
                )
                self.remote_fetch_count += 1
            dirty = True

        # ── Per-LC-class COH maps ────────────────────────────────────────────
        # These use a separate cache key (s1coh_cls:…) and are NOT fetched by
        # _fetch_season_coh_map.  Without pre-fetching, the first pair in the
        # scoring loop would block on a cold S3 + WorldCover read for each new
        # season, and the results would never be persisted to disk.
        cls_to_fetch = [
            s for s in needed
            if f"s1coh_cls:{self._lat:.2f}:{self._lon:.2f}:{s}:vv" not in self._coh_cache
        ]
        if cls_to_fetch:
            logger.info("S1 coherence: fetching %d per-class season(s) from S3 …",
                        len(cls_to_fetch))
            for season in cls_to_fetch:
                _coherence._fetch_season_coh_by_class(
                    self._wkt, self._lat, self._lon, season, "vv", self._coh_cache,
                )
                self.remote_fetch_count += 1
            dirty = True

        if dirty:
            self._save_coh_cache()

    # ── Public API ────────────────────────────────────────────────────────────

    def assemble(
        self,
        ref: str,
        sec: str,
        bperp_ref: float,
        bperp_sec: float,
        date1: str,
        date2: str,
    ) -> dict:
        """Return a flat FeatureVector dict for one (ref, sec) pair.

        Parameters
        ----------
        ref, sec      : Sentinel-1 scene names
        bperp_ref/sec : perpendicular baselines (m)
        date1, date2  : ISO-8601 acquisition dates
        """
        # 1. Baselines (always available, deterministic)
        bl = _baselines.extract(ref, sec, bperp_ref, bperp_sec)

        # 2. Date-level: weather + snow (DEM removed — slope_p90 weight 0.04 not worth ~10s fetch)
        w1 = self._get_weather(date1)
        w2 = self._get_weather(date2)
        s1 = self._get_snow(date1)
        s2 = self._get_snow(date2)

        # 3. Pair-level: vegetation
        # Skipped when skip_ndvi=True (coherence_aware mode): S3 coherence is the
        # primary signal, NDVI is only needed by lc_score() as a fallback.
        if self._skip_ndvi:
            veg = {
                "ndvi_d1": None, "ndvi_d2": None, "delta_ndvi": None,
                "ndvi_max": None, "growing_season": None,
                "veg_temporal": None, "ndvi_source": "skipped",
            }
        else:
            veg = _veg.get_veg_features(
                self._lat, self._lon,
                date1, date2,
                dt_normalized=bl["dt_normalized"],
                ndvi_cache=self._ndvi_cache,
            )

        # 4. Derived cross-features
        ft = _weather.freeze_thaw(w1, w2)
        delta_snow = _snow_modis.snow_cover_delta(s1, s2)

        # 5. Season crossing (from existing _scorer logic, inline here)
        season_pen = _season_penalty(date1, date2, self._lat)

        # 6. S1 global coherence estimate (uses in-memory cache from prefetch_coherence)
        coh_result = _coherence.estimate_coherence(
            self._wkt, self._lat, self._lon,
            date1, date2,
            pol="vv",
            cache=self._coh_cache,
        )

        fv: dict = {
            # Meta (used by lc_scorer for fire check)
            "date1":   date1,
            "date2":   date2,
            "aoi_wkt": self._wkt,

            # Baselines
            "dt_days":           bl["dt_days"],
            "bperp_diff":        bl["bperp_diff"],
            "dt_normalized":     bl["dt_normalized"],
            "bperp_normalized":  bl["bperp_normalized"],
            "is_annual_repeat":  bl["is_annual_repeat"],

            # Land cover (AOI-level)
            "lc_forest_fraction": self._lc_feats.get("lc_forest_fraction"),
            "lc_shrub_fraction":  self._lc_feats.get("lc_shrub_fraction"),
            "lc_grass_fraction":  self._lc_feats.get("lc_grass_fraction"),
            "lc_crop_fraction":   self._lc_feats.get("lc_crop_fraction"),
            "lc_urban_fraction":  self._lc_feats.get("lc_urban_fraction"),
            "lc_bare_fraction":   self._lc_feats.get("lc_bare_fraction"),
            "lc_snow_fraction":   self._lc_feats.get("lc_snow_fraction"),
            "lc_water_fraction":  self._lc_feats.get("lc_water_fraction"),
            "lc_dominant_class":  self._lc_feats.get("lc_dominant_class"),

            # Snow (pair-level, per-date)
            "snow_cover_frac_d1":  s1.get("snow_cover_frac"),
            "snow_cover_frac_d2":  s2.get("snow_cover_frac"),
            "delta_snow_cover":    delta_snow,
            "glacier_fraction":    s1.get("glacier_fraction"),  # AOI-level proxy
            "snow_depth_d1":       s1.get("snow_depth"),
            "snow_depth_d2":       s2.get("snow_depth"),
            "snow_source":         s1.get("snow_source", "none"),

            # Weather (pair-level, per-date)
            "temp_max_d1":     w1.get("temp_max"),
            "temp_max_d2":     w2.get("temp_max"),
            "precip_d1":       w1.get("precip"),
            "precip_d2":       w2.get("precip"),
            "precip_7day_d1":  w1.get("precip_7day"),
            "precip_7day_d2":  w2.get("precip_7day"),
            "soil_moisture_d1": w1.get("soil_moisture"),
            "soil_moisture_d2": w2.get("soil_moisture"),
            "freeze_thaw":     ft,

            # Vegetation (pair-level)
            "ndvi_d1":          veg["ndvi_d1"],
            "ndvi_d2":          veg["ndvi_d2"],
            "delta_ndvi":       veg["delta_ndvi"],
            "ndvi_max":         veg["ndvi_max"],
            "growing_season":   veg["growing_season"],
            "veg_temporal":     veg["veg_temporal"],
            "ndvi_source":      veg["ndvi_source"],

            # Season
            "season_penalty":  season_pen,

            # S1 global coherence (replaces landcover+NDVI in coherence_score mode)
            "coherence_expected":    coh_result.get("coherence_expected"),
            "coherence_by_class":    coh_result.get("coherence_by_class", {}),
            "coherence_source":      coh_result.get("coherence_source", "s3"),
            "coherence_same_season": coh_result.get("coherence_same_season"),
            "coherence_season_d1":   coh_result.get("coherence_season_d1"),
            "coherence_season_d2":   coh_result.get("coherence_season_d2"),
            "coherence_segments":    coh_result.get("coherence_segments"),
            "coherence_dt_total":    coh_result.get("coherence_dt_total"),
            # Final safeline: climatology estimate (always available, no network needed)
            "coherence_climatology": _coherence._climatology_pair_coherence(
                self._lat, date1, date2,
            ),
        }

        return fv


# ── Season penalty (inlined to avoid circular import with _scorer.py) ─────────

_SEASON_NH = {12: "winter", 1: "winter",  2: "winter",
               3: "spring", 4: "spring",  5: "spring",
               6: "summer", 7: "summer",  8: "summer",
               9: "autumn", 10: "autumn", 11: "autumn"}

_ADJACENT = {
    frozenset({"winter", "spring"}), frozenset({"spring", "summer"}),
    frozenset({"summer", "autumn"}), frozenset({"autumn", "winter"}),
}


def _season(month: int, lat: float) -> str:
    if lat < 0:
        month = ((month - 1 + 6) % 12) + 1
    return _SEASON_NH[month]


def _season_penalty(date1: str, date2: str, lat: float) -> float:
    m1, m2 = int(date1[5:7]), int(date2[5:7])
    s1, s2 = _season(m1, lat), _season(m2, lat)
    if s1 == s2:
        return 0.0
    pair = frozenset({s1, s2})
    if pair in _ADJACENT:
        return 0.35
    if "winter" in pair:
        return 0.95
    return 0.70
