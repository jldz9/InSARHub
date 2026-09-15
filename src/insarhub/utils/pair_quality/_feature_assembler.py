# -*- coding: utf-8 -*-
"""
Feature assembler — gathers the measurements one pair is judged on.

Two data sources, and only two
------------------------------
* **Open-Meteo ERA5 archive** (:mod:`_archive`) — temperature, soil
  temperature, soil moisture, rain, snow depth, snowfall, wind. One HTTP
  request covers an entire stack, and a second caller over the same AOI makes
  none.
* **S1 global coherence** (:mod:`_coherence`) — the seasonal decay model
  γ(t) = γ∞ + (γ0 − γ∞)·exp(−t/τ), read from windowed COG reads and cached.

WorldCover land cover, MODIS/Sentinel-2 NDVI, NSIDC MODIS snow cover, the
Copernicus DEM and FIRMS fire were all removed. They cost four more hosts and
three credential systems; NDVI was skipped by default anyway, MODIS snow cover
never populated the field the scorer read from it, and the DEM fed a terrain
term worth 0.04 of a weighted score that no longer exists.

The assembler does not decide anything. It produces a feature vector;
:mod:`_events` decides what in it counts as extreme and :mod:`_classifier`
records the verdict.
"""

from __future__ import annotations

import logging

from insarhub.utils.pair_quality import _archive, _baselines, _coherence, _weather
from insarhub.utils.pair_quality._cache import CacheManager, has_measurements
from insarhub.utils.pair_quality._geom import sample_grid_points

logger = logging.getLogger(__name__)


def _floor_at_ps(coh: dict) -> float | None:
    """Expected coherence floored at the permanent-scatterer level rho_inf."""
    expected = coh.get("coherence_expected")
    rho_inf  = coh.get("coherence_rho_inf")
    if expected is None and rho_inf is None:
        return None
    if expected is None:
        return round(float(rho_inf), 4)
    if rho_inf is None:
        return round(float(expected), 4)
    return round(max(float(rho_inf), float(expected)), 4)


class FeatureAssembler:
    """Assemble feature vectors for all pairs in one folder.

    Parameters
    ----------
    cache    : CacheManager for this folder
    aoi_wkt  : WKT polygon of the AOI — used for the coherence window and the
               native-resolution weather sample grid
    lat, lon : AOI centroid, the key for the weather disk cache and coherence
               lookup; weather itself is the mean over the sample grid
    """

    def __init__(self, cache: CacheManager, aoi_wkt: str, lat: float, lon: float):
        self._cache    = cache
        self._wkt      = aoi_wkt
        self._lat      = lat
        self._lon      = lon
        self._save_dir = self._cache._path.parent / "decay_maps"

        # Weather is sampled on the archive's native 0.1° grid across the AOI
        # and averaged, rather than read from the single centroid cell.  The
        # centroid still keys the disk cache and the coherence lookup.
        self._points = sample_grid_points(aoi_wkt)

        self._weather_cache: dict[str, dict] = {}
        self._date_hour:     dict[str, int]  = {}   # date → UTC overpass hour
        self.remote_fetch_count: int = 0

        # Dates the archive did not answer for. Non-empty means the events
        # below are missing environmental inputs, and callers should say so
        # rather than presenting the verdict as complete.
        self.missing_dates: set[str] = set()

        # Coherence cache: {cache_key → {level_str: coh_val}}
        self._coh_cache: dict = self._load_coh_cache()

    # ── Coherence cache ───────────────────────────────────────────────────────

    def _load_coh_cache(self) -> dict:
        """Load S1 coherence entries, dropping failures so they are retried."""
        cached = self._cache.get("s1_coherence", "map") or {}
        return {
            k: v for k, v in cached.items()
            if v is not None
            and not (isinstance(v, dict) and v.get("coherence_source") == "failed")
        }

    def _save_coh_cache(self) -> None:
        """Persist the coherence cache. Failures are never written to disk."""
        to_save = {
            k: v for k, v in self._coh_cache.items()
            if v is not None
            and not (isinstance(v, dict) and v.get("coherence_source") == "failed")
        }
        if to_save:
            self._cache.set("s1_coherence", "map", to_save)

    # ── Request accounting ────────────────────────────────────────────────────

    def _counting_requests(self, fn, *args, **kwargs):
        """Run *fn*, adding only requests that actually reached the network.

        The archive serves a date range it already holds without any request,
        so counting call sites instead of requests reported fetches that never
        happened — and made ``cached`` (derived from this count being zero)
        permanently false.
        """
        before = _archive.request_count
        try:
            return fn(*args, **kwargs)
        finally:
            self.remote_fetch_count += _archive.request_count - before

    # ── Weather ───────────────────────────────────────────────────────────────

    def _cache_key(self, date: str) -> str:
        return f"{self._lat:.3f}:{self._lon:.3f}:{date}"

    def get_weather(self, date: str) -> dict:
        """Return the measurement record for *date*, fetching only if needed."""
        if date in self._weather_cache:
            return self._weather_cache[date]

        cached = self._cache.get("weather", self._cache_key(date))
        if cached:
            self._weather_cache[date] = cached
            return cached

        # A date present in the batch result was answered by the archive; one
        # absent from it was not, and must not be cached. After prefetch_dates()
        # the archive already holds the stack's whole span, so this normally
        # costs no request at all.
        batch = self._counting_requests(
            _weather.fetch_weather_batch_points,
            self._points, [date],
            date_hour={date: self._date_hour[date]} if date in self._date_hour else None,
        )
        feats = batch.get(date)
        if not has_measurements(feats):
            self.missing_dates.add(date)
            return dict(_weather._EMPTY)      # neutral, deliberately not cached
        self._cache.set("weather", self._cache_key(date), feats)
        self._weather_cache[date] = feats
        return feats

    @property
    def weather_by_date(self) -> dict[str, dict]:
        """Everything fetched so far — the sample :func:`_events.calibrate` uses."""
        return dict(self._weather_cache)

    # ── Batch prefetch ────────────────────────────────────────────────────────

    def prefetch_dates(
        self,
        dates: list[str],
        date_hour: dict[str, int] | None = None,
    ) -> None:
        """Fetch every uncached date in one request, before the pair loop.

        Parameters
        ----------
        dates     : unique acquisition dates (YYYY-MM-DD)
        date_hour : {date: utc_hour} from scene names, so point-in-time
                    variables are read at the actual overpass rather than a
                    module default.
        """
        if date_hour:
            self._date_hour.update(date_hour)

        uncached: list[str] = []
        for date in dates:
            if date in self._weather_cache:
                continue
            cached = self._cache.get("weather", self._cache_key(date))
            if cached:
                self._weather_cache[date] = cached
            else:
                uncached.append(date)

        if not uncached:
            return

        logger.info("Fetching weather for %d date(s) over %d point(s) in one request …",
                    len(uncached), len(self._points))
        batch = self._counting_requests(
            _weather.fetch_weather_batch_points,
            self._points, uncached,
            date_hour=self._date_hour or None,
        )

        # Only entries carrying an actual measurement are cached. An all-None
        # entry reaches here when the archive answered for the range but had no
        # row for that date; the weather cache has no TTL, so storing it would
        # read back as a real "no snow, no rain" reading for the life of the
        # folder. Leaving it out costs nothing — the whole span is one request.
        for date, feats in batch.items():
            if not has_measurements(feats):
                continue
            self._weather_cache[date] = feats
            self._cache.set("weather", self._cache_key(date), feats)

        missing = [d for d in uncached if not has_measurements(batch.get(d))]
        if missing:
            self.missing_dates.update(missing)
            logger.warning(
                "Weather unavailable for %d of %d date(s) (%s%s) — pairs using "
                "them are judged without environmental events; re-run to retry",
                len(missing), len(uncached), ", ".join(sorted(missing)[:3]),
                ", …" if len(missing) > 3 else "",
            )

    def prefetch_coherence(self, date_pairs: list[tuple[str, str]]) -> None:
        """Warm the S1 coherence decay maps for every season the pairs touch."""
        try:
            _coherence.prefetch_coherence(
                self._wkt, self._lat, self._lon, date_pairs,
                pol="vv", cache=self._coh_cache,
            )
        except Exception as exc:
            logger.warning("S1 coherence prefetch failed: %s — pairs will be "
                           "judged on weather events only", exc)
        finally:
            self._save_coh_cache()

    # ── Per-pair assembly ─────────────────────────────────────────────────────

    def assemble(
        self,
        ref: str, sec: str,
        bperp_ref: float, bperp_sec: float,
        date1: str, date2: str,
    ) -> dict:
        """Return the feature vector for one pair."""
        bl = _baselines.extract(ref, sec, bperp_ref, bperp_sec)

        w1 = self.get_weather(date1)
        w2 = self.get_weather(date2)

        d1n = _coherence._normalize_date(date1)
        d2n = _coherence._normalize_date(date2)
        pair_key = f"s1coh_pair:{self._lat:.2f}:{self._lon:.2f}:{d1n}:{d2n}:vv"
        coh = self._coh_cache.get(pair_key) or _coherence.estimate_coherence(
            self._wkt, self._lat, self._lon, date1, date2,
            pol="vv", cache=self._coh_cache, save_dir=self._save_dir,
        )

        return {
            "ref": ref, "sec": sec,
            "date1": date1, "date2": date2,
            "aoi_wkt": self._wkt,

            # Geometry — context only, never flags a pair.
            "dt_days":    bl["dt_days"],
            "bperp_diff": bl["bperp_diff"],

            # The two acquisitions' measurements, whole. _events reads these.
            "weather_d1": w1,
            "weather_d2": w2,

            # S1 global coherence decay model at this pair's baseline.
            "coherence_expected":    coh.get("coherence_expected"),
            # Floored at the permanent-scatterer level rho_inf: even a fully
            # decorrelated distributed target keeps the PS fraction. This is
            # the value to compare against a MintPy minimum-coherence setting.
            "coherence_abs":         _floor_at_ps(coh),
            "coherence_source":      coh.get("coherence_source", "none"),
            "coherence_same_season": coh.get("coherence_same_season"),
            "coherence_season_d1":   coh.get("coherence_season_d1"),
            "coherence_season_d2":   coh.get("coherence_season_d2"),
        }
