"""ISCE3_NISAR -- NISAR L2 GSLC -> dolphin phase-linking + interferograms.

NISAR GSLC is ALREADY geocoded (each granule is one geocoded SLC frame per
date), so unlike ISCE3_Burst there is NO geocoding front-end: the S1/COMPASS
``dem``/``tec``/``cslc``/``static`` stages are dropped entirely. What remains is
the sensor-agnostic dolphin engine, which reads the complex SLC straight out of
the GSLC HDF5 via a subdataset path and phase-links the stack::

    ifg     PS + phase-link + interferograms   dolphin wrapped_phase.run  (ONE stack)
    stitch  correlation + mosaic (1 frame)      dolphin stitching_bursts.run
    unwrap  snaphu + connected components       dolphin unwrapping.run

Output lands in dolphin's native layout (``interferograms/``, ``unwrapped/``),
which the ISCE3_Dolphin_PL analyzer inverts to a time series. Because NISAR is
one frame per date (no OPERA bursts), ``ifg`` is a SINGLE ``wrapped_phase.run``
over the whole stack rather than one call per burst.

Each GSLC is ~21 GB, so a real stack lives on HPC/large storage; this processor
carries no assumptions that force everything into memory (dolphin streams).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from insarhub.config import ISCE3_NISAR_Config
from .isce3_burst import ISCE3_Burst, ensure_gdal_cli, ensure_proj_env

import logging
logger = logging.getLogger(__name__)


class ISCE3_NISAR(ISCE3_Burst):
    name = "ISCE3_NISAR"
    description = (
        "NISAR L2 GSLC -> dolphin phase-linking + interferograms (no geocoding; "
        "GSLC is already geocoded). Feeds the ISCE3_Dolphin_PL analyzer."
    )
    compatible_downloader = "NISAR_GSLC"
    compatible_analyzer = "ISCE3_Dolphin_PL"
    default_config = ISCE3_NISAR_Config
    JOBS_FILE = "isce3_nisar_jobs.json"

    # GSLC is pre-geocoded: only the dolphin engine stages run, preceded by an
    # AOI crop (NISAR's stand-in for the COMPASS geocode --bbox that ISCE3_Burst
    # gets its AOI cut from -- see run_crop).
    STAGES = ("crop", "ifg", "stitch", "unwrap")
    _IMPLEMENTED = ("crop", "ifg", "stitch", "unwrap")
    REQUIRED_MODULES = ("dolphin",)

    #: NISAR_GSLC downloads .h5 granules, not S1_Burst's .SAFE dirs.
    input_glob = "*GSLC*.h5"

    # ------------------------------------------------------------------ paths
    @property
    def gslc_dir(self) -> Path:
        """Where the NISAR_GSLC downloader put the ``*.h5`` products
        (defaults to workdir/slc, matching ISCE3_Burst's slc_dir convention)."""
        return self._p("gslc_dir", "slc")

    def _gslc_grid_path(self) -> str:
        """GDAL subdataset path of the complex SLC inside a NISAR GSLC .h5."""
        freq = str(getattr(self.config, "nisar_frequency", "A")).upper()
        pol = str(getattr(self.config, "nisar_polarization", "HH")).upper()
        return f"/science/LSAR/GSLC/grids/frequency{freq}/{pol}"

    @property
    def _subdataset(self) -> str:  # type: ignore[override]
        """dolphin reads the complex SLC from the NISAR GSLC grid group -- UNLESS
        the inputs have been AOI-cropped to standalone VRTs (see _cslc_list),
        in which case each VRT is already a single-band raster and no subdataset
        selection applies. ``_cslc_list`` runs first in ``_dolphin_cfg`` and sets
        ``_cropped_to_aoi``, so this reflects the choice it made."""
        if getattr(self, "_cropped_to_aoi", False):
            return ""
        return self._gslc_grid_path()

    @property
    def _cropped_gslc_dir(self) -> Path:
        return self._paths.cropped_gslc_dir

    def _raw_gslc_list(self) -> list[Path]:
        """The downloaded NISAR GSLC products, sorted -- one geocoded SLC per
        date. Excludes the small QA/side products ASF ships alongside."""
        h5 = sorted(
            p for p in self.gslc_dir.glob("*GSLC*.h5")
            if "QA" not in p.name and "STATS" not in p.name)
        if not h5:
            raise FileNotFoundError(
                f"ISCE3_NISAR: no NISAR GSLC .h5 under {self.gslc_dir}; "
                "run the NISAR_GSLC downloader first")
        return h5

    def _aoi_crop_enabled(self) -> bool:
        """Crop each GSLC to the AOI before phase-linking, unless the user asked
        for the full extent. GSLC is delivered as a full geocoded frame (e.g.
        69840x68688), but the AOI is usually a small window -- without cropping,
        dolphin phase-links the ENTIRE frame and the stitch's gdal_merge OOMs on
        the full-frame rasters (the AOI is otherwise only applied as a final
        output clip, too late to save the intermediate memory)."""
        if bool(getattr(self.config, "process_full_extent", False)):
            return False
        try:
            aoi = self._aoi()
        except Exception:                                        # noqa: BLE001
            return False
        return bool(aoi and len(aoi) == 4)

    def _crop_gslc_to_aoi(self, gslc: Path, aoi) -> Path:
        """Write a VRT that windows the GSLC's complex-SLC subdataset to the AOI.

        A VRT (not a copy) keeps this cheap -- it just records the source
        subdataset + the pixel window; dolphin reads it as an already-cropped
        single-band raster. ``-projwin_srs EPSG:4326`` lets us pass the AOI in
        lon/lat while the GSLC is in its native UTM. Idempotent + atomic.
        """
        w, s, e, n = (float(x) for x in aoi)
        self._cropped_gslc_dir.mkdir(parents=True, exist_ok=True)
        vrt = self._cropped_gslc_dir / f"{gslc.stem}.vrt"
        if vrt.exists():
            return vrt
        src = f'NETCDF:"{gslc}":{self._gslc_grid_path()}'
        tmp = vrt.with_name(vrt.name + ".tmp")
        # -projwin is ulx uly lrx lry == W N E S.
        cmd = ["gdal_translate", "-q", "-of", "VRT",
               "-projwin_srs", "EPSG:4326",
               "-projwin", str(w), str(n), str(e), str(s), src, str(tmp)]
        r = subprocess.run(cmd, capture_output=True, text=True, env=os.environ.copy())
        if r.returncode != 0 or not tmp.exists():
            raise RuntimeError(
                f"ISCE3_NISAR: AOI crop failed for {gslc.name}: {r.stderr.strip()}")
        os.replace(tmp, vrt)
        return vrt

    def _cslc_list(self) -> list[str]:  # type: ignore[override]
        """The stack dolphin reads: AOI-cropped VRTs when cropping applies (built
        by the ``crop`` stage; built on demand here too so ``ifg`` still works if
        run standalone -- ``_crop_gslc_to_aoi`` is idempotent), else the raw
        GSLC .h5 frames."""
        raw = self._raw_gslc_list()
        if self._aoi_crop_enabled():
            aoi = self._aoi()
            vrts = [self._crop_gslc_to_aoi(p, aoi) for p in raw]
            self._cropped_to_aoi = True
            return [str(v) for v in vrts]
        self._cropped_to_aoi = False
        return [str(p) for p in raw]

    # ------------------------------------------------------------------ crop
    def run_crop(self, force: bool = False) -> bool:
        """AOI-crop each GSLC to a lightweight VRT before phase-linking.

        NISAR's stand-in for the COMPASS geocode ``--bbox`` that gives
        ``ISCE3_Burst`` its AOI cut for free: a NISAR GSLC arrives as a full
        geocoded frame (e.g. 69840x68688), so this windows each one's complex-SLC
        subdataset to the AOI (see ``_crop_gslc_to_aoi``) and dolphin then
        processes only that window. A trivial pass-through when
        ``process_full_extent`` is set or no AOI is resolvable -- the full frame
        is used and the stage still succeeds.
        """
        ensure_gdal_cli()  # gdal_translate must be on PATH
        raw = self._raw_gslc_list()
        if not self._aoi_crop_enabled():
            print(f"[ISCE3_NISAR] crop: process_full_extent / no AOI -- using the "
                  f"full frames ({len(raw)} GSLC), no crop")
            return True
        aoi = self._aoi()
        if force:
            shutil.rmtree(self._cropped_gslc_dir, ignore_errors=True)
        vrts = [self._crop_gslc_to_aoi(p, aoi) for p in raw]
        print(f"[ISCE3_NISAR] crop: {len(vrts)} GSLC -> AOI "
              f"{['%.4f' % float(x) for x in aoi]} in {self._cropped_gslc_dir} "
              f"(dolphin processes only the AOI window, not the full frame)")
        return len(vrts) == len(raw)

    # ------------------------------------------------------------------ ifg
    def run_ifg(self, force: bool = False) -> bool:  # type: ignore[override]
        """PS + phase-link + interferograms over the WHOLE GSLC stack in one
        ``wrapped_phase.run`` (NISAR is one frame per date -- no burst split).

        Writes the same manifest ISCE3_Burst's ``stitch`` consumes, so the
        inherited ``run_stitch``/``run_unwrap`` run unchanged.
        """
        ensure_proj_env()
        ensure_gdal_cli()          # dolphin's stitch shells out to gdal_merge.py
        from dolphin.workflows import wrapped_phase

        cfg = self._dolphin_cfg()
        cfg.create_dir_tree()
        print(f"[ISCE3_NISAR] ifg: {len(cfg.cslc_file_list)} GSLC "
              f"({self._subdataset}) -> wrapped_phase.run")
        out = wrapped_phase.run(cfg, max_workers=self._nw)

        manifest = self._wrapped_phase_manifest_path()
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({
            "ifg_file_list": [str(p) for p in out.ifg_file_list],
            "temp_coh_file_list": [str(p) for p in out.temp_coh_files],
            "ps_file_list": [str(out.ps_looked_file)],
            "crlb_file_list": [str(p) for p in out.crlb_files],
            "closure_phase_file_list": [str(p) for p in out.closure_phase_files],
            "amp_dispersion_list": [str(out.amp_disp_looked_file)],
            "shp_count_file_list": [str(p) for p in out.shp_count_files],
            "similarity_file_list": [str(p) for p in out.similarity_files],
        }, indent=1))
        print(f"[ISCE3_NISAR] ifg: {len(out.ifg_file_list)} interferogram(s)")
        return bool(out.ifg_file_list)
