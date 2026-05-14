# 文件结构

InSARHub 在流程推进过程中会向磁盘写入一组一致的文件。每个阶段都会在工作目录中添加文件 — 从配对选择到分析，始终使用同一文件夹，因此可以通过查看哪些文件存在来判断文件夹的处理进度。

---

## 目录结构

**单堆叠（平铺）运行** — 仅找到一个轨道/帧时，所有文件直接写入 `workdir/`：

```
workdir/
├── insarhub_config.json               # 流程配置（随每个阶段累积）
├── stack_p0_f0.json                   # 配对、基线、场景、质量评分
├── network_p0_f0.png                  # 干涉图网络图像
├── dem_p0_f0.tif                      # DEM 栅格（下载器 dem 后）
├── hyp3_jobs.json                     # 已提交任务 ID（处理器提交后）
├── hyp3_retry_jobs_*.json             # 重试批次（处理器重试后）
├── .mintpy.cfg                        # InSARHub 写入的 MintPy 配置（分析器初始化后）
├── .insarhub_cache.json               # 处理器结果缓存（文件名 + 输出目录）
├── .insarhub_quality_cache.json       # 天气、积雪、土地覆盖、相干性特征缓存
├── .insarhub_pair_quality_db.json     # 所有 N×(N-1)/2 场景配对的预评分质量
├── decay_maps/                        # S1 相干性像素衰减 GeoTIFF（每季一个）
│   ├── S1_coherence_decay_winter_vv.tif
│   ├── S1_coherence_decay_spring_vv.tif
│   ├── S1_coherence_decay_summer_vv.tif
│   └── S1_coherence_decay_fall_vv.tif
├── tmp/                               # 解压的 zip 内容（清理时删除）
└── clip/                              # AOI 裁剪数据（清理时删除）
```

**多堆叠运行** — 搜索覆盖多个轨道/帧时，每个组获得自己的 `p{path}_f{frame}/` 子文件夹，结构与单堆叠布局完全相同。

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

MintPy 输入（`inputs/ifgramStack.h5` 等）和输出（`timeseries*.h5`、`velocity.h5`、`velocity.tif` 等）均由 MintPy 写入，此处不再列出。详情请参阅 [MintPy 文档](https://mintpy.readthedocs.io)。

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

### 第三阶段 — 分析

由 `insarhub analyzer run` 或 GUI **运行分析器** 产生。

| 文件 | 说明 |
|------|-------------|
| `insarhub_config.json` | 更新了分析器类型 |
| `.mintpy.cfg` | InSARHub 写入的 MintPy `smallbaselineApp` 配置 |
| `tmp/` | 解压的 HyP3 产品内容（临时） |
| `clip/` | AOI 裁剪后的干涉图（临时） |

**清理后：**`tmp/` 和 `clip/` 被删除。`insarhub_config.json`、`.mintpy.cfg` 和所有 MintPy 输出被保留。

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
  "analyzer": "Hyp3_SBAS"
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
  "out_dir": "/data/bryce/p100_f466"
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
