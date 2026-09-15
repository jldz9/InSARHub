# -*- coding: utf-8 -*-
"""
/api/pair-quality  — interferogram pair quality endpoint.

Returns "healthy" or "concern" for every pair in a folder. A pair is flagged
concern when at least one serious extreme condition was detected at either
acquisition —
wet snow, deep snow, heavy rain, a large soil-moisture step — or when the S1
global coherence decay model already predicts unusable coherence at the pair's
temporal baseline. Each verdict carries the events and the measurements behind
them; see insarhub.utils.pair_quality._events.

Results are cached in <folder>/.insarhub_quality_cache.json so repeated
calls are instant.
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from insarhub.app.models import PairQualityLookupRequest, PairQualityResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/pair-quality", response_model=PairQualityResponse)
async def get_pair_quality(path: str, force_refresh: bool = False):
    """Judge every pair in *path*: healthy, or concern.

    Query parameters
    ----------------
    path          : absolute path to a job folder
    force_refresh : if true, ignore the on-disk cache and re-fetch all data
    """
    import json as _json
    from pathlib import Path

    folder = Path(path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"Folder not found: {path}")

    # Require AOI before quality scoring — without intersectsWith the coherence
    # model silently uses a fallback AOI and caches wrong decay maps to disk.
    _cfg_file = folder / "insarhub_config.json"
    _has_aoi = False
    if _cfg_file.exists():
        try:
            _cfg = _json.loads(_cfg_file.read_text())
            _dl_cfg = _cfg.get("downloader", {}).get("config", {})
            _has_aoi = bool(_dl_cfg.get("intersectsWith") or _dl_cfg.get("scene_footprint_wkt"))
        except Exception:
            pass
    if not _has_aoi:
        raise HTTPException(
            status_code=400,
            detail="AOI not configured. Run pair selection first to record the study area.",
        )

    def _run():
        import json

        def _from_stack_files():
            """Return the pre-computed QualityResult, or None if none is stored."""
            merged_status: dict = {}
            merged_factors: dict = {}
            found_any = False
            for sfile in sorted(folder.glob("stack_p*_f*.json")):
                try:
                    data = json.loads(sfile.read_text())
                    pq = data.get("pair_quality", {})
                    # Schema 1 stored a numeric "scores" map. Those numbers came
                    # from a weighting that no longer exists and cannot be
                    # mapped onto an event verdict, so a pre-event stack file
                    # is treated as unjudged and rebuilt rather than migrated.
                    merged_status.update(pq.get("status", {}))
                    merged_factors.update(pq.get("factors", {}))
                    if pq.get("status"):
                        found_any = True
                except Exception:
                    pass
            if not found_any:
                return None
            from insarhub.utils.pair_quality import QualityResult
            return QualityResult(
                status=merged_status,
                factors=merged_factors,
                remote_fetches=0,
                cached=True,
                missing_dates=[],
                thresholds={},
            )

        # Fast path: read pre-computed JSON written by select_pairs (stored in stack_p*_f*.json)
        if not force_refresh:
            result = _from_stack_files()
            if result is not None:
                return result

        from insarhub.utils.pair_quality._db import build_lock, is_building

        # A background DB build populates exactly these verdicts, and select-pairs
        # launches one immediately -- so an empty fast path usually means "not
        # finished yet", not "nobody will ever compute this". Computing anyway
        # duplicated every weather/snow/S3 request alongside the build and put
        # two writers on one cache file. Wait for the build instead, then read
        # what it produced.
        if is_building(folder):
            logger.info("Pair quality for %s is being built — waiting for it", folder.name)
            with build_lock(folder):
                pass
            if not force_refresh:
                result = _from_stack_files()
                if result is not None:
                    return result

        # Slow path: compute on demand (also updates cache). PairQuality.compute()
        # takes the same folder lock, so this stays serialised either way.
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
    factors = result.factors if len(result.status) <= _FACTOR_LIMIT else {}

    return PairQualityResponse(
        status=result.status,
        factors=factors,
        remote_fetches=result.remote_fetches,
        cached=result.cached,
        missing_dates=result.missing_dates,
        thresholds=result.thresholds,
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


def _lookup_status(path: str, pair_keys: list[str]) -> dict:
    """Shared implementation for the GET and POST pair-DB lookup routes."""
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

    valid = [k for k in (p.strip() for p in pair_keys) if ":" in k]
    if not valid:
        raise HTTPException(status_code=400, detail="No valid pair keys provided (expect ref:sec format)")

    db = _load_db(folder)
    status_db  = db.get("status", {})  if db else {}
    factors_db = db.get("factors", {}) if db else {}

    out_status:  dict[str, str]  = {}
    out_factors: dict[str, dict] = {}
    for key in valid:
        if key in status_db:
            out_status[key]  = status_db[key]
            out_factors[key] = factors_db.get(key, {})

    return {"status": out_status, "factors": out_factors}


@router.get("/api/pair-quality-db/lookup")
async def lookup_pair_quality_db(path: str, pairs: str):
    """Return the precomputed verdict for the requested pairs.

    Query parameters
    ----------------
    path  : absolute path to a job folder
    pairs : comma-separated list of "ref:sec" pair keys

    Prefer the POST form for large pair sets: a query string is capped at the
    HTTP parser's ~64 KiB request line, so a stack with many pairs is rejected
    with HTTP 400 before it reaches this handler.

    Response
    --------
    { "status": {"ref:sec": "concern", ...}, "factors": {"ref:sec": {...}, ...} }
    If a pair is missing from the DB its key is omitted from the response.
    """
    return _lookup_status(path, pairs.split(","))


@router.post("/api/pair-quality-db/lookup")
async def lookup_pair_quality_db_post(req: PairQualityLookupRequest):
    """Same as the GET lookup, with the pair keys in the JSON body.

    The network editor looks up every edge lacking a cached verdict — for a
    large stack that is thousands of ~100-character keys. In a query string
    that exceeds the HTTP parser's request-line limit and comes back as
    HTTP 400; a request body has no such limit.
    """
    return _lookup_status(req.path, req.pairs)


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


