# -*- coding: utf-8 -*-
"""Folder management, pairs, and select-pairs endpoints."""

import asyncio
import dataclasses
import json
import threading as _threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import Response

import insarhub.app.state as state
from insarhub.app.models import FolderDownloadRequest, SelectPairsRequest, SavePairsRequest
from insarhub.commands.downloader import DownloadScenesCommand, SearchCommand
from insarhub.config import S1_SLC_Config
from insarhub.core.registry import Downloader
from insarhub.app.state import _apply_config_from_dict, _new_job, _finish_job

router = APIRouter()


@router.get("/api/folder-details")
async def get_folder_details(path: str):
    """Return downloader config, pairs file presence, and network image path for a job folder."""
    folder = Path(path).expanduser().resolve()
    result: dict[str, Any] = {
        "downloader_config": None,
        "has_pairs": False,
        "network_image": None,
    }
    cfg_file = folder / "downloader_config.json"
    if cfg_file.exists():
        try:
            result["downloader_config"] = json.loads(cfg_file.read_text())
        except Exception:
            pass
    pairs_files = list(folder.glob("pairs_p*_f*.json"))
    result["has_pairs"] = bool(pairs_files)
    network_files = sorted(folder.glob("network_p*_f*.png"))
    result["network_image"] = str(network_files[0]) if network_files else None
    return result


@router.get("/api/folder-pairs")
async def get_folder_pairs(path: str):
    """Return pairs list from the first pairs_p*_f*.json found in the folder."""
    folder = Path(path).expanduser().resolve()
    pairs_files = sorted(folder.glob("pairs_p*_f*.json"))
    if not pairs_files:
        raise HTTPException(status_code=404, detail="No pairs file found")
    try:
        pairs = json.loads(pairs_files[0].read_text())
        return {"pairs": pairs, "count": len(pairs), "file": pairs_files[0].name}
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
    """Re-search and download using the downloader_config.json saved in the job folder."""
    folder = Path(req.folder_path).expanduser().resolve()
    cfg_file = folder / "downloader_config.json"
    if not cfg_file.exists():
        raise HTTPException(status_code=404, detail="downloader_config.json not found in folder")
    job_id, _ = _new_job("Starting search…")
    background_tasks.add_task(_run_folder_download, job_id, req.folder_path)
    return {"job_id": job_id}


async def _run_folder_download(job_id: str, folder_path: str):
    stop_ev = _threading.Event()
    state._stop_events[job_id] = stop_ev

    def run():
        try:
            folder = Path(folder_path).expanduser().resolve()
            raw: dict[str, Any] = json.loads((folder / "downloader_config.json").read_text())

            cfg = S1_SLC_Config(workdir=folder)
            _apply_config_from_dict(cfg, raw, skip_keys={"workdir"})

            downloader = Downloader.create("S1_SLC", cfg)
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
    cfg_file = folder / "downloader_config.json"
    if not cfg_file.exists():
        raise HTTPException(status_code=404, detail="downloader_config.json not found in folder")
    job_id, _ = _new_job("Starting orbit download…")
    background_tasks.add_task(_run_folder_download_orbit, job_id, req.folder_path)
    return {"job_id": job_id}


async def _run_folder_download_orbit(job_id: str, folder_path: str):
    stop_ev = _threading.Event()
    state._stop_events[job_id] = stop_ev

    def run():
        try:
            folder = Path(folder_path).expanduser().resolve()
            raw: dict[str, Any] = json.loads((folder / "downloader_config.json").read_text())

            cfg = S1_SLC_Config(workdir=folder)
            _apply_config_from_dict(cfg, raw, skip_keys={"workdir"})

            downloader = Downloader.create("S1_SLC", cfg)
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
    if not (folder / "downloader_config.json").exists():
        raise HTTPException(status_code=404, detail="downloader_config.json not found in folder")
    job_id, _ = _new_job("Starting search…")
    background_tasks.add_task(_run_folder_select_pairs, job_id, req)
    return {"job_id": job_id}


async def _run_folder_select_pairs(job_id: str, req: SelectPairsRequest):
    def run():
        try:
            folder  = Path(req.folder_path).expanduser().resolve()
            raw: dict[str, Any] = json.loads((folder / "downloader_config.json").read_text())

            dl_type = "S1_SLC"
            wf_file = folder / "insarhub_workflow.json"
            if wf_file.exists():
                try:
                    dl_type = json.loads(wf_file.read_text()).get("downloader", dl_type)
                except Exception:
                    pass

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
            pairs, baselines, scene_bperp = downloader.select_pairs(
                dt_targets=tuple(req.dt_targets),
                dt_tol=req.dt_tol,
                dt_max=req.dt_max,
                pb_max=req.pb_max,
                min_degree=req.min_degree,
                max_degree=req.max_degree,
                force_connect=req.force_connect,
                max_workers=req.max_workers,
            )

            from insarhub.utils.tool import write_workflow_marker
            from insarhub.utils.pair_quality._db import PairQualityDB

            # Build scenes_by_stack from search results for the DB
            active = downloader.active_results
            scenes_by_stack: dict = {}
            if isinstance(active, dict):
                for k, prods in active.items():
                    scenes_by_stack[k] = [p.properties["sceneName"] for p in prods]
            else:
                scenes_by_stack[(0, 0)] = [p.properties["sceneName"] for p in active]

            if isinstance(pairs, dict):
                for (path, frame), group_pairs in pairs.items():
                    subdir = folder.parent / f"p{path}_f{frame}"
                    subdir.mkdir(parents=True, exist_ok=True)
                    write_workflow_marker(subdir, downloader=dl_type)
                    cfg_dict = {k: v for k, v in dataclasses.asdict(cfg).items() if k != 'workdir'}
                    cfg_dict['relativeOrbit'] = path
                    cfg_dict['frame'] = frame
                    (subdir / "downloader_config.json").write_text(json.dumps(cfg_dict, indent=2, default=str))
                    pjson = subdir / f"pairs_p{path}_f{frame}.json"
                    pjson.write_text(json.dumps([list(p) for p in group_pairs], indent=2))
                    sp = scene_bperp.get((path, frame)) or {}
                    (subdir / f"baselines_p{path}_f{frame}.json").write_text(
                        json.dumps({k: float(v) for k, v in sp.items()}, indent=2)
                    )
                    stack_scenes = scenes_by_stack.get((path, frame), [])
                    (subdir / f"scenes_p{path}_f{frame}.json").write_text(json.dumps(stack_scenes, indent=2))
                    # Build full DB (all possible pairs) then filter to selected
                    state._jobs[job_id]["message"] = f"Scoring all pairs — P{path}/F{frame}…"
                    try:
                        db = PairQualityDB(subdir)
                        db.build({(path, frame): stack_scenes}, {(path, frame): {k: float(v) for k, v in sp.items()}})
                        db_data = json.loads((subdir / ".insarhub_pair_quality_db.json").read_text())
                        all_scores  = db_data.get("scores", {})
                        all_factors = db_data.get("factors", {})
                        quality_scores  = {}
                        quality_factors = {}
                        for pair in group_pairs:
                            for k in [f"{pair[0]}:{pair[1]}", f"{pair[1]}:{pair[0]}"]:
                                if k in all_scores:
                                    quality_scores[k]  = all_scores[k]
                                    quality_factors[k] = all_factors.get(k, {})
                        (subdir / f"pair_quality_p{path}_f{frame}.json").write_text(
                            json.dumps({"scores": quality_scores, "factors": quality_factors}, indent=2)
                        )
                    except Exception:
                        quality_scores  = None
                        quality_factors = None
            else:
                pjson = folder / "pairs.json"
                pjson.write_text(json.dumps([list(p) for p in pairs], indent=2))
                write_workflow_marker(folder, downloader=dl_type)
                sp = scene_bperp if isinstance(scene_bperp, dict) else {}
                stack_scenes = scenes_by_stack.get((0, 0), [])
                (folder / "scenes_p0_f0.json").write_text(json.dumps(stack_scenes, indent=2))
                state._jobs[job_id]["message"] = "Scoring all pairs…"
                try:
                    db = PairQualityDB(folder)
                    db.build({(0, 0): stack_scenes}, {(0, 0): {k: float(v) for k, v in sp.items()}})
                    db_data = json.loads((folder / ".insarhub_pair_quality_db.json").read_text())
                    all_scores  = db_data.get("scores", {})
                    all_factors = db_data.get("factors", {})
                    quality_scores  = {}
                    quality_factors = {}
                    for pair in pairs:
                        for k in [f"{pair[0]}:{pair[1]}", f"{pair[1]}:{pair[0]}"]:
                            if k in all_scores:
                                quality_scores[k]  = all_scores[k]
                                quality_factors[k] = all_factors.get(k, {})
                    (folder / "pair_quality.json").write_text(json.dumps({"scores": quality_scores, "factors": quality_factors}, indent=2))
                except Exception:
                    quality_scores  = None
                    quality_factors = None

            _finish_job(job_id, status="done", message="Pairs selected")
        except Exception as e:
            _finish_job(job_id, status="error", progress=0, message=str(e))

    await asyncio.to_thread(run)


@router.get("/api/folder-pairs-candidates")
async def get_folder_pairs_candidates(path: str):
    """Return all pairs JSON files with their pair lists for the network editor."""
    folder = Path(path).expanduser().resolve()
    pairs_files = sorted(folder.glob("pairs_p*_f*.json"))
    result = {}
    for pf in pairs_files:
        key = pf.stem  # e.g. pairs_p100_f466
        try:
            result[key] = json.loads(pf.read_text())
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
    for pairs_file in sorted(folder.glob("pairs_p*_f*.json")):
        key = pairs_file.stem.replace("pairs_", "")   # "p100_f466"
        try:
            pairs = json.loads(pairs_file.read_text())
        except Exception:
            pairs = []

        # Load per-scene perpendicular baselines saved by _run_folder_select_pairs
        bperp_map: dict = {}
        bl_file = folder / f"baselines_{key}.json"
        if bl_file.exists():
            try:
                bperp_map = json.loads(bl_file.read_text())
            except Exception:
                pass

        # Collect unique scene names and build node list
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
        # Sort nodes chronologically
        nodes.sort(key=lambda n: n["date"])
        stacks[key] = {"nodes": nodes, "pairs": pairs}

    return {"stacks": stacks}


@router.post("/api/folder-save-pairs")
async def save_folder_pairs(req: SavePairsRequest):
    """Overwrite pairs JSON files in the folder with the user-edited pairs."""
    folder = Path(req.folder_path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Folder not found")
    saved = []
    for key, pairs in req.pairs.items():
        # key is like "p100_f466"; build filename pairs_p100_f466.json
        fname = f"pairs_{key}.json"
        out = folder / fname
        out.write_text(json.dumps(pairs, indent=2))
        saved.append(fname)
    return {"ok": True, "saved": saved}
