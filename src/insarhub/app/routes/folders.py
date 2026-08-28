# -*- coding: utf-8 -*-
"""Folder management, pairs, and select-pairs endpoints."""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from fastapi import APIRouter, BackgroundTasks, HTTPException

import insarhub.app.state as state
from insarhub.app.models import FolderDownloadRequest, SelectPairsRequest, SavePairsRequest
from insarhub.commands.downloader import DownloadScenesCommand, SearchCommand
from insarhub.config import S1_SLC_Config
from insarhub.core.registry import Downloader
from insarhub.app.routes.search import _download_workers, _EXECUTION_CONFIG_FIELDS
from insarhub.app.state import _apply_config_from_dict, _new_job, _finish_job, read_insarhub_config, write_insarhub_config
from insarhub.utils.pair_quality._cache import seed_prefetch
from insarhub.utils.pair_quality._geom import footprint_wkt_from_products
from insarhub.utils.stack_io import write_stack_file

router = APIRouter()


@router.get("/api/folder-details")
async def get_folder_details(path: str):
    """Return downloader config and pairs file presence for a job folder."""
    folder = Path(path).expanduser().resolve()
    cfg = read_insarhub_config(folder)
    return {
        "downloader_config": cfg.get("downloader", {}).get("config"),
        "has_pairs": bool(list(folder.glob("stack_p*_f*.json"))),
    }


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


@router.post("/api/folder-download")
async def folder_download(req: FolderDownloadRequest, background_tasks: BackgroundTasks):
    """Re-search and download using insarhub_config.json saved in the job folder."""
    folder = Path(req.folder_path).expanduser().resolve()
    if not read_insarhub_config(folder).get("downloader"):
        raise HTTPException(status_code=404, detail="No downloader config found in folder")
    return state.launch_job(background_tasks, _run_folder_download, req.folder_path,
                            start_message="Starting search…")


async def _run_folder_download(job_id: str, folder_path: str):
    def run(stop_ev):
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
            # Execution settings (max_workers, ssl_verify) track the CURRENT
            # settings form, not the folder's saved config -- otherwise changing
            # max_workers in the GUI after the folder was created would have no
            # effect (the saved config froze it at add-job time). Search filters
            # still come from the folder's config above; only these override.
            _exec = {k: v for k, v in (state._settings.get("downloader_config", {}) or {}).items()
                     if k in _EXECUTION_CONFIG_FIELDS}
            _apply_config_from_dict(cfg, _exec, skip_keys={"workdir"})

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

            # Download INTO the job folder itself, not its parent. Passing the
            # parent (a workdir root) breaks whenever that root also carries an
            # insarhub_config.json: download() then treats the root as a single
            # flat stack and dumps every scene into <root>/slc instead of
            # <folder>/slc. Passing the folder keeps the SLCs co-located with
            # the folder's own config/stack file for every downloader.
            _dl_save = str(folder) if dl_type != "S1_Burst" else str(folder / "slc")
            dl_result = DownloadScenesCommand(
                downloader,
                stop_event=stop_ev,
                on_progress=state._make_download_progress(job_id),
                save_path=_dl_save,
                max_workers=_download_workers(cfg),
            ).run()

            if stop_ev.is_set():
                _finish_job(job_id, status="done", progress=0, message="Stopped.")
                return

            _finish_job(job_id, status="done" if dl_result.success else "error", message=dl_result.message)
        except Exception as e:
            _finish_job(job_id, status="error", progress=0, message=str(e))

    with state.stop_event(job_id) as stop_ev:
        await asyncio.to_thread(run, stop_ev)


@router.post("/api/folder-download-orbit")
async def folder_download_orbit(req: FolderDownloadRequest, background_tasks: BackgroundTasks):
    """Download orbit files for scenes in a job folder."""
    folder = Path(req.folder_path).expanduser().resolve()
    if not read_insarhub_config(folder).get("downloader"):
        raise HTTPException(status_code=404, detail="No downloader config found in folder")
    return state.launch_job(background_tasks, _run_folder_download_orbit, req.folder_path,
                            start_message="Starting orbit download…")


async def _run_folder_download_orbit(job_id: str, folder_path: str):
    def run(stop_ev):
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

    with state.stop_event(job_id) as stop_ev:
        await asyncio.to_thread(run, stop_ev)


@router.post("/api/folder-select-pairs")
async def folder_select_pairs(req: SelectPairsRequest, background_tasks: BackgroundTasks):
    """Re-search using insarhub_config.json and run select_pairs with given parameters."""
    folder = Path(req.folder_path).expanduser().resolve()
    if not read_insarhub_config(folder).get("downloader"):
        raise HTTPException(status_code=404, detail="No downloader config found in folder")
    return state.launch_job(background_tasks, _run_folder_select_pairs, req,
                            start_message="Starting search…")



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

            cfg = cfg_cls(workdir=folder)
            _apply_config_from_dict(cfg, raw, skip_keys={"workdir"})

            downloader = Downloader.create(dl_type, cfg)
            state._jobs[job_id]["message"] = "Searching scenes…"
            search_result = SearchCommand(downloader, progress_callback=state._make_progress(job_id)).run()
            if not search_result.success:
                state._jobs[job_id] = {"status": "error", "progress": 0, "message": search_result.message, "data": None}
                return

            state._jobs[job_id]["message"] = "Selecting pairs…"
            # Auto-detect merge intent from what the search actually found, not
            # from how `frame` happens to be saved (a list, omitted, whatever) —
            # that's unreliable across the different ways a folder gets set up
            # (Add Job, Add Merged Job, hand-edited config, legacy layouts).
            # If this folder is already an established single stack directory
            # AND the search turned up more than one frame on one path, every
            # one of those frames belongs in this one folder's network.
            _dl_is_stack = (folder / "insarhub_config.json").exists()
            active = downloader.active_results
            _active_paths = {p for (p, _f) in active.keys()} if isinstance(active, dict) else set()
            merge_flag = (
                _dl_is_stack and isinstance(active, dict)
                and len(_active_paths) == 1 and len(active) > 1
            )
            pairs, baselines, scene_bperp, prefetch_cache, _qs, _qf = downloader.select_pairs(
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
                merge=merge_flag,
                quality_check=False,   # GUI scores via its own async DB build below
                plot_network=False,
            )

            # Build scenes_by_stack from search results for the DB — keys
            # line up with `pairs`: (path, frame) normally, or (path,
            # "merged_f89_f90...") per distinct frame group when merged (see
            # StackPaths.merge_tag — encodes every constituent frame so two
            # merge groups on the same path never collide on one directory name).
            from insarhub.utils.tool import group_scenes_by_stack
            from insarhub.config.paths import StackPaths
            scenes_by_stack = group_scenes_by_stack(downloader.active_results, merge=merge_flag)
            _sp = StackPaths(folder.parent)

            db_job_ids: list[str] = []
            if isinstance(pairs, dict):
                for (path, frame), group_pairs in pairs.items():
                    is_merged = StackPaths.is_merge_key(frame)
                    label = f"P{path} ({frame})" if is_merged else f"P{path}/F{frame}"
                    # Already an established stack directory (e.g. set up via Add
                    # Job / Add Merged Job) → write back into it directly, don't
                    # scatter results into new sibling folders under the parent.
                    subdir = folder if _dl_is_stack else _sp.dir_for(path, frame)
                    subdir.mkdir(parents=True, exist_ok=True)
                    cfg_dict = state._cfg_dict(cfg)
                    cfg_dict['relativeOrbit'] = path
                    if not is_merged:
                        cfg_dict['frame'] = frame
                    # When intersectsWith is absent, derive AOI from scene footprints so
                    # quality scoring has a valid region without falling back to a hardcoded default.
                    if not cfg_dict.get('intersectsWith'):
                        if is_merged:
                            merge_prods = [
                                p for (k_path, _k_frame), prods in downloader.active_results.items()
                                if k_path == path for p in prods
                            ]
                        else:
                            merge_prods = downloader.active_results.get((path, frame), [])
                        wkt = footprint_wkt_from_products(merge_prods)
                        if wkt:
                            cfg_dict['scene_footprint_wkt'] = wkt
                    write_insarhub_config(subdir, {"downloader": {"type": dl_type, "config": cfg_dict}})
                    sp = scene_bperp.get((path, frame)) or {}
                    stack_scenes = scenes_by_stack.get((path, frame), [])
                    # Filename only, applied to the actual subdir — subdir may be
                    # `folder` itself (whatever it's actually named) rather than
                    # the name _sp.dir_for(path, frame) would compute.
                    stack_path = subdir / _sp.stack_file_for(path, frame).name
                    write_stack_file(stack_path, group_pairs, sp, stack_scenes)
                    state._jobs[job_id]["message"] = f"Building weather/snow cache — {label}…"
                    seed_prefetch(subdir, prefetch_cache.get((path, frame), {}))
                    db_job_id = _launch_db_build(
                        subdir,
                        {(path, frame): stack_scenes},
                        {(path, frame): {k: float(v) for k, v in sp.items()}},
                    )
                    if db_job_id:
                        db_job_ids.append(db_job_id)
            else:
                cfg_dict = state._cfg_dict(cfg)
                if not cfg_dict.get('intersectsWith'):
                    all_prods = [r for rs in downloader.active_results.values() for r in rs]
                    wkt = footprint_wkt_from_products(all_prods)
                    if wkt:
                        cfg_dict['scene_footprint_wkt'] = wkt
                write_insarhub_config(folder, {"downloader": {"type": dl_type, "config": cfg_dict}})
                sp = scene_bperp if isinstance(scene_bperp, dict) else {}
                stack_scenes = scenes_by_stack.get((0, 0), [])
                stack_path = folder / _sp.stack_file(0, 0).name
                write_stack_file(stack_path, pairs, sp, stack_scenes)
                state._jobs[job_id]["message"] = "Building weather/snow cache…"
                seed_prefetch(folder, prefetch_cache)
                db_job_id = _launch_db_build(
                    folder,
                    {(0, 0): stack_scenes},
                    {(0, 0): {k: float(v) for k, v in sp.items()}},
                )
                if db_job_id:
                    db_job_ids.append(db_job_id)

            _finish_job(job_id, status="done", message="Pairs selected", data={"db_job_ids": db_job_ids})
        except Exception as e:
            _finish_job(job_id, status="error", progress=0, message=str(e))

    await asyncio.to_thread(run)


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
            # Burst stacks key nodes by bare YYYYMMDD acquisition dates (one
            # date = one stitched SAFE). Scene stacks key by full SLC granule
            # names. Both must resolve to an ISO date for the editor layout.
            if re.fullmatch(r"\d{8}", name):
                iso_date = f"{name[:4]}-{name[4:6]}-{name[6:8]}"
            else:
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
