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

        # Fast path: read pre-computed JSON written by select_pairs (stored in stack_p*_f*.json)
        if not force_refresh:
            merged_scores: dict = {}
            merged_factors: dict = {}
            ndvi_sources: set = set()
            found_any = False
            for sfile in sorted(folder.glob("stack_p*_f*.json")):
                try:
                    data = json.loads(sfile.read_text())
                    pq = data.get("pair_quality", {})
                    merged_scores.update(pq.get("scores", {}))
                    merged_factors.update(pq.get("factors", {}))
                    src = pq.get("ndvi_source", "climatology")
                    if src:
                        ndvi_sources.add(src)
                    if pq.get("scores"):
                        found_any = True
                except Exception:
                    pass
            if found_any:
                # Normalise legacy 0-1 scores to 0-100
                if merged_scores and max(merged_scores.values()) <= 1.5:
                    merged_scores = {k: max(0, min(100, round(float(v) * 100))) for k, v in merged_scores.items()}
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

    legacy = scores_db and max(scores_db.values(), default=0) <= 1.5
    out_scores:  dict[str, float] = {}
    out_factors: dict[str, dict]  = {}
    for key in pair_keys:
        if key in scores_db:
            v = scores_db[key]
            out_scores[key]  = max(0, min(100, round(float(v) * 100))) if legacy else v
            out_factors[key] = factors_db.get(key, {})

    return {"scores": out_scores, "factors": out_factors}


@router.get("/api/coherence-maps")
async def get_coherence_maps(path: str):
    """Return summary statistics for saved pixel decay map GeoTIFFs.

    Looks for ``{path}/decay_maps/S1_coherence_decay_*.tif`` files written by
    the pair-quality pipeline and returns per-season/pol statistics.

    Response
    --------
    {
      "available": [
        {
          "season": "summer",
          "pol":    "vv",
          "shape":  [H, W],
          "file":   "decay_maps/S1_coherence_decay_summer_vv.tif",
          "stats": {
            "gamma_inf": {"mean": 0.12, "min": 0.0,  "max": 0.48},
            "gamma0":    {"mean": 0.65, "min": 0.02, "max": 1.0},
            "tau":       {"mean": 28.4, "min": 3.1,  "max": 120.0}
          }
        },
        ...
      ]
    }
    """
    import numpy as np
    from pathlib import Path

    folder = Path(path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"Folder not found: {path}")

    decay_maps_dir = folder / "decay_maps"
    if not decay_maps_dir.exists():
        return {"available": []}

    def _read_stats():
        try:
            import rasterio
        except ImportError:
            raise HTTPException(status_code=500, detail="rasterio not installed")

        results = []
        for tif in sorted(decay_maps_dir.glob("S1_coherence_decay_*.tif")):
            try:
                with rasterio.open(tif) as src:
                    ginf = src.read(1).astype(float)
                    g0   = src.read(2).astype(float)
                    tau  = src.read(3).astype(float)
                    nd   = float(src.nodata) if src.nodata is not None else -9999.0
                    H, W = src.height, src.width
                    tags = src.tags()

                season = tags.get("season", "")
                pol    = tags.get("pol",    "")

                # Infer season/pol from filename if tags missing
                if not season or not pol:
                    stem = tif.stem  # S1_coherence_decay_summer_vv
                    parts = stem.split("_")
                    if len(parts) >= 5:
                        season = parts[3]
                        pol    = parts[4]

                valid = (ginf != nd) & (g0 != nd) & (tau != nd)
                if not valid.any():
                    continue

                def _s(arr: np.ndarray) -> dict:
                    v = arr[valid]
                    return {
                        "mean": round(float(v.mean()), 4),
                        "min":  round(float(v.min()),  4),
                        "max":  round(float(v.max()),  4),
                    }

                results.append({
                    "season": season,
                    "pol":    pol,
                    "shape":  [H, W],
                    "n_valid_pixels": int(valid.sum()),
                    "file":   str(tif.relative_to(folder)),
                    "stats":  {
                        "gamma_inf": _s(ginf),
                        "gamma0":    _s(g0),
                        "tau":       _s(tau),
                    },
                })

            except Exception as exc:
                results.append({"file": tif.name, "error": str(exc)})

        return {"available": results}

    try:
        import asyncio
        return await asyncio.to_thread(_read_stats)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


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
