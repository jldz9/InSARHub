# -*- coding: utf-8 -*-
"""Analyzer initialization, step execution, and cleanup endpoints."""

import asyncio
import dataclasses
import threading as _threading
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException

import insarhub.app.state as state
from insarhub.app.models import InitAnalyzerRequest, RunAnalyzerRequest
from insarhub.core.registry import Analyzer

router = APIRouter()

_MINTPY_STEPS = [
    'load_data', 'modify_network', 'reference_point', 'invert_network',
    'correct_LOD', 'correct_SET', 'correct_ionosphere', 'correct_troposphere',
    'deramp', 'correct_topography', 'residual_RMS', 'reference_date',
    'velocity', 'geocode', 'google_earth', 'hdfeos5', 'plot',
]


@router.post("/api/folder-init-analyzer")
async def folder_init_analyzer(req: InitAnalyzerRequest):
    """Mark a folder with an analyzer role in insarhub_workflow.json."""
    from insarhub.utils.tool import write_workflow_marker
    folder = Path(req.folder_path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Folder not found")
    if req.analyzer_type not in Analyzer._registry:
        raise HTTPException(status_code=400, detail=f"Unknown analyzer: {req.analyzer_type}")
    write_workflow_marker(folder, analyzer=req.analyzer_type)
    return {"ok": True, "analyzer": req.analyzer_type}


@router.get("/api/analyzer-steps")
async def get_analyzer_steps(analyzer_type: str):
    """Return the list of steps for a MintPy-based analyzer."""
    cls = Analyzer._registry.get(analyzer_type)
    if cls is None:
        raise HTTPException(status_code=400, detail=f"Unknown analyzer: {analyzer_type}")
    import inspect
    src = inspect.getsource(cls.run) if hasattr(cls, 'run') else ''
    if 'TimeSeriesAnalysis' in src or 'mintpy' in src.lower():
        steps = (['prep_data'] if hasattr(cls, 'prep_data') else []) + _MINTPY_STEPS
    else:
        steps = []
    return {"steps": steps}


@router.post("/api/folder-run-analyzer")
async def folder_run_analyzer(req: RunAnalyzerRequest, background_tasks: BackgroundTasks):
    import uuid
    job_id = str(uuid.uuid4())
    state._jobs[job_id] = {"status": "running", "progress": 0, "message": "Starting analyzer…", "data": None}
    background_tasks.add_task(_run_analyzer, job_id, req)
    return {"job_id": job_id}


async def _run_analyzer(job_id: str, req: RunAnalyzerRequest):
    stop_ev = _threading.Event()
    state._stop_events[job_id] = stop_ev

    def run():
        log: list[str] = []

        def update(msg: str, pct: int):
            log.append(msg)
            state._jobs[job_id]["progress"] = pct
            state._jobs[job_id]["message"]  = "\n".join(log)

        try:
            folder = Path(req.folder_path).expanduser().resolve()
            cls = Analyzer._registry.get(req.analyzer_type)
            if cls is None:
                state._jobs[job_id] = {"status": "error", "progress": 0, "message": f"Unknown analyzer: {req.analyzer_type}", "data": None}
                return
            config_cls = getattr(cls, "default_config", None)
            if config_cls is None or not dataclasses.is_dataclass(config_cls):
                state._jobs[job_id] = {"status": "error", "progress": 0, "message": "Analyzer has no config dataclass", "data": None}
                return

            saved_overrides = state._settings.get("analyzer_configs", {}).get(req.analyzer_type, {})
            valid_keys = {f.name for f in dataclasses.fields(config_cls)}
            init_kwargs: dict = {k: v for k, v in saved_overrides.items() if k in valid_keys}
            init_kwargs["workdir"] = folder
            cfg = config_cls(**init_kwargs)
            analyzer = cls(cfg)

            total = len(req.steps)
            completed = 0
            for i, step in enumerate(req.steps):
                if stop_ev.is_set():
                    update(f"[stopped] Cancelled before {step}", int(i / total * 100))
                    break

                update(f"[{i+1}/{total}] {step} — running…", int(i / total * 100))
                try:
                    if step == 'prep_data':
                        analyzer.prep_data()
                    else:
                        analyzer.run(steps=[step])
                    update(f"[{i+1}/{total}] {step} — done", int((i+1) / total * 100))
                    completed += 1
                except Exception as e:
                    update(f"[{i+1}/{total}] {step} — ERROR: {e}", int(i / total * 100))
                    state._jobs[job_id]["status"] = "error"
                    return

            state._stop_events.pop(job_id, None)
            if stop_ev.is_set():
                state._jobs[job_id]["status"] = "done"
            else:
                update(f"─── Finished {completed}/{total} step(s) ───", 100)
                state._jobs[job_id]["status"] = "done"
                state._jobs[job_id]["progress"] = 100

        except Exception as e:
            state._stop_events.pop(job_id, None)
            log.append(f"FATAL: {e}")
            state._jobs[job_id] = {"status": "error", "progress": 0, "message": "\n".join(log), "data": None}

    await asyncio.to_thread(run)


@router.post("/api/folder-analyzer-cleanup")
async def folder_analyzer_cleanup(req: RunAnalyzerRequest):
    """Run analyzer.cleanup() to remove tmp dirs and zip archives."""
    folder = Path(req.folder_path).expanduser().resolve()
    cls = Analyzer._registry.get(req.analyzer_type)
    if cls is None:
        raise HTTPException(status_code=400, detail=f"Unknown analyzer: {req.analyzer_type}")
    config_cls = getattr(cls, "default_config", None)
    if config_cls is None or not dataclasses.is_dataclass(config_cls):
        raise HTTPException(status_code=400, detail="Analyzer has no config dataclass")
    cfg = config_cls(workdir=folder)
    analyzer = cls(cfg)
    try:
        analyzer.cleanup()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}
