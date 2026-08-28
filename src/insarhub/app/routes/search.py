# -*- coding: utf-8 -*-
"""Scene search, download, and AOI parsing endpoints."""

import asyncio
import base64
import dataclasses
import logging
import os
import tempfile
import threading as _threading
import uuid
from pathlib import Path

import geopandas as gpd
from shapely import wkt as shapely_wkt
from shapely.geometry import shape as _shape
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

import insarhub.app.state as state
from insarhub.app.models import (
    AddJobRequest, AddMergedJobRequest, DownloadMergedRequest,
    DownloadSceneRequest, JobResponse, ParseAoiRequest, SearchRequest,
)
from insarhub.app.state import _apply_config_from_dict, _new_job, _finish_job, write_insarhub_config
from insarhub.commands.downloader import DownloadScenesCommand, SearchCommand
from insarhub.config import S1_SLC_Config
from insarhub.config.paths import StackPaths
from insarhub.core.registry import Downloader
from insarhub.downloader.s1_burst import S1_Burst

router = APIRouter()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_burst(downloader_type: str) -> bool:
    """True for the burst-based S1 downloader, whose results have no frame."""
    return downloader_type == "S1_Burst"


def _download_workers(cfg) -> int:
    """Download concurrency for a request, from the downloader's own config.

    Applies to EVERY downloader, not just bursts. This was previously passed
    only on the S1_Burst branch, so a GUI-set max_workers had no effect on
    S1_SLC -- the download fell back to its built-in 3 regardless.

    There is deliberately no global fallback: ``max_workers`` lives on
    ``ASF_Base_Config`` so every ASF downloader carries its own, and the two
    parallelise different work -- S1_SLC opens concurrent HTTP transfers,
    S1_Burst assembles whole dates via burst2safe. A single shared number
    could not suit both.
    """
    return max(1, int(getattr(cfg, "max_workers", None) or 3))


def _burst_full_burst_id(path: int, subswath: str | None,
                         burst_id: int | None) -> str | None:
    """ASF ``fullBurstID`` for a GUI-selected burst position.

    The GUI selects bursts by (path, subswath, relativeBurstID) — the same
    triple that names a burst stack. ASF identifies a burst position by its
    ``fullBurstID`` = ``{path:03d}_{relativeBurstID}_{subswath}`` (e.g.
    ``056_118969_IW2``), which is what the ASF search filter expects. Without
    this, a burst download re-searched the whole AOI and pulled every adjacent
    burst position, not just the ones the user checked.
    """
    if not subswath or burst_id is None:
        return None
    return f"{int(path):03d}_{int(burst_id)}_{str(subswath).upper()}"


def _apply_burst_ids(base_cfg: dict, fids: list[str | None]) -> None:
    """Restrict a burst search to exactly the burst positions the user selected.

    ``fullBurstID`` accepts a list, so a whole multi-burst selection is one
    filter rather than one search per burst.

    The AOI is deliberately KEPT. An earlier version of this dropped it, on the
    stated grounds that ASF returns nothing when ``fullBurstID`` is combined
    with ``intersectsWith``. That is false. Measured against the live API
    (track 56, IW2, three consecutive burst positions, 2024-01-01..2024-03-01)::

        3x fullBurstID, no AOI    ->  15 granules
        3x fullBurstID + AOI      ->  15 granules      (identical)

    The AND costs nothing — every selected burst came out of an AOI search in
    the first place, so its footprint necessarily intersects that AOI — while
    dropping it left ``intersectsWith: null`` in the folder's
    insarhub_config.json, leaving ISCE3_Burst with no AOI to crop to, size a
    DEM from, or bound a stack with.

    ``flightDirection`` is kept for the same reason (AOI + track + direction
    and AOI + track both return 65; a track has one direction anyway).

    What genuinely does return zero is ``beamSwath``: ASF leaves it empty on
    SLC-BURST products, so any value at all matches nothing (AOI alone -> 13
    granules, AOI + ``beamSwath=IW2`` -> 0). It is therefore not offered as a
    burst search filter; subswath selection is client-side, via
    ``config.swaths`` in :meth:`S1_Burst._group_by_date`.
    """
    fids = [f for f in fids if f]
    if not fids:
        return
    base_cfg["fullBurstID"] = fids if len(fids) > 1 else fids[0]


def _apply_burst_selection(base_cfg: dict, relative_orbit: int,
                           subswath: str | None, burst_id: int | None) -> None:
    """Restrict a burst search to one exact burst position (see _apply_burst_ids)."""
    _apply_burst_ids(base_cfg,
                     [_burst_full_burst_id(relative_orbit, subswath, burst_id)])


# Settings downloader_config is meant to carry *assembly / download* options.
# The settings form renders the base search-filter fields (dataset, beamMode,
# processingLevel, polarization, beamSwath, flightDirection, relativeOrbit,
# fullBurstID, ...) in its "Dataset" / "SAR Parameters" / "Orbit & Frame" /
# "Burst IDs" groups. Those are S1_SLC-flavoured identity + per-search filters
# and must NOT be inherited into a merged per-stack burst search: the settings
# form may be showing S1_SLC (the app's default downloader) even while the
# request is an S1_Burst download, and its `dataset=SENTINEL-1`,
# `processingLevel=SLC`, `polarization=['VV','VV+VH']` would overwrite the
# burst defaults and make ASF return empty results ("all stack searches
# failed"). Only burst-ASSEMBLY options are safe to inherit.
_BURST_GLOBAL_CONFIG_FIELDS = {
    "swaths", "mode", "min_bursts", "all_anns", "keep_files", "max_workers",
}

# Config that controls HOW a download runs rather than WHAT it searches for.
# These live on ASF_Base_Config, mean the same thing for every downloader, and
# so are safe to carry across a downloader-type mismatch.
_EXECUTION_CONFIG_FIELDS = {"max_workers", "ssl_verify"}


def _apply_global_downloader_config(cfg, downloader_type: str) -> None:
    """Apply the in-memory global downloader config to a freshly built config.

    The global ``downloader_config`` belongs to whatever downloader is selected
    in the settings panel. Applying it unconditionally lets an S1_SLC config
    (``dataset=SENTINEL-1``, ``polarization=['VV','VV+VH']``, ...) silently
    pollute an S1_Burst config and turn the burst search into an SLC search —
    which is exactly what produced SLC granule names being fed to burst2safe.
    Only apply it when it belongs to the same downloader type, and for the burst
    downloader only carry over assembly/download options (see
    ``_BURST_GLOBAL_CONFIG_FIELDS``) — the per-search filters come from the
    request's stacks, never from the settings form.
    """
    global_cfg = state._settings.get("downloader_config", {}) or {}

    if state._settings.get("downloader") != downloader_type:
        # The saved form belongs to a DIFFERENT downloader, so its search
        # filters must not leak into this run. Execution settings are not
        # search filters, though, and they are meaningful for every ASF
        # downloader -- dropping them here is why a GUI-set max_workers was
        # silently ignored whenever the settings form was left on S1_SLC
        # (which is also the state after every backend restart, since
        # _settings is in-memory only).
        global_cfg = {k: v for k, v in global_cfg.items()
                      if k in _EXECUTION_CONFIG_FIELDS}
    elif _is_burst(downloader_type):
        global_cfg = {k: v for k, v in global_cfg.items()
                      if k in _BURST_GLOBAL_CONFIG_FIELDS}
    _apply_config_from_dict(cfg, global_cfg, skip_keys={"workdir"})


def _burst_job_dir(workdir: Path, path: int, stacks: list,
                   single_subswath: str | None = None,
                   single_burst_id: int | None = None) -> Path:
    """Job-folder for a burst selection: per-burst when exactly one burst is
    chosen, per-track otherwise (see S1_Burst.folder_name)."""
    if len(stacks) == 1:
        s = stacks[0]
        return workdir / S1_Burst.folder_name(
            path, getattr(s, "subswath", None) or single_subswath,
            getattr(s, "burst_id", None) if getattr(s, "burst_id", None) is not None else single_burst_id)
    return workdir / S1_Burst.folder_name(path)

def _nisar_add_attr(umm: dict, name: str):
    for a in (umm or {}).get("AdditionalAttributes", []) or []:
        if isinstance(a, dict) and a.get("Name") == name:
            vals = a.get("Values") or []
            return vals[0] if vals else None
    return None


def _enrich_nisar_props(scene, props: dict) -> None:
    """Fill scene-detail fields ASF leaves empty on NISAR products.

    ASF's NISAR mapping keeps ``bytes`` as a per-file dict (not an int), drops
    the MD5 (it lives in ``ArchiveAndDistributionInformation``), and has no
    CENTER_LAT/LON, GRANULE_TYPE or BEAM_MODE attributes. Surface the main
    product's size/MD5, the footprint centroid, the product-type description,
    and the range bandwidth (NISAR's resolution/mode discriminator) so the
    scene-detail panel shows real values instead of "—" / NaN.
    """
    fname = props.get("fileName")
    raw_bytes = props.get("bytes")
    umm = getattr(scene, "umm", None) or {}

    # Main product size + MD5 from the CMR archive entries.
    checksums = {}
    archive = (umm.get("DataGranule", {}).get("ArchiveAndDistributionInformation") or [])
    for entry in archive:
        if not isinstance(entry, dict):
            continue
        cs = entry.get("Checksum") or {}
        if isinstance(cs, dict) and cs.get("Algorithm") == "MD5":
            checksums[entry.get("Name")] = cs.get("Value")

    if isinstance(raw_bytes, dict):
        entry = raw_bytes.get(fname) if fname else None
        if isinstance(entry, dict) and isinstance(entry.get("bytes"), (int, float)):
            props["bytes"] = int(entry["bytes"])
        else:
            props["bytes"] = sum(
                v.get("bytes", 0) for v in raw_bytes.values()
                if isinstance(v, dict) and isinstance(v.get("bytes"), (int, float))) or None
        if fname:
            props["md5sum"] = checksums.get(fname)

    # Footprint centroid → center lat/lon (NISAR has no CENTER_* attributes).
    if props.get("centerLat") is None or props.get("centerLon") is None:
        try:
            centroid = _shape(scene.geometry).centroid
            if props.get("centerLat") is None:
                props["centerLat"] = centroid.y
            if props.get("centerLon") is None:
                props["centerLon"] = centroid.x
        except Exception:
            pass

    # Granule type: NISAR carries PRODUCT_TYPE_DESC ("Geocoded Single Look Complex").
    if not props.get("granuleType"):
        props["granuleType"] = _nisar_add_attr(umm, "PRODUCT_TYPE_DESC") or props.get("processingLevel")

    # Beam mode: NISAR has no beam-mode concept; surface the range bandwidth
    # (e.g. "40+5") as the closest "acquisition mode" discriminator.
    if not props.get("beamModeType"):
        rb = props.get("rangeBandwidth")
        if isinstance(rb, (list, tuple)):
            props["beamModeType"] = "+".join(str(x) for x in rb) or None
        elif rb:
            props["beamModeType"] = str(rb)

    # s3Urls: show only the main product's direct-access URI, not every
    # browse/private/QA side file ASF lists.
    if isinstance(props.get("s3Urls"), list) and fname:
        props["s3Urls"] = [u for u in props["s3Urls"] if isinstance(u, str) and u.endswith(fname)]


def _to_geojson(results: dict) -> dict:
    features = []
    for stack_key, scenes in results.items():
        for scene in scenes:
            try:
                props = dict(scene.properties)
                # NISAR: ASF leaves `polarization` empty (it's carried in the
                # granule name, e.g. ..._4005_DHDH_A_...) and has no beamMode
                # concept -- so the scene detail showed "--". Fill polarization
                # from the name (field 10) and surface frameCoverage so the
                # detail panel has real values.
                _name = props.get("sceneName") or props.get("fileID") or ""
                if _name.startswith("NISAR"):
                    _parts = _name.split("_")
                    if not props.get("polarization") and len(_parts) > 9:
                        props["polarization"] = _parts[9]   # DHDH / SHSH / DVDV …
                    _enrich_nisar_props(scene, props)
                # ASF nests burst identity under properties['burst']; flatten
                # it so the frontend can render a burst stack (path, subswath,
                # burst index) without parsing the "_stack" tuple string.
                burst = props.get("burst") or {}
                if burst:
                    props.setdefault("fullBurstID", burst.get("fullBurstID"))
                    props.setdefault("relativeBurstID", burst.get("relativeBurstID"))
                    props.setdefault("absoluteBurstID", burst.get("absoluteBurstID"))
                    props.setdefault("subswath", burst.get("subswath"))
                    props.setdefault("burstIndex", burst.get("burstIndex"))
                features.append({
                    "type": "Feature",
                    "geometry":   scene.geometry,
                    "properties": {**props, "_stack": str(stack_key)},
                })
            except Exception:
                continue
    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/downloader-schema")
async def downloader_schema(downloaderType: str = "S1_SLC"):
    """Return the extra search-filter fields the given downloader declares.

    The frontend renders "Additional Filters" / "Path and Frame Filters"
    generically from this list instead of hardcoding one downloader's fields —
    every downloader gets AOI/date/maxResults/granule-names for free and can
    layer on whatever else (platform, path/frame range, ...) makes sense for it.
    """
    dl_cls = Downloader._registry.get(downloaderType)
    if dl_cls is None:
        raise HTTPException(status_code=404, detail=f"Unknown downloader: {downloaderType}")
    return {"fields": getattr(dl_cls, "search_filter_schema", [])}


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
    return state.launch_job(background_tasks, _run_search, str(uuid.uuid4()), req,
                            start_message="Starting search...")


async def _run_search(job_id: str, session_id: str, req: SearchRequest):
    def run():
        try:
            dl_cls = Downloader._registry.get(req.downloaderType)
            if dl_cls is None:
                _finish_job(job_id, status="error", progress=0,
                            message=f"Unknown downloader: {req.downloaderType}")
                return
            cfg_cls = getattr(dl_cls, "default_config", S1_SLC_Config)

            if req.granule_names:
                config = cfg_cls(workdir=req.workdir)
                _apply_config_from_dict(config, {"granule_names": req.granule_names}, skip_keys={"workdir"})
            else:
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

                config = cfg_cls(workdir=req.workdir)
                # Universal fields every downloader gets for free; anything else
                # (platform, path/frame range, ...) comes through req.overrides,
                # sourced from that downloader's own search_filter_schema.
                _apply_config_from_dict(config, {
                    "intersectsWith": intersects_with,
                    "start":          req.start,
                    "end":            req.end,
                    "maxResults":     req.maxResults,
                    "beamMode":       req.beamMode or None,
                    "polarization":   req.polarization or None,
                }, skip_keys={"workdir"})
                if req.overrides:
                    _apply_config_from_dict(config, req.overrides, skip_keys={"workdir"})

            downloader = Downloader.create(req.downloaderType, config)

            cmd    = SearchCommand(downloader, progress_callback=state._make_progress(job_id))
            result = cmd.run()

            if result.success:
                state._sessions[session_id] = downloader
                _finish_job(job_id, status="done", message=result.message, data={
                    "session_id": session_id,
                    "geojson":    _to_geojson(result.data),
                    "summary":    result.message,
                })
            elif "does not return any result" in (result.message or "").lower() \
                    or "no result" in (result.message or "").lower():
                # A valid search that simply matched nothing is NOT an error --
                # return an empty result (0 stacks) so the panel refreshes to
                # empty instead of leaving the previous search's footprints up.
                state._sessions[session_id] = downloader
                _finish_job(job_id, status="done", message="0 stacks found", data={
                    "session_id": session_id,
                    "geojson":    {"type": "FeatureCollection", "features": []},
                    "summary":    "0 stacks found",
                })
            else:
                _finish_job(job_id, status="error", progress=0, message=result.message)
        except Exception as e:
            _finish_job(job_id, status="error", progress=0, message=str(e))

    await asyncio.to_thread(run)


@router.post("/api/download-scene", response_model=JobResponse)
async def download_single_scene(req: DownloadSceneRequest, background_tasks: BackgroundTasks):
    return state.launch_job(background_tasks, _run_download_scene, req,
                            start_message="Starting download...")


async def _run_download_scene(job_id: str, req: DownloadSceneRequest):
    def run(stop_ev):
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
                        _finish_job(job_id, status="done", progress=0, message="Stopped.")
                        return
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_bytes:
                            pct = int(downloaded / total_bytes * 100)
                            state._jobs[job_id]["progress"] = pct
                            state._jobs[job_id]["message"]  = f"Downloading {filename}… {pct}%"

            _finish_job(job_id, status="done", message=f"Saved {filename}", data=str(file_path))
        except InterruptedError:
            _finish_job(job_id, status="done", progress=0, message="Stopped.")
        except Exception as e:
            if file_path and file_path.exists():
                file_path.unlink(missing_ok=True)
            _finish_job(job_id, status="error", progress=0, message=str(e))

    with state.stop_event(job_id) as stop_ev:
        await asyncio.to_thread(run, stop_ev)


# ---------------------------------------------------------------------------
# DEPRECATED — download-from-the-map endpoints.
#
# /api/download-stack, /api/download-orbit-stack and /api/download-merged used
# to back "Download SLC/Burst + Orbit" buttons in the stack panels. Those
# buttons are gone: the map selects, and downloading happens from the job folder
# (/api/folder-download, /api/folder-download-orbit) so there is exactly one
# path that re-searches, writes insarhub_config.json and reports progress.
#
# Nothing in the frontend calls these any more. They are kept for API
# compatibility with external scripts; do not wire new UI to them. Any new
# downloader gets the folder flow for free and needs no per-downloader work
# here.
# ---------------------------------------------------------------------------

@router.post("/api/download-stack", response_model=JobResponse)
async def download_stack(req: AddJobRequest, background_tasks: BackgroundTasks):
    job_id, _ = _new_job("Starting…")
    stop_ev = _threading.Event()
    state._stop_events[job_id] = stop_ev
    background_tasks.add_task(_run_download_stack, job_id, req, stop_ev)
    return {"job_id": job_id}


async def _run_download_stack(job_id: str, req: AddJobRequest, stop_ev: _threading.Event):
    def run():
        try:
            workdir = Path(req.workdir).expanduser().resolve()
            workdir.mkdir(parents=True, exist_ok=True)
            dl_cls  = Downloader._registry.get(req.downloaderType)
            cfg_cls = getattr(dl_cls, "default_config", S1_SLC_Config) if dl_cls else S1_SLC_Config
            cfg = cfg_cls(workdir=workdir)
            _apply_global_downloader_config(cfg, req.downloaderType)
            base_cfg = {
                "start": req.start, "end": req.end,
                "relativeOrbit": req.relativeOrbit,
                "intersectsWith": req.wkt, "flightDirection": req.flightDirection,
                "platform": req.platform,
            }
            if _is_burst(req.downloaderType):
                _apply_burst_selection(base_cfg, req.relativeOrbit,
                                       req.subswath, req.burst_id)
            else:
                base_cfg["frame"] = req.frame
            _apply_config_from_dict(cfg, base_cfg)

            downloader    = Downloader.create(req.downloaderType, cfg)
            state._jobs[job_id]["message"] = "Searching scenes…"
            search_result = SearchCommand(downloader).run()
            if not search_result.success:
                _finish_job(job_id, status="error", progress=0, message=search_result.message)
                return
            if stop_ev.is_set():
                _finish_job(job_id, status="done", progress=0, message="Stopped.")
                return

            total = sum(len(v) for v in downloader.results.values())
            state._jobs[job_id]["message"] = f"Downloading 0/{total}"

            if _is_burst(req.downloaderType):
                # SAFEs assemble into <folder>/slc, mirroring S1_SLC's layout.
                save_dir   = _burst_job_dir(workdir, req.relativeOrbit, [req],
                                            req.subswath, req.burst_id)
                dl_kwargs  = dict(stop_event=stop_ev,
                                  on_progress=state._make_download_progress(job_id),
                                  save_path=str(save_dir / "slc"),
                                  max_workers=_download_workers(cfg))
            else:
                save_dir  = workdir / f"p{req.relativeOrbit}_f{req.frame}"
                dl_kwargs = dict(stop_event=stop_ev,
                                 on_progress=state._make_download_progress(job_id),
                                 max_workers=_download_workers(cfg))

            dl_result = DownloadScenesCommand(downloader, **dl_kwargs).run()
            if stop_ev.is_set():
                _finish_job(job_id, status="done", progress=0, message="Stopped.")
            else:
                _finish_job(job_id, status="done" if dl_result.success else "error",
                            message=dl_result.message, data=str(save_dir))
        except Exception as e:
            _finish_job(job_id, status="error", progress=0, message=str(e))
        finally:
            state._stop_events.pop(job_id, None)

    await asyncio.to_thread(run)


@router.post("/api/add-job")
async def add_job(req: AddJobRequest):
    workdir = Path(req.workdir).expanduser().resolve()
    if _is_burst(req.downloaderType):
        # Bursts have no frame: the folder is per-burst position (or per track
        # when only the path is known) instead of p<path>_f<frame>.
        subdir = _burst_job_dir(workdir, req.relativeOrbit, [req], req.subswath, req.burst_id)
    else:
        subdir = StackPaths(workdir).stack_dir(req.relativeOrbit, req.frame)
    subdir.mkdir(parents=True, exist_ok=True)

    dl_cls      = Downloader._registry.get(req.downloaderType)
    cfg_cls     = getattr(dl_cls, "default_config", S1_SLC_Config) if dl_cls else S1_SLC_Config
    cfg_instance = cfg_cls(workdir=subdir)
    _apply_global_downloader_config(cfg_instance, req.downloaderType)
    base_cfg = {
        "start": req.start, "end": req.end,
        "relativeOrbit": req.relativeOrbit,
        "intersectsWith": req.wkt, "flightDirection": req.flightDirection,
        "platform": req.platform,
    }
    if _is_burst(req.downloaderType):
        _apply_burst_selection(base_cfg, req.relativeOrbit,
                               req.subswath, req.burst_id)
    else:
        base_cfg["frame"] = req.frame
    _apply_config_from_dict(cfg_instance, base_cfg)
    cfg = state._cfg_dict(cfg_instance)
    write_insarhub_config(subdir, {"downloader": {"type": req.downloaderType, "config": cfg}})
    return {"path": str(subdir), "name": subdir.name}


@router.post("/api/add-merged-job")
async def add_merged_job(req: AddMergedJobRequest):
    """Create one job folder for multiple frames sharing a relative orbit (path),
    ready for search/select-pairs/download with merge=True from within it —
    same lightweight "just create the folder + config" semantics as add_job,
    just for a merge group instead of one stack."""
    if not req.stacks:
        raise HTTPException(status_code=422, detail="Provide at least one stack")
    paths = {s.relativeOrbit for s in req.stacks}
    if len(paths) > 1:
        raise HTTPException(
            status_code=422,
            detail=f"All stacks must share one relative orbit (path), got {sorted(paths)}. "
                   f"Different tracks have unrelated viewing geometry and cannot be merged.",
        )
    path    = next(iter(paths))
    frames  = [s.frame for s in req.stacks]
    workdir = Path(req.workdir).expanduser().resolve()
    # A single selected frame is NOT a merge: name it p<path>_f<frame> exactly
    # like add_job, so the folder (and its later download) never gets the
    # p<path>_merged_f... layout. merge_dir / merge=True naming is only
    # meaningful for 2+ frames sharing one path.
    single_frame = (not _is_burst(req.downloaderType)) and len(req.stacks) == 1
    if _is_burst(req.downloaderType):
        # One burst -> per-burst folder; several -> one per-track folder
        # (SAFEs assemble per date+path, the unit ISCE3_Burst consumes).
        subdir = _burst_job_dir(workdir, path, req.stacks)
    elif single_frame:
        subdir = StackPaths(workdir).stack_dir(path, frames[0])
    else:
        subdir = StackPaths(workdir).merge_dir(path, frames)
    subdir.mkdir(parents=True, exist_ok=True)

    dl_cls       = Downloader._registry.get(req.downloaderType)
    cfg_cls      = getattr(dl_cls, "default_config", S1_SLC_Config) if dl_cls else S1_SLC_Config
    cfg_instance = cfg_cls(workdir=subdir)
    _apply_global_downloader_config(cfg_instance, req.downloaderType)
    first = req.stacks[0]
    merged_cfg = {
        "start": min(s.start for s in req.stacks),
        "end":   max(s.end for s in req.stacks),
        "relativeOrbit": path,
        # frame intentionally omitted — spans multiple; select_pairs(merge=True)
        # resolves the real frame set from the search itself.
        "intersectsWith": first.wkt,
        "flightDirection": first.flightDirection,
        # platform intentionally omitted — a merged track can span a satellite
        # handover (e.g. Sentinel-1C → Sentinel-1D); using only the first
        # selected stack's platform would silently restrict every future
        # re-search on this folder to one satellite and starve the network.
    }
    if single_frame:
        # One frame -> a normal single-stack config: pin the frame so re-search
        # and download stay per-frame (merge=False) and match the p<path>_f<frame>
        # folder created above.
        merged_cfg["frame"] = frames[0]
    if _is_burst(req.downloaderType):
        # Persist the exact burst positions the user selected so the folder's
        # re-search (select_pairs / download) stays limited to them instead of
        # re-searching the whole AOI, while keeping the AOI itself for the
        # processor to crop/size against. See _apply_burst_ids.
        _apply_burst_ids(merged_cfg,
                         [_burst_full_burst_id(s.relativeOrbit, s.subswath, s.burst_id)
                          for s in req.stacks])
    _apply_config_from_dict(cfg_instance, merged_cfg)
    cfg = state._cfg_dict(cfg_instance)
    write_insarhub_config(subdir, {"downloader": {"type": req.downloaderType, "config": cfg}})
    return {"path": str(subdir), "name": subdir.name}


@router.post("/api/download-orbit-stack", response_model=JobResponse)
async def download_orbit_stack(req: AddJobRequest, background_tasks: BackgroundTasks):
    return state.launch_job(background_tasks, _run_download_orbit_stack, req,
                            start_message="Starting orbit download…")


async def _run_download_orbit_stack(job_id: str, req: AddJobRequest):
    def run(stop_ev):
        try:
            workdir  = Path(req.workdir).expanduser().resolve()
            if _is_burst(req.downloaderType):
                save_dir = _burst_job_dir(workdir, req.relativeOrbit, [req],
                                          req.subswath, req.burst_id)
            else:
                save_dir = workdir / f"p{req.relativeOrbit}_f{req.frame}"
            save_dir.mkdir(parents=True, exist_ok=True)
            dl_cls  = Downloader._registry.get(req.downloaderType)
            cfg_cls = getattr(dl_cls, "default_config", S1_SLC_Config) if dl_cls else S1_SLC_Config
            cfg = cfg_cls(workdir=save_dir)
            _apply_global_downloader_config(cfg, req.downloaderType)
            base_cfg = {
                "start": req.start, "end": req.end,
                "relativeOrbit": req.relativeOrbit,
                "intersectsWith": req.wkt, "flightDirection": req.flightDirection,
                "platform": req.platform,
            }
            if _is_burst(req.downloaderType):
                _apply_burst_selection(base_cfg, req.relativeOrbit,
                                       req.subswath, req.burst_id)
            else:
                base_cfg["frame"] = req.frame
            _apply_config_from_dict(cfg, base_cfg)

            downloader    = Downloader.create(req.downloaderType, cfg)
            state._jobs[job_id]["message"] = "Searching scenes…"
            search_result = SearchCommand(downloader, progress_callback=state._make_progress(job_id)).run()
            if not search_result.success:
                _finish_job(job_id, status="error", progress=0, message=search_result.message)
                return
            state._jobs[job_id]["message"] = "Downloading orbit files…"
            downloader.download_orbit(save_dir=str(save_dir), stop_event=stop_ev)
            if stop_ev.is_set():
                _finish_job(job_id, status="done", progress=0, message="Stopped.")
            else:
                _finish_job(job_id, status="done", message="Orbit files downloaded.")
        except Exception as e:
            _finish_job(job_id, status="error", progress=0, message=str(e))

    with state.stop_event(job_id) as stop_ev:
        await asyncio.to_thread(run, stop_ev)


@router.post("/api/download-merged", response_model=JobResponse)
async def download_merged(req: DownloadMergedRequest, background_tasks: BackgroundTasks):
    if not req.stacks:
        raise HTTPException(status_code=422, detail="Provide at least one stack")
    job_id, _ = _new_job("Starting merged download…")
    stop_ev = _threading.Event()
    state._stop_events[job_id] = stop_ev
    background_tasks.add_task(_run_download_merged, job_id, req, stop_ev)
    return {"job_id": job_id}


async def _run_download_merged(job_id: str, req: DownloadMergedRequest, stop_ev: _threading.Event):
    def run():
        try:
            workdir = Path(req.workdir).expanduser().resolve()

            all_downloaders = []
            search_errors: list[str] = []
            for i, spec in enumerate(req.stacks):
                if stop_ev.is_set():
                    _finish_job(job_id, status="done", progress=0, message="Stopped.")
                    return
                state._jobs[job_id]["message"] = (
                    f"Searching stack {i + 1}/{len(req.stacks)} "
                    f"(P{spec.relativeOrbit}/{('F' + str(spec.frame)) if spec.frame is not None else 'burst'})…"
                )
                dl_cls  = Downloader._registry.get(req.downloaderType)
                cfg_cls = getattr(dl_cls, "default_config", S1_SLC_Config) if dl_cls else S1_SLC_Config
                cfg = cfg_cls(workdir=workdir)
                _apply_global_downloader_config(cfg, req.downloaderType)
                base_cfg = {
                    "start": spec.start, "end": spec.end,
                    "relativeOrbit": spec.relativeOrbit,
                    "intersectsWith": spec.wkt, "flightDirection": spec.flightDirection,
                    # platform intentionally omitted — see add_merged_job for why
                    # (a frame's own scene history can span a satellite handover;
                    # spec.platform here is derived client-side from a single
                    # representative scene and must not gate the real search).
                }
                if _is_burst(req.downloaderType):
                    # Restrict the search to exactly the burst position(s) the
                    # user selected in the GUI — one fullBurstID per stack —
                    # instead of pulling every burst intersecting the AOI.
                    _apply_burst_selection(base_cfg, spec.relativeOrbit,
                                           spec.subswath, spec.burst_id)
                else:
                    base_cfg["frame"] = spec.frame
                _apply_config_from_dict(cfg, base_cfg)
                downloader    = Downloader.create(req.downloaderType, cfg)
                search_result = SearchCommand(downloader).run()
                if not search_result.success:
                    err = f"stack {i+1} (P{spec.relativeOrbit}, burst_id={spec.burst_id}, subswath={spec.subswath}): {search_result.message}"
                    logger.error("S1_Burst merged: %s", err)
                    search_errors.append(err)
                    state._jobs[job_id]["message"] += f" [search failed: {err}]"
                    continue
                all_downloaders.append(downloader)

            if not all_downloaders:
                _finish_job(
                    job_id, status="error", progress=0,
                    message="All stack searches failed: " + " | ".join(search_errors)
                            if search_errors else "All stack searches failed.")
                return

            # Merge all results into primary downloader
            primary = all_downloaders[0]
            for dl in all_downloaders[1:]:
                primary.results.update(dl.results)

            if _is_burst(req.downloaderType) and len(req.stacks) > 1:
                # The loop above searched ONE burst per downloader, so
                # primary.config carries only the first stack's fullBurstID --
                # but primary.results now holds every selected burst. S1_Burst
                # .download() persists insarhub_config.json from self.config
                # (_mark_stack_dir), so leaving it alone writes a folder config
                # that claims a single burst while the folder actually contains
                # several. Any later re-search from that folder (select_pairs,
                # processor reload) would then silently drop the rest.
                # Widen it to the full selection before the download writes it.
                _apply_config_from_dict(primary.config, {
                    "fullBurstID": [f for f in (
                        _burst_full_burst_id(s.relativeOrbit, s.subswath, s.burst_id)
                        for s in req.stacks) if f],
                })

            # Directory name must match what downloader.download(merge=True) itself
            # computes (path + every constituent frame number) — not a fixed
            # "merged" literal — so orbit files (saved explicitly below, bypassing
            # download_orbit()'s own merge-aware logic) land next to the SLCs.
            paths = {path for (path, _frame) in primary.active_results.keys()}
            if len(paths) > 1:
                _finish_job(
                    job_id, status="error", progress=0,
                    message=f"Merged download requires all stacks to share one "
                            f"relative orbit (path), got {sorted(paths)}.")
                return
            path = next(iter(paths))
            if _is_burst(req.downloaderType):
                # Bursts: one burst -> per-burst folder, several -> per-track
                # folder (SAFEs assemble per date+path).
                merged_dir = _burst_job_dir(workdir, path, req.stacks)
                frames     = []
            else:
                frames     = [frame for (_path, frame) in primary.active_results.keys()]
                merged_dir = workdir / f"p{path}_{StackPaths.merge_tag(frames)}"

            if req.download_slc and not stop_ev.is_set():
                total = sum(len(v) for v in primary.results.values())
                state._jobs[job_id]["message"] = f"Downloading 0/{total} scenes → {merged_dir.name}/"

                dl_kwargs = dict(
                    merge=True,
                    stop_event=stop_ev,
                    on_progress=state._make_download_progress(job_id),
                )
                dl_kwargs["max_workers"] = _download_workers(cfg)
                if _is_burst(req.downloaderType):
                    # S1_Burst assembles .SAFE into save_path (no per-stack
                    # subdir); put them under <folder>/slc like S1_SLC.
                    dl_kwargs["save_path"] = str(merged_dir / "slc")
                dl_result = DownloadScenesCommand(primary, **dl_kwargs).run()
                if not dl_result.success:
                    _finish_job(job_id, status="error", progress=0, message=dl_result.message)
                    return

            if req.download_orbit and not stop_ev.is_set():
                state._jobs[job_id]["message"] = f"Downloading orbit files → {merged_dir.name}/"
                primary.download_orbit(save_dir=str(merged_dir), stop_event=stop_ev)

            if stop_ev.is_set():
                _finish_job(job_id, status="done", progress=0, message="Stopped.")
            else:
                _finish_job(
                    job_id, status="done",
                    message=f"Merged download complete → {merged_dir.name}/",
                    data=str(merged_dir),
                )
        except Exception as e:
            _finish_job(job_id, status="error", progress=0, message=str(e))
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
