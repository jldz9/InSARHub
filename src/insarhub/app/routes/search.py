# -*- coding: utf-8 -*-
"""Scene search, download, and AOI parsing endpoints."""

import asyncio
import base64
import dataclasses
import json
import os
import tempfile
import threading as _threading
import uuid
from pathlib import Path

import geopandas as gpd
from shapely import wkt as shapely_wkt
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

import insarhub.app.state as state
from insarhub.app.models import (
    AddJobRequest, DownloadByNameRequest, DownloadRequest,
    DownloadSceneRequest, JobResponse, ParseAoiRequest, SearchRequest,
)
from insarhub.commands.downloader import DownloadScenesCommand, SearchCommand
from insarhub.config import S1_SLC_Config
from insarhub.core.registry import Downloader

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_geojson(results: dict) -> dict:
    features = []
    for stack_key, scenes in results.items():
        for scene in scenes:
            try:
                features.append({
                    "type": "Feature",
                    "geometry":   scene.geometry,
                    "properties": {**scene.properties, "_stack": str(stack_key)},
                })
            except Exception:
                continue
    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/api/parse-granule-file")
async def parse_granule_file(file: UploadFile = File(...)):
    from insarhub.utils.tool import parse_scene_names_from_file
    suffix  = Path(file.filename or '').suffix.lower() or '.tmp'
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name
        names = parse_scene_names_from_file(tmp_path)
        return {"names": names, "count": len(names)}
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.post("/api/search", response_model=JobResponse)
async def start_search(req: SearchRequest, background_tasks: BackgroundTasks):
    job_id     = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    state._jobs[job_id] = {"status": "running", "progress": 0, "message": "Starting search...", "data": None}
    background_tasks.add_task(_run_search, job_id, session_id, req)
    return {"job_id": job_id}


async def _run_search(job_id: str, session_id: str, req: SearchRequest):
    def run():
        try:
            if req.granule_names:
                config     = S1_SLC_Config(workdir=req.workdir, granule_names=req.granule_names)
                downloader = Downloader.create("S1_SLC", config)
            else:
                rel_orbit = None
                if req.pathStart is not None:
                    p_end     = req.pathEnd if req.pathEnd is not None else req.pathStart
                    rel_orbit = list(range(req.pathStart, p_end + 1))
                asf_frame = None
                if req.frameStart is not None:
                    f_end     = req.frameEnd if req.frameEnd is not None else req.frameStart
                    asf_frame = list(range(req.frameStart, f_end + 1))

                intersects_with = req.wkt if req.wkt else (req.west, req.south, req.east, req.north)
                if isinstance(intersects_with, str):
                    try:
                        geom = shapely_wkt.loads(intersects_with)
                        for tol in (0.001, 0.005, 0.01, 0.05, 0.1):
                            simplified = geom.simplify(tol, preserve_topology=True)
                            if len(simplified.wkt) <= 2000:
                                break
                        intersects_with = simplified.wkt
                    except Exception:
                        pass

                config = S1_SLC_Config(
                    intersectsWith=intersects_with,
                    start=req.start, end=req.end,
                    workdir=req.workdir,
                    maxResults=req.maxResults,
                    beamMode=req.beamMode or None,
                    polarization=req.polarization or None,
                    flightDirection=req.flightDirection or None,
                    relativeOrbit=rel_orbit or None,
                    asfFrame=asf_frame or None,
                )
                downloader = Downloader.create("S1_SLC", config)

            cmd    = SearchCommand(downloader, progress_callback=state._make_progress(job_id))
            result = cmd.run()

            if result.success:
                state._sessions[session_id] = downloader
                state._jobs[job_id] = {
                    "status": "done", "progress": 100, "message": result.message,
                    "data": {
                        "session_id": session_id,
                        "geojson":    _to_geojson(result.data),
                        "summary":    result.message,
                    },
                }
            else:
                state._jobs[job_id] = {"status": "error", "progress": 0, "message": result.message, "data": None}
        except Exception as e:
            state._jobs[job_id] = {"status": "error", "progress": 0, "message": str(e), "data": None}

    await asyncio.to_thread(run)


@router.post("/api/download", response_model=JobResponse)
async def start_download(req: DownloadRequest, background_tasks: BackgroundTasks):
    if req.session_id not in state._sessions:
        raise HTTPException(status_code=404, detail="Session not found — run /api/search first")
    job_id = str(uuid.uuid4())
    state._jobs[job_id] = {"status": "running", "progress": 0, "message": "Starting download...", "data": None}
    background_tasks.add_task(_run_download, job_id, req)
    return {"job_id": job_id}


async def _run_download(job_id: str, req: DownloadRequest):
    def run():
        try:
            downloader = state._sessions[req.session_id]
            cmd    = DownloadScenesCommand(downloader, progress_callback=state._make_progress(job_id))
            result = cmd.run()
            state._jobs[job_id] = {
                "status":   "done" if result.success else "error",
                "progress": 100,
                "message":  result.message,
                "data":     str(result.data) if result.data else None,
            }
        except Exception as e:
            state._jobs[job_id] = {"status": "error", "progress": 0, "message": str(e), "data": None}
    await asyncio.to_thread(run)


@router.post("/api/download-scene", response_model=JobResponse)
async def download_single_scene(req: DownloadSceneRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    state._jobs[job_id] = {"status": "running", "progress": 0, "message": "Starting download...", "data": None}
    background_tasks.add_task(_run_download_scene, job_id, req)
    return {"job_id": job_id}


async def _run_download_scene(job_id: str, req: DownloadSceneRequest):
    stop_ev = _threading.Event()
    state._stop_events[job_id] = stop_ev

    def run():
        file_path = None
        try:
            import asf_search as asf
            from asf_search.download.download import _try_get_response

            workdir = Path(req.workdir)
            workdir.mkdir(parents=True, exist_ok=True)
            filename  = req.filename or req.url.rstrip("/").split("/")[-1].split("?")[0]
            file_path = workdir / filename
            state._jobs[job_id]["message"] = f"Downloading {filename}…"

            session   = asf.ASFSession()
            response  = _try_get_response(session=session, url=req.url)
            total_bytes = int(response.headers.get("content-length", 0))
            downloaded  = 0

            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=65536):
                    if stop_ev.is_set():
                        response.close()
                        state._jobs[job_id] = {"status": "done", "progress": 0, "message": "Stopped.", "data": None}
                        return
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_bytes:
                            pct = int(downloaded / total_bytes * 100)
                            state._jobs[job_id]["progress"] = pct
                            state._jobs[job_id]["message"]  = f"Downloading {filename}… {pct}%"

            state._jobs[job_id] = {"status": "done", "progress": 100, "message": f"Saved {filename}", "data": str(file_path)}
        except InterruptedError:
            state._jobs[job_id] = {"status": "done", "progress": 0, "message": "Stopped.", "data": None}
        except Exception as e:
            if file_path and file_path.exists():
                file_path.unlink(missing_ok=True)
            state._jobs[job_id] = {"status": "error", "progress": 0, "message": str(e), "data": None}
        finally:
            state._stop_events.pop(job_id, None)

    await asyncio.to_thread(run)


@router.post("/api/download-stack", response_model=JobResponse)
async def download_stack(req: AddJobRequest, background_tasks: BackgroundTasks):
    job_id  = str(uuid.uuid4())
    stop_ev = _threading.Event()
    state._jobs[job_id]        = {"status": "running", "progress": 0, "message": "Starting…", "data": None}
    state._stop_events[job_id] = stop_ev
    background_tasks.add_task(_run_download_stack, job_id, req, stop_ev)
    return {"job_id": job_id}


async def _run_download_stack(job_id: str, req: AddJobRequest, stop_ev: _threading.Event):
    def run():
        try:
            workdir = Path(req.workdir).expanduser().resolve()
            workdir.mkdir(parents=True, exist_ok=True)
            cfg          = S1_SLC_Config(workdir=workdir)
            valid_fields = {f.name for f in dataclasses.fields(cfg)}
            for key, val in state._settings.get("downloader_config", {}).items():
                if key in valid_fields and key != "workdir" and val is not None:
                    try: setattr(cfg, key, val)
                    except Exception: pass
            for key, val in {
                "start": req.start, "end": req.end,
                "relativeOrbit": req.relativeOrbit, "frame": req.frame,
                "intersectsWith": req.wkt, "flightDirection": req.flightDirection,
                "platform": req.platform,
            }.items():
                if key in valid_fields and val is not None:
                    setattr(cfg, key, val)

            downloader    = Downloader.create("S1_SLC", cfg)
            state._jobs[job_id]["message"] = "Searching scenes…"
            search_result = SearchCommand(downloader).run()
            if not search_result.success:
                state._jobs[job_id] = {"status": "error", "progress": 0, "message": search_result.message, "data": None}
                return
            if stop_ev.is_set():
                state._jobs[job_id] = {"status": "done", "progress": 0, "message": "Stopped.", "data": None}
                return

            total = sum(len(v) for v in downloader.results.values())
            state._jobs[job_id]["message"] = f"Downloading 0/{total}"

            def _on_progress(msg: str, pct: int):
                count = msg.split(']')[0].lstrip('[') if ']' in msg else ''
                state._jobs[job_id]["message"]  = f"Downloading {count}" if count else msg
                state._jobs[job_id]["progress"] = pct

            dl_result = DownloadScenesCommand(downloader, stop_event=stop_ev, on_progress=_on_progress).run()
            save_dir  = workdir / f"p{req.relativeOrbit}_f{req.frame}"
            if stop_ev.is_set():
                state._jobs[job_id] = {"status": "done", "progress": 0, "message": "Stopped.", "data": None}
            else:
                state._jobs[job_id] = {
                    "status": "done" if dl_result.success else "error",
                    "progress": 100, "message": dl_result.message, "data": str(save_dir),
                }
        except Exception as e:
            state._jobs[job_id] = {"status": "error", "progress": 0, "message": str(e), "data": None}
        finally:
            state._stop_events.pop(job_id, None)

    await asyncio.to_thread(run)


@router.post("/api/add-job")
async def add_job(req: AddJobRequest):
    from insarhub.utils.tool import write_workflow_marker
    workdir = Path(req.workdir).expanduser().resolve()
    subdir  = workdir / f"p{req.relativeOrbit}_f{req.frame}"
    subdir.mkdir(parents=True, exist_ok=True)

    dl_cls      = Downloader._registry.get(req.downloaderType)
    cfg_cls     = getattr(dl_cls, "default_config", S1_SLC_Config) if dl_cls else S1_SLC_Config
    cfg_instance = cfg_cls(workdir=subdir)
    valid_fields = {f.name for f in dataclasses.fields(cfg_instance)}
    for key, val in state._settings.get("downloader_config", {}).items():
        if key in valid_fields and key != "workdir" and val is not None:
            try: setattr(cfg_instance, key, val)
            except Exception: pass
    for key, val in {
        "start": req.start, "end": req.end,
        "relativeOrbit": req.relativeOrbit, "frame": req.frame,
        "intersectsWith": req.wkt, "flightDirection": req.flightDirection,
        "platform": req.platform,
    }.items():
        if key in valid_fields and val is not None:
            setattr(cfg_instance, key, val)
    cfg = {k: v for k, v in dataclasses.asdict(cfg_instance).items() if k != "workdir"}
    (subdir / "downloader_config.json").write_text(json.dumps(cfg, indent=2, default=str))
    write_workflow_marker(subdir, downloader=req.downloaderType)
    return {"path": str(subdir), "name": subdir.name}


@router.post("/api/download-orbit-stack", response_model=JobResponse)
async def download_orbit_stack(req: AddJobRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    state._jobs[job_id] = {"status": "running", "progress": 0, "message": "Starting orbit download…", "data": None}
    background_tasks.add_task(_run_download_orbit_stack, job_id, req)
    return {"job_id": job_id}


async def _run_download_orbit_stack(job_id: str, req: AddJobRequest):
    stop_ev = _threading.Event()
    state._stop_events[job_id] = stop_ev

    def run():
        try:
            workdir  = Path(req.workdir).expanduser().resolve()
            save_dir = workdir / f"p{req.relativeOrbit}_f{req.frame}"
            save_dir.mkdir(parents=True, exist_ok=True)
            cfg          = S1_SLC_Config(workdir=save_dir)
            valid_fields = {f.name for f in dataclasses.fields(cfg)}
            for key, val in state._settings.get("downloader_config", {}).items():
                if key in valid_fields and key != "workdir" and val is not None:
                    try: setattr(cfg, key, val)
                    except Exception: pass
            for key, val in {
                "start": req.start, "end": req.end,
                "relativeOrbit": req.relativeOrbit, "frame": req.frame,
                "intersectsWith": req.wkt, "flightDirection": req.flightDirection,
                "platform": req.platform,
            }.items():
                if key in valid_fields and val is not None:
                    setattr(cfg, key, val)

            downloader    = Downloader.create("S1_SLC", cfg)
            state._jobs[job_id]["message"] = "Searching scenes…"
            search_result = SearchCommand(downloader, progress_callback=state._make_progress(job_id)).run()
            if not search_result.success:
                state._jobs[job_id] = {"status": "error", "progress": 0, "message": search_result.message, "data": None}
                return
            state._jobs[job_id]["message"] = "Downloading orbit files…"
            downloader.download_orbit(save_dir=str(save_dir), stop_event=stop_ev)
            if stop_ev.is_set():
                state._jobs[job_id] = {"status": "done", "progress": 0, "message": "Stopped.", "data": None}
            else:
                state._jobs[job_id] = {"status": "done", "progress": 100, "message": "Orbit files downloaded.", "data": None}
        except Exception as e:
            state._jobs[job_id] = {"status": "error", "progress": 0, "message": str(e), "data": None}
        finally:
            state._stop_events.pop(job_id, None)

    await asyncio.to_thread(run)


@router.post("/api/download-by-name", response_model=JobResponse)
async def download_by_name(req: DownloadByNameRequest, background_tasks: BackgroundTasks):
    if not req.scene_names and not req.scene_file:
        raise HTTPException(status_code=422, detail="Provide scene_names or scene_file")
    job_id  = str(uuid.uuid4())
    stop_ev = _threading.Event()
    state._jobs[job_id]        = {"status": "running", "progress": 0, "message": "Starting…", "data": None}
    state._stop_events[job_id] = stop_ev
    background_tasks.add_task(_run_download_by_name, job_id, req, stop_ev)
    return {"job_id": job_id}


async def _run_download_by_name(job_id: str, req: DownloadByNameRequest, stop_ev: _threading.Event):
    def run():
        try:
            workdir = Path(req.workdir).expanduser().resolve()
            workdir.mkdir(parents=True, exist_ok=True)
            dl_cls  = Downloader._registry.get(req.downloaderType)
            cfg_cls = getattr(dl_cls, "default_config", S1_SLC_Config) if dl_cls else S1_SLC_Config
            cfg     = cfg_cls(workdir=workdir)
            downloader = Downloader.create(req.downloaderType, cfg)

            from insarhub.utils.tool import parse_scene_names_from_file
            names: list[str] = list(req.scene_names)
            if req.scene_file:
                names = list(dict.fromkeys(names + parse_scene_names_from_file(req.scene_file)))
            cfg.granule_names = names
            state._jobs[job_id]["message"] = f"Searching {len(names)} scene(s)…"
            downloader.search()

            if stop_ev.is_set():
                state._jobs[job_id] = {"status": "done", "progress": 0, "message": "Stopped.", "data": None}
                return

            total = sum(len(v) for v in downloader.results.values())
            state._jobs[job_id]["message"] = f"Downloading 0/{total}"

            def _on_progress(msg: str, pct: int):
                count = msg.split(']')[0].lstrip('[') if ']' in msg else ''
                state._jobs[job_id]["message"]  = f"Downloading {count}" if count else msg
                state._jobs[job_id]["progress"] = pct

            dl_result = DownloadScenesCommand(downloader, stop_event=stop_ev, on_progress=_on_progress).run()
            if stop_ev.is_set():
                state._jobs[job_id] = {"status": "done", "progress": 0, "message": "Stopped.", "data": None}
            else:
                state._jobs[job_id] = {
                    "status": "done" if dl_result.success else "error",
                    "progress": 100, "message": dl_result.message, "data": str(workdir),
                }
        except Exception as e:
            state._jobs[job_id] = {"status": "error", "progress": 0, "message": str(e), "data": None}
        finally:
            state._stop_events.pop(job_id, None)

    await asyncio.to_thread(run)


@router.post("/api/parse-aoi")
async def parse_aoi(req: ParseAoiRequest):
    import json as _json
    suffix   = Path(req.filename).suffix.lower()
    tmp_path = None
    try:
        content = base64.b64decode(req.data)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        gdf = gpd.read_file(tmp_path)
        if gdf.empty:
            raise HTTPException(status_code=422, detail="No features found in file")
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)
        feature = _json.loads(gdf.iloc[[0]].to_json())["features"][0]
        return {"feature": feature}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
