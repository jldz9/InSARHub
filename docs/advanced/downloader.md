The InSARHub Downloader module provides a streamlined interface for searching and downloading satellite data.

- **Import downloader**

    Import the Downloader class to access all downloader functionality
```python
from insarhub import Downloader
```

- **View available downloaders**

    List all registered downloaders
```python
Downloader.available()
```

## Available Downloaders

=== "ASF_Base_Downloader"

    InSARHub wrapped [asf_search](https://github.com/asfadmin/Discovery-asf_search) as one of its download backends. The `ASF_Base_Downloader` is implemented on top of a reusable base configuration class, which provides the full searching, filtering, and downloading logic of asf_search.

    ::: insarhub.downloader.asf_base.ASF_Base_Downloader
        options:
            heading_level: 0
            members: false

    ### Usage

    - **Create downloader with parameters**

        Initialize a downloader instance with search criteria

        ```python
        s1 = Downloader.create('ASF_Base_Downloader',
                                intersectsWith=[-113.05, 37.74, -112.68, 38.00],
                                dataset='SENTINEL-1',
                                instrument='C-SAR',
                                beamMode='IW',
                                polarization=['VV', 'VV+VH'],
                                processingLevel='SLC',
                                start='2020-01-01',
                                end='2020-12-31',
                                relativeOrbit=100,
                                frame=466,
                                workdir='path/to/dir')
        ```
        OR
        ```python
        params = {
            "intersectsWith": [-113.05, 37.74, -112.68, 38.00],
            "dataset": "SENTINEL-1",
            "instrument": "C-SAR",
            "beamMode": "IW",
            "polarization": ["VV", "VV+VH"],
            "processingLevel": "SLC",
            "start": "2020-01-01",
            "end": "2020-12-31",
            "relativeOrbit": 100,
            "frame": 466,
            "workdir": "path/to/dir"
        }
        dl = Downloader.create('ASF_Base_Downloader', **params)
        ```
        OR
        ```python
        from insarhub.config import ASF_Base_Config
        cfg = ASF_Base_Config(intersectsWith=[-113.05, 37.74, -112.68, 38.00],
                              dataset='SENTINEL-1',
                              instrument='C-SAR',
                              beamMode='IW',
                              polarization=['VV', 'VV+VH'],
                              processingLevel='SLC',
                              start='2020-01-01',
                              end='2020-12-31',
                              relativeOrbit=100,
                              frame=466,
                              workdir='path/to/dir')
        dl = Downloader.create('ASF_Base_Downloader', config=cfg)
        ```

        The base config `ASF_Base_Config` contains all parameters from asf_search keywords. For detailed descriptions refer to the [official ASF Search documentation](https://docs.asf.alaska.edu/asf_search/searching/#searching).

        ::: insarhub.config.ASF_Base_Config
            options:
                heading_level: 0
                members: false

    - **Search**

        Query the satellite archive and retrieve available scenes matching your criteria

        ```python
        results = dl.search()
        ```

        ::: insarhub.downloader.ASF_Base_Downloader.search
            options:
                show_source: false
                heading_level: 5

    - **Filter**

        Refine existing search results by applying additional constraints

        ```python
        filter_result = dl.filter(start='2020-02-01')
        ```

        ::: insarhub.downloader.ASF_Base_Downloader.filter
            options:
                show_source: false
                heading_level: 5

    - **Reset filter**

        Restore search results to the original unfiltered state

        ```python
        dl.reset()
        ```

        ::: insarhub.downloader.ASF_Base_Downloader.reset
            options:
                show_source: false
                heading_level: 5

    - **Summary**

        Display statistics and overview of current search results

        ```python
        dl.summary()
        ```

        ::: insarhub.downloader.ASF_Base_Downloader.summary
            options:
                show_source: false
                heading_level: 5

    - **View Footprint**

        Visualize geographic coverage of search results on an interactive map

        ```python
        dl.footprint()
        ```

        ::: insarhub.downloader.ASF_Base_Downloader.footprint
            options:
                show_source: false
                heading_level: 5

    - **Download**

        Download all scenes from current search results to local storage

        ```python
        dl.download()
        ```

        ::: insarhub.downloader.ASF_Base_Downloader.download
            options:
                show_source: false
                heading_level: 5

    - **DEM Download**

        Download DEM covering all scenes from current search results

        ```python
        dl.dem()
        ```

        ::: insarhub.downloader.ASF_Base_Downloader.dem
            options:
                show_source: false
                heading_level: 5

    - **Select Pairs**

        Compute interferogram pairs for all active stacks based on temporal and perpendicular baseline constraints. Scenes with poor acquisition conditions (heavy rain, snow) are excluded automatically when `avoid_low_quality_days=True` (default).

        ```python
        from insarhub.utils import plot_pair_network
        pairs, baselines, scene_bperp, _ = dl.select_pairs(
            dt_targets=(6, 12, 24, 36, 48, 72, 96),
            dt_tol=3,
            dt_max=120,
            pb_max=150.0,
            min_degree=3,
            max_degree=5,
            force_connect=True,
            avoid_low_quality_days=True,
            precip_mm_threshold=25.0,
            snow_threshold=0.5,
        )
        fig = plot_pair_network(pairs, baselines, scene_bperp)
        fig.show()
        ```

        ::: insarhub.downloader.ASF_Base_Downloader.select_pairs
            options:
                show_source: false
                heading_level: 5

=== "S1_SLC"

    `S1_SLC` is a specialized downloader that extends `ASF_Base_Downloader`, preconfigured specifically for downloading Sentinel-1 SLC data.

    ::: insarhub.downloader.s1_slc.S1_SLC
        options:
            show_source: true
            heading_level: 0
            members: false

    ### Usage

    - **Create downloader with parameters**

        Initialize a downloader instance with search criteria

        ```python
        s1 = Downloader.create('S1_SLC',
                                intersectsWith=[-113.05, 37.74, -112.68, 38.00],
                                start='2020-01-01',
                                end='2020-12-31',
                                relativeOrbit=100,
                                frame=466,
                                workdir='path/to/dir')
        ```
        OR
        ```python
        params = {
            "intersectsWith": [-113.05, 37.74, -112.68, 38.00],
            "start": "2020-01-01",
            "end": "2020-12-31",
            "relativeOrbit": 100,
            "frame": 466,
            "workdir": "path/to/dir"
        }
        dl = Downloader.create('S1_SLC', **params)
        ```
        OR
        ```python
        from insarhub.config import S1_SLC_Config
        cfg = S1_SLC_Config(intersectsWith=[-113.05, 37.74, -112.68, 38.00],
                            start="2020-01-01",
                            end="2020-12-31",
                            relativeOrbit=100,
                            frame=466,
                            workdir="path/to/dir")
        dl = Downloader.create('S1_SLC', config=cfg)
        ```

        The config `S1_SLC_Config` contains pre-defined parameters specifically for Sentinel-1 data. For detailed descriptions refer to the [official ASF Search documentation](https://docs.asf.alaska.edu/asf_search/searching/#searching).

        ::: insarhub.downloader.s1_slc.S1_SLC_Config
            options:
                heading_level: 0
                members: false

    - **Search**

        ```python
        results = dl.search()
        ```

        ::: insarhub.downloader.s1_slc.S1_SLC.search
            options:
                show_source: false
                heading_level: 5

    - **Filter**

        ```python
        filter_result = dl.filter(start='2020-02-01')
        ```

        ::: insarhub.downloader.s1_slc.S1_SLC.filter
            options:
                show_source: false
                heading_level: 5

    - **Reset filter**

        ```python
        dl.reset()
        ```

        ::: insarhub.downloader.s1_slc.S1_SLC.reset
            options:
                show_source: false
                heading_level: 5

    - **Summary**

        ```python
        dl.summary()
        ```

        ::: insarhub.downloader.s1_slc.S1_SLC.summary
            options:
                show_source: false
                heading_level: 5

    - **View Footprint**

        ```python
        dl.footprint()
        ```

        ::: insarhub.downloader.s1_slc.S1_SLC.footprint
            options:
                show_source: false
                heading_level: 5

    - **Download**

        ```python
        dl.download()
        ```

        ::: insarhub.downloader.s1_slc.S1_SLC.download
            options:
                show_source: false
                heading_level: 5

    - **DEM Download**

        ```python
        dl.dem()
        ```

        ::: insarhub.downloader.s1_slc.S1_SLC.dem
            options:
                show_source: false
                heading_level: 5

    - **Select Pairs**

        ```python
        from insarhub.utils import plot_pair_network
        pairs, baselines, scene_bperp, _ = s1.select_pairs(
            dt_targets=(6, 12, 24, 36, 48, 72, 96),
            dt_tol=3,
            dt_max=120,
            pb_max=150.0,
            min_degree=3,
            max_degree=5,
            force_connect=True,
            avoid_low_quality_days=True,
            precip_mm_threshold=25.0,
            snow_threshold=0.5,
        )
        fig = plot_pair_network(pairs, baselines, scene_bperp)
        fig.show()
        ```

        ::: insarhub.downloader.ASF_Base_Downloader.select_pairs
            options:
                show_source: false
                heading_level: 5

=== "S1_Burst"

    `S1_Burst` is a specialized downloader that extends `ASF_Base_Downloader` for ASF's **SLC-BURST** dataset. A burst is roughly 1/9th of a full IW slice, so an AOI-limited burst stack pulls far less data than an equivalent `S1_SLC` search — the whole point of burst-based processing, and what makes the `ISCE3_Burst` / COMPASS workflow practical over a small target. Pair it with the `ISCE3_Burst` processor and the `Dolphin_SBAS` analyzer.

    Search, filter, summary, footprint and pair selection behave exactly as for `S1_SLC` (they reuse `ASF_Base_Downloader`); only `download()` differs — it hands the selected burst granules to `burst2safe`, which assembles them into valid `.SAFE` directories by merging the annotation/calibration/noise XML and writing a manifest.

    !!! note "Burst stacks are keyed by `fullBurstID`, not frame"
        ASF returns no `frameNumber` on SLC-BURST products, so a frame filter matches nothing and is excluded from the query. A burst stack is identified by `fullBurstID` (e.g. `056_118970_IW2`); the downloader folder is named `p<path>_iw<s>_b<id>` accordingly.

    ::: insarhub.downloader.s1_burst.S1_Burst
        options:
            show_source: true
            heading_level: 0

    - **Create downloader with parameters**

        ```python
        s1b = Downloader.create('S1_Burst',
                                intersectsWith=[-106.06, 40.34, -105.70, 40.58],
                                fullBurstID=['056_118970_IW2', '056_118971_IW2'],
                                polarization=['VV'],
                                start='2022-08-04',
                                end='2026-07-21',
                                workdir='path/to/dir')
        ```

        OR with explicit config:

        ```python
        from insarhub.config import S1_Burst_Config

        cfg = S1_Burst_Config(
            intersectsWith=[-106.06, 40.34, -105.70, 40.58],
            fullBurstID=['056_118970_IW2', '056_118971_IW2'],
            polarization=['VV'],
            start='2022-08-04',
            end='2026-07-21',
            workdir='path/to/dir',
        )
        dl = Downloader.create('S1_Burst', config=cfg)
        ```

        ::: insarhub.config.defaultconfig.S1_Burst_Config
            options:
                heading_level: 0
                members: false

    - **Search / Filter / Summary / Footprint**

        Identical to `S1_SLC` — these reuse `ASF_Base_Downloader` and operate on the same ASF burst granule search.

        ```python
        results = dl.search()
        dl.summary()
        dl.footprint()
        ```

    - **Select Pairs**

        ```python
        pairs, baselines, scene_bperp, _ = dl.select_pairs(
            dt_targets=(6, 12, 24, 36, 48, 72, 96),
            dt_tol=3,
            dt_max=120,
            pb_max=150.0,
            force_connect=True,
        )
        ```

        ::: insarhub.downloader.ASF_Base_Downloader.select_pairs
            options:
                show_source: false
                heading_level: 5

    - **Download**

        Download the selected burst granules and assemble them into `.SAFE` directories via `burst2safe`.

        ```python
        dl.download()
        ```

        ::: insarhub.downloader.s1_burst.S1_Burst.download
            options:
                show_source: false
                heading_level: 5
