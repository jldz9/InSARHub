# 程序结构

InSARHub 由三个松耦合的层次构成——**下载器（Downloader）**、**处理器（Processor）** 和 **分析器（Analyzer）**，每层均以命名后端的注册表形式实现。写入任务文件夹的 `insarhub_config.json` 会随流程推进逐步累积配置，因此每个阶段既可独立运行，也可串联执行。

- **下载器** — 在 ASF 搜索场景（完整 SLC 或单个 SLC-BURST 颗粒）、基于质量评分选择干涉图配对，并获取 SLC 数据和轨道文件。
- **处理器** — 接收选定的配对，生成（解缠）干涉图：云端（HyP3）、本地 ISCE2 `stackSentinel`、本地 GMTSAR `p2p_processing`/`p2p_S1_TOPS_Frame`，或 ISCE3/COMPASS burst 地理编码。
- **分析器** — 将干涉图堆叠反演为形变时序：MintPy `smallbaselineApp`、GMTSAR 自带的 `sbas` 二进制程序，或 dolphin 的 `timeseries`。

Web UI 和命令行均为同一 Python API 的轻量封装，在浏览器中运行的任何工作流均可在命令行或脚本中精确复现。

![InSARHub 工作流程](fig/InSARHub_workflow.png){: .doc-img-wide }

## 流水线矩阵

每个下载器对应一组兼容的处理器，每个处理器又对应一组兼容的分析器。注册表通过 `compatible_downloader` / `compatible_processor` 建立这些关系；工作目录中保存的 `insarhub_config.json` 记录了它属于哪条流水线。

```
Downloader          Processor            Analyzer
─────────────────   ──────────────────   ─────────────────────────
S1_SLC           →  Hyp3_S1           →  Hyp3_Mintpy_SBAS
                 →  ISCE2_S1          →  ISCE2_Mintpy_SBAS
                 →  GMTSAR_S1         →  GMTSAR_Mintpy_SBAS
                                      →  GMTSAR_SBAS
S1_Burst         →  ISCE3_Burst       →  ISCE3_Dolphin_PL
NISAR_GSLC       →  ISCE3_NISAR       →  ISCE3_Dolphin_PL
```

任何本地后端都可以在容器内运行，而非在主机上——`--container` 的工作原理参见[容器运行](container.md)。
