The InSARHub Processor module provides functionality specifically for interferogram processing.

- **Import processor**

    Import the Processor class to access all processor functionality
```python
from insarhub import Processor
```

- **View available processors**

    List all registered processors
```python
Processor.available()
```

## Available Processors

=== "Hyp3_S1"

    The HyP3 InSAR processor is a cloud-based processing service provided by the ASF HyP3 system for generating interferograms from Sentinel-1 SAR data.
    InSARHub wrapped [hyp3_sdk](https://github.com/ASFHyP3/hyp3-sdk) as one of its process backends.

    The `Hyp3_S1` specifically wraps `insar_job` in hyp3_sdk to provide InSAR SLC processing workflows.

    ::: insarhub.processor.hyp3_s1.Hyp3_S1
        options:
            heading_level: 0
            members: false

    ### Usage

    - **Create Processor with Parameters**

        Initialize a processor instance with search criteria

        ```python
        processor = Processor.create('Hyp3_S1', workdir='/your/work/path', pairs=pairs)
        ```
        OR
        ```python
        params = {
            "workdir": '/your/work/path',
            "pairs": pairs,
        }
        processor = Processor.create('Hyp3_S1', **params)
        ```
        OR
        ```python
        from insarhub.config.defaultconfig import Hyp3_S1_Config
        cfg = Hyp3_S1_Config(workdir='/your/work/path', pairs=pairs)
        processor = Processor.create('Hyp3_S1', config=cfg)
        ```

        ::: insarhub.config.Hyp3_Base_Config
            options:
                members: false
                show_source: false
                heading_level: 0

        ::: insarhub.config.defaultconfig.Hyp3_S1_Config
            options:
                members: false
                heading_level: 0

    - **Submit Jobs**

        Submit InSAR jobs to HyP3 based on the current configuration.

        ```python
        jobs = processor.submit()
        ```

        ::: insarhub.processor.hyp3_s1.Hyp3_S1.submit
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Refresh Jobs**

        Refresh the status of all jobs.

        ```python
        jobs = processor.refresh()
        ```

        ::: insarhub.processor.hyp3_s1.Hyp3_S1.refresh
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Retry Failed Jobs**

        Retry all failed jobs by re-submitting them.

        ```python
        jobs = processor.retry()
        ```

        ::: insarhub.processor.hyp3_s1.Hyp3_S1.retry
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Download Succeeded Jobs**

        Download all succeeded jobs for all users.

        ```python
        processor.download()
        ```

        ::: insarhub.processor.hyp3_s1.Hyp3_S1.download
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Save Current Jobs**

        Save the current job batch information to a JSON file.

        ```python
        processor.save()
        ```

        ::: insarhub.processor.hyp3_s1.Hyp3_S1.save
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Watch Jobs**

        Continuously monitor jobs and download completed outputs.

        ```python
        processor.watch()
        ```

        ::: insarhub.processor.hyp3_s1.Hyp3_S1.watch
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Load Saved Job**

        Load a previously saved JSON file and resume work.

        ```python
        processor = Processor.create('Hyp3_S1', saved_job_path='path/to/your/json/file.json')
        ```

        When loaded, you can resume checking or downloading jobs submitted to the HyP3 server.

=== "ISCE2_S1"

    The ISCE2_S1 processor runs ISCE2 `stackSentinel` locally to generate Sentinel-1 interferograms from downloaded SLC `.SAFE` files. It generates a numbered sequence of run scripts and executes them sequentially, parallelising independent commands within each step.

    - **Import processor**

        ```python
        from insarhub import Processor
        ```

    - **Create processor**

        ```python
        from insarhub.config import ISCE2_S1_Config

        cfg = ISCE2_S1_Config(
            workdir='/data/p100_f466',
            bbox=[33.0, 38.0, -120.0, -115.0],   # [S, N, W, E]
        )
        pairs = [('20200101', '20200113'), ('20200113', '20200125')]
        processor = Processor.create('ISCE2_S1', pairs=pairs, config=cfg)
        ```

        ::: insarhub.config.defaultconfig.ISCE2_S1_Config
            options:
                members: false
                show_source: false
                heading_level: 0

    - **Submit (local mode)**

        Generate run scripts and start sequential execution in a background process. Returns immediately; use `refresh()` to monitor progress.

        ```python
        jobs = processor.submit()
        ```

        ::: insarhub.processor.isce2_s1.ISCE2_S1.submit
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Submit (HPC / SLURM mode)**

        Set `hpc_mode=True` to use the sliding-window SLURM manager. Steps are first grouped: consecutive steps with equal per-scene/per-pair command counts are merged into a single group-manager (e.g. `run_02_unpack_secondary_slc` + `run_03_average_baseline` when both have one command per scene); every other step gets its own single-step manager. Each manager keeps at most `max_concurrent_hpc` child jobs active at all times, submitting new ones immediately as slots open. Each sbatch script logs `START`/`DONE`/`FAIL` with elapsed seconds per command.

        Only the *first* group's manager is submitted directly by `submit()`. Every manager chain-submits the next group's manager itself right after it succeeds — via its own trailing `sbatch` call, not a SLURM `--dependency` — so at most one manager (plus its own ≤`max_concurrent_hpc` children) is ever sitting in the queue at a time, instead of every group's manager being pre-submitted up front. This matters because SLURM's submitted-jobs-per-user QOS limit counts jobs that are merely waiting on a dependency just as much as running ones; pre-submitting the whole chain could exhaust that limit on managers doing nothing but waiting their turn. A failed or cancelled manager simply never submits the next one, so the chain halts on its own — no separate cleanup needed for the not-yet-submitted remainder. `refresh()` picks up each newly chain-submitted job's ID automatically (written to a small `chained_job_id.txt` next to the group's logs) as soon as it's submitted.

        Manager job names are short and state which run(s) they own: `i<NN>_mgr` for a single-step manager (e.g. `i04_mgr` for `run_04_...`), `i<NN>-<MM>_grp` for a group manager spanning steps NN–MM (e.g. `i02-03_grp`) — handy for reading `squeue` at a glance.

        ```python
        cfg = ISCE2_S1_Config(
            workdir='/data/p100_f466',
            bbox=[33.0, 38.0, -120.0, -115.0],
            hpc_mode=True,
            max_concurrent_hpc=12,   # default; tune to your cluster's fair-share limit
        )
        processor = Processor.create('ISCE2_S1', pairs=pairs, config=cfg)
        processor.submit()
        ```

        `retry()` auto-detects HPC mode from saved job metadata (`slurm_job_ids` / `hpc_manager` / `hpc_array`) — passing `hpc_mode=True` again is not required.

    - **Dry run**

        Preview the run scripts and path checks without executing anything.

        ```python
        cfg = ISCE2_S1_Config(
            workdir='/data/p100_f466',
            bbox=[33.0, 38.0, -120.0, -115.0],
            dry_run=True,
        )
        processor = Processor.create('ISCE2_S1', pairs=pairs, config=cfg)
        processor.submit()
        ```

    - **Refresh**

        Read step and command statuses from disk.

        ```python
        jobs = processor.refresh()
        ```

        ::: insarhub.processor.isce2_base.ISCE2_Base.refresh
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Retry failed steps**

        Re-run all steps that have `FAILED` status.

        ```python
        processor.retry()
        ```

        ::: insarhub.processor.isce2_base.ISCE2_Base.retry
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Cancel**

        Terminate the running background process (local mode) or `scancel` all active SLURM jobs (HPC mode).

        ```python
        processor.cancel()
        ```

        ::: insarhub.processor.isce2_base.ISCE2_Base.cancel
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Watch**

        Poll step statuses at regular intervals until all steps complete.

        ```python
        processor.watch(refresh_interval=60)
        ```

        ::: insarhub.processor.isce2_base.ISCE2_Base.watch
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Save / Load**

        Job state is saved automatically after `submit()`. To reload and resume from a saved job file:

        ```python
        cfg = ISCE2_S1_Config(
            workdir='/data/p100_f466',
            saved_job_path='/data/p100_f466/isce/isce_jobs_<timestamp>.json',
        )
        processor = Processor.create('ISCE2_S1', pairs=[], config=cfg)
        processor.refresh()   # or .retry(), .cancel(), .watch()
        ```

    - **Running without a local ISCE2 install**

        Set the `container` field to a path to an Apptainer/Singularity `.sif` image, or a Docker image reference (name[:tag]), and `submit()`/`retry()`/`refresh()`/`watch()`/`cancel()` all re-invoke the same `insarhub processor ...` CLI call inside that container instead of on the host — the workdir is bind-mounted at the identical path, so output lands exactly where a native run would put it, and ISCE2 never needs to be discovered on the host at all. The container image just needs `insarhub` installed alongside ISCE2/topsStack (see [`Dockerfile`](https://github.com/jldz9/InSARHub/blob/main/Dockerfile) in the repo root for a ready-to-build example).

        ```python
        cfg = ISCE2_S1_Config(
            workdir='/data/p100_f466',
            bbox=[33.0, 38.0, -120.0, -115.0],
            container='ghcr.io/jldz9/insarhub-isce2:latest',
        )
        processor = Processor.create('ISCE2_S1', pairs=pairs, config=cfg)
        processor.submit()
        ```

        The CLI form is the same:

        ```bash
        insarhub processor -N ISCE2_S1 -w /data/p100_f466 submit \\
            --container ghcr.io/jldz9/insarhub-isce2:latest
        ```

        `container` is a per-invocation setting, not persisted config — it must be set again on every subsequent call (`retry()`, a fresh `submit()`, etc.) that should also run inside the container. In HPC mode only each stage's child jobs run inside the container; the sbatch manager scaffolding stays on the host.

=== "GMTSAR_S1"

    The `GMTSAR_S1` processor runs [GMTSAR](https://github.com/gmtsar/gmtsar)'s Python pipeline locally to generate Sentinel-1 interferograms from downloaded SLC `.SAFE` files. Both GMTSAR entry points are supported, chosen automatically by whether `subswath` names one IW or several:

    - `subswath` names exactly one IW (e.g. `2`) — single-subswath, via `p2p_processing`. `GMTSAR_S1` extracts the configured IW subswath + polarization from each `.SAFE` scene itself, so callers only ever pass raw `.SAFE`/`.EOF` names, the same as multi-subswath mode.
    - `subswath` names more than one IW (e.g. `"1 2 3"`, the default) — multi-subswath, via `p2p_S1_TOPS_Frame`, producing a merged interferogram across every subswath named.

    Note `p2p_S1_TOPS_Frame` does not *replace* `p2p_processing` — it is built on top of it, calling `p2p_processing S1_TOPS` per subswath and then merging. `p2p_processing` is the generic pairwise engine and supports ~14 sensors (ERS, ENVI, ALOS, TSX, RS2, …); `S1_TOPS` is one of them.

    GMTSAR runs in its own conda environment, separate from InSARHub's (different numpy/GDAL stack) — `gmtsar_root` and `gmtsar_env_bin` tell `GMTSAR_S1` where to find GMTSAR's scripts and the `gmt` binary it shells out to. Both auto-detect when unset (`$GMTSAR` / a known GMTSAR script on `$PATH` for `gmtsar_root`; a sibling conda env with `gmt` in its `bin/` / bare `gmt` on `$PATH` for `gmtsar_env_bin`), so they're optional in practice — pass them explicitly only if auto-detection picks the wrong one or finds nothing. Alternatively, set `container` to a `.sif`/Docker image with `insarhub`+GMTSAR installed and skip local discovery entirely (mirrors `ISCE2_S1`'s `--container`; in HPC mode only each stage's child jobs run inside the container, the sbatch manager scaffolding stays on the host).

    - **Import processor**

        ```python
        from insarhub import Processor
        ```

    - **Create processor**

        ```python
        from insarhub.config import GMTSAR_S1_Config

        cfg = GMTSAR_S1_Config(
            workdir       = '/data/stack',
            slc_dir       = '/data/slcs',
            orbit_dir     = '/data/orbits',
            dem_path      = '/data/dem.grd',   # GMTSAR-format DEM; auto-downloaded at staging if unset
            subswath      = 2,                 # IW2 only -- single-subswath. "1 2 3" (default) = multi-subswath merged
            gmtsar_root   = '/path/to/gmtsar',    # optional -- auto-detected if unset
            gmtsar_env_bin= '/path/to/conda/envs/gmtsar/bin',  # optional -- auto-detected if unset
        )
        pairs = [
            ("REF.SAFE", "REF.EOF", "SEC.SAFE", "SEC.EOF"),
        ]
        processor = Processor.create('GMTSAR_S1', pairs=pairs, config=cfg)
        ```

        !!! warning "Set `dem_path` for multi-subswath runs"
            Multi-subswath gives **each pair** its own case directory. With `dem_path` unset the DEM is auto-downloaded at staging time — once per pair. On a 27-pair network that is the same DEM fetched 27 times.

        ::: insarhub.config.defaultconfig.GMTSAR_S1_Config
            options:
                members: false
                show_source: false
                heading_level: 0

    - **Submit**

        Stage the GMTSAR case directory (and, for single-subswath mode, extract each pair's subswath), then launch `p2p_processing`/`p2p_S1_TOPS_Frame` in the background, up to `max_workers` concurrent pairs. Returns immediately; use `refresh()`/`watch()` to monitor progress.

        ```python
        jobs = processor.submit()
        ```

        ::: insarhub.processor.gmtsar_s1.GMTSAR_S1.submit
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Submit (HPC / SLURM mode)**

        Both modes support `hpc_mode=True`.

        **p2p mode** (`stack_mode=False`, the default) is the simpler of the two: every pair is completely independent — multi-subswath gives each its own case directory, and single-subswath output is namespaced `intf/<julian_pair>/` — so a single sliding-window manager fans every pair out at once, `max_concurrent_hpc` live at a time, with **no chaining at all**. Each child job runs that pair's whole chain (align → interferogram → filter → unwrap → geocode) via the internal `run-stage-unit --stage pair --index N` re-entry. Job names are `g_p2p_mgr` / `g_p2p_<idx>`.

        ```python
        cfg = GMTSAR_S1_Config(workdir='/data/stack', hpc_mode=True)
        Processor.create('GMTSAR_S1', pairs=pairs, config=cfg).submit()
        ```

        **stack_mode** instead runs each stack stage (`align_F<N>`/`intf_F<N>`/`merge`, or the flat `align`/`intf` for a single subswath) as its own sliding-window SLURM manager, instead of `_run_stack()` running as a background thread in the submitting process. Same chain-submission design as `ISCE2_S1` (see its HPC-mode docs above): only the *first* stage's manager is submitted directly, and each chain-submits the next stage's manager itself right after it succeeds — never a `--dependency` chain pre-submitted up front — so at most one manager (plus its own ≤`max_concurrent_hpc` children) is ever sitting in the queue at a time. Manager job names are `g_<stage>_mgr` / children `g_<stage>_<idx>` (e.g. `g_intf_F2_0007`), the same short/self-describing convention as ISCE's `i<NN>_mgr`.

        One real difference from `ISCE2_S1`: `GMTSAR_S1` has no flat shell-command-list generator the way `stackSentinel.py`'s `run_NN_*` files give ISCE — each stage's real work lives in Python methods (`_run_align_unit`/`_run_intf_unit`/`_run_merge_unit`), so every HPC child job's "command" re-enters `insarhub` itself (the internal `run-stage-unit` CLI action) to call one of those methods in a fresh process, rather than a raw shell command line calling a GMTSAR binary directly.

        ```python
        cfg = GMTSAR_S1_Config(
            workdir='/data/p100_f466',
            stack_mode=True,
            hpc_mode=True,
            max_concurrent_hpc=12,   # default; tune to your cluster's fair-share limit
            gmtsar_root=..., gmtsar_env_bin=...,
        )
        processor = Processor.create('GMTSAR_S1', pairs=pairs, config=cfg)
        processor.submit()
        ```

    - **Refresh**

        Read per-pair status from GMTSAR's own output markers (`.succeeded`/`.failed` under `intf/<julian_date_pair>/` (GMTSAR-assigned) or `merge/`).

        ```python
        jobs = processor.refresh()
        ```

        ::: insarhub.processor.gmtsar_s1.GMTSAR_S1.refresh
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Retry failed pairs**

        Re-run only the pairs whose status is `FAILED`.

        ```python
        processor.retry()
        ```

        ::: insarhub.processor.gmtsar_s1.GMTSAR_S1.retry
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Watch**

        Poll pair statuses at regular intervals until all pairs reach `SUCCEEDED` or `FAILED`.

        ```python
        processor.watch(poll_interval=60)
        ```

        ::: insarhub.processor.gmtsar_s1.GMTSAR_S1.watch
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Cancel (HPC mode)**

        `scancel` every SLURM job (managers + their children) for an HPC submission, in either mode. p2p jobs are found from the `hpc/p2p/` directory rather than `config.hpc_mode`, so a bare `cancel` locates them without repeating `--hpc-mode`; any pair still `PENDING`/`RUNNING` is marked `FAILED` so `refresh` does not report it as in flight. Local (non-HPC) `stack_mode` runs have nothing to cancel from a separate CLI invocation — `_run_stack()` runs as a background thread inside whichever process called `submit()`, not a detached background process the way `ISCE2_S1`'s local mode is, so there's no separate process left running once that call returns.

        ```python
        processor.cancel()
        ```

        ::: insarhub.processor.gmtsar_s1.GMTSAR_S1.cancel
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Save**

        Job state is saved automatically after `submit()` to `<workdir>/gmtsar/gmtsar_jobs.json`.

        ```python
        processor.save()
        ```

        ::: insarhub.processor.gmtsar_s1.GMTSAR_S1.save
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Output layout**

        Single-subswath: `<workdir>/gmtsar/intf/<julian_date_pair>/` (e.g. `intf/2019184_2019196/` — GMTSAR's own Julian-date pair naming, not ref/sec stems) — GMTSAR's native file names (`corr_ll.grd`, `phasefilt_ll.grd`, `*.PRM` files), which is exactly what MintPy's `prep_gmtsar.py` expects directly.

        Multi-subswath: `<workdir>/gmtsar/<ref_safe>_<sec_safe>/merge/` — the merged, geocoded product across every subswath named (`phasefilt_ll.grd`, `corr_ll.grd`, plus PNG/KML previews).
    - **Time series: use MintPy, not GMTSAR's own `sbas`**

        This is a direct consequence of choosing p2p. GMTSAR's `sbas` works in **radar coordinates** and needs every SLC resampled onto one common grid — which per-pair alignment does not provide. MintPy's `prep_gmtsar` reads the **geocoded** `*_ll.grd`, so all pairs already share a geographic grid and no common alignment reference is required. Use the `GMTSAR_MINTPY_SBAS` analyzer.

    - **Running without a local GMTSAR install**

        As with `ISCE2_S1`, set `container` to a `.sif` or Docker image carrying `insarhub` + GMTSAR and local discovery is skipped entirely:

        ```bash
        insarhub processor -N GMTSAR_S1 -w /data/stack submit \\
            --container ghcr.io/jldz9/insarhub-gmtsar:latest
        ```

        In HPC mode only each stage's child jobs run inside the container; the sbatch manager scaffolding stays on the host.

=== "ISCE3_Burst"

    The `ISCE3_Burst` processor builds an interferogram stack from ASF `SLC-BURST` granules using [ISCE3](https://github.com/isce-framework/isce3)/[COMPASS](https://github.com/opera-adt/COMPASS) for geocoding and [dolphin](https://github.com/isce-framework/dolphin) for everything downstream. Pair it with the `S1_Burst` downloader.

    Its defining property is that **there is no coregistration**. COMPASS geocodes every acquisition independently onto absolute UTM coordinates, so two dates of the same burst are pixel-aligned by construction — the misregistration artefacts that pairwise coregistration can introduce cannot arise.

    Nine stages, run in order:

    | stage | tool | output |
    |---|---|---|
    | `dem` | `sardem` | Copernicus DEM + NASADEM water mask |
    | `tec` | COMPASS | one IONEX map per acquisition date |
    | `cslc` | `s1_geocode_stack.py` → `run_*.sh` | geocoded CSLC per burst-date |
    | `static` | `s1_static_layers.py` | LOS/incidence geometry, then mosaicked onto the stack grid |
    | `crop` | dolphin | each burst cut to the AOI |
    | `ifg` | dolphin | interferograms (see `ifg_mode`) |
    | `stitch` | dolphin | each pair's bursts merged into one raster |
    | `filt` | dolphin | multilook → Goldstein → coherence |
    | `unwrap` | snaphu | unwrapped phase + connected components |

    ### Choosing the estimator — `ifg_mode`

    | value | pairs come from | notes |
    |---|---|---|
    | `phase_link` *(default)* | full-covariance estimation | every pair contributes; `pl_*` fields tune it |
    | `network` | a rule | `n_connections`, or `max_temporal_baseline` |
    | `user_defined` | this folder's `stack_*.json` | exactly the pairs `select_pairs` chose |

    `phase_link` is the default because it measurably outperforms a pairwise network: on a test stack it gave one connected component at 83% coverage against three at 55%, and cut closure error from 0.157 to 0.067 rad. Its parameters match dolphin's own shipped configuration (`glrt` / 0.001, half-window 7×14, ministack 15).

    Under `phase_link` + `pl_ifg_network=single_reference` (the defaults) a user-defined network is ignored — the estimator's output already *is* that network. Set `pl_ifg_network=bandwidth` if you want your pairs honoured there.

    ### Processing extent

    `AOI` is seeded automatically from the folder's downloader `intersectsWith`, so it is normally already filled in. Tick `process_full_extent` to process the whole downloaded burst footprint instead. Note `crop_buffer_deg` (0.05° by default) is added on every side — on a small AOI that buffer can approach the burst footprint on its own, so lower it if you want the AOI to actually bite.

    `dem` and `cslc` run before anything is geocoded, so they always use `AOI`; `process_full_extent` applies from `crop` onward.

    ### Notes

    - Stages are decomposed for SLURM — see [HPC (SLURM)](hpc.md). `cslc` is one job per burst-date and dominates runtime; `ifg` under `phase_link` is a single job, because the estimator has no per-pair unit.
    - The interferogram network is the **intersection** of dates across bursts. Where ASF has no coverage for one burst on one day, that date is excluded and named, so every pair stays formable on every burst.
    - Time series is via the `Dolphin_SBAS` analyzer, which serves both estimator modes.

    ::: insarhub.processor.isce3_burst.ISCE3_Burst
        options:
            heading_level: 0
            members: false


*[HyP3]: Hybrid Pluggable Processing Pipeline
