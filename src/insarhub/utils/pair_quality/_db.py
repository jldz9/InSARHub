# -*- coding: utf-8 -*-
"""
PairQualityDB — precompute a healthy/concern verdict for every scene combination.

Stored in <folder>/.insarhub_pair_quality_db.json so manual pair selection
(e.g. NetworkEditor in the web UI) can look a pair up instantly without any
network call.

File structure
--------------
{
  "_schema_version": 4,
  "_built_at":       "ISO-8601",
  "_n_scenes":       42,
  "_n_pairs":        861,
  "_complete":       true,
  "_missing_dates":  ["2021-03-04"],
  "_thresholds":     { "rain_3day_serious": 18.4, "calibrated": true, ... },
  "status":  { "scene_a:scene_b": "concern", ... },
  "factors": { "scene_a:scene_b": {"status": ..., "events": [...], ...}, ... }
}

Schema 2 replaced the 0-100 ``scores`` map with ``status``; schema 3 renamed the
flagged value from ``"risky"`` to ``"concern"``. Schema 4 demoted ``deep_snow``
from serious to minor — an unchanged dry pack does not cost coherence — so
verdicts written before it no longer mean the same thing. Older files are
discarded and rebuilt rather than migrated.

Usage
-----
    from insarhub.utils.pair_quality._db import PairQualityDB

    # Build in background after select_pairs():
    db = PairQualityDB(folder)
    thread = db.precompute_background(scenes_by_stack, bperp_by_stack)

    # Lookup from anywhere (e.g. API):
    score = PairQualityDB.lookup(folder, ref, sec)          # float | None
    scores = PairQualityDB.lookup_many(folder, pairs_list)  # {key: float}
"""

from __future__ import annotations

import itertools
import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from insarhub.utils.pair_quality._dates import scene_date, scene_hour

logger = logging.getLogger(__name__)

DB_FILE         = ".insarhub_pair_quality_db.json"
_SCHEMA_VERSION = 4


# ── Per-folder build serialisation ────────────────────────────────────────────
#
# Scoring a folder is expensive and chatty: it batch-fetches weather and snow,
# pulls S1 coherence rasters from S3, and writes decay-map GeoTIFFs. Two of
# those running on one folder at once (the background DB build launched by
# select-pairs, and /api/pair-quality computing on demand because the stack
# file has no scores yet) duplicated every remote request — which is what
# triggered Open-Meteo's "too many concurrent requests" — and had them writing
# the same cache file and the same GeoTIFFs. One builder per folder at a time.

_folder_locks: dict[str, threading.RLock] = {}
_folder_locks_guard = threading.Lock()
_building: set[str] = set()


def _folder_key(folder: Path | str) -> str:
    return str(Path(folder).expanduser().resolve())


def build_lock(folder: Path | str) -> threading.RLock:
    """Return the process-wide lock guarding scoring work for *folder*."""
    key = _folder_key(folder)
    with _folder_locks_guard:
        lock = _folder_locks.get(key)
        if lock is None:
            lock = _folder_locks[key] = threading.RLock()
        return lock


def is_building(folder: Path | str) -> bool:
    """True while a pair-quality build is in progress for *folder*."""
    with _folder_locks_guard:
        return _folder_key(folder) in _building


@contextmanager
def building(folder: Path | str):
    """Hold *folder*'s build lock and advertise the build for its duration."""
    key = _folder_key(folder)
    with build_lock(folder):
        with _folder_locks_guard:
            _building.add(key)
        try:
            yield
        finally:
            with _folder_locks_guard:
                _building.discard(key)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON via a temp file and os.replace.

    A crash or a concurrent reader partway through a plain write leaves
    truncated JSON, which _load_db discards — silently throwing away a build
    that can take minutes.
    """
    import os

    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _scene_date(name: str) -> str:
    """ISO date for a bare date id, SLC scene name, or BURST granule name."""
    return scene_date(name)


def _scene_hour(name: str) -> int | None:
    """UTC overpass hour for a bare date id, SLC scene name, or BURST name."""
    return scene_hour(name)


def _pair_key(ref: str, sec: str) -> str:
    """Always earliest-date scene first so lookups are order-independent."""
    d1, d2 = _scene_date(ref), _scene_date(sec)
    if d1 <= d2:
        return f"{ref}:{sec}"
    return f"{sec}:{ref}"


# ── Static lookup helpers ─────────────────────────────────────────────────────

def _load_db(folder: Path) -> dict | None:
    path = folder / DB_FILE
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        if raw.get("_schema_version") != _SCHEMA_VERSION:
            return None
        return raw
    except Exception as exc:
        logger.warning("Could not read pair quality DB: %s", exc)
        return None


# ── Main class ────────────────────────────────────────────────────────────────

class PairQualityDB:
    """Precomputed healthy/concern verdicts for a single InSARHub folder.

    The scoring-mode flags (``weights``, ``lc_aware``, ``coherence_aware``) are
    gone with the weighted score: there is one way to judge a pair now, and the
    land-cover and NDVI inputs the alternate modes needed are no longer fetched.
    """

    def __init__(self, folder_path: str | Path, force_refresh: bool = False):
        self.folder        = Path(folder_path).expanduser().resolve()
        self.force_refresh = force_refresh

    # ── Static lookup API ─────────────────────────────────────────────────────

    @staticmethod
    def exists(folder: str | Path) -> bool:
        return (Path(folder) / DB_FILE).exists()

    @staticmethod
    def status(folder: str | Path) -> dict:
        """Return DB metadata without loading scores."""
        db = _load_db(Path(folder))
        if db is None:
            return {"exists": False}
        return {
            "exists":    True,
            "n_scenes":  db.get("_n_scenes", 0),
            "n_pairs":   db.get("_n_pairs", 0),
            "built_at":  db.get("_built_at", ""),
            "complete":  db.get("_complete", False),
        }

    @staticmethod
    def lookup(folder: str | Path, ref: str, sec: str) -> str | None:
        """Return "healthy"/"concern" for one pair, or None if not in DB."""
        db = _load_db(Path(folder))
        if db is None:
            return None
        return db.get("status", {}).get(_pair_key(ref, sec))

    @staticmethod
    def lookup_factors(folder: str | Path, ref: str, sec: str) -> dict | None:
        """Return factor breakdown for one pair, or None if not in DB."""
        db = _load_db(Path(folder))
        if db is None:
            return None
        return db.get("factors", {}).get(_pair_key(ref, sec))

    @staticmethod
    def lookup_many(
        folder: str | Path,
        pairs: list[tuple[str, str]],
    ) -> dict[str, str]:
        """Return {key: "healthy"|"concern"} for all requested pairs found in DB."""
        db = _load_db(Path(folder))
        if db is None:
            return {}
        status_db = db.get("status", {})
        return {
            _pair_key(r, s): status_db[_pair_key(r, s)]
            for r, s in pairs
            if _pair_key(r, s) in status_db
        }

    # ── Build ─────────────────────────────────────────────────────────────────

    def build(
        self,
        scenes_by_stack: dict[tuple[int, int], list[str]],
        bperp_by_stack:  dict[tuple[int, int], dict[str, float]],
        progress_cb=None,
        show_progress: bool = True,
    ) -> None:
        """Judge all N*(N-1)/2 pairs healthy/concern and persist to disk.

        Parameters
        ----------
        scenes_by_stack : {(path, frame): [scene_name, ...]}
        bperp_by_stack  : {(path, frame): {scene_name: bperp_m}}
        progress_cb     : optional callable(done: int, total: int) — for API use
        show_progress   : show tqdm progress bars on stderr (default True)

        Serialised per folder: a second builder waits rather than duplicating
        the weather and S3 coherence fetches alongside the first.
        """
        with building(self.folder):
            self._build(scenes_by_stack, bperp_by_stack, progress_cb, show_progress)

    def _build(
        self,
        scenes_by_stack: dict[tuple[int, int], list[str]],
        bperp_by_stack:  dict[tuple[int, int], dict[str, float]],
        progress_cb=None,
        show_progress: bool = True,
    ) -> None:
        """Do the scoring. Call :meth:`build`, which holds the folder lock."""
        from insarhub.utils.pair_quality import _classifier
        from insarhub.utils.pair_quality._cache import CacheManager
        from insarhub.utils.pair_quality._feature_assembler import FeatureAssembler
        from insarhub.utils.pair_quality.pair_quality import _load_aoi

        try:
            from tqdm import tqdm as _tqdm_cls
        except ImportError:
            _tqdm_cls = None

        def _tqdm(iterable, **kw):
            if _tqdm_cls and show_progress:
                return _tqdm_cls(iterable, **kw)
            return iterable

        # No AOI means no weather location and no coherence window, so every
        # event would be evaluated somewhere else entirely. Refusing is the
        # only honest option — the previous fallback to lat=45/lon=0 scored
        # stacks against central-European weather behind a single log warning,
        # and /api/pair-quality already returns 400 for exactly this case.
        lat, lon, wkt = _load_aoi(self.folder)

        cache     = CacheManager(self.folder, force_refresh=self.force_refresh)
        assembler = FeatureAssembler(cache=cache, aoi_wkt=wkt, lat=lat, lon=lon)

        # Collect all unique scenes across all stacks
        all_scenes: list[str] = []
        for scene_list in scenes_by_stack.values():
            all_scenes.extend(scene_list)
        all_scenes = list(dict.fromkeys(all_scenes))  # deduplicate, preserve order

        # Build per-date overpass hour from scene names (exact UTC time in name)
        date_hour: dict[str, int] = {}
        for scene in all_scenes:
            d = _scene_date(scene)
            h = _scene_hour(scene)
            if d and h is not None:
                date_hour[d] = h

        # Prefetch date-level features (weather, snow, NDVI) for all unique dates
        unique_dates: list[str] = list(date_hour.keys()) or list({
            d for s in all_scenes for d in [_scene_date(s)] if d
        })
        if _tqdm_cls and show_progress:
            _tqdm_cls.write(f"Prefetching weather/snow for {len(unique_dates)} dates …")
        assembler.prefetch_dates(unique_dates, date_hour=date_hour or None)

        # Prefetch S1 global coherence COH maps for all seasons in the pair set
        all_date_pairs = [
                (_scene_date(r), _scene_date(s))
                for r, s, _, _ in (
                    (ref, sec, None, None)
                    for key, scene_list in scenes_by_stack.items()
                    for ref, sec in itertools.combinations(scene_list, 2)
                )
        ]
        if _tqdm_cls and show_progress:
            _tqdm_cls.write("Prefetching S1 coherence decay maps …")
        assembler.prefetch_coherence(
            [(d1, d2) for d1, d2 in all_date_pairs if d1 and d2]
        )

        cache.save()

        # Build flat bperp lookup from all stacks
        bperp_flat: dict[str, float] = {}
        for bp_map in bperp_by_stack.values():
            bperp_flat.update(bp_map)

        status_map: dict[str, str]  = {}
        factors:    dict[str, dict] = {}

        # Rain and soil moisture have no published C-band threshold, so they are
        # calibrated against this AOI's own record — see _events.calibrate().
        thresholds = _classifier.calibrate(assembler.weather_by_date)
        logger.info("Event thresholds for %s: %s", self.folder.name, thresholds.as_dict())

        # Generate all N*(N-1)/2 combinations within each stack
        all_pairs: list[tuple[str, str, float, float]] = []
        for key, scene_list in scenes_by_stack.items():
            for ref, sec in itertools.combinations(scene_list, 2):
                d1, d2 = _scene_date(ref), _scene_date(sec)
                if not d1 or not d2:
                    continue
                # Ensure chronological order
                if d1 > d2:
                    ref, sec, d1, d2 = sec, ref, d2, d1
                bperp_ref = bperp_flat.get(ref, 0.0)
                bperp_sec = bperp_flat.get(sec, 0.0)
                all_pairs.append((ref, sec, bperp_ref, bperp_sec))

        total = len(all_pairs)
        logger.info("PairQualityDB: scoring %d pairs for %d scenes", total, len(all_scenes))

        # Write a stub so the status endpoint returns exists=True, complete=False
        # while the background build is in progress (visible in the UI).
        stub: dict = {
            "_schema_version": _SCHEMA_VERSION,
            "_built_at":  datetime.now(timezone.utc).isoformat(),
            "_n_scenes":  len(all_scenes),
            "_n_pairs":   total,
            "_complete":  False,
        }
        _atomic_write_json(self.folder / DB_FILE, stub)

        for i, (ref, sec, bperp_ref, bperp_sec) in enumerate(_tqdm(
            all_pairs,
            desc="Judging pairs",
            unit="pair",
            total=total,
        )):
            d1, d2 = _scene_date(ref), _scene_date(sec)
            fv = assembler.assemble(ref, sec, bperp_ref, bperp_sec, d1, d2)
            st, fct = _classifier.classify(fv, thresholds)
            key = _pair_key(ref, sec)
            status_map[key] = st
            factors[key]    = fct
            if progress_cb:
                progress_cb(i + 1, total)

        cache.save()

        n_concern = sum(1 for v in status_map.values() if v == "concern")
        db: dict = {
            "_schema_version": _SCHEMA_VERSION,
            "_built_at":  datetime.now(timezone.utc).isoformat(),
            "_n_scenes":  len(all_scenes),
            "_n_pairs":   total,
            # Complete means every pair was judged on complete inputs. A date
            # the archive never answered for leaves its pairs judged without
            # environmental events, which is not the same as healthy.
            "_complete":  not assembler.missing_dates,
            "_missing_dates": sorted(assembler.missing_dates),
            "_thresholds":    thresholds.as_dict(),
            "_n_concern":       n_concern,
            "_scene_names":   sorted(all_scenes),
            "status":     status_map,
            "factors":    factors,
        }
        _atomic_write_json(self.folder / DB_FILE, db)
        logger.info("PairQualityDB: %d pairs judged (%d concern) → %s",
                    total, n_concern, self.folder / DB_FILE)

    def build_from_folder(self, progress_cb=None) -> None:
        """Load scenes from saved stack_p*_f*.json files and build DB.

        Requires that stack_p*_f*.json exists in the folder
        (written by insarhub downloader --select-pairs).
        """
        scenes_by_stack: dict[tuple[int, int], list[str]] = {}
        bperp_by_stack:  dict[tuple[int, int], dict[str, float]] = {}

        for stack_file in sorted(self.folder.glob("stack_p*_f*.json")):
            stem = stack_file.stem  # "stack_p100_f466" / "stack_p56_merged_f056_118970_IW2_..."
            parts = stem.split("_")
            try:
                path  = int(parts[1][1:])
                frame = int(parts[2][1:])
            except (IndexError, ValueError):
                # Non-integer frame (burst merge tag, e.g. merged_f056_118970_IW2):
                # group under path with a string label instead of dropping it.
                try:
                    path = int(parts[1][1:])
                except (IndexError, ValueError):
                    continue
                frame = parts[2] if len(parts) > 2 else "merged"
            key = (path, frame)
            data = json.loads(stack_file.read_text())
            scenes = data.get("scenes", [])
            # A burst stack file may still hold granule names from an older run;
            # fall back to the date pairs so the DB stays date-keyed.
            if scenes and not any(str(s).isdigit() for s in scenes):
                scenes = sorted({p for pair in data.get("pairs", []) for p in pair})
            scenes_by_stack[key] = scenes
            bperp_by_stack[key]  = data.get("baselines", {})

        if not scenes_by_stack:
            raise FileNotFoundError(
                f"No stack_p*_f*.json files found in {self.folder}. "
                "Run 'insarhub downloader --select-pairs' first."
            )

        self.build(scenes_by_stack, bperp_by_stack, progress_cb=progress_cb)

    # ── Background ────────────────────────────────────────────────────────────

    def precompute_background(
        self,
        scenes_by_stack: dict[tuple[int, int], list[str]],
        bperp_by_stack:  dict[tuple[int, int], dict[str, float]],
        on_done=None,
    ) -> threading.Thread:
        """Start background thread. Returns the thread (already started).

        Parameters
        ----------
        on_done : optional callable() invoked when build completes (or fails)
        """
        def _run():
            try:
                self.build(scenes_by_stack, bperp_by_stack)
            except Exception as exc:
                logger.error("PairQualityDB background build failed: %s", exc)
            finally:
                if on_done:
                    on_done()

        t = threading.Thread(target=_run, daemon=True, name="pair-quality-db")
        t.start()
        return t
