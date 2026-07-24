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

=== "ISCE_S1"

    The ISCE_S1 processor runs ISCE2 `stackSentinel` locally to generate Sentinel-1 interferograms from downloaded SLC `.SAFE` files. It generates a numbered sequence of run scripts and executes them sequentially, parallelising independent commands within each step.

    - **Import processor**

        ```python
        from insarhub import Processor
        ```

    - **Create processor**

        ```python
        from insarhub.config import ISCE_S1_Config

        cfg = ISCE_S1_Config(
            workdir='/data/p100_f466',
            bbox=[33.0, 38.0, -120.0, -115.0],   # [S, N, W, E]
        )
        pairs = [('20200101', '20200113'), ('20200113', '20200125')]
        processor = Processor.create('ISCE_S1', pairs=pairs, config=cfg)
        ```

        ::: insarhub.config.defaultconfig.ISCE_S1_Config
            options:
                members: false
                show_source: false
                heading_level: 0

    - **Submit (local mode)**

        Generate run scripts and start sequential execution in a background process. Returns immediately; use `refresh()` to monitor progress.

        ```python
        jobs = processor.submit()
        ```

        ::: insarhub.processor.isce_s1.ISCE_S1.submit
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Submit (HPC / SLURM mode)**

        Set `hpc_mode=True` to use the sliding-window SLURM manager. Each step submits a lightweight manager job that keeps at most `max_concurrent_hpc` child jobs active at all times, submitting new ones immediately as slots open. Consecutive steps with equal command counts are merged into a single group-manager. Steps are chained via `--dependency=afterok`. Each sbatch script logs `START`/`DONE`/`FAIL` with elapsed seconds per command.

        ```python
        cfg = ISCE_S1_Config(
            workdir='/data/p100_f466',
            bbox=[33.0, 38.0, -120.0, -115.0],
            hpc_mode=True,
            max_concurrent_hpc=12,   # default; tune to your cluster's fair-share limit
        )
        processor = Processor.create('ISCE_S1', pairs=pairs, config=cfg)
        processor.submit()
        ```

        `retry()` auto-detects HPC mode from saved job metadata (`slurm_job_ids` / `hpc_manager` / `hpc_array`) — passing `hpc_mode=True` again is not required.

    - **Dry run**

        Preview the run scripts and path checks without executing anything.

        ```python
        cfg = ISCE_S1_Config(
            workdir='/data/p100_f466',
            bbox=[33.0, 38.0, -120.0, -115.0],
            dry_run=True,
        )
        processor = Processor.create('ISCE_S1', pairs=pairs, config=cfg)
        processor.submit()
        ```

    - **Refresh**

        Read step and command statuses from disk.

        ```python
        jobs = processor.refresh()
        ```

        ::: insarhub.processor.isce_base.ISCE_Base.refresh
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Retry failed steps**

        Re-run all steps that have `FAILED` status.

        ```python
        processor.retry()
        ```

        ::: insarhub.processor.isce_base.ISCE_Base.retry
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Cancel**

        Terminate the running background process (local mode) or `scancel` all active SLURM jobs (HPC mode).

        ```python
        processor.cancel()
        ```

        ::: insarhub.processor.isce_base.ISCE_Base.cancel
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Watch**

        Poll step statuses at regular intervals until all steps complete.

        ```python
        processor.watch(refresh_interval=60)
        ```

        ::: insarhub.processor.isce_base.ISCE_Base.watch
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Save / Load**

        Job state is saved automatically after `submit()`. To reload and resume from a saved job file:

        ```python
        cfg = ISCE_S1_Config(
            workdir='/data/p100_f466',
            saved_job_path='/data/p100_f466/isce/isce_jobs_<timestamp>.json',
        )
        processor = Processor.create('ISCE_S1', pairs=[], config=cfg)
        processor.refresh()   # or .retry(), .cancel(), .watch()
        ```

=== "GMTSAR_S1"

    The `GMTSAR_S1` processor runs [GMTSAR](https://github.com/gmtsar/gmtsar)'s Python pipeline locally to generate Sentinel-1 interferograms from downloaded SLC `.SAFE` files. Both GMTSAR entry points are supported, selected via `frame_mode`:

    - `frame_mode=False` (default) — single-subswath, via `p2p_processing`. `GMTSAR_S1` extracts the configured IW subswath + polarization from each `.SAFE` scene itself, so callers only ever pass raw `.SAFE`/`.EOF` names, the same as Frame mode.
    - `frame_mode=True` — multi-subswath, via `p2p_S1_TOPS_Frame`, producing a merged interferogram across all three IW subswaths.

    GMTSAR runs in its own conda environment, separate from InSARHub's (different numpy/GDAL stack) — `gmtsar_root` and `gmtsar_env_bin` tell `GMTSAR_S1` where to find GMTSAR's scripts and the `gmt` binary it shells out to; both are required.

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
            dem_path      = '/data/dem.grd',   # GMTSAR-format DEM; no auto-download yet
            subswath      = 2,                 # IW2 (default); frame_mode=False only
            gmtsar_root   = '/path/to/gmtsar',
            gmtsar_env_bin= '/path/to/conda/envs/gmtsar/bin',
        )
        pairs = [
            ("REF.SAFE", "REF.EOF", "SEC.SAFE", "SEC.EOF"),
        ]
        processor = Processor.create('GMTSAR_S1', pairs=pairs, config=cfg)
        ```

        ::: insarhub.config.defaultconfig.GMTSAR_S1_Config
            options:
                members: false
                show_source: false
                heading_level: 0

    - **Submit**

        Stage the GMTSAR case directory (and, for `frame_mode=False`, extract each pair's subswath), then launch `p2p_processing`/`p2p_S1_TOPS_Frame` in the background, up to `max_workers` concurrent pairs. Returns immediately; use `refresh()`/`watch()` to monitor progress.

        ```python
        jobs = processor.submit()
        ```

        ::: insarhub.processor.gmtsar_s1.GMTSAR_S1.submit
            options:
                members: false
                show_source: false
                heading_level: 5

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

    - **Save**

        Job state is saved automatically after `submit()` to `<workdir>/gmtsar_case/gmtsar_jobs.json`.

        ```python
        processor.save()
        ```

        ::: insarhub.processor.gmtsar_s1.GMTSAR_S1.save
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Output layout**

        `frame_mode=False`: `<workdir>/gmtsar_case/intf/<julian_date_pair>/` (e.g. `intf/2019184_2019196/` — GMTSAR's own Julian-date pair naming, not ref/sec stems) — GMTSAR's native file names (`corr_ll.grd`, `phasefilt_ll.grd`, `*.PRM` files), which is exactly what MintPy's `prep_gmtsar.py` expects directly.

        `frame_mode=True`: `<workdir>/gmtsar_case/<ref_safe>_<sec_safe>/merge/` — the merged, geocoded product across all three subswaths (`phasefilt_ll.grd`, `corr_ll.grd`, plus PNG/KML previews).
    - **Running without a local ISCE2 install**

        Set the `container` field to a path to an Apptainer/Singularity `.sif` image, or a Docker image reference (name[:tag]), and `submit()`/`retry()`/`refresh()`/`watch()`/`cancel()` all re-invoke the same `insarhub processor ...` CLI call inside that container instead of on the host — the workdir is bind-mounted at the identical path, so output lands exactly where a native run would put it, and ISCE2 never needs to be discovered on the host at all. The container image just needs `insarhub` installed alongside ISCE2/topsStack (see [`Dockerfile`](https://github.com/jldz9/InSARHub/blob/main/Dockerfile) in the repo root for a ready-to-build example).

        ```python
        cfg = ISCE_S1_Config(
            workdir='/data/p100_f466',
            bbox=[33.0, 38.0, -120.0, -115.0],
            container='ghcr.io/jldz9/insarhub-isce2:latest',
        )
        processor = Processor.create('ISCE_S1', pairs=pairs, config=cfg)
        processor.submit()
        ```

        `container` is a per-invocation setting, not persisted config — it must be set again on every subsequent call (`retry()`, a fresh `submit()`, etc.) that should also run inside the container.

*[HyP3]: Hybrid Pluggable Processing Pipeline
