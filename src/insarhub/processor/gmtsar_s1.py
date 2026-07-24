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
    JOBS_SUBDIR = "gmtsar_case"  # matches case_dir property below

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
        if not self.config.dem_path:
            raise NotImplementedError(
                "GMTSAR_S1_Config.dem_path is required in this version -- "
                "bbox-driven DEM auto-fetch (matching ISCE_S1's GLO-30 "
                "download) is not implemented yet."
            )
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
        """
        return self.workdir / "gmtsar_case"

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
            return self.case_dir / "intf" / self._real_intf_dirs[key]
        if key not in self._stems:
            # Not staged yet (or staging failed partway through a
            # multi-pair _stage_case() and never reached this pair) --
            # a path that can never exist reads as PENDING via
            # _read_status(), instead of a masking KeyError that hides
            # the real staging failure (found via audit).
            return self.case_dir / "intf" / f"_unstaged_{key}"
        # Not yet run (or the real dir wasn't found post-run, e.g. the
        # pair failed before GMTSAR created any output). Sentinel path
        # based on our own stems -- not GMTSAR's real naming, but stable
        # and never collides with a real Julian-date directory, so it's
        # safe as a PENDING/RUNNING placeholder before/without a real run.
        ref_stem, sec_stem = self._stems[key]
        return self.case_dir / "intf" / f"_pending_{ref_stem}_{sec_stem}"

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
        raw_dir = self.case_dir / "raw"
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
        raw_dir = self.case_dir / "raw"
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
        candidate = self.case_dir / "intf" / f"{days[0]}_{days[1]}"
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

    def _extract_subswath_stem(self, ref_safe: str, ref_eof: str, raw_dir: Path) -> str:
        """Extract one IW subswath's .tiff/.xml from a raw .SAFE dir and
        stage them plus the matching .EOF into raw_dir under GMTSAR's
        required same-stem naming (<stem>.tiff/.xml/.EOF).

        p2p_processing does not read .SAFE directories itself -- see
        module docstring for how this was confirmed (both from
        p2p_processing's own usage string and from GMTSAR's bundled
        H_res/raw/ single-subswath test fixture).
        """
        cfg = self.config
        safe_dir = self._find_input(ref_safe, cfg.slc_dir)
        eof_path = self._find_input(ref_eof, cfg.orbit_dir)
        tiffs = sorted(
            (safe_dir / "measurement").glob(
                f"s1?-iw{cfg.subswath}-slc-{cfg.polarization}-*.tiff"
            )
        )
        if not tiffs:
            raise FileNotFoundError(
                f"no IW{cfg.subswath}/{cfg.polarization} subswath product "
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

        DEM auto-download from a bbox (dem_path=None) is NOT implemented
        -- config.dem_path must point at an existing GMTSAR-format DEM
        (topo/dem.grd) for now.
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

        dest = topo_dir / "dem.grd"
        if not dest.exists():
            dest.symlink_to(Path(cfg.dem_path).resolve())

        config_py = target / "config.py"
        if not config_py.exists():
            if cfg.config_template:
                import shutil
                shutil.copy(cfg.config_template, config_py)
            else:
                subprocess.run(
                    ["pop_config", cfg.sat],
                    cwd=str(target),
                    check=True,
                    env=self._subprocess_env(),
                )

    def _stage_case(self) -> None:
        if not self.config.frame_mode:
            self._stage_one_case_dir(self.case_dir)
            raw_dir = self.case_dir / "raw"
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
        intf_dir = self.case_dir / "intf"
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
        for pair in self.pairs:
            key = _pair_key(pair)
            status = _read_status(self._status_dir(pair))
            if key in self.jobs:
                self.jobs[key]["status"] = status
        self._print_table()
        return self.jobs

    def retry(self) -> dict:
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
