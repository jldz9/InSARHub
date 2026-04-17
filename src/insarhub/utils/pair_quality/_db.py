# -*- coding: utf-8 -*-
"""
PairQualityDB — precompute quality scores for ALL possible scene combinations.

Scores are stored in <folder>/.insarhub_pair_quality_db.json so that manual
pair selection (e.g. NetworkEditor in the web UI) can do instant lookups
without any network calls.

File structure
--------------
{
  "_schema_version": 1,
  "_built_at":       "ISO-8601",
  "_n_scenes":       42,
  "_n_pairs":        861,
  "_complete":       true,
  "scores":  { "scene_a:scene_b": 0.234, ... },
  "factors": { "scene_a:scene_b": { "dt_days": 12, "score": 0.234, ... }, ... }
}

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
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB_FILE         = ".insarhub_pair_quality_db.json"
_SCHEMA_VERSION = 1


# ── Helpers ───────────────────────────────────────────────────────────────────

def _scene_date(name: str) -> str:
    raw = name[17:25] if len(name) > 25 else ""
    if len(raw) == 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return ""


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
    """Precomputed pair quality database for a single InSARHub folder."""

    def __init__(
        self,
        folder_path: str | Path,
        weights: dict[str, float] | None = None,
        force_refresh: bool = False,
        lc_aware: bool = True,
        coherence_aware: bool = True,
    ):
        self.folder           = Path(folder_path).expanduser().resolve()
        self.weights          = weights
        self.force_refresh    = force_refresh
        self.lc_aware         = lc_aware
        self.coherence_aware  = coherence_aware

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
    def lookup(folder: str | Path, ref: str, sec: str) -> float | None:
        """Return quality score for one pair, or None if not in DB."""
        db = _load_db(Path(folder))
        if db is None:
            return None
        return db.get("scores", {}).get(_pair_key(ref, sec))

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
    ) -> dict[str, float]:
        """Return {key: score} for all requested pairs found in DB."""
        db = _load_db(Path(folder))
        if db is None:
            return {}
        scores_db = db.get("scores", {})
        return {
            _pair_key(r, s): scores_db[_pair_key(r, s)]
            for r, s in pairs
            if _pair_key(r, s) in scores_db
        }

    # ── Build ─────────────────────────────────────────────────────────────────

    def build(
        self,
        scenes_by_stack: dict[tuple[int, int], list[str]],
        bperp_by_stack:  dict[tuple[int, int], dict[str, float]],
        progress_cb=None,
        show_progress: bool = True,
    ) -> None:
        """Compute scores for all N*(N-1)/2 pairs and persist to disk.

        Parameters
        ----------
        scenes_by_stack : {(path, frame): [scene_name, ...]}
        bperp_by_stack  : {(path, frame): {scene_name: bperp_m}}
        progress_cb     : optional callable(done: int, total: int) — for API use
        show_progress   : show tqdm progress bars on stderr (default True)
        """
        from insarhub.utils.pair_quality._cache import CacheManager
        from insarhub.utils.pair_quality._feature_assembler import FeatureAssembler
        from insarhub.utils.pair_quality import _classifier
        from insarhub.utils.pair_quality.pair_quality import _load_aoi, _wkt_centroid

        try:
            from tqdm import tqdm as _tqdm_cls
        except ImportError:
            _tqdm_cls = None

        def _tqdm(iterable, **kw):
            if _tqdm_cls and show_progress:
                return _tqdm_cls(iterable, **kw)
            return iterable

        try:
            lat, lon, wkt = _load_aoi(self.folder)
        except Exception as exc:
            logger.warning("Could not load AOI from folder: %s — using lat=45, lon=0", exc)
            lat, lon, wkt = 45.0, 0.0, "POLYGON ((0 45, 1 45, 1 46, 0 46, 0 45))"

        cache     = CacheManager(self.folder, force_refresh=self.force_refresh)
        assembler = FeatureAssembler(
            cache=cache, aoi_wkt=wkt, lat=lat, lon=lon,
            skip_ndvi=self.coherence_aware,  # S3 COG is primary; skip slow NDVI fetch
        )

        # Collect all unique scenes across all stacks
        all_scenes: list[str] = []
        for scene_list in scenes_by_stack.values():
            all_scenes.extend(scene_list)
        all_scenes = list(dict.fromkeys(all_scenes))  # deduplicate, preserve order

        # Prefetch date-level features (weather, snow, NDVI) for all unique dates
        unique_dates: list[str] = list({
            d for s in all_scenes for d in [_scene_date(s)] if d
        })
        if _tqdm_cls and show_progress:
            _tqdm_cls.write(f"Prefetching weather/snow for {len(unique_dates)} dates …")
        assembler.prefetch_dates(unique_dates)

        # Prefetch S1 global coherence COH maps for all seasons in the pair set
        if self.coherence_aware:
            all_date_pairs = [
                (_scene_date(r), _scene_date(s))
                for r, s, _, _ in (
                    (ref, sec, None, None)
                    for key, scene_list in scenes_by_stack.items()
                    for ref, sec in itertools.combinations(scene_list, 2)
                )
            ]
            if _tqdm_cls and show_progress:
                _tqdm_cls.write("Prefetching S1 coherence maps from S3 …")
            assembler.prefetch_coherence(
                [(d1, d2) for d1, d2 in all_date_pairs if d1 and d2]
            )

        cache.save()

        # Build flat bperp lookup from all stacks
        bperp_flat: dict[str, float] = {}
        for bp_map in bperp_by_stack.values():
            bperp_flat.update(bp_map)

        scores:  dict[str, float] = {}
        factors: dict[str, dict]  = {}

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

        mode = "S3 coherence" if self.coherence_aware else ("LC/NDVI" if self.lc_aware else "flat")
        for i, (ref, sec, bperp_ref, bperp_sec) in enumerate(_tqdm(
            all_pairs,
            desc=f"Scoring pairs [{mode}]",
            unit="pair",
            total=total,
        )):
            d1, d2 = _scene_date(ref), _scene_date(sec)
            fv = assembler.assemble(ref, sec, bperp_ref, bperp_sec, d1, d2)
            if self.coherence_aware:
                sc, fct = _classifier.coherence_score(fv)
            elif self.lc_aware:
                sc, fct = _classifier.lc_score(fv)
            else:
                sc, fct = _classifier.score(fv, weights=self.weights)
            key = _pair_key(ref, sec)
            scores[key]  = sc
            factors[key] = fct
            if progress_cb:
                progress_cb(i + 1, total)

        cache.save()

        db: dict = {
            "_schema_version": _SCHEMA_VERSION,
            "_built_at":  datetime.now(timezone.utc).isoformat(),
            "_n_scenes":  len(all_scenes),
            "_n_pairs":   total,
            "_complete":  True,
            "scores":     scores,
            "factors":    factors,
        }
        (self.folder / DB_FILE).write_text(json.dumps(db, indent=2))
        logger.info("PairQualityDB: saved %d scores → %s", total, self.folder / DB_FILE)

    def build_from_folder(self, progress_cb=None) -> None:
        """Load scenes from saved stack_p*_f*.json files and build DB.

        Requires that stack_p*_f*.json exists in the folder
        (written by insarhub downloader --select-pairs).
        """
        scenes_by_stack: dict[tuple[int, int], list[str]] = {}
        bperp_by_stack:  dict[tuple[int, int], dict[str, float]] = {}

        for stack_file in sorted(self.folder.glob("stack_p*_f*.json")):
            stem = stack_file.stem  # "stack_p100_f466"
            parts = stem.split("_")
            try:
                path  = int(parts[1][1:])
                frame = int(parts[2][1:])
            except (IndexError, ValueError):
                continue
            key = (path, frame)
            data = json.loads(stack_file.read_text())
            scenes_by_stack[key] = data.get("scenes", [])
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
