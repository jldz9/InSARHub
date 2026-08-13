"""Time-series inversion for ISCE3_Burst stacks, via dolphin.

One analyzer serves both of ``ISCE3_Burst``'s wrapped-phase estimators, because
**the inversion is the same for both**. Whether the interferograms came from a
chosen subset of pairs (``ifg_mode="network"``) or from phase linking over the
full covariance (``ifg_mode="phase_link"``), what reaches this point is a set of
unwrapped interferograms plus connected-component labels -- and
``dolphin.timeseries.run`` inverts those identically::

    A · d = phi          A = incidence matrix (n_ifg x n_date-1)
                         phi = unwrapped phase per interferogram
                         d  = cumulative displacement per date

So there is no "SBAS mode" and "phase-linking mode" here. The estimator choice
was made upstream and is recorded in the stack's ``ifg_manifest.json``; this
reads it rather than taking it again as an option, so the two cannot disagree.

What the mode genuinely changes is the **quality raster** used to pick the
reference point and mask low-quality pixels:

``phase_link``  the processor leaves a stitched temporal-coherence raster --
                how well the estimated phase history explains the observed
                covariance, which is a whole-stack measure.
``network``     there is no such single raster, only per-pair correlations, so
                those are reduced to a temporal average.

``timeseries.run`` refuses to run without one (``Must provide quality_file if
not reference_point given``), since it has no other way to choose a stable
reference pixel.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from insarhub.config import Dolphin_SBAS_Config
from insarhub.core.base import BaseAnalyzer

logger = logging.getLogger(__name__)


class Dolphin_SBAS(BaseAnalyzer):
    name = "Dolphin_SBAS"
    description = ("Time-series inversion of an ISCE3_Burst stack with dolphin: "
                   "cumulative displacement per date, velocity, residuals. "
                   "Works for both ifg_mode=network and ifg_mode=phase_link.")
    compatible_processor = "ISCE3_Burst"
    default_config = Dolphin_SBAS_Config

    def __init__(self, config=None):
        super().__init__(config=config)
        wd = getattr(self.config, "workdir", None) or Path.cwd()
        self.workdir = Path(wd).expanduser().resolve()

    # ------------------------------------------------------------------
    # stack discovery
    # ------------------------------------------------------------------

    def manifest(self) -> dict:
        """The ifg stage's record of how the wrapped phase was estimated.

        Read from the stack's own tree first (``<stack>/stack_info.json``, next
        to the stitched products). With both ifg_modes present in one workdir a
        search by convention over ``ifgrams*/`` picks whichever exists first,
        which mislabels the stack without failing -- so that is only the
        fallback, for stacks produced before the processor wrote the sidecar.
        """
        p = self.unwrap_dir.parent / "stack_info.json"
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:                                    # noqa: BLE001
                logger.warning("Dolphin_SBAS: unreadable %s", p)
        cands = sorted(self.workdir.glob("ifgrams*/ifg_manifest.json"))
        if len(cands) > 1:
            logger.warning("Dolphin_SBAS: %d ifg manifests under %s and no "
                           "stack_info.json beside %s -- the reported mode may "
                           "not match this stack", len(cands), self.workdir,
                           self.unwrap_dir)
        for c in cands:
            try:
                return json.loads(c.read_text())
            except Exception:                                    # noqa: BLE001
                continue
        return {}

    @property
    def unwrap_dir(self) -> Path:
        v = getattr(self.config, "unwrap_dir", None)
        if v:
            return Path(v).expanduser()
        # phase-link runs are conventionally written alongside, not over, the
        # network ones; prefer whichever actually holds unwrapped products
        for cand in (self.workdir / "stitched" / "unwrapped",
                     self.workdir / "stitched_pl" / "unwrapped"):
            if any(cand.glob("*.unw.tif")):
                return cand
        return self.workdir / "stitched" / "unwrapped"

    @property
    def out_dir(self) -> Path:
        v = getattr(self.config, "output_dir", None)
        return Path(v).expanduser() if v else self.workdir / "timeseries"

    def inputs(self) -> tuple[list[Path], list[Path]]:
        """Unwrapped interferograms and their connected-component labels."""
        unw = sorted(p for p in self.unwrap_dir.glob("*.unw.tif")
                     if "conncomp" not in p.name)
        if not unw:
            raise FileNotFoundError(
                f"Dolphin_SBAS: no unwrapped interferograms in {self.unwrap_dir}; "
                "run the processor's 'unwrap' stage first")
        ccl, missing = [], []
        for u in unw:
            c = u.with_suffix(".conncomp.tif")
            (ccl if c.exists() else missing).append(c)
        if missing:
            # Not fatal, but worth saying plainly: without them the inversion
            # cannot tell an unwrapping jump from real displacement.
            logger.warning("Dolphin_SBAS: %d interferogram(s) have no conncomp; "
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

        # phase_link: the processor mosaics temporal coherence next to the
        # stitched interferograms
        for cand in (self.unwrap_dir.parent / "temporal_coherence.tif",):
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
        filt = self.unwrap_dir.parent / "ifgrams_filtered"
        cors = sorted(filt.glob("*.cor.tif"))
        if not cors:
            logger.warning("Dolphin_SBAS: no correlation rasters in %s", filt)
            return None
        from dolphin import timeseries

        out = self.unwrap_dir.parent / "avg_correlation.tif"
        if not out.exists():
            timeseries.create_temporal_average(
                file_list=cors, output_file=out,
                block_shape=tuple(getattr(self.config, "block_shape", (256, 256))),
                num_threads=int(getattr(self.config, "num_threads", 4)))
            logger.info("Dolphin_SBAS: built %s from %d correlation rasters",
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

        los_up = self.workdir / "stitched" / "geometry" / "los_up.tif"
        if not los_up.exists():
            logger.error("Dolphin_SBAS: los_projection='vertical' needs %s -- "
                         "run the processor's 'los' stage (it mosaics COMPASS's "
                         "per-burst static layers onto the interferogram grid, "
                         "so it runs after 'filt', not inside 'static')", los_up)
            return 0
        dest = self.out_dir / "vertical"
        dest.mkdir(parents=True, exist_ok=True)

        # The geometry rasters are built on whichever interferogram grid existed
        # when 'static' ran, and the two ifg_modes do not share a grid (looks vs
        # strides round differently -- 710 rows against 711 here). Resample onto
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
                    logger.error("Dolphin_SBAS: could not match los_up to %s "
                                 "(%s vs %s); skipping", f.name, inv.shape, a.shape)
                    continue
            io.write_arr(arr=(a * inv).astype("float32"),
                         output_name=dest / f.name, like_filename=f,
                         driver="GTiff", nodata=float("nan"),
                         options=["COMPRESS=DEFLATE", "TILED=YES"])
            n += 1
        (dest / "README.txt").write_text(
            "Vertical displacement = LOS / u_up, where u_up is the up-component\n"
            "of the ground-to-satellite unit vector (stitched/geometry/los_up.tif).\n"
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
            Path(self.workdir), "sbas", "Dolphin_SBAS",
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

        print(f"{Fore.GREEN}Dolphin_SBAS job submitted: {job_id}{Style.RESET_ALL}")
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
        if getattr(self.config, "container", None):
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
                raise ValueError(f"Dolphin_SBAS: reference_point must be "
                                 f"'row,col', got {rp!r}") from None
        if q is None and ref is None:
            raise FileNotFoundError(
                "Dolphin_SBAS: need a quality raster to choose a reference "
                "point, and none was found. Either run the processor's 'ifg' "
                "stage in phase_link mode (which writes temporal coherence), "
                "or set config.reference_point='row,col'.")

        self.out_dir.mkdir(parents=True, exist_ok=True)
        print(f"[Dolphin_SBAS] stack mode   : {mode}")
        print(f"[Dolphin_SBAS] interferograms: {len(unw)}  conncomp: {len(ccl)}")
        print(f"[Dolphin_SBAS] quality file : {q.name if q else '(reference_point given)'}")
        print(f"[Dolphin_SBAS] method       : {getattr(self.config,'method','L1')}")

        method = timeseries.InversionMethod(str(
            getattr(self.config, "method", "L1")).upper())
        res = timeseries.run(
            unwrapped_paths=unw,
            conncomp_paths=ccl or None,
            output_dir=self.out_dir,
            quality_file=q,
            method=method,
            reference_point=ref,
            run_velocity=bool(getattr(self.config, "run_velocity", True)),
            wavelength=float(getattr(self.config, "wavelength", 0.055465763)),
            correlation_threshold=float(
                getattr(self.config, "correlation_threshold", 0.0)),
            num_threads=int(getattr(self.config, "num_threads", 4)),
            block_shape=tuple(getattr(self.config, "block_shape", (256, 256))),
        )

        disp = sorted(p for p in self.out_dir.glob("*.tif")
                      if p.name[0].isdigit())
        vel = self.out_dir / "velocity.tif"
        print(f"[Dolphin_SBAS] displacement rasters: {len(disp)}")
        if vel.exists():
            print(f"[Dolphin_SBAS] velocity            : {vel}")

        if str(getattr(self.config, "los_projection", "none")).lower() == "vertical":
            n = self._project_to_vertical(disp + ([vel] if vel.exists() else []))
            print(f"[Dolphin_SBAS] projected to vertical: {n} raster(s) "
                  f"-> {self.out_dir / 'vertical'}")
        return res
