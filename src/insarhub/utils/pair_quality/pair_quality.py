# -*- coding: utf-8 -*-
"""
PairQuality — compute interferogram pair quality scores for a folder.

Usage (Python API)
------------------
    from insarhub.utils.pair_quality import PairQuality

    pq = PairQuality("/data/bryce/p100_f466")
    result = pq.compute()
    # result.scores  -> {"scene_a:scene_b": 0.42, ...}
    # result.factors -> {"scene_a:scene_b": {"dt_days": 12, ...}, ...}
    # result.ndvi_source -> "sentinel2" | "modis" | "climatology" | "mixed"

Usage (API)
-----------
    GET /api/pair-quality?path=/data/bryce/p100_f466
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from insarhub.utils.pair_quality._cache import CacheManager
from insarhub.utils.pair_quality._feature_assembler import FeatureAssembler
from insarhub.utils.pair_quality import _classifier

logger = logging.getLogger(__name__)


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class QualityResult:
    scores:       dict[str, float]   # "ref:sec" -> 0–1
    factors:      dict[str, dict]    # "ref:sec" -> factor breakdown
    ndvi_source:  str                # dominant NDVI source across pairs
    snow_fetched: int                # number of dates fetched from remote APIs
    cached:       bool               # True if all data came from cache


# ── AOI helpers ───────────────────────────────────────────────────────────────

def _wkt_centroid(wkt: str) -> tuple[float, float]:
    coords = re.findall(r'(-?\d+\.?\d*)\s+(-?\d+\.?\d*)', wkt)
    if not coords:
        raise ValueError(f"No coordinates found in WKT: {wkt!r}")
    lons = [float(c[0]) for c in coords]
    lats = [float(c[1]) for c in coords]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def _load_aoi(folder: Path) -> tuple[float, float, str]:
    """Return (lat, lon, wkt) from downloader_config.json."""
    cfg_file = folder / "downloader_config.json"
    if not cfg_file.exists():
        raise FileNotFoundError(f"downloader_config.json not found in {folder}")
    cfg = json.loads(cfg_file.read_text())
    wkt = cfg.get("intersectsWith")
    if not wkt:
        raise ValueError("intersectsWith not set in downloader_config.json")
    lat, lon = _wkt_centroid(wkt)
    return lat, lon, wkt


# ── Pair / baseline loading ───────────────────────────────────────────────────

def _scene_date(name: str) -> str:
    """Extract ISO-8601 date from a Sentinel-1 scene name."""
    raw = name[17:25] if len(name) > 25 else ""
    if len(raw) == 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return ""


def _load_pairs(folder: Path) -> list[tuple[str, str, float, float]]:
    """Return list of (ref, sec, bperp_ref, bperp_sec) for the folder."""
    records: list[tuple[str, str, float, float]] = []
    for pairs_file in sorted(folder.glob("pairs_p*_f*.json")):
        key = pairs_file.stem.replace("pairs_", "")
        pairs: list = json.loads(pairs_file.read_text())

        bperp_map: dict[str, float] = {}
        bl_file = folder / f"baselines_{key}.json"
        if bl_file.exists():
            bperp_map = json.loads(bl_file.read_text())

        for pair in pairs:
            if len(pair) < 2:
                continue
            ref, sec = str(pair[0]), str(pair[1])
            records.append((
                ref, sec,
                float(bperp_map.get(ref, 0.0)),
                float(bperp_map.get(sec, 0.0)),
            ))
    return records


# ── Main class ────────────────────────────────────────────────────────────────

class PairQuality:
    """Compute pair quality scores for a single InSARHub folder."""

    def __init__(
        self,
        folder_path: str | Path,
        force_refresh: bool = False,
        weights: dict[str, float] | None = None,
        lc_aware: bool = True,
        coherence_aware: bool = True,
    ):
        self.folder           = Path(folder_path).expanduser().resolve()
        self.force_refresh    = force_refresh
        self.weights          = weights         # only used when lc_aware=False
        self.lc_aware         = lc_aware        # True = land-cover branching
        self.coherence_aware  = coherence_aware # True = use S1 global coherence dataset

    def compute(self, show_progress: bool = True) -> QualityResult:
        """Run the full quality computation and return a QualityResult.

        Parameters
        ----------
        show_progress : show tqdm progress bars on stderr (default True).
                        Set False when called from API/background threads.
        """
        try:
            from tqdm import tqdm
        except ImportError:
            tqdm = None

        def _tqdm(iterable, **kw):
            if tqdm and show_progress:
                return tqdm(iterable, **kw)
            return iterable

        pairs = _load_pairs(self.folder)
        if not pairs:
            return QualityResult(
                scores={}, factors={},
                ndvi_source="n/a", snow_fetched=0, cached=True,
            )

        try:
            lat, lon, wkt = _load_aoi(self.folder)
        except Exception as exc:
            logger.warning("Could not determine AOI: %s — using lat=45, lon=0", exc)
            lat, lon, wkt = 45.0, 0.0, "POLYGON ((0 45, 1 45, 1 46, 0 46, 0 45))"

        cache = CacheManager(self.folder, force_refresh=self.force_refresh)
        assembler = FeatureAssembler(
            cache=cache, aoi_wkt=wkt, lat=lat, lon=lon,
            skip_ndvi=self.coherence_aware,  # S3 COG is primary; skip slow NDVI fetch
        )

        # Batch-prefetch weather + snow for all unique acquisition dates upfront
        unique_dates: set[str] = set()
        for ref, sec, _, _ in pairs:
            d1, d2 = _scene_date(ref), _scene_date(sec)
            if d1: unique_dates.add(d1)
            if d2: unique_dates.add(d2)

        if tqdm and show_progress:
            tqdm.write(f"Prefetching weather/snow for {len(unique_dates)} dates …")
        assembler.prefetch_dates(list(unique_dates))

        # Prefetch S1 global coherence COH maps for all unique seasons
        if self.coherence_aware:
            if tqdm and show_progress:
                tqdm.write("Prefetching S1 coherence maps from S3 …")
            assembler.prefetch_coherence([
                (_scene_date(ref).replace("-", ""), _scene_date(sec).replace("-", ""))
                for ref, sec, _, _ in pairs
                if _scene_date(ref) and _scene_date(sec)
            ])

        cache.save()  # persist batch results before the pair loop

        scores:  dict[str, float] = {}
        factors: dict[str, dict]  = {}
        ndvi_sources: set[str]    = set()
        snow_fetched = 0

        mode = "S3 coherence" if self.coherence_aware else ("LC/NDVI" if self.lc_aware else "flat")
        for ref, sec, bperp_ref, bperp_sec in _tqdm(
            pairs,
            desc=f"Scoring pairs [{mode}]",
            unit="pair",
            total=len(pairs),
        ):
            date1 = _scene_date(ref)
            date2 = _scene_date(sec)
            if not date1 or not date2:
                continue

            fv = assembler.assemble(ref, sec, bperp_ref, bperp_sec, date1, date2)

            if self.coherence_aware:
                sc, fct = _classifier.coherence_score(fv)
            elif self.lc_aware:
                sc, fct = _classifier.lc_score(fv)
            else:
                sc, fct = _classifier.score(fv, weights=self.weights)
            pair_key          = f"{ref}:{sec}"
            scores[pair_key]  = sc
            factors[pair_key] = fct

            ndvi_sources.add(fv.get("ndvi_source", "climatology"))

        # Flush cache to disk
        cache.save()

        snow_fetched = assembler.remote_fetch_count

        # Determine dominant NDVI source
        if len(ndvi_sources) > 1:
            if "sentinel2" in ndvi_sources:
                ndvi_source = "sentinel2"
            elif "modis" in ndvi_sources:
                ndvi_source = "mixed"
            else:
                ndvi_source = "climatology"
        else:
            ndvi_source = next(iter(ndvi_sources), "climatology")

        all_cached = (snow_fetched == 0)
        return QualityResult(
            scores=scores,
            factors=factors,
            ndvi_source=ndvi_source,
            snow_fetched=snow_fetched,
            cached=all_cached,
        )

    def print_summary(self) -> None:
        """CLI helper — print a human-readable summary table."""
        result = self.compute()
        if not result.scores:
            print("No pairs found.")
            return

        print(f"\nPair quality summary  (NDVI source: {result.ndvi_source})")
        print(f"{'Pair':<60}  {'Score':>6}  {'dt':>5}  {'bperp':>6}  "
              f"{'season':>7}  {'veg':>5}  {'snow_d1':>7}  {'snow_d2':>7}")
        print("-" * 110)
        for key in sorted(result.scores, key=lambda k: result.scores[k], reverse=True):
            ref, _, sec = key.partition(":")
            label = f"{ref[17:25]}–{sec[17:25]}"
            sc    = result.scores[key]
            fct   = result.factors[key]
            bar   = "█" * int(sc * 20)
            print(
                f"  {label:<56}  {sc:6.3f}"
                f"  {fct.get('dt_days', 0):>5}d"
                f"  {fct.get('bperp_diff', 0):>5.0f}m"
                f"  {fct.get('season', 0):>7.2f}"
                f"  {fct.get('veg') or 0:>5.2f}"
                f"  {fct.get('snow_cover_d1') or 0:>7.2f}"
                f"  {fct.get('snow_cover_d2') or 0:>7.2f}"
                f"  {bar}"
            )
