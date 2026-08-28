# -*- coding: utf-8 -*-
"""Analyzer initialization, step execution, and cleanup endpoints."""

import asyncio
import dataclasses
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException

import insarhub.app.state as state
from insarhub.app.models import InitAnalyzerRequest, RunAnalyzerRequest
from insarhub.app.state import _finish_job, read_insarhub_config, write_insarhub_config
from insarhub.core.registry import Analyzer

router = APIRouter()


def _resolve_aoi_yx(config, folder: Path) -> None:
    """For geocoded (UTM) stacks, convert network_aoiLALO → network_aoiYX.

    MintPy's aoiLALO→pixel conversion only works for radar-geometry stacks
    that have a lat/lon lookup table.  HyP3 outputs are geocoded in UTM, so
    we project the LALO bounding box ourselves and set aoiYX directly.
    """
    lalo = getattr(config, "network_aoiLALO", "auto")
    if not lalo or lalo in ("auto", ""):
        return

    geo_file = folder / "inputs" / "geometryGeo.h5"
    if not geo_file.exists():
        return  # radar geometry — MintPy handles aoiLALO natively

    try:
        import h5py
        from pyproj import Transformer

        with h5py.File(geo_file, "r") as f:
            meta = dict(f.attrs)

        epsg    = int(meta.get("EPSG", 0))
        x_first = float(meta["X_FIRST"])
        y_first = float(meta["Y_FIRST"])
        x_step  = float(meta["X_STEP"])
        y_step  = float(meta["Y_STEP"])
        width   = int(meta["WIDTH"])
        length  = int(meta["LENGTH"])

        if epsg == 4326 or meta.get("X_UNIT", "").lower() == "degrees":
            return  # already geographic — aoiLALO works natively

        # Parse "S:N,W:E"
        lat_part, lon_part = lalo.split(",")
        south, north = [float(v) for v in lat_part.split(":")]
        west,  east  = [float(v) for v in lon_part.split(":")]

        # Project corners from WGS84 to the stack CRS
        transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        w_m, s_m = transformer.transform(west,  south)
        e_m, n_m = transformer.transform(east,  north)

        # Geo → pixel  (y_step is negative for north-up)
        x0 = int((w_m - x_first) / x_step)
        x1 = int((e_m - x_first) / x_step)
        y0 = int((n_m - y_first) / y_step)
        y1 = int((s_m - y_first) / y_step)

        # Clamp to valid range and ensure correct order
        x0, x1 = sorted([max(0, min(x0, width  - 1)), max(0, min(x1, width  - 1))])
        y0, y1 = sorted([max(0, min(y0, length - 1)), max(0, min(y1, length - 1))])

        if x0 < x1 and y0 < y1:
            config.network_aoiYX  = f"{y0}:{y1},{x0}:{x1}"
            config.network_aoiLALO = "auto"  # suppress from .mintpy.cfg; stored in insarhub_config.json

    except Exception:
        pass  # silently fall back; aoiLALO stays as-is


_MINTPY_STEPS = [
    'load_data', 'modify_network', 'reference_point', 'quick_overview',
    'correct_unwrap_error', 'invert_network',
    'correct_LOD', 'correct_SET', 'correct_ionosphere', 'correct_troposphere',
    'deramp', 'correct_topography', 'residual_RMS', 'reference_date',
    'velocity', 'geocode', 'google_earth', 'hdfeos5', 'plot',
]


@router.post("/api/folder-init-analyzer")
async def folder_init_analyzer(req: InitAnalyzerRequest):
    """Mark a folder with an analyzer type in insarhub_config.json."""
    folder = Path(req.folder_path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Folder not found")
    if req.analyzer_type not in Analyzer._registry:
        raise HTTPException(status_code=400, detail=f"Unknown analyzer: {req.analyzer_type}")
    az_defaults = state._default_config_values(req.analyzer_type, state._ANALYZERS_META)

    # Populate network_aoiLALO from the folder's downloader AOI if still default.
    # Only MintPy-based analyzers carry this field; adding it to a standalone
    # config (ISCE3_Dolphin_PL, GMTSAR_SBAS) would write a key their __init__ later
    # rejects on load.
    if "network_aoiLALO" in az_defaults and \
            az_defaults.get("network_aoiLALO", "auto") in ("auto", "", None):
        try:
            from insarhub.utils.pair_quality._geom import _wkt_bbox
            insarhub_cfg = read_insarhub_config(folder)
            wkt = insarhub_cfg.get("downloader", {}).get("config", {}).get("intersectsWith")
            if wkt:
                west, south, east, north = _wkt_bbox(wkt)
                az_defaults["network_aoiLALO"] = f"{south:.4f}:{north:.4f},{west:.4f}:{east:.4f}"
        except Exception:
            pass

    write_insarhub_config(folder, {"analyzer": {"type": req.analyzer_type, "config": az_defaults}})
    return {"ok": True, "analyzer": req.analyzer_type}


@router.get("/api/analyzer-steps")
async def get_analyzer_steps(analyzer_type: str):
    """Return the list of steps for the analyzer.

    MintPy-family analyzers expose their per-step workflow (prep_data + named
    MintPy steps + plot). Self-contained analyzers (GMTSAR_SBAS,
    ISCE3_Dolphin_PL) invert in one shot via run(), so they expose a single
    'sbas' step.
    """
    cls = Analyzer._registry.get(analyzer_type)
    if cls is None:
        raise HTTPException(status_code=400, detail=f"Unknown analyzer: {analyzer_type}")
    from insarhub.analyzer.mintpy_base import Mintpy_SBAS_Base_Analyzer
    if issubclass(cls, Mintpy_SBAS_Base_Analyzer):
        steps = (['prep_data'] if hasattr(cls, 'prep_data') else []) + _MINTPY_STEPS
    elif hasattr(cls, 'run'):
        steps = ['sbas']
    else:
        steps = []
    return {"steps": steps}


@router.post("/api/folder-run-analyzer")
async def folder_run_analyzer(req: RunAnalyzerRequest, background_tasks: BackgroundTasks):
    return state.launch_job(background_tasks, _run_analyzer, req, start_message="Starting analyzer…")


async def _run_analyzer(job_id: str, req: RunAnalyzerRequest):
    def run(stop_ev):
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

            # Load config: merge insarhub_config.json analyzer section with in-memory overrides
            insarhub_cfg = read_insarhub_config(folder)
            saved_on_disk = insarhub_cfg.get("analyzer", {}).get("config", {})
            in_memory = state._settings.get("analyzer_configs", {}).get(req.analyzer_type, {})
            merged = {**in_memory, **saved_on_disk}  # disk (folder-specific) overrides in-memory globals
            valid_keys = {f.name for f in dataclasses.fields(config_cls)}
            init_kwargs: dict = {k: v for k, v in merged.items() if k in valid_keys}
            init_kwargs["workdir"] = folder
            # container is a runtime-only flag: it is deliberately excluded from
            # the saved analyzer config (below), so it never comes back through
            # `merged` -- it has to be taken from the run request each time,
            # mirroring how the processor submit passes it and the CLI's
            # --container behaves ("not remembered between runs").
            if getattr(req, "container", None) and "container" in valid_keys:
                init_kwargs["container"] = req.container

            # Auto-bind AOI from insarhub_config.json downloader section →
            # network_aoiLALO. Only for MintPy-based analyzers: network_aoiLALO
            # lives on Mintpy_SBAS_Base_Config, so standalone configs
            # (ISCE3_Dolphin_PL, GMTSAR_SBAS) don't have the field and passing it
            # to their __init__ would raise.
            if "network_aoiLALO" in valid_keys and \
                    init_kwargs.get("network_aoiLALO", "auto") in ("auto", "", None):
                try:
                    from insarhub.utils.pair_quality._geom import _wkt_bbox
                    wkt = insarhub_cfg.get("downloader", {}).get("config", {}).get("intersectsWith")
                    if wkt:
                        west, south, east, north = _wkt_bbox(wkt)
                        init_kwargs["network_aoiLALO"] = f"{south}:{north},{west}:{east}"
                except Exception:
                    pass

            cfg = config_cls(**init_kwargs)
            analyzer = cls(cfg)

            # Save updated analyzer config back to insarhub_config.json
            cfg_dict = state._cfg_dict(cfg, exclude=('workdir', 'container'))
            write_insarhub_config(folder, {"analyzer": {"type": req.analyzer_type, "config": cfg_dict}})

            # Use the analyzer's own cfg_path (e.g. ISCE2_Mintpy_SBAS writes to mintpy/.mintpy.cfg)
            # In container mode the .mintpy.cfg is written INSIDE the container
            # (prep_data runs there and resolves mintpy.load.*); the host config
            # object here still has the unresolved `auto` paths, so writing it
            # from the host would clobber the container's resolved file. Skip it.
            cfg_path = getattr(analyzer, "cfg_path", None) or (folder / ".mintpy.cfg")
            if not getattr(cfg, "container", None) and hasattr(cfg, "write_mintpy_config"):
                cfg_path.parent.mkdir(parents=True, exist_ok=True)
                cfg.write_mintpy_config(cfg_path)

            # Self-contained analyzers (GMTSAR_SBAS, ISCE3_Dolphin_PL): run() is
            # the whole pipeline -- it handles prep + --container re-invocation
            # internally. No per-step loop, no .mintpy.cfg, no plot().
            from insarhub.analyzer.mintpy_base import Mintpy_SBAS_Base_Analyzer
            if not issubclass(cls, Mintpy_SBAS_Base_Analyzer):
                update("Running analysis…", 0)
                try:
                    analyzer.run()
                except Exception as e:
                    update(f"ERROR: {e}", 0)
                    state._jobs[job_id]["status"] = "error"
                    return
                update("Finished", 100)
                state._jobs[job_id]["status"] = "done"
                state._jobs[job_id]["progress"] = 100
                return

            # HPC mode: submit everything as a single sbatch job instead of running locally
            if getattr(cfg, "hpc_mode", False) and hasattr(analyzer, "submit_hpc"):
                update("Submitting HPC job…", 0)
                slurm_job_id = analyzer.submit_hpc(steps=req.steps or None)
                if slurm_job_id is None:
                    sbatch_path = folder / "sbatch_options.json"
                    state._finish_job(
                        job_id, status="error", progress=0,
                        message=f"No sbatch_options.json found — created a default at "
                                f"{sbatch_path}. Review the resource settings for step "
                                f"\"17\" (SBAS), then submit again.")
                    return
                state._finish_job(job_id, status="done", progress=100,
                                  message=f"HPC job submitted: {slurm_job_id}")
                return

            # 'plot' isn't a real MintPy step (TimeSeriesAnalysis.run() would
            # silently ignore it) -- it's handled as a standalone call to
            # analyzer.plot() below. Auto-add it when more than one real
            # MintPy step was requested and the user didn't already pick it
            # explicitly, mirroring MintPy's own "a bulk run auto-plots"
            # semantics (run()'s own internal version of this check never
            # fires here since steps execute one at a time for per-step
            # progress reporting).
            steps_to_run = list(req.steps)
            real_mintpy_steps = [s for s in req.steps if s not in ('prep_data', 'plot')]
            if 'plot' not in steps_to_run and len(real_mintpy_steps) > 1:
                steps_to_run.append('plot')

            total = len(steps_to_run)
            completed = 0
            for i, step in enumerate(steps_to_run):
                if stop_ev.is_set():
                    update(f"[stopped] Cancelled before {step}", int(i / total * 100))
                    break

                update(f"[{i+1}/{total}] {step} — running…", int(i / total * 100))
                try:
                    if step == 'prep_data':
                        analyzer.prep_data()
                        # Persist load paths set by _set_load_parameters() so the
                        # next run (e.g. load_data only) can read them from disk.
                        _post_cfg = state._cfg_dict(analyzer.config, exclude=('workdir', 'container'))
                        write_insarhub_config(folder, {"analyzer": {"type": req.analyzer_type, "config": _post_cfg}})
                        _acfg_path = getattr(analyzer, "cfg_path", None) or (folder / ".mintpy.cfg")
                        # Container mode wrote the resolved .mintpy.cfg inside the
                        # container; the host config still holds `auto`, so don't
                        # overwrite it here (see the pre-loop write above).
                        if not getattr(analyzer.config, "container", None) and hasattr(analyzer.config, "write_mintpy_config"):
                            _acfg_path.parent.mkdir(parents=True, exist_ok=True)
                            analyzer.config.write_mintpy_config(_acfg_path)
                    elif step == 'modify_network':
                        # For geocoded (UTM) stacks, convert aoiLALO → aoiYX
                        # because MintPy can't do the projection itself.
                        _resolve_aoi_yx(analyzer.config, folder)
                        _acfg_path = getattr(analyzer, "cfg_path", None) or (folder / ".mintpy.cfg")
                        # Container mode wrote the resolved .mintpy.cfg inside the
                        # container; the host config still holds `auto`, so don't
                        # overwrite it here (see the pre-loop write above).
                        if not getattr(analyzer.config, "container", None) and hasattr(analyzer.config, "write_mintpy_config"):
                            _acfg_path.parent.mkdir(parents=True, exist_ok=True)
                            analyzer.config.write_mintpy_config(_acfg_path)
                        analyzer.run(steps=[step])
                    elif step == 'plot':
                        analyzer.plot()
                    else:
                        analyzer.run(steps=[step])
                    update(f"[{i+1}/{total}] {step} — done", int((i+1) / total * 100))
                    completed += 1
                except Exception as e:
                    update(f"[{i+1}/{total}] {step} — ERROR: {e}", int(i / total * 100))
                    state._jobs[job_id]["status"] = "error"
                    return

            if stop_ev.is_set():
                state._jobs[job_id]["status"] = "done"
            else:
                update(f"─── Finished {completed}/{total} step(s) ───", 100)
                state._jobs[job_id]["status"] = "done"
                state._jobs[job_id]["progress"] = 100

        except Exception as e:
            log.append(f"FATAL: {e}")
            _finish_job(job_id, status="error", progress=0, message="\n".join(log))

    with state.stop_event(job_id) as stop_ev:
        await asyncio.to_thread(run, stop_ev)


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
