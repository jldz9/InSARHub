"""
Workdir path layout definitions for each module family.

Each class takes a workdir Path and exposes sub-paths as properties.
Add a new class per satellite/processor family to keep paths centralized.

Usage:
    paths = Hyp3Paths(workdir)
    paths.output_dir        # workdir/hyp3
    paths.jobs_file         # workdir/hyp3_jobs.json
    paths.retry_file(ts)    # workdir/hyp3_retry_jobs_<ts>.json

    paths = ISCEPaths(workdir)
    paths.isce_dir              # workdir/isce
    paths.run_files_dir         # workdir/isce/run_files
    paths.step_log_dir("run_01")  # workdir/isce/run_files/run_01_logs
    paths.step_sbatch_dir("run_01")  # workdir/isce/run_files/run_01_sbatch
    paths.slc_dir               # workdir/slc
    paths.dem_dir               # workdir/dem

    paths = MintPyPaths(workdir)
    paths.mintpy_dir        # workdir/mintpy
    paths.tmp_dir           # workdir/mintpy/tmp
    paths.clip_dir          # workdir/mintpy/clip
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Hyp3Paths:
    """Path layout for HyP3 processor outputs (any satellite)."""
    workdir: Path

    @property
    def output_dir(self) -> Path:
        return self.workdir / "hyp3"

    @property
    def jobs_file(self) -> Path:
        return self.workdir / "hyp3_jobs.json"

    def retry_file(self, ts: str) -> Path:
        return self.workdir / f"hyp3_retry_jobs_{ts}.json"


@dataclass
class ISCEPaths:
    """Path layout for ISCE2 stackSentinel processor (any SAR satellite)."""
    workdir: Path

    @property
    def isce_dir(self) -> Path:
        return self.workdir / "isce"

    @property
    def run_files_dir(self) -> Path:
        return self.isce_dir / "run_files"

    def step_log_dir(self, step: str) -> Path:
        return self.run_files_dir / f"{step}_logs"

    def step_sbatch_dir(self, step: str) -> Path:
        return self.run_files_dir / f"{step}_sbatch"

    @property
    def slc_dir(self) -> Path:
        return self.workdir / "slc"

    @property
    def dem_dir(self) -> Path:
        return self.workdir / "dem"


@dataclass
class MintPyPaths:
    """Path layout for MintPy SBAS analyzer outputs (any SAR satellite)."""
    workdir: Path

    @property
    def mintpy_dir(self) -> Path:
        return self.workdir / "mintpy"

    @property
    def tmp_dir(self) -> Path:
        return self.mintpy_dir / "tmp"

    @property
    def clip_dir(self) -> Path:
        return self.mintpy_dir / "clip"
