# Changelog

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
