# Backend Contributing Guide

The backend is pure Python — FastAPI routes, processor/analyzer classes, CLI, and shared utilities.

## Setup

```bash
conda env create -f environment.yml
conda activate insarhub
pip install -e ".[dev]"
```

## Architecture Overview

InSARHub uses a registry pattern. Every `Processor`, `Analyzer`, and `Downloader` subclass with a `name` attribute is automatically discovered and available via `Processor.create("MyName", cfg)`.

```
CloudProcessor (ABC) ──► Hyp3Base   ──► Hyp3_S1
LocalProcessor (ABC) ──► ISCE_Base ──► ISCE_S1

BaseDownloader (ABC) ──► ASF_Base_Downloader ──► S1_SLC

BaseAnalyzer (ABC) ──► Mintpy_SBAS_Base_Analyzer ──► Hyp3_SBAS
                                                  └──► ISCE_SBAS
```

Each mid-layer base class (`Hyp3Base`, `ISCE_Base`, `ASF_Base_Downloader`, `Mintpy_SBAS_Base_Analyzer`) implements all the shared infrastructure — auth, job tracking, HPC submission, file I/O. Concrete leaf classes only need to implement `submit()` (and `prep_data()` for analyzers) with sensor-specific logic.

The CLI (`cli/main.py`) and GUI routes (`app/routes/`) are thin shells over the same Python API — any workflow that works from the CLI works identically in the browser.

## Path Conventions

All sub-directory paths are centralized in `config/paths.py`. Never hardcode `workdir / "hyp3"` — use the dataclass properties:

```python
from insarhub.config.paths import Hyp3Paths, ISCEPaths, MintPyPaths

Hyp3Paths(workdir).output_dir       # workdir/hyp3
Hyp3Paths(workdir).jobs_file        # workdir/hyp3_jobs.json

ISCEPaths(workdir).isce_dir         # workdir/isce
ISCEPaths(workdir).slc_dir          # workdir/slc
ISCEPaths(workdir).dem_dir          # workdir/dem

MintPyPaths(workdir).mintpy_dir     # workdir/mintpy
MintPyPaths(workdir).tmp_dir        # workdir/mintpy/tmp
MintPyPaths(workdir).clip_dir       # workdir/mintpy/clip
```

If a new processor writes to a new subdirectory, add a new dataclass to `config/paths.py`.

## Adding a New Processor

Create `src/insarhub/processor/myprocessor.py`, set `name`, add a config dataclass in `config/defaultconfig.py`. Extend the appropriate base class — each handles all shared infrastructure, so the subclass only writes `submit()`:

### Adding a New Base Processor

To introduce a new mid-layer base (e.g. a backend API beyond HyP3 or ISCE2), inherit directly from the ABCs in `insarhub/core/base.py`:

- `CloudProcessor` — for cloud-based processors that submit jobs to an external API
- `LocalProcessor` — for locally-executed processors that run shell commands step-by-step

Implement all abstract methods, then subclass your new base for each sensor.

=== "CloudProcessor"

    ```python
    # src/insarhub/processor/mycloud_base.py
    from insarhub.core.base import CloudProcessor
    from insarhub.config import MyCloud_Base_Config

    class MyCloud_Base(CloudProcessor):
        # No `name` here — base classes must NOT register themselves
        default_config = MyCloud_Base_Config

        def __init__(self, config=None):
            super().__init__(config)
            self.client = MyCloudAPIClient(
                username=self.config.username,
                password=self.config.password,
            )

        def submit(self): ...
        def refresh(self): ...
        def download(self, *args, **kwargs): ...
        def retry(self): ...
        def watch(self): ...
        def save(self, path=None): ...
        def check_credits(self): ...
    ```

=== "LocalProcessor"

    ```python
    # src/insarhub/processor/mylocal_base.py
    from insarhub.core.base import LocalProcessor
    from insarhub.config import MyLocal_Base_Config

    class MyLocal_Base(LocalProcessor):
        # No `name` here — base classes must NOT register themselves
        default_config = MyLocal_Base_Config

        def submit(self): ...   # generate run scripts, stage inputs
        def refresh(self): ...  # re-scan .done / .fail step markers
        def retry(self): ...    # clear .fail markers and re-run
        def watch(self): ...    # block until all steps complete
        def save(self, path=None): ...
    ```

### Extending an Existing Base Processor

=== "Hyp3Base"

    `Hyp3Base` handles Earthdata auth, multi-user credit pool rotation, job submission queueing, `refresh()`, `download()`, `retry()`, `watch()`, and `save()`. Only `submit()` is needed — prepare payloads and call `_submit_job_queue`.

    ```python
    # src/insarhub/processor/hyp3_mysensor.py
    from insarhub.processor.hyp3_base import Hyp3Base
    from insarhub.config import MyHyp3Config

    class Hyp3_MySensor(Hyp3Base):
        name = "Hyp3_MySensor"
        description = "HyP3 processing for MySensor."
        compatible_downloader = "MySensor_SLC"
        default_config = MyHyp3Config

        def __init__(self, config: MyHyp3Config | None = None):
            super().__init__(config)
            self.cost = self.client.costs()["MY_JOB_TYPE"]["cost_table"]["default"]

        def submit(self):
            job_queue = [
                {
                    "job_type": "MY_JOB_TYPE",
                    "job_parameters": {"granules": [ref, sec], "looks": self.config.looks},
                    "name": f"{self.config.name_prefix}_{ref[:15]}",
                }
                for ref, sec in self.config.pairs
            ]
            return self._submit_job_queue(job_queue)
    ```

    Config — inherit from `Hyp3_Base_Config`:

    ```python
    @dataclass
    class MyHyp3Config(Hyp3_Base_Config):
        looks: str = "20x4"

        _ui_groups = [{"id": "job", "label": "Job"}]
        _ui_fields  = [
            {"group": "job", "key": "looks", "label": "Looks",
             "type": "select", "options": ["20x4", "10x2"]},
        ]
    ```

=== "ISCE_Base"

    `ISCE_Base` handles run-file execution, per-step status tracking (`.done`/`.fail`), sliding-window HPC submission via SLURM, `refresh()`, `retry()`, `watch()`, and `save()`. Only `submit()` is needed — set up the ISCE2 input namespace and generate run scripts, then call `_step_executor`.

    ```python
    # src/insarhub/processor/isce_mysensor.py
    from insarhub.processor.isce_base import ISCE_Base
    from insarhub.config import ISCE_MySensor_Config
    from insarhub.config.paths import ISCEPaths

    class ISCE_MySensor(ISCE_Base):
        name = "ISCE_MySensor"
        description = "ISCE2 processing for MySensor."
        compatible_downloader = "MySensor_SLC"
        default_config = ISCE_MySensor_Config

        def submit(self):
            ISCEPaths(self.workdir).isce_dir.mkdir(parents=True, exist_ok=True)

            # Build ISCE2 input namespace for your sensor, then generate run_files/
            inps = self._build_inps_namespace()
            self._run_stack_tool(inps)

            # Hand off — ISCE_Base discovers run_files/ and executes each step
            self._step_executor(self.steps)
    ```

    Config — inherit from `ISCE_Base_Config` and add `_ui_groups` / `_ui_fields` for any new fields.

## Adding a New Downloader

Create `src/insarhub/downloader/mysensor_slc.py`. Extend `ASF_Base_Downloader`, which already handles ASF auth, scene search, footprint plotting, pair selection with quality scoring, and parallel file download. Override `download()` only if extra post-download steps are needed.

### Adding a New Base Downloader

To support a data archive other than ASF, inherit directly from `BaseDownloader` in `insarhub/core/base.py`. Implement all abstract methods, then subclass your new base for each product type.

```python
# src/insarhub/downloader/myarchive_base.py
from insarhub.core.base import BaseDownloader
from insarhub.config import MyArchive_Base_Config

class MyArchive_Base(BaseDownloader):
    # No `name` here — base classes must NOT register themselves
    default_config = MyArchive_Base_Config

    def search(self, *args, **kwargs): ...   # query archive, populate self.active_results
    def download(self, *args, **kwargs): ... # fetch files to workdir
    def filter(self, *args, **kwargs): ...   # narrow active_results by user criteria
    def footprint(self, *args, **kwargs): ...# return GeoJSON footprints for map display
    def summary(self, *args, **kwargs): ...  # return human-readable result summary
    def reset(self, *args, **kwargs): ...    # clear search state
```

### Extending an Existing Base Downloader

=== "ASF_Base_Downloader"

    ```python
    # src/insarhub/downloader/mysensor_slc.py
    from insarhub.downloader.asf_base import ASF_Base_Downloader
    from insarhub.config import MySensor_SLC_Config

    class MySensor_SLC(ASF_Base_Downloader):
        name = "MySensor_SLC"
        description = "MySensor SLC search and download via ASF."
        default_config = MySensor_SLC_Config

        def download(self, save_path=None, max_workers=4,
                     download_aux=False, stop_event=None, on_progress=None):
            super().download(save_path=save_path, max_workers=max_workers,
                             stop_event=stop_event, on_progress=on_progress)
            if download_aux:
                self._download_aux_files()

        def _download_aux_files(self):
            ...
    ```

    After `search()` is called, `self.active_results` holds the ASF result list and `self.config.workdir` is the resolved workdir.

## Adding a New Analyzer

Create `src/insarhub/analyzer/mysensor_sbas.py`. Extend `Mintpy_SBAS_Base_Analyzer`, which handles MintPy config writing, `run()` (calls `TimeSeriesAnalysis` into `mintpy_dir`), diagnostic geocoding, and `cleanup()`. Only `prep_data()` is needed — stage input files and wire `load_*` config fields.

### Adding a New Base Analyzer

To support a time-series package other than MintPy, inherit directly from `BaseAnalyzer` in `insarhub/core/base.py`. Implement all abstract methods, then subclass your new base for each input data format.

```python
# src/insarhub/analyzer/myts_base.py
from insarhub.core.base import BaseAnalyzer
from insarhub.config import MyTS_Base_Config

class MyTS_Base(BaseAnalyzer):
    # No `name` here — base classes must NOT register themselves
    default_config = MyTS_Base_Config

    def run(self): ...  # execute the time-series analysis
```

### Extending an Existing Base Analyzer

=== "Mintpy_SBAS_Base_Analyzer"

    ```python
    # src/insarhub/analyzer/mysensor_sbas.py
    from insarhub.analyzer.mintpy_base import Mintpy_SBAS_Base_Analyzer
    from insarhub.config import MySensor_SBAS_Config

    class MySensor_SBAS(Mintpy_SBAS_Base_Analyzer):
        name = "MySensor_SBAS"
        description = "SBAS time-series for MySensor products using MintPy."
        compatible_processor = "MySensor_Processor"
        default_config = MySensor_SBAS_Config

        def prep_data(self):
            self._collect_and_stage_files()   # unpack/collect into self.tmp_dir

            # Wire MintPy load_* fields
            self.config.load_unwFile      = str(self.tmp_dir / "*" / "unw_phase.tif")
            self.config.load_corFile      = str(self.tmp_dir / "*" / "corr.tif")
            self.config.load_demFile      = str(self.tmp_dir / "*" / "dem.tif")

            super().prep_data()   # writes .mintpy.cfg

        def _collect_and_stage_files(self):
            ...
    ```

    `run()` is inherited — writes all MintPy output to `self.mintpy_dir` (`workdir/mintpy/`).

## Exposing Settings in the GUI

Config fields appear in the Web UI settings panel automatically via `_ui_groups` and `_ui_fields` on the config dataclass. No React changes needed:

```python
@dataclass
class MyProcessorConfig:
    max_workers: int = 4

    _ui_groups = [{"id": "job", "label": "Job"}]
    _ui_fields = [
        {"group": "job", "key": "max_workers", "label": "Max Workers",
         "type": "number", "min": 1, "max": 32},
    ]
```

Supported field types: `"number"`, `"text"`, `"boolean"`, `"select"` (add `"options": [...]`).

## Adding a FastAPI Route

Routes live in `app/routes/`. Long-running operations run in a background thread via `asyncio.to_thread` and communicate progress through `state._jobs[job_id]`:

```python
@router.post("/api/my-action")
async def my_action(req: MyRequest, background_tasks: BackgroundTasks):
    job_id, _ = _new_job("Starting…")
    background_tasks.add_task(_run_my_action, job_id, req)
    return {"job_id": job_id}

async def _run_my_action(job_id: str, req: MyRequest):
    def run():
        try:
            # ... do work ...
            state._jobs[job_id]["progress"] = 50
            # ...
            _finish_job(job_id, status="done", message="Done.")
        except Exception as e:
            state._stop_events.pop(job_id, None)
            _finish_job(job_id, status="error", message=str(e))
    await asyncio.to_thread(run)
```

Always pop `state._stop_events[job_id]` before returning on both success and error paths.

## Code Style

- No comments explaining *what* the code does — only *why* (hidden constraint, workaround, subtle invariant).
- No error handling for scenarios that cannot happen.
- Use `Hyp3Paths` / `ISCEPaths` / `MintPyPaths` for all workdir sub-paths.
- Prefer editing existing files over creating new abstractions.
