# -*- coding: utf-8 -*-
"""
GMTSAR_S1 — Sentinel-1 InSAR processor backed by GMTSAR's Python
p2p_processing / p2p_S1_TOPS_Frame pipelines.

STATUS: v2, unified pairs signature (2026-07-21). Both modes now take the
same pairs = [(ref_safe, ref_eof, sec_safe, sec_eof), ...] shape -- raw
.SAFE + .EOF names, nothing more. v1 required frame_mode=False callers to
hand-derive raw per-subswath product stems themselves; that was correct
per GMTSAR's own CLI contract but bad UX and untested against real data.
Real end-to-end validation (frame_mode=True, see
docs/gmtsar_s1_notes/OPEN_ISSUES.md) confirmed the pipeline genuinely
works; this revision closes the remaining gap by having GMTSAR_S1 itself
do the single-subswath extraction that frame_mode=False needs.

Two distinct GMTSAR entry points, selected via config.frame_mode:

  frame_mode=False (default) -- single-subswath, via p2p_processing.
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

  frame_mode=True -- multi-subswath Frame, via p2p_S1_TOPS_Frame.
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

Interface mirrors ISCE_S1 / Hyp3_S1:
  submit()  -- stage case dir(s), launch the right GMTSAR entry point per
               pair (subprocess), up to max_workers concurrent.
  refresh() -- read per-pair status, print a table.
  retry()   -- re-run failed pairs (and only failed pairs).
  watch()   -- poll until every pair is SUCCEEDED or FAILED.
  save()    -- persist gmtsar_jobs.json, matching ISCE_S1's isce_jobs.json.

Why no custom output-normalization step (frame_mode=False only -- see
KNOWN GAP below for frame_mode=True): GMTSAR's native per-pair output
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
KNOWN GAP: frame_mode=True's real output lands in merge/ with the same
file basenames but has NOT been checked against prep_gmtsar.py's
directory-discovery logic -- needs a real Frame-mode run + a
prep_gmtsar.py dry run before that claim can be made for Frame mode too.

Deliberately kept as a subprocess-per-pair design, not in-process Python
calls into GMTSAR's own p2p_stages.py: (1) InSARHub and GMTSAR run in
separate conda environments with different numpy/GDAL stacks -- importing
GMTSAR's stage code in-process risks real dependency collisions; (2) most
wall-clock is spent in C binaries (gmt, snaphu) either way, so in-process
Python orchestration wouldn't meaningfully speed anything up; (3) this
matches both ISCE_S1's own external-process pattern AND GMTSAR's own test
harness (case_runner.py), which deliberately runs each case in its own
subprocess for process-group isolation.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from insarhub.config import GMTSAR_S1_Config
from insarhub.config.paths import GMTSARPaths
from insarhub.core import LocalProcessor

logger = logging.getLogger(__name__)

_PENDING = "PENDING"
_RUNNING = "RUNNING"
_SUCCEEDED = "SUCCEEDED"
_FAILED = "FAILED"

JOBS_FILE = "gmtsar_jobs.json"

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

    Usage, single-subswath (default, mirrors ISCE_S1's own docstring
    example)::

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
                subswath  = 2,  # IW2 (default)
            ),
        )
        proc.submit()
        proc.watch()

    Usage, multi-subswath Frame (set frame_mode=True; same pairs shape)::

        proc = GMTSAR_S1(
            pairs  = [("S1A_IW_SLC__1SSV_20150526T014935_20150526T015002_006086_007E23_679A.SAFE",
                       "S1A_OPER_AUX_POEORB_OPOD_20150627T155155_V20150606T225944_20150608T005944.EOF",
                       "S1A_IW_SLC__1SDV_20150607T014936_20150607T015003_006261_00832E_3626.SAFE",
                       "S1A_OPER_AUX_POEORB_OPOD_20150615T155109_V20150525T225944_20150527T005944.EOF")],
            config = GMTSAR_S1_Config(
                workdir = '/data/stack', slc_dir = '/data/slcs',
                orbit_dir = '/data/orbits', dem_path = '/data/dem.grd',
                frame_mode = True,
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
                "sec_safe, sec_eof) -- same shape for both frame_mode "
                "settings."
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
        # gmtsar_root/gmtsar_env_bin are required for the same reason
        # _subprocess_env() exists at all (see its docstring): InSARHub's
        # own env does not provide `gmt`, so silently falling back to the
        # inherited PATH fails almost instantly with no useful error.
        # dem_path is optional: when None, a GMTSAR DEM is auto-downloaded
        # at staging time via `make_dem` (SRTM) from the SLC footprint or
        # config.bbox -- see _ensure_dem().
        if not self.config.gmtsar_root or not self.config.gmtsar_env_bin:
            raise ValueError(
                "GMTSAR_S1_Config.gmtsar_root and gmtsar_env_bin are both "
                "required -- GMTSAR's own Python stages shell out to the "
                "standalone `gmt` binary from GMTSAR's own conda "
                "environment, which InSARHub's environment does not "
                "provide. See _subprocess_env()'s docstring for the real "
                "bug this prevents."
            )
        self.pairs = pairs
        self.jobs: dict[str, dict] = {}
        # frame_mode=False only: pair_key -> (ref_stem, sec_stem), the
        # per-subswath product stems _extract_subswath_stem() derives
        # during staging. Populated by submit()/retry() before any
        # _status_dir()/_build_cmd() call needs it.
        self._stems: dict[str, tuple[str, str]] = {}
        # frame_mode=False only: pair_key -> GMTSAR's real Julian-date
        # output dirname (e.g. "2019184_2019196"), discovered post-run.
        # See _run_one_pair()'s docstring for why this exists.
        self._real_intf_dirs: dict[str, str] = {}
        self._paths = GMTSARPaths(self.workdir)
        self._lock = threading.Lock()
        self._rediscover_state()

    def _rediscover_state(self) -> None:
        """Populate self.jobs (and self._stems/_real_intf_dirs for
        frame_mode=False) from real on-disk state, so a freshly
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
            if not self.config.frame_mode:
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
        """Shared GMTSAR case directory -- used directly when
        frame_mode=False (p2p_processing is pair-namespaced via
        intf/<julian_date_pair>/, GMTSAR's own naming -- see
        _run_one_pair()). When frame_mode=True this is only the PARENT
        of each pair's own subdirectory; see pair_case_dir().
        Layout centralized in config/paths.py (GMTSARPaths).
        """
        return GMTSARPaths(self.workdir).case_dir

    def pair_case_dir(self, pair: tuple) -> Path:
        """The directory a given pair's p2p_* invocation actually runs
        from. Shared case_dir for frame_mode=False; a dedicated
        per-pair subdirectory for frame_mode=True (p2p_S1_TOPS_Frame's
        F1/F2/F3/merge/ output is not itself pair-namespaced)."""
        if self.config.frame_mode:
            return self.case_dir / _pair_key(pair)
        return self.case_dir

    def _status_dir(self, pair: tuple) -> Path:
        if self.config.frame_mode:
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
            prms = list(raw_dir.glob(f"S1_*_F{self.config.subswath}.PRM"))
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
        directories, not .zip archives, in both frame_mode settings
        (frame_mode=True wholesale-symlinks slc_dir's contents into raw/;
        a .zip sitting there is just a useless symlinked zip
        p2p_S1_TOPS_Frame can't read as a .SAFE tree).

        Idempotent: if a COMPLETE .SAFE directory already exists, returns it
        unchanged without touching the .zip. A partial .SAFE (e.g. an earlier
        extraction killed mid-write -- no measurement/ tiffs) is treated as
        absent and re-extracted from the .zip, rather than trusted and later
        crashing _extract_subswath_stem with "no subswath product found".
        """
        cfg = self.config
        base = Path(cfg.slc_dir) if cfg.slc_dir and str(cfg.slc_dir) not in ("auto", "") else self.workdir
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
        safe_dir = self._find_input(ref_safe, cfg.slc_dir)
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

        frame_mode=True: symlinks slc_dir/orbit_dir contents wholesale
        into raw/ -- p2p_S1_TOPS_Frame reads raw .SAFE dirs + .EOF orbits
        directly (confirmed this matches p2p_processing's own
        P2P1Preprocess, which calls `pre_proc SAT master aligned`
        internally on raw/ input, so raw .SAFE-derived files are the
        right thing to stage, NOT pre-focused SLCs).

        frame_mode=False: raw/ is instead populated per-pair by
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

        if cfg.frame_mode:
            if cfg.slc_dir and str(cfg.slc_dir) not in ("auto", ""):
                self._symlink_dir_contents(Path(cfg.slc_dir), raw_dir)
            if cfg.orbit_dir and str(cfg.orbit_dir) not in ("auto", ""):
                self._symlink_dir_contents(Path(cfg.orbit_dir), raw_dir)

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

        Same DEM (and the same stitch_dem call) ISCE_S1._prepare_dem() uses, so
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
          Mirrors ISCE_S1's bbox-driven DEM auto-fetch, but produces GMTSAR's
          native dem.grd directly rather than an ISCE2 binary DEM.
        """
        dest = topo_dir / "dem.grd"
        if dest.exists():
            return dest
        cfg = self.config
        if cfg.dem_path:
            dest.symlink_to(Path(cfg.dem_path).resolve())
            return dest

        # Auto-download. Derive [S, N, W, E] from the real SLC footprints
        # (preferred) or fall back to the AOI's bounding box.
        from insarhub.processor.isce_s1 import _bbox_from_slc_dir
        bbox = None
        for scan in dict.fromkeys([
            Path(str(cfg.slc_dir)) if cfg.slc_dir and str(cfg.slc_dir) not in ("auto", "") else None,
            self.workdir,
        ]):
            if scan is None:
                continue
            bbox = _bbox_from_slc_dir(scan)
            if bbox:
                break
        if bbox is None:
            aoi = self._resolve_aoi()
            if aoi:
                coords = self._aoi_coords(aoi)
                if len(coords) >= 3:
                    lons = [c[0] for c in coords]
                    lats = [c[1] for c in coords]
                    bbox = [min(lats), max(lats), min(lons), max(lons)]  # [S, N, W, E]
        if bbox is None:
            raise ValueError(
                "dem_path is unset and no DEM could be auto-downloaded: no SLC "
                "footprints found in slc_dir/workdir and no AOI is available. "
                "Provide dem_path, an aoi (WKT), or ensure SLCs are on disk."
            )
        S, N, W, E = bbox

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

    def _render_config_py(self) -> str:
        """Render config.py from the config's GMTSAR processing params
        (GMTSAR_CONFIG_PARAMS on GMTSAR_Base_Config/GMTSAR_S1_Config).

        Replaces the old `pop_config <SAT>` shell-out: field defaults match
        pop_config's own defaults exactly (verified by diffing its output),
        so an untouched config reproduces GMTSAR's stock config.py, while any
        overridden field now actually takes effect -- previously these
        params were inert (buried in the auto-generated file with no way to
        set them from InSARHub)."""
        cfg = self.config
        lines = [f"# Generated by GMTSAR_S1 for SAT={cfg.sat} "
                 f"(from {type(cfg).__name__} fields)."]
        for name in cfg.GMTSAR_CONFIG_PARAMS:
            val = getattr(cfg, name)
            lines.append(f"{name} = {val}")
        return "\n".join(lines) + "\n"

    def _stage_case(self) -> None:
        # Extract any scene still sitting as a raw ASF .zip (never manually
        # unzipped) into a real .SAFE directory before either branch below
        # touches slc_dir -- see _ensure_safe_extracted()'s docstring.
        unique_safes = {p[0] for p in self.pairs} | {p[2] for p in self.pairs}
        for safe_name in unique_safes:
            self._ensure_safe_extracted(safe_name)

        if not self.config.frame_mode:
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
    #  LocalProcessor interface                                          #
    # ------------------------------------------------------------------ #

    def submit(self) -> dict:
        if self.config.stack_mode:
            return self._submit_stack()

        self._stage_case()

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

        thread = threading.Thread(target=self._run_pairs, args=(pending,), daemon=True)
        thread.start()
        self._thread = thread
        self.save()
        return self.jobs

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
        """Tracked stage keys, in run order. Multi-subswath adds an
        align/intf pair per F<N> plus a final merge; single stays align+intf."""
        if self._multiswath:
            stages: list[str] = []
            for n in self._subswath_list():
                stages += [f"align_F{n}", f"intf_F{n}"]
            stages.append("merge")
            return tuple(stages)
        return ("align", "intf")

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
        thread = threading.Thread(target=self._run_stack, daemon=True)
        thread.start()
        self._thread = thread
        self.save()
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

    def _stage_stack(self) -> None:
        """Stage the stack: one F<N> case per subswath (full frame) or a
        single flat case (single subswath). Each case gets raw/ with that
        subswath's per-scene .tiff/.xml/.EOF, topo/dem.grd, and its data.in /
        intf.in / batch_tops.config."""
        scenes, ordered = self._ordered_scenes()
        for safe in scenes:
            self._ensure_safe_extracted(safe)

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

    def _run_stack(self) -> None:
        """Run the stack. Multi-subswath: an independent align+intf pipeline
        per F<N>, then merge_batch combines the subswaths (GMTSAR's own
        full-frame layout -- the structural analogue of ISCE topsStack). Single
        subswath: the flat case. Shells out to GMTSAR's batch tools (csh for the
        unported-stub TOPS batch steps, real Python/C for the rest)."""
        env = self._subprocess_env()
        multi = self._multiswath

        for sw, work, raw, topo in self._swath_layout():
            astage = f"align_F{sw}" if multi else "align"
            istage = f"intf_F{sw}" if multi else "intf"
            dem = (topo / "dem.grd").resolve()

            # ── align this swath's whole stack to its super-master ──
            if self.config.skip_existing and _read_status(self._stack_status_dir(astage)) == _SUCCEEDED:
                logger.info("%s already succeeded, skipping.", astage)
            else:
                self._set_stage(astage, _RUNNING)
                log = work / "stack_align.log"
                ok = True
                # ESD (enhanced spectral diversity) alignment vs geometry-only,
                # selected by config.coregistration -- ESD is the analogue of
                # ISCE_S1's NESD and the default. Same data.in/output contract,
                # esd variant just takes a 4th esd_mode arg.
                esd = str(self.config.coregistration).lower() == "esd"
                with open(log, "w") as lf:
                    for mode in ("1", "2"):
                        cmd = (["preproc_batch_tops_esd.csh", "data.in", str(dem),
                                mode, str(self.config.esd_mode)] if esd else
                               ["preproc_batch_tops.csh", "data.in", str(dem), mode])
                        proc = subprocess.run(
                            cmd, cwd=str(raw), stdout=lf, stderr=subprocess.STDOUT, env=env)
                        if proc.returncode != 0:
                            ok = False
                            break
                self._set_stage(astage, _SUCCEEDED if ok else _FAILED)
                if not ok:
                    logger.error("%s failed -- see %s", astage, log)
                    return

            # ── AOI -> region_cut (needs the post-align master PRM; per swath,
            # since radar coords differ between subswaths) ──
            self._apply_aoi_region_cut(work, raw)

            # ── interferograms for this swath ──
            if self.config.skip_existing and _read_status(self._stack_status_dir(istage)) == _SUCCEEDED:
                logger.info("%s already succeeded, skipping.", istage)
            elif not self._run_swath_intf(sw, work, raw, env, istage):
                return

        if multi:
            self._run_merge(env)

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

    def _run_swath_intf(self, sw: int, work: Path, raw: Path, env: dict,
                        stage: str) -> bool:
        """intf_tops.csh per pair (own ThreadPoolExecutor -- no GNU `parallel`
        dependency, real per-pair status). Returns True on full success."""
        self._set_stage(stage, _RUNNING)
        intf_all = work / "intf_all"   # intf_tops moves finished pairs here
        pair_lines = [l for l in (work / "intf.in").read_text().splitlines() if l.strip()]
        product = "unwrap.grd" if float(self.config.threshold_snaphu) > 0 else "phasefilt.grd"

        def _pair_dir(line: str) -> Path | None:
            ref, rep = line.split(":")
            rid = self._stem_julian(ref, raw)
            pid = self._stem_julian(rep, raw)
            return intf_all / f"{rid}_{pid}" if rid and pid else None

        def _run_one(line: str) -> bool:
            tag = line.replace(":", "__")
            pin = work / f"pair_{tag}.in"
            pin.write_text(line + "\n")
            with open(work / f"intf_{tag}.log", "w") as lf:
                subprocess.run(["intf_tops.csh", pin.name, "batch_tops.config"],
                               cwd=str(work), stdout=lf, stderr=subprocess.STDOUT, env=env)
            pd = _pair_dir(line)
            return bool(pd and (pd / product).exists())

        results = []
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as pool:
            futs = {pool.submit(_run_one, l): l for l in pair_lines}
            for fut in as_completed(futs):
                line = futs[fut]
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
        self._set_stage(stage, _SUCCEEDED if ok else _FAILED)
        return ok

    def _run_merge(self, env: dict) -> None:
        """Combine the per-subswath interferograms with GMTSAR's merge_batch
        (real Python-ported merge_unwrap_geocode_tops underneath). Output lands
        in merge/<pair_julian>/ with the same *_ll.grd product names the
        single-subswath path produces, so the analyzers consume it unchanged."""
        self._set_stage("merge", _RUNNING)
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
            self._set_stage("merge", _FAILED)
            return

        (merge / "intflist").write_text("\n".join(common) + "\n")
        # create_merge_input.csh <intflist> <path-to-F dirs> <mode 0=all 3>
        # is still a python stub upstream, so use the csh. Called with ".."
        # (path relative to merge/) because merge_batch prefixes "../".
        mode = "0" if swaths == [1, 2, 3] else ("1" if swaths == [1, 2] else "2")
        log = merge / "stack_merge.log"
        with open(log, "w") as lf:
            mi = subprocess.run(
                ["create_merge_input.csh", "intflist", "..", mode],
                cwd=str(merge), stdout=subprocess.PIPE, stderr=lf, text=True, env=env)
            if mi.returncode != 0 or not mi.stdout.strip():
                lf.write(f"\ncreate_merge_input failed (rc={mi.returncode})\n{mi.stdout}")
                logger.error("merge: create_merge_input failed -- see %s", log)
                self._set_stage("merge", _FAILED)
                return
            (merge / "merge_input").write_text(mi.stdout)
            lf.write(mi.stdout + "\n")
            cfg = self._paths.swath_batch_config(swaths[0])  # any swath's config
            proc = subprocess.run(
                ["merge_batch", "merge_input", str(cfg.resolve()), str(self.config.det_stitch)],
                cwd=str(merge), stdout=lf, stderr=subprocess.STDOUT, env=env)

        product = "unwrap.grd" if float(self.config.threshold_snaphu) > 0 else "phasefilt.grd"
        merged = [d for d in merge.iterdir()
                  if d.is_dir() and re.match(r"^\d{7}_\d{7}$", d.name)
                  and (d / product).exists()]
        ok = proc.returncode == 0 and len(merged) == len(common)
        if not ok:
            logger.error("merge: produced %d/%d merged pairs (rc=%s) -- see %s",
                         len(merged), len(common), proc.returncode, log)
        self._set_stage("merge", _SUCCEEDED if ok else _FAILED)

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
        cfg = self.config
        env = os.environ.copy()
        prepend = []
        if cfg.gmtsar_env_bin:
            prepend.append(str(cfg.gmtsar_env_bin))
        if cfg.gmtsar_root:
            env["GMTSAR"] = str(cfg.gmtsar_root)
            prepend.append(str(Path(cfg.gmtsar_root) / "bin"))
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
        if not self.config.frame_mode and intf_dir.is_dir():
            before = {p.name for p in intf_dir.iterdir()}

        with open(log_path, "w") as log_f:
            proc = subprocess.run(
                cmd, cwd=str(run_dir), stdout=log_f, stderr=subprocess.STDOUT,
                env=self._subprocess_env(),
            )

        if not self.config.frame_mode and intf_dir.is_dir():
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
        if not cfg.frame_mode:
            ref_stem, sec_stem = self._stems[_pair_key(pair)]
            return ["p2p_processing", cfg.sat, ref_stem, sec_stem, "config.py"]
        ref_safe, ref_eof, sec_safe, sec_eof = pair
        return [
            "p2p_S1_TOPS_Frame", ref_safe, ref_eof, sec_safe, sec_eof,
            "config.py", cfg.polarization, "1" if cfg.parallel else "0",
        ]

    def refresh(self) -> dict:
        if self.config.stack_mode:
            for stage in self._stack_stages:
                status = _read_status(self._stack_status_dir(stage))
                if stage in self.jobs:
                    self.jobs[stage]["status"] = status
            self._print_table()
            return self.jobs
        for pair in self.pairs:
            key = _pair_key(pair)
            status = _read_status(self._status_dir(pair))
            if key in self.jobs:
                self.jobs[key]["status"] = status
        self._print_table()
        return self.jobs

    def retry(self) -> dict:
        if self.config.stack_mode:
            # Re-run from the first failed stage onward (align gates intf).
            failed = [s for s in self._stack_stages
                      if _read_status(self._stack_status_dir(s)) == _FAILED]
            if not failed:
                logger.info("No failed stack stages to retry.")
                return self.jobs
            for stage in self._stack_stages:
                _write_status(self._stack_status_dir(stage), _PENDING)
            thread = threading.Thread(target=self._run_stack, daemon=True)
            thread.start()
            self._thread = thread
            return self.jobs
        failed = [p for p in self.pairs if _read_status(self._status_dir(p)) == _FAILED]
        if not failed:
            logger.info("No failed pairs to retry.")
            return self.jobs
        for pair in failed:
            _write_status(self._status_dir(pair), _PENDING)
        thread = threading.Thread(target=self._run_pairs, args=(failed,), daemon=True)
        thread.start()
        self._thread = thread
        return self.jobs

    def watch(self, poll_interval: float = 10.0) -> dict:
        import time
        while True:
            self.refresh()
            statuses = {j["status"] for j in self.jobs.values()}
            if statuses <= {_SUCCEEDED, _FAILED}:
                break
            time.sleep(poll_interval)
        return self.jobs

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

    def _print_table(self) -> None:
        for key, meta in self.jobs.items():
            print(f"  {key:24s} {meta['status']}")
