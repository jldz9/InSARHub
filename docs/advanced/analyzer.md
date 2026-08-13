The InSARHub analyzer module provides workflow for InSAR time-series analysis.

- **Import analyzer**

    Import the Analyzer class to access all time-series analysis functionality
```python
from insarhub import Analyzer
```

- **View Available Analyzers**

    List all registered analyzers
```python
Analyzer.available()
```

## Available Analyzers

=== "Mintpy_SBAS_Base_Analyzer"

    InSARHub wrapped [Mintpy](https://github.com/insarlab/MintPy) as one of its analysis backends. The `Mintpy_SBAS_Base_Analyzer` is implemented on top of a reusable base configuration class, which provides the full `smallbaselineApp` logic of Mintpy. Provides users with an experience similar to using MintPy directly, allowing full customization of processing parameters and steps.

    ::: insarhub.analyzer.mintpy_base.Mintpy_SBAS_Base_Analyzer
        options:
            members: false
            heading_level: 0

    ### Usage

    - **Create Analyzer with Parameters**

        Initialize an analyzer instance

        ```python
        analyzer = Analyzer.create('Mintpy_SBAS_Base_Analyzer',
                                    workdir="/your/work/dir",
                                    load_processor="hyp3", ....)
        ```
        OR
        ```python
        params = {"workdir": "/your/work/dir", "load_processor": "hyp3" ....}
        analyzer = Analyzer.create('Mintpy_SBAS_Base_Analyzer', **params)
        ```
        OR
        ```python
        from insarhub.config import Mintpy_SBAS_Base_Config
        cfg = Mintpy_SBAS_Base_Config(workdir="/your/work/dir",
                                      load_processor="hyp3",
                                      ....)
        analyzer = Analyzer.create('Mintpy_SBAS_Base_Analyzer', config=cfg)
        ```

        The base config `Mintpy_SBAS_Base_Config` contains all parameters from Mintpy `smallbaselineApp.cfg`. For detailed descriptions refer to the [official Mintpy config documentation](https://github.com/insarlab/MintPy/blob/054c6010b5e40e98fe16e283121fdd1ae4bc1732/src/mintpy/defaults/smallbaselineApp.cfg).

        ::: insarhub.config.Mintpy_SBAS_Base_Config
            options:
                members: false
                heading_level: 0

    - **Run**

        Run the Mintpy time-series analysis based on provided configuration

        ```python
        analyzer.run()
        ```

        ::: insarhub.analyzer.Mintpy_SBAS_Base_Analyzer.run
            options:
                members: true
                show_source: false
                heading_level: 5

    - **Submit (HPC / SLURM mode)**

        Generate a single `sbatch` script covering all selected steps and submit it to SLURM. Inherited by `Hyp3_SBAS` and `ISCE_SBAS`.

        ```python
        # Submit full pipeline as one SLURM job
        analyzer.submit_hpc()

        # Submit only specific steps
        analyzer.submit_hpc(steps=["velocity", "geocode"])
        ```

        The script is written to `<workdir>/mintpy/mintpy_sbas.sbatch` and job state to `mintpy/mintpy_job.json`. SLURM resources come from `<workdir>/sbatch_options.json`, step key `"17"` — the same file `ISCE2_S1`'s own HPC submission uses for steps `01`–`16`, since the processor and analyzer typically share one workdir. Default: `time=24:00:00`, `ntasks=1`, `cpus_per_task=16`, `mem=128G`, `partition=all`.

        `submit_hpc()` returns the SLURM job ID string on success, or `None` if `sbatch_options.json` was just created (or updated with a missing `"17"` entry) — callers should check for `None` and stop rather than treat it as a successful submission:

        ```python
        cfg = Mintpy_SBAS_Base_Config(
            workdir="/your/work/dir",
            load_processor="hyp3",
            hpc_mode=True,
        )
        analyzer = Analyzer.create('Hyp3_SBAS', config=cfg)
        job_id = analyzer.submit_hpc()
        if job_id is None:
            print("sbatch_options.json was just created/updated — review it, then resubmit.")
        ```

        Edit step `"17"` in `sbatch_options.json` directly to change resources (e.g. `{"17": {"time": "48:00:00", "mem": "256G", "partition": "gpu"}}`), then call `submit_hpc()` again.

        ::: insarhub.analyzer.mintpy_base.Mintpy_SBAS_Base_Analyzer.submit_hpc
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Plot**

        (Re)generate the figures under `mintpy/pic/` from already-computed results, without recomputing anything. `run()`'s own auto-plot only fires for a single call covering more than one step (mirroring MintPy's own CLI semantics) — the CLI and GUI execute steps one at a time internally for per-step progress reporting, so that condition never actually fires there; `plot()` is the explicit, standalone alternative both call once after their step sequence completes (or on demand, e.g. after tweaking a plotting-related config value and wanting fresh figures without rerunning the whole pipeline).

        ```python
        analyzer.plot()
        ```

        ::: insarhub.analyzer.mintpy_base.Mintpy_SBAS_Base_Analyzer.plot
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Running without a local MintPy (or ISCE2) install**

        Set the `container` field to a path to an Apptainer/Singularity `.sif` image, or a Docker image reference (name[:tag]), and `run()`/`prep_data()`/`submit_hpc()` all re-invoke the same `insarhub analyzer ...` CLI call inside that container instead of on the host — the workdir is bind-mounted at the identical path, so output lands exactly where a native run would put it. The container image just needs `insarhub` installed alongside MintPy (and ISCE2, for `ISCE_SBAS`) — see [`Dockerfile`](https://github.com/jldz9/InSARHub/blob/main/Dockerfile) in the repo root for a ready-to-build example.

        ```python
        cfg = Mintpy_SBAS_Base_Config(
            workdir="/your/work/dir",
            load_processor="hyp3",
            container="ghcr.io/jldz9/insarhub-isce2:latest",
        )
        analyzer = Analyzer.create('Hyp3_SBAS', config=cfg)
        analyzer.run()
        ```

        `container` is a per-invocation setting, not persisted config — it must be set again on every subsequent call that should also run inside the container.

    - **Clean up**

        Remove intermediate processing files generated during the time-series process

        ```python
        analyzer.cleanup()
        ```

        ::: insarhub.analyzer.Mintpy_SBAS_Base_Analyzer.cleanup
            options:
                members: true
                show_source: false
                heading_level: 5

=== "Hyp3_SBAS"

    The `Hyp3_SBAS` is a specialized analyzer that extends `Mintpy_SBAS_Base_Analyzer`, preconfigured specifically for processing time-series data from HyP3 InSAR products.

    ::: insarhub.analyzer.Hyp3_SBAS
        options:
            members: false
            heading_level: 0

    ### Usage

    - **Create Analyzer with Parameters**

        Initialize an analyzer instance

        ```python
        analyzer = Analyzer.create('Hyp3_SBAS',
                                    workdir="/your/work/dir")
        ```
        OR
        ```python
        params = {"workdir": "/your/work/dir"}
        analyzer = Analyzer.create('Hyp3_SBAS', **params)
        ```
        OR
        ```python
        from insarhub.config import Mintpy_SBAS_Base_Config
        cfg = Mintpy_SBAS_Base_Config(workdir="/your/work/dir")
        analyzer = Analyzer.create('Hyp3_SBAS', config=cfg)
        ```

    - **Prepare data**

        Prepare interferogram data downloaded from HyP3 server for MintPy

        ```python
        analyzer.prep_data()
        ```

        ::: insarhub.analyzer.Hyp3_SBAS.prep_data
            options:
                members: false
                heading_level: 5

    - **Run**

        Run the Mintpy time-series analysis based on provided configuration

        ```python
        analyzer.run()
        ```

        ::: insarhub.analyzer.Hyp3_SBAS.run
            options:
                members: false
                heading_level: 5

    - **Submit (HPC / SLURM mode)**

        Inherited from `Mintpy_SBAS_Base_Analyzer`. Submit full MintPy run as a single sbatch job.

        ```python
        analyzer.submit_hpc()
        ```

    - **Clean up**

        Remove intermediate processing files generated during the time-series process

        ```python
        analyzer.cleanup()
        ```

        ::: insarhub.analyzer.Mintpy_SBAS_Base_Analyzer.cleanup
            options:
                members: true
                show_source: false
                heading_level: 5

=== "ISCE_SBAS"

    The `ISCE_SBAS` analyzer extends `Mintpy_SBAS_Base_Analyzer` and is preconfigured for ISCE2 `stackSentinel` outputs. `prep_data()` auto-discovers interferograms and geometry from the `isce/` directory and writes the MintPy config to `mintpy/.mintpy.cfg`. All MintPy outputs are written to `workdir/mintpy/`.

    ::: insarhub.analyzer.isce_sbas.ISCE_SBAS
        options:
            members: false
            heading_level: 0

    ### Usage

    - **Create Analyzer**

        ```python
        from insarhub import Analyzer

        analyzer = Analyzer.create('ISCE_SBAS', workdir='/your/work/dir')
        ```

        OR with explicit config:

        ```python
        from insarhub.config.defaultconfig import ISCE_SBAS_Config

        cfg = ISCE_SBAS_Config(workdir='/your/work/dir')
        analyzer = Analyzer.create('ISCE_SBAS', config=cfg)
        ```

        ::: insarhub.config.defaultconfig.ISCE_SBAS_Config
            options:
                members: false
                show_source: false
                heading_level: 0

    - **Prepare data**

        Auto-discover ISCE2 outputs and write `mintpy/.mintpy.cfg`.

        ```python
        analyzer.prep_data()
        ```

        ::: insarhub.analyzer.isce_sbas.ISCE_SBAS.prep_data
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Run**

        Run MintPy SBAS time-series analysis. All output written to `workdir/mintpy/`.

        ```python
        analyzer.run()
        ```

        ::: insarhub.analyzer.isce_sbas.ISCE_SBAS.run
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Submit (HPC / SLURM mode)**

        Inherited from `Mintpy_SBAS_Base_Analyzer`. Submit full MintPy run as a single sbatch job.

        ```python
        analyzer.submit_hpc()
        ```

    - **Clean up**

        Remove large ISCE2 intermediate directories and input data no longer needed after `load_data`.
        Removes `isce/coarse_interferograms/`, `isce/ESD/`, `isce/coreg_secondarys/`, `isce/interferograms/`, `slc/`, and `dem/`.

        ```python
        analyzer.cleanup()
        ```

        ::: insarhub.analyzer.isce_sbas.ISCE_SBAS.cleanup
            options:
                members: false
                show_source: false
                heading_level: 5

=== "GMTSAR_MINTPY_SBAS"

    The `GMTSAR_MINTPY_SBAS` analyzer runs MintPy SBAS time-series on the coherent stack produced by the `GMTSAR_S1` processor. It hands GMTSAR's geocoded `*_ll.grd` products and `baseline_table.dat` to MintPy's own `prep_gmtsar.py` loader (via the `mintpy.load.*` keys), so it works without any common alignment reference — every pair already shares a geographic grid. It is the MintPy analogue of `ISCE_SBAS`, differing only in how it wires the `load_*` paths. Output is written to `workdir/gmtsar_mintpy/` (a dedicated directory, so it never collides with a Hyp3/ISCE MintPy run in the same workdir).

    ::: insarhub.analyzer.gmtsar_mintpy_sbas.GMTSAR_MINTPY_SBAS
        options:
            members: false
            heading_level: 0

    ### Usage

    - **Create Analyzer**

        ```python
        from insarhub import Analyzer

        analyzer = Analyzer.create('GMTSAR_MINTPY_SBAS', workdir='/your/work/dir')
        ```

        OR with explicit config:

        ```python
        from insarhub.config.defaultconfig import GMTSAR_MINTPY_SBAS_Config

        cfg = GMTSAR_MINTPY_SBAS_Config(workdir='/your/work/dir')
        analyzer = Analyzer.create('GMTSAR_MINTPY_SBAS', config=cfg)
        ```

        ::: insarhub.config.defaultconfig.GMTSAR_MINTPY_SBAS_Config
            options:
                members: false
                show_source: false
                heading_level: 0

    - **Prepare data**

        Discover GMTSAR output (stack_mode `merge/<julian_pair>/`, or p2p `gmtsar/<ref>_<sec>/merge/`) and write the MintPy config. For p2p output it stages the merged `unwrap_ll.grd`/`corr_ll.grd` into the `<pair>/unwrap_ll.grd` shape MintPy expects (symlinked, no multi-GB copies), keeping GMTSAR's Julian `yyyyddd_yyyyddd` directory naming that `prep_gmtsar.py` derives pair dates from.

        ```python
        analyzer.prep_data()
        ```

        ::: insarhub.analyzer.gmtsar_mintpy_sbas.GMTSAR_MINTPY_SBAS.prep_data
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Run**

        Run MintPy SBAS time-series analysis. All output is written to `workdir/gmtsar_mintpy/`.

        ```python
        analyzer.run()
        ```

        ::: insarhub.analyzer.gmtsar_mintpy_sbas.GMTSAR_MINTPY_SBAS.run
            options:
                members: false
                show_source: false
                heading_level: 5

    - **HPC submission**

        Inherited from `Mintpy_SBAS_Base_Analyzer`. Submit the full MintPy run as a single sbatch job (written to `workdir/gmtsar_mintpy/mintpy_sbas.sbatch`).

        ```python
        analyzer.submit_hpc()
        ```

    - **Clean up**

        ```python
        analyzer.cleanup()
        ```

        ::: insarhub.analyzer.mintpy_base.Mintpy_SBAS_Base_Analyzer.cleanup
            options:
                members: true
                show_source: false
                heading_level: 5

=== "GMTSAR_SBAS"

    The `GMTSAR_SBAS` analyzer runs **GMTSAR's own native SBAS inversion** (`prep_sbas` + the `sbas` C binary) on a `GMTSAR_S1` stack_mode stack — no MintPy involved. It consumes `workdir/gmtsar/` (`intf.in`, `baseline_table.dat`, `intf/<pair>/`) and produces the cumulative displacement per date (`disp_*.grd`) and the linear velocity (`vel.grd`) in radar coordinates under `workdir/gmtsar_sbas/`.

    Because the inversion is a GMTSAR C binary, both `gmtsar_root` and `gmtsar_env_bin` are **required** in the config — the `sbas` binary and `gmt` come from GMTSAR's own install/conda env, not InSARHub's.

    ::: insarhub.analyzer.gmtsar_sbas.GMTSAR_SBAS
        options:
            members: false
            heading_level: 0

    ### Usage

    - **Create Analyzer**

        ```python
        from insarhub import Analyzer

        analyzer = Analyzer.create('GMTSAR_SBAS', workdir='/your/work/dir',
                                   gmtsar_root='/path/to/gmtsar',
                                   gmtsar_env_bin='/path/to/conda/envs/gmtsar/bin')
        ```

        OR with explicit config:

        ```python
        from insarhub.config.defaultconfig import GMTSAR_SBAS_Config

        cfg = GMTSAR_SBAS_Config(
            workdir='/your/work/dir',
            gmtsar_root='/path/to/gmtsar',
            gmtsar_env_bin='/path/to/conda/envs/gmtsar/bin',
        )
        analyzer = Analyzer.create('GMTSAR_SBAS', config=cfg)
        ```

        ::: insarhub.config.defaultconfig.GMTSAR_SBAS_Config
            options:
                members: false
                show_source: false
                heading_level: 0

    - **Prepare data**

        Build `intf.tab` and `scene.tab` from the stack's `baseline_table.dat`, then echo the `sbas intf.tab scene.tab N S xdim ydim` command line to run.

        ```python
        analyzer.prep_data()
        ```

        ::: insarhub.analyzer.gmtsar_sbas.GMTSAR_SBAS.prep_data
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Run**

        Run the `sbas` inversion, streaming its progress to the console and `sbas.log` under `workdir/gmtsar_sbas/`.

        ```python
        analyzer.run()
        ```

        ::: insarhub.analyzer.gmtsar_sbas.GMTSAR_SBAS.run
            options:
                members: false
                show_source: false
                heading_level: 5

=== "Dolphin_SBAS"

    The `Dolphin_SBAS` analyzer runs dolphin's `timeseries.run` on the unwrapped interferogram stack produced by `ISCE3_Burst`. One analyzer serves **both** of the processor's wrapped-phase estimators (`ifg_mode="network"` and `ifg_mode="phase_link"`) — the linear inversion is identical for both; only the quality raster used to pick the reference point and mask low-quality pixels differs (temporal coherence for `phase_link`, a temporal average of pairwise correlations for `network`). The estimator choice is read from the stack's `ifg_manifest.json`, never re-specified here. Output is written under `workdir/timeseries/`.

    ::: insarhub.analyzer.dolphin_sbas.Dolphin_SBAS
        options:
            members: false
            heading_level: 0

    ### Usage

    - **Create Analyzer**

        ```python
        from insarhub import Analyzer

        analyzer = Analyzer.create('Dolphin_SBAS', workdir='/your/work/dir')
        ```

        OR with explicit config:

        ```python
        from insarhub.config.defaultconfig import Dolphin_SBAS_Config

        cfg = Dolphin_SBAS_Config(workdir='/your/work/dir')
        analyzer = Analyzer.create('Dolphin_SBAS', config=cfg)
        ```

        ::: insarhub.config.defaultconfig.Dolphin_SBAS_Config
            options:
                members: false
                show_source: false
                heading_level: 0

    - **Run**

        Run the dolphin time-series inversion (cumulative displacement, velocity, residuals) for the stack in this workdir.

        ```python
        analyzer.run()
        ```

        ::: insarhub.analyzer.dolphin_sbas.Dolphin_SBAS.run
            options:
                members: false
                show_source: false
                heading_level: 5
