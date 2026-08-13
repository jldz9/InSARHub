InSARHub 处理器模块专门提供干涉图处理功能。

- **导入处理器**

    导入 Processor 类以访问所有处理器功能
```python
from insarhub import Processor
```

- **查看可用处理器**

    列出所有已注册的处理器
```python
Processor.available()
```

## 可用处理器

=== "Hyp3_S1"

    HyP3 InSAR 处理器是 ASF HyP3 系统提供的基于云端的处理服务，用于从 Sentinel-1 SAR 数据生成干涉图。
    InSARHub 将 [hyp3_sdk](https://github.com/ASFHyP3/hyp3-sdk) 封装为其处理后端之一。

    `Hyp3_S1` 专门封装了 hyp3_sdk 中的 `insar_job`，提供 InSAR SLC 处理工作流。

    ::: insarhub.processor.hyp3_s1.Hyp3_S1
        options:
            heading_level: 0
            members: false

    ### 使用方法

    - **使用参数创建处理器**

        使用搜索条件初始化处理器实例

        ```python
        processor = Processor.create('Hyp3_S1', workdir='/your/work/path', pairs=pairs)
        ```
        或
        ```python
        params = {
            "workdir": '/your/work/path',
            "pairs": pairs,
        }
        processor = Processor.create('Hyp3_S1', **params)
        ```
        或
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

    - **提交任务**

        根据当前配置向 HyP3 提交 InSAR 任务。

        ```python
        jobs = processor.submit()
        ```

        ::: insarhub.processor.hyp3_s1.Hyp3_S1.submit
            options:
                members: false
                show_source: false
                heading_level: 5

    - **刷新任务**

        刷新所有任务的状态。

        ```python
        jobs = processor.refresh()
        ```

        ::: insarhub.processor.hyp3_s1.Hyp3_S1.refresh
            options:
                members: false
                show_source: false
                heading_level: 5

    - **重试失败任务**

        通过重新提交来重试所有失败的任务。

        ```python
        jobs = processor.retry()
        ```

        ::: insarhub.processor.hyp3_s1.Hyp3_S1.retry
            options:
                members: false
                show_source: false
                heading_level: 5

    - **下载成功任务**

        下载所有用户的已成功任务。

        ```python
        processor.download()
        ```

        ::: insarhub.processor.hyp3_s1.Hyp3_S1.download
            options:
                members: false
                show_source: false
                heading_level: 5

    - **保存当前任务**

        将当前任务批次信息保存到 JSON 文件。

        ```python
        processor.save()
        ```

        ::: insarhub.processor.hyp3_s1.Hyp3_S1.save
            options:
                members: false
                show_source: false
                heading_level: 5

    - **监控任务**

        持续监控任务并下载已完成的输出。

        ```python
        processor.watch()
        ```

        ::: insarhub.processor.hyp3_s1.Hyp3_S1.watch
            options:
                members: false
                show_source: false
                heading_level: 5

    - **加载已保存任务**

        加载之前保存的 JSON 文件并恢复工作。

        ```python
        processor = Processor.create('Hyp3_S1', saved_job_path='path/to/your/json/file.json')
        ```

        加载后可恢复检查/下载提交至 HyP3 服务器的任务。

=== "ISCE2_S1"

    ISCE2_S1 处理器在本地运行 ISCE2 `stackSentinel`，从下载的 SLC `.SAFE` 文件生成 Sentinel-1 干涉图。它生成一系列编号运行脚本并顺序执行，在每个步骤内并行运行独立命令。

    - **导入处理器**

        ```python
        from insarhub import Processor
        ```

    - **创建处理器**

        ```python
        from insarhub.config import ISCE2_S1_Config

        cfg = ISCE2_S1_Config(
            workdir='/data/p100_f466',
            bbox=[33.0, 38.0, -120.0, -115.0],   # [南, 北, 西, 东]
        )
        pairs = [('20200101', '20200113'), ('20200113', '20200125')]
        processor = Processor.create('ISCE2_S1', pairs=pairs, config=cfg)
        ```

        ::: insarhub.config.defaultconfig.ISCE2_S1_Config
            options:
                members: false
                show_source: false
                heading_level: 0

    - **提交（本地模式）**

        生成运行脚本并在后台进程中开始顺序执行。立即返回；使用 `refresh()` 监控进度。

        ```python
        jobs = processor.submit()
        ```

        ::: insarhub.processor.isce2_s1.ISCE2_S1.submit
            options:
                members: false
                show_source: false
                heading_level: 5

    - **提交（HPC / SLURM 模式）**

        设置 `hpc_mode=True` 启用滑动窗口 SLURM 管理器。步骤会先分组：每场景/每对命令数相同的连续步骤会合并为单个组管理器（例如 `run_02_unpack_secondary_slc` 与 `run_03_average_baseline` 若每个场景各有一条命令，就会合并）；其余步骤各自拥有独立的单步管理器。每个管理器随时保持最多 `max_concurrent_hpc` 个子作业同时运行，有空槽时立即补充。每个 sbatch 脚本按命令记录带耗时秒数的 `START`/`DONE`/`FAIL` 日志。

        `submit()` 只直接提交*第一个*分组的管理器。此后每个管理器在自身成功完成后，会通过自己脚本末尾的 `sbatch` 调用去提交下一个分组的管理器——而不是依赖 SLURM 的 `--dependency`——因此任意时刻队列中最多只会有一个管理器（加上它自己不超过 `max_concurrent_hpc` 个的子作业），而不是把所有分组的管理器一次性提前全部提交。这一点很关键，因为 SLURM 按用户限制提交作业数的 QOS 上限，对"仅仅在等待依赖"的作业和正在运行的作业是一视同仁地计数的；一次性提前提交整条链，可能会让本来什么都没做、只是在排队等轮到自己的管理器把这个上限占满。若某个管理器失败或被取消，它就不会再提交下一个，链条自然中断——不需要额外清理尚未提交的剩余部分。`refresh()` 会自动读取新链式提交作业的 ID（管理器会把它写入该分组日志目录旁的小文件 `chained_job_id.txt`）。

        管理器作业名简短且能直接看出它管理哪个/哪些运行步骤：单步管理器为 `i<NN>_mgr`（例如 `run_04_...` 对应 `i04_mgr`），组管理器为跨越步骤 NN–MM 的 `i<NN>-<MM>_grp`（例如 `i02-03_grp`）——便于在 `squeue` 中一眼辨认。

        ```python
        cfg = ISCE2_S1_Config(
            workdir='/data/p100_f466',
            bbox=[33.0, 38.0, -120.0, -115.0],
            hpc_mode=True,
            max_concurrent_hpc=12,   # 默认值；根据集群公平份额限制调整
        )
        processor = Processor.create('ISCE2_S1', pairs=pairs, config=cfg)
        processor.submit()
        ```

        `retry()` 从已保存的作业元数据（`slurm_job_ids` / `hpc_manager` / `hpc_array`）自动检测 HPC 模式，无需再次传入 `hpc_mode=True`。

    - **试运行**

        预览运行脚本和路径检查，不执行任何操作。

        ```python
        cfg = ISCE2_S1_Config(
            workdir='/data/p100_f466',
            bbox=[33.0, 38.0, -120.0, -115.0],
            dry_run=True,
        )
        processor = Processor.create('ISCE2_S1', pairs=pairs, config=cfg)
        processor.submit()
        ```

    - **刷新**

        从磁盘读取步骤和命令状态。

        ```python
        jobs = processor.refresh()
        ```

        ::: insarhub.processor.isce2_base.ISCE2_Base.refresh
            options:
                members: false
                show_source: false
                heading_level: 5

    - **重试失败步骤**

        重新运行所有状态为 `FAILED` 的步骤。

        ```python
        processor.retry()
        ```

        ::: insarhub.processor.isce2_base.ISCE2_Base.retry
            options:
                members: false
                show_source: false
                heading_level: 5

    - **取消**

        终止正在运行的后台进程（本地模式）或对所有活动 SLURM 任务执行 `scancel`（HPC 模式）。

        ```python
        processor.cancel()
        ```

        ::: insarhub.processor.isce2_base.ISCE2_Base.cancel
            options:
                members: false
                show_source: false
                heading_level: 5

    - **监控**

        定期轮询步骤状态，直到所有步骤完成。

        ```python
        processor.watch(refresh_interval=60)
        ```

        ::: insarhub.processor.isce2_base.ISCE2_Base.watch
            options:
                members: false
                show_source: false
                heading_level: 5

    - **保存 / 加载**

        任务状态在 `submit()` 后自动保存。从已保存的任务文件重新加载并恢复：

        ```python
        cfg = ISCE2_S1_Config(
            workdir='/data/p100_f466',
            saved_job_path='/data/p100_f466/isce/isce_jobs_<timestamp>.json',
        )
        processor = Processor.create('ISCE2_S1', pairs=[], config=cfg)
        processor.refresh()   # 或 .retry()、.cancel()、.watch()
        ```

    - **无需本地安装 ISCE2**

        将 `container` 字段设置为 Apptainer/Singularity `.sif` 镜像的路径，或 Docker 镜像引用（name[:tag]），`submit()`/`retry()`/`refresh()`/`watch()`/`cancel()` 都会在容器内而非宿主机上重新执行同一个 `insarhub processor ...` CLI 调用 — 工作目录会以相同路径绑定挂载，因此输出会像本机运行一样落在原处，ISCE2 也完全不需要在宿主机上被发现。容器镜像只需在 ISCE2/topsStack 旁额外安装 `insarhub`（可参考仓库根目录的 [`Dockerfile`](https://github.com/jldz9/InSARHub/blob/main/Dockerfile) 作为现成示例）。

        ```python
        cfg = ISCE2_S1_Config(
            workdir='/data/p100_f466',
            bbox=[33.0, 38.0, -120.0, -115.0],
            container='ghcr.io/jldz9/insarhub-isce2:latest',
        )
        processor = Processor.create('ISCE2_S1', pairs=pairs, config=cfg)
        processor.submit()
        ```

        CLI 用法相同：

        ```bash
        insarhub processor -N ISCE2_S1 -w /data/p100_f466 submit \\
            --container ghcr.io/jldz9/insarhub-isce2:latest
        ```

        `container` 是按次调用的设置，而非持久化配置 — 之后每次调用（`retry()`、新的 `submit()` 等）若也要在容器内运行，都需要再次设置。在 HPC 模式下，只有各阶段的子作业在容器内运行，sbatch 管理器脚手架仍留在宿主机上。

=== "GMTSAR_S1"

    `GMTSAR_S1` 处理器在本地运行 [GMTSAR](https://github.com/gmtsar/gmtsar) 的 Python 流程，从已下载的 SLC `.SAFE` 文件生成 Sentinel-1 干涉图。两个 GMTSAR 入口都支持，根据 `subswath` 指定的是一个还是多个 IW 自动选择：

    - `subswath` 只指定一个 IW（例如 `2`）— 单子条带，走 `p2p_processing`。`GMTSAR_S1` 会自行从每景 `.SAFE` 中提取所配置的 IW 子条带与极化方式，因此调用方始终只需传入原始的 `.SAFE`/`.EOF` 名称，与多子条带模式一致。
    - `subswath` 指定多个 IW（例如默认的 `"1 2 3"`）— 多子条带，走 `p2p_S1_TOPS_Frame`，生成跨所有指定子条带的合并干涉图。

    需要说明的是，`p2p_S1_TOPS_Frame` 并不会取代 `p2p_processing` — 它是建立在后者之上的编排层：内部按子条带循环调用 `p2p_processing S1_TOPS`，再执行合并。`p2p_processing` 本身是通用的逐对处理引擎，支持 ERS、ENVI、ALOS、TSX、RS2 等十余种传感器，`S1_TOPS` 只是其中之一。

    GMTSAR 运行在自己的 conda 环境中，与 InSARHub 的环境相互独立（numpy/GDAL 版本不同）— `gmtsar_root` 与 `gmtsar_env_bin` 分别告诉 `GMTSAR_S1` 到哪里找 GMTSAR 的脚本，以及它会调用的 `gmt` 可执行文件。两者在未设置时都会自动探测（`gmtsar_root`：`$GMTSAR` → `$PATH` 上的已知 GMTSAR 脚本 → 常见安装位置扫描；`gmtsar_env_bin`：带 `gmt` 的相邻 conda 环境 → `$PATH` 上的 `gmt`），因此实际使用中通常无需设置 — 只有在自动探测选错或找不到时才需要显式指定。

    - **导入处理器**

        ```python
        from insarhub import Processor
        ```

    - **创建处理器**

        ```python
        from insarhub.config import GMTSAR_S1_Config

        cfg = GMTSAR_S1_Config(
            workdir       = '/data/stack',
            slc_dir       = '/data/slcs',
            orbit_dir     = '/data/orbits',
            dem_path      = '/data/dem.grd',   # GMTSAR 格式 DEM；未设置时在暂存阶段自动下载
            subswath      = 2,                 # 仅 IW2 — 单子条带。默认 "1 2 3" 为多子条带合并
        )
        pairs = [
            ("REF.SAFE", "REF.EOF", "SEC.SAFE", "SEC.EOF"),
        ]
        processor = Processor.create('GMTSAR_S1', pairs=pairs, config=cfg)
        ```

        !!! warning "多子条带模式下请设置 `dem_path`"
            多子条带模式为**每个干涉对**建立独立的 case 目录。若 `dem_path` 未设置，DEM 会在暂存阶段自动下载 — 也就是每个干涉对下载一次。对 27 个干涉对的网络而言，这意味着重复下载 27 次同一个 DEM。

        ::: insarhub.config.defaultconfig.GMTSAR_S1_Config
            options:
                members: false
                show_source: false
                heading_level: 0

    - **提交**

        暂存 GMTSAR case 目录（单子条带模式下还会提取每个干涉对的子条带），随后在后台启动 `p2p_processing`/`p2p_S1_TOPS_Frame`，最多同时运行 `max_workers` 个干涉对。该调用立即返回；请用 `refresh()`/`watch()` 查看进度。

        ```python
        jobs = processor.submit()
        ```

    - **提交（HPC / SLURM 模式）**

        两种模式都支持 `hpc_mode=True`。

        **p2p 模式**（`stack_mode=False`，默认）是两者中较简单的：干涉对之间完全独立 — 多子条带模式下各自拥有独立的 case 目录，单子条带模式的输出以 `intf/<julian_pair>/` 隔离 — 因此由单个滑动窗口管理器一次性展开全部干涉对，同时最多 `max_concurrent_hpc` 个，**完全不需要链式提交**。每个子作业通过内部的 `run-stage-unit --stage pair --index N` 重入，运行该干涉对的完整流程（配准 → 干涉图 → 滤波 → 解缠 → 地理编码）。作业名为 `g_p2p_mgr` / `g_p2p_<idx>`。

        ```python
        cfg = GMTSAR_S1_Config(workdir='/data/stack', hpc_mode=True)
        Processor.create('GMTSAR_S1', pairs=pairs, config=cfg).submit()
        ```

        **stack_mode** 则把每个栈阶段（`align_F<N>`/`intf_F<N>`/`merge`，单子条带时为扁平的 `align`/`intf`）作为各自的滑动窗口管理器运行，与 `ISCE2_S1` 采用相同的链式提交设计：只直接提交*第一个*阶段的管理器，其余每个管理器在自己成功后再提交下一个 — 而不是预先排入 `--dependency` 依赖链 — 因此队列中同时最多只有一个管理器（外加它自己的 ≤`max_concurrent_hpc` 个子作业）。

        与 `ISCE2_S1` 的一个真实差异：`GMTSAR_S1` 没有 `stackSentinel.py` 的 `run_NN_*` 那种扁平 shell 命令清单 — 每个阶段的实际工作都在 Python 方法中（`_run_align_unit`/`_run_intf_unit`/`_run_merge_unit`），因此每个 HPC 子作业的"命令"都是重新进入 `insarhub` 自身（内部的 `run-stage-unit` CLI 动作），在新进程中调用其中一个方法，而不是直接执行调用 GMTSAR 可执行文件的 shell 命令行。

        详见 [HPC（SLURM）](hpc.md)。

    - **刷新状态**

        从 GMTSAR 自身的输出标记读取每个干涉对的状态。p2p 模式下会以每个干涉对一行的彩色表格显示，并用 SLURM 的实时状态覆盖过期的标记文件。

        ```python
        jobs = processor.refresh()
        ```

    - **重试失败的干涉对**

        只重新运行状态为 `FAILED` 的干涉对。

        ```python
        processor.retry()
        ```

    - **等待完成**

        轮询直到每个干涉对都为 `SUCCEEDED` 或 `FAILED`。

        ```python
        processor.watch()
        ```

    - **保存状态**

        `submit()` 之后作业状态会自动保存到 `<workdir>/gmtsar/gmtsar_jobs.json`。

        ```python
        processor.save()
        ```

    - **取消（HPC 模式）**

        对 HPC 提交执行 `scancel`，涵盖管理器与全部子作业，两种模式均适用。p2p 的作业是通过 `hpc/p2p/` 目录识别的，而不是依赖 `config.hpc_mode`，因此直接执行 `cancel` 即可找到它们、无需重复加 `--hpc-mode`；任何仍处于 `PENDING`/`RUNNING` 的干涉对会被标记为 `FAILED`，以免 `refresh` 继续把它显示为运行中。

        ```python
        processor.cancel()
        ```

    - **输出目录结构**

        单子条带：`<workdir>/gmtsar/intf/<julian_date_pair>/`（例如 `intf/2019184_2019196/` — 这是 GMTSAR 自己的儒略日命名，而不是 ref/sec 词干）— 保持 GMTSAR 的原生文件名（`corr_ll.grd`、`phasefilt_ll.grd`、`*.PRM`），正是 MintPy 的 `prep_gmtsar.py` 直接期望的形式。

        多子条带：`<workdir>/gmtsar/<ref_safe>_<sec_safe>/merge/` — 跨所有指定子条带的合并、地理编码产品（`phasefilt_ll.grd`、`corr_ll.grd`，以及 PNG/KML 预览图）。

    - **时序分析：请使用 MintPy，而非 GMTSAR 自带的 `sbas`**

        这是选择 p2p 的直接后果。GMTSAR 自带的 `sbas` 在**雷达坐标**下运行，要求所有 SLC 都重采样到同一个公共网格 — 而 p2p 的逐对配准并不提供这一点。MintPy 的 `prep_gmtsar` 读取的是**地理编码后**的 `*_ll.grd`，所有干涉对本来就在同一地理网格上，因此无需公共配准主影像。请使用 `GMTSAR_MINTPY_SBAS` 分析器。


    - **无需本地安装 GMTSAR 即可运行**

        与 `ISCE2_S1` 相同，把 `container` 设为一个装有 `insarhub` + GMTSAR 的 `.sif` 或 Docker 镜像，即可跳过本地探测：

        ```bash
        insarhub processor -N GMTSAR_S1 -w /data/stack submit \
            --container ghcr.io/jldz9/insarhub-gmtsar:latest
        ```

        在 HPC 模式下，只有各阶段的子作业在容器内运行，sbatch 管理器脚手架仍留在宿主机上。

=== "ISCE3_Burst"

    `ISCE3_Burst` 处理器从 ASF `SLC-BURST` 数据构建干涉图栈，使用 [ISCE3](https://github.com/isce-framework/isce3)/[COMPASS](https://github.com/opera-adt/COMPASS) 进行地理编码，其后的所有环节由 [dolphin](https://github.com/isce-framework/dolphin) 完成。请与 `S1_Burst` 下载器配合使用。

    它最本质的特点是**不做配准**。COMPASS 把每一景独立地理编码到绝对 UTM 坐标，因此同一个 burst 的任意两个日期在构造上就是逐像元对齐的 — 逐对配准可能引入的错配伪影在这里根本不会出现。

    共九个阶段，按顺序执行：

    | 阶段 | 工具 | 产出 |
    |---|---|---|
    | `dem` | `sardem` | Copernicus DEM + NASADEM 水体掩膜 |
    | `tec` | COMPASS | 每个采集日期一个 IONEX 电离层图 |
    | `cslc` | `s1_geocode_stack.py` → `run_*.sh` | 每个 burst-日期一个地理编码 CSLC |
    | `static` | `s1_static_layers.py` | LOS/入射角几何量，随后重采样到栈网格 |
    | `crop` | dolphin | 按 AOI 裁剪每个 burst |
    | `ifg` | dolphin | 干涉图（见 `ifg_mode`） |
    | `stitch` | dolphin | 把每个干涉对的各 burst 拼接成一景 |
    | `filt` | dolphin | 多视 → Goldstein 滤波 → 相干性 |
    | `unwrap` | snaphu | 解缠相位 + 连通分量 |

    ### 选择估计方式 — `ifg_mode`

    | 取值 | 干涉对来源 | 说明 |
    |---|---|---|
    | `phase_link`（默认） | 完整协方差估计 | 所有干涉对都参与；由 `pl_*` 参数调节 |
    | `network` | 规则生成 | `n_connections` 或 `max_temporal_baseline` |
    | `user_defined` | 本目录的 `stack_*.json` | 恰好使用 `select_pairs` 选出的干涉对 |

    默认使用 `phase_link`，因为它的效果有实测优势：在测试栈上得到 1 个连通分量、覆盖率 83%，而逐对网络为 3 个、55%；闭合误差从 0.157 rad 降到 0.067 rad。其参数与 dolphin 自身发布的配置一致（`glrt` / 0.001、半窗 7×14、ministack 15）。

    在 `phase_link` + `pl_ifg_network=single_reference`（均为默认值）下，用户自定义网络会被忽略 — 估计器的输出本身就*是*那个网络。若希望在该模式下仍使用自己的干涉对，请设置 `pl_ifg_network=bandwidth`。

    ### 处理范围

    `AOI` 会自动取自本目录下载器配置中的 `intersectsWith`，通常已经填好。若要改为处理整个已下载的 burst 范围，勾选 `process_full_extent`。注意 `crop_buffer_deg`（默认 0.05°）会在四周各外扩一圈 — 对较小的 AOI 而言，这个缓冲本身就可能接近整个 burst 范围，因此若希望 AOI 真正起到裁剪作用，请调小该值。

    `dem` 与 `cslc` 在任何地理编码完成之前运行，因此始终使用 `AOI`；`process_full_extent` 从 `crop` 阶段起才生效。

    ### 其他说明

    - 各阶段针对 SLURM 做了拆分 — 参见 [HPC（SLURM）](hpc.md)。`cslc` 是每个 burst-日期一个作业，也是耗时主体；`phase_link` 下的 `ifg` 是单个作业，因为估计器没有逐干涉对的单元。
    - 干涉图网络取各 burst 日期列表的**交集**。若 ASF 在某天缺少某个 burst 的数据，该日期会被剔除并明确列出，从而保证每个干涉对在所有 burst 上都能生成。
    - 时序分析使用 `Dolphin_SBAS` 分析器，它同时支持两种估计方式。

    ::: insarhub.processor.isce3_burst.ISCE3_Burst
        options:
            heading_level: 0
            members: false


*[HyP3]: Hybrid Pluggable Processing Pipeline
