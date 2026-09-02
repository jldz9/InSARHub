# Changelog

## [0.4.0]

### Bug Fixes

* Fixed CLI `--stacks PATH:FRAME` selecting nothing for `S1_Burst`. ASF returns no `frameNumber` on `SLC-BURST` products, so burst stacks key on `fullBurstID` (`124_264305_IW2`) — but the CLI coerced both halves of a token to `int`, producing a target that could never equal a burst key. Selectors are now kept as strings when they are not numeric and matched through a new `_stack_key_matches()` hook on the downloader, so `S1_Burst` accepts the full burst ID (`124:124_264305_IW2`), the burst index with subswath (`124:264305_IW2`), or a bare burst index (`124:264305`, which matches that index in every subswath, since ASF reuses an index across subswaths). Path zero-padding is ignored, so `87:87_185682_IW2` and `87:087_185682_IW2` select the same stack. `frame` is now forwarded to the ASF query only when every selector really is a frame number *and* the downloader queries on frame at all, keeping burst IDs out of `asf_search`'s int-range validator.
* Fixed `filter()` silently falling back to the unfiltered search when no stack matched. `_subset` was assigned only in the non-empty branch, so `active_results` returned every stack the user had just excluded and downstream summary, pair selection and download all ran on the wrong set behind a single warning line. The subset is now committed even when empty.
* Fixed CLI `--stacks` exiting `0` after matching no stacks. An explicit stack selection that matches nothing is a typo or a stale config, so it now exits non-zero and prints both the requested and the available stack keys.
* Fixed empty downloader search results with `asf_search` 13.0.0. Its `should_use_asf_frame()` no longer detects a generic `platform=SENTINEL-1` query (it checks for a `shortName[]` CMR key while the query emits `shortName`, and its `platform[]` fallback only lists `SENTINEL-1A/-1B/-1C/-1D`), so `frame` silently queried the ESA frame and matched nothing. Sentinel-1 / ALOS / NISAR frame filters (including CLI `--stacks PATH:FRAME`) are now routed to `asfFrame` (`FRAME_NUMBER`), which works on both `asf_search` 12.x and 13.x.

### Downloader Output

* Downloader progress and stack listings now describe the product actually being searched instead of reusing the `S1_SLC` wording for every dataset. Two new overridable class attributes drive this: `product_label` (`Searching for bursts....`, `GSLCs`, `RSLCs`, `GUNWs`, `SLCs`) and `stack_key_label`, which names the second half of a stack key. Burst stacks key on a burst ID rather than a frame number, so `S1_Burst` now prints `relativeOrbit 124 Burst_ID 124_264305_IW2` — previously it printed `frame 124_264305_IW2`, which misnamed the value and implied a number that could be passed to `--frame`. The label is applied consistently across `summary()`, the `footprint()` map annotation, the `min_count` drop message, and the `--stacks` token error text.

## [0.4.0rc1]

## New Features

* **Expanded InSAR Processing Backends — GMTSAR, ISCE3, and NISAR**

  * Added the **`GMTSAR_S1`** local/HPC processor for Sentinel-1 SLC `.SAFE` data, supporting both single-subswath (`p2p_processing`) and multi-subswath merged-stack (`p2p_S1_TOPS_Frame`) workflows, with staged `align` / `topo` / `intf` / `merge` processing.
  * Added the **`ISCE3_Burst`** processor for ASF `SLC-BURST` granules using ISCE3/COMPASS and Dolphin, with DEM, TEC, CSLC, static, crop, interferogram, stitching, filtering, unwrapping, and LOS stages. Both network and phase-linking estimators are supported.
  * Added the **`ISCE3_NISAR`** processor for NISAR L2 GSLC products. Because GSLC data is already geocoded, the workflow directly performs AOI cropping, interferogram generation, stitching, and unwrapping through Dolphin.
  * Added **`GMTSAR_MINTPY_SBAS`**, **`GMTSAR_SBAS`**, and **`Dolphin_SBAS`** analyzers for MintPy-based, GMTSAR-native, and Dolphin time-series inversion workflows.
  * Extended **`ISCE3_Dolphin_PL`** to support both `ISCE3_Burst` and `ISCE3_NISAR` processors.

* **NISAR Data Ecosystem**

  * Added documented downloaders for **`NISAR_GSLC`**, **`NISAR_RSLC`**, and **`NISAR_GUNW`**, covering geocoded SLC, radar SLC, and geocoded unwrapped interferogram products.
  * Added **`S1_Burst`** downloading for ASF burst products, assembling individual burst granules into `.SAFE` directories.
  * Added AOI-aware NISAR GSLC preprocessing: each large GSLC is cropped to the AOI before Dolphin phase linking, substantially reducing memory and processing requirements. `process_full_extent` can disable this behavior.

* **Containerized Processing**

  * Added unified **Docker and Apptainer/Singularity** execution for processors and analyzers through the `container` configuration field and `--container` CLI option.
  * Container execution removes the requirement for local ISCE2, GMTSAR, or MintPy installations and is supported across local and HPC workflows.
  * Added shared container utilities and corresponding processing images/documentation.
  * Made **MintPy an optional dependency**, allowing downloader and processor workflows to run without MintPy installed locally.

* **Scalable HPC / SLURM Processing**

  * Added a shared, processor-independent **SLURM sliding-window manager** for `ISCE2_S1` and `GMTSAR_S1`.
  * HPC workflows now limit the number of concurrent child jobs with `max_concurrent_hpc` and **chain-submit processing stages**, avoiding large pre-submitted dependency chains.
  * Added symmetric HPC behavior for analyzers through `analyzer.run()` and standardized `--container` handling across local and HPC execution.

* **Improved GUI and Interactive Visualization**

  * Fully integrated the new GMTSAR processors/analyzers into the web GUI, including configuration, submission, refresh, retry, cancellation, and processor-specific SLURM templates.
  * Added analyzer discovery based on `compatible_processor`, including support for analyzers compatible with multiple processors.
  * Added Chinese GUI translations and a merge-download option.
  * Added **raw-data visualization** through the Downloader's **View Data** function for Sentinel-1 and NISAR products.
  * Renamed the processor viewer to **View Result** and expanded it to display geocoded interferograms from HyP3, ISCE3, and GMTSAR.
  * Added four-corner raster overlays so rotated Sentinel-1 quicklooks are correctly positioned on the map.

* **Adaptive MintPy Time-Series Processing**

  * Added adaptive defaults for MintPy coherence thresholds based on the actual coherence distribution of each stack.
  * `network.minCoherence`, `networkInversion.maskThreshold`, and `reference.minCoherence` now adapt automatically while respecting configured caps.
  * Users can still specify explicit numeric thresholds to override adaptive behavior.

* **Unified and More Flexible Workflow APIs**

  * Standardized processor and analyzer execution so `analyzer.run()` can transparently dispatch to HPC when `hpc_mode` is enabled.
  * Extended processor/analyzer compatibility registration to support multiple compatible processors.
  * Promoted the **NISAR AOI crop** to an explicit processing stage, making it independently visible and executable through the workflow and refresh interfaces.

## Bug Fixes

### HPC / SLURM Reliability

* Fixed orphaned child jobs and processes remaining after cancelling HPC workflows.
* Fixed stale `FAILED` states and retry handling so failed GMTSAR/ISCE jobs correctly return to a runnable state.
* Fixed background and asynchronous commands incorrectly reporting `SUCCEEDED` before processing had actually finished.
* Improved SLURM process monitoring, termination, workflow state updates, and manager walltime handling.
* Fixed excessive dependency-chain submission that could exceed SLURM `MaxSubmitPU` / submission limits. HPC managers now chain-submit downstream stages only after the preceding manager succeeds.
* Fixed stale per-command markers causing `--step` resubmissions to submit no work.
* Fixed manager scripts incorrectly masking failures as success due to `set -uo pipefail`.
* Centralized SLURM manager/script generation and status-query logic for consistent ISCE and GMTSAR HPC execution.
* Added clearer SLURM job naming and improved per-command status visibility through `refresh --ls`.

### GMTSAR Processing

* Refactored multi-subswath GMTSAR HPC execution so `align`, `topo`, `intf`, and `merge` are pooled across subswaths rather than running as separate sequential managers.
* Fixed shared-file race conditions in multi-subswath/interferogram processing by separating `topo` generation from interferogram execution.
* Fixed intermediate-file handling, merge failures, output-directory detection, and `dem.grd` discovery.
* Fixed GMTSAR retry behavior and workflow-state recovery.
* Fixed incorrect SBAS geometry parameters and missing post-SBAS geocoding.
* Fixed GMTSAR stack processing to correctly support full-frame multi-subswath workflows and merged outputs.
* Fixed GMTSAR compatibility with downloader-generated pair files, including automatic orbit resolution and `.zip` → `.SAFE` extraction.
* Renamed the GMTSAR output directory from `gmtsar_case/` to `gmtsar/`.
* Fixed GMTSAR DEM handling and added Copernicus GLO-30 support.
* Fixed GMTSAR GUI workflows that incorrectly used the ISCE sbatch template.
* Added processor-specific GMTSAR sbatch defaults while preserving existing user settings through additive configuration merging.
* Fixed GMTSAR GUI HPC resource editing and processor/analyzer-specific sbatch configuration handling.
* Added detailed GMTSAR HPC child-job status reporting through `refresh --ls`.

### ISCE2 Processing

* Fixed ISCE2 background commands being marked `SUCCEEDED` before the underlying process completed.
* Fixed orphaned ISCE2 processes and incomplete workflow state updates.
* Fixed HPC `num_proc` / `num_proc4topo` values drifting from SLURM `cpus_per_task`; HPC multiprocessing is now derived from sbatch resources.
* Tuned default ISCE2 HPC resources for full-frame multi-swath processing.
* Fixed `--step` resubmission behavior by clearing stale command markers.
* Fixed ISCE2 discovery when using `--container`, allowing execution on hosts without ISCE2 installed.
* Fixed ISCE2 DEM downloads to use the actual SLC footprint rather than only the search AOI.
* Fixed containerized ISCE2 / `topsStack` discovery and execution.

### Container Execution

* Fixed `docker: not found` errors and unintended nested Docker execution inside containers.
* Fixed containers terminating prematurely because of asynchronous command execution.
* Fixed MintPy configuration and `.mintpy.cfg` handling in container environments.
* Fixed incorrect GUI refresh and status behavior for local and containerized workflows.
* Improved actionable error messages when ISCE2 or MintPy is unavailable, including guidance to use container execution.

### GUI / Processor Integration

* Fixed GMTSAR being unusable through the web GUI.
* Unified CLI and GUI local-processor/job-file discovery and saved-processor reload logic.
* Fixed GMTSAR job submission, cancellation, pair reconstruction, and local-job discovery.
* Replaced processor-name-specific GUI logic with generic local/cloud processor metadata.
* Fixed processor/analyzer compatibility filtering and incorrect processor types passed to sbatch configuration.
* Fixed GUI refresh and result-viewer behavior across local, HPC, and container workflows.
* Fixed the GUI not exposing GMTSAR sbatch resource editing.
* Fixed GUI sbatch configuration from overwriting existing GMTSAR resource settings.
* Fixed `GMTSAR_MINTPY_SBAS` HPC submissions from using an ISCE step such as `"17"` instead of the GMTSAR SBAS configuration.

### MintPy / SBAS

* Fixed MintPy analysis leaving the server process permanently `cd`'d into the analysis directory.
* Restored automatic `mintpy/pic/` figure generation for CLI and GUI workflows.
* Added an explicit `plot` step that works independently and through HPC execution.
* Fixed plotting failures caused by matplotlib figure-number collisions so completed SBAS processing is not marked failed merely because visualization fails.
* Added the missing `correct_unwrap_error` step to the default 18-step MintPy workflow.
* Fixed analyzer HPC jobs dropping `prep_data` and user-specified configuration overrides.
* Fixed `--list-options` failing to show defaults when `.mintpy.cfg` does not yet exist.
* Fixed `Hyp3_SBSAS` / `ISCE_SBAS` MintPy output handling and improved analyzer-specific output organization.
* Fixed `Hyp3_SBAS` clipped-raster corruption persisting across reruns by validating existing rasters and using atomic `.part` writes.
* Fixed `networkx` dependency handling so fresh installations work correctly.
* Added clearer errors for missing MintPy installations.

### Data / Downloader / Search

* Fixed downloader-generated GMTSAR pairs being incompatible with GMTSAR's required pair/orbit format.
* Fixed search end dates being treated as midnight, causing all scenes acquired later on the specified end date to be excluded.
* Fixed merged-download orbit files being written to the wrong directory.
* Fixed merge searches being incorrectly restricted to a single satellite platform. Satellite platforms are now aggregated across all scenes in a stack.
* Fixed incorrect AOI handling and output detection across processing workflows.
* Fixed interferogram/result discovery for ISCE3 and GMTSAR outputs.
* Fixed map overlays and raster rendering for rotated Sentinel-1 quicklooks and non-zipped interferogram files.

### Configuration / CLI

* Fixed `container` being incorrectly persisted in `insarhub_config.json`.
* Fixed duplicate runtime-only configuration definitions that caused incorrect fields to be persisted.
* Fixed `max_workers` being hidden from `--list-options` and processor configuration overrides.
* Fixed negative numeric values such as negative longitude/bbox coordinates being incorrectly classified as unknown CLI flags.
* Fixed multi-value fields such as `reference_lalo`, `subswath`, and `swath_num` losing tokens when supplied without quotes.
* Fixed `reference_lalo` / `reference_yx` formatting when writing MintPy configuration files by converting space-separated values to MintPy's required comma-separated format.
* Fixed `ssl_verify` being incorrectly passed through to ASF search requests.
* Added processor-specific automatic sbatch templates while keeping the shared `sbatch_options.json` file additive and backward-compatible.
* Fixed resource settings from existing sbatch configuration being overwritten during GUI/configuration updates.

### DEM / Backend Consistency

* Aligned default HyP3, ISCE-S1, and GMTSAR-S1 multilooking to **20 × 4**.
* Aligned Goldstein filtering to **alpha = 0.5**.
* Updated GMTSAR defaults for reliable time-series processing:

  * `threshold_snaphu`: `0` → `0.1`
  * `near_interp`: `0` → `1`
  * `mask_water`: `1` → `0`
  * `filter_wavelength`: `200` → `25`
* Added Copernicus GLO-30 DEM support to GMTSAR for more consistent backend comparisons.
* Tuned ISCE-S1 HPC defaults for full-frame processing:

  * `num_proc4topo`: `1` → `6`
  * `num_proc`: `1` → `4`
  * Step `01`: `6 CPU / 16 GB`
  * Steps `09` / `10`: `4 CPU / 16 GB`
  * Step `16`: `4 CPU`
  * Step `08`: `1 CPU / 4 GB`

### Paths / Output Isolation

* Centralized processor and analyzer output paths in `config/paths.py`.
* Added dedicated MintPy output directories for different analyzers to prevent result-file collisions.
* Added GMTSAR-specific path resolution for per-subswath and merged stack layouts.
* Fixed long-standing analyzer output collisions and improved discovery of merged versus flat GMTSAR products.

### Logging / Environment

* Fixed global logging suppression that prevented most InSARHub logs from being emitted.
* Restored normal CLI logging while remaining compatible with applications that configure their own logging.
* Fixed `pyproj` coordinate-transform failures in affected conda environments by explicitly resolving the active environment's PROJ database.
* Fixed test environments where the MintPy stub incorrectly required `dask`.


# Refactor

* **HPC / SLURM Architecture**

  * Centralized processor-agnostic SLURM manager functionality into a shared `_slurm_manager.py`, reused across ISCE and GMTSAR workflows.
  * Reworked HPC execution from pre-submitted dependency chains to **chain-submitted sliding-window managers**, reducing queue pressure and avoiding `MaxSubmitPU` limits.
  * Standardized manager resources to **1 CPU / 1 GB** and automatically determine manager walltime from the selected partition's maximum allowed time.
  * Added configurable `"manager": {"partition": "..."}` support while removing the obsolete `manager_time` setting.
  * Added shared SLURM job-state/query utilities and improved job naming for monitoring and debugging.

* **GMTSAR HPC & Multi-Subswath Processing**

  * Unified `GMTSAR_S1` processing into four stages: **`align` → `topo` → `intf` → `merge`**, independent of subswath count.
  * Added pooled sliding-window execution across subswaths and interferogram pairs for improved concurrency.
  * Separated shared `topo` generation into its own stage, eliminating concurrent access/race conditions.
  * Aligned local and HPC processing around the same pooled execution model.
  * Added full-frame multi-subswath processing and native GMTSAR merging while preserving the single-subswath layout.
  * Added layout-independent path resolution for merged and legacy stack layouts.
  * Simplified configuration by removing the breaking `frame_mode` option; subswath configuration now determines single- vs multi-subswath processing.
  * Added detailed `GMTSAR_S1.refresh --ls` task and SLURM job status reporting.

* **SLURM Configuration & SBAS**

  * Added processor-specific `sbatch_options.json` templates with additive merging so existing user settings are preserved.
  * GMTSAR and ISCE now populate only their relevant processing stages.
  * Changed GMTSAR MintPy SBAS configuration from ISCE-specific step `"17"` to **`"sbas"`**.
  * Updated the GUI to display processor-specific stage/SBAS configuration and document manager settings.

* **ISCE HPC Resource Management**

  * Automatically derive ISCE `num_proc` and `num_proc4topo` from corresponding SLURM CPU allocations.
  * Tuned default CPU and memory resources for full-frame multi-subswath processing while reducing resources for lightweight stages.

* **Configuration & Path Architecture**

  * Centralized processor/analyzer paths in `config/paths.py` using dedicated path objects.
  * Added `GMTSARPaths` and expanded `Hyp3Paths`.
  * Isolated MintPy outputs by analyzer (`hyp3_mintpy/`, `isce_mintpy/`, `gmtsar_mintpy/`) and colocated each analyzer's `.mintpy.cfg`.
  * Moved shared processor-reload helpers from `core/local_processor_reload.py` to `utils/local_processor_reload.py`.

* **Shared Backend Utilities & Cleanup**

  * Moved CLI/GUI pair-quality and stack operations into shared `utils/` helpers, eliminating duplicated computation between interfaces.
  * Added shared helpers for cache seeding, configuration overrides, job iteration, job launching, stop-event handling, and command error handling.
  * Removed the unused `S1_Burst` downloader and associated registration/configuration.
  * Removed unused API routes, request models, CLI commands, and other dead backend code.
  * Enforced the architecture that CLI and GUI layers delegate computation to shared utility modules.

* **Frontend Refactor**

  * Removed unused frontend components and dead fields.
  * Centralized the API base URL and status-color logic.
  * Added shared `DrawerShell` / `DrawerHeader` components to eliminate repeated drawer UI structure.
  * Added reusable `useCopyFeedback()` and `useFetchJson()` hooks for common frontend behavior.

* **Processing Consistency**

  * Added Copernicus GLO-30 DEM support to GMTSAR.
  * Aligned HyP3, ISCE, and GMTSAR defaults around **20×4 multilooking** and **Goldstein alpha 0.5**.
  * Updated GMTSAR time-series defaults for SNAPHU, low-coherence interpolation, and water masking.

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
- **ISCE cleanup misses `merged/interferograms`** (`isce2_sbas.py`) — cleanup targeted `isce/interferograms/` (nonexistent); real stackSentinel output is `isce/merged/interferograms/`. Large intermediate files were never deleted.
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
- **Path dataclass coverage extended** — remaining hardcoded path literals replaced with dataclass properties: `isce2_sbas.py` now uses `ISCEPaths` for `isce_dir`, `slc_dir`, `dem_dir`; `cli/main.py` uses `Hyp3Paths`/`ISCEPaths` in `_has_zips` and `_find_job_file`; `utils/batch.py` and `utils/tool.py` use `Hyp3Paths.output_dir` for ZIP discovery.
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
