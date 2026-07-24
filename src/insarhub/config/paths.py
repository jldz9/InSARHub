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

    paths = StackPaths(workdir)
    paths.stack_dir(100, 466)                    # workdir/p100_f466
    paths.stack_file(100, 466)                   # workdir/p100_f466/stack_p100_f466.json
    paths.merge_tag([89, 90])                    # "merged_f89_f90"
    paths.merge_dir(87, [89, 90])                # workdir/p87_merged_f89_f90
    paths.dir_for(100, 466)                       # same as stack_dir — from a select_pairs() key
    paths.dir_for(87, "merged_f89_f90")           # workdir/p87_merged_f89_f90 — merge key variant
    paths.stack_file_for(100, 466)                # same as stack_file — from a select_pairs() key
    paths.stack_file_for(87, "merged_f89_f90")    # workdir/p87_merged_f89_f90/stack_p87_merged_f89_f90.json
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

    @property
    def clipped_dir(self) -> Path:
        """AOI-clipped rasters written by utils.tool.clip_hyp3_s1()."""
        return self.workdir / "clipped"

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
    """Path layout for MintPy SBAS analyzer outputs (any SAR satellite).

    subdir names the analyzer's own output folder under workdir, so each
    MintPy-family analyzer keeps its products separate (hyp3_mintpy/,
    isce_mintpy/, gmtsar_mintpy/) instead of all sharing workdir/mintpy/
    and silently overwriting each other.
    """
    workdir: Path
    subdir: str = "mintpy"

    @property
    def mintpy_dir(self) -> Path:
        return self.workdir / self.subdir

    @property
    def tmp_dir(self) -> Path:
        return self.mintpy_dir / "tmp"

    @property
    def clip_dir(self) -> Path:
        return self.mintpy_dir / "clip"


@dataclass
class GMTSARPaths:
    """Path layout for GMTSAR_S1 stack-mode output + GMTSAR_SBAS analyzer.

    GMTSAR's own tools dictate most of this: preproc_batch_tops writes
    baseline_table.dat into raw/ (its cwd); intf_tops moves each finished
    pair from intf/ (scratch) to intf_all/<ref_jd>_<rep_jd>/.
    """
    workdir: Path

    @property
    def case_dir(self) -> Path:
        return self.workdir / "gmtsar"

    @property
    def raw_dir(self) -> Path:
        return self.case_dir / "raw"

    @property
    def topo_dir(self) -> Path:
        return self.case_dir / "topo"

    @property
    def dem_grd(self) -> Path:
        return self.topo_dir / "dem.grd"

    @property
    def intf_dir(self) -> Path:
        """intf_tops.csh's per-pair scratch dir -- it moves each finished pair
        from here to intf_all/ (intf_tops.csh:247). Also where the per-pair
        (non-stack) p2p_processing path writes intf/<julian_date_pair>/."""
        return self.case_dir / "intf"

    @property
    def intf_all_dir(self) -> Path:
        return self.case_dir / "intf_all"

    @property
    def intf_in(self) -> Path:
        return self.case_dir / "intf.in"

    @property
    def baseline_table(self) -> Path:
        return self.raw_dir / "baseline_table.dat"

    @property
    def batch_config(self) -> Path:
        return self.case_dir / "batch_tops.config"

    @property
    def sbas_dir(self) -> Path:
        return self.workdir / "gmtsar_sbas"

    # ── multi-subswath (full-frame) stack layout ──────────────────────────
    # More than one subswath runs an independent pipeline per swath in
    # F<N>/ (GMTSAR's own convention, same as p2p_S1_TOPS_Frame) and then
    # combines them with merge_batch into merge/. A single subswath keeps
    # the flat layout above.
    def swath_dir(self, n: int) -> Path:
        return self.case_dir / f"F{n}"

    def swath_raw_dir(self, n: int) -> Path:
        return self.swath_dir(n) / "raw"

    def swath_topo_dir(self, n: int) -> Path:
        return self.swath_dir(n) / "topo"

    def swath_intf_all_dir(self, n: int) -> Path:
        return self.swath_dir(n) / "intf_all"

    def swath_batch_config(self, n: int) -> Path:
        return self.swath_dir(n) / "batch_tops.config"

    @property
    def merge_dir(self) -> Path:
        return self.case_dir / "merge"

    def product_dir(self) -> Path:
        """Where the finished per-pair interferograms actually live.

        merge/ for a multi-subswath (full-frame) stack, else the flat
        intf_all/. Analyzers use this so they work against either layout.
        """
        m = self.merge_dir
        if m.is_dir() and any(d.is_dir() for d in m.iterdir()):
            return m
        return self.intf_all_dir

    def baseline_table_for(self, subswaths: list[int]) -> Path:
        """preproc_batch_tops writes baseline_table.dat into its own cwd, so
        it lands in F<N>/raw for a multi-subswath stack (any swath's table
        has the same per-date baselines) and in raw/ for a flat one."""
        if len(subswaths) > 1:
            return self.swath_raw_dir(subswaths[0]) / "baseline_table.dat"
        return self.baseline_table

    # ── layout-agnostic resolvers (analyzers don't know the subswath config,
    # so they detect the on-disk layout: F<N>/ + merge/ vs the flat case) ──
    def _swath_dirs(self) -> list[Path]:
        return sorted(d for d in self.case_dir.glob("F[1-9]") if d.is_dir()) \
            if self.case_dir.is_dir() else []

    @property
    def meta_raw_dir(self) -> Path:
        """The raw/ holding the aligned *.PRM + baseline_table.dat: the first
        F<N>/raw for a multi-subswath stack, else the flat raw/."""
        fs = self._swath_dirs()
        return (fs[0] / "raw") if fs else self.raw_dir

    @property
    def baseline_table_auto(self) -> Path:
        return self.meta_raw_dir / "baseline_table.dat"


@dataclass
class StackPaths:
    """Path layout for downloader search / pair-selection output.

    One stack is (path, frame). A merge group combines every frame sharing
    one path into a single stack — its directory/file names encode every
    constituent frame number (merge_tag) so two independent merge groups on
    the same path never collide, and so the stack file always ends up
    co-located with wherever download(merge=True) put the SLCs.
    """
    workdir: Path

    @staticmethod
    def merge_tag(frames: list[int]) -> str:
        return "merged_" + "_".join(f"f{f}" for f in sorted(set(frames)))

    @staticmethod
    def is_merge_key(frame: int | str) -> bool:
        return isinstance(frame, str) and frame.startswith("merged")

    def stack_dir(self, path: int, frame: int) -> Path:
        return self.workdir / f"p{path}_f{frame}"

    def stack_file(self, path: int, frame: int) -> Path:
        return self.stack_dir(path, frame) / f"stack_p{path}_f{frame}.json"

    def merge_dir(self, path: int, frames: list[int]) -> Path:
        return self.workdir / f"p{path}_{self.merge_tag(frames)}"

    def dir_for(self, path: int, frame: int | str) -> Path:
        """Directory for a (path, frame) key from select_pairs() output —
        frame is either a plain frame number, or an already-computed merge
        tag (str starting with "merged", e.g. from merge_tag()). Handles
        both without the caller needing to know which one it has."""
        if self.is_merge_key(frame):
            return self.workdir / f"p{path}_{frame}"
        return self.stack_dir(path, frame)

    def stack_file_for(self, path: int, frame: int | str) -> Path:
        if self.is_merge_key(frame):
            return self.dir_for(path, frame) / f"stack_p{path}_{frame}.json"
        return self.stack_file(path, frame)
