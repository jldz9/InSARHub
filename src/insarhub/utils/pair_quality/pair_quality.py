# -*- coding: utf-8 -*-
"""
PairQuality — judge every interferogram pair in a folder healthy or concern.

A pair is flagged ``concern`` when at least one *serious* extreme condition was
detected at either acquisition, or when the S1 decay model already predicts
unusable coherence at its temporal baseline. There is no score and no third label; see
:mod:`_events` for the event definitions and their sources.

Usage (Python API)
------------------
    from insarhub.utils.pair_quality import PairQuality

    pq = PairQuality("/data/bryce/p100_f466")
    result = pq.compute()
    # result.status  -> {"scene_a:scene_b": "concern", ...}
    # result.factors -> {"scene_a:scene_b": {"events": [...], ...}, ...}

Usage (API)
-----------
    GET /api/pair-quality?path=/data/bryce/p100_f466
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from insarhub.utils.pair_quality._cache import CacheManager
from insarhub.utils.pair_quality._feature_assembler import FeatureAssembler
from insarhub.utils.pair_quality import _classifier

logger = logging.getLogger(__name__)


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class QualityResult:
    """The verdict for every pair in a folder.

    There is no score. ``status`` is "healthy" or "concern", and ``factors``
    carries the events that decided it together with the measurements behind
    them — see :mod:`_events`.
    """

    status:        dict[str, str]    # "ref:sec" -> "healthy" | "concern"
    factors:       dict[str, dict]   # "ref:sec" -> events + observations
    remote_fetches: int              # HTTP requests that actually went out
    cached:        bool              # True when nothing had to be fetched
    missing_dates: list[str]         # dates the archive never answered for
    thresholds:    dict              # per-AOI thresholds actually applied


# ── AOI helpers ───────────────────────────────────────────────────────────────

def _wkt_centroid(wkt: str) -> tuple[float, float]:
    """Deprecated alias — use :func:`insarhub.utils.pair_quality._geom.wkt_centroid`.

    Kept so existing imports keep working; it must stay a thin delegation so
    the AOI centroid has exactly one definition (see the docstring there for
    why that matters to the cache).
    """
    from insarhub.utils.pair_quality._geom import wkt_centroid

    return wkt_centroid(wkt)


def _load_aoi(folder: Path) -> tuple[float, float, str]:
    """Return (lat, lon, wkt) from insarhub_config.json or downloader_config.json.

    Tries intersectsWith first (user-drawn AOI), then scene_footprint_wkt (derived
    from scene footprints during select_pairs when no AOI was drawn).
    """
    # Primary: insarhub_config.json (current format)
    insarhub_cfg = folder / "insarhub_config.json"
    if insarhub_cfg.exists():
        cfg = json.loads(insarhub_cfg.read_text())
        dl_cfg = cfg.get("downloader", {}).get("config", {})
        wkt = dl_cfg.get("intersectsWith") or dl_cfg.get("scene_footprint_wkt")
        if wkt:
            lat, lon = _wkt_centroid(wkt)
            return lat, lon, wkt

    # Fallback: legacy downloader_config.json
    cfg_file = folder / "downloader_config.json"
    if cfg_file.exists():
        cfg = json.loads(cfg_file.read_text())
        wkt = cfg.get("intersectsWith")
        if wkt:
            lat, lon = _wkt_centroid(wkt)
            return lat, lon, wkt

    raise FileNotFoundError(f"No AOI found in insarhub_config.json or downloader_config.json in {folder}")


# ── Pair / baseline loading ───────────────────────────────────────────────────

def _scene_date(name: str) -> str:
    """Extract ISO-8601 date from a bare date id, SLC scene, or BURST granule."""
    from insarhub.utils.pair_quality._dates import scene_date
    return scene_date(name)


def _load_pairs(folder: Path) -> list[tuple[str, str, float, float]]:
    """Return list of (ref, sec, bperp_ref, bperp_sec) for the folder."""
    records: list[tuple[str, str, float, float]] = []

    # Primary: unified stack_p*_f*.json (current format)
    for stack_file in sorted(folder.glob("stack_p*_f*.json")):
        data = json.loads(stack_file.read_text())
        pairs: list = data.get("pairs", [])
        bperp_map: dict[str, float] = data.get("baselines", {})
        for pair in pairs:
            if len(pair) < 2:
                continue
            ref, sec = str(pair[0]), str(pair[1])
            records.append((
                ref, sec,
                float(bperp_map.get(ref, 0.0)),
                float(bperp_map.get(sec, 0.0)),
            ))
    if records:
        return records

    # Fallback: legacy pairs_p*_f*.json + baselines_p*_f*.json
    for pairs_file in sorted(folder.glob("pairs_p*_f*.json")):
        key = pairs_file.stem.replace("pairs_", "")
        pairs = json.loads(pairs_file.read_text())

        bperp_map = {}
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
    """Judge every pair in a single InSARHub folder."""

    def __init__(self, folder_path: str | Path, force_refresh: bool = False):
        self.folder        = Path(folder_path).expanduser().resolve()
        self.force_refresh = force_refresh

    def compute(self, show_progress: bool = True) -> QualityResult:
        """Run the full quality computation and return a QualityResult.

        Parameters
        ----------
        show_progress : show tqdm progress bars on stderr (default True).
                        Set False when called from API/background threads.

        Shares the per-folder build lock with :meth:`PairQualityDB.build`, so
        this never runs alongside a background DB build of the same folder --
        they would otherwise duplicate every remote fetch and write the same
        cache file and decay-map GeoTIFFs at the same time.
        """
        from insarhub.utils.pair_quality._db import building

        if self.force_refresh:
            # Both layers hold successful payloads for the life of the process
            # precisely so nothing is fetched twice. force_refresh is the one
            # request to go back to the network, so it has to reach them too --
            # otherwise it only discards the on-disk cache and refills it from
            # the same in-memory copy.
            from insarhub.utils.pair_quality import _archive, _http

            _archive.clear()
            _http.clear_cache()

        with building(self.folder):
            return self._compute(show_progress=show_progress)

    def _compute(self, show_progress: bool = True) -> QualityResult:
        """Do the work. Call :meth:`compute`, which holds the folder lock."""
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
                status={}, factors={}, remote_fetches=0, cached=True,
                missing_dates=[], thresholds={},
            )

        try:
            lat, lon, wkt = _load_aoi(self.folder)
        except Exception as exc:
            logger.error("Cannot determine AOI for quality scoring: %s", exc)
            return QualityResult(
                status={}, factors={}, remote_fetches=0, cached=False,
                missing_dates=[], thresholds={},
            )

        cache = CacheManager(self.folder, force_refresh=self.force_refresh)
        assembler = FeatureAssembler(cache=cache, aoi_wkt=wkt, lat=lat, lon=lon)

        # One request for every acquisition date in the stack.
        unique_dates: set[str] = set()
        for ref, sec, _, _ in pairs:
            d1, d2 = _scene_date(ref), _scene_date(sec)
            if d1: unique_dates.add(d1)
            if d2: unique_dates.add(d2)

        if tqdm and show_progress:
            tqdm.write(f"Fetching weather for {len(unique_dates)} dates …")
        assembler.prefetch_dates(list(unique_dates))

        if tqdm and show_progress:
            tqdm.write("Prefetching S1 coherence decay maps …")
        assembler.prefetch_coherence([
            (_scene_date(ref).replace("-", ""), _scene_date(sec).replace("-", ""))
            for ref, sec, _, _ in pairs
            if _scene_date(ref) and _scene_date(sec)
        ])

        cache.save()  # persist batch results before the pair loop

        status_map: dict[str, str]  = {}
        factors:    dict[str, dict] = {}

        # Rain and soil moisture are calibrated against this AOI's own record
        # because neither has a published C-band threshold — _events.calibrate.
        thresholds = _classifier.calibrate(assembler.weather_by_date)

        for ref, sec, bperp_ref, bperp_sec in _tqdm(
            pairs,
            desc="Judging pairs",
            unit="pair",
            total=len(pairs),
        ):
            date1 = _scene_date(ref)
            date2 = _scene_date(sec)
            if not date1 or not date2:
                continue

            fv = assembler.assemble(ref, sec, bperp_ref, bperp_sec, date1, date2)
            st, fct = _classifier.classify(fv, thresholds)
            pair_key             = f"{ref}:{sec}"
            status_map[pair_key] = st
            factors[pair_key]    = fct

        cache.save()

        fetches = assembler.remote_fetch_count
        return QualityResult(
            status=status_map,
            factors=factors,
            remote_fetches=fetches,
            cached=(fetches == 0),
            missing_dates=sorted(assembler.missing_dates),
            thresholds=thresholds.as_dict(),
        )

    def print_summary(self) -> None:
        """CLI helper — print a human-readable summary table."""
        result = self.compute()
        if not result.status:
            print("No pairs found.")
            return

        concern = [k for k, v in result.status.items() if v == "concern"]
        print(f"\nPair quality — {len(result.status)} pairs, "
              f"{len(concern)} concern, {len(result.status) - len(concern)} healthy")
        if result.missing_dates:
            print(f"  ⚠ {len(result.missing_dates)} date(s) had no weather data: "
                  f"{', '.join(result.missing_dates[:5])}")
        print(f"{'Pair':<28}  {'Status':<8}  {'dt':>5}  {'γ':>5}  Events")
        print("-" * 100)

        from insarhub.utils.pair_quality._dates import scene_date_compact

        # Concern first, then by how much evidence there is.
        def _rank(key: str):
            fct = result.factors[key]
            return (0 if result.status[key] == "concern" else 1,
                    -len(fct.get("events", [])))

        for key in sorted(result.status, key=_rank):
            ref, _, sec = key.partition(":")
            fct    = result.factors[key]
            events = fct.get("events", [])
            coh    = fct.get("coherence_expected")
            label  = f"{scene_date_compact(ref)}-{scene_date_compact(sec)}"
            kinds  = ", ".join(
                e["kind"] + ("!" if e["severity"] == "serious" else "")
                for e in events
            ) or "-"
            print(
                f"  {label:<26}  {result.status[key]:<8}"
                f"  {fct.get('dt_days') or 0:>4}d"
                f"  {coh if coh is not None else float('nan'):>5.2f}"
                f"  {kinds}"
            )
        print("\n  ! = serious (sets the concern verdict)")

if __name__ == "__main__":
    from pathlib import Path
    p = Path("~/playground/p100_f466/").expanduser().resolve()
    pq = PairQuality(p, force_refresh=True)
    a = pq.compute(show_progress=True)
    pq.print_summary()
    
