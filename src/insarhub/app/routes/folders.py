# -*- coding: utf-8 -*-
"""Folder management, pairs, and select-pairs endpoints."""

import asyncio
import dataclasses
import json
import logging
import threading as _threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import Response

import insarhub.app.state as state
from insarhub.app.models import FolderDownloadRequest, SelectPairsRequest, SavePairsRequest
from insarhub.commands.downloader import DownloadScenesCommand, SearchCommand
from insarhub.config import S1_SLC_Config
from insarhub.core.registry import Downloader
from insarhub.app.state import _apply_config_from_dict, _new_job, _finish_job, read_insarhub_config, write_insarhub_config

router = APIRouter()


@router.get("/api/folder-details")
async def get_folder_details(path: str):
    """Return downloader config, pairs file presence, and network image path for a job folder."""
    folder = Path(path).expanduser().resolve()
    cfg = read_insarhub_config(folder)
    result: dict[str, Any] = {
        "downloader_config": cfg.get("downloader", {}).get("config"),
        "has_pairs": bool(list(folder.glob("stack_p*_f*.json"))),
        "network_image": None,
    }
    network_files = sorted(folder.glob("network_p*_f*.png"))
    result["network_image"] = str(network_files[0]) if network_files else None
    return result


@router.get("/api/folder-pairs")
async def get_folder_pairs(path: str):
    """Return pairs list from the first stack_p*_f*.json found in the folder."""
    folder = Path(path).expanduser().resolve()
    stack_files = sorted(folder.glob("stack_p*_f*.json"))
    if not stack_files:
        raise HTTPException(status_code=404, detail="No stack file found")
    try:
        data = json.loads(stack_files[0].read_text())
        pairs = data.get("pairs") or []
        return {"pairs": pairs, "count": len(pairs), "file": stack_files[0].name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/folder-image")
async def get_folder_image(path: str):
    """Serve a PNG image from the filesystem by absolute path."""
    img_path = Path(path).expanduser().resolve()
    workdir = Path(state._settings["workdir"])
    try:
        img_path.resolve().relative_to(workdir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Path is outside workdir")
    if not img_path.exists() or img_path.suffix.lower() != ".png":
        raise HTTPException(status_code=404, detail="Image not found")
    return Response(content=img_path.read_bytes(), media_type="image/png")


@router.post("/api/folder-download")
async def folder_download(req: FolderDownloadRequest, background_tasks: BackgroundTasks):
    """Re-search and download using insarhub_config.json saved in the job folder."""
    folder = Path(req.folder_path).expanduser().resolve()
    if not read_insarhub_config(folder).get("downloader"):
        raise HTTPException(status_code=404, detail="No downloader config found in folder")
    job_id, _ = _new_job("Starting search…")
    background_tasks.add_task(_run_folder_download, job_id, req.folder_path)
    return {"job_id": job_id}


async def _run_folder_download(job_id: str, folder_path: str):
    stop_ev = _threading.Event()
    state._stop_events[job_id] = stop_ev

    def run():
        try:
            folder = Path(folder_path).expanduser().resolve()
            insarhub_cfg = read_insarhub_config(folder)
            dl_section = insarhub_cfg.get("downloader", {})
            dl_type = dl_section.get("type", "S1_SLC")
            raw: dict[str, Any] = dl_section.get("config", {})

            dl_cls  = Downloader._registry.get(dl_type)
            cfg_cls = getattr(dl_cls, "default_config", S1_SLC_Config) if dl_cls else S1_SLC_Config
            cfg = cfg_cls(workdir=folder)
            _apply_config_from_dict(cfg, raw, skip_keys={"workdir"})

            downloader = Downloader.create(dl_type, cfg)
            search_result = SearchCommand(downloader, progress_callback=state._make_progress(job_id)).run()
            if not search_result.success:
                _finish_job(job_id, status="error", progress=0, message=search_result.message)
                return

            if stop_ev.is_set():
                _finish_job(job_id, status="done", progress=0, message="Stopped.")
                return

            total = sum(len(v) for v in downloader.results.values())
            state._jobs[job_id]["message"] = f"Downloading 0/{total}"

            def _on_progress(msg: str, pct: int):
                count = msg.split(']')[0].lstrip('[') if ']' in msg else ''
                state._jobs[job_id]["message"] = f"Downloading {count}" if count else msg
                state._jobs[job_id]["progress"] = pct

            dl_result = DownloadScenesCommand(
                downloader,
                stop_event=stop_ev,
                on_progress=_on_progress,
                save_path=str(folder.parent),
            ).run()

            if stop_ev.is_set():
                _finish_job(job_id, status="done", progress=0, message="Stopped.")
                return

            _finish_job(job_id, status="done" if dl_result.success else "error", message=dl_result.message)
        except Exception as e:
            _finish_job(job_id, status="error", progress=0, message=str(e))
        finally:
            state._stop_events.pop(job_id, None)

    await asyncio.to_thread(run)


@router.post("/api/folder-download-orbit")
async def folder_download_orbit(req: FolderDownloadRequest, background_tasks: BackgroundTasks):
    """Download orbit files for scenes in a job folder."""
    folder = Path(req.folder_path).expanduser().resolve()
    if not read_insarhub_config(folder).get("downloader"):
        raise HTTPException(status_code=404, detail="No downloader config found in folder")
    job_id, _ = _new_job("Starting orbit download…")
    background_tasks.add_task(_run_folder_download_orbit, job_id, req.folder_path)
    return {"job_id": job_id}


async def _run_folder_download_orbit(job_id: str, folder_path: str):
    stop_ev = _threading.Event()
    state._stop_events[job_id] = stop_ev

    def run():
        try:
            folder = Path(folder_path).expanduser().resolve()
            insarhub_cfg = read_insarhub_config(folder)
            dl_section = insarhub_cfg.get("downloader", {})
            dl_type = dl_section.get("type", "S1_SLC")
            raw: dict[str, Any] = dl_section.get("config", {})

            dl_cls  = Downloader._registry.get(dl_type)
            cfg_cls = getattr(dl_cls, "default_config", S1_SLC_Config) if dl_cls else S1_SLC_Config
            cfg = cfg_cls(workdir=folder)
            _apply_config_from_dict(cfg, raw, skip_keys={"workdir"})

            downloader = Downloader.create(dl_type, cfg)
            state._jobs[job_id]["message"] = "Searching scenes…"
            search_result = SearchCommand(downloader, progress_callback=state._make_progress(job_id)).run()
            if not search_result.success:
                _finish_job(job_id, status="error", progress=0, message=search_result.message)
                return

            state._jobs[job_id]["message"] = "Downloading orbit files…"
            downloader.download_orbit(save_dir=str(folder), stop_event=stop_ev)
            if stop_ev.is_set():
                _finish_job(job_id, status="done", progress=0, message="Stopped.")
            else:
                _finish_job(job_id, status="done", message="Orbit files downloaded.")
        except Exception as e:
            _finish_job(job_id, status="error", progress=0, message=str(e))
        finally:
            state._stop_events.pop(job_id, None)

    await asyncio.to_thread(run)


@router.post("/api/folder-select-pairs")
async def folder_select_pairs(req: SelectPairsRequest, background_tasks: BackgroundTasks):
    """Re-search using downloader_config.json and run select_pairs with given parameters."""
    folder = Path(req.folder_path).expanduser().resolve()
    if not read_insarhub_config(folder).get("downloader"):
        raise HTTPException(status_code=404, detail="No downloader config found in folder")
    job_id, _ = _new_job("Starting search…")
    background_tasks.add_task(_run_folder_select_pairs, job_id, req)
    return {"job_id": job_id}


def _seed_pair_quality_cache(folder: Path, prefetch: dict) -> None:
    """Write weather/snow data fetched by select_pairs into the folder's quality cache.

    prefetch must be {"weather": {date: feats}, "snow": {date: feats}, "lat": float, "lon": float}.
    FeatureAssembler.prefetch_dates() checks this cache first, so the data won't be
    re-fetched from Open-Meteo / MODIS during scoring.
    """
    weather = prefetch.get("weather", {})
    snow    = prefetch.get("snow", {})
    if not weather and not snow:
        return
    lat = prefetch.get("lat", 0.0)
    lon = prefetch.get("lon", 0.0)
    try:
        from insarhub.utils.pair_quality._cache import CacheManager
        cache = CacheManager(folder)
        for date, feats in weather.items():
            if feats:
                cache.set("weather", f"{lat:.3f}:{lon:.3f}:{date}", feats)
        for date, feats in snow.items():
            if feats:
                cache.set("snow_modis", f"{lat:.3f}:{lon:.3f}:{date}", feats)
        cache.save()
        logger.debug("Seeded quality cache for %s with %d weather + %d snow entries",
                     folder.name, len(weather), len(snow))
    except Exception as exc:
        logger.warning("Could not seed quality cache for %s: %s", folder, exc)


def _launch_db_build(folder, scenes_by_stack, bperp_by_stack) -> str | None:
    """Launch a background thread that builds the full pair-quality DB for *folder*.

    Skips the build if an existing complete DB already covers the same scene set.
    Returns the job_id, or None if the build was skipped.
    """
    import threading
    from insarhub.utils.pair_quality._db import PairQualityDB

    # Count unique scenes across all stacks
    all_scenes: set[str] = set()
    for scene_list in scenes_by_stack.values():
        all_scenes.update(scene_list)
    n_scenes = len(all_scenes)

    from insarhub.utils.pair_quality._db import _load_db
    existing = _load_db(folder)
    if existing and existing.get("_complete"):
        existing_names = existing.get("_scene_names")
        if existing_names is not None:
            # Full check: skip if current scenes are a subset of what the DB covers
            skip = all_scenes <= set(existing_names)
        else:
            # Old DB without _scene_names: fall back to count (avoids spurious rebuilds)
            skip = existing.get("_n_scenes", 0) >= n_scenes
        if skip:
            logger.debug("Pair DB up-to-date for %s (%d scenes) — skipping rebuild", folder.name, n_scenes)
            return None

    db_job_id, _ = _new_job(f"Building pair database — {folder.name}…")

    def _run():
        try:
            PairQualityDB(folder).build(scenes_by_stack, bperp_by_stack, show_progress=False)
            _finish_job(db_job_id, status="done", message="Pair database ready")
        except Exception as exc:
            logger.warning("Background DB build failed for %s: %s", folder, exc)
            _finish_job(db_job_id, status="error", progress=0, message=str(exc))

    t = threading.Thread(target=_run, daemon=True, name=f"pq-db-bg-{folder.name}")
    t.start()
    return db_job_id


async def _run_folder_select_pairs(job_id: str, req: SelectPairsRequest):
    def run():
        try:
            folder  = Path(req.folder_path).expanduser().resolve()
            insarhub_cfg = read_insarhub_config(folder)
            dl_section = insarhub_cfg.get("downloader", {})
            dl_type = dl_section.get("type", "S1_SLC")
            raw: dict[str, Any] = dl_section.get("config", {})

            dl_cls  = Downloader._registry.get(dl_type)
            cfg_cls = getattr(dl_cls, "default_config", S1_SLC_Config) if dl_cls else S1_SLC_Config

            cfg = cfg_cls(workdir=folder.parent)
            _apply_config_from_dict(cfg, raw, skip_keys={"workdir"})

            downloader = Downloader.create(dl_type, cfg)
            state._jobs[job_id]["message"] = "Searching scenes…"
            search_result = SearchCommand(downloader, progress_callback=state._make_progress(job_id)).run()
            if not search_result.success:
                state._jobs[job_id] = {"status": "error", "progress": 0, "message": search_result.message, "data": None}
                return

            state._jobs[job_id]["message"] = "Selecting pairs…"
            pairs, baselines, scene_bperp, prefetch_cache = downloader.select_pairs(
                dt_targets=tuple(req.dt_targets),
                dt_tol=req.dt_tol,
                dt_max=req.dt_max,
                pb_max=req.pb_max,
                min_degree=req.min_degree,
                max_degree=req.max_degree,
                force_connect=req.force_connect,
                max_workers=req.max_workers,
                avoid_low_quality_days=req.avoid_low_quality_days,
                snow_threshold=req.snow_threshold,
                precip_mm_threshold=req.precip_mm_threshold,
            )

            # Build scenes_by_stack from search results for the DB
            active = downloader.active_results
            scenes_by_stack: dict = {}
            if isinstance(active, dict):
                for k, prods in active.items():
                    scenes_by_stack[k] = [p.properties["sceneName"] for p in prods]
            else:
                scenes_by_stack[(0, 0)] = [p.properties["sceneName"] for p in active]

            db_job_ids: list[str] = []

            if isinstance(pairs, dict):
                for (path, frame), group_pairs in pairs.items():
                    subdir = folder.parent / f"p{path}_f{frame}"
                    subdir.mkdir(parents=True, exist_ok=True)
                    cfg_dict = {k: v for k, v in dataclasses.asdict(cfg).items() if k != 'workdir'}
                    cfg_dict['relativeOrbit'] = path
                    cfg_dict['frame'] = frame
                    write_insarhub_config(subdir, {"downloader": {"type": dl_type, "config": cfg_dict}})
                    sp = scene_bperp.get((path, frame)) or {}
                    stack_scenes = scenes_by_stack.get((path, frame), [])
                    stack_data: dict = {
                        "pairs":    [list(p) for p in group_pairs],
                        "baselines": {k: float(v) for k, v in sp.items()},
                        "scenes":   stack_scenes,
                        "pair_quality": {"scores": {}, "factors": {}},
                    }
                    (subdir / f"stack_p{path}_f{frame}.json").write_text(json.dumps(stack_data, indent=2))
                    state._jobs[job_id]["message"] = f"Building weather/snow cache — P{path}/F{frame}…"
                    _seed_pair_quality_cache(subdir, prefetch_cache.get((path, frame), {}))
                    state._jobs[job_id]["message"] = f"Scoring {len(group_pairs)} pairs — P{path}/F{frame}…"
                    try:
                        from insarhub.utils.pair_quality import PairQuality
                        result = PairQuality(subdir, force_refresh=False).compute(show_progress=False)
                        stack_data["pair_quality"] = {
                            "scores":  result.scores,
                            "factors": result.factors,
                        }
                        (subdir / f"stack_p{path}_f{frame}.json").write_text(json.dumps(stack_data, indent=2))
                    except Exception as _exc:
                        logger.warning("Pair quality scoring failed: %s", _exc)
                    db_jid = _launch_db_build(
                        subdir, {(path, frame): stack_scenes},
                        {(path, frame): {k: float(v) for k, v in sp.items()}}
                    )
                    if db_jid:
                        db_job_ids.append(db_jid)
            else:
                cfg_dict = {k: v for k, v in dataclasses.asdict(cfg).items() if k != 'workdir'}
                write_insarhub_config(folder, {"downloader": {"type": dl_type, "config": cfg_dict}})
                sp = scene_bperp if isinstance(scene_bperp, dict) else {}
                stack_scenes = scenes_by_stack.get((0, 0), [])
                stack_data = {
                    "pairs":    [list(p) for p in pairs],
                    "baselines": {k: float(v) for k, v in sp.items()},
                    "scenes":   stack_scenes,
                    "pair_quality": {"scores": {}, "factors": {}},
                }
                (folder / "stack_p0_f0.json").write_text(json.dumps(stack_data, indent=2))
                state._jobs[job_id]["message"] = "Building weather/snow cache…"
                _seed_pair_quality_cache(folder, prefetch_cache)
                state._jobs[job_id]["message"] = f"Scoring {len(pairs)} selected pairs…"
                try:
                    from insarhub.utils.pair_quality import PairQuality
                    result = PairQuality(folder, force_refresh=False).compute(show_progress=False)
                    stack_data["pair_quality"] = {
                        "scores":  result.scores,
                        "factors": result.factors,
                    }
                    (folder / "stack_p0_f0.json").write_text(json.dumps(stack_data, indent=2))
                except Exception as _exc:
                    logger.warning("Pair quality scoring failed: %s", _exc)
                db_jid = _launch_db_build(
                    folder, {(0, 0): stack_scenes},
                    {(0, 0): {k: float(v) for k, v in sp.items()}}
                )
                if db_jid:
                    db_job_ids.append(db_jid)

            _finish_job(job_id, status="done", message="Pairs selected",
                        data={"db_job_ids": db_job_ids})
        except Exception as e:
            _finish_job(job_id, status="error", progress=0, message=str(e))

    await asyncio.to_thread(run)


@router.get("/api/folder-pairs-candidates")
async def get_folder_pairs_candidates(path: str):
    """Return all stack JSON files with their pair lists for the network editor."""
    folder = Path(path).expanduser().resolve()
    stack_files = sorted(folder.glob("stack_p*_f*.json"))
    result = {}
    for sf in stack_files:
        # key mirrors old "pairs_p100_f466" convention for frontend compatibility
        key = sf.stem.replace("stack_", "pairs_", 1)
        try:
            data = json.loads(sf.read_text())
            result[key] = data.get("pairs", [])
        except Exception:
            result[key] = []
    return {"candidates": result}


@router.get("/api/folder-network-data")
async def get_folder_network_data(path: str):
    """Return nodes + pairs for every stack in a folder, ready for the network editor.

    Each stack entry has:
      nodes  – list of {id, date, bperp} (date = ISO-8601, bperp = metres)
      pairs  – list of [ref, sec] scene-name pairs
    """
    folder = Path(path).expanduser().resolve()
    stacks: dict = {}
    for stack_file in sorted(folder.glob("stack_p*_f*.json")):
        key = stack_file.stem.replace("stack_", "")   # "p100_f466"
        try:
            data = json.loads(stack_file.read_text())
        except Exception:
            data = {}
        pairs     = data.get("pairs", [])
        bperp_map = data.get("baselines", {})

        seen: dict[str, None] = {}
        for pair in pairs:
            if len(pair) >= 2:
                seen.setdefault(pair[0], None)
                seen.setdefault(pair[1], None)
        nodes = []
        for name in seen:
            raw_date = name[17:25] if len(name) > 25 else ""
            iso_date = (f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                        if len(raw_date) == 8 else "")
            nodes.append({
                "id":    name,
                "date":  iso_date,
                "bperp": float(bperp_map.get(name, 0.0)),
            })
        nodes.sort(key=lambda n: n["date"])
        stacks[key] = {"nodes": nodes, "pairs": pairs}

    return {"stacks": stacks}


@router.post("/api/folder-save-pairs")
async def save_folder_pairs(req: SavePairsRequest):
    """Overwrite pairs in stack_p*_f*.json with the user-edited pairs."""
    folder = Path(req.folder_path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Folder not found")
    saved = []
    for key, pairs in req.pairs.items():
        stack_file = folder / f"stack_{key}.json"
        try:
            data = json.loads(stack_file.read_text()) if stack_file.exists() else {}
        except Exception:
            data = {}
        data["pairs"] = pairs
        stack_file.write_text(json.dumps(data, indent=2))
        saved.append(stack_file.name)
    return {"ok": True, "saved": saved}
