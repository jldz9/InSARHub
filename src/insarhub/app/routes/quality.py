# -*- coding: utf-8 -*-
"""
/api/pair-quality  — interferogram pair quality scoring endpoint.

Returns a quality score in [0, 1] (0 = likely good, 1 = likely bad) for
every pair in a folder, computed from temporal/perpendicular baseline,
snow conditions (Open-Meteo ERA5), and NDVI (MODIS or climatology).

Results are cached in <folder>/.insarhub_quality_cache.json so repeated
calls are instant.
"""

import asyncio

from fastapi import APIRouter, HTTPException

from insarhub.app.models import PairQualityResponse

router = APIRouter()


@router.get("/api/pair-quality", response_model=PairQualityResponse)
async def get_pair_quality(path: str, force_refresh: bool = False):
    """Compute and return quality scores for all pairs in *path*.

    Query parameters
    ----------------
    path          : absolute path to a job folder
    force_refresh : if true, ignore the on-disk cache and re-fetch all data
    """
    from pathlib import Path

    folder = Path(path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"Folder not found: {path}")

    def _run():
        import json
        from pathlib import Path as _Path

        # Fast path: read pre-computed JSON written by select_pairs
        if not force_refresh:
            merged_scores: dict = {}
            merged_factors: dict = {}
            ndvi_sources: set = set()
            found_any = False
            for qfile in sorted(folder.glob("pair_quality_*.json")) or [folder / "pair_quality.json"]:
                if not qfile.exists():
                    continue
                try:
                    data = json.loads(qfile.read_text())
                    merged_scores.update(data.get("scores", {}))
                    merged_factors.update(data.get("factors", {}))
                    src = data.get("ndvi_source", "climatology")
                    if src:
                        ndvi_sources.add(src)
                    found_any = True
                except Exception:
                    pass
            if found_any:
                from insarhub.utils.pair_quality import QualityResult
                return QualityResult(
                    scores=merged_scores,
                    factors=merged_factors,
                    ndvi_source=next(iter(ndvi_sources), "climatology"),
                    snow_fetched=0,
                    cached=True,
                )

        # Slow path: compute on demand (also updates cache)
        from insarhub.utils.pair_quality import PairQuality
        pq = PairQuality(folder, force_refresh=force_refresh)
        return pq.compute(show_progress=False)

    try:
        result = await asyncio.to_thread(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # For large pair sets, omit factor details from the response to avoid
    # sending tens of MB over HTTP.  The GUI fetches per-pair factors on
    # demand via /api/pair-quality-db/lookup when the user hovers an edge.
    _FACTOR_LIMIT = 2000
    factors = result.factors if len(result.scores) <= _FACTOR_LIMIT else {}

    return PairQualityResponse(
        scores=result.scores,
        factors=factors,
        ndvi_source=result.ndvi_source,
        snow_fetched=result.snow_fetched,
        cached=result.cached,
    )


@router.get("/api/pair-quality-db/status")
async def get_pair_quality_db_status(path: str):
    """Return metadata about the precomputed pair quality DB for *path*.

    Response keys: exists, n_scenes, n_pairs, built_at, complete
    """
    from pathlib import Path
    from insarhub.utils.pair_quality._db import PairQualityDB

    folder = Path(path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"Folder not found: {path}")
    return PairQualityDB.status(folder)


@router.get("/api/pair-quality-db/lookup")
async def lookup_pair_quality_db(path: str, pairs: str):
    """Return precomputed quality scores for the requested pairs.

    Query parameters
    ----------------
    path  : absolute path to a job folder
    pairs : comma-separated list of "ref:sec" pair keys

    Response
    --------
    { "scores": {"ref:sec": 0.23, ...}, "factors": {"ref:sec": {...}, ...} }
    If a pair is missing from the DB its key is omitted from the response.
    """
    from pathlib import Path
    from insarhub.utils.pair_quality._db import PairQualityDB, _load_db

    folder = Path(path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"Folder not found: {path}")

    if not PairQualityDB.exists(folder):
        raise HTTPException(
            status_code=404,
            detail="Pair quality DB not found. Run insarhub downloader --select-pairs first."
        )

    pair_keys = [p.strip() for p in pairs.split(",") if ":" in p]
    if not pair_keys:
        raise HTTPException(status_code=400, detail="No valid pair keys provided (expect ref:sec format)")

    db = _load_db(folder)
    scores_db  = db.get("scores", {})  if db else {}
    factors_db = db.get("factors", {}) if db else {}

    out_scores:  dict[str, float] = {}
    out_factors: dict[str, dict]  = {}
    for key in pair_keys:
        if key in scores_db:
            out_scores[key]  = scores_db[key]
            out_factors[key] = factors_db.get(key, {})

    return {"scores": out_scores, "factors": out_factors}


_building: set[str] = set()  # track folders currently being built

@router.post("/api/pair-quality-db/build")
async def build_pair_quality_db(path: str):
    """Trigger a background DB build for *path* from saved scenes_p*_f*.json files.

    Returns immediately; idempotent — ignores if a build is already running.
    """
    import threading
    from pathlib import Path
    from insarhub.utils.pair_quality._db import PairQualityDB

    folder = Path(path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"Folder not found: {path}")

    key = str(folder)
    if key in _building:
        return {"status": "already_building", "path": key}

    def _run():
        _building.add(key)
        try:
            PairQualityDB(folder).build_from_folder()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("DB build failed for %s: %s", key, exc)
        finally:
            _building.discard(key)

    t = threading.Thread(target=_run, daemon=False, name=f"pq-db-{folder.name}")
    t.start()
    return {"status": "building", "path": key}
