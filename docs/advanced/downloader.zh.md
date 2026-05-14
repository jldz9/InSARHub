InSARHub 下载器模块提供了搜索和下载卫星数据的流线化接口。

- **导入下载器**

    导入 Downloader 类以访问所有下载器功能
```python
from insarhub import Downloader
```

- **查看可用下载器**

    列出所有已注册的下载器
```python
Downloader.available()
```

## 可用下载器

=== "ASF_Base_Downloader"

    InSARHub 将 [asf_search](https://github.com/asfadmin/Discovery-asf_search) 封装为其下载后端之一。`ASF_Base_Downloader` 基于可复用的基础配置类实现，提供 asf_search 的完整搜索、过滤和下载逻辑。

    ::: insarhub.downloader.asf_base.ASF_Base_Downloader
        options:
            heading_level: 0
            members: false

    ### 使用方法

    - **使用参数创建下载器**

        使用搜索条件初始化下载器实例

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
        或
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
        或
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

        基础配置 `ASF_Base_Config` 包含 asf_search 的所有关键词参数。有关每个参数的详细说明，请参阅 [ASF Search 官方文档](https://docs.asf.alaska.edu/asf_search/searching/#searching)。

        ::: insarhub.config.ASF_Base_Config
            options:
                heading_level: 0
                members: false

    - **搜索**

        查询卫星档案并检索符合条件的可用场景

        ```python
        results = dl.search()
        ```

        ::: insarhub.downloader.ASF_Base_Downloader.search
            options:
                show_source: false
                heading_level: 5

    - **过滤**

        通过添加额外约束来细化现有搜索结果

        ```python
        filter_result = dl.filter(start='2020-02-01')
        ```

        ::: insarhub.downloader.ASF_Base_Downloader.filter
            options:
                show_source: false
                heading_level: 5

    - **重置过滤器**

        将搜索结果恢复至原始未过滤状态

        ```python
        dl.reset()
        ```

        ::: insarhub.downloader.ASF_Base_Downloader.reset
            options:
                show_source: false
                heading_level: 5

    - **摘要**

        显示当前搜索结果的统计和概览

        ```python
        dl.summary()
        ```

        ::: insarhub.downloader.ASF_Base_Downloader.summary
            options:
                show_source: false
                heading_level: 5

    - **查看覆盖范围**

        在交互式地图上可视化搜索结果的地理覆盖范围

        ```python
        dl.footprint()
        ```

        ::: insarhub.downloader.ASF_Base_Downloader.footprint
            options:
                show_source: false
                heading_level: 5

    - **下载**

        将当前搜索结果中的所有场景下载到本地存储

        ```python
        dl.download()
        ```

        ::: insarhub.downloader.ASF_Base_Downloader.download
            options:
                show_source: false
                heading_level: 5

    - **下载 DEM**

        下载覆盖当前搜索结果所有场景的 DEM

        ```python
        dl.dem()
        ```

        ::: insarhub.downloader.ASF_Base_Downloader.dem
            options:
                show_source: false
                heading_level: 5

    - **选择配对**

        根据时间和垂直基线约束，为所有活动堆叠计算干涉图配对。当 `avoid_low_quality_days=True`（默认）时，自动排除获取条件较差（大雨、积雪）的场景。

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

    `S1_SLC` 是一个专门用于下载 Sentinel-1 SLC 数据的下载器，扩展自 `ASF_Base_Downloader`。

    ::: insarhub.downloader.s1_slc.S1_SLC
        options:
            show_source: true
            heading_level: 0
            members: false

    ### 使用方法

    - **使用参数创建下载器**

        使用搜索条件初始化下载器实例

        ```python
        s1 = Downloader.create('S1_SLC',
                                intersectsWith=[-113.05, 37.74, -112.68, 38.00],
                                start='2020-01-01',
                                end='2020-12-31',
                                relativeOrbit=100,
                                frame=466,
                                workdir='path/to/dir')
        ```
        或
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
        或
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

        配置 `S1_SLC_Config` 包含专门针对 Sentinel-1 数据的预定义参数。详情请参阅 [ASF Search 官方文档](https://docs.asf.alaska.edu/asf_search/searching/#searching)。

        ::: insarhub.downloader.s1_slc.S1_SLC_Config
            options:
                heading_level: 0
                members: false

    - **搜索**

        ```python
        results = dl.search()
        ```

        ::: insarhub.downloader.s1_slc.S1_SLC.search
            options:
                show_source: false
                heading_level: 5

    - **过滤**

        ```python
        filter_result = dl.filter(start='2020-02-01')
        ```

        ::: insarhub.downloader.s1_slc.S1_SLC.filter
            options:
                show_source: false
                heading_level: 5

    - **重置过滤器**

        ```python
        dl.reset()
        ```

        ::: insarhub.downloader.s1_slc.S1_SLC.reset
            options:
                show_source: false
                heading_level: 5

    - **摘要**

        ```python
        dl.summary()
        ```

        ::: insarhub.downloader.s1_slc.S1_SLC.summary
            options:
                show_source: false
                heading_level: 5

    - **查看覆盖范围**

        ```python
        dl.footprint()
        ```

        ::: insarhub.downloader.s1_slc.S1_SLC.footprint
            options:
                show_source: false
                heading_level: 5

    - **下载**

        ```python
        dl.download()
        ```

        ::: insarhub.downloader.s1_slc.S1_SLC.download
            options:
                show_source: false
                heading_level: 5

    - **下载 DEM**

        ```python
        dl.dem()
        ```

        ::: insarhub.downloader.s1_slc.S1_SLC.dem
            options:
                show_source: false
                heading_level: 5

    - **选择配对**

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
