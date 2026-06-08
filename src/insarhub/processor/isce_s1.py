# -*- coding: utf-8 -*-
"""
ISCE_S1 — Sentinel-1 time-series InSAR processor backed by ISCE2 stackSentinel.

stackSentinel.py (part of ISCE2's topsStack contrib package) generates a set of
numbered run scripts from all SLCs in slc_dir, then executes them in order.
Each run script contains independent commands that InSARHub runs in parallel up
to max_workers.

Interface mirrors Hyp3Base:
  submit()  — generate run scripts with stackSentinel.py, start execution
  refresh() — read per-step status, print table
  retry()   — re-run failed step and everything after it
  watch()   — poll until all steps finish
  save()    — persist stack_jobs.json
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from colorama import Fore, Style

from insarhub.config import ISCE_S1_Config
from insarhub.config.paths import ISCEPaths, MintPyPaths
from insarhub.processor.isce_base import (
    ISCE_Base,
    _PENDING,
    _SUCCEEDED,
    _read_status,
    _write_status,
)

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"(\d{8})(?:T\d{6})?")

_MARGIN = 0.1  # degrees added around SLC footprint when auto-deriving bbox


def _bbox_from_slc_dir(slc_dir: Path) -> list[float] | None:
    """Scan SAFE dirs and zips in slc_dir, parse manifest.safe corner coords.

    Returns [S, N, W, E] with a small margin, or None if no manifests found.
    """
    import xml.etree.ElementTree as ET
    import zipfile

    all_lats: list[float] = []
    all_lons: list[float] = []

    def _parse_coords(text: str) -> None:
        # manifest.safe uses "lat,lon lat,lon …" (space-separated pairs)
        for token in text.split():
            parts = token.split(",")
            if len(parts) == 2:
                try:
                    all_lats.append(float(parts[0]))
                    all_lons.append(float(parts[1]))
                except ValueError:
                    pass

    def _read_manifest(xml_text: str) -> None:
        try:
            root = ET.fromstring(xml_text)
            for coords in root.iter("{http://www.opengis.net/gml}coordinates"):
                if coords.text:
                    _parse_coords(coords.text.strip())
        except ET.ParseError:
            pass

    for safe in slc_dir.glob("*.SAFE"):
        manifest = safe / "manifest.safe"
        if manifest.exists():
            try:
                _read_manifest(manifest.read_text())
            except Exception:
                pass

    for zf_path in slc_dir.glob("*.zip"):
        try:
            with zipfile.ZipFile(zf_path, "r") as zf:
                for name in zf.namelist():
                    if name.endswith("manifest.safe"):
                        _read_manifest(zf.read(name).decode())
        except Exception:
            pass

    if not all_lats:
        return None

    return [
        min(all_lats) - _MARGIN,
        max(all_lats) + _MARGIN,
        min(all_lons) - _MARGIN,
        max(all_lons) + _MARGIN,
    ]


def _parse_date(name: str) -> str:
    """Return YYYYMMDD from a date string or a Sentinel-1 scene/granule name."""
    name = str(name).strip()
    m = _DATE_RE.search(name)
    if m:
        return m.group(1)
    raise ValueError(f"Cannot extract YYYYMMDD from: {name!r}")


def _geotiff_to_isce_dem(tif_path: Path, out_dir: Path) -> Path:
    """Convert a GeoTIFF DEM to ISCE2 binary + XML format.

    dem_stitcher outputs GeoTIFF; ISCE2 needs a flat float32 binary file
    with a sidecar .xml written by isceobj.
    """
    import rasterio
    from isce.components.isceobj.Image import createDemImage  # type: ignore[import]

    dem_out = out_dir / "dem.wgs84"
    xml_out = out_dir / "dem.wgs84.xml"

    if dem_out.exists() and xml_out.exists():
        print(f"  Reusing converted DEM: {dem_out.name}")
        return dem_out

    print(f"  Converting GeoTIFF DEM → ISCE2 format: {tif_path.name} …")
    with rasterio.open(tif_path) as ds:
        arr = ds.read(1).astype(np.float32)
        t = ds.transform
        nodata = ds.nodata

    if nodata is not None:
        arr[arr == nodata] = -32768.0
    arr[np.isnan(arr)] = -32768.0

    arr.tofile(str(dem_out))

    height, width = arr.shape
    dem_img = createDemImage()
    dem_img.filename      = str(dem_out)
    dem_img.width         = width
    dem_img.length        = height
    dem_img.dataType      = "FLOAT"
    dem_img.scheme        = "BIL"
    dem_img.bands         = 1
    dem_img.accessMode    = "READ"
    dem_img.reference     = "WGS84"
    dem_img.setFirstLongitude(t.c + 0.5 * t.a)
    dem_img.setFirstLatitude(t.f  + 0.5 * t.e)
    dem_img.setDeltaLongitude(t.a)
    dem_img.setDeltaLatitude(t.e)
    dem_img.renderHdr()

    print(f"  DEM converted → {dem_out.name}  ({width}×{height} px)")
    return dem_out


def _prepare_dem(config: ISCE_S1_Config, workdir: Path) -> Path:
    """Return path to an ISCE2-format DEM, downloading GLO-30 if needed."""
    raw = config.dem_path
    dem_dir = ISCEPaths(workdir).dem_dir
    _auto = raw is None or str(raw).strip().lower() in ("", "none", "auto")
    _is_sentinel = (not _auto) and Path(str(raw)) == dem_dir

    if not _auto and not _is_sentinel:
        p = Path(str(raw))
        if p.suffix.lower() in (".tif", ".tiff"):
            return _geotiff_to_isce_dem(p, workdir)
        if p.is_dir():
            dem_dir = p
        elif p.is_file():
            return p
        # explicit path given but doesn't exist yet — fall through to download into dem_dir

    bbox: list[float] | None = (
        list(config.bbox) if config.bbox and len(config.bbox) == 4 else None
    )
    if bbox is None:
        for scan_dir in dict.fromkeys([  # ordered, deduplicated
            Path(str(config.slc_dir)) if config.slc_dir else None,
            workdir,
        ]):
            if scan_dir is None:
                continue
            bbox = _bbox_from_slc_dir(scan_dir)
            if bbox:
                print(
                    f"  Auto-derived bbox from SLCs in {scan_dir}: "
                    f"S={bbox[0]:.4f} N={bbox[1]:.4f} W={bbox[2]:.4f} E={bbox[3]:.4f}"
                )
                break

    if bbox is None:
        raise ValueError(
            "No bbox could be derived for DEM download. "
            "Provide --dem_path (ISCE2 binary DEM + .xml), --bbox 'S N W E', "
            "or ensure SLC .SAFE/.zip files are in slc_dir or workdir."
        )

    dem_dir.mkdir(parents=True, exist_ok=True)
    dem_out = dem_dir / "dem.wgs84"
    xml_out = dem_dir / "dem.wgs84.xml"
    if dem_out.exists() and xml_out.exists():
        logger.info("Reusing existing ISCE2 DEM: %s", dem_out)
        print(f"  Reusing existing DEM: {dem_out.name}")
        return dem_out

    try:
        from isce.components.isceobj.Image import createDemImage  # type: ignore[import]
        from dem_stitcher import stitch_dem

        s, n, w, e = bbox
        print(f"  Downloading GLO-30 DEM  bbox {bbox}…")
        arr, profile = stitch_dem(
            [w, s, e, n],
            dem_name="glo_30",
            dst_ellipsoidal_height=True,
            dst_area_or_point="Point",
        )
        arr_f32 = arr.astype(np.float32)
        arr_f32[np.isnan(arr_f32)] = -32768.0
        arr_f32.tofile(str(dem_out))

        height, width = arr_f32.shape
        t = profile["transform"]
        dem_img = createDemImage()
        dem_img.filename   = str(dem_out)
        dem_img.width      = width
        dem_img.length     = height
        dem_img.dataType   = "FLOAT"
        dem_img.scheme     = "BIL"
        dem_img.bands      = 1
        dem_img.accessMode = "READ"
        dem_img.reference  = "WGS84"
        dem_img.setFirstLongitude(t.c + 0.5 * t.a)
        dem_img.setFirstLatitude(t.f  + 0.5 * t.e)
        dem_img.setDeltaLongitude(t.a)
        dem_img.setDeltaLatitude(t.e)
        dem_img.renderHdr()
        print(f"  DEM saved → {dem_out.name}  ({width}×{height} px)")
        return dem_out

    except Exception as exc:
        raise RuntimeError(
            f"GLO-30 DEM download failed: {exc}. "
            "Provide a pre-existing ISCE2-format DEM via dem_path=."
        ) from exc


# ── Main class ────────────────────────────────────────────────────────────────

class ISCE_S1(ISCE_Base):
    """Time-series InSAR processor using ISCE2 stackSentinel.

    stackSentinel.py generates a numbered series of run scripts from all SLCs
    in ``slc_dir``.  InSARHub executes the scripts sequentially (each step must
    complete before the next starts) and parallelises the independent commands
    *within* each step up to ``max_workers``.

    Usage::

        from insarhub.processor import ISCE_S1
        from insarhub.config import ISCE_S1_Config

        proc = ISCE_S1(
            pairs  = [("20200101", "20200113"), ("20200101", "20200125")],
            config = ISCE_S1_Config(
                workdir   = '/data/stack',
                slc_dir   = '/data/slcs',
                orbit_dir = '/data/orbits',
                bbox      = [33.0, 38.0, -120.0, -115.0],
            ),
        )
        proc.submit()
        proc.watch()
    """

    name                  = "ISCE_S1"
    description           = ("Time-series InSAR with ISCE2 stackSentinel. "
                              "Requires ISCE2 with topsStack contrib.")
    compatible_downloader = "S1_SLC"
    default_config        = ISCE_S1_Config

    def __init__(self, pairs: list[tuple[str, str]], config: ISCE_S1_Config | None = None):
        super().__init__(config)
        self.config: ISCE_S1_Config = (
            self.config if self.config is not None else ISCE_S1_Config()
        )
        # Allow empty/dummy pairs when loading from a saved job file (refresh/watch/retry)
        if not pairs and not self.jobs:
            raise ValueError("pairs must be a non-empty list of (reference, secondary) tuples.")
        self.pairs = pairs

    # ── Submit ────────────────────────────────────────────────────────────────

    def submit(self) -> dict:
        """Generate run scripts and start sequential step execution."""
        dem_path = _prepare_dem(self.config, self.workdir)
        aux_dir  = self._resolve_aux_dir()
        self._generate_run_files(dem_path, aux_dir, pairs=self.pairs)

        scripts = sorted(
            p for p in self._run_files_dir.glob("run_*")
            if p.is_file() and not p.suffix
        )
        if not scripts:
            raise RuntimeError(
                f"No run scripts found in {self._run_files_dir}. "
                "Check stackSentinel.log for errors."
            )

        pending: list[str] = []
        for script in scripts:
            step = script.name
            status, _ = _read_status(self._run_files_dir, step)
            if status == _SUCCEEDED and self.config.skip_existing:
                print(f"{Fore.YELLOW}  ✓ {step} already succeeded, skipping.{Style.RESET_ALL}")
                if step not in self.jobs:
                    self.jobs[step] = self._job_meta(step, script, _SUCCEEDED)
                continue

            log_dir = self._run_files_dir / f"{step}_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            _write_status(self._run_files_dir, step, _PENDING)
            self.jobs[step] = self._job_meta(step, script, _PENDING, log_dir)
            pending.append(step)

        print(f"{Fore.GREEN}Registered {len(pending)} pending step(s) "
              f"({len(self.jobs)} total).{Style.RESET_ALL}")

        hpc_mode = getattr(self.config, "hpc_mode", False)
        dry_run  = getattr(self.config, "dry_run", False)
        if hpc_mode or dry_run:
            # HPC/dry-run: sbatch calls are fast — run blocking so CLI doesn't
            # exit before all jobs are submitted.
            self._step_executor(sorted(pending))
        else:
            # Local mode: fork a detached background process so CLI returns
            # immediately; use refresh/cancel/retry to control execution.
            self._start_local_background(sorted(pending))
        if not dry_run:
            self.save()
        return self.jobs

    def _job_meta(self, step: str, script: Path, status: str,
                  log_dir: Path | None = None) -> dict:
        return {
            "step":         step,
            "script":       str(script),
            "log_dir":      str(log_dir or self._run_files_dir / f"{step}_logs"),
            "status":       status,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }

    def _resolve_aux_dir(self) -> Path:
        p = Path(str(self.config.aux_dir)).expanduser().resolve() if self.config.aux_dir else self._paths.slc_dir
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _build_inps_namespace(self, dem_path: Path, aux_dir: Path, orbit_dir_str: str):
        import types
        cfg = self.config
        ns  = types.SimpleNamespace()

        # paths
        ns.slc_dirname             = str(cfg.slc_dir)
        ns.orbit_dirname           = orbit_dir_str
        ns.aux_dirname             = str(aux_dir)
        ns.work_dir                = str(self.isce_dir)
        ns.dem                     = str(dem_path)

        # area / dates
        ns.polarization            = cfg.polarization
        ns.workflow                = cfg.workflow
        ns.swath_num               = cfg.swath_num
        ns.bbox                    = (f"{cfg.bbox[0]} {cfg.bbox[1]} {cfg.bbox[2]} {cfg.bbox[3]}"
                                      if cfg.bbox else None)
        ns.exclude_dates           = cfg.exclude_dates
        ns.include_dates           = cfg.include_dates
        ns.startDate               = cfg.start_date
        ns.stopDate                = cfg.end_date

        # coregistration
        ns.coregistration          = cfg.coregistration
        ns.reference_date          = cfg.reference_date
        ns.snrThreshold            = str(cfg.snr_misreg_threshold)
        ns.esdCoherenceThreshold   = str(cfg.esd_coherence_threshold)
        ns.num_overlap_connections = str(cfg.num_overlap_connections)

        # interferogram
        ns.azimuthLooks            = str(cfg.looks_azimuth)
        ns.rangeLooks              = str(cfg.looks_range)
        ns.filtStrength            = str(cfg.filter_strength)
        ns.unwMethod               = cfg.unw_method
        ns.rmFilter                = cfg.rm_filter
        ns.virtualMerge            = cfg.virtual_merge

        # ionosphere
        ns.param_ion               = cfg.param_ion
        ns.num_connections_ion     = str(cfg.num_connections_ion)

        # compute
        ns.useGPU                  = cfg.use_gpu
        ns.numProcess              = cfg.num_proc
        ns.numProcess4topo         = cfg.num_proc4topo
        ns.text_cmd                = cfg.text_cmd

        return ns

    def _generate_run_files(self, dem_path: Path, aux_dir: Path,
                             pairs: list[tuple[str, str]]) -> None:
        if (self._run_files_dir.exists()
                and any(p for p in self._run_files_dir.glob("run_*")
                        if p.is_file() and not p.suffix)):
            print(f"{Fore.YELLOW}  run_files/ already exists — reusing. "
                  f"Delete it to regenerate with new settings.{Style.RESET_ALL}")
            return

        cfg       = self.config
        orbit_dir = str(cfg.orbit_dir) if cfg.orbit_dir else str(self.workdir / "slc")  # config always resolves this
        Path(orbit_dir).mkdir(parents=True, exist_ok=True)

        # ensure topsStack is importable
        topsstack_parent = str(self._pythonpath_add)
        if topsstack_parent not in sys.path:
            sys.path.insert(0, topsstack_parent)

        from topsStack.stackSentinel import (  # type: ignore[import]
            checkCurrentStatus, interferogramStack,
        )

        inps = self._build_inps_namespace(dem_path, aux_dir, orbit_dir)
        stack_pairs = [(_parse_date(r), _parse_date(s)) for r, s in pairs]

        # stackSentinel writes SAFE_files.txt relative to CWD
        orig_cwd = os.getcwd()
        os.chdir(str(self.isce_dir))
        try:
            print(f"  Discovering SLCs in {cfg.slc_dir} …")
            acquisitionDates, stackReferenceDate, secondaryDates, safe_dict, updateStack = (
                checkCurrentStatus(inps)
            )
            print(f"  Using {len(stack_pairs)} user-supplied pair(s).")
            print(f"  Writing run scripts for workflow '{cfg.workflow}' …")
            interferogramStack(
                inps, acquisitionDates, stackReferenceDate,
                secondaryDates, safe_dict, stack_pairs, updateStack,
            )
        finally:
            os.chdir(orig_cwd)

        # Redirect MintPy output to workdir/mintpy/ (not isce/mintpy/)
        mintpy_dir = MintPyPaths(self.workdir).mintpy_dir
        for script in self._run_files_dir.glob("run_*"):
            if not script.is_file() or script.suffix:
                continue
            text = script.read_text()
            if "smallbaselineApp.py" in text:
                patched = re.sub(
                    r"(smallbaselineApp\.py)(\s+)(\S+\.cfg)",
                    rf"\1\2\3 --dir {mintpy_dir}",
                    text,
                )
                if patched != text:
                    script.write_text(patched)

        print("  run_files/ generated.")
