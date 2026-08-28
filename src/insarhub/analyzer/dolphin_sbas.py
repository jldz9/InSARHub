"""Time-series inversion for ISCE3_Burst stacks, via dolphin.

The processor's engine is a thin wrapper over dolphin's ``displacement.run``, so
the wrapped-phase estimator is always dolphin's phase linking. What reaches this
point is a set of unwrapped interferograms plus connected-component labels, and
``dolphin.timeseries.run`` inverts them::

    A · d = phi          A = incidence matrix (n_ifg x n_date-1)
                         phi = unwrapped phase per interferogram
                         d  = cumulative displacement per date

The quality raster used to pick the reference point and mask low-quality pixels
is dolphin's stitched temporal coherence (``interferograms/temporal_coherence_*.tif``).

``timeseries.run`` refuses to run without one (``Must provide quality_file if
not reference_point given``), since it has no other way to choose a stable
reference pixel.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from insarhub.config import ISCE3_Dolphin_PL_Config
from insarhub.core.base import BaseAnalyzer

logger = logging.getLogger(__name__)


class ISCE3_Dolphin_PL(BaseAnalyzer):
    name = "ISCE3_Dolphin_PL"
    aliases = ("Dolphin_SBAS", "Dolphin_TS", "ISCE3_Dolphin_TS")   # legacy names
    description = ("Time-series inversion of an ISCE3 (burst or NISAR GSLC) stack "
                   "with dolphin: cumulative displacement per date, velocity, residuals.")
    # Dolphin drives the time-series for both ISCE3 processors: the Sentinel-1
    # burst stack (ISCE3_Burst) and the NISAR GSLC stack (ISCE3_NISAR). A tuple
    # matches either upstream -- see core.base._compatible_processor.
    compatible_processor = ("ISCE3_Burst", "ISCE3_NISAR")
    default_config = ISCE3_Dolphin_PL_Config

    def __init__(self, config=None):
        super().__init__(config=config)
        wd = getattr(self.config, "workdir", None) or Path.cwd()
        self.workdir = Path(wd).expanduser().resolve()

    # ------------------------------------------------------------------
    # stack discovery
    # ------------------------------------------------------------------

    def manifest(self) -> dict:
        """The wrapped-phase estimator is always dolphin's phase linking now --
        the engine is a thin wrapper over dolphin's ``displacement.run`` -- so
        the mode is constant and no per-stack sidecar is read.
        """
        return {"mode": "phase_link"}

    @property
    def unwrap_dir(self) -> Path:
        v = getattr(self.config, "unwrap_dir", None)
        if v:
            return Path(v).expanduser()
        return self.workdir / "unwrapped"

    @property
    def out_dir(self) -> Path:
        v = getattr(self.config, "output_dir", None)
        return Path(v).expanduser() if v else self.workdir / "timeseries"

    @property
    def _footprint_path(self) -> Path:
        """Boolean interferogram-footprint mask written by prep_data()."""
        return self.unwrap_dir.parent / "footprint_mask.tif"

    def water_mask_path(self) -> Path | None:
        """The processor's water mask (dem/water_mask.tif), or None if absent.

        dolphin's ``displacement.run`` passes this to ``timeseries.run`` as
        ``mask_path`` (apply_mask_to_timeseries=True), which masks water out of
        the inversion; matching it keeps the sbas stage byte-identical to a
        native ``dolphin run``.
        """
        wm = self.workdir / "dem" / "water_mask.tif"
        return wm if wm.exists() else None

    def _compute_footprint(self):
        """(mask, profile): the reliable scene footprint. Returns (None, None)
        when there are no interferograms.

        Two conditions must both hold:
          * valid (finite, non-zero, non-nodata) data in EVERY unwrapped ifg --
            drops the empty stitch pad; and
          * temporal coherence above the threshold -- drops the incoherent
            out-of-scene patches that snaphu unwraps to a NON-zero constant, so
            they survive the ifg!=0 test and would otherwise leak through.
        """
        import numpy as np
        import rasterio
        unw = sorted(p for p in self.unwrap_dir.glob("*.unw.tif")
                     if "conncomp" not in p.name)
        if not unw:
            return None, None
        valid = None
        prof = None
        for u in unw:
            with rasterio.open(u) as s:
                a = s.read(1)
                nd = s.nodata
                prof = s.profile
            v = np.isfinite(a) & (a != 0)
            if nd is not None and np.isfinite(nd):
                v &= (a != nd)
            valid = v if valid is None else (valid & v)

        # Tighten with the temporal-coherence quality raster. correlation_threshold
        # gates it; at 0 we still drop coherence == 0 / nan (dead pixels).
        try:
            q = self.quality_file()
        except Exception:                                        # noqa: BLE001
            q = None
        if q is not None and Path(q).exists():
            with rasterio.open(q) as s:
                coh = s.read(1)
            if coh.shape == valid.shape:
                thr = float(getattr(self.config, "correlation_threshold", 0.0))
                # coh == 1.0 EXACTLY is a degenerate phase-linking artifact, not
                # real signal: it is what phase linking emits where a pixel's
                # estimation window held a single/near-single sample -- the
                # grid-edge / no-data bands that snaphu then unwraps to a bright
                # rectangular border. Real coherence is < 1, so excluding it
                # drops those borders. The burst-overlap seam used to be
                # degenerate here too, but the processor's coherence mosaic now
                # fills the seam from whichever burst has real coherence (see
                # _mosaic_real drop_degenerate), so it survives this gate as
                # normal data -- only the true single-burst edges remain == 1.0.
                good = (np.isfinite(coh) & (coh >= thr) & (coh < 1.0)) if thr > 0 else \
                       (np.isfinite(coh) & (coh > 0) & (coh < 1.0))
                valid &= good
        return valid, prof

    def prep_data(self) -> None:
        """Build the interferogram-footprint mask that trims the inversion.

        dolphin's ``timeseries.run`` inverts EVERY pixel of the padded stitch
        grid -- regardless of nodata, connected component, or
        correlation_threshold -- so pixels OUTSIDE the interferogram footprint
        come out as spurious flat blocks (each snaphu component gets the
        reference-point offset). Masking the conncomp does nothing (verified:
        dolphin ignores it for masking), so the fix has to be applied to the
        OUTPUT: this computes the footprint and saves it; :meth:`run` sets the
        velocity/displacement to nodata outside it.
        """
        if self.config.container and not os.environ.get("INSARHUB_CONTAINER_CHILD"):
            return self._run_via_container(["prep_data"])
        import rasterio
        mask, prof = self._compute_footprint()
        if mask is None:
            print(f"{Fore.YELLOW}[ISCE3_Dolphin_PL] prep_data: no unwrapped "
                  f"interferograms in {self.unwrap_dir}{Fore.RESET}")
            return
        self._footprint_path.parent.mkdir(parents=True, exist_ok=True)
        prof = {**prof, "dtype": "uint8", "count": 1, "nodata": 0,
                "compress": "deflate"}
        with rasterio.open(self._footprint_path, "w", **prof) as dst:
            dst.write(mask.astype("uint8"), 1)
        print(f"[ISCE3_Dolphin_PL] prep_data: footprint mask "
              f"({int(mask.sum())} valid px, {100*mask.mean():.0f}% of grid) "
              f"-> {self._footprint_path}")

    def _apply_footprint(self, tifs) -> None:
        """Set every raster in ``tifs`` to NaN outside the scene footprint.

        NaN, not 0: dolphin writes velocity/displacement with nodata=0, but real
        STABLE ground is also ~0, so filling with 0 would make nodata and genuine
        zero-motion indistinguishable (and a viewer masking 0 would hide stable
        pixels). NaN is an unambiguous nodata that GDAL/QGIS and the renderer all
        honour, and it leaves real zeros intact.
        """
        import numpy as np
        import rasterio
        fp = self._footprint_path
        if fp.exists():
            with rasterio.open(fp) as s:
                mask = s.read(1).astype(bool)
        else:
            mask, _ = self._compute_footprint()   # prep_data not run: do it inline
        if mask is None:
            return
        outside = ~mask
        n = 0
        for tif in tifs:
            tif = Path(tif)
            if not tif.exists():
                continue
            with rasterio.open(tif) as s:
                a = s.read(1).astype("float32")
                prof = s.profile
            if a.shape != outside.shape:
                continue
            a[outside] = np.float32("nan")
            prof = {**prof, "dtype": "float32", "nodata": float("nan")}
            with rasterio.open(tif, "w", **prof) as dst:
                dst.write(a, 1)
            n += 1
        print(f"[ISCE3_Dolphin_PL] masked {n} output raster(s) to the scene "
              f"footprint (NaN outside)")

    def inputs(self) -> tuple[list[Path], list[Path]]:
        """Unwrapped interferograms and their connected-component labels."""
        unw = sorted(p for p in self.unwrap_dir.glob("*.unw.tif")
                     if "conncomp" not in p.name)
        if not unw:
            raise FileNotFoundError(
                f"ISCE3_Dolphin_PL: no unwrapped interferograms in {self.unwrap_dir}; "
                "run the processor's 'unwrap' stage first")
        ccl, missing = [], []
        for u in unw:
            c = u.with_suffix(".conncomp.tif")
            (ccl if c.exists() else missing).append(c)
        if missing:
            # Not fatal, but worth saying plainly: without them the inversion
            # cannot tell an unwrapping jump from real displacement.
            logger.warning("ISCE3_Dolphin_PL: %d interferogram(s) have no conncomp; "
                           "inverting without connected-component masking",
                           len(missing))
            return unw, []
        return unw, ccl

    # ------------------------------------------------------------------
    # quality raster
    # ------------------------------------------------------------------

    def quality_file(self) -> Path | None:
        """Per-pixel quality on the stack grid, or None if it can't be built."""
        v = getattr(self.config, "quality_file", None)
        if v and Path(v).exists():
            return Path(v)

        mf = self.manifest()
        q = mf.get("quality_file")
        if q and Path(q).exists():
            return Path(q)

        # phase_link: dolphin stitches temporal coherence into interferograms/
        for cand in sorted((self.workdir / "interferograms").glob("temporal_coherence*.tif")):
            if cand.exists():
                return cand

        # network: no single quality raster exists, so build one from the
        # per-pair correlations
        return self._temporal_average_correlation()

    def _temporal_average_correlation(self) -> Path | None:
        """Mean of the per-pair correlation rasters, as a stand-in quality map.

        A pairwise network has no whole-stack quality measure the way phase
        linking's temporal coherence is one. Averaging the pair correlations is
        the honest approximation: it says how well this pixel correlated on
        average, which is enough to choose a stable reference point.
        """
        stitched = self.workdir / "interferograms"
        cors = sorted(stitched.glob("*.int.cor.tif"))
        if not cors:
            logger.warning("ISCE3_Dolphin_PL: no correlation rasters in %s", stitched)
            return None
        from dolphin import timeseries

        out = stitched / "avg_correlation.tif"
        if not out.exists():
            timeseries.create_temporal_average(
                file_list=cors, output_file=out,
                block_shape=tuple(getattr(self.config, "block_shape", (256, 256))),
                num_threads=int(getattr(self.config, "num_threads", 4)))
            logger.info("ISCE3_Dolphin_PL: built %s from %d correlation rasters",
                        out.name, len(cors))
        return out

    # ------------------------------------------------------------------
    # LOS projection
    # ------------------------------------------------------------------

    @staticmethod
    def _warp_like(src: Path, like: Path):
        """Resample ``src`` onto ``like``'s grid, returning the array."""
        from osgeo import gdal

        gdal.UseExceptions()
        ref = gdal.Open(str(like))
        gt = ref.GetGeoTransform()
        nx, ny = ref.RasterXSize, ref.RasterYSize
        bounds = (gt[0], gt[3] + gt[5] * ny, gt[0] + gt[1] * nx, gt[3])
        proj = ref.GetProjection()
        ref = None
        ds = gdal.Warp("", str(src), format="MEM", outputBounds=bounds,
                       width=nx, height=ny, dstSRS=proj, resampleAlg="bilinear")
        arr = ds.GetRasterBand(1).ReadAsArray()
        ds = None
        return arr

    def _project_to_vertical(self, files: list[Path]) -> int:
        """Divide LOS displacement by the up-component of the LOS unit vector.

        This is a modelling assumption, not a coordinate change: it attributes
        *all* motion to the vertical. Horizontal motion, if present, is inflated
        by 1/u_up and reported as vertical. Written to a separate directory and
        labelled so the assumption travels with the product.
        """
        import numpy as np
        from dolphin import io

        los_up = self.workdir / "geometry" / "los_up.tif"
        if not los_up.exists():
            logger.error("ISCE3_Dolphin_PL: los_projection='vertical' needs %s -- "
                         "run the processor's 'los' stage (it mosaics COMPASS's "
                         "per-burst static layers onto the interferogram grid, "
                         "so it runs after 'stitch', not inside 'static')", los_up)
            return 0
        dest = self.out_dir / "vertical"
        dest.mkdir(parents=True, exist_ok=True)

        # The geometry rasters are built on the interferogram grid that existed
        # when 'static' ran, which may not match the current grid. Resample onto
        # the displacement grid rather than demanding they already match; the
        # LOS vector is a smooth physical field, so this is exact enough and far
        # less brittle than requiring 'static' to be re-run per mode.
        inv = None
        ref_shape = None
        n = 0
        for f in files:
            a = io.load_gdal(f).astype("float64")
            if a.shape != ref_shape:
                uu = self._warp_like(los_up, f).astype("float64")
                with np.errstate(divide="ignore", invalid="ignore"):
                    inv = np.where(uu > 1e-6, 1.0 / uu, np.nan)
                ref_shape = a.shape
                if inv.shape != a.shape:
                    logger.error("ISCE3_Dolphin_PL: could not match los_up to %s "
                                 "(%s vs %s); skipping", f.name, inv.shape, a.shape)
                    continue
            io.write_arr(arr=(a * inv).astype("float32"),
                         output_name=dest / f.name, like_filename=f,
                         driver="GTiff", nodata=float("nan"),
                         options=["COMPRESS=DEFLATE", "TILED=YES"])
            n += 1
        (dest / "README.txt").write_text(
            "Vertical displacement = LOS / u_up, where u_up is the up-component\n"
            "of the ground-to-satellite unit vector (geometry/los_up.tif).\n"
            "This ASSUMES all motion is vertical. Any horizontal motion is\n"
            "inflated by 1/u_up and misreported as vertical. For a rigorous\n"
            "decomposition combine ascending and descending tracks.\n")
        return n

    # ------------------------------------------------------------------
    # HPC (SLURM)
    # ------------------------------------------------------------------

    def submit_hpc(self, steps: list[str] | None = None) -> str | None:
        """Generate a sbatch script for the Dolphin run and submit it.

        Returns the SLURM job ID string, or None if sbatch_options.json was
        just created/updated and needs review before submitting — callers
        must check for this and stop rather than treat it as success.

        ``num_threads`` is auto-derived from the merged sbatch options'
        ``cpus_per_task``, so the inversion uses exactly the cores the SLURM
        job was allocated instead of the config default.
        """
        import dataclasses
        import os
        import shutil
        import subprocess
        import sys as _sys

        from colorama import Fore, Style

        from insarhub.processor.isce2_base import (
            _merge_sbatch_opts, load_or_init_sbatch_options,
        )
        from insarhub.utils.slurm_manager import sbatch_template_header
        from insarhub.utils.tool import Slurmjob_Config

        self.out_dir.mkdir(parents=True, exist_ok=True)

        default_template = {
            **sbatch_template_header(),
            "_steps": {
                "sbas": "Dolphin time-series inversion (timeseries.run) — "
                        "one job for the whole stack",
            },
            "default": {
                "time":          "04:00:00",
                "partition":     "all",
                "nodes":         1,
                "ntasks":        1,
                "cpus_per_task": 4,
                "mem":           "16G",
            },
            "sbas": {},
        }
        per_step = load_or_init_sbatch_options(
            Path(self.workdir), "sbas", "ISCE3_Dolphin_PL",
            default_template=default_template)
        if per_step is None:
            return None
        opts = _merge_sbatch_opts(per_step, "sbas")

        _slurm_fields = {f.name for f in dataclasses.fields(Slurmjob_Config)}
        _skip = {"job_name", "output_file", "error_file", "command",
                 "modules", "conda_env", "export_env", "array", "dependency"}
        slurm_kwargs = {k: v for k, v in opts.items()
                        if k in _slurm_fields and k not in _skip}

        slurm_cfg = Slurmjob_Config(
            job_name="dolphin_sbas",
            output_file=str(self.out_dir / "dolphin_slurm_%j.out"),
            error_file=str(self.out_dir / "dolphin_slurm_%j.err"),
            **slurm_kwargs,
        )

        insarhub_bin = shutil.which("insarhub") or f"{Path(_sys.executable).parent}/insarhub"
        current_path = os.environ.get("PATH", "")

        # Auto-derive num_threads from the sbatch cpus_per_task, so the
        # inversion runs with exactly the cores this job was allocated.
        cpus = int(opts.get("cpus_per_task") or getattr(self.config, "num_threads", 4))
        self.config.num_threads = cpus
        extra = f" --num-threads {cpus}"

        # `--step run` is a no-op for Dolphin (run() ignores steps) but keeps
        # the CLI from expanding the default 'all' into the full MintPy step
        # list, which would otherwise re-run the inversion once per step.
        body_cmd = f"{insarhub_bin} analyzer -N {type(self).name} -w {self.workdir} run --step run{extra}"
        body = "\n".join([
            f'export PATH="{current_path}"',
            body_cmd,
        ])

        lines = ["#!/bin/bash"] + slurm_cfg.to_header_lines() + ["", body, ""]
        sbatch_script = self.out_dir / "dolphin_sbas.sbatch"
        sbatch_script.write_text("\n".join(lines) + "\n")
        sbatch_script.chmod(0o755)

        result = subprocess.run(
            ["sbatch", "--parsable", str(sbatch_script)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"sbatch failed: {result.stderr.strip()}")

        job_id = result.stdout.strip().split(";")[0]

        job_file = self.out_dir / "dolphin_job.json"
        job_file.write_text(json.dumps({
            "job_id":  job_id,
            "status":  "PENDING",
            "script":  str(sbatch_script),
            "log":     str(self.out_dir / f"dolphin_slurm_{job_id}.out"),
        }, indent=2))

        print(f"{Fore.GREEN}ISCE3_Dolphin_PL job submitted: {job_id}{Style.RESET_ALL}")
        print(f"  script : {sbatch_script}")
        print(f"  log    : {self.out_dir}/dolphin_slurm_{job_id}.out")
        return job_id

    # ------------------------------------------------------------------
    # container
    # ------------------------------------------------------------------

    def _run_via_container(self, steps: list[str] | None = None) -> None:
        """Re-invoke `insarhub analyzer ... run` inside self.config.container.

        The container image is expected to have `insarhub` plus dolphin
        installed — mirrors the MintPy analyzers' container short-circuit.
        """
        import subprocess as _sp

        from insarhub.utils.container import wrap_container_cmd

        extra = ""
        if getattr(self.config, "num_threads", None):
            extra = f" --num-threads {self.config.num_threads}"
        cli_cmd = (f"insarhub analyzer -N {type(self).name} "
                   f"-w {self.workdir} run --step run{extra}")
        wrapped = wrap_container_cmd(self.config.container, cli_cmd, self.workdir)

        result = _sp.run(wrapped, shell=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"Container run failed (exit {result.returncode}): {wrapped}")

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------

    def run(self, steps=None) -> Any:
        if getattr(self.config, "container", None) and not os.environ.get("INSARHUB_CONTAINER_CHILD"):
            return self._run_via_container(steps)
        from dolphin import timeseries

        unw, ccl = self.inputs()
        mf = self.manifest()
        mode = mf.get("mode", "unknown")
        q = self.quality_file()

        ref = None
        rp = getattr(self.config, "reference_point", None)
        if rp:
            try:
                r, c = (int(x) for x in str(rp).replace(" ", "").split(","))
                ref = (r, c)
            except Exception:                                    # noqa: BLE001
                raise ValueError(f"ISCE3_Dolphin_PL: reference_point must be "
                                 f"'row,col', got {rp!r}") from None
        if q is None and ref is None:
            raise FileNotFoundError(
                "ISCE3_Dolphin_PL: need a quality raster to choose a reference "
                "point, and none was found. Either run the processor's 'ifg' "
                "stage in phase_link mode (which writes temporal coherence), "
                "or set config.reference_point='row,col'.")

        self.out_dir.mkdir(parents=True, exist_ok=True)
        print(f"[ISCE3_Dolphin_PL] stack mode   : {mode}")
        print(f"[ISCE3_Dolphin_PL] interferograms: {len(unw)}  conncomp: {len(ccl)}")
        print(f"[ISCE3_Dolphin_PL] quality file : {q.name if q else '(reference_point given)'}")
        print(f"[ISCE3_Dolphin_PL] method       : {getattr(self.config,'method','L1')}")

        method = timeseries.InversionMethod(str(
            getattr(self.config, "method", "L1")).upper())
        # Mask water out of the inversion, matching dolphin's displacement.run
        # (apply_mask_to_timeseries=True -> mask_path=water mask). On by default.
        wmask = (self.water_mask_path()
                 if bool(getattr(self.config, "apply_water_mask", True)) else None)
        print(f"[ISCE3_Dolphin_PL] water mask   : {wmask.name if wmask else '(none)'}")
        res = timeseries.run(
            unwrapped_paths=unw,
            conncomp_paths=ccl or None,
            output_dir=self.out_dir,
            quality_file=q,
            method=method,
            reference_point=ref,
            run_velocity=bool(getattr(self.config, "run_velocity", True)),
            wavelength=float(getattr(self.config, "wavelength", 0.055465764662349676)),
            correlation_threshold=float(
                getattr(self.config, "correlation_threshold", 0.0)),
            mask_path=str(wmask) if wmask else None,
            num_threads=int(getattr(self.config, "num_threads", 4)),
            block_shape=tuple(getattr(self.config, "block_shape", (256, 256))),
        )

        disp = sorted(p for p in self.out_dir.glob("*.tif")
                      if p.name[0].isdigit())
        vel = self.out_dir / "velocity.tif"
        print(f"[ISCE3_Dolphin_PL] displacement rasters: {len(disp)}")
        if vel.exists():
            print(f"[ISCE3_Dolphin_PL] velocity            : {vel}")

        # Optional insarhub-specific cleanup (off by default): trim the outputs
        # to the interferogram footprint. dolphin fills the whole padded grid; by
        # default we leave it exactly as dolphin wrote it (thin-wrapper). When
        # enabled, rebuild the mask from the CURRENT unwrapped ifgs + coherence
        # first (a stale footprint_mask.tif from an earlier processor run would
        # otherwise mask fresh outputs to the old extent), then apply it.
        if bool(getattr(self.config, "apply_footprint", False)):
            self.prep_data()
            self._apply_footprint(disp + ([vel] if vel.exists() else []))

        if str(getattr(self.config, "los_projection", "none")).lower() == "vertical":
            n = self._project_to_vertical(disp + ([vel] if vel.exists() else []))
            print(f"[ISCE3_Dolphin_PL] projected to vertical: {n} raster(s) "
                  f"-> {self.out_dir / 'vertical'}")
        return res
