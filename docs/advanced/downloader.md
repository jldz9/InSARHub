The InSARHub Downloader module provides a streamlined interface for searching and downloading satellite data.

- **Import downloader**

    Import the Downloader class to access all downloader functionality
```python
from insarhub import Downloader
```

- **View available downloaders**

    List all registered downloader
```python
Downloader.available()
```

## Available Downloaders

### ASF Base Downloader

InSARHub wrapped [asf_search](https://github.com/asfadmin/Discovery-asf_search) as one of its download backends. The `ASF_Base_Downloader` is implemented on top of a reusable base configuration class, which provides the full searching, filtering, and downloading logic of asf_search.

::: insarhub.downloader.asf_base.ASF_Base_Downloader
    options:
        heading_level: 0
        members: false

#### Usage

- **Create downloader with parameters**

    Initialize a downloader instance with search criteria
```python
s1 = Downloader.create('ASF_Base_Downloader', 
                        intersectsWith=[-113.05, 37.74, -112.68, 38.00],
                        dataset='SENTINEL-1',
                        instrument='C-SAR',
                        beamMode='IW',
                        polarization=['VV', 'VV+VH'],
                        processingLevel='SLC'
                        start='2020-01-01', 
                        end='2020-12-31',  
                        relativeOrbit=100, 
                        frame=466, 
                        workdir='path/to/dir')
```
OR:
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
                        processingLevel='SLC'
                        start='2020-01-01', 
                        end='2020-12-31',  
                        relativeOrbit=100, 
                        frame=466, 
                        workdir='path/to/dir')
dl = Downloader.create('ASF_Base_Downloader', config=cfg)
```

    The base configure `ASF_Base_Config` contains all parameters from asf_search keywords. For detailed descriptions and usage of each parameter, please refer to the [official ASF Search documentation](https://docs.asf.alaska.edu/asf_search/searching/#searching).

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

    Download DEM that covers all scenes from current search results to local storatge
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
        avoid_low_quality_days=True,   # exclude scenes with bad weather/snow (default)
        precip_mm_threshold=25.0,      # 3-day precip threshold in mm
        snow_threshold=0.5,            # MODIS snow-cover fraction threshold
    )
    fig = plot_pair_network(pairs, baselines, scene_bperp)
    fig.show()
    ```

    ::: insarhub.downloader.ASF_Base_Downloader.select_pairs
        options:
            show_source: false
            heading_level: 5

### S1_SLC

S1_SLC is a specialized downloader that extends ASF_Base_Downloader, preconfigured specifically for downloading Sentinel-1 SLC data.

::: insarhub.downloader.s1_slc.S1_SLC
    options:
        show_source: true
        heading_level: 0
        members: false

#### Usage

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
cfg = S1_SLC_Config(intersectsWith= [-113.05, 37.74, -112.68, 38.00],
                    start= "2020-01-01",
                    end= "2020-12-31",
                    relativeOrbit= 100,
                    frame= 466,
                    workdir= "path/to/dir")
dl = Downloader.create('S1_SLC', config=cfg)

```


    The configure `S1_SLC_config` contains pre-defined parameters specifically for Sentinel-1 data. For detailed descriptions and usage of each parameter, please refer to the [official ASF Search documentation](https://docs.asf.alaska.edu/asf_search/searching/#searching).

    ::: insarhub.downloader.s1_slc.S1_SLC_Config
        options:
            heading_level: 0
            members: false


- **Search**

    Query the satellite archive and retrieve available scenes matching your criteria
```python
results = dl.search()
```

    ::: insarhub.downloader.s1_slc.S1_SLC.search
        options:
            show_source: false
            heading_level: 5

- **Filter**

    Refine existing search results by applying additional constraints
```python
filter_result = dl.filter(start='2020-02-01')
```

    ::: insarhub.downloader.s1_slc.S1_SLC.filter
        options:
            show_source: false
            heading_level: 5

- **Reset filter**

    Restore search results to the original unfiltered state
```python
dl.reset()
```
    ::: insarhub.downloader.s1_slc.S1_SLC.reset
        options:
            show_source: false
            heading_level: 5

- **Summary**

    Display statistics and overview of current search results
```python
dl.summary()
```
    ::: insarhub.downloader.s1_slc.S1_SLC.summary
        options:
            show_source: false
            heading_level: 5

- **View Footprint**

    Visualize geographic coverage of search results on an interactive map
```python
dl.footprint()
```

    ::: insarhub.downloader.s1_slc.S1_SLC.footprint
            options:
                show_source: false
                heading_level: 5

- **Download**

    Download all scenes from current search results to local storage
```python
dl.download()
```

    ::: insarhub.downloader.s1_slc.S1_SLC.download
        options:
            show_source: false
            heading_level: 5
        

- **DEM Download**

    Download DEM that covers all scenes from current search results to local storatge
```python
dl.dem()
```
    ::: insarhub.downloader.s1_slc.S1_SLC.dem
        options:
            show_source: false
            heading_level: 5

- **Select Pairs**

    Compute interferogram pairs for all active stacks based on temporal and perpendicular baseline constraints. Scenes with poor acquisition conditions (heavy rain, snow) are excluded automatically when `avoid_low_quality_days=True` (default).

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
        avoid_low_quality_days=True,   # exclude scenes with bad weather/snow (default)
        precip_mm_threshold=25.0,      # 3-day precip threshold in mm
        snow_threshold=0.5,            # MODIS snow-cover fraction threshold
    )
    fig = plot_pair_network(pairs, baselines, scene_bperp)
    fig.show()
    ```

    ::: insarhub.downloader.ASF_Base_Downloader.select_pairs
        options:
            show_source: false
            heading_level: 5




