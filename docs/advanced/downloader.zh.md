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

=== "S1_Burst"

    `S1_Burst` 是一个专用下载器，扩展自 `ASF_Base_Downloader`，用于 ASF 的 **SLC-BURST** 数据集。一个 burst 大约是完整 IW 切片（slice）的 1/9，因此面向 AOI 的 burst 堆叠比等效的 `S1_SLC` 搜索拉取的数据少得多——这正是 burst 处理的意义所在，也使 `ISCE3_Burst` / COMPASS 工作流在小目标区域上切实可行。请与 `ISCE3_Burst` 处理器和 `ISCE3_Dolphin_PL` 分析器配合使用。

    搜索、过滤、摘要、足迹和配对选择与 `S1_SLC` 完全一致（它们复用 `ASF_Base_Downloader`）；只有 `download()` 不同——它将选中的 burst 颗粒交给 `burst2safe`，由后者合并注释/定标/噪声 XML 并写入清单，从而组装成合法的 `.SAFE` 目录。

    !!! note "Burst 堆叠以 `fullBurstID` 为键，而非 frame"
        ASF 的 SLC-BURST 产品不返回 `frameNumber`，因此 frame 过滤器匹配不到任何内容，已从查询中排除。burst 堆叠由 `fullBurstID`（如 `056_118970_IW2`）标识；下载器文件夹据此命名为 `p<path>_iw<s>_b<id>`。

    ::: insarhub.downloader.s1_burst.S1_Burst
        options:
            show_source: true
            heading_level: 0

    - **创建下载器**

        ```python
        s1b = Downloader.create('S1_Burst',
                                intersectsWith=[-106.06, 40.34, -105.70, 40.58],
                                fullBurstID=['056_118970_IW2', '056_118971_IW2'],
                                polarization=['VV'],
                                start='2022-08-04',
                                end='2026-07-21',
                                workdir='path/to/dir')
        ```

        或使用显式配置：

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

    - **搜索 / 过滤 / 摘要 / 足迹**

        与 `S1_SLC` 完全一致——这些操作复用 `ASF_Base_Downloader`，作用于相同的 ASF burst 颗粒搜索。

        ```python
        results = dl.search()
        dl.summary()
        dl.footprint()
        ```

    - **选择配对**

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

    - **下载**

        下载选中的 burst 颗粒，并通过 `burst2safe` 组装为 `.SAFE` 目录。

        ```python
        dl.download()
        ```

        ::: insarhub.downloader.s1_burst.S1_Burst.download
            options:
                show_source: false
                heading_level: 5

=== "NISAR_GSLC"

    `NISAR_GSLC` 通过 ASF 搜索并下载 NISAR **L2 GSLC**（已地理编码的 SLC）产品。GSLC 是每个日期一帧的已地理编码复数 SLC，因此可直接送入 `ISCE3_NISAR` 处理器（无需配准、无需地理编码），再进入 `ISCE3_Dolphin_PL` 分析器。搜索、筛选、足迹与配对选择均复用 `ASF_Base_Downloader`；**不下载轨道** —— NISAR 产品自带状态矢量。

    !!! note "NISAR 的检索维度不同于 Sentinel-1"
        NISAR 的极化按**频段**记录，而非单一的 `polarization` 字段（ASF 将其留空）：请按 `mainBandPolarization`（频段 A，即用于 InSAR 的宽高分辨率主带）筛选，必要时用 `sideBandPolarization`（频段 B，5 MHz 电离层带）。`rangeBandwidth`（如 `40+5`）是采集模式 —— 在一个栈内应保持不变，以保证每个日期分辨率一致。`frameCoverage`（`FULL`/`PARTIAL`）、`relativeOrbit`（path）与 `frame` 组成其余维度。NISAR 产品还不返回 `beamMode`/`centerLat`/`centerLon`/`granuleType`/`md5sum`，其 `bytes` 是按文件的映射而非单个数字。

    - **使用参数创建下载器**

        ```python
        gslc = Downloader.create('NISAR_GSLC',
                                 intersectsWith=[-113.08, 37.68, -112.58, 38.07],
                                 mainBandPolarization='HH+HV',
                                 rangeBandwidth='40+5',
                                 start='2025-11-01',
                                 end='2026-09-01',
                                 workdir='path/to/dir')
        ```

        ::: insarhub.config.defaultconfig.NISAR_GSLC_Config
            options:
                heading_level: 0
                members: false

    - **搜索 / 下载**

        与 `S1_SLC` 相同 —— 均复用 `ASF_Base_Downloader`。下载得到的 `*GSLC*.h5` 帧落在 `workdir/slc/`，`ISCE3_NISAR` 从这里读取。

        ```python
        gslc.search()
        gslc.download()
        ```

=== "NISAR_RSLC"

    `NISAR_RSLC` 下载 NISAR **L1 RSLC**（雷达坐标 SLC）产品 —— 最原始的 InSAR 输入（频段 A 与 B），尚未地理编码。它面向 GMTSAR 的 NISAR 路径（`pre_proc_nsr` / `p2p_processing_nsr`，`SAT=NSR_A`）。检索维度与 `NISAR_GSLC` 相同（主/副带极化、range 带宽、frame 覆盖、path/frame），同样不下载轨道。

    ::: insarhub.config.defaultconfig.NISAR_RSLC_Config
        options:
            heading_level: 0
            members: false

=== "NISAR_GUNW"

    `NISAR_GUNW` 下载 NISAR **L2 GUNW**（已地理编码的解缠干涉图）—— 一种现成的、已地理编码、已解缠的干涉对产品，是 Sentinel-1 HyP3 GUNW 的 NISAR 对应物。它是单频段（仅在主带上生成），因此没有副带极化维度；`mainBandPolarization` 是单一取值（`HH`/`HV`/`VH`/`VV`）。设计上通过 MintPy 的 `prep_nisar` 加载器进入 MintPy（无需处理器）。

    ::: insarhub.config.defaultconfig.NISAR_GUNW_Config
        options:
            heading_level: 0
            members: false
