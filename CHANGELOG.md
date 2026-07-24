# Changelog

## [Unreleased]

### CLI

- **Inconsistent multithread flag** (`cli/main.py`) — worker/thread-count overrides were scattered across `--workers`, `--max-workers`, and `--max-worker`-style lookups; the HyP3 and local `submit` handlers even checked `args.max_worker` (an attribute that doesn't exist) instead of `args.worker`, so the flag silently had no effect on `max_workers`/`max_concurrent_hpc`. Consolidated to a single `--worker` flag everywhere and fixed the attribute lookup in both submit paths.
- **`--step` on `ISCE_S1 submit`** (`cli/main.py`, `isce_base.py`, `isce_s1.py`) — new flag to force-(re)run specific step(s) without the destructive cascade of `retry()` (which resets and reruns every step from the failure point onward). Accepts full names (`run_03_average_baseline`) or short numeric forms (`03`, `3`); unknown steps raise an error. Steps not listed are left completely untouched, regardless of their current status. `_resolve_step_names()` (shared by `submit()` and `refresh()`) moved to `isce_base.py`.
- **`--ls` on `ISCE_S1 refresh`** (`cli/main.py`, `isce_base.py`) — by default `refresh` now prints only the step-level summary, omitting per-command (`cmd_XXXX`) detail lines. Bare `--ls` restores full per-command output for every step; `--ls 01` / `--ls 1` / `--ls run_01` restricts detailed output to just that step.
- **`ISCE_SBAS --hpc-mode` now uses `sbatch_options.json`** (`isce_base.py`, `mintpy_base.py`, `defaultconfig.py`) — removed the `--hpc-sbatch-opts` inline-JSON flag; SLURM resources for the analyzer's single HPC job now come from the same `sbatch_options.json` file used by `ISCE_S1`, under a new `"17": "SBAS"` step entry (added to `_SBATCH_DEFAULT_TEMPLATE`), so one file governs both the processor's 16 steps and the analyzer's step. New `load_or_init_sbatch_options()` helper: generates the file (steps 01-16 + 17) if missing and stops for review; if the file exists but lacks the `"17"` entry, adds it with default resources, rewrites the file, and prints a warning before proceeding. `_sbatch_opts_for_step()` refactored to share the default/step-override merge logic (`_merge_sbatch_opts()`) with the analyzer.
- **`--merge` on downloader** (`cli/main.py`) — downloads all requested stacks into a single `workdir/merged/slc/` directory instead of per-stack subdirectories; orbit files (`-O`) follow into the same merged location when combined with `--merge`.
- **`--max-workers` on `processor download`** (`cli/main.py`) — HyP3 job-output downloads can now override the saved config's worker count per-invocation instead of only at submit time.
- **`--no-verify-ssl` on downloader** (`cli/main.py`, `config/defaultconfig.py`, `downloader/asf_base.py`) — new `ssl_verify` config field (default `True`), applied to every `ASFSession` the downloader creates (interactive login, `.netrc` login, and per-thread search sessions), to bypass TLS verification when ASF's certificate has expired or is otherwise untrusted.
- **`--worker` on `processor watch` (HyP3)** (`cli/main.py`) — the standalone `download` action already let you override the saved config's parallel-download-worker count per-invocation; `watch` (which also downloads, as jobs succeed) had no equivalent flag and always ran with `Hyp3_S1_Config`'s default of 4, regardless of what was set at submit time. Added `--worker INT`, threaded into `_load_hyp3_processor(..., max_workers=...)` the same way `download` already does it.

### Bug Fixes

- **`Hyp3_SBAS` and `ISCE_SBAS` silently share/overwrite the same `mintpy/` directory when pointed at the same workdir** (`test/e2e/cli_e2e_isce_mintpy.sh`, `test/e2e/api_e2e_isce_mintpy.py`) — `MintPyPaths.mintpy_dir` (`config/paths.py`) is `workdir / "mintpy"` with no analyzer-type awareness, so both analyzers derive the identical output directory when run against the same workdir — `ifgramStack.h5` (same filename either way, despite very different content: HyP3's geocoded 3661×2899 vs ISCE's radar-coordinate 3437×3281), `smallbaselineApp.cfg`, `velocity.h5`, `timeseries.h5`, `pic/`, etc. all silently collide, with whichever pipeline ran most recently overwriting the other's results with no error or warning. `cli_e2e_hyp3_mintpy.sh` and `cli_e2e_isce_mintpy.sh` (plus their API equivalents) both defaulted to the same `p100_f466/` workdir, hitting exactly this — likely the real explanation for a confusing, hard-to-reproduce `'STARTING_RANGE'` `KeyError` seen partway through this investigation. Fixed the e2e tests by giving the ISCE variant its own default workdir (`p100_f466_isce/`). The underlying core issue (no analyzer-type separation in `MintPyPaths`) remains for any real user who processes the same AOI with both `Hyp3_SBAS` and `ISCE_SBAS` against one workdir — deliberately not changed here, pending a decision on the right fix (e.g. `workdir/mintpy_hyp3/` vs `workdir/mintpy_isce/`).
- **A clip raster left behind by an interrupted `Hyp3_SBAS.prep_data()` run was silently skipped forever on every subsequent rerun** (`analyzer/hyp3_sbas.py`) — `_clip_rasters()`'s skip-existing check was a bare `if out.exists(): continue`, with no validation that the file was actually a complete, valid raster. If a prior clip write was ever interrupted (crash, Ctrl+C, disk full, an earlier bug elsewhere in the process) it left a corrupt/truncated file sitting at the final output path; every later `prep_data()` rerun then saw "it exists" and skipped regenerating it, so the corrupt file (which GDAL/MintPy's `prep_hyp3.py` would reject with `ERROR 4: ... not recognized as being in a supported file format`, then crash with `'NoneType' object has no attribute 'RasterXSize'`) persisted indefinitely. There was also no atomic-write protection — clipped output was written directly to its final filename rather than a temp path, so an interruption *during* a clip left a broken file at that same final path. Fixed: added `_is_valid_raster()` (tries `rasterio.open()`, treats any failure as invalid) and only skip a pre-existing file if it passes that check — otherwise it's deleted and re-clipped; writes now go to a `.part` temp file and are renamed to the final path only on success, matching the same pattern already used by `Hyp3Base.download()`'s ZIP-validity check.
- **Neither the CLI nor the GUI ever actually generated `mintpy/pic/` figures, even after the previous `plot_result()`/`close()` fix** (`analyzer/mintpy_base.py`, `commands/analyzer.py`, `commands/__init__.py`, `cli/main.py`, `app/routes/analyzer.py`) — that fix made `run()` auto-plot when called with more than one step (`len(run_steps) > 1`, mirroring MintPy's own CLI semantics), but both `cli/main.py`'s `_az_run()` and the GUI's `_run_analyzer()` execute every requested step **one at a time** (`AnalyzeCommand(analyzer, steps=[step]).run()` / `analyzer.run(steps=[step])`) for per-step progress reporting — so `len(run_steps)` is always 1 there, and that condition can in practice only ever fire for a direct multi-step Python API call, never through the CLI or GUI. Separately, the GUI's step checklist already listed a `'plot'` entry, but selecting it was a silent no-op: MintPy's own `TimeSeriesAnalysis.run()` ignores any step name it doesn't recognize, and `'plot'` isn't a real MintPy step. Fixed with a new `Mintpy_SBAS_Base_Analyzer.plot()` method (open → `plot_result()` → close, independent of `run()`/step count) plus a new `PlotCommand` wrapper: the CLI now recognizes `plot` as a `--step` keyword (auto-added after more than one real MintPy step, or usable standalone — including through `--hpc-mode`, since it's a real token that survives the sbatch job's own re-invocation of this same command) and the GUI auto-appends it to the executed step list under the same condition, or runs it directly if explicitly selected in the checklist.
- **Default MintPy run skipped the `correct_unwrap_error` step** (`analyzer/mintpy_base.py`, `analyzer/isce_sbas.py`, `cli/main.py`, `app/routes/analyzer.py`, `docs/quickstart/cli.md`/`.zh.md`, `docs/advanced/frontend.md`/`.zh.md`) — InSARHub's default 17-step list omitted `correct_unwrap_error` entirely, one step short of MintPy's own real `STEP_LIST` (18 steps), so phase-unwrapping-error correction never ran unless a caller explicitly listed it via `--step`. Added it in the correct position (between `quick_overview` and `invert_network`) to both analyzers' default `run_steps`, the CLI's `_MINTPY_ALL_STEPS` (backs both the `--step` help table and the `all` shorthand expansion), the GUI's `_MINTPY_STEPS` (backs the `/api/analyzer-steps` step checklist), and the corresponding doc tables (which had also been missing `quick_overview` in `cli.md`/`.zh.md` — fixed alongside).
- **MintPy analysis produced no `mintpy/pic/` figures, and left the process permanently `cd`'d into the analysis folder** (`analyzer/mintpy_base.py`, `analyzer/isce_sbas.py`) — both `run()` overrides called `TimeSeriesAnalysis.open()` + `.run(steps=...)` directly instead of going through MintPy's own CLI entry point (`smallbaselineApp.run_smallbaselineApp()`), which does two more things afterward: calls `app.plot_result()` (the actual generator of every figure under `pic/`, gated on `template['mintpy.plot']` — already defaulted to `'yes'` in `Hyp3_SBAS_Config`/`ISCE_SBAS_Config` — and `len(run_steps) > 1`) and `app.close()` (which `os.chdir()`s back to the original directory — `open()` unconditionally `os.chdir()`s *into* `mintpy_dir` and nothing ever changed it back). Skipping `close()` wasn't just cosmetic: in the long-running API/GUI server process, every MintPy run permanently left the whole process's working directory pinned inside whatever folder was last analyzed, silently affecting any other relative-path logic in the same process. Fixed: both `run()` methods now call `plot_result()` under the same condition MintPy's own CLI uses, and call `close()` in a `finally` block so the working directory is restored even if a step raises partway through.
- **In some conda environments, every `pyproj` coordinate transform failed with `CRSError: ... no database context specified`** (`__init__.py`) — in certain conda-forge package combinations, pyproj's compiled `_crs` extension dynamically links against the environment's own shared `libproj.so` rather than pyproj's private bundled copy, but pyproj's default data-directory resolution still points at that unused private copy — a mismatch that breaks every `CRS`/`Transformer` call, even though the environment's actual PROJ install (verified against its own `projinfo` CLI) is completely fine, and setting `PROJ_DATA`/`PROJ_LIB` has no effect (pyproj doesn't honor either at the point its internal context is first created — only calling `pyproj.datadir.set_data_dir()` from Python does). Fixed: at import time, `insarhub` now points pyproj at the running interpreter's own `<prefix>/share/proj` directory (via `sys.prefix`, not `$CONDA_PREFIX` — the latter only reflects the shell's *activated* environment and can be wrong if a different env's Python is invoked directly without activating it) whenever that directory actually contains a `proj.db`; a no-op in environments where this bug doesn't occur.
- **HyP3 `watch` deadlocked the moment any job succeeded — download never started** (`cli/main.py`) — `_proc_watch()` called `processor.download()` from *inside* a `with tqdm.external_write_mode(...)` block. `external_write_mode()` acquires tqdm's process-global write lock for its entire `with` body; `download()` spawns a `ThreadPoolExecutor` whose worker threads each open their own per-file `tqdm` progress bar, which also needs that same global lock to initialize/update. Result: the main thread holds the lock waiting for the download workers to finish, while every worker blocks trying to acquire that same lock just to report progress — a guaranteed deadlock on the very first successful job, confirmed live via `ss -tnp` (real response bytes queued unread in the kernel socket buffer while every thread sat in `futex_do_wait`). Fixed by moving the `download()` call to after the `with` block exits, so the lock is released before any worker thread needs it.
- **HyP3 `watch` silently swallowed the real cause of a stuck refresh, and could permanently skip a failed download** (`cli/main.py`) — two related issues in `_proc_watch()`: (1) `processor.refresh()`'s stdout/stderr were redirected into a `StringIO` to suppress its noisy per-job status table, but the buffer was never read back, so a genuine per-user refresh failure (bad credential-pool entry, network error — printed internally by `Hyp3Base.refresh()`'s own `except Exception: print(...); continue`) vanished with zero indication anything was wrong, leaving the progress bar frozen at `0/0` with no explanation. (2) job IDs were added to the "already downloaded" tracking set *before* `download()` even ran, so a transient download failure (network blip, disk error) permanently skipped that job on every later iteration even though its ZIP never landed. Fixed: failure lines matching `"Failed to refresh"` are now surfaced via `tqdm.write()` instead of being discarded, and job IDs are only marked handled once `download()` reports zero failures for that round — otherwise they're left unmarked so the next iteration retries them.
- **Search silently dropped every scene acquired on the exact end date** (`downloader/asf_base.py`) — both the GUI's `<input type="date">` and typical CLI usage pass a bare `YYYY-MM-DD` string for `end`; handed to `asf_search` as-is, `dateparser` parses that as midnight (`00:00:00`) of that day, so a search for `end=2021-06-01` actually only included results up to `2021-06-01T00:00:00Z` — excluding essentially every acquisition later that same day, even though the user meant "through the end of that day." The same off-by-one existed independently in `downloader.filter()`'s own `end` comparison. Fixed with a new `_end_of_day()` helper (appends `T23:59:59` to a bare date, leaves anything with a time component untouched), applied both before `search()` hands `end` to `asf.search()` and inside `filter()`'s `end_dt` parsing. Added regression tests (`test_downloader.py::TestEndOfDay`, `TestFilterEndDateInclusive`).
- **`container` was never actually excluded from persisted config, despite the code comment above it** (`cli/main.py`) — `_RUNTIME_ONLY_FIELDS` was defined twice at module level with different contents; the second definition silently shadowed the first, so `_skip_write`'s reference to the name (used by `_proc_local_submit`/`_proc_local_retry` when writing `insarhub_config.json`) picked up the wrong set at call time. Renamed the second, unrelated definition to `_ROLE_CONFIG_STRIP_FIELDS` and refactored its two callers (`_read_dl_config_from_folder`, `_read_proc_config_from_folder`) to share `read_insarhub_config()` instead of hand-rolling JSON reads.
- **`--container` didn't actually work without ISCE2 installed on the host** (`processor/isce_base.py`) — `ISCE_Base.__init__` discovered ISCE2 unconditionally, so constructing `ISCE_S1` (before `submit()`/`retry()`'s container short-circuit was ever reached) failed with "ISCE2 not found" on a host with no local ISCE2 — exactly the case `--container` exists to cover. Fixed: ISCE2 discovery is now skipped entirely when `container` is set (the container brings its own ISCE2/topsStack install; nothing on the host needs discovering).
- **Missing MintPy/ISCE2 raised bare `ModuleNotFoundError`s instead of actionable errors** (`analyzer/mintpy_base.py`, `analyzer/isce_sbas.py`, `processor/isce_base.py`) — running `Hyp3_SBAS`/`ISCE_SBAS` without MintPy installed hit a raw `No module named 'mintpy'` at the point of use, with no guidance. New `_require_mintpy()` helper in `mintpy_base.py` (used by both `run()` overrides) raises a clear message instead: *"MintPy is required to run this analyzer, but isn't installed. Install it locally: pip install insarhub[mintpy]. Or run this analyzer inside a container instead: ...".* `ISCE_S1`'s existing `_check_isce2`/`_find_topsstack` `EnvironmentError`s (already reasonably specific) now append the same `--container` recommendation. CLI (`_fail()`) and GUI (`_run_analyzer`'s per-step error handling) both already surface exception messages directly to the user, so these friendlier messages propagate all the way through without further changes.
- **`networkx` was an undeclared hard dependency, missing entirely from `pyproject.toml`** (`utils/tool.py`) — `plot_pair_network()` (backing the base, non-optional `insarhub utils plot-network` CLI command) imported `networkx` at module level; it happened to work in every environment used during this project's development because something else always pulled it in transitively, but a genuinely fresh `pip install insarhub` crashed immediately on `import insarhub` with `ModuleNotFoundError: No module named 'networkx'`. Only caught by testing against an isolated, from-scratch virtualenv rather than a pre-populated dev environment. Fixed: moved the import to be lazy (inside `plot_pair_network()` itself, matching this codebase's established lazy-import pattern for ISCE2/MintPy/GDAL) and added `networkx` as a proper declared base dependency.
- **`test/conftest.py`'s MintPy stub silently required `dask`** (`conftest.py`) — the shared test stub makes `import mintpy` succeed (via a `MagicMock`) so tests can exercise MintPy-present code paths without the real package installed; this had the side effect of making `insarhub/__init__.py`'s `_has_mintpy` check evaluate `True` during test collection, which then tried a *real* `from dask import config` (only skipped when MintPy is genuinely absent) — breaking test collection in any environment without `dask` actually installed. Fixed: added `dask` to the stub list. (`h5py` was deliberately *not* added the same way — `postprocess.py`'s `h5_to_raster()` needs the real package, since `test_utils_postprocess.py` builds genuine HDF5 fixtures to test it.)
- **`--step` silently submitted no job** (`isce_base.py`) — forcing a step via `--step` reset its step-level `.status` file but left stale per-command `cmd_XXXX.done`/`.fail` marker files in place from a prior (possibly silently-failed) run. The manager sbatch script's sliding-window logic checks these markers directly, independent of step-level status, so it reported "all commands already done" and submitted nothing. Fixed: added `_clear_step_markers()`, called whenever a step is forced via `--step`, which removes stale `.done`/`.fail` markers across all three possible layouts (`{step}_logs/`, `{step}_sbatch/`, `{step}_group/`) before resubmission.
- **`set -uo pipefail` masked failed commands as succeeded** (`isce_base.py`) — the single-step manager sbatch script used `set -uo pipefail`; with `nounset` enabled, referencing an empty associative array elsewhere in the script aborted the script *before* the failure-handling logic ran, and the manager's outer retry/tracking loop then read the absence of a `.fail` marker as success. This produced empty output files (e.g. `computeBaseline` baseline `.txt` files) that were recorded as `SUCCEEDED`. Fixed: `set -o pipefail` (dropped `-u`), matching the group-manager script which already omitted it.
- **`max_workers` hidden from `--list-options`/config overrides** (`cli/main.py`) — `max_workers` was listed in `_SUBMIT_SKIP_FIELDS`, so it never appeared as a settable CLI flag or in `--list-options` output for processor submit. Fixed: removed from the skip set.
- **Unknown-flag filter misclassified negative numbers as flags** (`cli/main.py`) — argparse parsers with `type=int`/`type=float` options set `_has_negative_number_optionals=True`, causing tokens like `-105.51` (e.g. a bbox coordinate) to be treated as unrecognized flags and rejected with `[ERROR] Unknown flags: ...` instead of being parsed as values. Fixed: added `_filter_unknown_flags()` to drop standalone negative-number tokens before the unknown-flag check, applied across `downloader`, `processor submit`, and `analyzer run`.
- **`ssl_verify` leaked into ASF search request** (`downloader/asf_base.py`) — the new `ssl_verify` config field (see `--no-verify-ssl` above) wasn't excluded from the dict built from `asdict(self.config)` and passed as search kwargs to `asf.search()`, so every search request carried an unexpected `ssl_verify` parameter. Fixed: excluded alongside `workdir`/`name`/`bbox`/`granule_names`.
- **Analyzer `--hpc-mode` dropped `prep_data` from the submitted job** (`cli/main.py`, `mintpy_base.py`) — the HPC-mode check ran *after* `prep_data` had already executed locally, and only `mintpy_steps` (not `prep_data`) were passed to `submit_hpc()`, so `prep_data` always ran outside SLURM even in HPC mode. Fixed: HPC check moved before `prep_data` execution; `submit_hpc()` now accepts a `steps` list that includes `prep_data` when requested and threads it through as `--step` on the submitted job's `run` invocation.
- **Analyzer HPC sbatch job dropped config overrides** (`mintpy_base.py`) — the sbatch script's `run` command only carried `--step`; any non-default config value set at submit time (e.g. `--compute-maxMemory`, `--geocode-SNWE`) was silently lost, so the job running on the compute node used default config instead. Fixed: `submit_hpc()` now diffs the resolved config against its dataclass defaults and serializes every non-default field back onto the command line (bools as bare flags, lists/dicts JSON- or space-joined).
- **`--list-options` skipped output entirely when `.mintpy.cfg` was missing** (`cli/main.py`) — printed a warning and skipped the analysis dir instead of showing config defaults, unlike every other `--list-options` path. Fixed: falls through with empty overrides so defaults still print when the cfg file doesn't exist yet.
- **CLI merged download saved orbit files to the wrong directory** (`cli/main.py`) — `--merge --download --orbit-files` computed the orbit save location as a hardcoded `workdir / "merged"`, but `asf_base.download(merge=True)` actually writes SLCs to `workdir/p{path}_{merge_tag}/slc/` (via `StackPaths.merge_dir`, which encodes every constituent frame number). Orbit files landed in a different, empty folder than the SLCs. Fixed: computes the same `merge_dir` from `downloader.active_results` (path + frames), matching `asf_base`'s internal logic.
- **Merge search silently restricted to one satellite platform** (`app/routes/search.py`, `app/frontend/src/StackSummaryDrawer.tsx`, `app/frontend/src/App.tsx`, `app/frontend/src/ScenePanel.tsx`) — a `(path, frame)` group can legitimately span a satellite handover (e.g. Sentinel-1C → Sentinel-1D on the same track), but `platform` was derived from a single representative scene instead of aggregated across the whole stack: `StackSummaryDrawer.tsx` set it only from the first GeoJSON feature seen per stack key, and `add_merged_job`/`_run_download_merged` then used that single value (`first.platform`/`spec.platform`) to filter the *entire* merged group's re-search, silently starving it to whichever satellite happened to be first/newest. The regular non-merged single-stack flow (`ScenePanel.tsx`'s `handleAddJob`/`handleDownloadStack`/`handleDownloadOrbit`) had the identical bug via `feature.properties.platform` from whichever single scene was clicked on the map. Fixed: `platform` is no longer sent as a search filter from any of these flows; `StackSummaryDrawer.tsx` now aggregates every distinct platform seen per stack (comma-joined) instead of freezing on the first; `App.tsx` computes a `stackPlatform` aggregate across all scenes in a stack, and `ScenePanel.tsx`'s "Stack Info" panel now displays that aggregate instead of one scene's platform.

### GUI

- **Map longitude unbounded past the antimeridian** (`app/frontend/src/Map.tsx`) — map clicks, box-draw AOI selection, mouse-move coordinate readout, and raster pixel lookup all read `e.lngLat.lng` directly, which grows unbounded (e.g. past ±180°) when the map is panned repeatedly across the antimeridian. Fixed: all four call sites now use `e.lngLat.wrap().lng` to normalize back into the ±180° range.
- **Merged-stack download endpoint** (`app/routes/search.py`) — new `POST /api/download-merged` endpoint (backing the CLI's `--merge`) downloads multiple stacks into a shared `workdir/merged/slc/` directory as a single background job.
- **Long merged-folder names overflowed instead of truncating** (`app/frontend/src/JobQueueDrawer.tsx`) — the level-2 folder-list button, the file-row span, and the workflow-detail "Folder:" header all had `overflow: hidden`/`textOverflow: ellipsis` but were missing `minWidth: 0` on the flex child — a flexbox gotcha where a `flex: 1` item won't shrink below its content's natural width without it, so `text-overflow: ellipsis` silently did nothing for long names (e.g. `p151_merged_f109_f112_f113_...`). Fixed in all three spots; full name still available via `title` tooltips.
- **Language switcher showed the target language instead of the current one** (`app/frontend/src/TopBar.tsx`) — the button displayed `'EN'` while already in Chinese mode (i.e. the language you'd switch *to*) and `'中'` while in English mode, backwards from the usual convention of a language toggle showing where you currently are. Swapped so it now shows `'EN'` under English mode and `'中'` under Chinese mode.

### New Features

- **`GMTSAR_S1` processor** (`processor/gmtsar_s1.py`, `config/defaultconfig.py`) — new local processor backend wrapping [GMTSAR](https://github.com/gmtsar/gmtsar)'s Python `p2p_processing`/`p2p_S1_TOPS_Frame` pipelines to generate Sentinel-1 interferograms, selected via `config.frame_mode`: single-subswath (`p2p_processing`, default) or multi-subswath merged (`p2p_S1_TOPS_Frame`). Both modes take the same `pairs = [(ref_safe, ref_eof, sec_safe, sec_eof), ...]` shape — single-subswath mode extracts the configured IW subswath + polarization from each `.SAFE` scene itself (mirroring GMTSAR's own per-subswath test fixture layout), so callers only ever hand it raw `.SAFE`/`.EOF` names. Requires GMTSAR installed in its own environment; `gmtsar_root`/`gmtsar_env_bin` config fields bridge InSARHub's and GMTSAR's separate conda environments for subprocess calls. Real end-to-end validated (both modes) against real Sentinel-1 data — see `docs/gmtsar_s1_notes/OPEN_ISSUES.md`.
- **ERA5-Land supplementary weather features** (`utils/pair_quality/_weather_era5.py`) — new CDS API-based extractor providing variables not available via Open-Meteo: dewpoint, 7-28 cm and 28-100 cm soil moisture, skin temperature, wind speed, net radiation, high/low-vegetation LAI, and snowmelt, spatially averaged over ERA5-Land 0.1° grid cells intersecting the AOI. Requires `~/.cdsapirc` with valid Copernicus CDS credentials.
- **Downloader-agnostic search filter schema** (`core/base.py`, `downloader/s1_slc.py`, `app/routes/search.py`, `app/models.py`, `app/frontend/src/SearchFilters.tsx`) — new `search_filter_schema` class attribute on `BaseDownloader`: each downloader declares its own extra search fields (name, UI kind, choices, section) instead of the GUI hardcoding one downloader's fields. New `GET /api/downloader-schema` endpoint exposes it; `SearchFilters.tsx`'s "Additional Filters"/"Path and Frame Filters" sections now render generically from whatever the selected downloader reports (`SchemaFieldInput` handles `select`/`range`/`number`/`text` kinds). `SearchRequest`/`_run_search` no longer hardcode `S1_SLC_Config` — the downloader class and its config are looked up from the registry, and a generic `overrides` dict is applied via the existing `_apply_config_from_dict()` helper. `S1_SLC` declares Flight Direction + Platform (`select`) and Path + Frame (`range`, mapped to `relativeOrbit`/`asfFrame`). Adding a future downloader (e.g. NISAR) with different filter needs requires no frontend changes.
- **Manual granule-name entry** (`app/frontend/src/SearchFilters.tsx`) — new textarea under "Granule Names" lets users type/paste scene names directly (one per line, or comma/space-separated) instead of only uploading a file. Shares the same name-parsing logic (`parseNamesText()`) as the existing CSV/TXT upload path; uploading a file also populates the textarea so either method stays visible/editable in one place.
- **`--container` for ISCE_S1/MintPy** (`utils/container.py`, `processor/isce_base.py`, `processor/isce_s1.py`, `analyzer/mintpy_base.py`, `analyzer/isce_sbas.py`, `analyzer/hyp3_sbas.py`, `config/defaultconfig.py`, `cli/main.py`) — new `container` config field on `ISCE_S1_Config`/`Mintpy_SBAS_Base_Config` (and CLI flag/GUI field via the existing schema-driven mechanisms, no new argparse or frontend code) accepting a path to an Apptainer/Singularity `.sif` image or a Docker image reference. When set, `ISCE_S1.submit()`/`retry()` and the MintPy analyzers' `run()`/`prep_data()`/`submit_hpc()` re-invoke the same `insarhub processor/analyzer ... ` CLI command inside the container (bind-mounting the workdir at an identical path) instead of running on the host — the container image needs `insarhub` installed alongside the underlying tool. `refresh()`/`watch()`/`cancel()` need no changes since they only read the same shared status files the container-side process writes. Not persisted to `insarhub_config.json` (pass again for subsequent retry/submit calls, like `--dry-run`).
- **`Dockerfile`/`Dockerfile.local` for `--container`** (`Dockerfile`, `Dockerfile.local`, `utils/container.py`, `processor/isce_base.py`) — ready-to-build images with insarhub + ISCE2 + MintPy for use with `--container` (`Dockerfile` installs insarhub from PyPI; `Dockerfile.local` from this repo's local `src/`, for testing unreleased changes). Fixed along the way: (1) an `ENTRYPOINT` collided with `wrap_container_cmd`'s own `bash -c '<cmd>'` invocation, silently no-op'ing every container run; (2) `--user UID:GID` left `$HOME` unresolvable inside the container (no matching `/etc/passwd` entry), breaking anything writing to `~` — fixed via `-e HOME=<bind_dir>`; (3) `_start_local_background()` forking *again* inside the container (on top of `_reinvoke_via_container`'s own host-side fork) let the container's main process exit immediately, so `docker run --rm` tore the whole container down before the actual work ran — fixed via `INSARHUB_CONTAINER_CHILD`, which tells the container-side process to run synchronously instead; (4) step status recorded `os.getpid()` from inside the container's own PID namespace, which is meaningless to a host-side `os.kill(pid, 0)` liveness check in `refresh()` (confirmed this holds even with `--pid=host` on Docker Desktop/WSL2, since its containers run inside Docker Desktop's own VM) — fixed via `INSARHUB_HOST_PID`, the real host-side PID blocking on `docker run`, passed into the container and recorded instead. Also added `--container` to the `refresh`/`watch`/`cancel` CLI subparsers (previously only `retry` had it) and threaded it through their handlers — without it, simply constructing an `ISCE_S1`/`ISCE_Base` processor for those actions unconditionally attempted host ISCE2 discovery and failed on a host with none installed, even though those actions never actually need ISCE2 themselves.
- **`osgeo`/GDAL removed from InSARHub's own code** (`analyzer/hyp3_sbas.py`, `utils/batch.py`, `app/routes/render.py`) — `Hyp3_SBAS`'s two raster-clipping methods (`_get_common_overlap`, `_clip_rasters`) called `osgeo.gdal` directly (`gdal.Open`/`GetGeoTransform` for bounding boxes, `gdal.Translate` for windowed clipping). Rewrote both to use `rasterio` (already a base dependency, ships GDAL statically bundled in its own pip wheel) via `dataset.bounds` and `rasterio.windows.from_bounds()` + windowed read/write. Verified byte-for-byte identical output (bounds, shape, pixel values) against the old `gdal.Translate` behavior on synthetic overlapping GeoTIFFs. Also removed an unused module-level `from osgeo import gdal` in `utils/batch.py` (dead code), and a dead `except ImportError: from osgeo import gdal, osr` fallback in `app/routes/render.py`'s `_tif_bounds_wgs84()` (unreachable since `rasterio`, tried first, is a hard base dependency). Note: this only removes InSARHub's *own* direct GDAL usage — MintPy itself still requires a real `osgeo` install for its HyP3 data-loading path (`mintpy.utils.readfile.read_gdal_vrt()`); GDAL is not, and cannot be, fully eliminated from the HyP3→MintPy pipeline. Install it via `conda install -c conda-forge gdal` (with a matching `numpy<2.0` pin — see the `environment.yml`/`environment-isce2.yml` fix below).
- **`environment.yml` let conda silently upgrade numpy to 2.x, breaking MintPy/GDAL** (`environment.yml`) — `numpy <2.0` was listed under the `pip:` sub-section, which only takes effect *after* conda's own solver has already resolved and installed `gdal`/`mintpy` — with no numpy ceiling visible to it — potentially against numpy 2.x. Pip then downgrading numpy afterward doesn't recompile those already-installed conda packages' compiled extensions, leaving them ABI-linked against a numpy version no longer present. Fixed: moved `numpy <2.0` into the top-level conda `dependencies:` list, so conda's solver respects the ceiling from the start.
- **`environment-isce2.yml` removed** (`environment-isce2.yml` deleted; `Dockerfile`, `Dockerfile.local`, `README.md`, `docs/quickstart/install.md`/`.zh.md`, `test/e2e/cli_e2e_isce_mintpy.sh`, `test/e2e/api_e2e_isce_mintpy.py`) — consolidated to a single `environment.yml`; ISCE2 is now added as a separate step (`conda/mamba install -c conda-forge "numpy<2.0" isce2`) on top of an already-created `environment.yml` env, matching the pattern the docs already used for the non-dev-setup install path. The explicit `numpy<2.0` on the follow-up install keeps the solver from re-resolving numpy upward when adding isce2 to an existing env.

### Refactor

- **Deduplicated CLI/GUI pair-quality logic** (`utils/stack_io.py`, `utils/pair_quality/_cache.py`, `utils/pair_quality/_geom.py`) — new `stack_io.py` module (`write_stack_file()`, `merge_db_scores_into_stack()`) replaces ~95 lines of logic duplicated between `cli/main.py` and `app/routes/folders.py`. New shared `seed_prefetch()` replaces `_seed_cli_cache()`/`_seed_pair_quality_cache()`. New `footprint_wkt_from_products()` extracts a shapely union that appeared twice in `routes/folders.py`. Architecture rule enforced: CLI and GUI layers do no computation; all calculation now delegated to `utils/`.
- **Removed `S1_Burst` downloader** (`downloader/s1_brust.py` deleted, `downloader/__init__.py`, `insarhub/__init__.py`, `config/__init__.py`, `config/defaultconfig.py`) — unregistered and deleted; no longer available in the GUI dropdown, `--list-downloaders`, or the registry at all. No processor/analyzer declared `compatible_downloader="S1_Burst"`, so nothing else depended on it.
- **Backend dead-code sweep + shared helpers** (`cli/main.py`, `commands/base.py`, `commands/downloader.py`, `commands/processor.py`, `commands/analyzer.py`, `app/state.py`, `app/routes/processor.py`, `app/routes/analyzer.py`, `app/routes/folders.py`, `app/routes/search.py`, `app/routes/render.py`, `app/routes/quality.py`, `app/models.py`) — full audit of the CLI/API/GUI for unreachable code and duplicated logic. Removed: 7 API routes with no frontend caller and no test coverage (`/api/folder-image`, `/api/folder-pairs-candidates`, `/api/download`, `/api/download-by-name`, `/api/serve-tif`, `/api/pair-quality-db/build`, `/api/folder-pairs-files`), their now-unused request models, and several unreachable CLI functions/classes (`ResetCommand`, `DEMCommand`, `WatchCommand`, `_str_to_bool`, `_write_config_json`, `_generate_consecutive_pairs`) — `/api/auth-status` was flagged by the same heuristic but kept after finding it's exercised directly by `test_api_routes.py`/`test_insarhub.py`. New shared helpers eliminate repeated boilerplate: `commands/base.py`'s `@safe_command` decorator replaces 15 duplicated try/except-wrap blocks across every `Command.run()`; `app/state.py` gained `launch_job()`, `stop_event()` (contextmanager), `_cfg_dict()`, `_read_processor_type()`, and `_make_download_progress()`, applied across all four route modules to replace the repeated job-launch/stop-event/config-dict-building skeleton at ~14 call sites; `cli/main.py` gained `_apply_config_overrides()` (replacing a duplicated override-collection loop across `cmd_downloader`/`_proc_submit`/`_proc_local_submit`/`_az_run` — and in the process fixing a real divergence where `_proc_local_submit` was missing a subfolder-config fallback branch `_proc_submit` already had) and `_iter_job_entries()` (replacing an identical job-directory-scanning loop duplicated across `_proc_refresh`/`_proc_download_results`/`_proc_retry`/`_proc_watch`).
- **Frontend dead-code sweep + shared components** (`api.ts`, `theme.ts`, `Drawer.tsx`, `useCopyFeedback.ts`, `useFetchJson.ts`, `JobQueueDrawer.tsx`, `ScenePanel.tsx`, `SceneDetailPanel.tsx`, `StackSummaryDrawer.tsx`) — removed three unused components (`SearchBar.tsx`, `StatusBar.tsx`, `DrawToolbar.tsx`, zero imports anywhere) and a dead `network_image` field. New shared modules replace repeated patterns: `api.ts` exports a single `API` base-URL constant (previously redefined in 8 files, including one file with a subtly different form); `theme.ts` gained `statusColor()` for the repeated `status === 'done' ? green : status === 'error' ? red : fallback` ternary (applied everywhere it appeared byte-for-byte identically; more elaborate multi-property button-styling blocks with per-call-site fallback colors were deliberately left alone rather than force-fit, to avoid visual-regression risk); new `Drawer.tsx` (`DrawerShell`/`DrawerHeader`) replaces the outer fixed-panel/resize-handle/header boilerplate copy-pasted across all 7 job-queue side drawers plus the main drawer in `JobQueueDrawer.tsx`; new `useCopyFeedback()` hook replaces 3 hand-rolled copy-to-clipboard-with-timeout implementations; new `useFetchJson()` hook replaces 2 call sites whose fetch/loading/error effect was byte-for-byte identical (other `useEffect`+fetch sites had real differences — extra state resets, multi-fetch `Promise.all`, polling — and were left as-is).

## [0.3.2] - 2026-06-09

### New Features

- **HPC sliding-window submission** (`isce_base.py`) — each step now runs a lightweight sbatch manager job that keeps ≤`max_concurrent_hpc` child jobs active at all times, refilling immediately on completion. Replaces the old batch-sequential approach. Consecutive steps with equal command counts (e.g. `run_13`–`run_16`) are merged into a single group-manager. Steps are chained via `--dependency=afterok`.
- **Per-command elapsed time in sbatch logs** (`isce_base.py`) — sbatch scripts print `START`/`DONE`/`FAIL` with elapsed seconds. Group tasks also print total elapsed across all grouped steps.

### Bug Fixes

- **HyP3 file paths** (`hyp3_base.py`) — `hyp3_jobs.json` now saves to workdir root (was `workdir/hyp3/`); downloaded ZIPs go to `workdir/hyp3/`; retry job files save to workdir root; legacy `out_dir=workdir` entries auto-migrate to `workdir/hyp3/`.
- **`watch` command ignores `--interval` flag** (`cli/main.py`) — `_proc_local_watch` read `args.refresh_interval` but argparse stores it as `args.interval`; interval was always 60 s regardless of user input. Fixed.
- **`insarhub processor refresh/download` re-processes retry job files** (`cli/main.py`) — `_find_job_files` globbed `hyp3*.json`, matching `hyp3_retry_jobs_<ts>.json` files from past runs alongside `hyp3_jobs.json`. On refresh/download, stale retry files were loaded as separate processors, causing duplicate downloads or incorrect status. Fixed: retry files excluded from glob.
- **Orbit files downloaded twice** (`cli/main.py`) — `-d -O` flags triggered two orbit downloads. Fixed: skips explicit `download_orbit()` call when downloader already handled it.
- **`retry()` runs locally after HPC submission** (`isce_base.py`, `cli/main.py`) — `hpc_mode` excluded from saved config, so retry defaulted to local. Fixed: `retry()` auto-detects HPC from job metadata (`slurm_job_ids`/`hpc_manager`/`hpc_array`) and writes it back to `config.hpc_mode` so `_step_executor()` routes correctly. `_load_local_processor` also restores `max_concurrent_hpc` and HPC config fields from `insarhub_config.json`.
- **Manager job killed mid-run leaves step stuck PENDING** (`isce_base.py`) — `elif n_cmds > 0 and not job_ids:` was dead code (always False inside `if job_ids:` block); SLURM-killed managers with incomplete commands never resolved to FAILED. Fixed: condition is now `elif n_cmds > 0:`.
- **`_parse_time_s` mis-parses 2-part SLURM time strings** (`isce_base.py`) — `"30:00"` was treated as 30 h 0 min (108,000 s) instead of 30 min 0 s (1,800 s), overestimating group-manager walltime 60×. Fixed: 2-part strings now parsed as MM:SS per SLURM spec.
- **Group-manager step stuck PENDING when job gone from SLURM** (`isce_base.py`) — if `group_task_dir` was absent from saved metadata (old jobs), `n_cmds` resolved to 0, preventing the SUCCEEDED/FAILED transition. Fixed: `n_cmds` now stored in job metadata at submission; refresh uses it directly with file-count as fallback.
- **`refresh()` shows only one RUNNING command in manager mode** (`isce_base.py`) — with sliding-window, multiple commands run concurrently but only one showed RUNNING. Fixed: per-command status now derived from `.done`/`.fail` files; all in-flight commands show RUNNING.

### GUI

- **Hyp3_S1 `max_workers` in settings panel** (`defaultconfig.py`) — parallel download threads now configurable via Job settings group (default 4, range 1–16).

### Bug Fixes (additional)

- **`Hyp3_SBAS` MintPy output in workdir root** (`mintpy_base.py`) — base class `run()` passed `self.workdir` to `TimeSeriesAnalysis`; `Hyp3_SBAS` inherits without override, so all MintPy outputs scattered to workdir root instead of `workdir/mintpy/`. Fixed: uses `self.mintpy_dir`; same correction for `_geocode_diagnostic_files`.
- **ISCE cleanup misses `merged/interferograms`** (`isce_sbas.py`) — cleanup targeted `isce/interferograms/` (nonexistent); real stackSentinel output is `isce/merged/interferograms/`. Large intermediate files were never deleted.
- **HyP3 auth failure submits to wrong user** (`hyp3_base.py`) — when re-auth failed for a pool user, `credits=0` was overwritten by `self.client.check_credits()` on the previous user's client; jobs were then submitted under the wrong account. Fixed: credits check guarded by auth result.
- **`self.batchs` updated per-loop-iteration** (`hyp3_base.py`) — on multi-user refresh, if any user failed, their batch was silently dropped from `self.batchs`. Fixed: assignment moved after loop.
- **Missing `filename` key in HyP3 file metadata crashes download** (`hyp3_base.py`) — direct dict subscript raised `KeyError` for auxiliary entries lacking `filename`. Fixed: `file_meta.get('filename')` with skip on empty.
- **delete_job_folder blocks on `~`-prefixed workdir** (`settings.py`) — `Path(workdir)` without `expanduser().resolve()` made `relative_to()` always raise `ValueError`, returning 403 on every delete. Fixed.
- **`_run_folder_select_pairs` uses `folder.parent` as workdir** (`folders.py`) — downloader config received parent directory instead of job folder; sub-paths written one level up, potentially colliding with sibling jobs. Fixed: `workdir=folder`.
- **Analyzer stop_event leaks on step error** (`routes/analyzer.py`) — early `return` on step exception bypassed `_stop_events.pop(job_id)`, leaking events indefinitely. Fixed: pop before return.
- **Refresh overwrites `.insarhub_cache.json` filenames with empty list** (`routes/processor.py`) — if no jobs had SUCCEEDED yet, `filenames=[]` overwrote a valid cache from a prior successful refresh. Fixed: preserves existing filenames when current refresh yields none.
- **Retry job files appear as selectable job entries in GUI** (`routes/processor.py`) — `hyp3*.json` glob matched `hyp3_retry_jobs_<ts>.json`; selecting one for refresh returned only the retry batch status. Fixed: retry files excluded.

### Refactor

- **Centralized path layout** (`config/paths.py`) — `Hyp3Paths`, `ISCEPaths`, `MintPyPaths` dataclasses replace all hardcoded `workdir / "subdir"` strings across `hyp3_base.py`, `isce_base.py`, `isce_s1.py`, `mintpy_base.py`, `hyp3_sbas.py`.
- **Path dataclass coverage extended** — remaining hardcoded path literals replaced with dataclass properties: `isce_sbas.py` now uses `ISCEPaths` for `isce_dir`, `slc_dir`, `dem_dir`; `cli/main.py` uses `Hyp3Paths`/`ISCEPaths` in `_has_zips` and `_find_job_file`; `utils/batch.py` and `utils/tool.py` use `Hyp3Paths.output_dir` for ZIP discovery.
- **`Hyp3Processor` renamed to `CloudProcessor`** (`core/base.py`) — ABC renamed to reflect generic cloud-backend semantics rather than HyP3 specificity. Updated across `core/__init__.py`, `__init__.py`, `processor/hyp3_base.py`, `commands/processor.py`, `cli/main.py`, `core/engine.py`.

### Docs

- **Contributing guide** — new tab in MkDocs navigation (EN + ZH). Split into Overview, Backend, and Frontend pages.
- **Backend contributing guide** — architecture overview, path conventions, per-section instructions for adding new processors/downloaders/analyzers. Each section includes a "Adding a New Base X" subsection (with code examples for `CloudProcessor`/`LocalProcessor`, `BaseDownloader`, `BaseAnalyzer`) and an "Extending an Existing Base X" subsection with switch tabs (`Hyp3Base`, `ISCE_Base`, `ASF_Base_Downloader`, `Mintpy_SBAS_Base_Analyzer`).
- **Frontend contributing guide** — conda Node.js install, uvicorn backend startup from InSARHub root, module reference tables grouped by area (Entry & Global, Map, Search & Scene Selection, Jobs & Results, Settings, Utilities), backend communication pattern, settings panel, Vite proxy, build output, code style.

---

## [0.3.1] - 2026-05-28

### Bug Fixes

- **Download result unpacking** (`commands/processor.py`) — `processor.download()` returns `(Path, dict)` tuple; `DownloadCommand` was assigning the whole tuple to `output_dir`. Fixed: now unpacks to `output_dir, dl_stats`. `CommandResult.data` now includes both output path and download stats.
- **HyP3 workflow marker wrong location** (`hyp3_base.py`) — `write_workflow_marker` was writing `insarhub_config.json` to `workdir/hyp3/` instead of the job folder root, so HyP3 tags never appeared in the job drawer. Fixed: writes to `config.workdir`.
- **Stale `out_dir` from saved job file** (`hyp3_base.py`) — old `hyp3_jobs.json` pointing to a pre-migration path outside current workdir would silently redirect output. Fixed: `out_dir` rejected if not under current workdir.
- **`wslpath` unchecked** (`settings.py`) — if `wslpath -w` failed, PowerShell was called with `-File ""`. Fixed: returncode + empty string guard added.
- **ZIP detection for `hyp3/` layout** (`cli/main.py`) — analyzer now checks `workdir/hyp3/*.zip` first, with fallback to `workdir/*.zip` for legacy layouts.

### Performance

- **Auth status parallel checks** (`auth.py`) — HyP3 credit check, CDSE, CDS, and Earthdata checks now run concurrently via `ThreadPoolExecutor` instead of sequentially. Typical improvement: 3–5× faster settings panel load.
- **Job folder listing SSH speed** (`settings.py`) — removed all per-folder `glob`/`exists`/`is_file` checks. Now reads only `insarhub_config.json` per folder. Significant speedup on remote filesystems.

### Source

- **`hyp3/` subdir awareness** (`hyp3_sbas.py`, `mintpy_base.py`, `batch.py`) — all ZIP lookups now check `workdir/hyp3/*.zip` first, falling back to `workdir/*.zip` for legacy layouts. Affected paths: `_unzip_hyp3`, `cleanup`, and `ERA5Downloader.download_batch`.
- **Missing `.mintpy.cfg` guard** (`mintpy_base.py`) — if `.mintpy.cfg` is not found when `run()` is called, a warning is printed and the config is written automatically rather than crashing downstream MintPy steps.
- **`write_mintpy_config` parent mkdir** (`defaultconfig.py`) — `outpath.parent.mkdir(parents=True, exist_ok=True)` added before opening the file, preventing `FileNotFoundError` when the output directory does not yet exist.

### CLI

- **`prep` alias** — `insarhub analyzer run --step prep` now accepted as alias for `prep_data`. Help text updated to show alias.
- **Default port** — `insarhub-app` now defaults to `8080` (was `8000`). Use `--port` to override.

### GUI

- **Subfolder navigation** (`JobQueueDrawer`) — click any folder to drill into subfolders; `↑` button to go up. Resets to workdir root on workdir change. Uses `/api/browse-subfolders` endpoint.
- **Cancel button** (`JobQueueDrawer`) — Cancel action added for local ISCE jobs.
- **Modern folder picker** (`settings.py`) — Windows/WSL now uses `IFileOpenDialog` COM API via embedded C# in PowerShell. Fixes: DPI blurriness on 2K monitors, Chinese character paths.
- Add nyan cat

### Network Graph (`utils/tool.py`)

- Node labels changed from last-8-chars to `YYYY-MM-DD` dates.
- Bottom axis: real acquisition dates. Top axis: days since first acquisition (swapped).
- Left graph title removed.
- Font sizes increased throughout; date labels rotated for readability.

### Docs

- Port references updated to `8080` across README, quickstart, and frontend docs.
- `file_structure.md/zh`: added `hyp3/` to directory layout; `out_dir` examples updated to `.../hyp3`.
- `cli.md/zh`: `--credential-pool` corrected from "JSON" to plain `username:password` text file; `prep` → `prep_data`.
- `index.md/zh`: satellite support table added; program structure section moved to new Advanced page with workflow diagram.

---

## [0.3.0] - 2026-05-14

### New Features

- **ISCE_S1 local processor**: New processor backend that runs ISCE2 `stackSentinel` locally. Supports sequential local execution and SLURM HPC mode (`--hpc-mode`). Bounding box is auto-filled from the map AOI in the GUI.
- **ISCE_SBAS analyzer**: New MintPy SBAS analyzer for ISCE2 `stackSentinel` outputs. `prep_data()` auto-discovers interferogram, geometry, baseline, and metadata paths; MintPy outputs written to `mintpy/` subdirectory.
- **HPC mode (SLURM)**: ISCE_S1 can submit each processing step as a separate `sbatch` job. Per-step resource configuration via `sbatch_options.json`, editable in the GUI via **Sbatch Options** modal.
- **Job Folders subfolder browser**: The Jobs drawer now lists both folders and files. Click any folder to navigate into it; click **↑ Up** to return to the parent. Breadcrumb path shown in the header.
- **Cancel button for local processors**: A **Cancel** button appears in the ISCE_S1 processor panel to terminate the running background process (local) or `scancel` all active SLURM jobs (HPC).
- **Refresh with per-command detail**: ISCE_S1 `refresh()` now shows per-command status (`cmd_NNNN RUNNING / SUCCEEDED / FAILED`) for multi-command steps, matching the CLI output.

### Bug Fixes

- **ISCE_S1 bbox not passed**: `Processor.create()` was calling `cls(cfg)` which mapped the config to the `pairs` argument in ISCE_S1's two-argument constructor. Fixed by detecting `pairs` in the constructor signature via `inspect.signature` and using keyword arguments.
- **ISCE_SBAS diagnostic geocoding**: `avgPhaseVelocity.h5`, `numTriNonzeroIntAmbiguity.h5`, and `maskConnComp.h5` are now geocoded automatically after the `geocode` step. Existing radar-coordinate data is geocoded on demand in the render endpoint.
- **ISCE_SBAS timeseries filter**: View Results now returns only `geo/geo_timeseries*.h5` (geocoded) when present, not the radar-coordinate `timeseries*.h5` files.
- **ISCE_SBAS `.mintpy.cfg` path**: Analyzer route was writing `.mintpy.cfg` to the job folder root; ISCE_SBAS expects it at `mintpy/.mintpy.cfg`. Fixed by reading `analyzer.cfg_path` at runtime.
- **ISCE_S1 submit via GUI missing sbatch options**: `_run_folder_process` now loads `sbatch_options.json` and calls `processor.submit()` directly for local processors, bypassing the HyP3-only `SubmitCommand`/`SaveJobsCommand` wrappers.
- **cmd index parsing crash**: `int()` raised `ValueError` on malformed `cmd_????.done/fail` filenames. Fixed with a safe `_idx()` helper.
- **Job Folders empty workdir path traversal**: An empty workdir in `browse-subfolders` resolved to CWD, allowing requests outside the workdir. Fixed with an early 400 response when workdir is not configured.
- **Job Folders `has_children` OSError**: `subfolder.iterdir()` on restricted directories could raise `OSError`. Wrapped in `try/except`.

## [0.2.5] - 2026-04-21

### New Features

- **SBAS network editor (GUI)**: Interactive baseline-time graph editor in the processor panel. Drag between scene nodes to create new pairs; click an existing edge to delete it; hover to inspect temporal baseline, perpendicular baseline, and quality score. Edges are colored by quality (green → yellow → red).
- **Pair quality scoring**: Pre-processing interferogram quality assessment combining S1 global coherence decay models, WorldCover land-cover class fractions (stable, vegetation, forest), precipitation, snow cover, NDVI, and fire data. Quality scores drive edge colors in the network editor and can exclude bad-weather scenes automatically.
- **Per-class coherence decay models**: `_coherence.py` fits separate exponential decay models per WorldCover land-cover class (stable, vegetation, forest, water). Per-class cache persisted to disk; prefetch runs before the pair loop to avoid warm-run stalls.
- **Decay maps overlay (GUI)**: Seasonal S1 global coherence maps (γ∞ PS baseline, γ0 initial coherence, τ decay constant) can be overlaid on the main map directly from the processor panel for rapid site assessment before submitting jobs.
- **`quick_overview` MintPy step**: Added as an optional step in the analyzer workflow to generate diagnostic map layers (coherence, phase velocity, unwrapping errors, connected-components mask) before full SBAS inversion.
- **`avoid_low_quality_days` default changed to `True`**: Bad-weather scenes are now excluded from the pair network by default. Default precipitation threshold tightened to 25 mm (3-day accumulation). Weather/snow data fetched during filtering is seeded directly into the pair quality cache, eliminating duplicate API calls.
- **API route refactor**: `api.py` split into separate route modules under `routes/` (`search`, `processor`, `analyzer`, `quality`, `render`, `folders`, `settings`) for easier maintenance.

### Performance

- **Parallel coherence prefetch**: S1 global coherence tile S3 downloads now run concurrently (up to 4 threads), followed by per-pair numpy evaluation in parallel (8 threads). Expected 4–6× speedup for stacks with 32 000+ pairs on first run; warm-cache runs unchanged.
- **Smarter pair quality DB rebuilds**: DB only rebuilds when the scene set actually changes. Stores `_scene_names` for exact scene-set comparison; parameter changes (`dt_max`, `pb_max`, degree limits) no longer trigger a rebuild. Backward-compatible with old DBs (falls back to count comparison, migrates on next rebuild).

### Bug Fixes

- **Coherence scoring thresholds corrected** to Hanssen 2001 values: Good ≥ 0.60, Risky 0.30–0.60, Bad < 0.30 (was 0.65/0.35).
- **matplotlib `Agg` backend**: Added `matplotlib.use('Agg')` before `pyplot` import in `tool.py` — fixes `RuntimeError: main thread is not in main loop` when plotting from FastAPI background threads.
- **CDSE account validation**: Login credentials for the Copernicus Data Space Ecosystem are now validated on entry in the settings panel.
- **Pair quality prefetch cache stall**: Per-class coherence S3 reads were blocking the first pair of each season on warm runs. Pre-fetching both overall and per-class maps before the pair loop fixes the 0% stall.

---

## [0.2.4] - 2026-03-25

### New Features
- **CLI & API**: `select_pairs()` is now a pure computation method — no file I/O inside the class. File writing (JSON, PNG, workflow marker) has been moved to the CLI and API call sites, keeping the core logic reusable and testable
- **Path handling**: All functions that accept path arguments now call `.expanduser().resolve()`, enabling `~` tilde paths everywhere
- **WebUI**: Added documentation button in the General Settings panel (bottom-left) linking to the InSARHub docs site
- **WebUI (`insarhub-app`)**: Auto-creates the working directory if it does not exist when `-w <path>` is passed
- **CLI (`insarhub-app`)**: Added `-v` / `--version` flag
- **Windows fix**: `insarhub-app` no longer returns immediately on Windows — sets `WindowsSelectorEventLoopPolicy` so uvicorn blocks correctly

### Bug Fixes
- **WebUI Processor**: Unchecking dry-run after a completed run no longer leaves the button stuck at "✓ Done" — the status resets to idle on checkbox change
- **WebUI Processor**: Clicking "✓ Done" after a real (non-dry-run) submit now correctly closes the modal
- **WebUI Processor**: "✓ Done" button now shows a pointer cursor on hover
- **Analyzer**: Fixed `NoneType` crash in troposphere correction when `Path.mkdir()` was called on an already-resolved path
- **CLI credential setup**: Removed spurious blank first line from `.cdsapirc` written by the interactive credential prompt

---

## [0.2.3] - 2026-03-18

### New Features
- **Documentation**: Completed full WebUI (frontend) documentation with screenshots and usage guide
- **Documentation**: Added version changelog and update log pages to the docs site
- **WebUI**: Added email and Discord contact buttons next to the light/dark mode toggle in the header
- **WebUI**: Reduced extra whitespace around the GitHub badge in the header


### Bug Fixes
- Fixed gh-pages CI push rejection when remote branch was ahead of local (`git fetch origin gh-pages` before `mike deploy`)
- Minor doc link and typo fixes
- Fixed broken image link in the WebUI overview documentation page

---

## [0.2.1] - 2026-03-06

### New Features
- **Frontend**: Download orbit file option added to the downloader panel
- **Frontend**: Granule name file upload — users can supply a text file of scene names for custom searches
- **Frontend**: Drawer now auto-hides when the user clicks on the map
- **Downloader**: Added `parse_granule_names()` to parse scene names from a string, list, or file for search
- **Downloader (`S1_SLC`)**: `-O <dir>` now downloads all orbit files to the specified directory
- **Downloader (`S1_SLC`)**: Skips orbit files that already exist (checked by acquisition time)
- **Downloader**: Automatically falls back to the ASF orbit server if the CDSE sentineleof server fails
- **Documentation**: Completed WebUI documentation

### Bug Fixes
- Fixed velocity map display shifting caused by incorrect EPSG selection in the frontend
- Fixed duplicate search results when multiple stacks share the same path (ASF server-side bug workaround)
- Fixed `[ERROR] download: not enough values to unpack` in the download future handler
- Fixed numpy deprecation warnings
- Pinned CI to Python 3.12 to avoid breakage on 3.13/3.14

---

## [0.2.0] - 2026-02-20

### New Features
- **WebUI (`insarhub-app`)**: Full Panel-based browser frontend for download, processing, and analysis
- **Frontend**: Interactive map for AOI selection with basemap overlay
- **Frontend**: Job queue drawer with dry-run toggle, live log streaming, and submit/cancel controls
- **Frontend**: Settings panel for credentials, working directory, and HyP3 account configuration
- **Frontend**: Velocity and time-series result visualization directly in the browser
- **CLI**: `insarhub-app` command to launch the WebUI server
- **Core**: Unified `CommandResult` pattern shared between CLI and Panel frontend
- **Core**: `InSAREngine` high-level pipeline runner with per-step skip flags and watch mode

---

## [0.1.0] - 2026-03-06

### Initial Release

First public release of **InSARHub** — a modular Python framework for automated InSAR time-series processing.

---

### Features

#### Downloader
- `ASF_Base_Downloader`: Search and download Sentinel-1, ALOS, and NISAR SLC data via the ASF Search API
- Spatial filtering with bounding box, WKT, or GeoJSON/shapefile AOI
- Post-search filtering by date range, path/frame, flight direction, polarization, season, coverage, and scene count
- Scene footprint visualization with basemap overlay (`footprint()`)
- DEM download via `dem-stitcher` aligned to search footprints
- Multi-threaded download with Ctrl+C cancellation and partial-file cleanup
- `S1_SLC`: Sentinel-1 SLC specialized downloader with orbit file (`sentineleof`) support

#### Processor
- `Hyp3_S1`: Submit, monitor, download, retry, and persist HyP3 InSAR jobs
- Multi-account credential pool with automatic credit-aware job rotation
- Batch job persistence (save/load JSON) for resumable workflows
- `watch()` mode: polls job status and downloads succeeded outputs continuously
- Retry failed jobs with automatic timestamp-stamped save files

#### Analyzer
- `Hyp3_SBAS`: End-to-end MintPy SBAS time-series analysis from HyP3 outputs
- Automatic unzip, file collection, common-overlap clipping, and MintPy config generation
- Optional pyAPS tropospheric correction with CDS API credential management
- `cleanup()` to remove temporary files after processing

#### Utilities
- `select_pairs`: Temporal and perpendicular baseline filtering with configurable targets and tolerances
- Local baseline computation (zero network calls for Sentinel-1 and ALOS)
- API fallback with threaded fetching for products without local baseline data
- Connectivity enforcement: minimum/maximum degree per scene with force-connect option
- `plot_pair_network`: Network visualization with per-scene connection histogram
- `ERA5Downloader`: Batch ERA5 reanalysis download for MintPy tropospheric correction, MintPy-compatible filenames
- `clip_hyp3_insar`: Clip HyP3 zip outputs to a custom AOI before analysis
- `Slurmjob_Config`: Generate SLURM batch scripts for HPC job submission
- `earth_credit_pool`: Load multi-account Earthdata credentials from a pool file

#### CLI (`insarhub`)
- `insarhub download` — search, filter, and download SLC scenes
- `insarhub processor submit/refresh/download/retry/watch/save/credits` — full HyP3 job lifecycle
- `insarhub analyzer prep/run` — prepare and run MintPy analysis
- `insarhub utils select-pairs/plot-network/era5/clip` — utility commands
- Workdir (`-w`) and credential pool (`--credential-pool`) flags across all subcommands

#### Core
- Auto-registering component registry (`Downloader`, `Processor`, `Analyzer`)
- `InSAREngine`: high-level pipeline runner with skip flags and watch mode
- Unified `CommandResult` pattern shared between CLI and Panel frontend


[0.2.5]: https://github.com/jldz9/InSARHub/releases/tag/v0.2.5
[0.2.4]: https://github.com/jldz9/InSARHub/releases/tag/v0.2.4
[0.2.3]: https://github.com/jldz9/InSARHub/releases/tag/v0.2.3
[0.2.1]: https://github.com/jldz9/InSARHub/releases/tag/v0.2.1
[0.2.0]: https://github.com/jldz9/InSARHub/releases/tag/v0.2.0
[0.1.0]: https://github.com/jldz9/InSARHub/releases/tag/v0.1.0
