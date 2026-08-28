# 文件结构

InSARHub 在流程推进过程中会向磁盘写入一组一致的文件。每个阶段都会在工作目录中添加文件 — 从配对选择到分析，始终使用同一文件夹，因此可以通过查看哪些文件存在来判断文件夹的处理进度。

---

## 目录结构

单堆叠布局 — 仅找到一个轨道/帧时，所有文件直接写入 `workdir/`：

=== "HyP3"

    ```
    workdir/
    ├── insarhub_config.json               # 流程配置（随每个阶段累积）
    ├── stack_p0_f0.json                   # 配对、基线、场景、质量评分
    ├── network_p0_f0.png                  # 干涉图网络图像
    ├── hyp3_jobs.json                     # 已提交任务 ID（处理器提交后）
    ├── hyp3_retry_jobs_*.json             # 重试批次（处理器重试后）
    ├── .insarhub_cache.json               # 处理器结果缓存（文件名 + 输出目录）
    ├── .insarhub_quality_cache.json       # 天气、积雪、土地覆盖、相干性特征缓存
    ├── .insarhub_pair_quality_db.json     # 所有 N×(N-1)/2 场景配对的预评分质量
    ├── decay_maps/                        # S1 相干性像素衰减 GeoTIFF（每季一个）
    │   └── S1_coherence_decay_*.tif
    ├── hyp3/                              # HyP3 下载的 ZIP 产品（处理器下载后）
    │   └── S1AA_*_INT20_*.zip
    └── mintpy/                            # MintPy 分析输出（分析器运行后）
        ├── .mintpy.cfg
        ├── inputs/
        ├── geo/
        ├── tmp/                           # 解压的 zip 内容（清理时删除）
        ├── clip/                          # AOI 裁剪后的干涉图（清理时删除）
        └── timeseries*.h5, velocity.h5, ...
    ```

=== "ISCE2"

    ```
    workdir/
    ├── insarhub_config.json
    ├── stack_p0_f0.json
    ├── network_p0_f0.png
    ├── .insarhub_quality_cache.json
    ├── .insarhub_pair_quality_db.json
    ├── decay_maps/
    │   └── S1_coherence_decay_*.tif
    ├── slc/                               # 下载的 SLC .SAFE 文件和轨道 .EOF 文件
    │   ├── S1A_IW_SLC__*.SAFE/
    │   └── *.EOF
    ├── dem/                               # ISCE2 格式 DEM（自动下载 GLO-30）
    │   ├── dem.wgs84
    │   └── dem.wgs84.xml
    ├── isce/                              # ISCE2 stackSentinel 工作目录
    │   ├── run_files/
    │   ├── merged/
    │   │   ├── interferograms/
    │   │   └── geom_reference/
    │   └── ...
    └── mintpy/                            # MintPy 分析输出（分析器运行后）
        ├── .mintpy.cfg
        ├── inputs/
        ├── geo/
        └── timeseries*.h5, velocity.h5, ...
    ```

=== "GMTSAR_S1"

    ```
    workdir/
    ├── insarhub_config.json
    ├── stack_p0_f0.json
    ├── network_p0_f0.png
    ├── slc/                            # 下载的 SLC .SAFE 文件和轨道 .EOF 文件
    ├── topo/                           # GMTSAR 格式 DEM（dem.grd）
    └── gmtsar/                         # GMTSAR 实例目录
        ├── gmtsar_jobs.json            # 已保存任务状态（按配对）
        ├── baseline_table.dat
        ├── raw/                        # 解包的 SLC + 每个场景的 PRM/LED/SLC
        ├── <ref>.SAFE_<sec>.SAFE/      # 每对实例目录（多子条带）
        │   └── merge/                  #   合并后的地理编码产品（phasefilt_ll.grd、corr_ll.grd）
        └── intf/<julian_pair>/         # 单子条带输出（GMTSAR 儒略日命名）
    ```

    分析器输出（同一工作目录中可选其一或两者）：

    | 分析器 | 输出目录 | 内容 |
    |---|---|---|
    | `GMTSAR_Mintpy_SBAS` | `gmtsar_mintpy/` | MintPy `smallbaselineApp` 输出（`timeseries*.h5`、`velocity.h5` 等） |
    | `GMTSAR_SBAS` | `gmtsar_sbas/` | 每个日期的 `disp_*.grd`、线性速度 `vel.grd` |

=== "ISCE3_Burst"

    ```
    workdir/
    ├── insarhub_config.json
    ├── stack_p56_merged_*.json         # 配对（基于 burst，以 fullBurstID 为键）
    ├── slc/                            # 组装的 .SAFE 目录（通过 burst2safe）
    ├── s1-burst-db/                    # 缓存的 OPERA burst bbox sqlite3
    ├── dem/                            # Copernicus DEM + 水体掩膜
    ├── tec/                            # IONEX 地图（每个采集日期一张）
    ├── cslc/                           # 每个 burst-日期的地理编码 CSLC（COMPASS）
    ├── ifgrams/                        # 干涉图（dolphin）
    ├── stitched/                       # 每对合并的 burst
    ├── timeseries/                     # dolphin 时序输出（ISCE3_Dolphin_PL）
    ├── isce3_burst_jobs.json           # 已保存任务状态（按阶段）
    └── .stage_status/                  # 每个阶段的 .succeeded/.failed 标记
    ```

=== "ISCE3_NISAR"

    NISAR GSLC 已经完成地理编码，因此没有 COMPASS 前端（`dem/`、`tec/`、`cslc/` 均不存在）。原始 GSLC 先被裁剪到 AOI，再直接送入 dolphin。

    ```
    workdir/
    ├── insarhub_config.json
    ├── slc/                            # NISAR L2 GSLC 数据（*.h5），每个日期一个
    ├── cropped_gslc/                   # 按 AOI 裁剪的 GSLC VRT（`crop` 阶段）
    ├── slc_stack.vrt                   # dolphin 在裁剪后 GSLC 上的输入堆叠
    ├── bounds_mask.tif, combined_mask.tif
    ├── linked_phase/                   # 相位链接 + 时间相干性（dolphin）
    ├── PS/                             # 永久散射体幅度离差
    ├── interferograms/                 # 地理编码干涉图 + 相关系数（dolphin）
    ├── unwrapped/                      # 地理编码解缠相位 + 连通分量
    ├── timeseries/                     # dolphin 时序输出（ISCE3_Dolphin_PL）
    ├── isce3_nisar_jobs.json           # 已保存任务状态（按阶段）
    └── .stage_status/                  # 每个阶段的 .succeeded/.failed 标记
    ```

**多堆叠运行** — 搜索覆盖多个轨道/帧时，每个组获得自己的 `p{path}_f{frame}/` 子文件夹，结构与对应单堆叠布局完全相同。

```
workdir/
├── p100_f466/                    # 每个轨道/帧组一个子文件夹
│   ├── insarhub_config.json
│   ├── stack_p100_f466.json
│   ├── .insarhub_quality_cache.json
│   ├── .insarhub_pair_quality_db.json
│   ├── decay_maps/
│   └── ...
├── p93_f121/
│   └── ...
```

---

## 各阶段文件

### 第一阶段 — 配对选择

由 `insarhub downloader --select-pairs` 或 GUI **选择配对** 产生。

| 文件 | 说明 |
|------|-------------|
| `insarhub_config.json` | 下载器类型和设置 |
| `stack_p{path}_f{frame}.json` | 选定配对、垂直基线、场景列表和配对质量评分 |
| `network_p{path}_f{frame}.png` | 干涉图网络图 — 节点为场景，边为配对，按质量评分着色 |
| `.insarhub_quality_cache.json` | 配对评分期间获取的天气、积雪和相干性数据 |
| `.insarhub_pair_quality_db.json` | 所有 N×(N−1)/2 场景组合的预评分质量 |
| `decay_maps/` | 从 AWS S3 缓存的 S1 全球相干性像素衰减 GeoTIFF（每季一个） |

### 第一阶段 b — DEM 下载

由 `insarhub downloader dem` 产生。可选 — 仅在配准需要本地 DEM 时使用。

| 文件 | 说明 |
|------|-------------|
| `dem_p{path}_f{frame}.tif` | 覆盖堆叠 AOI 的合并重投影 DEM 栅格 |

### 第二阶段 — 任务提交

由 `insarhub processor submit` 或 GUI **处理** 产生。

| 文件 | 说明 |
|------|-------------|
| `insarhub_config.json` | 更新了处理器类型和设置 |
| `hyp3_jobs.json` | 按账户分组的 HyP3 任务 ID |
| `hyp3_retry_jobs_{timestamp}.json` | 重试批次的任务 ID（每次**重试**时写入） |
| `.insarhub_cache.json` | 每次**检查**后更新，包含已成功文件名和输出目录 |

本地处理器会写入各自的任务文件，而非 `hyp3_jobs.json`：

| 处理器 | 任务文件 |
|-----------|----------|
| `ISCE2_S1` | `isce/isce_jobs_{timestamp}.json` |
| `GMTSAR_S1` | `gmtsar/gmtsar_jobs.json` |
| `ISCE3_Burst` | `isce3_burst_jobs.json` |

### 第三阶段 — 分析

由 `insarhub analyzer run` 或 GUI **运行分析器** 产生。

| 文件 | 说明 |
|------|-------------|
| `insarhub_config.json` | 更新了分析器类型 |
| `.mintpy.cfg` | InSARHub 写入的 MintPy `smallbaselineApp` 配置 |
| `mintpy/tmp/` | 解压的 HyP3 产品内容（临时） |
| `mintpy/clip/` | AOI 裁剪后的干涉图（临时） |

**清理后：**`mintpy/tmp/` 和 `mintpy/clip/` 被删除。`insarhub_config.json`、`.mintpy.cfg` 和所有 MintPy 输出被保留。

---

## 主要 JSON 文件格式

### `insarhub_config.json`

随每个阶段运行而累积的中央流程配置。所有键均为可选 — 仅包含已执行阶段的内容。

```json
{
  "downloader": {
    "type": "S1_SLC",
    "config": {
      "start": "2020-01-01",
      "end": "2020-12-31",
      "relativeOrbit": 100,
      "frame": 466
    }
  },
  "processor": {
    "type": "Hyp3_S1",
    "config": {
      "phase_filter_parameter": 0.6,
      "looks": "20x4"
    }
  },
  "analyzer": "Hyp3_Mintpy_SBAS"
}
```

### `stack_p{path}_f{frame}.json`

一个轨道/帧组的配对网络和质量评分。

```json
{
  "pairs": [
    ["S1A_IW_SLC__1SDV_20200101", "S1A_IW_SLC__1SDV_20200113"],
    ["S1A_IW_SLC__1SDV_20200113", "S1A_IW_SLC__1SDV_20200125"]
  ],
  "baselines": {
    "S1A_IW_SLC__1SDV_20200101": 0.0,
    "S1A_IW_SLC__1SDV_20200113": 12.4,
    "S1A_IW_SLC__1SDV_20200125": -5.8
  },
  "scenes": [
    "S1A_IW_SLC__1SDV_20200101",
    "S1A_IW_SLC__1SDV_20200113",
    "S1A_IW_SLC__1SDV_20200125"
  ],
  "pair_quality": {
    "scores": {
      "S1A_..._20200101,S1A_..._20200113": 87.5
    }
  }
}
```

### `hyp3_jobs.json`

按账户分组的已保存 HyP3 任务 ID 及输出目录。

```json
{
  "job_ids": {
    "username1": ["job-id-aaa", "job-id-bbb"],
    "username2": ["job-id-ccc"]
  },
  "out_dir": "/data/bryce/p100_f466/hyp3"
}
```

---

## 内部缓存文件

这些点文件由程序自动写入，可以安全删除 — InSARHub 会在下次运行时重新生成。

### `decay_maps/`

从 AWS S3 缓存的 S1 全球相干性像素衰减 GeoTIFF，每季一个三波段文件：

| 波段 | 内容 |
|------|----------|
| 1 | γ∞ — 永久散射体相干性基底 |
| 2 | γ0 — t = 0 时的初始相干性 |
| 3 | τ — 去相干时间常数（天） |

这些文件在进程重启后仍保留，因此每个 AOI 每季只查询一次 S3。删除它们可强制从 S3 重新下载。
