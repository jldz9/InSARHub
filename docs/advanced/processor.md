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

*[HyP3]: Hybrid Pluggable Processing Pipeline
