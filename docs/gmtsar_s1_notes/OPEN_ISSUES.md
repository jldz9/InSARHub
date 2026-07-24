# GMTSAR_S1 processor — open issues found by tracing real GMTSAR source

Tracked separately from code comments so they're easy to scan before the
next work session. Update/remove entries as they're resolved.

## RESOLVED: pre-processing gap (was "biggest open unknown")

Traced `p2p_stages.py::P2P1Preprocess` in the GMTSAR repo directly. It
calls `pre_proc SAT master aligned` internally on files in `raw/` before
any interferometry step runs. So `p2p_processing` DOES handle raw input
-> focused SLC/PRM preprocessing itself -- `_stage_case()`'s assumption
(stage raw files into `raw/`, don't pre-focus them) is correct as
written. No fix needed for this one.

## RESOLVED: `pairs=[(ref, sec), ...]` signature was wrong for S1_TOPS

Fixed 2026-07-21. `GMTSAR_S1` now supports BOTH real GMTSAR entry points,
selected via `config.frame_mode`:

- `frame_mode=False` (default): single-subswath `p2p_processing`, pairs =
  `[(ref_stem, sec_stem), ...]` -- raw per-subswath product basename
  stems (Sentinel-1's own naming:
  `s1a-iw<N>-slc-<pol>-<start>-<end>-<orbit>-<mission>-<swath>`), NOT
  plain `YYYYMMDD` dates.
- `frame_mode=True`: multi-subswath `p2p_S1_TOPS_Frame`, pairs =
  `[(ref_safe, ref_eof, sec_safe, sec_eof), ...]` -- .SAFE directory
  names + matching .EOF orbit filenames.

Verified: `proc._build_cmd(pair)` for both modes produces a command
**identical** to the real recipe lines in
`gmtsar/python/tests/recipes/README_S1_Ridgecrest_EQ.txt`, confirmed by
direct comparison, not just code review.

Frame mode uses a per-pair case subdirectory (`case_dir/<ref>_<sec>/`),
not one shared case_dir, because `p2p_S1_TOPS_Frame`'s output
(`F1/F2/F3/merge/`) is not itself pair-namespaced -- confirmed by
tracing the script (no per-pair subdirectory logic exists in it).

## RESOLVED: real end-to-end run revealed a genuine environment bug (not a logic bug)

Ran `GMTSAR_S1` for real (both modes) against real cached S1_Ridgecrest_EQ
data on 2026-07-21. Both runs got well past staging into actual multi-stage
GMTSAR processing (single-subswath mode ran preprocessing/alignment
successfully before failing; Frame mode successfully processed all three
subswaths' SLC/topo stages before failing) -- proving the pairs-signature
fix and staging logic are structurally correct.

The actual failure: GMTSAR's own Python stages (e.g. `dem2topo_ra`) shell
out to the standalone `gmt` binary, which is NOT provided by GMTSAR's own
`bin/` -- it comes from the specific conda environment GMTSAR was
installed into. InSARHub runs in its own separate conda env (different
numpy/GDAL stack, deliberately), which doesn't have `gmt` on PATH at all.
Confirmed directly (`which gmt` inside the InSARHub env: not found), and
confirmed the fix works (re-running `dem2topo_ra` manually with the
correct env active: it actually computes, instead of failing in ~1s).

**Fixed**: `GMTSAR_S1_Config` gained `gmtsar_root` and `gmtsar_env_bin`
fields. `_subprocess_env()` builds an explicit environment for every
GMTSAR subprocess call (both `pop_config` and the real `p2p_*` calls),
prepending both paths to PATH regardless of what environment the calling
InSARHub process itself is running under. This is NOT optional
configuration -- without it, every GMTSAR subprocess call fails near
instantly with no useful error surfaced to the caller (just a nonzero
GMTSAR-internal exit code buried in a per-pair log file).

**Real end-to-end validation: CONFIRMED SUCCEEDED (2026-07-21).** A
freshly-launched real run (Frame mode, with the environment fix, against
real S1_Ridgecrest_EQ data: `S1A_IW_SLC__1SDV_20190704T135158_...SAFE` x
`S1A_IW_SLC__1SDV_20190716T135159_...SAFE`) ran to completion via
`proc.submit()` / `proc.watch()`: `p2p_S1_TOPS_Frame` exited rc=0 after
processing all three subswaths (F1/F2/F3: SLC focusing, alignment, topo,
interferogram, filtering all succeeded per-subswath), then merged,
unwrapped, and geocoded, writing a `.succeeded` marker under `merge/` and
real output products -- `phasefilt.grd`, `phasefilt_ll.grd`, `corr.grd`,
`corr_ll.grd` plus PNG/KML previews, all non-empty, real byte sizes
(`phasefilt_ll.grd` ~55MB, `corr_ll.grd` ~53MB). `proc.jobs[...]['status']`
reported `SUCCEEDED`.

This is genuine, not structural-only: the full real GMTSAR C/Python
pipeline ran end-to-end inside InSARHub's process/env model, on real
Sentinel-1 SAFE data, producing real geocoded interferometric products.

## RESOLVED: single-subswath mode (`frame_mode=False`) fixed and validated

The real recipe (`README_S1_Ridgecrest_EQ.txt`) runs `p2p_processing` from
a special `H_res/` subdirectory that the reference test tarball
pre-populates with correctly per-subswath-extracted `.xml`/`.tiff` files
(stems like `s1a-iw2-slc-vv-<start>-<end>-<orbit>-<mission>-<swath>`).
Inspecting `H_res/raw/` directly showed these per-stem files are plain
symlinks into the equivalent Frame-mode `F<N>/raw/` subswath files pulled
from the same `.SAFE`, and the `.EOF` is the same scene-level orbit file,
just symlinked under the per-subswath stem name -- i.e. the "extraction"
GMTSAR itself needs is mechanical, not a real preprocessing step.

**Fixed**: `GMTSAR_S1._extract_subswath_stem()` reproduces this directly --
given a `.SAFE` name, `config.subswath` (IW1/2/3), and `config.polarization`,
it globs `measurement/`+`annotation/` for the matching `.tiff`/`.xml`,
symlinks them plus the `.EOF` into `raw/` under GMTSAR's required
same-stem naming. Both `frame_mode` settings now take the SAME pairs
shape -- `[(ref_safe, ref_eof, sec_safe, sec_eof), ...]` -- callers never
hand-derive per-subswath stems themselves.

**Real end-to-end validation: CONFIRMED SUCCEEDED (2026-07-21).** A real
run (`frame_mode=False`, IW2/vv, same S1_Ridgecrest_EQ pair as the Frame
run above) ran to completion via `proc.submit()`/`proc.watch()`:
`align_tops.csh` preprocessing succeeded (172s), `p2p_processing` exited
rc=0, wrote a `.succeeded` marker under
`intf/<ref_stem>_<sec_stem>/`, and produced real output products
(`phasefilt.grd`, `phasefilt_ll.grd`, `corr.grd`, `corr_ll.grd` plus
PNG/KML previews). `proc.jobs[...]['status']` reported `SUCCEEDED`.

Both `frame_mode` settings are now genuinely end-to-end validated against
real Sentinel-1 data.

## Known gap: not yet tested

- **Full pipeline chain**: InSARHub's `S1_SLC` downloader -> `GMTSAR_S1`
  -> a MintPy analyzer run, chained continuously. Both real runs above fed
  pre-staged `.SAFE`/`.EOF`/DEM files directly; the downloader itself was
  never exercised. Next up.

## RESOLVED: frame_mode=False output directory naming was wrong

Found while starting real `prep_gmtsar.py` testing (2026-07-21): GMTSAR's
real `p2p_processing` output directory is named by its own Julian-date
pair (e.g. `intf/2019184_2019196/`, derived from each SLC's
`SC_clock_start`), NOT `intf/<ref_stem>_<sec_stem>/` as the code and docs
previously claimed. The wrong assumption was masked because `_write_status()`
`mkdir`s its own status directory regardless of whether GMTSAR actually
wrote output there -- so `SUCCEEDED` was still reported correctly, but
downstream consumers (MintPy) pointed at that path would find nothing.
`prep_gmtsar.py` itself relies on this exact naming: it derives `DATE12`
by parsing the interferogram directory's basename as a Julian-date pair.

**Fixed**: `_run_one_pair()` now diffs `intf/`'s contents before/after
each real run and records whichever new directory matches GMTSAR's real
`\d{7}_\d{7}` naming; `_status_dir()` uses that discovered directory
once known, instead of assuming a name. Real MintPy run against this
fixed path is the next validation step.
- **CLI partially generalized for GMTSAR_S1** (2026-07-21 -> 2026-07-23):
  dispatch in `cli/main.py` is generic
  (`issubclass(processor_cls, LocalProcessor)`), so `GMTSAR_S1` is
  *routed* to the local-processor code path -- but that path had real
  ISCE_S1-specific assumptions. **Fixed and confirmed via a real CLI
  `refresh` run against GMTSAR_S1's actual saved `gmtsar_jobs.json`**:
  - `_proc_local_submit` used to hardcode 2-tuple pairs, truncating
    GMTSAR_S1's required 4-tuples. Now preserves full arity.
  - `refresh`/`retry`/`watch`/`cancel` used to look for `isce_jobs*.json`
    specifically. Added `JOBS_FILE`/`JOBS_SUBDIR` class attributes to
    `LocalProcessor` (set per-processor) + a generic `_jobs_glob()`
    helper -- now finds `gmtsar_jobs.json` under `gmtsar_case/`.
  - `_load_local_processor` (saved-job reload) assumed ISCE's
    `{"jobs": {...}}` wrapper and step-based pairs. GMTSAR_S1.save()
    writes the jobs dict directly at the top level and stores the real
    pair under `"pair"` -- reload now handles both shapes.
  **Method-signature mismatch: FIXED.** `refresh(ls=...)` /
  `watch(refresh_interval=...)` crashed on `GMTSAR_S1` (different
  signatures than `ISCE_Base`). Added `_call_if_supported()` in
  cli/main.py -- calls each processor's method with only the kwargs its
  real signature accepts (via `inspect.signature`), instead of assuming
  every local processor shares ISCE's exact shape. `cancel` (no
  `GMTSAR_S1` equivalent at all -- `ISCE_Base.cancel()` does a real
  `scancel`/kill-background-process, nothing analogous exists yet):
  now a clean `[ERROR] 'GMTSAR_S1' does not support cancel()` instead of
  an `AttributeError` crash.

  **Also found and fixed, real and deeper**: a freshly CLI-reconstructed
  `GMTSAR_S1` (submit() ran in a DIFFERENT process than refresh/watch/
  cancel, the normal CLI usage pattern) had `self.jobs` permanently empty
  -- `refresh()` only updates entries already in `self.jobs`, so a
  reloaded processor printed nothing, ever, regardless of real on-disk
  status. Root cause went deeper for `frame_mode=False`: `self._stems`
  and `self._real_intf_dirs` are populated only in-memory during
  `_stage_case()`/`_run_one_pair()` in the ORIGINAL submitting process,
  never persisted -- so even fixing `self.jobs` init couldn't find the
  real Julian-date `intf/` directory. Fixed with two read-only rediscovery
  methods run at construction time: `_rediscover_stem()` (matches
  already-extracted `raw/*.tiff` symlinks against a pair's real `.SAFE`
  name, no need for the original `.SAFE` source again) and
  `_rediscover_real_intf_dir()` (recomputes the Julian-date pair name from
  real `SC_clock_start` in the already-present `.PRM` files, same formula
  `_run_one_pair()` uses live). `frame_mode=True` needed none of this --
  its `_status_dir()` is purely path-based, no `_stems` dependency.

  **Confirmed via a real CLI run against the actual completed
  single-subswath job** (fresh process, no submit() call in this
  process): `refresh` and `watch --interval 3` both correctly printed
  `SUCCEEDED`, matching the real on-disk `.succeeded` marker.
  `cancel` correctly refused instead of crashing.

  GUI: no frontend dialog integration exists either.
- **DEM auto-download** from `bbox` (matching `ISCE_S1`'s GLO-30
  auto-fetch): not implemented -- `dem_path` must be supplied explicitly
  (now validated at construction time, fails fast if missing).
- **Processor-level edge cases**: `retry()` on a real failure, reloading
  a saved `gmtsar_jobs.json` into a fresh process, concurrent multi-pair
  submission against real GMTSAR subprocesses (`max_workers`>1),
  `skip_existing` re-submit against a real completed case, and sensors
  other than `S1_TOPS` (13 other GMTSAR-supported families listed in
  `SUPPORTED_SATS`, none exercised) -- all only covered by mocked unit
  tests, not real runs.

## MintPy `prep_gmtsar.py` — partial real progress, then a real scope wall

Ran `prep_gmtsar.py` for real against our real single-subswath
(`frame_mode=False`, unwrapping enabled, `threshold_snaphu=0.1`) output.
Confirmed working, in order, each fixed for real as it was hit:

- `ALOOKS`/`RLOOKS`: no `config.<SAT>.txt` exists (`GMTSAR_S1` only
  writes `config.py`) -- prep_gmtsar.py needs these supplied directly in
  its own template (bare `ALOOKS`/`RLOOKS` keys, not `mintpy.load.*`).
  Computed from real PRM `AZIMUTH_PIXEL_SIZE`/`RANGE_PIXEL_SIZE` using
  prep_gmtsar.py's own formula.
- `HEADING`: not derivable from GMTSAR's PRM (`orbdir=D`, `lookdir=R`
  only, no angle). Used MintPy's own documented canonical value for
  descending IW Sentinel-1 (-168 deg, `mintpy/utils/utils0.py:706`) --
  a standard reference value, not a per-track precision derivation.
- GDAL netCDF driver missing in `insarhub_test` (pip-installed GDAL) --
  `gdal.Open()` on GMTSAR's `.grd` returned None. Fixed by installing
  `libgdal-netcdf` (now pinned in `environment.yml`).

With those three fixed, prep_gmtsar.py got past metadata extraction,
LAT/LON_REF, and X/Y_FIRST/STEP -- real progress, `data.rsc` was
written successfully.

**Real wall hit**: `read_baseline_table()` requires a
`baseline_table.dat` file (`file_ID yyyyddd.fraction day_cnt b_para
b_perp` per line, one row per SLC date) -- a **stack-level** artifact
GMTSAR generates across multiple dates, not something a single
interferogram run produces. Our validation used exactly one real pair
(two dates), so no real baseline table exists. Confirming full MintPy
load requires either a real multi-pair stack (significant more compute
-- snaphu alone took ~3h for the one pair we ran) or accepting a
synthetic stand-in, which was NOT built (declined, rather than guess) --
this is a genuine open item, not a "should work" claim.
