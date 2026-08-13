# -*- coding: utf-8 -*-
"""
GMTSAR_S1 — Sentinel-1 InSAR processor backed by GMTSAR's Python
p2p_processing / p2p_S1_TOPS_Frame pipelines.

STATUS: v4, fused HPC stages + topo precompute (2026-07-28) -- align_F<N>/
topo_F<N>/intf_F<N> collapsed into single "align"/"topo"/"intf" stages, each
one manager whose sliding window fans out across every subswath (and, for
intf, every pair within each subswath) as concurrent child jobs, instead of
one manager per subswath chained strictly sequentially -- align_F<N>/
topo_F<N> have no data dependency on each other, so there was no reason to
force them through a single-file-of-work chain. Also added the "topo" stage
itself: previously each intf pair recomputed topo_ra.grd from scratch
(intf_tops.csh's Stage 1), which is unsafe under any pair concurrency --
Stage 1 starts with `cleanup.csh topo`, deleting whatever a concurrently-
running pair is still using -- confirmed as a real failure cause on a live
run ("No datapoints inside region" from an empty, torn trans.dat). GMTSAR's
own intf_tops_parallel.csh has this identical bug. Now topo_ra.grd is
computed exactly once per subswath before any pair's intf starts; see
run_stage_unit()/_run_topo_unit()'s docstrings.

STATUS: v3, frame_mode dropped (2026-07-27) -- single- vs multi-subswath
dispatch is now inferred from whether config.subswath names one IW or
several (see _multiswath), instead of a separate frame_mode bool. It was
purely redundant: p2p_S1_TOPS_Frame doesn't take a subswath argument at
all (always processes every subswath present), so frame_mode only ever
encoded "does subswath name one value or all of them" -- information
subswath's own shape already carries, the same way stack_mode's own
multi-subswath handling already worked.

STATUS: v2, unified pairs signature (2026-07-21). Both modes now take the
same pairs = [(ref_safe, ref_eof, sec_safe, sec_eof), ...] shape -- raw
.SAFE + .EOF names, nothing more. v1 required single-subswath callers to
hand-derive raw per-subswath product stems themselves; that was correct
per GMTSAR's own CLI contract but bad UX and untested against real data.
Real end-to-end validation (multi-subswath Frame mode, see
docs/gmtsar_s1_notes/OPEN_ISSUES.md) confirmed the pipeline genuinely
works; this revision closes the remaining gap by having GMTSAR_S1 itself
do the single-subswath extraction that single-subswath mode needs.

Two distinct GMTSAR entry points, selected by whether config.subswath
names one IW or several (_multiswath):

  Single subswath (e.g. subswath="2") -- via p2p_processing.
    p2p_processing does not read .SAFE directories itself -- it expects
    one subswath's .tiff/.xml already extracted to matching-stem files
    in raw/ (confirmed against p2p_processing's own usage string, AND
    independently against GMTSAR's bundled single-subswath test fixture,
    H_res/raw/: its per-stem .tiff/.xml/.EOF files are plain symlinks
    into the equivalent Frame-mode F<N>/raw/ subswath files pulled from
    the same .SAFE). GMTSAR_S1._extract_subswath_stem() reproduces that
    extraction (glob measurement/annotation for config.subswath +
    config.polarization, symlink the matching .EOF under the same stem)
    so callers only ever pass raw .SAFE/.EOF names, same as Frame mode:
        p2p_processing S1_TOPS \
          s1a-iw2-slc-vv-20190704t135158-20190704t135223-027968-032877-005 \
          s1a-iw2-slc-vv-20190716t135159-20190716t135224-028143-032dc3-005 \
          config.py
    One shared case_dir for the whole pairs list -- p2p_processing's own
    output (intf/<julian_date_pair>/, e.g. intf/2019184_2019196/ --
    GMTSAR's own Julian-date naming, NOT ref/sec stems, confirmed via a
    real run + real MintPy testing) is pair-namespaced, so
    concurrent pairs don't collide.

  Multiple subswaths (e.g. subswath="1 2 3", the default) -- multi-subswath
    Frame, via p2p_S1_TOPS_Frame, which always processes every subswath
    present (it takes no subswath argument itself).
    pairs = [(ref_safe, ref_eof, sec_safe, sec_eof), ...] -- .SAFE
    directory names + matching .EOF orbit filenames (confirmed against
    gmtsar/python/utils/p2p_S1_TOPS_Frame's own usage string and a real
    recipe, tests/recipes/README_S1A_SLC_TOPS_LA.txt):
        p2p_S1_TOPS_Frame Master.SAFE Master.EOF Aligned.SAFE Aligned.EOF \
          config.py vv 1
    p2p_S1_TOPS_Frame is NOT pair-namespaced -- it always writes
    F1/F2/F3/merge/ into its current working directory (confirmed by
    tracing the script: no per-pair subdirectory logic exists). So each
    pair gets its OWN case subdirectory (case_dir/<ref>_<sec>/), not a
    shared one -- otherwise pair 2 would silently overwrite pair 1's
    merge/ output.

Interface mirrors ISCE2_S1 / Hyp3_S1:
  submit()  -- stage case dir(s), launch the right GMTSAR entry point per
               pair (subprocess), up to max_workers concurrent.
  refresh() -- read per-pair status, print a table.
  retry()   -- re-run failed pairs (and only failed pairs).
  watch()   -- poll until every pair is SUCCEEDED or FAILED.
  save()    -- persist gmtsar_jobs.json, matching ISCE2_S1's isce_jobs.json.

Why no custom output-normalization step (single-subswath mode only -- see
KNOWN GAP below for multi-subswath mode): GMTSAR's native per-pair output
directory (intf/<julian_date_pair>/ -- e.g. intf/2019184_2019196/,
GMTSAR's own Julian-date pair naming derived from each SLC's
SC_clock_start, NOT the ref/sec stems passed on the CLI -- confirmed
via a real run + reading intf/'s actual contents; the ref_stem_sec_stem
name this docstring used to claim was wrong, found via real MintPy
integration testing 2026-07-21) contains corr_ll.grd, phasefilt_ll.grd,
and *.PRM files, matching what MintPy's own prep_gmtsar.py expects
(confirmed by reading mintpy/prep_gmtsar.py directly: it globs
`{fbase}_ll*.grd` and `*.PRM`, and derives DATE12 from the Julian-date
directory basename itself, so GMTSAR's naming is not just compatible --
prep_gmtsar.py actually requires it). GMTSAR_S1 discovers the real
directory post-run (_run_one_pair()) rather than assuming a name.
KNOWN GAP: multi-subswath mode's real output lands in merge/ with the same
file basenames but has NOT been checked against prep_gmtsar.py's
directory-discovery logic -- needs a real Frame-mode run + a
prep_gmtsar.py dry run before that claim can be made for Frame mode too.

Deliberately kept as a subprocess-per-pair design, not in-process Python
calls into GMTSAR's own p2p_stages.py: (1) InSARHub and GMTSAR run in
separate conda environments with different numpy/GDAL stacks -- importing
GMTSAR's stage code in-process risks real dependency collisions; (2) most
wall-clock is spent in C binaries (gmt, snaphu) either way, so in-process
Python orchestration wouldn't meaningfully speed anything up; (3) this
matches both ISCE2_S1's own external-process pattern AND GMTSAR's own test
harness (case_runner.py), which deliberately runs each case in its own
subprocess for process-group isolation.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from insarhub.config import GMTSAR_S1_Config
from insarhub.config.paths import GMTSARPaths
from insarhub.core import LocalProcessor
from insarhub.utils.slurm_manager import sbatch_template_header
from insarhub.utils.tool import Slurmjob_Config

logger = logging.getLogger(__name__)

_PENDING = "PENDING"
_RUNNING = "RUNNING"
_SUCCEEDED = "SUCCEEDED"
_FAILED = "FAILED"

JOBS_FILE = "gmtsar_jobs.json"

# Own default template, passed to isce2_base.py's shared load_or_init_sbatch_options()
# so a workdir whose *first* HPC submission is GMTSAR gets a GMTSAR-only
# sbatch_options.json instead of ISCE's irrelevant numbered "01".."17" steps
# (both processors still share the one file/mechanism -- see that function's
# docstring). Stage keys are the same regardless of subswath count -- align/
# topo/intf are each ONE manager whose sliding window fans out across every
# subswath (and, for intf, every pair within each subswath) as concurrent
# child jobs, rather than one manager per subswath. GMTSAR's own align_F<N>/
# topo_F<N>/intf_F<N> stages are fully independent of each other (different
# subswath directories, no data dependency), so there's no reason to force
# them through separate, strictly-sequential chain links -- see
# _stage_commands()/run_stage_unit()'s docstrings.
_GMTSAR_SBATCH_DEFAULT_TEMPLATE: dict = {
    # Shared self-documenting header -- see slurm_manager.sbatch_template_header.
    # Each stage is one manager fanning out across every subswath (and for intf
    # every pair too) as concurrent child jobs; see _stage_commands().
    **sbatch_template_header(),
    "_steps": {
        "p2p": "p2p mode (stack_mode=False): ONE job per pair -- the whole "
               "p2p_processing / p2p_S1_TOPS_Frame chain (align, ifg, filter, "
               "unwrap, geocode) for that pair. Sized like unwrap, since snaphu "
               "dominates: ~3h and ~15GB of output per full-frame pair.",
        "align": "align (one manager, all subswaths concurrent)",
        "topo":  "topo precompute (one manager, all subswaths concurrent)",
        "intf":  "intf (one manager, all subswath x pair combinations concurrent)",
        "mergeprep": "merge seed pair (produces the shared trans.dat)",
        "merge": "merge (one manager, all remaining pairs concurrent)",
        "cohmask": "stacked-coherence mask (mask_def.grd), once, before unwrap",
        "unwrap": "snaphu unwrap + geocode (one manager, all pairs concurrent)",
        "sbas": "SBAS (MintPy smallbaselineApp, via GMTSAR_MINTPY_SBAS)",
        "manager": "job managers -- only 'partition' is used; cores/memory/walltime are fixed (1 idle core, partition max walltime)",
    },
    # Job managers are idle single-core babysitters that must outlive every
    # child they supervise, so they suit a long-walltime queue even when the
    # real work belongs elsewhere. Only "partition" is read here.
    "manager": {"partition": "all"},
    # p2p runs an entire pair end-to-end in one job, so it needs the whole
    # pipeline's walltime -- not a single stage's.
    "p2p": {"cpus_per_task": 4, "mem": "24G", "time": "24:00:00"},
    "default": {
        "time":          "02:00:00",
        "partition":     "all",
        "nodes":         1,
        "ntasks":        1,
        "cpus_per_task": 2,
        "mem":           "8G",
    },
    # align/merge are full-frame-per-swath work (heavier); topo is a single
    # lightweight precompute (Stage 1 of intf_tops.csh -- topo_ra.grd depends
    # only on the fixed super-master + DEM, not on any pair, and must run
    # exactly once before any intf pair starts -- see run_stage_unit()'s
    # docstring for the shared-topo/ race this avoids); intf is per-pair
    # (lighter -- one child job per pair already provides the real
    # parallelism, see this module's docstring).
    "align": {"cpus_per_task": 4, "mem": "16G", "time": "03:00:00"},
    "topo":  {"cpus_per_task": 2, "mem": "8G",  "time": "01:00:00"},
    "intf":  {"cpus_per_task": 2, "mem": "8G",  "time": "02:00:00"},
    "mergeprep": {"cpus_per_task": 4, "mem": "16G", "time": "04:00:00"},
    "merge":     {"cpus_per_task": 4, "mem": "16G", "time": "04:00:00"},
    # unwrapping is the long pole of a multi-subswath run (single pairs have
    # exceeded 4 h on the merged frame), so it gets the generous walltime
    "cohmask":   {"cpus_per_task": 2, "mem": "16G", "time": "02:00:00"},
    "unwrap":    {"cpus_per_task": 4, "mem": "16G", "time": "24:00:00"},
    # GMTSAR_MINTPY_SBAS's HPC submission (Mintpy_SBAS_Base_Analyzer.submit_hpc,
    # shared code with ISCE_SBAS/Hyp3_SBAS). Same resource needs as ISCE's own
    # SBAS entry -- it's the identical MintPy smallbaselineApp workflow either
    # way -- but keyed "sbas", not "17": ISCE's numbers are stackSentinel run-file
    # indices (SBAS being its 17th and last step), and GMTSAR has no such
    # numbering, so borrowing "17" here would name nothing.
    "sbas": {"time": "24:00:00", "ntasks": 1, "cpus_per_task": 16, "mem": "128G"},
}


# GMTSAR's own multi-sensor coverage (see gmtsar/python/tests/cases.py
# upstream in the GMTSAR repo) -- GMTSAR_S1 only exercises S1_TOPS today,
# but every one of these is already a real, tested SAT argument to
# p2p_processing. Listed here so a future GMTSAR_<sensor> processor
# doesn't have to re-derive this from scratch.
SUPPORTED_SATS = (
    "ERS", "ENVI", "ENVI_SLC", "ALOS", "ALOS_SLC", "ALOS2", "ALOS2_SCAN",
    "S1_STRIP", "S1_TOPS", "CSK_RAW", "CSK_SLC", "TSX", "RS2", "GF3",
)

# Both modes take the same pair shape now (see module docstring, STATUS v2).
Pair = tuple  # (ref_safe, ref_eof, sec_safe, sec_eof)


def _pair_key(pair: tuple) -> str:
    ref_safe, _ref_eof, sec_safe, _sec_eof = pair
    return f"{ref_safe}_{sec_safe}"


def _pair_dates(pair: tuple) -> str:
    """``YYYYMMDD_YYYYMMDD`` for display. Two .SAFE names are ~136 characters
    side by side and unreadable in a status table; the dates identify the pair."""
    import re as _re

    out = []
    for name in (pair[0], pair[2]):
        m = _re.search(r"_(\d{8})T\d{6}_", str(name))
        out.append(m.group(1) if m else str(name)[:8])
    return "_".join(out)


def _gmtsar_stem(raw_stem: str) -> str:
    """Derive GMTSAR's PRM basename (S1_<YYYYMMDD>_<HHMMSS>_F<subswath>)
    from a raw Sentinel-1 measurement stem
    (s1a-iw2-slc-vv-20210108t133459-...). Mirrors the exact 1-indexed
    substr logic in preproc_batch_tops.csh
    (S1_<substr 16,8>_<substr 25,6>_F<substr 7,1>) so the stems this
    module writes into intf.in match the *.PRM filenames GMTSAR's own
    preproc_batch_tops writes into raw/ -- otherwise intf_tops can't find
    them."""
    return f"S1_{raw_stem[15:23]}_{raw_stem[24:30]}_F{raw_stem[6]}"


def _gmtsar_aligned_stem(raw_stem: str) -> str:
    """The ALIGNED/assembled stem preproc_batch_tops produces per date:
    S1_<YYYYMMDD>_ALL_F<subswath> (matches preproc_batch_tops.csh's
    `mmaster` naming). This is the stem that has the coregistered .SLC/.PRM
    in raw/ and the row in baseline_table.dat -- so intf.in and
    batch_tops.config's master_image must use THIS form, not _gmtsar_stem's
    per-acquisition-time form (which only names the pre-alignment
    per-subswath focus product, with no .SLC)."""
    return f"S1_{raw_stem[15:23]}_ALL_F{raw_stem[6]}"


def _read_status(status_dir: Path) -> str:
    if (status_dir / ".succeeded").exists():
        return _SUCCEEDED
    if (status_dir / ".failed").exists():
        return _FAILED
    if status_dir.exists():
        return _RUNNING
    return _PENDING


def _write_status(status_dir: Path, status: str) -> None:
    status_dir.mkdir(parents=True, exist_ok=True)
    for name in (".succeeded", ".failed"):
        f = status_dir / name
        if f.exists():
            f.unlink()
    if status == _SUCCEEDED:
        (status_dir / ".succeeded").touch()
    elif status == _FAILED:
        (status_dir / ".failed").touch()


# ── GMTSAR discovery ─────────────────────────────────────────────────────────

_CONTAINER_HINT = (
    "\nAlternatively, skip installing GMTSAR locally and run this processor "
    "inside a container instead: pass --container <path-or-image> (the "
    "container needs `insarhub` installed alongside GMTSAR — see the "
    "--container docs)."
)


def _gmtsar_root_candidates(gmtsar_root: Path | None):
    """Every place a GMTSAR install might be, in priority order, as
    (path, how_it_was_found) pairs. Yields candidates only -- validity is
    checked by the caller."""
    import shutil
    if gmtsar_root:
        yield Path(gmtsar_root), "config gmtsar_root"
    env_root = os.environ.get("GMTSAR")
    if env_root:
        yield Path(env_root), "$GMTSAR"
    which = shutil.which("preproc_batch_tops.csh")
    if which:
        # GMTSAR's own scripts live in <gmtsar_root>/bin/
        yield Path(which).resolve().parent.parent, "preproc_batch_tops.csh on $PATH"
    # Common sibling layouts, so a reinstall under a new directory name is
    # picked up without editing every workdir's insarhub_config.json.
    seen = set()
    for base in (Path.home() / "dev", Path.home(), Path("/opt"), Path("/usr/local")):
        if not base.is_dir():
            continue
        try:
            for cand in sorted(base.glob("[Gg][Mm][Tt][Ss][Aa][Rr]*")):
                if cand.is_dir() and cand not in seen:
                    seen.add(cand)
                    yield cand, f"scan of {base}"
        except OSError:
            continue


def _find_gmtsar_root(gmtsar_root: Path | None) -> Path:
    """Return GMTSAR's repo root, raising only if nothing valid is findable.

    Order: explicit gmtsar_root, $GMTSAR (GMTSAR's own install docs have
    users set this), a known GMTSAR script on $PATH, then a scan of the
    usual install locations.

    A configured gmtsar_root that no longer exists does NOT abort -- it is
    warned about and skipped. GMTSAR gets reinstalled, renamed and moved
    (this happened here: a tree was deleted and rebuilt under a different
    name), and every workdir's insarhub_config.json would otherwise have to
    be hand-edited in lockstep. The warning keeps a genuinely wrong path
    visible rather than silently ignored.
    """
    tried = []
    for cand, how in _gmtsar_root_candidates(gmtsar_root):
        if (cand / "bin" / "preproc_batch_tops.csh").exists():
            if tried:
                logger.warning(
                    "gmtsar_root %s is not a GMTSAR install; using %s (found via %s). "
                    "Update gmtsar_root to silence this.", tried[0], cand, how)
            return cand
        tried.append(str(cand))
    raise EnvironmentError(
        "GMTSAR is not installed or not findable"
        + (f" (checked: {', '.join(tried)})" if tried else "")
        + ". Install GMTSAR (gmtsar/python/install.py) and either set "
        "$GMTSAR or add its bin/ directory to $PATH, or pass gmtsar_root= "
        "to the config." + _CONTAINER_HINT
    )


def _find_gmtsar_env_bin(gmtsar_env_bin: Path | None) -> Path:
    """Return the bin/ dir of the conda env providing GMTSAR's `gmt` binary.

    Checked in order: explicit gmtsar_env_bin, a best-effort scan of sibling
    conda envs for one with a real `gmt` binary in its own bin/, then a bare
    shutil.which("gmt") as a last resort. Unlike gmtsar_root ($GMTSAR)
    there's no standard env var for this -- it's an InSARHub-specific
    bridging need (InSARHub's own env deliberately doesn't ship `gmt`, see
    _subprocess_env()'s docstring), not a GMTSAR install convention.
    """
    if gmtsar_env_bin:
        p = Path(gmtsar_env_bin)
        if (p / "gmt").exists():
            return p
        raise EnvironmentError(
            f"No `gmt` binary found under gmtsar_env_bin='{gmtsar_env_bin}'."
            + _CONTAINER_HINT
        )
    # Conda-env scan first, deliberately ahead of a bare shutil.which("gmt"):
    # a `gmt` found loose on $PATH could be an unrelated system package (e.g.
    # /usr/bin/gmt from a distro repo) with no numba/scipy alongside it --
    # confirmed as a real false positive on a real host during development.
    # A conda env's own bin/ having `gmt` is a much stronger signal that
    # it's the actual GMTSAR-provisioned environment.
    conda_exe = os.environ.get("CONDA_EXE")
    if conda_exe:
        envs_dir = Path(conda_exe).resolve().parent.parent / "envs"
        if envs_dir.is_dir():
            for env_dir in sorted(envs_dir.iterdir()):
                candidate = env_dir / "bin" / "gmt"
                if candidate.exists():
                    return candidate.parent
    import shutil
    which = shutil.which("gmt")
    if which:
        return Path(which).resolve().parent
    raise EnvironmentError(
        "No `gmt` binary found anywhere -- InSARHub's own environment "
        "deliberately doesn't provide it (see _subprocess_env()'s "
        "docstring). Install/activate GMTSAR's own conda environment (the "
        "one providing `gmt`) so it's on $PATH, or pass gmtsar_env_bin= to "
        "the config explicitly." + _CONTAINER_HINT
    )


_SCENE_TIME_RE = re.compile(r"(\d{8}T\d{6})")
_EOF_VALIDITY_RE = re.compile(r"V(\d{8}T\d{6})_(\d{8}T\d{6})")


def _scene_start_time(scene_name: str) -> str:
    """Extract the acquisition start time (YYYYMMDDTHHMMSS) from a bare
    ASF scene name, e.g.
    S1A_IW_SLC__1SDV_20210108T133459_20210108T133526_036047_043970_92D3
    -> "20210108T133459" (the first, not second, timestamp -- start, not
    stop, of acquisition)."""
    m = _SCENE_TIME_RE.search(scene_name)
    if not m:
        raise ValueError(f"could not parse acquisition time from scene name: {scene_name!r}")
    return m.group(1)


def resolve_scene_eof(scene_name: str, orbit_dir: Path, cache_dir: Path | None = None) -> Path:
    """Return the .EOF orbit file covering scene_name's acquisition time.

    Local-first: scans orbit_dir for a .EOF whose own V<start>_<end>
    validity window covers the scene -- avoids a redundant network call
    when downloader output has already staged the matching .EOF (the
    common case, e.g. from an earlier select_pairs() baseline computation
    or download_orbit() run). Falls back to
    insarhub.utils.tool._ensure_poeorb() (download/find in the POEORB
    cache) only if nothing local covers it, then returns that cached path
    directly -- callers needing it under orbit_dir specifically should
    symlink it there themselves.
    """
    orbit_dir = Path(orbit_dir)
    scene_time = _scene_start_time(scene_name)
    if orbit_dir.is_dir():
        for eof in orbit_dir.glob("*.EOF"):
            m = _EOF_VALIDITY_RE.search(eof.name)
            if not m:
                continue
            start, end = m.group(1), m.group(2)
            if start <= scene_time <= end:
                return eof

    from insarhub.utils.tool import _ensure_poeorb, _POEORB_DEFAULT_CACHE

    resolved = _ensure_poeorb(scene_name, Path(cache_dir) if cache_dir else _POEORB_DEFAULT_CACHE)
    if resolved is None:
        raise FileNotFoundError(
            f"no .EOF covering {scene_name} found under {orbit_dir}, and "
            f"POEORB download/lookup also failed -- see logged warning above"
        )
    return resolved


def pairs_from_downloader(
    stack_json_or_pairs,
    slc_dir,
    orbit_dir=None,
) -> list[Pair]:
    """Convert InSARHub downloader pair-selection output (bare ASF scene
    name 2-tuples, e.g. from `insarhub downloader --select-pairs`'s
    stack_p<path>_f<frame>.json) into the 4-tuple
    (ref_safe, ref_eof, sec_safe, sec_eof) shape GMTSAR_S1 requires.

    stack_json_or_pairs: either a path to a stack_p*_f*.json file (reads
    its "pairs" key) or an already-loaded flat [[ref, sec], ...] list --
    same two shapes cli/main.py's _load_pairs() already normalizes to.

    orbit_dir defaults to slc_dir (downloader output stages .EOF files
    alongside the SLCs). .SAFE extraction from .zip (if the scene hasn't
    been unzipped yet) is handled separately, by GMTSAR_S1's own
    _stage_case() at submit() time -- this function only builds the pair
    tuples.
    """
    if isinstance(stack_json_or_pairs, (str, Path)):
        data = json.loads(Path(stack_json_or_pairs).read_text())
        raw_pairs = data.get("pairs", []) if isinstance(data, dict) else data
    else:
        raw_pairs = stack_json_or_pairs

    slc_dir = Path(slc_dir)
    orbit_dir = Path(orbit_dir) if orbit_dir else slc_dir

    eof_cache: dict[str, str] = {}

    def _eof_name(scene: str) -> str:
        if scene not in eof_cache:
            eof_cache[scene] = resolve_scene_eof(scene, orbit_dir).name
        return eof_cache[scene]

    pairs: list[Pair] = []
    for ref, sec in raw_pairs:
        pairs.append((f"{ref}.SAFE", _eof_name(ref), f"{sec}.SAFE", _eof_name(sec)))
    return pairs


class GMTSAR_S1(LocalProcessor):
    """Sentinel-1 InSAR processor backed by GMTSAR.

    Both modes take the same pairs shape -- .SAFE + .EOF names.

    Usage, single-subswath (mirrors ISCE2_S1's own docstring example)::

        from insarhub.processor import GMTSAR_S1
        from insarhub.config import GMTSAR_S1_Config

        proc = GMTSAR_S1(
            pairs  = [("S1A_IW_SLC__1SSV_20150526T014935_20150526T015002_006086_007E23_679A.SAFE",
                       "S1A_OPER_AUX_POEORB_OPOD_20150627T155155_V20150606T225944_20150608T005944.EOF",
                       "S1A_IW_SLC__1SDV_20150607T014936_20150607T015003_006261_00832E_3626.SAFE",
                       "S1A_OPER_AUX_POEORB_OPOD_20150615T155109_V20150525T225944_20150527T005944.EOF")],
            config = GMTSAR_S1_Config(
                workdir   = '/data/stack',
                slc_dir   = '/data/slcs',
                orbit_dir = '/data/orbits',
                dem_path  = '/data/dem.grd',
                subswath  = 2,  # IW2 only -- single-subswath, via p2p_processing
            ),
        )
        proc.submit()
        proc.watch()

    Usage, multi-subswath Frame (subswath names more than one IW; same
    pairs shape, dispatches to p2p_S1_TOPS_Frame automatically -- this is
    also the default, since subswath defaults to "1 2 3")::

        proc = GMTSAR_S1(
            pairs  = [("S1A_IW_SLC__1SSV_20150526T014935_20150526T015002_006086_007E23_679A.SAFE",
                       "S1A_OPER_AUX_POEORB_OPOD_20150627T155155_V20150606T225944_20150608T005944.EOF",
                       "S1A_IW_SLC__1SDV_20150607T014936_20150607T015003_006261_00832E_3626.SAFE",
                       "S1A_OPER_AUX_POEORB_OPOD_20150615T155109_V20150525T225944_20150527T005944.EOF")],
            config = GMTSAR_S1_Config(
                workdir = '/data/stack', slc_dir = '/data/slcs',
                orbit_dir = '/data/orbits', dem_path = '/data/dem.grd',
                subswath = "1 2 3",
            ),
        )
        proc.submit()
        proc.watch()
    """

    name = "GMTSAR_S1"
    description = (
        "Sentinel-1 InSAR via GMTSAR (p2p_processing or p2p_S1_TOPS_Frame). "
        "Requires GMTSAR installed (gmtsar/python/install.py) and GMTSAR "
        "env vars set. Single-interferogram metadata (ALOOKS/RLOOKS/HEADING/"
        "geo-transform) is real-data confirmed loadable via MintPy's "
        "prep_gmtsar.py -- full stack loading additionally needs a real "
        "multi-pair baseline_table.dat GMTSAR_S1 does not generate yet, "
        "see docs/gmtsar_s1_notes/OPEN_ISSUES.md."
    )
    compatible_downloader = "S1_SLC"
    default_config = GMTSAR_S1_Config
    JOBS_FILE = JOBS_FILE      # "gmtsar_jobs.json" -- module constant, exposed for cli/main.py
    JOBS_SUBDIR = "gmtsar"  # matches case_dir property below; sibling to
                            # workdir/isce/, workdir/hyp3/, workdir/mintpy/

    def __init__(
        self,
        pairs: list[Pair],
        config: GMTSAR_S1_Config | None = None,
    ):
        super().__init__(config)
        self.config: GMTSAR_S1_Config = self.config or GMTSAR_S1_Config()
        if not pairs and not getattr(self, "jobs", None):
            raise ValueError(
                "pairs must be non-empty: 4-tuples (ref_safe, ref_eof, "
                "sec_safe, sec_eof) -- same shape for single- and "
                "multi-subswath modes."
            )
        for p in pairs:
            if len(p) != 4:
                raise ValueError(
                    "pairs must be 4-tuples (ref_safe, ref_eof, sec_safe, "
                    f"sec_eof), got a {len(p)}-tuple: {p!r}"
                )
        # Fail fast at construction, not deep inside a background staging
        # thread -- found via audit: dem_path was previously only checked
        # inside _stage_one_case_dir(), so a misconfigured processor would
        # construct fine and only fail after submit() had already started.
        # gmtsar_root/gmtsar_env_bin are needed for the same reason
        # _subprocess_env() exists at all (see its docstring): InSARHub's
        # own env does not provide `gmt`, so silently falling back to the
        # inherited PATH fails almost instantly with no useful error. Both
        # auto-detect when unset (see _find_gmtsar_root/_find_gmtsar_env_bin)
        # -- skipped entirely when config.container is set, since GMTSAR
        # discovery then happens *inside* the container instead (that
        # environment's own gmtsar_root/gmtsar_env_bin, not the host's).
        # dem_path is optional: when None, a GMTSAR DEM is auto-downloaded
        # at staging time via `make_dem` (SRTM) from the SLC footprint or
        # config.bbox -- see _ensure_dem().
        if not self.config.container:
            self.config.gmtsar_root = str(_find_gmtsar_root(
                Path(self.config.gmtsar_root) if self.config.gmtsar_root else None))
            self.config.gmtsar_env_bin = str(_find_gmtsar_env_bin(
                Path(self.config.gmtsar_env_bin) if self.config.gmtsar_env_bin else None))
        self.pairs = pairs
        self.jobs: dict[str, dict] = {}
        # Single-subswath mode only: pair_key -> (ref_stem, sec_stem), the
        # per-subswath product stems _extract_subswath_stem() derives
        # during staging. Populated by submit()/retry() before any
        # _status_dir()/_build_cmd() call needs it.
        self._stems: dict[str, tuple[str, str]] = {}
        # Single-subswath mode only: pair_key -> GMTSAR's real Julian-date
        # output dirname (e.g. "2019184_2019196"), discovered post-run.
        # See _run_one_pair()'s docstring for why this exists.
        self._real_intf_dirs: dict[str, str] = {}
        self._paths = GMTSARPaths(self.workdir)
        self._lock = threading.Lock()
        self._rediscover_state()

    def _rediscover_state(self) -> None:
        """Populate self.jobs (and self._stems/_real_intf_dirs for
        single-subswath pairs) from real on-disk state, so a freshly
        constructed object -- e.g. the CLI reconstructing GMTSAR_S1 from
        a saved gmtsar_jobs.json in a separate process -- reports real
        status immediately, instead of every pair reading PENDING just
        because this process never ran submit() itself.

        Found via a real CLI `refresh` run: without this, self.jobs
        stayed empty forever (refresh() only updates entries already in
        self.jobs), so a CLI-reloaded processor printed nothing at all.
        """
        if self.config.stack_mode:
            self.jobs = {
                stage: {"stage": stage,
                        "status": _read_status(self._stack_status_dir(stage)),
                        "submitted_at": datetime.now(timezone.utc).isoformat()}
                for stage in self._stack_stages
            }
            return
        for pair in self.pairs:
            key = _pair_key(pair)
            if not self._multiswath:
                ref_safe, _ref_eof, sec_safe, _sec_eof = pair
                ref_stem = self._rediscover_stem(ref_safe)
                sec_stem = self._rediscover_stem(sec_safe)
                if ref_stem and sec_stem:
                    self._stems[key] = (ref_stem, sec_stem)
                    real_dir = self._rediscover_real_intf_dir(ref_stem, sec_stem)
                    if real_dir:
                        self._real_intf_dirs[key] = real_dir
            status = _read_status(self._status_dir(pair))
            self.jobs[key] = self._job_meta(pair, status)

    # ------------------------------------------------------------------ #
    #  Paths                                                              #
    # ------------------------------------------------------------------ #

    @property
    def workdir(self) -> Path:
        return Path(self.config.workdir)

    @property
    def case_dir(self) -> Path:
        """Shared GMTSAR case directory -- used directly for single-
        subswath pairs (p2p_processing is pair-namespaced via
        intf/<julian_date_pair>/, GMTSAR's own naming -- see
        _run_one_pair()). For multi-subswath pairs (_multiswath, i.e.
        subswath names more than one IW) this is only the PARENT of each
        pair's own subdirectory; see pair_case_dir().
        Layout centralized in config/paths.py (GMTSARPaths).
        """
        return GMTSARPaths(self.workdir).case_dir

    def pair_case_dir(self, pair: tuple) -> Path:
        """The directory a given pair's p2p_* invocation actually runs
        from. Shared case_dir for single-subswath pairs; a dedicated
        per-pair subdirectory when _multiswath (p2p_S1_TOPS_Frame's
        F1/F2/F3/merge/ output is not itself pair-namespaced)."""
        if self._multiswath:
            return self.case_dir / _pair_key(pair)
        return self.case_dir

    def _status_dir(self, pair: tuple) -> Path:
        if self._multiswath:
            # p2p_S1_TOPS_Frame's real product lives in merge/; use that
            # directory's existence/markers as the status signal.
            return self.pair_case_dir(pair) / "merge"
        key = _pair_key(pair)
        if key in self._real_intf_dirs:
            # GMTSAR's own real output directory, discovered post-run --
            # see _run_one_pair()'s docstring. This is the directory
            # MintPy's prep_gmtsar.py actually looks at.
            return self._paths.intf_dir / self._real_intf_dirs[key]
        if key not in self._stems:
            # Not staged yet (or staging failed partway through a
            # multi-pair _stage_case() and never reached this pair) --
            # a path that can never exist reads as PENDING via
            # _read_status(), instead of a masking KeyError that hides
            # the real staging failure (found via audit).
            return self._paths.intf_dir / f"_unstaged_{key}"
        # Not yet run (or the real dir wasn't found post-run, e.g. the
        # pair failed before GMTSAR created any output). Sentinel path
        # based on our own stems -- not GMTSAR's real naming, but stable
        # and never collides with a real Julian-date directory, so it's
        # safe as a PENDING/RUNNING placeholder before/without a real run.
        ref_stem, sec_stem = self._stems[key]
        return self._paths.intf_dir / f"_pending_{ref_stem}_{sec_stem}"

    # ------------------------------------------------------------------ #
    #  Case staging (the InSARHub <-> GMTSAR directory-convention bridge) #
    # ------------------------------------------------------------------ #

    def _symlink_dir_contents(self, src: Path, dest_dir: Path) -> None:
        """Symlink every entry in src into dest_dir, by name.

        Found via a real end-to-end test against messy real data: broken
        symlinks (e.g. a stray self-referencing .EOF left over from an
        unrelated process) must be skipped with a warning, not crash the
        whole staging step -- `Path.resolve()` raises RuntimeError on a
        symlink loop, and a single bad entry in a large real data
        directory shouldn't take down staging for every other pair.
        """
        for f in src.glob("*"):
            dest = dest_dir / f.name
            if dest.exists() or dest.is_symlink():
                continue
            try:
                target = f.resolve(strict=True)
            except (RuntimeError, OSError) as exc:
                logger.warning("skipping unresolvable entry %s (%s)", f, exc)
                continue
            dest.symlink_to(target)

    def _rediscover_stem(self, safe_name: str) -> str | None:
        """Read-only counterpart to _extract_subswath_stem(): find a stem
        already extracted into case_dir/raw/ by a PRIOR process (e.g. a
        real submit() run), without needing the original .SAFE source
        available again.

        Needed for CLI reload (refresh/retry/watch/cancel run as a fresh
        process, invoked separately from submit()): self._stems is only
        ever populated in-memory during _stage_case(), so a freshly
        reconstructed GMTSAR_S1 object has no way to find real on-disk
        status without this. real .SAFE names are unique enough (mission +
        timestamp + orbit) that matching a raw/*.tiff symlink's resolved
        target path against safe_name is reliable, not a guess.
        """
        raw_dir = self._paths.raw_dir
        if not raw_dir.is_dir():
            return None
        for tiff in raw_dir.glob("*.tiff"):
            if not tiff.is_symlink():
                continue
            try:
                target = tiff.resolve(strict=True)
            except (RuntimeError, OSError):
                continue
            if safe_name in str(target):
                return tiff.stem
        return None

    def _rediscover_real_intf_dir(self, ref_stem: str, sec_stem: str) -> str | None:
        """Read-only counterpart to the before/after-diff discovery in
        _run_one_pair(): recompute GMTSAR's real Julian-date intf/
        directory name from the two SLC .PRM files' SC_clock_start
        (already-real files sitting in raw/ from a prior run), instead of
        needing to have observed the directory's creation live.
        """
        raw_dir = self._paths.raw_dir
        days = []
        for stem in (ref_stem, sec_stem):
            prms = list(raw_dir.glob(f"S1_*_F{self._subswath_list()[0]}.PRM"))
            match = None
            for prm in prms:
                # stem's date (YYYYMMDD) appears in the PRM's own filename
                # (S1_<YYYYMMDD>_<HHMMSS>_F<N>.PRM) for the scene it was
                # focused from -- extract the date segment from the stem's
                # own naming (s1a-iw2-slc-vv-<start>-...) to match.
                parts = stem.split("-")
                if len(parts) < 5:
                    continue
                date_token = parts[4][:8]  # YYYYMMDD from YYYYMMDDtHHMMSS
                if date_token in prm.name:
                    match = prm
                    break
            if match is None:
                return None
            clock_start = None
            for line in match.read_text().splitlines():
                if line.strip().startswith("SC_clock_start"):
                    clock_start = float(line.split("=")[1].strip())
                    break
            if clock_start is None:
                return None
            days.append(int(clock_start))
        if len(days) != 2:
            return None
        candidate = self._paths.intf_dir / f"{days[0]}_{days[1]}"
        return candidate.name if candidate.is_dir() else None

    def _find_input(self, name: str, cfg_dir) -> Path:
        """Resolve a .SAFE/.EOF name the caller passed in `pairs` against
        config.slc_dir/orbit_dir (or workdir, if that config field is
        left as 'auto')."""
        base = Path(cfg_dir) if cfg_dir and str(cfg_dir) not in ("auto", "") else self.workdir
        path = base / name
        if not path.exists():
            raise FileNotFoundError(f"{name} not found under {base}")
        return path

    def _ensure_safe_extracted(self, safe_name: str) -> Path:
        """Extract safe_name's .zip into a real .SAFE directory under
        config.slc_dir if only the zip exists -- e.g. straight off ASF's
        downloader output, never manually unzipped. GMTSAR reads .SAFE
        directories, not .zip archives, in both single- and multi-subswath
        modes (multi-subswath wholesale-symlinks slc_dir's contents into
        raw/; a .zip sitting there is just a useless symlinked zip
        p2p_S1_TOPS_Frame can't read as a .SAFE tree).

        Idempotent: if a COMPLETE .SAFE directory already exists, returns it
        unchanged without touching the .zip. A partial .SAFE (e.g. an earlier
        extraction killed mid-write -- no measurement/ tiffs) is treated as
        absent and re-extracted from the .zip, rather than trusted and later
        crashing _extract_subswath_stem with "no subswath product found".
        """
        cfg = self.config
        base = self._resolve_dir(cfg.slc_dir, self.workdir)
        safe_path = base / safe_name
        if safe_path.exists() and any((safe_path / "measurement").glob("*.tiff")):
            return safe_path
        if safe_path.exists():
            # partial/corrupt extraction -- clear it before re-extracting
            import shutil
            shutil.rmtree(safe_path, ignore_errors=True)
        zip_path = base / f"{safe_name.removesuffix('.SAFE')}.zip"
        if not zip_path.exists():
            raise FileNotFoundError(
                f"neither {safe_name} nor {zip_path.name} found under {base}"
            )
        import zipfile
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(base)
        if not safe_path.exists():
            raise FileNotFoundError(
                f"extracted {zip_path.name} but expected {safe_name} was "
                f"not produced -- check the zip's internal layout"
            )
        return safe_path

    # csh scripts with NO Python port in gmtsar/python/utils/. snaphu is the
    # dangerous one: bin/snaphu is the SNAPHU *unwrapper binary*, so dropping
    # the .csh suffix would silently invoke raw SNAPHU with GMTSAR wrapper
    # arguments rather than a port. fitoffset is the other (also shipped as a
    # library, gmtsar/python/utils/fitoffset.py, not an executable).
    _NO_PYTHON_PORT = frozenset({"snaphu.csh", "fitoffset.csh"})

    def _gmtsar_script(self, name: str) -> str:
        """Resolve a GMTSAR script name to the implementation to invoke.

        The fork installs both variants side by side in bin/: the unsuffixed
        name is the Python port (gmtsar/python/utils/<name>), <name>.csh the
        classic script. Selecting between them is purely a matter of which
        name we exec -- the arguments and the workflow are identical, so this
        never changes stage structure, only the implementation that runs.

        Falls back to csh for anything in _NO_PYTHON_PORT, and for anything
        whose Python variant isn't actually present in this install, so a
        partial or older GMTSAR checkout degrades gracefully instead of
        failing with "command not found".
        """
        if not name.endswith(".csh"):
            return name
        if not getattr(self.config, "use_python_framework", True):
            return name
        if name in self._NO_PYTHON_PORT:
            return name
        py = name[:-4]
        if shutil.which(py, path=self._subprocess_env().get("PATH")) is None:
            logger.debug("No Python port for %s in this install; using csh.", name)
            return name
        return py

    def _safe_source(self, safe_name: str) -> Path:
        """Where this scene's subswath products should be read from: its
        reframed .SAFE when reframing produced one, else the delivered
        .SAFE. Reframing is best-effort per scene, so this falls back
        per-scene rather than all-or-nothing."""
        reframed = getattr(self, "_reframed_safe", {}).get(safe_name)
        if reframed is not None and Path(reframed).exists():
            return Path(reframed)
        return self._find_input(safe_name, self.config.slc_dir)

    def _extract_subswath_stem(self, ref_safe: str, ref_eof: str, raw_dir: Path,
                               subswath: int | None = None) -> str:
        """Extract one IW subswath's .tiff/.xml from a raw .SAFE dir and
        stage them plus the matching .EOF into raw_dir under GMTSAR's
        required same-stem naming (<stem>.tiff/.xml/.EOF).

        subswath defaults to the first of config.subswath; pass an explicit
        IW number to stage a specific swath (used by the multi-subswath /
        full-frame stack, which stages F1/F2/F3 separately).

        p2p_processing does not read .SAFE directories itself -- see
        module docstring for how this was confirmed (both from
        p2p_processing's own usage string and from GMTSAR's bundled
        H_res/raw/ single-subswath test fixture).
        """
        cfg = self.config
        sw = subswath if subswath is not None else self._subswath_list()[0]
        safe_dir = self._safe_source(ref_safe)
        eof_path = self._find_input(ref_eof, cfg.orbit_dir)
        tiffs = sorted(
            (safe_dir / "measurement").glob(
                f"s1?-iw{sw}-slc-{cfg.polarization}-*.tiff"
            )
        )
        if not tiffs:
            raise FileNotFoundError(
                f"no IW{sw}/{cfg.polarization} subswath product "
                f"found under {safe_dir / 'measurement'} -- check "
                f"config.subswath/polarization against this scene's "
                f"actual coverage"
            )
        tiff = tiffs[0]
        stem = tiff.stem
        xml = safe_dir / "annotation" / f"{stem}.xml"
        if not xml.exists():
            raise FileNotFoundError(f"expected annotation file missing: {xml}")

        for src, ext in ((tiff, ".tiff"), (xml, ".xml"), (eof_path, ".EOF")):
            dest = raw_dir / f"{stem}{ext}"
            if not dest.exists():
                dest.symlink_to(src.resolve())
        return stem

    def _stage_one_case_dir(self, target: Path) -> None:
        """Populate target/{raw,topo}/ and target/config.py, matching what
        GMTSAR's own case.setup / p2p_config would produce for a
        manually-run case.

        Multi-subswath (_multiswath, i.e. subswath names more than one IW):
        symlinks slc_dir/orbit_dir contents wholesale into raw/ --
        p2p_S1_TOPS_Frame reads raw .SAFE dirs + .EOF orbits directly
        (confirmed this matches p2p_processing's own P2P1Preprocess, which
        calls `pre_proc SAT master aligned` internally on raw/ input, so raw
        .SAFE-derived files are the right thing to stage, NOT pre-focused
        SLCs).

        Single-subswath: raw/ is instead populated per-pair by
        _extract_subswath_stem() (called from _stage_case()), since
        p2p_processing needs specific per-subswath files, not the whole
        .SAFE tree.

        The DEM (topo/dem.grd) is either symlinked from config.dem_path or,
        when that is None, auto-downloaded via GMTSAR make_dem from the SLC
        footprint / config.bbox -- see _ensure_dem().
        """
        cfg = self.config
        target.mkdir(parents=True, exist_ok=True)
        raw_dir = target / "raw"
        topo_dir = target / "topo"
        raw_dir.mkdir(exist_ok=True)
        topo_dir.mkdir(exist_ok=True)

        if self._multiswath:
            if cfg.slc_dir and str(cfg.slc_dir) not in ("auto", ""):
                self._symlink_dir_contents(self._resolve_dir(cfg.slc_dir), raw_dir)
            if cfg.orbit_dir and str(cfg.orbit_dir) not in ("auto", ""):
                self._symlink_dir_contents(self._resolve_dir(cfg.orbit_dir), raw_dir)

        self._ensure_dem(topo_dir)

        config_py = target / "config.py"
        if not config_py.exists():
            if cfg.config_template:
                import shutil
                shutil.copy(cfg.config_template, config_py)
            else:
                config_py.write_text(self._render_config_py())

    def _dem_from_dem_stitcher(self, topo_dir: Path, dest: Path,
                               bbox: tuple[float, float, float, float]) -> Path:
        """Copernicus GLO-30 -> GMTSAR dem.grd, via dem_stitcher.

        Same DEM (and the same stitch_dem call) ISCE2_S1._prepare_dem() uses, so
        a GMTSAR-vs-ISCE-vs-HyP3 comparison isn't confounded by DEM source --
        HyP3 also uses GLO-30. dst_ellipsoidal_height=True gives WGS84
        ellipsoidal heights, which is what GMTSAR expects (its own make_dem
        adds the EGM96 geoid back for exactly this reason).

        dem_stitcher returns a rasterio array/profile -> GeoTIFF -> `gmt
        grdconvert ...=nf` for GMTSAR's native netCDF grid.
        """
        import numpy as np
        import rasterio
        from dem_stitcher import stitch_dem

        S, N, W, E = bbox
        logger.info("Auto-downloading GLO-30 DEM (dem_stitcher) bbox S=%s N=%s W=%s E=%s…",
                    S, N, W, E)
        arr, profile = stitch_dem(
            [W, S, E, N],
            dem_name="glo_30",
            dst_ellipsoidal_height=True,
            dst_area_or_point="Point",
        )
        tif = topo_dir / "dem_glo30.tif"
        profile.update(driver="GTiff", dtype="float32", count=1)
        with rasterio.open(tif, "w", **profile) as ds:
            ds.write(np.asarray(arr, dtype="float32"), 1)
            # stitch_dem was asked for node-centred ("Point") values; without
            # this tag GDAL defaults to Area and grdconvert then labels the
            # grid PIXEL-registered, a half-cell (~15 m) shift versus the
            # GRIDLINE registration GMTSAR's own make_dem produces.
            ds.update_tags(AREA_OR_POINT="Point")

        env = self._subprocess_env()
        proc = subprocess.run(
            ["gmt", "grdconvert", str(tif), f"-G{dest}=nf"],
            cwd=str(topo_dir), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        if proc.returncode != 0 or not dest.exists():
            raise RuntimeError(
                f"grdconvert failed to turn {tif.name} into {dest} "
                f"(rc={proc.returncode}):\n{proc.stdout}")

        # belt-and-braces: if it still came out pixel-registered, relabel it
        # (grdedit -T only rewrites the header -- the samples really are
        # node-centred, so this restores their true meaning, no resampling).
        info = subprocess.run(["gmt", "grdinfo", str(dest)], cwd=str(topo_dir),
                              env=env, capture_output=True, text=True).stdout
        if "Pixel node registration" in info:
            subprocess.run(["gmt", "grdedit", str(dest), "-T"], cwd=str(topo_dir),
                           env=env, check=True, capture_output=True)
            logger.info("relabelled DEM to gridline registration (matches make_dem)")
        logger.info("GLO-30 DEM ready: %s", dest)
        return dest

    def _ensure_dem(self, topo_dir: Path) -> Path:
        """Guarantee topo_dir/dem.grd exists.

        - config.dem_path set: symlink that GMTSAR-format DEM in.
        - config.dem_path None: auto-download via GMTSAR's `make_dem W E S N
          [mode]` (SRTM off the GMT server, EGM96 geoid removed) using the
          joint footprint of the SLCs on disk (or config.bbox as a fallback).
          Mirrors ISCE2_S1's bbox-driven DEM auto-fetch, but produces GMTSAR's
          native dem.grd directly rather than an ISCE2 binary DEM.
        """
        dest = topo_dir / "dem.grd"
        cfg = self.config
        box = self._aoi_bbox()

        # An AOI narrows the box below the frame, so a DEM that already exists
        # (from an earlier full-frame run, or supplied via dem_path) is bigger
        # than we need and costs dem2topo_ra time and memory in proportion to
        # its area. Crop it. Without an AOI the box is footprint+buffer, i.e.
        # larger than the data, and _grdcut_dem declines the no-op.
        if dest.exists():
            if box and box[4] and not dest.is_symlink():
                tmp = dest.with_suffix(".grd.aoi")
                if self._grdcut_dem(dest, tmp, box[:4]):
                    tmp.replace(dest)
            return dest
        if cfg.dem_path:
            src = self._resolve_dir(cfg.dem_path).resolve()
            if box and box[4]:
                # Crop ONCE into a shared file beside the source, then symlink
                # every case at it. p2p gives each pair its own case dir, so
                # cropping per case would leave one full DEM copy per pair
                # (27 x ~120 MB on this stack) instead of one. The box is in
                # the name so a changed AOI produces a different file rather
                # than silently reusing the previous crop.
                W, E, S, N = box[2], box[3], box[0], box[1]
                shared = src.parent / f"dem_aoi_{W:.2f}_{E:.2f}_{S:.2f}_{N:.2f}.grd"
                if shared.exists() or self._grdcut_dem(src, shared, box[:4]):
                    src = shared
            dest.symlink_to(src)
            return dest

        if box is None:
            raise ValueError(
                "dem_path is unset and no DEM could be auto-downloaded: no SLC "
                "footprints found in slc_dir/workdir and no AOI is available. "
                "Provide dem_path, an aoi (WKT), or ensure SLCs are on disk."
            )
        S, N, W, E = box[:4]
        logger.info("DEM extent from %s + %.2f deg: W=%.4f E=%.4f S=%.4f N=%.4f",
                    "AOI" if box[4] else "scene footprint", self.AOI_BUFFER_DEG, W, E, S, N)

        if str(cfg.dem_source).lower() == "glo30":
            return self._dem_from_dem_stitcher(topo_dir, dest, (S, N, W, E))

        logger.info("Auto-downloading GMTSAR DEM via make_dem W=%s E=%s S=%s N=%s (mode %s)…",
                    W, E, S, N, cfg.dem_mode)
        env = self._subprocess_env()
        # Force make_dem's legacy `gmt grdsample` geoid-resample path: the
        # dev fork's in-process grdsample port (GMTSAR_GRDSAMPLE_PY=1, its
        # default) rejects real SRTM tiles with "x is not uniformly spaced"
        # -- floating-point non-uniformity in the 1-arcsec grid that the
        # legacy gmt binary tolerates fine (the port's own error message
        # tells you to set this). Only scoped to make_dem; other GMTSAR
        # python kernels keep their default fast path.
        env["GMTSAR_GRDSAMPLE_PY"] = "0"
        proc = subprocess.run(
            ["make_dem", str(W), str(E), str(S), str(N), str(cfg.dem_mode)],
            cwd=str(topo_dir), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        if proc.returncode != 0 or not dest.exists():
            raise RuntimeError(
                f"make_dem failed to produce {dest} (rc={proc.returncode}):\n{proc.stdout}"
            )
        return dest

    def _resolve_aoi(self) -> str | None:
        """The geographic AOI (WKT POLYGON): config.AOI if set, else the
        downloader's intersectsWith from the workdir's insarhub_config.json
        (what --select-pairs searched with). None if neither is available."""
        if self.config.AOI:
            return self.config.AOI
        try:
            from insarhub.utils.config_io import read_insarhub_config
            data = read_insarhub_config(self.workdir)
            aoi = data.get("downloader", {}).get("config", {}).get("intersectsWith")
            return aoi or None
        except Exception:
            return None

    #: Geographic buffer (degrees) added to the AOI -- or, when there is no
    #: AOI, to the scene footprint -- before it sets the DEM extent. Generous
    #: on purpose: the DEM must overhang the SLC everywhere the radar geometry
    #: reaches, or dem2topo_ra leaves NaN gaps at the edges of topo_ra.grd
    #: that propagate into every interferogram.
    AOI_BUFFER_DEG = 0.5

    def _aoi_bbox(self, buffer_deg: float | None = None):
        """(S, N, W, E, narrowed) -- the buffered geographic box the DEM is
        built to.

        AOI first, scene footprint second. That order is the point: before
        this, the footprint always won and the AOI was consulted only if no
        SLCs were on disk, so the DEM was unconditionally full-frame and
        dem2topo_ra paid for the whole frame no matter how small the area of
        interest was (a real p2p run: 12352x7996 = 99M DEM points, ~70 GB
        resident per subswath in SAT_llt2rat_py, three of them at once).

        `narrowed` is True only when a real AOI produced the box. Falling back
        to footprint+buffer yields a box strictly LARGER than the data, so
        callers can skip cropping that would be a no-op (or worse -- a crop
        that pads with NaN).
        """
        buf = self.AOI_BUFFER_DEG if buffer_deg is None else buffer_deg
        aoi = self._resolve_aoi()
        if aoi:
            coords = self._aoi_coords(aoi)
            if len(coords) >= 3:
                lons = [c[0] for c in coords]
                lats = [c[1] for c in coords]
                return (min(lats) - buf, max(lats) + buf,
                        min(lons) - buf, max(lons) + buf, True)
            logger.warning("AOI %r had <3 vertices; falling back to the scene "
                           "footprint for the DEM extent.", aoi)

        from insarhub.processor.isce2_s1 import _bbox_from_slc_dir
        cfg = self.config
        # slc_dir is conventionally stored relative ("./slc"); resolve it
        # against the workdir, not the CWD -- under SLURM the CWD is wherever
        # sbatch launched, so the bare relative path silently found nothing
        # and the footprint fallback never fired.
        slc_dir = self._resolve_dir(cfg.slc_dir)
        # workdir/slc is where the downloader puts scenes, and is what
        # slc_dir="auto" (the default) means.
        for scan in dict.fromkeys([slc_dir, self.workdir / "slc", self.workdir]):
            if scan is None:
                continue
            bbox = _bbox_from_slc_dir(scan)
            if bbox:
                S, N, W, E = bbox
                return (S - buf, N + buf, W - buf, E + buf, False)
        return None

    def _grdcut_dem(self, src: Path, dest: Path, box) -> bool:
        """Crop `src` to `box` (S, N, W, E) as `dest`. Returns False -- leaving
        dest untouched -- when the crop would be a no-op or would fail.

        The requested box is intersected with the source's own range first:
        `gmt grdcut` errors on a region larger than the grid unless -N is
        given, and -N would pad the DEM with NaN, which is exactly the edge
        gap the buffer exists to prevent.
        """
        S, N, W, E = box
        info = subprocess.run(["gmt", "grdinfo", "-C", str(src)],
                              capture_output=True, text=True, env=self._subprocess_env())
        if info.returncode != 0:
            logger.warning("grdinfo failed on %s; leaving the DEM uncropped.", src)
            return False
        f = info.stdout.split()
        try:
            sW, sE, sS, sN = (float(f[1]), float(f[2]), float(f[3]), float(f[4]))
        except (IndexError, ValueError):
            logger.warning("could not parse grdinfo -C for %s; DEM uncropped.", src)
            return False
        W, E = max(W, sW), min(E, sE)
        S, N = max(S, sS), min(N, sN)
        if E <= W or N <= S:
            logger.warning("AOI+%.2f deg does not overlap the DEM (%s); DEM uncropped.",
                           self.AOI_BUFFER_DEG, src)
            return False
        # Nothing to gain if we would keep essentially the whole grid.
        if (E - W) * (N - S) > 0.98 * (sE - sW) * (sN - sS):
            return False
        proc = subprocess.run(
            ["gmt", "grdcut", str(src), f"-G{dest}", f"-R{W}/{E}/{S}/{N}"],
            capture_output=True, text=True, env=self._subprocess_env(),
        )
        if proc.returncode != 0:
            logger.warning("grdcut failed (rc=%s); DEM uncropped:\n%s",
                           proc.returncode, proc.stderr)
            return False
        before = (sE - sW) * (sN - sS)
        logger.info("DEM cropped to AOI+%.2f deg: %.2f/%.2f/%.2f/%.2f "
                    "(%.0f%% of the frame's DEM area)",
                    self.AOI_BUFFER_DEG, W, E, S, N, 100.0 * (E - W) * (N - S) / before)
        return True

    @staticmethod
    def _aoi_coords(aoi) -> list[tuple[float, float]]:
        """Extract (lon, lat) vertices from an AOI in any form InSARHub's own
        downloader accepts -- delegates to utils.tool._to_wkt (the same
        parser the S1_SLC downloader uses for intersectsWith), so the AOI
        field takes exactly what --aoi does: a bbox
        [min_lon, min_lat, max_lon, max_lat], a spatial file path
        (GeoJSON/SHP/...), or a WKT string. Returns the normalized polygon's
        exterior vertices."""
        from insarhub.utils.tool import _to_wkt
        from shapely import wkt as _wkt
        wkt_str = _to_wkt(aoi)
        if not wkt_str:
            return []
        try:
            return list(_wkt.loads(wkt_str).exterior.coords)
        except Exception:
            nums = re.findall(r"(-?\d+\.?\d*)\s+(-?\d+\.?\d*)", wkt_str)
            return [(float(a), float(b)) for a, b in nums]

    def _aoi_to_region_cut(self, master_prm: Path, aoi_wkt: str) -> str | None:
        """Convert a geographic AOI (WKT) to a GMTSAR region_cut
        (x0/xN/y0/yN in radar range/azimuth pixels) via SAT_llt2rat -- the
        same geo->radar tool GMTSAR uses internally (align_batch.csh:101).

        Feeds each AOI vertex as `lon lat 0` to `SAT_llt2rat master.PRM 0`,
        reads back range (col 1) + azimuth (col 2), and takes the bounding
        box (with a small margin, clamped to the frame's num_rng_bins /
        num_lines). Elevation 0 is a coarse approximation -- region_cut is a
        generous crop box, and the margin absorbs elevation-induced range
        shift."""
        coords = self._aoi_coords(aoi_wkt)
        if len(coords) < 3:
            logger.warning("AOI WKT had <3 vertices (%r); skipping region_cut.", aoi_wkt)
            return None
        llt = "\n".join(f"{lon} {lat} 0" for lon, lat in coords) + "\n"
        proc = subprocess.run(
            ["SAT_llt2rat", master_prm.name, "0"],
            cwd=str(master_prm.parent), input=llt, text=True,
            capture_output=True, env=self._subprocess_env(),
        )
        if proc.returncode != 0:
            logger.warning("SAT_llt2rat failed (rc=%s), skipping region_cut:\n%s",
                           proc.returncode, proc.stderr)
            return None
        ranges, azis = [], []
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                try:
                    ranges.append(float(parts[0]))
                    azis.append(float(parts[1]))
                except ValueError:
                    continue
        if not ranges:
            return None

        def _prm(key: str) -> int | None:
            for l in master_prm.read_text().splitlines():
                if l.strip().startswith(key):
                    try:
                        return int(float(l.split("=")[1]))
                    except (ValueError, IndexError):
                        return None
            return None
        rmax = _prm("num_rng_bins") or 10**9
        amax = _prm("num_lines") or 10**9

        # 5% margin, clamp to [0, frame extent]
        r0, r1 = min(ranges), max(ranges)
        a0, a1 = min(azis), max(azis)
        rm = (r1 - r0) * 0.05 or 100
        am = (a1 - a0) * 0.05 or 100
        x0 = max(0, int(r0 - rm)); xN = min(rmax, int(r1 + rm))
        y0 = max(0, int(a0 - am)); yN = min(amax, int(a1 + am))
        if xN <= x0 or yN <= y0:
            logger.warning("AOI mapped outside the frame (r=%s..%s a=%s..%s); "
                           "skipping region_cut.", r0, r1, a0, a1)
            return None
        return f"{x0}/{xN}/{y0}/{yN}"

    #: Sentinel-1 IW nominal SLC sampling, used when no PRM exists yet (the
    #: check runs at config-render time, before preprocessing). Slant-range
    #: spacing c/(2*fs) with fs = 64.35 MHz, and the azimuth line spacing.
    _S1_SLANT_RNG_M = 2.33
    _S1_AZIMUTH_M   = 13.9
    #: IW incidence spans ~30 deg (IW1 near) to ~46 deg (IW3 far); ground
    #: range is slant/sin(theta), so it varies across the merged frame. Use
    #: the mid-swath value, not the near-range extreme: the check is about
    #: whether the BULK of the frame fills, and 30 deg rejects combinations
    #: that demonstrably work -- HyP3 runs 20x4 at an 80 m posting, which the
    #: near-range figure would have blocked. Calibrated against three known
    #: points on p100_f466 (20x4+200 m holes at 57% fill, 8x2+200 m fills,
    #: HyP3 20x4+80 m fills); mid-swath reproduces all three.
    _S1_INCIDENCE_DEG = 39.0

    def _resolve_dir(self, value, default: Path | None = None) -> Path | None:
        """Resolve a config directory against the WORKDIR, not the CWD.

        slc_dir/orbit_dir/dem_path are conventionally stored relative
        ("./slc"), and bare `Path("./slc")` resolves against the process's
        current directory. That silently made `-w <dir>` work only when you
        already stood in <dir>: running the same command from anywhere else
        failed with "neither ...SAFE nor ...zip found under slc", and every
        SLURM child job runs from wherever sbatch was invoked.
        """
        if value is None or str(value) in ("auto", ""):
            return default
        p = Path(str(value))
        return p if p.is_absolute() else self.workdir / p

    def _ground_posting(self) -> tuple[float, float]:
        """(ground-range, azimuth) metres per multilooked pixel.

        Reads rng_samp_rate / PRF from a PRM when one exists, so the numbers
        are the real ones for this frame; falls back to S1 IW nominals at
        config-render time, when no PRM has been produced yet.
        """
        import math
        cfg = self.config
        slant, azim = self._S1_SLANT_RNG_M, self._S1_AZIMUTH_M
        prm = next(iter(sorted(self.workdir.rglob("S1_*_F?.PRM"))), None)
        if prm is not None:
            vals = {}
            for line in prm.read_text().splitlines():
                k, _, v = line.partition("=")
                k = k.strip()
                if k in ("rng_samp_rate", "PRF", "vel"):
                    try:
                        vals[k] = float(v.split()[0])
                    except (ValueError, IndexError):
                        pass
            if vals.get("rng_samp_rate"):
                slant = 299792458.0 / (2.0 * vals["rng_samp_rate"])
            if vals.get("PRF") and vals.get("vel"):
                azim = vals["vel"] / vals["PRF"]
        gr = slant / math.sin(math.radians(self._S1_INCIDENCE_DEG))
        return gr * int(cfg.range_dec), azim * int(cfg.azimuth_dec)

    def _check_geocode_posting(self) -> None:
        """Fail fast when filter_wavelength and the looks disagree.

        proj_ra2ll sets the geocoded grid to filter_wavelength/4 metres::

            filt  = glob.glob("gauss_*")        # gauss_200
            pix_m = float(filt[0][6:]) / 4      # 50 m

        That posting is independent of range_dec/azimuth_dec, so coarsening
        the looks without coarsening the filter geocodes a sparse grid: the
        projection is a scatter (gmt blockmedian + xyz2grd), nothing
        interpolates, and unhit cells stay NaN. Measured on p100_f466 with
        20x4 and the stock 200 m filter: 74 m data onto a 50 m grid, 38.7%
        of every *_ll.grd populated, in a diagonal moire. Each pair still
        "succeeds" -- the loss only shows up when MintPy tries to load it.

        For reference, HyP3 runs the same 20x4 looks and geocodes at 80 m
        (its INT80 products), i.e. filter_wavelength 320.
        """
        cfg = self.config
        if getattr(cfg, "allow_sparse_geocode", False):
            return
        try:
            fw = float(cfg.filter_wavelength)
        except (TypeError, ValueError):
            return
        if fw <= 0:
            return
        gr, az = self._ground_posting()
        need = max(gr, az)
        pix = fw / 4.0
        if pix >= need:
            return
        suggest = int(math.ceil(need * 4 / 10.0) * 10)
        raise ValueError(
            f"filter_wavelength={fw:g} m geocodes to {pix:.0f} m "
            f"(proj_ra2ll uses filter_wavelength/4), but range_dec="
            f"{cfg.range_dec} / azimuth_dec={cfg.azimuth_dec} multilook to "
            f"{gr:.0f} m ground range x {az:.0f} m azimuth. Projecting "
            f"{need:.0f} m data onto a {pix:.0f} m grid leaves roughly "
            f"{100 * (1 - min(1.0, pix / gr) * min(1.0, pix / az)):.0f}% "
            f"of every *_ll.grd empty, "
            f"and nothing in GMTSAR fills it (near_interp only gap-fills "
            f"snaphu's input, in radar coordinates).\n"
            f"  NB filter_wavelength does two jobs in GMTSAR -- the Gaussian "
            f"pre-filter kernel AND the geocoded posting -- so raising it to "
            f"fix the grid also widens the kernel (below ~40 m the kernel is "
            f"1x1, i.e. Goldstein only, like ISCE/HyP3).\n"
            f"  Fix either side:\n"
            f"    filter_wavelength = {suggest}      keeps {cfg.range_dec}x"
            f"{cfg.azimuth_dec} looks; 320 gives HyP3's 80 m posting\n"
            f"    range_dec = 8, azimuth_dec = 2     GMTSAR's stock S1_TOPS "
            f"pairing (needs filter_wavelength >= 120)\n"
            f"  Or set allow_sparse_geocode=True to geocode anyway."
        )

    def _render_config_py(self) -> str:
        """Render config.py from the config's GMTSAR processing params
        (GMTSAR_CONFIG_PARAMS on GMTSAR_Base_Config/GMTSAR_S1_Config).

        Replaces the old `pop_config <SAT>` shell-out, so any overridden field
        now actually takes effect -- previously these params were inert
        (buried in the auto-generated file with no way to set them).

        This used to claim the defaults "match pop_config's own defaults
        exactly (verified by diffing its output)". They do not, and have not
        for some time. Diffed against `pop_config S1_TOPS` (2026-08-11), six
        fields differ, every one of them on purpose::

            threshold_snaphu    0    -> 0.1   0 SKIPS unwrapping entirely
            near_interp         0    -> 1     gap-fill snaphu's input
            mask_water          1    -> 0     no water mask by default
            range_dec           8    -> 20  } parity with Hyp3_S1_Config.looks
            azimuth_dec         2    -> 4   } and ISCE2_S1_Config looks_*
            filter_wavelength   200  -> 320   80 m geocode posting for 20x4

        The parity claim was not harmless: it discouraged checking, and the
        two decimation fields silently broke geocoding for every S1 run --
        see _check_geocode_posting(), which now enforces the one relation
        between these fields that GMTSAR itself never validates."""
        cfg = self.config
        # Before anything is written: a filter/looks mismatch costs a full
        # 2-hour pair per pair and only surfaces at MintPy load time.
        self._check_geocode_posting()
        lines = [f"# Generated by GMTSAR_S1 for SAT={cfg.sat} "
                 f"(from {type(cfg).__name__} fields)."]
        # config.py is IMPORTED as a python module by p2p_processing
        #     from config import proc_stage, ..., num_patches, ..., region_cut, ...
        # so every one of those names must exist. Omitting a key, or leaving it
        # blank after the `=`, breaks the import outright -- both were tried:
        #   (absent)         ImportError: cannot import name 'num_patches'
        #   region_cut =     SyntaxError / grep_value IndexError
        #
        # An unset region_cut must therefore be the empty STRING, not -999.
        # merge_unwrap_geocode_tops does:
        #     region_cut = _get_config(config, "region_cut", "")
        #     if not region_cut:            # falsy -> derive from the data
        #         region_cut = grdinfo(phasefilt.grd)
        # "-999" is a non-empty string, so it is truthy, reaches grdcut, and
        # raises "region string must be 'w/e/s/n'" -- after ~21 minutes of
        # otherwise successful merging.
        for name in cfg.GMTSAR_CONFIG_PARAMS:
            val = getattr(cfg, name)
            if name == "region_cut" and str(val) in ("-999", "", "None"):
                # Deliberately NO spaces around the '='. config.py is read two
                # incompatible ways and this is the only form both accept:
                #
                #   p2p_processing IMPORTS it  -> needs valid Python, and every
                #       name in its import list must exist. Omitting the key is
                #       an ImportError.
                #   merge_unwrap_geocode_tops GREPS it -- _grep_field takes
                #       FIELD 3 of the whitespace-split line, and treats a
                #       falsy value as "derive the region from phasefilt.grd".
                #
                # So:  region_cut = -999  -> field 3 is "-999", truthy, reaches
                #                            grdcut -> "must be 'w/e/s/n'"
                #      region_cut = ""    -> field 3 is the literal '""',
                #                            still truthy -> same crash
                #      (omitted)          -> ImportError
                #      region_cut=""      -> ONE token, so `len(parts) >= 3`
                #                            fails and _grep_field returns ""
                #                            -> falsy -> region derived. And it
                #                            is still valid Python.
                lines.append('region_cut=""')
                continue
            if name == "region_cut":
                # A real region_cut has to be QUOTED. config.py is imported as
                # Python, so the bare form is an arithmetic expression:
                #     region_cut = 300/5900/0/25000   -> ZeroDivisionError
                # and with a non-zero third field it silently imports as a
                # float, which cut_slc then rejects. Restricting the p2p path
                # to AOI-driven DEM cropping (see _aoi_bbox) means nothing sets
                # this automatically, but a hand-set value must still work.
                #
                # NOT usable on a multi-subswath frame: p2p_S1_TOPS_Frame
                # copies ONE config.py into F1/F2/F3, while cut_slc.c does
                #     if (... || xh > nr || ... ) die("wrong range ", "");
                # against each subswath's own num_rng_bins (e.g. 21952/25824/
                # 24912) -- no clamp, no "full range" wildcard. A shared box
                # either kills the narrow subswath or clips the wide ones.
                if self._multiswath:
                    raise ValueError(
                        f"region_cut={val!r} cannot be used with subswath="
                        f"{self.config.subswath!r}: p2p_S1_TOPS_Frame shares one "
                        "config.py across subswaths that have different range "
                        "widths, and cut_slc aborts on a mismatch. Set an AOI "
                        "instead (the DEM is cropped to AOI + "
                        f"{self.AOI_BUFFER_DEG} deg), or process a single subswath."
                    )
                lines.append(f'{name} = "{val}"')
                continue
            lines.append(f"{name} = {val}")
        return "\n".join(lines) + "\n"

    def _stage_case(self) -> None:
        # Extract any scene still sitting as a raw ASF .zip (never manually
        # unzipped) into a real .SAFE directory before either branch below
        # touches slc_dir -- see _ensure_safe_extracted()'s docstring.
        unique_safes = {p[0] for p in self.pairs} | {p[2] for p in self.pairs}
        for safe_name in unique_safes:
            self._ensure_safe_extracted(safe_name)

        if not self._multiswath:
            self._stage_one_case_dir(self.case_dir)
            raw_dir = self._paths.raw_dir
            for pair in self.pairs:
                ref_safe, ref_eof, sec_safe, sec_eof = pair
                ref_stem = self._extract_subswath_stem(ref_safe, ref_eof, raw_dir)
                sec_stem = self._extract_subswath_stem(sec_safe, sec_eof, raw_dir)
                self._stems[_pair_key(pair)] = (ref_stem, sec_stem)
            return
        # Frame mode: one case dir PER PAIR, each independently staged
        # (raw/topo/config.py symlinked/copied per pair). Slightly more
        # I/O than sharing one case_dir, but required for correctness --
        # see pair_case_dir()'s docstring.
        for pair in self.pairs:
            self._stage_one_case_dir(self.pair_case_dir(pair))

    # ------------------------------------------------------------------ #
    #  Container re-invocation                                           #
    # ------------------------------------------------------------------ #

    def _reinvoke_via_container(self, action: str) -> None:
        """Re-run this same `insarhub processor ... {action}` CLI call
        inside self.config.container instead of on the host.

        Mirrors ISCE2_Base._reinvoke_via_container (see its docstring for
        the full container-mechanics writeup: bind-mounting the workdir,
        why gmtsar_root/gmtsar_env_bin auto-detection is skipped when
        container is set -- see __init__ -- and why `container` itself is
        excluded from cfg_dict/not persisted, to avoid infinite recursion
        once actually running inside the container). Simpler than ISCE's
        version: GMTSAR's local mode backgrounds via a daemon thread inside
        whichever process calls submit() (see _run_local_or_sync()), not a
        forked detached host process, so there's no INSARHUB_HOST_PID-style
        liveness marker to thread through.
        """
        import dataclasses

        from insarhub.utils.config_io import write_insarhub_config
        from insarhub.utils.container import wrap_container_cmd

        cfg_dict = {
            f.name: getattr(self.config, f.name)
            for f in dataclasses.fields(self.config)
            if f.name not in ("container", "workdir", "saved_job_path")
        }
        write_insarhub_config(self.workdir, {
            "processor": {"type": type(self).name, "config": cfg_dict}
        })

        cli_cmd = (f"INSARHUB_CONTAINER_CHILD=1 insarhub processor "
                   f"-N {type(self).name} -w {self.workdir} {action}")

        self.case_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.case_dir / "executor.log"
        pid_file = self.case_dir / "executor.pid"
        if os.name == "posix":
            pid = os.fork()
            if pid == 0:  # child -- detach and run
                try:
                    os.setsid()
                    wrapped = wrap_container_cmd(self.config.container, cli_cmd, self.workdir)
                    with open(log_file, "w") as _lf:
                        os.dup2(_lf.fileno(), sys.stdout.fileno())
                        os.dup2(_lf.fileno(), sys.stderr.fileno())
                    subprocess.run(wrapped, shell=True)
                finally:
                    os._exit(0)
            # parent
            pid_file.write_text(str(pid))
            print(f"Container executor running in background (PID {pid}).")
            print(f"  log : {log_file}")
            print(f"  Use 'refresh' to check status, 'cancel' to stop.")
        else:
            # Windows: no fork -- run blocking
            wrapped = wrap_container_cmd(self.config.container, cli_cmd, self.workdir)
            subprocess.run(wrapped, shell=True)

    def _run_local_or_sync(self, target, args: tuple = ()) -> None:
        """Start target as a daemon thread (returns immediately), unless
        INSARHUB_CONTAINER_CHILD is set -- then run it synchronously
        instead, so this (already backgrounded, by _reinvoke_via_container's
        fork) container-side foreground process doesn't exit -- and get
        torn down by `docker run --rm` -- before the work finishes. Mirrors
        ISCE2_Base._start_local_background's INSARHUB_CONTAINER_CHILD check,
        adapted to GMTSAR's daemon-thread (not forked-process) local mode.
        """
        if os.environ.get("INSARHUB_CONTAINER_CHILD"):
            target(*args)
            return
        thread = threading.Thread(target=target, args=args, daemon=True)
        thread.start()
        self._thread = thread

    # ------------------------------------------------------------------ #
    #  LocalProcessor interface                                          #
    # ------------------------------------------------------------------ #

    def submit(self) -> dict:
        # HPC mode is deliberately excluded here: the outer submit() call
        # only builds sbatch scripts + submits the first one -- pure Python/
        # bash bookkeeping that never touches GMTSAR/gmt itself, so it stays
        # on the host regardless of container. The container instead wraps
        # each HPC child job's own command (see _stage_commands()), since
        # that's the thing that actually needs GMTSAR. Re-invoking the whole
        # process here would also be a no-op anyway: config.container isn't
        # persisted to insarhub_config.json (see _reinvoke_via_container),
        # so _stage_commands()'s wrap check would never see it on the
        # container-side re-invocation.
        if (self.config.container and not os.environ.get("INSARHUB_CONTAINER_CHILD")
                and not (self.config.stack_mode and self.config.hpc_mode)):
            self._reinvoke_via_container("submit")
            return self.jobs
        if self.config.stack_mode:
            return self._submit_stack()

        self._stage_case()

        if self.config.hpc_mode:
            return self._submit_p2p_hpc()

        pending = []
        for pair in self.pairs:
            key = _pair_key(pair)
            status = _read_status(self._status_dir(pair))
            if status == _SUCCEEDED and self.config.skip_existing:
                logger.info("%s already succeeded, skipping.", key)
                self.jobs[key] = self._job_meta(pair, _SUCCEEDED)
                continue
            _write_status(self._status_dir(pair), _PENDING)
            self.jobs[key] = self._job_meta(pair, _PENDING)
            pending.append(pair)

        if self.config.dry_run:
            logger.info("dry_run: would submit %d pair(s): %s", len(pending), pending)
            return self.jobs

        self._run_local_or_sync(self._run_pairs, (pending,))
        self.save()
        return self.jobs

    def _submit_p2p_hpc(self) -> dict:
        """p2p on SLURM: ONE child job per pair, one sliding-window manager.

        p2p is the natural fit for this -- far more so than stack_mode, whose
        stages must run in order. Every pair is genuinely independent: in
        multi-subswath mode each gets its own case dir, and in single-subswath
        mode p2p_processing namespaces its output as intf/<julian_pair>/. So
        there is nothing to chain and nothing to serialise -- a single manager
        fans every pair out at once, ``max_concurrent_hpc`` live at a time.

        Before this, p2p ran only in-process (``_run_local_or_sync``), so a
        27-pair full-frame stack meant one machine, ``max_workers`` at a time,
        for days -- and if that process died the whole run went with it.
        """
        import dataclasses
        from colorama import Fore, Style
        from insarhub.utils.slurm_manager import build_sliding_window_manager
        from insarhub.processor.isce2_base import _manager_partition, _manager_time

        pending = [p for p in self.pairs
                   if not (self.config.skip_existing
                           and _read_status(self._status_dir(p)) == _SUCCEEDED)]
        for p in self.pairs:
            key = _pair_key(p)
            if p not in pending:
                self.jobs[key] = self._job_meta(p, _SUCCEEDED)
                print(f"  {Fore.GREEN}  ✓ {key}  (already succeeded){Style.RESET_ALL}")
        if not pending:
            print(f"{Fore.GREEN}Every pair already succeeded.{Style.RESET_ALL}")
            self.save()
            return self.jobs

        exe = f"{sys.executable} -m insarhub.cli.main"
        # Index into self.pairs, not into `pending` -- run_stage_unit resolves
        # --index against the full pairs list, so a resumed run that skips
        # finished pairs still points every child at the right one.
        cmds = [f'{exe} processor -N {type(self).name} -w "{self.workdir}" '
                f'run-stage-unit --stage pair --index {self.pairs.index(p)}'
                for p in pending]

        hpc_dir = self.workdir / "hpc" / "p2p"
        hpc_dir.mkdir(parents=True, exist_ok=True)
        # Markers are keyed by POSITION (cmd_0007.done) but `cmds` is re-derived
        # every submit with finished pairs filtered out, so index i routinely
        # means a different pair than it did last time. The manager skips any
        # index whose .done exists, so a stale marker silently drops real work.
        #
        # Measured here: a 1-pair test left cmd_0000.done behind; the following
        # 27-pair submit logged "cmd_0000 SKIPPED (already done)" and produced
        # 25 of 26 interferograms while reporting success. Same failure ISCE3
        # hit on cslc -- see ISCE3_Base._invalidate_stale_markers, which this
        # mirrors. Cleared unconditionally: reaching here means these pairs are
        # going to run (already-succeeded ones were filtered into `pending`).
        stale = sorted(hpc_dir.glob("cmd_*.done")) + sorted(hpc_dir.glob("cmd_*.fail"))
        for m in stale:
            m.unlink()
        if stale:
            logger.info("cleared %d stale cmd_*.done/.fail marker(s) in %s",
                        len(stale), hpc_dir)
        status_dir = self.workdir / ".p2p_status"
        write_status_fn = (
            f'STATUS_DIR="{status_dir}"\n'
            'write_status() {\n'
            '    mkdir -p "$STATUS_DIR"\n'
            '    rm -f "$STATUS_DIR/.succeeded" "$STATUS_DIR/.failed"\n'
            '    case "$1" in\n'
            '        SUCCEEDED) touch "$STATUS_DIR/.succeeded" ;;\n'
            '        FAILED*)   touch "$STATUS_DIR/.failed" ;;\n'
            '    esac\n'
            '}'
        )
        step_cfg = self._sbatch_opts_for_stage("p2p")
        _slurm_fields = {f.name for f in dataclasses.fields(Slurmjob_Config)}
        manager = build_sliding_window_manager(
            job_name_base="g_p2p", commands=cmds,
            log_dir=hpc_dir, sbatch_dir=hpc_dir,
            max_concurrent=self.config.max_concurrent_hpc,
            slurm_kwargs={k: v for k, v in step_cfg.items() if k in _slurm_fields},
            env_lines=self._hpc_env_lines(),
            write_status_fn=write_status_fn,
            manager_partition=_manager_partition(self.config.sbatch_options_per_step or {}),
            manager_time=_manager_time(self.config.sbatch_options_per_step or {}),
            file_prefix="p2p", label="p2p",
        )

        print(f"\n[{type(self).name}] HPC plan "
              f"({self.config.max_concurrent_hpc} concurrent job(s) max):")
        print(f"    {'p2p':<24} {len(cmds):>4} job(s)   {manager.name}")
        if self.config.dry_run:
            print("[GMTSAR_S1] dry run -- nothing submitted.")
            return self.jobs

        out = subprocess.run(["sbatch", str(manager)], capture_output=True, text=True)
        if out.returncode != 0:
            print(f"  {Fore.RED}sbatch failed: {out.stderr.strip()}{Style.RESET_ALL}")
            return self.jobs
        jid = "".join(c for c in out.stdout if c.isdigit())
        print(f"[GMTSAR_S1] submitted {manager.name} -> manager job {jid}")
        for p in pending:
            # Write the status FILE, not just the in-memory job dict. refresh()
            # reads the file, so without this a resubmitted pair kept whatever
            # its last run left behind -- a pair cancelled and then resubmitted
            # showed FAILED while its job was actually RUNNING.
            _write_status(self._status_dir(p), _PENDING)
            self.jobs[_pair_key(p)] = self._job_meta(p, _PENDING)
        self.save()
        return self.jobs

    @staticmethod
    def _live_job_names() -> dict[str, str]:
        """``{job_id: job_name}`` for this user's queued/running jobs.

        Deliberately NOT slurm_manager.slurm_active_jobs(), which maps job id
        -> STATE. Both are dict[str, str], so confusing them fails silently: a
        name check against "PENDING"/"RUNNING" simply never matches and every
        job looks absent from the queue.
        """
        try:
            p = subprocess.run(["squeue", "-h", "-u", os.environ.get("USER", ""),
                                "-o", "%i %j"], text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               timeout=20)
            if p.returncode != 0:
                return {}
        except Exception:                                        # noqa: BLE001
            return {}
        out: dict[str, str] = {}
        for line in p.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                out[parts[0].strip()] = parts[1].strip()
        return out

    def _hpc_env_lines(self) -> list[str]:
        """PATH lines a child job needs: GMTSAR's bin plus this interpreter's.

        A SLURM child starts from a login shell, so neither GMTSAR nor the env
        carrying insarhub is on its PATH by default -- and p2p_S1_TOPS_Frame is
        `#!/usr/bin/env python3`, so the python it finds must satisfy its
        imports too.
        """
        lines: list[str] = []
        # Same resolution as the local subprocess path (_subprocess_env), so a
        # pair behaves identically whether it runs here or on a compute node.
        # A SLURM child inherits nothing from the submitting shell, so anything
        # left implicit surfaces only once a job starts, as
        # "p2p_S1_TOPS_Frame: command not found" in one pair's log.
        env_bin = self.config.gmtsar_env_bin
        if env_bin:
            lines.append(f'export PATH="{env_bin}:$PATH"')
        root = self.config.gmtsar_root
        if root:
            lines.append(f'export GMTSAR="{root}"')
            lines.append(f'export PATH="{root}/bin:$PATH"')
        lines.append(f'export PATH="{Path(sys.executable).parent}:$PATH"')
        return lines

    def _run_pairs(self, pairs: list[tuple]) -> None:
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as pool:
            futures = {pool.submit(self._run_one_pair, pair): pair for pair in pairs}
            for fut in as_completed(futures):
                pair = futures[fut]
                key = _pair_key(pair)
                try:
                    ok = fut.result()
                except Exception:
                    logger.exception("pair %s raised", key)
                    ok = False
                status = _SUCCEEDED if ok else _FAILED
                _write_status(self._status_dir(pair), status)
                with self._lock:
                    self.jobs[key]["status"] = status
                self.save()

    # ------------------------------------------------------------------ #
    #  Time-series stack mode                                             #
    # ------------------------------------------------------------------ #

    def _subswath_list(self) -> list[int]:
        """Parse config.subswath into a list of IW numbers.

        Accepts an int (2), an ISCE-style space-separated string ("1 2 3"),
        or a comma/space list. "1 2 3" (the default) = full frame.
        """
        sw = self.config.subswath
        if isinstance(sw, int):
            return [sw]
        toks = str(sw).replace(",", " ").split()
        return [int(t) for t in toks] or [2]

    @property
    def _multiswath(self) -> bool:
        return len(self._subswath_list()) > 1

    def _swath_layout(self) -> list[tuple[int, Path, Path, Path]]:
        """(subswath, work_dir, raw_dir, topo_dir) per subswath.

        Multi-subswath (full frame) -> one F<N>/ pipeline each, merged after.
        Single subswath -> the flat case_dir layout (already validated; kept
        untouched so existing single-swath stacks stay valid)."""
        p = self._paths
        if self._multiswath:
            return [(n, p.swath_dir(n), p.swath_raw_dir(n), p.swath_topo_dir(n))
                    for n in self._subswath_list()]
        n = self._subswath_list()[0]
        return [(n, self.case_dir, p.raw_dir, p.topo_dir)]

    @property
    def _stack_stages(self) -> tuple[str, ...]:
        """Tracked stage keys, in run order: align -> topo -> intf, plus a
        final merge for multi-subswath. Same stage names regardless of
        subswath count -- each stage is ONE manager whose sliding window
        fans out across every subswath (align_F<N>/topo_F<N> are fully
        independent of each other: different subswath directories, no data
        dependency -- see _stage_commands()) and, for intf, every pair
        within each subswath too, instead of one manager per subswath
        chained strictly sequentially.

        The "topo" stage precomputes topo_ra.grd once per subswath (Stage 1
        of intf_tops.csh) before any intf pair for that subswath starts --
        see run_stage_unit()'s docstring for why this can't be folded into
        intf itself (topo_ra.grd is per-swath, not per-pair, and Stage 1's
        cleanup.csh topo call is unsafe to run concurrently with itself).

        "mergeprep" is the exact analogue for the merge stage: it merges the
        first pair alone so merge_batch.csh produces the shared cross-pair
        geometry (trans.dat/raln.grd/ralt.grd/landmask_ra.grd) once, before
        "merge" fans the remaining pairs out concurrently -- see
        _run_mergeprep_unit()."""
        # "esdnet" refines the align output with a network-inverted azimuth
        # misregistration before anything consumes the aligned SLCs (see
        # _run_esdnet_unit()). Opt-in, and only meaningful for a stack big
        # enough to form a network -- a 2-scene stack has nothing to invert.
        # On-disk evidence wins over config: `refresh`/`watch`/`cancel` rebuild
        # the processor with whatever pairs the workdir yields (often the
        # placeholder), so a config-only test would silently drop a stage that
        # demonstrably ran and hide its status from the user.
        extra: tuple[str, ...] = ()
        ran_esdnet = self._stack_status_dir("esdnet").exists()
        if ran_esdnet or (bool(getattr(self.config, "esd_network", False))
                          and self._scene_count() >= 3):
            extra = ("esdnet",)
        if self._multiswath:
            return ("align", *extra, "topo", "intf",
                    "mergeprep", "merge", "cohmask", "unwrap")
        return ("align", *extra, "topo", "intf")

    def _scene_count(self) -> int:
        """Number of distinct scenes, WITHOUT parsing acquisition times.

        _stack_stages() only needs "is this stack big enough to invert an ESD
        network" (>= 3 scenes), and it is evaluated during __init__ via
        _rediscover_state(). At that point self.pairs may be the placeholder
        _load_local_processor() passes when a workdir has no saved pairs --
        [("_", "_")] -- so anything that parses a date out of the scene name
        raises and takes down `refresh`/`watch`/`cancel`, the very commands used
        to inspect a workdir. Counting names avoids the parse entirely, and
        tolerates 2-tuples as well as the normal 4-tuples.
        """
        scenes: set[str] = set()
        for p in self.pairs or []:
            if not p:
                continue
            scenes.add(p[0])
            if len(p) > 2:
                scenes.add(p[2])
        return len(scenes)

    def _scene_map(self) -> dict[str, str]:
        """Ordered {safe_name: eof_name} for every unique scene across all
        pairs, sorted by acquisition date (earliest first). Order matters:
        the super-master defaults to the earliest scene, and data.in is
        written in date order."""
        m: dict[str, str] = {}
        for ref_safe, ref_eof, sec_safe, sec_eof in self.pairs:
            m.setdefault(ref_safe, ref_eof)
            m.setdefault(sec_safe, sec_eof)
        # sort by the YYYYMMDDTHHMMSS token in the .SAFE name
        return dict(sorted(m.items(), key=lambda kv: _scene_start_time(kv[0])))

    def _stack_status_dir(self, stage: str) -> Path:
        return self.case_dir / ".stack_status" / stage

    def _submit_stack(self) -> dict:
        if float(self.config.threshold_snaphu) <= 0:
            # SBAS needs unwrapped interferograms; threshold_snaphu=0 (the
            # pop_config default) skips unwrapping entirely, leaving no
            # unwrap.grd for prep_sbas/sbas. Bump to a sane default so the
            # stack is actually usable downstream, and say so loudly.
            logger.warning(
                "threshold_snaphu=%s skips unwrapping, but a time-series stack "
                "needs unwrapped interferograms -- using 0.1 instead. Set "
                "config.threshold_snaphu explicitly to override.",
                self.config.threshold_snaphu,
            )
            self.config.threshold_snaphu = 0.1
        self._stage_stack()
        self.jobs = {
            stage: {"stage": stage, "status": _read_status(self._stack_status_dir(stage)),
                    "submitted_at": datetime.now(timezone.utc).isoformat()}
            for stage in self._stack_stages
        }
        if self.config.dry_run:
            logger.info("dry_run: staged stack (%d scenes, %d pairs); would run "
                        "preproc_batch_tops + intf_tops_parallel.",
                        len(self._scene_map()), len(self.pairs))
            return self.jobs
        if self.config.hpc_mode:
            return self._submit_stack_hpc()
        self._run_local_or_sync(self._run_stack)
        self.save()
        return self.jobs

    # ------------------------------------------------------------------ #
    #  HPC (SLURM) stack submission                                       #
    # ------------------------------------------------------------------ #
    # Mirrors ISCE2_Base._step_executor_hpc's sliding-window chain-submission
    # design (see isce2_base.py / docs/advanced/processor.md for the QOS
    # rationale) but with no "group manager spanning multiple named steps"
    # concept -- GMTSAR's stages never need fusing, every stage already maps
    # to exactly one manager. The other real difference: GMTSAR has no flat
    # shell-command-list generator the way ISCE2_S1's stackSentinel.py run_NN_*
    # files do, so each child job re-enters `insarhub` itself (the
    # run-stage-unit CLI action, see cli/main.py) to call one of the
    # already-implemented per-unit methods above, instead of a raw shell
    # command line running a GMTSAR binary directly.

    def _stage_hpc_dir(self, stage: str) -> Path:
        """Where a stage's manager/child sbatch scripts, logs, and
        cmd_<idx>.done/.fail markers live -- kept separate from
        .stack_status/<stage>/ (the SUCCEEDED/FAILED marker dir _read_status
        actually reads) so HPC bookkeeping never collides with it."""
        return self.case_dir / ".hpc" / stage

    def _manager_paths_for_stage(self, stage: str) -> tuple[Path, Path]:
        """Deterministic (manager_script, chained_job_id_file) paths for a
        stage -- computed before the script itself is built, so an earlier
        stage's script can embed the next stage's path in its own
        chain-submit trailer (see slurm_manager.chain_submit_lines)."""
        d = self._stage_hpc_dir(stage)
        return d / "manager.sbatch", d / "chained_job_id.txt"

    def _stage_commands(self, stage: str) -> list[str]:
        """Shell command(s) for one stack stage's HPC child job(s) -- each
        one re-enters `insarhub` via run-stage-unit (see run_stage_unit()'s
        docstring for why).

        align/topo (multi-subswath): one command per subswath, via
        --subswath -- align_F<N>/topo_F<N> are fully independent of each
        other (different subswath directories, no data dependency), so this
        stage's ONE manager fans them out as concurrent child jobs instead
        of forcing one manager per subswath through a strictly-sequential
        chain. Single-subswath: exactly 1 command (--subswath is then just
        the one configured subswath; run_stage_unit() defaults it anyway).

        intf: one command per (subswath, pair) combination -- --subswath
        AND --index together, since a pair index alone is ambiguous once
        pairs from every subswath are pooled into one manager.

        merge: exactly 1 command (already a single cross-subswath operation).

        If config.container is set, each command is wrapped to run inside
        it (docker/apptainer must be available on the compute nodes running
        these child sbatch jobs) -- only the actual GMTSAR-touching work
        runs containerized this way; the sbatch manager scaffolding
        (sliding-window submit/poll loop, chain-submission) stays on the
        host, since it never calls GMTSAR/gmt itself.
        """
        base = (f'insarhub processor -N GMTSAR_S1 -w "{self.workdir}" '
                f'run-stage-unit --stage {stage}')
        if stage == "intf":
            cmds = []
            for sw in self._subswath_list():
                work, _raw, _topo = self._swath_paths(sw)
                n_pairs = len([l for l in (work / "intf.in").read_text().splitlines() if l.strip()])
                cmds += [f"{base} --subswath {sw} --index {i}" for i in range(n_pairs)]
        elif stage == "merge":
            # One command per pair, skipping index 0 -- mergeprep already
            # merged it to produce the shared trans.dat these all reuse.
            cmds = [f"{base} --index {i}"
                    for i in range(1, self._merge_pair_count())]
        elif stage == "unwrap":
            # Every pair, index 0 included: mergeprep merges the seed pair but
            # unwrapping is now a separate stage, so nothing has unwrapped it.
            cmds = [f"{base} --index {i}" for i in range(self._merge_pair_count())]
        elif stage in ("align", "esdnet", "topo") and self._multiswath:
            # esdnet is per-subswath like align/topo: each subswath has its
            # own raw/ dir, its own shift tables and its own ESD network, with
            # no cross-subswath dependency.
            cmds = [f"{base} --subswath {sw}" for sw in self._subswath_list()]
        else:
            cmds = [base]
        if self.config.container:
            from insarhub.utils.container import wrap_container_cmd
            cmds = [wrap_container_cmd(self.config.container, c, self.workdir) for c in cmds]
        return cmds

    def _stage_command_labels(self, stage: str) -> list[str]:
        """Human-readable label for each of _stage_commands(stage)'s child
        jobs, in the same order (so cmd_<idx>.done/.fail in .hpc/<stage>/
        lines up with labels[idx]) -- used by refresh()'s --ls detail view."""
        if stage == "intf":
            labels: list[str] = []
            for sw in self._subswath_list():
                work, _raw, _topo = self._swath_paths(sw)
                pair_lines = [l for l in (work / "intf.in").read_text().splitlines() if l.strip()]
                labels += [f"F{sw} {line}" for line in pair_lines]
            return labels
        if stage == "merge":
            lines = self._merge_input_lines()
            n = self._merge_pair_count()
            return [self._pair_id_from_merge_line(lines[i]) if i < len(lines)
                    else f"pair {i}" for i in range(1, n)]
        if stage == "unwrap":
            lines = self._merge_input_lines()
            n = self._merge_pair_count()
            return [self._pair_id_from_merge_line(lines[i]) if i < len(lines)
                    else f"pair {i}" for i in range(n)]
        if stage == "cohmask":
            return ["stacked-coherence mask"]
        if stage == "mergeprep":
            lines = self._merge_input_lines()
            seed = self._pair_id_from_merge_line(lines[0]) if lines else "seed pair"
            return [f"seed {seed}"]
        if stage in ("align", "topo") and self._multiswath:
            return [f"F{sw}" for sw in self._subswath_list()]
        return [stage]

    def _merge_input_lines(self) -> list[str]:
        """merge_input's lines, when they exist -- for labelling the merge
        stage's fan-out. Empty on a fresh run; use _merge_pair_count() for
        sizing, which works before any interferogram exists."""
        merge_input = self._paths.merge_dir / "merge_input"
        if merge_input.exists():
            return [l for l in merge_input.read_text().splitlines() if l.strip()]
        return []

    def _merge_pair_count(self) -> int:
        """How many pairs the merge stage will have to merge.

        Sized from intf.in -- the planned pair list, written by _stage_stack()
        before anything is submitted -- NOT from merge_input, which
        create_merge_input.csh can only produce once intf has actually run.
        Every stage's HPC manager (and therefore its child command list) is
        generated up front at submit time so the managers can chain, so on a
        from-scratch run merge's fan-out has to be derivable before a single
        interferogram exists. Sizing it from merge_input meant a fresh submit
        built the merge stage with ZERO commands: it then "succeeded"
        immediately having merged nothing, while earlier retry runs looked
        fine only because intf_all/ was already populated from a previous run.

        This is an upper bound: a pair that fails in intf for some subswath
        won't appear in merge_input, so the corresponding child job finds no
        line for its index and exits successfully having done nothing (see
        _run_merge_unit) -- that pair's failure is already reported by the
        intf stage rather than being double-counted here.
        """
        for sw in self._subswath_list():
            work, _raw, _topo = self._swath_paths(sw)
            intf_in = work / "intf.in"
            if intf_in.exists():
                n = len([l for l in intf_in.read_text().splitlines() if l.strip()])
                if n:
                    return n
        return len(self._merge_input_lines())

    def _gmtsar_hpc_env_lines(self) -> list[str]:
        """export PATH line ensuring each child job's `insarhub` re-entry
        finds the same conda env's `insarhub` this submit() call is running
        under -- run_stage_unit()'s dispatched methods build GMTSAR's own
        PATH themselves via _subprocess_env(), so this only needs to make
        `insarhub`/python runnable at all, not GMTSAR-aware."""
        env = os.environ.copy()
        bin_dir = str(Path(sys.executable).parent)
        path = bin_dir + os.pathsep + env.get("PATH", "")
        return [f"export PATH={path!r}"]

    def _sbatch_opts_for_stage(self, stage: str) -> dict:
        from insarhub.processor.isce2_base import (
            _merge_sbatch_opts, load_or_init_sbatch_options)
        per_step = load_or_init_sbatch_options(
            self.workdir, step_key=stage, step_label=stage,
            default_template=_GMTSAR_SBATCH_DEFAULT_TEMPLATE,
        )
        self.config.sbatch_options_per_step = per_step or {}
        return _merge_sbatch_opts(self.config.sbatch_options_per_step, stage)

    def _submit_stack_hpc(self) -> dict:
        """HPC analogue of _run_stack(): one sliding-window sbatch manager
        per stack stage, chain-submitted the same way ISCE2_S1's step
        managers are -- only the first stage's manager is submitted
        directly; every manager chain-submits the next on success, so at
        most one is ever actually sitting in the SLURM queue at a time."""
        from colorama import Fore, Style
        from insarhub.utils.slurm_manager import build_sliding_window_manager
        from insarhub.processor.isce2_base import _manager_partition, _manager_time

        max_concurrent = self.config.max_concurrent_hpc

        # ── Pass 1: classify -- skip stages already SUCCEEDED ───────────────
        to_build: list[str] = []
        cmds_by_stage: dict[str, list[str]] = {}
        for stage in self._stack_stages:
            if self.config.skip_existing and _read_status(self._stack_status_dir(stage)) == _SUCCEEDED:
                self.jobs[stage] = {"stage": stage, "status": _SUCCEEDED,
                                     "submitted_at": datetime.now(timezone.utc).isoformat()}
                print(f"  {Fore.GREEN}  ✓ {stage}  (all commands already done)"
                      f"{Style.RESET_ALL}")
                continue
            cmds_by_stage[stage] = self._stage_commands(stage)
            to_build.append(stage)

        if not to_build:
            self.save()
            print(f"\n{Fore.GREEN}All stages already complete.{Style.RESET_ALL}")
            return self.jobs

        # ── Pass 2: precompute every stage's (manager_script, job_id_file)
        #    path up front so each stage's trailer can point at the next ────
        manager_paths = {s: self._manager_paths_for_stage(s) for s in to_build}
        env_lines = self._gmtsar_hpc_env_lines()

        # ── Pass 3: build every manager script (none submitted yet) ────────
        for i, stage in enumerate(to_build):
            hpc_dir = self._stage_hpc_dir(stage)
            hpc_dir.mkdir(parents=True, exist_ok=True)
            next_script, next_jobfile = (
                manager_paths[to_build[i + 1]] if i + 1 < len(to_build) else (None, None)
            )
            status_dir = self._stack_status_dir(stage)
            write_status_fn = (
                f'STATUS_DIR="{status_dir}"\n'
                'write_status() {\n'
                '    mkdir -p "$STATUS_DIR"\n'
                '    rm -f "$STATUS_DIR/.succeeded" "$STATUS_DIR/.failed"\n'
                '    case "$1" in\n'
                '        SUCCEEDED) touch "$STATUS_DIR/.succeeded" ;;\n'
                '        FAILED*)   touch "$STATUS_DIR/.failed" ;;\n'
                '    esac\n'
                '}'
            )
            step_cfg = self._sbatch_opts_for_stage(stage)
            _slurm_fields = {f.name for f in dataclasses.fields(Slurmjob_Config)}
            build_sliding_window_manager(
                job_name_base=f"g_{stage}", commands=cmds_by_stage[stage],
                log_dir=hpc_dir, sbatch_dir=hpc_dir, max_concurrent=max_concurrent,
                slurm_kwargs={k: v for k, v in step_cfg.items()
                              if k in _slurm_fields},
                env_lines=env_lines, write_status_fn=write_status_fn,
                manager_partition=_manager_partition(
                    self.config.sbatch_options_per_step or {}),
                manager_time=_manager_time(
                    self.config.sbatch_options_per_step or {}),
                next_manager_script=next_script, next_job_id_file=next_jobfile,
                file_prefix=stage, label=stage,
            )
            self.jobs[stage] = {
                "stage": stage, "status": _PENDING, "slurm_job_ids": [],
                "job_id_file": str(manager_paths[stage][1]),
                "n_cmds": len(cmds_by_stage[stage]),
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            }

        # ── Submit only the first stage's manager; the rest chain-submit ───
        first_script = manager_paths[to_build[0]][0]
        result = subprocess.run(f"sbatch {first_script}", shell=True,
                                capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  {Fore.RED}sbatch failed for {to_build[0]}: "
                  f"{result.stderr.strip()}{Style.RESET_ALL}")
            for stage in to_build:
                self.jobs[stage]["status"] = _FAILED
                _write_status(self._stack_status_dir(stage), _FAILED)
            self.save()
            return self.jobs
        m = re.search(r"\d+", result.stdout)
        job_id = m.group() if m else "unknown"
        self.jobs[to_build[0]]["slurm_job_ids"] = [job_id]
        # Write the first stage's own job id to its job_id_file too, even
        # though nothing chains INTO it -- self.jobs isn't reloaded from
        # gmtsar_jobs.json on a later CLI invocation (_rediscover_state()
        # always rebuilds purely from on-disk status markers), so without
        # this only the chained stages' job ids would survive across
        # separate `refresh`/`cancel` calls. Makes every stage's job id
        # uniformly disk-derivable via _manager_paths_for_stage().
        manager_paths[to_build[0]][1].write_text(job_id)

        # Per-stage breakdown then a closing summary, mirroring
        # ISCE2_Base._step_executor_hpc()'s output line for line.
        for i, stage in enumerate(to_build):
            n_cmds    = len(cmds_by_stage[stage])
            n_batches = (n_cmds + max_concurrent - 1) // max_concurrent
            tag       = f"[{job_id}]" if i == 0 else "(chained, queued after previous stage)"
            print(f"  {Fore.CYAN}  ▶ [{stage}]  →  manager {tag}  "
                  f"({n_cmds} cmd, {n_batches} batch(es), "
                  f"max {max_concurrent} concurrent){Style.RESET_ALL}")

        self.save()
        print(f"\n{Fore.GREEN}First job queued [{job_id}] — {len(to_build)} stage(s) total, "
              f"chaining automatically. "
              f"SSH session can now be closed — use 'refresh' to check status."
              f"{Style.RESET_ALL}")
        return self.jobs

    def _ordered_scenes(self) -> tuple[dict[str, str], list[str]]:
        """(scene->eof map, date-ordered scene list with super-master first)."""
        cfg = self.config
        scenes = self._scene_map()
        ordered = list(scenes)
        if cfg.reference:
            ref_match = next((s for s in ordered
                              if cfg.reference in s), None)
            if ref_match:
                ordered.remove(ref_match)
                ordered.insert(0, ref_match)
        return scenes, ordered

    # ------------------------------------------------------------------ #
    #  Reframing (GMTSAR tutorial section 3c/4b)                          #
    # ------------------------------------------------------------------ #
    # The official Sentinel-1 TOPS recipe cuts every scene of a track to one
    # common along-track window BEFORE aligning, using two pins dropped in
    # orbit order (pins.ll) and organize_files_tops.csh. We drive the same
    # cut with create_frame_tops.csh, which is the reframing core that
    # organize_files_tops.csh calls in a loop: the wrapper additionally
    # groups by date and auto-downloads orbits, neither of which we need
    # (one .SAFE per date, POEORBs already staged by the downloader), and
    # its date arithmetic is macOS-only (`date -jf`) -- the reason a
    # separate organize_files_tops_linux.csh exists upstream at all.
    #
    # Reframing does NOT fix the p100_f466 azimuth-misregistration defect
    # (that stack's scenes were already co-framed: 9 bursts each, first-burst
    # azimuthAnxTime within 14 lines across all 13 dates, yet two of them
    # still got a ~40-line offset out of preproc_batch_tops). It is here
    # because the tutorial requires it and because it makes the burst window
    # identical by construction rather than by luck.

    @staticmethod
    def _safe_footprint(safe_dir: Path):
        """The scene's ground footprint as a shapely polygon, read from
        manifest.safe's <gml:coordinates> (space-separated "lat,lon" pairs)."""
        from shapely.geometry import Polygon
        manifest = Path(safe_dir) / "manifest.safe"
        if not manifest.exists():
            return None
        m = re.search(r"<gml:coordinates>(.*?)</gml:coordinates>",
                      manifest.read_text(), re.S)
        if not m:
            return None
        pts = []
        for tok in m.group(1).split():
            lat, lon = tok.split(",")
            pts.append((float(lon), float(lat)))   # shapely wants (x=lon, y=lat)
        return Polygon(pts) if len(pts) >= 3 else None

    @staticmethod
    def _descending(safe_dir: Path, pol: str = "vv") -> bool:
        """True when latitude DECREASES with azimuth line, i.e. the platform
        is flying south. Read straight off the annotation's geolocation grid
        so it needs no PRM/orbit -- create_frame_tops.csh requires the second
        pin to come later in orbit time than the first, and "later" means
        southward on a descending pass, northward on an ascending one."""
        xmls = sorted(Path(safe_dir).glob(f"annotation/s1?-iw*-slc-{pol}-*.xml"))
        if not xmls:
            return True
        txt = xmls[0].read_text()
        pts = re.findall(
            r"<line>(\d+)</line>.*?<latitude>([-+0-9.eE]+)</latitude>", txt, re.S)
        if len(pts) < 2:
            return True
        first, last = pts[0], pts[-1]
        return float(last[1]) < float(first[1])

    def _reframe_pins(self, scene_dirs: list[Path]) -> list[tuple[float, float]] | None:
        """The two along-track pins every scene gets cut to.

        AOI drives this entirely:

          * AOI set          -> cut to the AOI, clipped to what the scenes
                                actually share so the cut is satisfiable by
                                every date. An AOI LARGER than the stack
                                therefore collapses to the common footprint.
          * AOI None/blank   -> FULL FRAME: no cut at all. Returns None and
                                the caller leaves every scene on its
                                delivered burst window.

        Returns [(lon, lat), (lon, lat)] ordered by increasing orbit time,
        or None to mean "do not reframe".
        """
        aoi = self._resolve_aoi()
        if not aoi:
            logger.info("Reframe: no AOI configured -- keeping the full frame.")
            return None
        coords = self._aoi_coords(aoi)
        if len(coords) < 3:
            logger.warning("Reframe: AOI %r has <3 vertices -- keeping the "
                           "full frame.", aoi)
            return None

        foots = [f for f in (self._safe_footprint(d) for d in scene_dirs) if f]
        if not foots:
            logger.warning("Reframe: no readable footprints -- keeping the "
                           "full frame.")
            return None
        common = foots[0]
        for f in foots[1:]:
            common = common.intersection(f)
        if common.is_empty:
            logger.warning("Reframe: scenes share no common footprint -- "
                           "keeping the full frame.")
            return None

        from shapely.geometry import Polygon
        target = Polygon(coords).intersection(common)
        if target.is_empty:
            logger.warning("Reframe: AOI does not intersect the scenes -- "
                           "keeping the full frame.")
            return None
        src = "AOI ∩ scene common footprint"

        min_lon, min_lat, max_lon, max_lat = target.bounds
        lon_mid = (min_lon + max_lon) / 2.0
        north, south = (lon_mid, max_lat), (lon_mid, min_lat)
        pins = [north, south] if self._descending(
            scene_dirs[0], self.config.polarization) else [south, north]
        logger.info("Reframe pins from %s: %.4f/%.4f -> %.4f/%.4f",
                    src, pins[0][0], pins[0][1], pins[1][0], pins[1][1])
        return pins

    def _reframe_scenes(self, scenes: dict[str, str]) -> dict[str, Path]:
        """Cut every scene to the common along-track window, returning
        {safe_name: reframed_safe_dir}. Scenes that fail to reframe are
        simply absent from the mapping, so staging falls back to their
        original .SAFE for those -- a partial reframe is still valid input,
        it just leaves those dates on their delivered burst window."""
        scene_dirs = {s: self._find_input(s, self.config.slc_dir) for s in scenes}
        pins = self._reframe_pins(list(scene_dirs.values()))
        if pins is None:
            return {}          # full frame -- create nothing on disk

        reframed_root = self.case_dir / "reframed"
        reframed_root.mkdir(parents=True, exist_ok=True)
        pins_ll = reframed_root / "pins.ll"
        pins_ll.write_text("".join(f"{lon:.6f} {lat:.6f}\n" for lon, lat in pins))

        env = self._subprocess_env()
        out: dict[str, Path] = {}
        for safe, safe_dir in scene_dirs.items():
            date = _scene_start_time(safe)[:8]
            dest = reframed_root / f"{date}.SAFE"
            if dest.exists():                      # honor skip_existing reruns
                out[safe] = dest
                continue
            eof = self._find_input(scenes[safe], self.config.orbit_dir)
            work = reframed_root / f".work_{date}"
            if work.exists():
                shutil.rmtree(work)
            work.mkdir(parents=True)
            # The SAFE list takes absolute paths, but the orbit and pins
            # arguments MUST be bare names in the invocation directory:
            # create_frame_tops.csh does `cd new.SAFE` and then reads them
            # back as ../$orb and ../$tps (csh lines 78-83), so an absolute
            # path becomes "..//abs/path" and silently fails to open. The
            # script does not check, and still exits 0 having written an
            # empty annotation/ and measurement/.
            (work / "SAFE_filelist").write_text(f"{safe_dir.resolve()}\n")
            (work / eof.name).symlink_to(eof.resolve())
            (work / "pins.ll").symlink_to(pins_ll.resolve())
            log = reframed_root / f"reframe_{date}.log"
            with open(log, "w") as lf:
                proc = subprocess.run(
                    [self._gmtsar_script("create_frame_tops.csh"), "SAFE_filelist", eof.name,
                     "pins.ll", self.config.polarization],
                    cwd=str(work), stdout=lf, stderr=subprocess.STDOUT, env=env)
            # create_frame_tops.csh renames new.SAFE on success; take whatever
            # .SAFE it left behind rather than trusting a name format. Judge
            # success by CONTENT, not by exit status or directory existence --
            # the failure mode above produces a well-formed but empty .SAFE.
            produced = [d for d in sorted(work.glob("*.SAFE"))
                        if any((d / "measurement").glob("*.tiff"))
                        and any((d / "annotation").glob("*.xml"))]
            if proc.returncode != 0 or not produced:
                logger.warning("Reframe produced no bursts for %s (see %s) -- "
                               "using the original .SAFE for this date.", date, log)
                shutil.rmtree(work, ignore_errors=True)
                continue
            shutil.move(str(produced[0]), str(dest))
            shutil.rmtree(work, ignore_errors=True)
            out[safe] = dest
            logger.info("Reframed %s -> %s", date, dest.name)
        if out:
            logger.info("Reframed %d/%d scenes into %s",
                        len(out), len(scene_dirs), reframed_root)
        return out

    def _stage_stack(self) -> None:
        """Stage the stack: one F<N> case per subswath (full frame) or a
        single flat case (single subswath). Each case gets raw/ with that
        subswath's per-scene .tiff/.xml/.EOF, topo/dem.grd, and its data.in /
        intf.in / batch_tops.config."""
        scenes, ordered = self._ordered_scenes()
        for safe in scenes:
            self._ensure_safe_extracted(safe)

        # Cut every scene to one common along-track window before anything
        # reads a subswath out of it (tutorial 3c/4b). Always runs -- it is
        # part of the documented workflow, and its extent is already fully
        # specified by AOI (see _reframe_pins). Consulted by
        # _extract_subswath_stem via _safe_source().
        self._reframed_safe: dict[str, Path] = self._reframe_scenes(scenes)

        # safe_name -> {subswath: raw measurement stem}
        self._stack_raw_stems: dict[str, dict[int, str]] = {s: {} for s in scenes}

        shared_dem = None   # download once, symlink into every F<N>/topo
        for sw, work, raw, topo in self._swath_layout():
            raw.mkdir(parents=True, exist_ok=True)
            topo.mkdir(parents=True, exist_ok=True)

            # DEM: build for the first swath, reuse for the rest
            if shared_dem is None:
                shared_dem = self._ensure_dem(topo).resolve()
            else:
                dest = topo / "dem.grd"
                if not dest.exists():
                    dest.symlink_to(shared_dem)

            # per-subswath stems into this swath's raw/
            for safe, eof in scenes.items():
                self._stack_raw_stems[safe][sw] = \
                    self._extract_subswath_stem(safe, eof, raw, subswath=sw)

            # data.in: <raw_measurement_stem>:<orbit.EOF>, super-master first
            (raw / "data.in").write_text("\n".join(
                f"{self._stack_raw_stems[s][sw]}:{scenes[s]}" for s in ordered
            ) + "\n")

            # intf.in: ALIGNED stems S1_<date>_ALL_F<sw> (match preproc's
            # .SLC/.PRM + baseline_table.dat; intf_tops reads ./raw/<stem>.SLC)
            intf_lines = [
                f"{_gmtsar_aligned_stem(self._stack_raw_stems[r][sw])}:"
                f"{_gmtsar_aligned_stem(self._stack_raw_stems[sec][sw])}"
                for r, _re, sec, _se in self.pairs
            ]
            (work / "intf.in").write_text("\n".join(intf_lines) + "\n")

            master_stem = _gmtsar_aligned_stem(self._stack_raw_stems[ordered[0]][sw])
            (work / "batch_tops.config").write_text(self._batch_tops_config(master_stem))

    def _batch_tops_config(self, master_stem: str) -> str:
        """GMTSAR csh-style batch_tops.config (key = value), as intf_tops.csh
        parses via `grep KEY | awk '{print $3}'`. Distinct from the config.py
        the per-pair path writes. Pulls the overlapping processing params
        straight from the config's fields (so stack mode honors the same
        filter/decimation/threshold knobs), with master_image filled from the
        super-master and switch_land derived from mask_water."""
        cfg = self.config
        # Same filter/looks coupling as the p2p path -- stack mode geocodes
        # through the same proj_ra2ll, so it holes identically (measured on
        # p100_f466_gmtsar_esdnet: 57.6% of unwrap_ll.grd populated).
        self._check_geocode_posting()
        # region_cut / num_patches use "" (blank) rather than GMTSAR's -999
        # sentinel in batch_tops.config's grep-based parser.
        def _blank(v):
            return "" if str(v) == "-999" else v
        return (
            f"proc_stage = {cfg.proc_stage}\n"
            f"master_image = {master_stem}\n"
            f"num_patches = {_blank(cfg.num_patches)}\n"
            f"topo_phase = {cfg.topo_phase}\n"
            f"shift_topo = {cfg.shift_topo}\n"
            f"filter_wavelength = {cfg.filter_wavelength}\n"
            f"dec_factor = {cfg.dec_factor}\n"
            f"range_dec = {cfg.range_dec}\n"
            f"azimuth_dec = {cfg.azimuth_dec}\n"
            f"threshold_snaphu = {cfg.threshold_snaphu}\n"
            f"threshold_geocode = {cfg.threshold_geocode}\n"
            f"region_cut = {_blank(cfg.region_cut)}\n"
            f"switch_land = {cfg.mask_water}\n"
            f"defomax = {cfg.defomax}\n"
            f"near_interp = {cfg.near_interp}\n"
        )

    def _set_stage(self, stage: str, status: str) -> None:
        _write_status(self._stack_status_dir(stage), status)
        with self._lock:
            if stage in self.jobs:
                self.jobs[stage]["status"] = status
        self.save()

    def _swath_paths(self, sw: int) -> tuple[Path, Path, Path]:
        """(work, raw, topo) for a single subswath -- used by the per-unit
        methods below, which (unlike _run_stack()'s loop) only know a swath
        number, not the tuple _swath_layout() already picked out for it."""
        for n, work, raw, topo in self._swath_layout():
            if n == sw:
                return work, raw, topo
        raise ValueError(f"subswath {sw} not in configured layout {self._subswath_list()}")

    def _config_with_stage(self, work: Path, stage: int, suffix: str,
                           skip_unwrap_geocode: bool = False) -> Path:
        """A throwaway copy of this swath's batch_tops.config with proc_stage
        forced to `stage`, written to work/batch_tops_<suffix>.config.

        skip_unwrap_geocode additionally forces threshold_snaphu = 0 and
        threshold_geocode = 0, which is how GMTSAR's own Sentinel-1
        time-series recipe configures the per-subswath interferogram runs of
        a multi-subswath stack ("How to make an InSAR time series from
        Sentinel-1 TOPS data", section 6b): "The two zeros for
        threshold_snaphu and threshold_geocode indicate that we want to skip
        unwrapping and geocoding as this will be done after merging the
        subswaths." Unwrapping each subswath separately is not just wasted
        work -- merge_unwrap_geocode_tops.csh consumes only phasefilt.grd,
        corr.grd and mask.grd and never reads a per-subswath unwrap.grd, so
        every one of those unwraps is discarded. On a real 3-subswath,
        27-pair stack the intf stage burned 79.5 h of CPU (max 6.8 h for a
        single pair) with SNAPHU dominating it. Same for the geocoding.

        _run_topo_unit()/_run_intf_unit() each need an explicit, unambiguous
        proc_stage for their own intf_tops.csh invocation (1 for topo-only,
        2 for intf-only) -- reading and patching the CURRENT on-disk
        batch_tops.config (rather than regenerating from self.config via
        _batch_tops_config()) means any AOI-derived region_cut that
        _apply_aoi_region_cut() already baked in is respected. Writing a
        SEPARATE file per caller (never mutating the shared batch_tops.config
        in place) means topo/intf callers never race or clobber each other's
        expected stage, regardless of call order or how many pairs are
        running concurrently -- unlike a single shared proc_stage toggle,
        which _apply_aoi_region_cut()'s own independent rewrites (one per
        fresh HPC child process, when an AOI is configured) could silently
        reset out from under a concurrently-running sibling."""
        text = (work / "batch_tops.config").read_text()
        text = re.sub(r"(?m)^proc_stage\s*=.*$", f"proc_stage = {stage}", text)
        if skip_unwrap_geocode:
            text = re.sub(r"(?m)^threshold_snaphu\s*=.*$", "threshold_snaphu = 0", text)
            text = re.sub(r"(?m)^threshold_geocode\s*=.*$", "threshold_geocode = 0", text)
        out = work / f"batch_tops_{suffix}.config"
        out.write_text(text)
        return out

    def _run_align_unit(self, sw: int) -> bool:
        """Align this swath's whole stack to its super-master: the two
        sequential preproc_batch_tops[_esd].csh calls. A standalone,
        self-contained unit of work -- called from _run_stack()'s local loop
        below AND (re-entering a fresh process via the CLI's run-stage-unit
        action) from an HPC child job, so it takes only what it needs to
        derive on its own (sw) rather than values a caller's loop happens to
        already have on hand."""
        env = self._subprocess_env()
        work, raw, topo = self._swath_paths(sw)
        dem = (topo / "dem.grd").resolve()
        log = work / "stack_align.log"
        # ESD (enhanced spectral diversity) alignment vs geometry-only,
        # selected by config.coregistration -- ESD is the analogue of
        # ISCE2_S1's NESD and the default. Same data.in/output contract,
        # esd variant just takes a 4th esd_mode arg.
        esd = str(self.config.coregistration).lower() == "esd"
        with open(log, "w") as lf:
            for mode in ("1", "2"):
                cmd = ([self._gmtsar_script("preproc_batch_tops_esd.csh"), "data.in", str(dem),
                        mode, str(self.config.esd_mode)] if esd else
                       [self._gmtsar_script("preproc_batch_tops.csh"), "data.in", str(dem), mode])
                proc = subprocess.run(
                    cmd, cwd=str(raw), stdout=lf, stderr=subprocess.STDOUT, env=env)
                if proc.returncode != 0:
                    return False
        return True

    def _run_esdnet_unit(self, sw: int) -> bool:
        """Network-based ESD misregistration refinement for this swath.

        Runs AFTER _run_align_unit(). GMTSAR aligns every scene to the
        super-master in one step, so a scene 144 days out has its azimuth
        misregistration measured directly across 144 days -- where the
        burst-overlap ESD estimate is unreliable. The residual shows up as
        phase steps at burst boundaries that grow with distance from the
        master. Measured on p100_f466: p2p (12-day master) is clean, while
        both the InSARHub stack AND a tutorial-exact stock-GMTSAR run
        (master 120+ days out) show ~20 burst discontinuities at 11-13x
        background. ESD on/off and decimation were controlled and excluded
        -- it is the single-pass topology, not the settings.

        This stage measures a SHORT-baseline network instead (12-48 days,
        where ESD is reliable) and inverts it for per-date corrections, the
        way ISCE topsStack's run_07_pairs_misreg + run_08_timeseries_misreg
        do. ISCE holds ~0.0005 px residual flat from 12 to 144 days out on
        this same stack, with the same master.

        Measurement is GMTSAR's own spectral_diversity, and the correction
        is applied exactly where preproc_batch_tops_esd.csh applies its own
        (grdmath onto the azimuth shift table, then make_s1a_tops mode 1),
        so everything downstream is untouched.
        """
        from insarhub.processor._gmtsar_esd_network import run_esd_network

        env = self._subprocess_env()
        work, raw, _ = self._swath_paths(sw)
        # _scene_map() is date-sorted and the super-master is its first entry
        # (same rule _stage_stack() uses when writing data.in, whose line 1
        # preproc_batch_tops treats as the super-master).
        master = _scene_start_time(next(iter(self._scene_map())))[:8]
        # Resolve sharedir the way GMTSAR's own scripts do, so a non-standard
        # install layout is honoured; fall back to the conventional path.
        sharedir = Path(self.config.gmtsar_root) / "share" / "gmtsar"
        try:
            p = subprocess.run(["gmtsar_sharedir.csh"], env=env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            if p.returncode == 0 and p.stdout.strip():
                sharedir = Path(p.stdout.strip())
        except OSError:
            pass
        res = run_esd_network(
            raw=raw,
            sharedir=sharedir,
            master_date=master,
            max_days=int(self.config.esd_network_max_days),
            max_conn=int(self.config.esd_network_max_conn),
            env=env,
            log_path=work / "stack_esdnet.log",
        )
        if not res.n_used:
            logger.error("F%d: ESD network produced no usable measurements; "
                         "leaving the geometric alignment in place", sw)
            return False
        if not res.full_rank:
            logger.warning("F%d: ESD network rank %d -- some dates keep the "
                           "geometric alignment", sw, res.rank)
        logger.info("F%d: ESD network applied (RMSE %.5f px, %d/%d pairs)",
                    sw, res.rmse, res.n_used, res.n_used + res.n_failed)
        return True

    def _run_topo_unit(self, sw: int) -> bool:
        """Precompute topo/topo_ra.grd once for this swath -- Stage 1 of
        intf_tops.csh only (topo generation), run exactly once, before any
        per-pair intf work starts.

        REAL BUG FOUND via a p100_f466_gmtsar HPC run: Stage 1 depends only
        on the fixed super-master image + DEM (batch_tops.config's
        master_image, identical for every pair in this swath) -- it has
        nothing to do with which pair triggered it, and _run_intf_unit()
        used to call intf_tops.csh (both stages) once per pair. Stage 1
        starts with `cleanup.csh topo` (wipes topo/ except dem.grd), so two
        pairs' Stage 1 overlapping in time -- inherent once more than 1 pair
        runs concurrently -- means one pair's cleanup can delete topo/
        files another pair is actively reading/writing mid-computation.
        Confirmed as the actual cause of a "No datapoints inside region,
        aborting" / empty trans.dat failure in a real run (GMTSAR's own
        intf_tops_parallel.csh -- the upstream "run intf in parallel" wrapper
        -- has this identical bug, it just fans out intf_tops.csh under GNU
        parallel with no isolation for the shared topo/ dir either).

        Once this succeeds, _run_intf_unit() always requests Stage 2 only
        (see _config_with_stage) -- it never touches topo/ for writes again,
        only reads topo_ra.grd/trans.dat, so every pair is safe to run with
        full concurrency after this one precompute completes."""
        work, raw, topo = self._swath_paths(sw)
        self._apply_aoi_region_cut(work, raw)  # must run before Stage 1 so
        # region_cut (if any) is already baked into batch_tops.config --
        # idempotent/no-op once region_cut is set, same as _run_intf_unit's
        # own call.
        if self.config.topo_phase != 1:
            return True  # nothing for Stage 1 to produce; Stage 2 won't use -topo
        env = self._subprocess_env()
        cfg = self._config_with_stage(work, 1, "topo")
        empty_in = work / "topo_precompute.in"
        empty_in.write_text("")  # Stage 1 is unconditional on pair content
        log = work / "stack_topo.log"
        with open(log, "w") as lf:
            proc = subprocess.run(
                [self._gmtsar_script("intf_tops.csh"), empty_in.name, cfg.name],
                cwd=str(work), stdout=lf, stderr=subprocess.STDOUT, env=env)
        if proc.returncode != 0:
            return False
        return (topo / "topo_ra.grd").exists()

    def _run_stack(self) -> None:
        """Run the stack. Multi-subswath: an independent align+intf pipeline
        per F<N>, then merge_batch combines the subswaths (GMTSAR's own
        full-frame layout -- the structural analogue of ISCE topsStack). Single
        subswath: the flat case. Shells out to GMTSAR's batch tools (csh for the
        unported-stub TOPS batch steps, real Python/C for the rest)."""
        multi = self._multiswath
        swaths = self._swath_layout()

        # ── align: every subswath, concurrently (local ThreadPoolExecutor)
        # -- align_F1/F2/F3 are fully independent of each other (different
        # subswath directories, no data dependency), so there's no reason to
        # process them one swath at a time. See run_stage_unit()'s docstring
        # for the HPC-mode analogue. ──
        if self.config.skip_existing and _read_status(self._stack_status_dir("align")) == _SUCCEEDED:
            logger.info("align already succeeded, skipping.")
        else:
            self._set_stage("align", _RUNNING)
            ok = self._run_for_all_swaths(self._run_align_unit, swaths)
            self._set_stage("align", _SUCCEEDED if ok else _FAILED)
            if not ok:
                logger.error("align failed -- see stack_align.log in each subswath dir")
                return

        # ── topo: every subswath, concurrently -- each precomputes its own
        # topo_ra.grd once (also applies AOI -> region_cut first, since it
        # must be baked in before Stage 1 runs) -- must complete before any
        # pair's intf starts, see _run_topo_unit's docstring ──
        if self.config.skip_existing and _read_status(self._stack_status_dir("topo")) == _SUCCEEDED:
            logger.info("topo already succeeded, skipping.")
        else:
            self._set_stage("topo", _RUNNING)
            ok = self._run_for_all_swaths(self._run_topo_unit, swaths)
            self._set_stage("topo", _SUCCEEDED if ok else _FAILED)
            if not ok:
                logger.error("topo failed -- see stack_topo.log in each subswath dir")
                return

        # ── intf: every (subswath, pair) combination, pooled together --
        # safe for full concurrency now that topo has already run once per
        # subswath (see _run_intf_unit's docstring) ──
        if self.config.skip_existing and _read_status(self._stack_status_dir("intf")) == _SUCCEEDED:
            logger.info("intf already succeeded, skipping.")
        elif not self._run_all_intf(swaths):
            return

        if multi:
            self._run_merge()

    def _run_for_all_swaths(self, unit_fn, swaths) -> bool:
        """Run unit_fn(sw) for every subswath concurrently (local
        ThreadPoolExecutor) -- used for align/topo, both fully independent
        per subswath. Returns True only if every subswath succeeded."""
        results = []
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as pool:
            futs = {pool.submit(unit_fn, sw): sw for sw, _work, _raw, _topo in swaths}
            for fut in as_completed(futs):
                sw = futs[fut]
                try:
                    ok = fut.result()
                except Exception:
                    logger.exception("%s(F%s) raised", unit_fn.__name__, sw)
                    ok = False
                results.append(ok)
        return bool(results) and all(results)

    def _apply_aoi_region_cut(self, work: Path, raw: Path) -> None:
        """Derive region_cut from the AOI (if region_cut wasn't set manually)
        and rewrite this swath's batch_tops.config so intf crops to it."""
        if str(self.config.region_cut) != "-999":
            return  # user set it explicitly; respect it
        aoi = self._resolve_aoi()
        if not aoi:
            return  # no AOI -> full frame
        master_prm = next(iter(sorted(raw.glob("S1_*_ALL_F*.PRM"))), None)
        if master_prm is None:
            logger.warning("No super-master PRM in %s for AOI->region_cut; full frame.", raw)
            return
        region_cut = self._aoi_to_region_cut(master_prm, aoi)
        if not region_cut:
            return
        logger.info("AOI -> region_cut = %s (from %s)", region_cut, master_prm.name)
        # NB: region_cut is per-swath but stored on the shared config; the
        # value is only used immediately below to rewrite THIS swath's config.
        self.config.region_cut = region_cut
        (work / "batch_tops.config").write_text(self._batch_tops_config(master_prm.stem))

    def _stem_julian(self, aligned_stem: str, raw_dir: Path) -> int | None:
        """Integer Julian id GMTSAR names a pair dir with, from the aligned
        stem's PRM SC_clock_start (raw_dir/<stem>.PRM). e.g.
        S1_20210108_ALL_F2 -> 2021007. Locates the pair's real
        intf_all/<ref_id>_<rep_id>/ output dir."""
        prm = raw_dir / f"{aligned_stem}.PRM"
        if not prm.exists():
            return None
        for l in prm.read_text().splitlines():
            if l.strip().startswith("SC_clock_start"):
                try:
                    return int(float(l.split("=")[1]))
                except (ValueError, IndexError):
                    return None
        return None

    def _run_intf_unit(self, sw: int, pair_line: str) -> bool:
        """intf_tops.csh for one pair (one line of intf.in). A standalone,
        self-contained unit of work -- same reasoning as _run_align_unit:
        called both from _run_all_intf()'s local ThreadPoolExecutor below
        and from an HPC child job re-entering via run_stage_unit().

        Calls _apply_aoi_region_cut() unconditionally first: in HPC mode each
        pair is a fresh process with no in-memory state, so (unlike
        _run_stack()'s single unconditional call in local mode) there's no
        other place already guaranteed to run before intf starts. It's cheap
        and idempotent (self.config.region_cut short-circuits it after the
        first real computation), so redundant per-pair calls cost a bit of
        wasted SAT_llt2rat work, not correctness.

        Always requests Stage 2 only (proc_stage=2, via _config_with_stage)
        -- topo_ra.grd is precomputed once by _run_topo_unit() before any
        pair reaches this method (see its docstring for the real
        shared-topo/-directory race this avoids); Stage 2 never writes to
        topo/, only reads it, so this is safe under any pair concurrency.
        """
        work, raw, topo = self._swath_paths(sw)
        self._apply_aoi_region_cut(work, raw)
        env = self._subprocess_env()
        intf_all = work / "intf_all"   # intf_tops moves finished pairs here

        # Multi-subswath: unwrap and geocode once on the merged frame, not
        # per subswath -- GMTSAR's own recipe sets threshold_snaphu = 0 and
        # threshold_geocode = 0 here for exactly that reason (see
        # _config_with_stage). The finished product of this stage is then the
        # wrapped phasefilt.grd that the merge stage consumes. Single
        # subswath has no merge stage, so it must still unwrap here.
        skip_uw = self._multiswath
        product = ("phasefilt.grd" if skip_uw or float(self.config.threshold_snaphu) <= 0
                   else "unwrap.grd")

        tag = pair_line.replace(":", "__")
        pin = work / f"pair_{tag}.in"
        pin.write_text(pair_line + "\n")
        cfg = self._config_with_stage(work, 2, tag, skip_unwrap_geocode=skip_uw)
        with open(work / f"intf_{tag}.log", "w") as lf:
            subprocess.run([self._gmtsar_script("intf_tops.csh"), pin.name, cfg.name],
                           cwd=str(work), stdout=lf, stderr=subprocess.STDOUT, env=env)

        ref, rep = pair_line.split(":")
        rid = self._stem_julian(ref, raw)
        pid = self._stem_julian(rep, raw)
        pd = intf_all / f"{rid}_{pid}" if rid and pid else None
        return bool(pd and (pd / product).exists())

    def _run_all_intf(self, swaths) -> bool:
        """Run every (subswath, pair) combination concurrently (local
        ThreadPoolExecutor -- no GNU `parallel` dependency, real per-pair
        status), pooled across ALL subswaths at once rather than one
        subswath at a time. Safe because topo has already run once per
        subswath by this point (see _run_intf_unit's docstring). Returns
        True on full success."""
        self._set_stage("intf", _RUNNING)
        tasks: list[tuple[int, str]] = []
        for sw, work, _raw, _topo in swaths:
            pair_lines = [l for l in (work / "intf.in").read_text().splitlines() if l.strip()]
            tasks += [(sw, l) for l in pair_lines]

        results = []
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as pool:
            futs = {pool.submit(self._run_intf_unit, sw, l): (sw, l) for sw, l in tasks}
            for fut in as_completed(futs):
                sw, line = futs[fut]
                try:
                    ok = fut.result()
                except Exception:
                    logger.exception("intf pair %s (F%s) raised", line, sw)
                    ok = False
                if not ok:
                    logger.error("intf pair %s (F%s) produced no output -- see intf_*.log",
                                 line, sw)
                results.append(ok)

        ok = bool(results) and all(results)
        self._set_stage("intf", _SUCCEEDED if ok else _FAILED)
        return ok

    def _merge_prepare(self) -> tuple[Path, list[str], list[str], Path] | None:
        """Everything the merge stage needs before any pair is merged:
        the cross-subswath common-pair set, intflist, a staged dem.grd, and
        create_merge_input.csh's output.

        Returns (merge_dir, common_pair_ids, merge_input_lines, config_path)
        or None on failure. Idempotent and cheap (no interferogram work), so
        both the mergeprep seed job and every per-pair merge child job call
        it -- each child is a separate process that has to rediscover this
        state for itself.
        """
        env = self._subprocess_env()
        swaths = self._subswath_list()
        merge = self._paths.merge_dir
        merge.mkdir(parents=True, exist_ok=True)

        # pair-dir Julian ids present in EVERY subswath's intf_all (a pair
        # merges only if all its subswaths produced output). SC_clock_start is
        # per-date, so the id is the same across F<N>.
        per_sw = []
        for n in swaths:
            ia = self._paths.swath_intf_all_dir(n)
            per_sw.append({d.name for d in ia.iterdir()
                           if d.is_dir() and re.match(r"^\d{7}_\d{7}$", d.name)}
                          if ia.is_dir() else set())
        common = sorted(set.intersection(*per_sw)) if per_sw else []
        missing = sorted(set.union(*per_sw) - set(common)) if per_sw else []
        if missing:
            logger.warning("merge: %d pair(s) not present in every subswath, "
                           "skipping them: %s", len(missing), missing)
        if not common:
            logger.error("merge: no pair is present in all subswaths; nothing to merge.")
            return None

        (merge / "intflist").write_text("\n".join(common) + "\n")

        # merge_batch.csh hard-requires dem.grd in its own working directory
        # ("dem.grd is required ..." then exit 1, before doing anything else)
        # and later does `ln -s ../dem.grd .` inside each per-pair subdir it
        # creates, so the file has to sit at merge/dem.grd specifically.
        # _stage_stack() only ever puts the DEM in each subswath's own
        # topo/ dir, so without this the whole merge stage failed after
        # every align/topo/intf pair had already succeeded -- 3 seconds in,
        # having produced 0/27 merged pairs. It's the same DEM for every
        # subswath (one download, staged per swath), so any swath's copy
        # will do; symlinked rather than copied, matching what GMTSAR's own
        # usage text asks for ("Also make sure the dem.grd is linked").
        merge_dem = merge / "dem.grd"
        if not merge_dem.exists():
            src_dem = self._paths.swath_topo_dir(swaths[0]) / "dem.grd"
            if not src_dem.exists():
                logger.error("merge: %s not found -- cannot stage dem.grd, which "
                             "merge_batch requires.", src_dem)
                return None
            merge_dem.symlink_to(os.path.relpath(src_dem, merge))
        # create_merge_input.csh <intflist> <path-to-F dirs> <mode 0=all 3>
        # is still a python stub upstream, so use the csh. Called with ".."
        # (path relative to merge/) because merge_batch prefixes "../".
        mode = "0" if swaths == [1, 2, 3] else ("1" if swaths == [1, 2] else "2")
        mi = subprocess.run(
            [self._gmtsar_script("create_merge_input.csh"), "intflist", "..", mode],
            cwd=str(merge), capture_output=True, text=True, env=env)
        if mi.returncode != 0 or not mi.stdout.strip():
            logger.error("merge: create_merge_input failed (rc=%s): %s",
                         mi.returncode, (mi.stderr or mi.stdout).strip()[:500])
            return None
        (merge / "merge_input").write_text(mi.stdout)
        lines = [l for l in mi.stdout.splitlines() if l.strip()]
        cfg = self._merge_config(self._paths.swath_batch_config(swaths[0]), merge)
        return merge, common, lines, cfg

    def _merged_product(self) -> str:
        """The per-pair file whose existence means that pair MERGED OK.

        phasefilt.grd, not unwrap.grd: merging and unwrapping are separate
        stages now (the merge stage runs with threshold_snaphu = 0, see
        _merge_config), so a merged pair has no unwrap.grd until the unwrap
        stage has run over it. Checking for unwrap.grd here would make every
        freshly merged pair look unmerged and be redone forever. The unwrap
        stage does its own unwrap.grd check.
        """
        return "phasefilt.grd"

    def _merge_read(self) -> tuple[Path, list[str], list[str]] | None:
        """(merge_dir, merge_input_lines, config) read from what mergeprep
        already wrote -- no writes, nothing regenerated.

        The read-only counterpart to _merge_prepare(), for the per-pair merge
        child jobs: they all share one merge/ directory and run at the same
        time, so any of them writing merge_input there corrupts it for the
        rest (see _run_merge_unit)."""
        merge = self._paths.merge_dir
        merge_input = merge / "merge_input"
        if not merge_input.exists():
            logger.error("merge: %s not found -- the mergeprep stage must run "
                         "before any per-pair merge job.", merge_input)
            return None
        lines = [l for l in merge_input.read_text().splitlines() if l.strip()]
        if not lines:
            logger.error("merge: %s is empty.", merge_input)
            return None
        cfg = self._merge_config(
            self._paths.swath_batch_config(self._subswath_list()[0]), merge)
        return merge, lines, cfg

    def _run_mergeprep_unit(self) -> bool:
        """Merge the *first* pair alone, via stock merge_batch.

        merge_batch.csh computes the shared cross-pair geometry -- trans.dat
        (1.7 GB here), raln.grd, ralt.grd, landmask_ra.grd -- inside whichever
        pair happens to run first, then promotes it to merge/ and symlinks it
        into every later pair (`if (! -f ../trans.dat) mv trans.dat ../`).
        That's the same shared-precompute-under-concurrency hazard as
        intf_tops.csh's Stage 1 (see this module's docstring and
        _run_topo_unit): fanning pairs out without it, every pair would
        recompute its own 1.7 GB trans.dat and race on the promotion.

        Running exactly one pair first, with the stock csh, produces that
        shared state once -- and the first pair is the only one merge_batch
        *can* process in isolation, because it builds its supermaster
        reference (tmpm.filelist) from line 1 of the input file while taking
        each pair's own reference from the current line. For pair 0 those
        coincide; for any other pair they don't, which is why the rest go
        through _merge_one_pair() instead.
        """
        prep = self._merge_prepare()
        if prep is None:
            return False
        merge, common, lines, cfg = prep
        seed_pair = common[0]
        product = self._merged_product()
        if (merge / seed_pair / product).exists():
            logger.info("mergeprep: %s already merged, skipping.", seed_pair)
            return True

        (merge / "merge_input_seed").write_text(lines[0] + "\n")
        log = merge / "stack_mergeprep.log"
        with open(log, "w") as lf:
            lf.write(lines[0] + "\n")
            proc = subprocess.run(
                ["merge_batch", "merge_input_seed", str(cfg.resolve()),
                 str(self.config.det_stitch)],
                cwd=str(merge), stdout=lf, stderr=subprocess.STDOUT, env=self._subprocess_env())
        ok = proc.returncode == 0 and (merge / seed_pair / product).exists()
        if not ok:
            logger.error("mergeprep: seed pair %s failed (rc=%s) -- see %s",
                         seed_pair, proc.returncode, log)
        return ok

    def _merge_one_pair(self, merge: Path, lines: list[str], index: int,
                        cfg: Path) -> bool:
        """Merge one pair's subswaths, reusing the shared geometry mergeprep built.

        A faithful port of merge_batch.csh's per-pair loop body. It has to be
        a port rather than a call, because merge_batch derives its supermaster
        PRM from line 1 of its input file but each pair's own reference from
        the line being processed -- with a single-line input those collapse
        into the same value, so every pair after the first would silently be
        merged against *its own* reference instead of the stack's supermaster,
        giving each merged pair a different radar-grid origin. Passing a
        two-line file instead (supermaster line + target line) makes
        merge_batch reprocess the supermaster pair in every child job, racing
        on that pair's shared output directory.

        Mirrors the csh exactly, including its quirk that det_stitch is
        enabled whenever three arguments are passed regardless of the third
        argument's value -- keeping output identical to the serial path.
        """
        env = self._subprocess_env()
        line = lines[index]
        pair = self._pair_id_from_merge_line(line)
        pair_dir = merge / pair
        pair_dir.mkdir(exist_ok=True)

        def _grep_field(path: Path | str, key: str, last: bool = False) -> str | None:
            try:
                txt = Path(path).read_text().splitlines()
            except OSError:
                return None
            hits = [l for l in txt if l.strip().startswith(key)]
            if not hits:
                return None
            parts = (hits[-1] if last else hits[0]).split()
            return parts[2] if len(parts) >= 3 else None

        # tmpm.filelist equivalent: the supermaster PRM per subswath, always
        # taken from line 0 (paths are relative to merge/<pair>/, hence "../").
        mm_list = []
        for entry in lines[0].split(","):
            f = ("../" + entry.strip()).split(":")
            mm_list.append(f[0] + f[1])

        filelist: list[str] = []
        for j, entry in enumerate(line.split(",")):
            f = ("../" + entry.strip()).split(":")
            pth, f1, f2 = f[0], f[1], f[2]
            supermaster = pair_dir / "supermaster.PRM"
            shutil.copyfile(pair_dir / mm_list[j], supermaster)

            rshift = _grep_field(pair_dir / (pth + f1), "rshift", last=True)
            if rshift is not None:
                subprocess.run(["update_PRM", "supermaster.PRM", "rshift", rshift],
                               cwd=str(pair_dir), capture_output=True, env=env)
            fs1 = _grep_field(supermaster, "first_sample")
            fs2 = _grep_field(pair_dir / (pth + f1), "first_sample")
            if fs1 is not None and fs2 is not None and int(fs2) > int(fs1):
                subprocess.run(["update_PRM", "supermaster.PRM", "first_sample", fs2],
                               cwd=str(pair_dir), capture_output=True, env=env)
            shutil.copyfile(supermaster, pair_dir / pth / "supermaster.PRM")
            filelist.append(f"{pth}:supermaster.PRM:{f2}")

        (pair_dir / "tmp.filelist").write_text("\n".join(filelist) + "\n")

        # Reuse the shared geometry instead of recomputing it (the whole point
        # of the mergeprep stage). dem.grd is required; the rest are only
        # present once mergeprep has produced them.
        for shared in ("dem.grd", "trans.dat", "raln.grd", "ralt.grd", "landmask_ra.grd"):
            src, dst = merge / shared, pair_dir / shared
            if src.exists() and not dst.exists():
                dst.symlink_to(os.path.join("..", shared))

        log = merge / f"stack_merge_{pair}.log"
        with open(log, "w") as lf:
            proc = subprocess.run(
                [self._gmtsar_script("merge_unwrap_geocode_tops.csh"), "tmp.filelist", str(cfg.resolve()), "1"],
                cwd=str(pair_dir), stdout=lf, stderr=subprocess.STDOUT, env=env)

        # Success is judged by the product, not the exit code:
        # merge_unwrap_geocode_tops.csh ends with an `rm` over a glob that
        # frequently matches nothing, and csh exits 1 on "rm: No match." even
        # though the merge itself completed ("GEOCODE END" is printed just
        # before). merge_batch.csh never notices because a csh foreach body
        # doesn't propagate it; running the script directly does. Verified
        # against a serially-merged reference pair: rc=1, yet all 8 products
        # (unwrap/corr/phasefilt/los_ll/unwrap_ll/unwrap_mask/mask/conncomp)
        # were byte-for-byte identical in grdinfo terms.
        ok = (pair_dir / self._merged_product()).exists()
        if not ok:
            logger.error("merge: pair %s failed (rc=%s) -- see %s",
                         pair, proc.returncode, log)
        elif proc.returncode != 0:
            logger.debug("merge: pair %s produced its output but exited rc=%s "
                         "(see this method's comment on csh's trailing rm).",
                         pair, proc.returncode)
        return ok

    @staticmethod
    def _pair_id_from_merge_line(line: str) -> str:
        """<pair-id> from a merge_input line -- the same second-to-last path
        component merge_batch.csh uses for the output directory name."""
        first = line.split(",")[0].split(":")[0].rstrip("/")
        return first.rsplit("/", 1)[-1]


    def _merge_config(self, src_cfg: Path, merge: Path) -> Path:
        """batch_tops.config copy for the MERGE stage, with unwrapping and
        geocoding switched off (threshold_snaphu = threshold_geocode = 0).

        Merging and unwrapping are separate stages, exactly as GMTSAR's own
        recipe orders them (section 7 merges, section 8 unwraps afterwards).
        Splitting them is what makes a stacked-coherence mask possible at all:
        that mask is built from the merged corr.grd of EVERY pair, so it can
        only exist once merging has finished, and it has to exist before snaphu
        starts or it cannot reduce snaphu's workload. Geocoding is deferred too
        -- there is nothing to geocode until unwrap.grd exists.
        """
        text = src_cfg.read_text()
        text = re.sub(r"(?m)^threshold_snaphu\s*=.*$", "threshold_snaphu = 0", text)
        text = re.sub(r"(?m)^threshold_geocode\s*=.*$", "threshold_geocode = 0", text)
        out = merge / "batch_tops_merge.config"
        out.write_text(text)
        return out

    def _unwrap_config(self) -> Path:
        """The real thresholds, for the unwrap stage (merge zeroes them)."""
        return self._paths.swath_batch_config(self._subswath_list()[0])

    def _run_cohmask_unit(self) -> bool:
        """Build mask_def.grd: mean coherence over all merged pairs, thresholded.

        Runs once, between merge and unwrap. snaphu.csh/snaphu_interp.csh pick
        mask_def.grd up automatically if it is present in the pair directory and
        multiply it into the correlation grid BEFORE writing snaphu's input, so
        masked ground is removed from the solve rather than merely discarded
        afterwards. config.coherence_mask_threshold = 0 skips this entirely.
        """
        thr = float(getattr(self.config, "coherence_mask_threshold", 0) or 0)
        merge = self._paths.merge_dir
        if thr <= 0:
            logger.info("coherence_mask_threshold=0 -- skipping the stacked-coherence mask.")
            return True
        env = self._subprocess_env()
        corrs = sorted(merge.glob("*/corr.grd"))
        if not corrs:
            logger.error("cohmask: no merged corr.grd found under %s", merge)
            return False
        (merge / "corr.grd_list").write_text(
            "\n".join(str(c.relative_to(merge)) for c in corrs) + "\n")
        log = merge / "stack_cohmask.log"
        with open(log, "w") as lf:
            r = subprocess.run([self._gmtsar_script("stack.csh"), "corr.grd_list", "1",
                                "corr_stack.grd", "corr_std.grd"],
                               cwd=str(merge), stdout=lf, stderr=subprocess.STDOUT, env=env)
            if r.returncode != 0 or not (merge / "corr_stack.grd").exists():
                logger.error("cohmask: stack.csh failed -- see %s", log)
                return False
            r = subprocess.run(["gmt", "grdmath", "corr_stack.grd", str(thr),
                                "GE", "0", "NAN", "=", "mask_def.grd"],
                               cwd=str(merge), stdout=lf, stderr=subprocess.STDOUT, env=env)
        ok = r.returncode == 0 and (merge / "mask_def.grd").exists()
        if ok:
            logger.info("cohmask: mask_def.grd from %d pair(s), coherence >= %s",
                        len(corrs), thr)
        else:
            logger.error("cohmask: grdmath failed -- see %s", log)
        return ok

    def _run_unwrap_unit(self, index: int) -> bool:
        """Unwrap + geocode one merged pair (the recipe's section 8).

        Mirrors what intf_tops.csh does per subswath, and what
        merge_unwrap_geocode_tops.csh used to do inline before the split:
        snaphu_interp.csh / snaphu.csh (chosen by near_interp), then
        geocode.csh, which produces unwrap_mask/los and every *_ll.grd.
        mask_def.grd is linked in first so snaphu never sees the incoherent
        ground.
        """
        read = self._merge_read()
        if read is None:
            return False
        merge, lines, _ = read
        if index >= len(lines):
            logger.info("unwrap: no pair at index %d (%d merged) -- nothing to do.",
                        index, len(lines))
            return True
        pair = self._pair_id_from_merge_line(lines[index])
        pair_dir = merge / pair
        if not pair_dir.is_dir():
            logger.error("unwrap: %s not merged -- run the merge stage first.", pair_dir)
            return False
        if (pair_dir / "unwrap.grd").exists():
            logger.info("unwrap: %s already unwrapped, skipping.", pair)
            return True

        env = self._subprocess_env()
        cfg = self._unwrap_config()
        thr = str(self.config.threshold_snaphu)
        defomax = str(self.config.defomax)
        script = self._gmtsar_script(
            "snaphu_interp.csh" if int(self.config.near_interp) == 1 else "snaphu.csh")

        for shared in ("mask_def.grd", "landmask_ra.grd"):
            src, dst = merge / shared, pair_dir / shared
            if src.exists() and not dst.exists():
                dst.symlink_to(os.path.join("..", shared))

        log = merge / f"stack_unwrap_{pair}.log"
        with open(log, "w") as lf:
            cmd = [script, thr, defomax]
            rc = str(self.config.region_cut).strip()
            if rc and rc not in ("-999", "None"):
                cmd.append(rc)
            subprocess.run(cmd, cwd=str(pair_dir), stdout=lf,
                           stderr=subprocess.STDOUT, env=env)
            if float(self.config.threshold_geocode) != 0:
                subprocess.run([self._gmtsar_script("geocode.csh"), str(self.config.threshold_geocode)],
                               cwd=str(pair_dir), stdout=lf,
                               stderr=subprocess.STDOUT, env=env)

        # judged by the product, not the exit code -- these csh scripts end on
        # an `rm` over a glob that often matches nothing, and csh exits 1 on
        # "rm: No match." even when the work completed (see _merge_one_pair).
        ok = (pair_dir / "unwrap.grd").exists()
        if not ok:
            logger.error("unwrap: pair %s produced no unwrap.grd -- see %s", pair, log)
        return ok

    def _run_merge_unit(self, index: int | None = None) -> bool:
        """Merge one pair (``index``), or every pair serially when index is None.

        index is 0-based over the merge_input lines. Index 0 is the seed pair
        that the mergeprep stage already merged, so a child job for it is a
        no-op; HPC fan-out only ever dispatches 1..N-1 (see _stage_commands).
        """
        product = self._merged_product()

        if index is not None:
            # Read-only: a per-pair child MUST NOT call _merge_prepare(). That
            # runs create_merge_input.csh inside the shared merge/ directory
            # and rewrites merge_input (plus the csh's own scratch files)
            # there, so with the fan-out every child was regenerating the same
            # file at the same time. Their writes interleaved and left a
            # merge_input whose first line spliced fields from different pairs
            # and subswaths (":S1_20210108_ALL_F2.PRM:S1_20210414_ALL_F3.PRM,
            # ../F1/intf_all/2021007_2021031/:..."), which then resolved the
            # supermaster PRM to a nonexistent path and failed 9 of 26 jobs.
            # mergeprep writes these files once; children only read them.
            read = self._merge_read()
            if read is None:
                return False
            merge, lines, cfg = read
            if index >= len(lines):
                # Fan-out is sized from the planned pair list, so a pair that
                # didn't survive intf in every subswath simply has no line
                # here. Its failure is the intf stage's to report; merging
                # nothing is the correct outcome for this child.
                logger.info("merge: no pair at index %d (only %d pair(s) merged "
                            "across all subswaths) -- nothing to do.", index, len(lines))
                return True
            if index < 0:
                raise ValueError(f"stage 'merge' needs a valid --index (0..{len(lines) - 1})")
            pair = self._pair_id_from_merge_line(lines[index])
            if (merge / pair / product).exists():
                logger.info("merge: %s already merged, skipping.", pair)
                return True
            return self._merge_one_pair(merge, lines, index, cfg)

        prep = self._merge_prepare()
        if prep is None:
            return False
        merge, common, lines, cfg = prep

        # Serial path (local, non-HPC): stock merge_batch over every pair.
        log = merge / "stack_merge.log"
        with open(log, "w") as lf:
            lf.write("\n".join(lines) + "\n")
            proc = subprocess.run(
                ["merge_batch", "merge_input", str(cfg.resolve()),
                 str(self.config.det_stitch)],
                cwd=str(merge), stdout=lf, stderr=subprocess.STDOUT,
                env=self._subprocess_env())
        merged = [d for d in merge.iterdir()
                  if d.is_dir() and re.match(r"^\d{7}_\d{7}$", d.name)
                  and (d / product).exists()]
        ok = proc.returncode == 0 and len(merged) == len(common)
        if not ok:
            logger.error("merge: produced %d/%d merged pairs (rc=%s) -- see %s",
                         len(merged), len(common), proc.returncode, log)
        return ok

    def _run_merge(self) -> None:
        """Local (non-HPC) merge: seed pair alone, then every remaining pair
        concurrently -- the same mergeprep->merge shape the HPC path uses, and
        the same reason (the seed produces the shared trans.dat the rest just
        symlink; see _run_mergeprep_unit()). Each stage's status is set here
        rather than inside the unit methods, so an HPC child job can call just
        the work with its sbatch manager tracking status instead."""
        if self.config.skip_existing and _read_status(self._stack_status_dir("mergeprep")) == _SUCCEEDED:
            logger.info("mergeprep already succeeded, skipping.")
        else:
            self._set_stage("mergeprep", _RUNNING)
            ok = self._run_mergeprep_unit()
            self._set_stage("mergeprep", _SUCCEEDED if ok else _FAILED)
            if not ok:
                logger.error("mergeprep failed -- see stack_mergeprep.log in %s",
                             self._paths.merge_dir)
                return

        if self.config.skip_existing and _read_status(self._stack_status_dir("merge")) == _SUCCEEDED:
            logger.info("merge already succeeded, skipping.")
            return
        self._set_stage("merge", _RUNNING)
        n_pairs = len(self._merge_input_lines())
        results: list[bool] = []
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as pool:
            futs = {pool.submit(self._run_merge_unit, i): i for i in range(1, n_pairs)}
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    ok = fut.result()
                except Exception:
                    logger.exception("merge pair index %d raised", i)
                    ok = False
                results.append(ok)
        ok = all(results)  # vacuously True when the seed was the only pair
        self._set_stage("merge", _SUCCEEDED if ok else _FAILED)
        if not ok:
            return

        # cohmask -> unwrap, the same order the HPC chain uses: the mask needs
        # every merged corr.grd, and must exist before snaphu starts to be able
        # to shrink its workload at all.
        if self.config.skip_existing and _read_status(self._stack_status_dir("cohmask")) == _SUCCEEDED:
            logger.info("cohmask already succeeded, skipping.")
        else:
            self._set_stage("cohmask", _RUNNING)
            ok = self._run_cohmask_unit()
            self._set_stage("cohmask", _SUCCEEDED if ok else _FAILED)
            if not ok:
                return

        if self.config.skip_existing and _read_status(self._stack_status_dir("unwrap")) == _SUCCEEDED:
            logger.info("unwrap already succeeded, skipping.")
            return
        self._set_stage("unwrap", _RUNNING)
        n_pairs = self._merge_pair_count()
        results = []
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as pool:
            futs = {pool.submit(self._run_unwrap_unit, i): i for i in range(n_pairs)}
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    results.append(fut.result())
                except Exception:
                    logger.exception("unwrap pair index %d raised", i)
                    results.append(False)
        self._set_stage("unwrap", _SUCCEEDED if all(results) else _FAILED)

    def run_stage_unit(self, stage: str, index: int | None = None,
                       subswath: int | None = None) -> bool:
        """Dispatch to the right per-unit callable for one HPC child job's
        unit of work. Each child sbatch job invokes
        ``insarhub processor -N GMTSAR_S1 -w <workdir> --config
        run-stage-unit --stage <stage> [--subswath N] [--index N]`` (see
        cli/main.py's run-stage-unit action), which reconstructs this
        processor from saved config/pairs and calls this method, exiting
        0/1 on the result -- GMTSAR_S1 has no flat shell-command-list
        generator the way ISCE2_S1's stackSentinel.py run_NN_* files do, so
        each child re-enters Python to run one already-implemented unit of
        work instead of re-deriving it in bash (see slurm_manager.py's
        module docstring / the HPC-mode docs for the full rationale).

        --subswath selects which subswath's align/topo/intf unit to run --
        align/topo/intf are each ONE manager whose sliding window fans out
        across every subswath (align_F<N>/topo_F<N> are fully independent
        of each other, and for intf every pair too) as concurrent child
        jobs, rather than one manager per subswath (see _stage_commands()).
        Defaults to this processor's only configured subswath when omitted
        -- single-subswath callers never need to pass it."""
        if stage == "pair":
            # p2p mode: one whole pair per child job. Pairs are completely
            # independent -- multi-subswath gives each its own case dir, and
            # single-subswath output is pair-namespaced as intf/<julian_pair>/
            # -- so there is nothing to serialise between them.
            if index is None or not (0 <= index < len(self.pairs)):
                raise ValueError(
                    f"stage 'pair' needs a valid --index (0..{len(self.pairs) - 1})")
            pair = self.pairs[index]
            ok = self._run_one_pair(pair)
            _write_status(self._status_dir(pair), _SUCCEEDED if ok else _FAILED)
            return ok
        if stage == "mergeprep":
            return self._run_mergeprep_unit()
        if stage == "merge":
            return self._run_merge_unit(index)
        if stage == "cohmask":
            return self._run_cohmask_unit()
        if stage == "unwrap":
            if index is None:
                raise ValueError("stage 'unwrap' needs --index")
            return self._run_unwrap_unit(index)
        sw = subswath if subswath is not None else self._subswath_list()[0]
        if stage == "align":
            return self._run_align_unit(sw)
        if stage == "esdnet":
            return self._run_esdnet_unit(sw)
        if stage == "topo":
            return self._run_topo_unit(sw)
        if stage == "intf":
            work, _raw, _topo = self._swath_paths(sw)
            pair_lines = [l for l in (work / "intf.in").read_text().splitlines() if l.strip()]
            if index is None or not (0 <= index < len(pair_lines)):
                raise ValueError(
                    f"stage 'intf' (subswath {sw}) needs a valid --index "
                    f"(0..{len(pair_lines) - 1})"
                )
            return self._run_intf_unit(sw, pair_lines[index])
        raise ValueError(f"Unknown GMTSAR stack stage: {stage!r}")

    def _subprocess_env(self) -> dict:
        """Build the environment GMTSAR subprocess calls actually need.

        REAL BUG FOUND in end-to-end testing (2026-07-21): GMTSAR's own
        Python stages (e.g. dem2topo_ra) shell out to the standalone `gmt`
        binary, which is NOT provided by GMTSAR's own bin/ directory --
        it comes from the conda environment GMTSAR was installed into
        (conda-forge's gmt package). InSARHub runs in its OWN separate
        conda environment (different numpy/GDAL stack, deliberately --
        see this module's docstring), which does not have `gmt` on PATH
        at all. Confirmed directly: `which gmt` inside InSARHub's env
        returns nothing; dem2topo_ra then fails in ~1s (not the tens of
        seconds a real DEM interpolation takes) because the `gmt`
        subprocess it shells out to doesn't exist.

        So this can't rely on inheriting the caller's PATH -- it must
        build an explicit PATH prepending both gmtsar_root/bin (GMTSAR's
        own scripts) and gmtsar_env_bin (the conda env providing `gmt`,
        numba, scipy, etc.), regardless of what environment the InSARHub
        process itself happens to be running under.
        """
        import os
        env = os.environ.copy()
        prepend = []
        cfg = self.config
        # gmtsar_root / gmtsar_env_bin are already resolved in __init__ via
        # _find_gmtsar_root / _find_gmtsar_env_bin (config -> $GMTSAR -> a
        # GMTSAR script on PATH -> a scan of the usual install locations), so
        # they are concrete paths here, not the user's possibly-blank input.
        env_bin = cfg.gmtsar_env_bin
        if env_bin:
            prepend.append(str(env_bin))
        root = cfg.gmtsar_root
        if root:
            env["GMTSAR"] = str(root)
            prepend.append(str(Path(root) / "bin"))
        if prepend:
            env["PATH"] = ":".join(prepend) + ":" + env.get("PATH", "")
        return env

    def _run_one_pair(self, pair: tuple) -> bool:
        key = _pair_key(pair)
        with self._lock:
            self.jobs[key]["status"] = _RUNNING
        run_dir = self.pair_case_dir(pair)
        log_path = run_dir / "p2p.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = self._build_cmd(pair)

        before = set()
        intf_dir = self._paths.intf_dir
        if not self._multiswath and intf_dir.is_dir():
            before = {p.name for p in intf_dir.iterdir()}

        with open(log_path, "w") as log_f:
            proc = subprocess.run(
                cmd, cwd=str(run_dir), stdout=log_f, stderr=subprocess.STDOUT,
                env=self._subprocess_env(),
            )

        if not self._multiswath and intf_dir.is_dir():
            # Real bug found via MintPy integration testing (2026-07-21):
            # p2p_processing's real output directory is named by GMTSAR's
            # own Julian-date pair (e.g. intf/2019184_2019196/, derived
            # from each SLC's SC_clock_start), NOT our ref_stem_sec_stem
            # naming -- that assumption only "worked" because our own
            # status marker mkdir'd its own (unpopulated) directory,
            # masking the mismatch. Find the real directory GMTSAR just
            # created so _status_dir() points MintPy at real output.
            after = {p.name for p in intf_dir.iterdir()}
            new_dirs = [d for d in after - before if re.match(r"^\d{7}_\d{7}$", d)]
            if new_dirs:
                self._real_intf_dirs[key] = new_dirs[0]

        return proc.returncode == 0

    def _build_cmd(self, pair: tuple) -> list[str]:
        cfg = self.config
        if not self._multiswath:
            ref_stem, sec_stem = self._stems[_pair_key(pair)]
            return ["p2p_processing", cfg.sat, ref_stem, sec_stem, "config.py"]
        ref_safe, ref_eof, sec_safe, sec_eof = pair
        return [
            "p2p_S1_TOPS_Frame", ref_safe, ref_eof, sec_safe, sec_eof,
            "config.py", cfg.polarization, "1" if cfg.parallel else "0",
        ]

    def refresh(self, ls: str | bool | None = None) -> dict:
        """Read file-based status for all stages and print a coloured table.

        By default only the one-line-per-stage summary is printed. Pass
        ls=True to also show per-command (cmd_XXXX) detail for every
        multi-command stage (align/topo: one line per subswath; intf: one
        line per subswath x pair combination), or ls="intf" (also accepts
        "topo", "align", "merge") to show it for just that one stage --
        mirrors ISCE2_S1.refresh()'s --ls.
        """
        if self.config.stack_mode:
            ls_stage: str | None = None
            if isinstance(ls, str):
                if ls not in self._stack_stages:
                    raise ValueError(
                        f"Unknown stage for --ls: {ls!r}. Valid stages: {list(self._stack_stages)}"
                    )
                ls_stage = ls
            show_all_cmds = ls is True
            detail_stages = (list(self._stack_stages) if show_all_cmds
                             else ([ls_stage] if ls_stage else []))

            hpc = bool(self.config.hpc_mode) or self._stage_hpc_dir(self._stack_stages[0]).exists()
            active_slurm: dict[str, str] = {}
            sacct_states: dict[str, str] = {}
            job_ids_by_stage: dict[str, list[str]] = {}
            cmd_job_ids_by_stage: dict[str, dict[int, str]] = {}
            if hpc:
                from insarhub.utils.slurm_manager import (
                    slurm_active_jobs, slurm_job_states, SLURM_DEAD_STATES,
                )
                # Job ids are always disk-derived (never trusted from a
                # reloaded self.jobs) -- _rediscover_state() rebuilds self.jobs
                # from status markers alone on every fresh construction, so a
                # separate `refresh`/`cancel` CLI invocation never has them
                # otherwise (see _submit_stack_hpc()'s comment on why even the
                # first stage's id gets written to its own job_id_file).
                all_ids: list[str] = []
                for stage in self._stack_stages:
                    _, jf = self._manager_paths_for_stage(stage)
                    if jf.exists():
                        jid_txt = jf.read_text().strip()
                        if jid_txt.isdigit():
                            job_ids_by_stage[stage] = [jid_txt]
                            all_ids.append(jid_txt)
                # For stages being shown in detail, also pull each child
                # command's job id (parsed from the manager's own "cmd_XXXX
                # -> job YYYY" log lines) so per-command status can show
                # RUNNING/queued, not just done/fail/unknown.
                for stage in detail_stages:
                    hpc_dir = self._stage_hpc_dir(stage)
                    cmd_ids: dict[int, str] = {}
                    # A stage's .hpc dir accumulates one manager_<jobid>.out
                    # per retry -- sort oldest-to-newest by job id (SLURM job
                    # ids are monotonically increasing) so the current retry's
                    # mappings always win the per-index overwrite below,
                    # never an earlier retry's stale one.
                    out_files = sorted(
                        (hpc_dir.glob("manager_*.out") if hpc_dir.exists() else []),
                        key=lambda p: int(m.group(1)) if (m := re.search(r"manager_(\d+)\.out", p.name)) else -1,
                    )
                    for out_file in out_files:
                        for m in re.finditer(r"cmd_(\d+)\s*->\s*job\s*(\d+)", out_file.read_text()):
                            cmd_ids[int(m.group(1))] = m.group(2)
                    if cmd_ids:
                        cmd_job_ids_by_stage[stage] = cmd_ids
                        all_ids.extend(cmd_ids.values())
                if all_ids:
                    active_slurm = slurm_active_jobs()
                    sacct_states = slurm_job_states(all_ids)

            for stage in self._stack_stages:
                status = _read_status(self._stack_status_dir(stage))
                if hpc and status in (_PENDING, _RUNNING):
                    job_ids = job_ids_by_stage.get(stage, [])
                    if job_ids and not any(jid in active_slurm for jid in job_ids):
                        dead = [jid for jid in job_ids
                                if sacct_states.get(jid) in SLURM_DEAD_STATES]
                        if dead:
                            logger.error(
                                "%s: SLURM job %s ended (%s) without writing status "
                                "-- marking FAILED.", stage, dead[0], sacct_states[dead[0]])
                            _write_status(self._stack_status_dir(stage), _FAILED)
                            status = _FAILED
                if stage in self.jobs:
                    self.jobs[stage]["status"] = status
                    if stage in job_ids_by_stage:
                        self.jobs[stage]["slurm_job_ids"] = job_ids_by_stage[stage]
            self._print_table(detail_stages=detail_stages, active_slurm=active_slurm,
                              sacct_states=sacct_states, cmd_job_ids_by_stage=cmd_job_ids_by_stage)
            return self.jobs
        # p2p: one row per pair. Live SLURM state OVERRIDES the status file --
        # a file is written once, at the end, so it cannot say "in progress",
        # and a FAILED left by a cancel is stale the moment the pair is
        # resubmitted (which is exactly how two RUNNING pairs reported FAILED).
        from colorama import Fore, Style

        live_names = self._live_job_names() if (self.workdir / "hpc" / "p2p").is_dir() else {}
        # Map pair -> its own child job. A sliding window keeps only
        # max_concurrent children alive at a time, so "some job is live" is NOT
        # evidence that a given pair is running: reporting every pair RUNNING
        # because the manager exists would claim 27 concurrent jobs when 8 are.
        # Children are named g_p2p_<idx>, indexing the submitted command list.
        live_idx = set()
        mgr_live = False
        for n in live_names.values():
            if n == "g_p2p_mgr":
                mgr_live = True
            elif n.startswith("g_p2p_"):
                tail = n.rsplit("_", 1)[-1]
                if tail.isdigit():
                    live_idx.add(int(tail))
        counts: dict[str, int] = {}
        for i, pair in enumerate(self.pairs):
            key = _pair_key(pair)
            status = _read_status(self._status_dir(pair))
            if i in live_idx:
                status = _RUNNING
            elif mgr_live and status == _RUNNING:
                # Marked RUNNING on submit but no child in the queue yet: still
                # queued behind the window, not actually executing.
                status = _PENDING
            if key in self.jobs:
                self.jobs[key]["status"] = status
            counts[status] = counts.get(status, 0) + 1

        colour = {_SUCCEEDED: Fore.GREEN, _FAILED: Fore.RED,
                  _RUNNING: Fore.CYAN, _PENDING: Fore.YELLOW}
        print(f"\n{Style.BRIGHT}  {'PAIR':<22} STATUS{Style.RESET_ALL}")
        print("  " + "-" * 44)
        for pair in self.pairs:
            st = self.jobs.get(_pair_key(pair), {}).get(
                "status", _read_status(self._status_dir(pair)))
            # Full .SAFE names are ~68 chars each and unreadable side by side;
            # a pair is identified by its two dates.
            print(f"  {_pair_dates(pair):<22} "
                  f"{colour.get(st, '')}{st}{Style.RESET_ALL}")
        print("  " + "-" * 44)
        print("  " + "  ".join(f"{colour.get(k, '')}{k}={v}{Style.RESET_ALL}"
                               for k, v in sorted(counts.items())))
        if live_idx or mgr_live:
            print(f"  {len(live_idx)} job(s) live in SLURM"
                  + ("  (+ manager)" if mgr_live else ""))
        return self.jobs
        return self.jobs

    def retry(self) -> dict:
        # See submit()'s comment: HPC mode's own retry path (_submit_stack_hpc)
        # stays on the host too, container wrapping happens per child job.
        if (self.config.container and not os.environ.get("INSARHUB_CONTAINER_CHILD")
                and not (self.config.stack_mode and self.config.hpc_mode)):
            self._reinvoke_via_container("retry")
            return self.jobs
        from colorama import Fore, Style

        if self.config.stack_mode:
            # Re-run from the first failed stage onward (align gates topo
            # gates intf), exactly as ISCE2_Base.retry() does. Stages *before*
            # the first failure keep their SUCCEEDED marker and are skipped by
            # _submit_stack_hpc()'s own pass 1 -- previously every stage was
            # reset to PENDING, which resubmitted already-finished managers
            # just to have them skip every command via .done markers.
            failed = [s for s in self._stack_stages
                      if _read_status(self._stack_status_dir(s)) == _FAILED]
            if not failed:
                print(f"{Fore.GREEN}No failed stages.{Style.RESET_ALL}")
                return self.jobs
            first_failed = failed[0]
            to_retry = list(self._stack_stages[self._stack_stages.index(first_failed):])
            for stage in to_retry:
                _write_status(self._stack_status_dir(stage), _PENDING)
            print(f"{Fore.YELLOW}Retrying {len(to_retry)} stage(s) "
                  f"from {first_failed}…{Style.RESET_ALL}")
            if self.config.hpc_mode:
                return self._submit_stack_hpc()
            self._run_local_or_sync(self._run_stack)
            return self.jobs
        failed = [p for p in self.pairs if _read_status(self._status_dir(p)) == _FAILED]
        if not failed:
            print(f"{Fore.GREEN}No failed pairs.{Style.RESET_ALL}")
            return self.jobs
        for pair in failed:
            _write_status(self._status_dir(pair), _PENDING)
        print(f"{Fore.YELLOW}Retrying {len(failed)} pair(s)…{Style.RESET_ALL}")
        self._run_local_or_sync(self._run_pairs, (failed,))
        return self.jobs

    def watch(self, poll_interval: float = 10.0) -> dict:
        while True:
            self.refresh()
            statuses = {j["status"] for j in self.jobs.values()}
            if statuses <= {_SUCCEEDED, _FAILED}:
                break
            time.sleep(poll_interval)
        return self.jobs

    def cancel(self) -> None:
        """Cancel a running submission.

        Three cases, checked in order:
        1. A containerized run (config.container was set at submit() time,
           see _reinvoke_via_container): a real host-side PID exists at
           case_dir/executor.pid -- SIGTERM its whole process group (kills
           the docker/apptainer subprocess and everything it started).
        2. HPC-mode stack_mode: scancel every stage's manager + children.
           Auto-detected the same way refresh() does (any stage's
           .hpc/<stage>/ dir existing on disk) -- no need to pass hpc_mode
           explicitly.
        3. Plain local (non-HPC, non-container) mode: nothing to cancel from
           a separate CLI invocation -- _run_stack()/_run_pairs() run as a
           daemon thread inside whichever process called submit(), which has
           no PID left to signal once that process has already returned.

        Reports via coloured print(), matching ISCE2_Base.cancel()'s output
        (same wording, same single "scancel: cancelled N job(s)." summary)
        rather than logger calls, so the two processors' cancel commands read
        identically.
        """
        from colorama import Fore, Style
        from insarhub.utils.slurm_manager import slurm_active_jobs

        pid_file = self.case_dir / "executor.pid"
        if pid_file.exists():
            import signal
            try:
                pid = int(pid_file.read_text().strip())
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                    print(f"{Fore.GREEN}Sent SIGTERM to executor (PID {pid})."
                          f"{Style.RESET_ALL}")
                except ProcessLookupError:
                    print(f"{Fore.YELLOW}Process already finished.{Style.RESET_ALL}")
                pid_file.unlink(missing_ok=True)
            except Exception as e:
                print(f"{Fore.RED}Cancel error: {e}{Style.RESET_ALL}")
            if self.config.stack_mode:
                for stage in self._stack_stages:
                    status = _read_status(self._stack_status_dir(stage))
                    if status in (_PENDING, _RUNNING):
                        _write_status(self._stack_status_dir(stage), _FAILED)
            else:
                for pair in self.pairs:
                    status = _read_status(self._status_dir(pair))
                    if status in (_PENDING, _RUNNING):
                        _write_status(self._status_dir(pair), _FAILED)
            return

        if not self.config.stack_mode:
            # p2p on SLURM: one manager fanning every pair out as its own child
            # job (see _submit_p2p_hpc). Detected from the hpc/p2p directory
            # rather than config.hpc_mode, so a cancel issued from a plain CLI
            # invocation -- which has no reason to repeat --hpc-mode -- still
            # finds them.
            p2p_dir = self.workdir / "hpc" / "p2p"
            if p2p_dir.is_dir():
                ids: list[str] = []
                sub = p2p_dir / "submitted_child_jobs.txt"
                if sub.exists():
                    ids += [l.strip() for l in sub.read_text().splitlines()
                            if l.strip().isdigit()]
                # The manager itself is only discoverable from the queue: it is
                # submitted by Python, not recorded by a chain trailer the way
                # stack_mode's stage managers are.
                for jid, name in self._live_job_names().items():
                    if name.startswith("g_p2p"):
                        ids.append(str(jid))
                ids = sorted(set(ids))
                if not ids:
                    print(f"{Fore.YELLOW}No p2p SLURM jobs on record to cancel."
                          f"{Style.RESET_ALL}")
                    return
                subprocess.run(["scancel", *ids], capture_output=True, text=True)
                for pair in self.pairs:
                    if _read_status(self._status_dir(pair)) in (_PENDING, _RUNNING):
                        _write_status(self._status_dir(pair), _FAILED)
                print(f"{Fore.GREEN}[{type(self).name}] cancelled "
                      f"{len(ids)} job(s): {' '.join(ids)}{Style.RESET_ALL}")
                return
            print(f"{Fore.YELLOW}cancel() found no p2p HPC jobs (no hpc/p2p "
                  f"directory) and no containerized run. A plain local p2p run "
                  f"has no separate process to signal once the submit() call's "
                  f"process has exited.{Style.RESET_ALL}")
            return
        hpc = bool(self.config.hpc_mode) or self._stage_hpc_dir(self._stack_stages[0]).exists()
        if not hpc:
            print(f"{Fore.YELLOW}No HPC jobs found -- local (non-HPC, "
                  f"non-container) stack_mode runs have no separate process to "
                  f"cancel once the original submit() call's process has exited."
                  f"{Style.RESET_ALL}")
            return

        def _manager_ids() -> list[str]:
            ids: list[str] = []
            for stage in self._stack_stages:
                _, jf = self._manager_paths_for_stage(stage)
                if jf.exists():
                    jid_txt = jf.read_text().strip()
                    if jid_txt.isdigit():
                        ids.append(jid_txt)
            return ids

        def _child_ids() -> list[str]:
            ids: list[str] = []
            for stage in self._stack_stages:
                child_file = self._stage_hpc_dir(stage) / "submitted_child_jobs.txt"
                if child_file.exists():
                    ids.extend(l.strip() for l in child_file.read_text().splitlines()
                               if l.strip() and l.strip() != "unknown")
            return ids

        def _scancel(ids: list[str]) -> int:
            """scancel *ids*, returning how many were actually still active.

            Prints nothing itself -- the two phases below share one combined
            summary, so the output matches ISCE2_Base.cancel()'s single
            "scancel: cancelled N job(s)." line rather than emitting one per
            phase.
            """
            if not ids:
                return 0
            active = slurm_active_jobs()
            live = sum(1 for j in ids if j in active)
            r = subprocess.run(["scancel"] + ids, capture_output=True, text=True)
            if r.returncode != 0:
                print(f"{Fore.RED}scancel error: {r.stderr.strip()}{Style.RESET_ALL}")
            return live

        # Two phases, managers first: a running manager keeps submitting new
        # child jobs as slots free up, so cancelling children first (or in
        # one combined call built from a single up-front read of
        # submitted_child_jobs.txt) leaves anything submitted between that
        # read and scancel landing alive as an orphan with its manager dead.
        # Killing the managers first stops new submissions at the source;
        # only then is the child list final enough to act on. Re-cancelling
        # an already-finished or already-cancelled id is a harmless no-op
        # (verified: scancel exits 0 for completed and unknown job ids
        # alike), so the overlap between the two phases costs nothing.
        managers = _manager_ids()
        children_before = _child_ids()
        if not managers and not children_before:
            print(f"{Fore.YELLOW}No SLURM job IDs found.{Style.RESET_ALL}")
            return
        n_cancelled = _scancel(managers)

        # Let the managers actually receive SIGTERM and stop submitting
        # before trusting the child list -- scancel returning only means the
        # request was accepted, not that the job has died yet.
        if managers:
            time.sleep(2)
        n_cancelled += _scancel(_child_ids())
        print(f"{Fore.GREEN}[{type(self).name}] cancelled {n_cancelled} job(s)."
              f"{Style.RESET_ALL}")

        for stage in self._stack_stages:
            status = _read_status(self._stack_status_dir(stage))
            if status in (_PENDING, _RUNNING):
                _write_status(self._stack_status_dir(stage), _FAILED)

    def save(self) -> None:
        jobs_path = self.case_dir / JOBS_FILE
        jobs_path.parent.mkdir(parents=True, exist_ok=True)
        jobs_path.write_text(json.dumps(self.jobs, indent=2))

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #

    def _job_meta(self, pair: tuple, status: str) -> dict:
        return {
            "pair": list(pair),
            "status": status,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }

    def _print_table(
        self,
        detail_stages: list[str] | None = None,
        active_slurm: dict[str, str] | None = None,
        sacct_states: dict[str, str] | None = None,
        cmd_job_ids_by_stage: dict[str, dict[int, str]] | None = None,
    ) -> None:
        """Print a coloured stage-status table (stack_mode) or the plain
        per-pair listing (pair mode). See refresh()'s docstring for the
        detail_stages/--ls behavior -- mirrors ISCE2_S1.refresh()'s table."""
        from colorama import Fore, Style
        from insarhub.utils.slurm_manager import SLURM_DEAD_STATES

        if not self.config.stack_mode:
            for key, meta in self.jobs.items():
                print(f"  {key:24s} {meta['status']}")
            return

        detail_stages = detail_stages or []
        active_slurm = active_slurm or {}
        sacct_states = sacct_states or {}
        cmd_job_ids_by_stage = cmd_job_ids_by_stage or {}
        color_map = {
            _SUCCEEDED: Fore.GREEN, _FAILED: Fore.RED,
            _RUNNING: Fore.CYAN, _PENDING: Fore.YELLOW,
        }

        print(f"\n{Style.BRIGHT}{'  ':<3} {'STAGE':<10} {'STATUS'}{Style.RESET_ALL}")
        print("-" * 60)
        for stage in self._stack_stages:
            meta = self.jobs.get(stage, {})
            status = meta.get("status", _PENDING)
            color = color_map.get(status, "")
            job_ids = meta.get("slurm_job_ids") or []
            n_cmds = meta.get("n_cmds", 0)
            job_tag = f"  [manager {job_ids[0]}]" if job_ids else ""
            cmd_tag = f"  ({n_cmds} cmd(s))" if n_cmds else ""
            print(f"  - {stage:<8}  {color}{status}{Style.RESET_ALL}{job_tag}{cmd_tag}")

            if stage not in detail_stages:
                continue
            hpc_dir = self._stage_hpc_dir(stage)
            if not hpc_dir.exists():
                continue
            labels = self._stage_command_labels(stage)
            cmd_ids = cmd_job_ids_by_stage.get(stage, {})
            for i, label in enumerate(labels):
                done_f = hpc_dir / f"cmd_{i:04d}.done"
                fail_f = hpc_dir / f"cmd_{i:04d}.fail"
                jid = cmd_ids.get(i)
                # Live squeue state is checked BEFORE the on-disk .done/.fail
                # markers: a retried command's .fail from its previous,
                # superseded attempt isn't removed until the new attempt
                # itself finishes (success clears it, failure rewrites it) --
                # so a currently RUNNING/queued retry must win over that
                # stale marker, or refresh() shows FAILED for a job that's
                # actually in flight right now.
                if jid and active_slurm.get(jid) == "RUNNING":
                    cmd_st, cmd_color = _RUNNING, Fore.CYAN
                    extra = f"  [job {jid}]"
                elif jid and jid in active_slurm:
                    cmd_st, cmd_color = _PENDING, Fore.YELLOW  # queued, not yet running
                    extra = f"  [job {jid}]"
                elif done_f.exists():
                    cmd_st, cmd_color = _SUCCEEDED, Fore.GREEN
                    extra = ""
                elif fail_f.exists():
                    cmd_st, cmd_color = _FAILED, Fore.RED
                    rc = fail_f.read_text().strip()
                    extra = f"  (rc={rc})" if rc else ""
                elif jid and sacct_states.get(jid) in SLURM_DEAD_STATES:
                    cmd_st, cmd_color = _FAILED, Fore.RED
                    extra = f"  [job {jid} {sacct_states[jid]}, no marker]"
                else:
                    cmd_st, cmd_color = _PENDING, Fore.YELLOW  # not yet submitted
                    extra = ""
                print(f"      {i:04d}  {label:<40}  {cmd_color}{cmd_st}{Style.RESET_ALL}{extra}")
