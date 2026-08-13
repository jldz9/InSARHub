InSARHub 分析器模块提供 InSAR 时序分析工作流。

- **导入分析器**

    导入 Analyzer 类以访问所有时序分析功能
```python
from insarhub import Analyzer
```

- **查看可用分析器**

    列出所有已注册的分析器
```python
Analyzer.available()
```

## 可用分析器

=== "Mintpy_SBAS_Base_Analyzer"

    InSARHub 将 [Mintpy](https://github.com/insarlab/MintPy) 封装为其分析后端之一。`Mintpy_SBAS_Base_Analyzer` 基于可复用的基础配置类实现，提供 Mintpy 完整的 `smallbaselineApp` 逻辑。为用户提供类似于直接使用 MintPy 的体验，支持对处理参数和步骤进行完整自定义。

    ::: insarhub.analyzer.mintpy_base.Mintpy_SBAS_Base_Analyzer
        options:
            members: false
            heading_level: 0

    ### 使用方法

    - **使用参数创建分析器**

        初始化分析器实例

        ```python
        analyzer = Analyzer.create('Mintpy_SBAS_Base_Analyzer',
                                    workdir="/your/work/dir",
                                    load_processor="hyp3", ....)
        ```
        或
        ```python
        params = {"workdir": "/your/work/dir", "load_processor": "hyp3" ....}
        analyzer = Analyzer.create('Mintpy_SBAS_Base_Analyzer', **params)
        ```
        或
        ```python
        from insarhub.config import Mintpy_SBAS_Base_Config
        cfg = Mintpy_SBAS_Base_Config(workdir="/your/work/dir",
                                      load_processor="hyp3",
                                      ....)
        analyzer = Analyzer.create('Mintpy_SBAS_Base_Analyzer', config=cfg)
        ```

        基础配置 `Mintpy_SBAS_Base_Config` 包含 Mintpy `smallbaselineApp.cfg` 的所有参数。有关每个参数的详细说明，请参阅 [Mintpy 官方配置文档](https://github.com/insarlab/MintPy/blob/054c6010b5e40e98fe16e283121fdd1ae4bc1732/src/mintpy/defaults/smallbaselineApp.cfg)。

        ::: insarhub.config.Mintpy_SBAS_Base_Config
            options:
                members: false
                heading_level: 0

    - **运行**

        根据提供的配置运行 Mintpy 时序分析

        ```python
        analyzer.run()
        ```

        ::: insarhub.analyzer.Mintpy_SBAS_Base_Analyzer.run
            options:
                members: true
                show_source: false
                heading_level: 5

    - **提交（HPC / SLURM 模式）**

        生成一个涵盖所有选定步骤的单个 `sbatch` 脚本并提交至 SLURM。`Hyp3_SBAS` 和 `ISCE_SBAS` 均继承此方法。

        ```python
        # 将完整流程作为一个 SLURM 作业提交
        analyzer.submit_hpc()

        # 仅提交特定步骤
        analyzer.submit_hpc(steps=["velocity", "geocode"])
        ```

        脚本写入 `<workdir>/mintpy/mintpy_sbas.sbatch`，作业状态保存至 `mintpy/mintpy_job.json`。SLURM 资源来自 `<workdir>/sbatch_options.json` 的 `"17"` 步骤键 — 与 `ISCE2_S1` 自身 HPC 提交（步骤 `01`–`16`）使用同一个文件，因为处理器和分析器通常共用同一工作目录。默认值：`time=24:00:00`、`ntasks=1`、`cpus_per_task=16`、`mem=128G`、`partition=all`。

        `submit_hpc()` 成功时返回 SLURM 作业 ID 字符串；若 `sbatch_options.json` 刚被创建（或补充了缺失的 `"17"` 条目），则返回 `None` — 调用方应检查 `None` 并停止，而不是将其当作提交成功处理：

        ```python
        cfg = Mintpy_SBAS_Base_Config(
            workdir="/your/work/dir",
            load_processor="hyp3",
            hpc_mode=True,
        )
        analyzer = Analyzer.create('Hyp3_SBAS', config=cfg)
        job_id = analyzer.submit_hpc()
        if job_id is None:
            print("sbatch_options.json 刚被创建/更新 — 请先检查，再重新提交。")
        ```

        直接编辑 `sbatch_options.json` 中的 `"17"` 步骤以更改资源（例如 `{"17": {"time": "48:00:00", "mem": "256G", "partition": "gpu"}}`），然后再次调用 `submit_hpc()`。

        ::: insarhub.analyzer.mintpy_base.Mintpy_SBAS_Base_Analyzer.submit_hpc
            options:
                members: false
                show_source: false
                heading_level: 5

    - **绘图**

        基于已计算完成的结果，（重新）生成 `mintpy/pic/` 下的图片，不重新计算任何内容。`run()` 自身的自动绘图只在单次调用中涵盖一个以上步骤时才会触发（与 MintPy 自身的 CLI 语义一致）— CLI 和 GUI 在内部都是逐步执行每个步骤以提供逐步进度反馈，因此该条件在那里实际上永远不会触发；`plot()` 是显式的独立替代方案，两者都在各自的步骤序列完成后调用一次（或按需调用，例如调整了与绘图相关的配置值后，只想重新生成图片而不重新运行整个流程）。

        ```python
        analyzer.plot()
        ```

        ::: insarhub.analyzer.mintpy_base.Mintpy_SBAS_Base_Analyzer.plot
            options:
                members: false
                show_source: false
                heading_level: 5

    - **无需本地安装 MintPy（或 ISCE2）**

        将 `container` 字段设置为 Apptainer/Singularity `.sif` 镜像的路径，或 Docker 镜像引用（name[:tag]），`run()`/`prep_data()`/`submit_hpc()` 都会在容器内而非宿主机上重新执行同一个 `insarhub analyzer ...` CLI 调用 — 工作目录会以相同路径绑定挂载，因此输出会像本机运行一样落在原处。容器镜像只需在 MintPy（`ISCE_SBAS` 还需要 ISCE2）旁额外安装 `insarhub`（可参考仓库根目录的 [`Dockerfile`](https://github.com/jldz9/InSARHub/blob/main/Dockerfile) 作为现成示例）。

        ```python
        cfg = Mintpy_SBAS_Base_Config(
            workdir="/your/work/dir",
            load_processor="hyp3",
            container="ghcr.io/jldz9/insarhub-isce2:latest",
        )
        analyzer = Analyzer.create('Hyp3_SBAS', config=cfg)
        analyzer.run()
        ```

        `container` 是按次调用的设置，而非持久化配置 — 之后每次调用若也要在容器内运行，都需要再次设置。

    - **清理**

        删除时序处理过程中生成的中间处理文件

        ```python
        analyzer.cleanup()
        ```

        ::: insarhub.analyzer.Mintpy_SBAS_Base_Analyzer.cleanup
            options:
                members: true
                show_source: false
                heading_level: 5

=== "Hyp3_SBAS"

    `Hyp3_SBAS` 是专门为处理 HyP3 InSAR 产品时序数据而预配置的分析器，扩展自 `Mintpy_SBAS_Base_Analyzer`。

    ::: insarhub.analyzer.Hyp3_SBAS
        options:
            members: false
            heading_level: 0

    ### 使用方法

    - **使用参数创建分析器**

        初始化分析器实例

        ```python
        analyzer = Analyzer.create('Hyp3_SBAS',
                                    workdir="/your/work/dir")
        ```
        或
        ```python
        params = {"workdir": "/your/work/dir"}
        analyzer = Analyzer.create('Hyp3_SBAS', **params)
        ```
        或
        ```python
        from insarhub.config import Mintpy_SBAS_Base_Config
        cfg = Mintpy_SBAS_Base_Config(workdir="/your/work/dir")
        analyzer = Analyzer.create('Hyp3_SBAS', config=cfg)
        ```

    - **准备数据**

        将从 HyP3 服务器下载的干涉图数据准备至 MintPy

        ```python
        analyzer.prep_data()
        ```

        ::: insarhub.analyzer.Hyp3_SBAS.prep_data
            options:
                members: false
                heading_level: 5

    - **运行**

        根据提供的配置运行 Mintpy 时序分析

        ```python
        analyzer.run()
        ```

        ::: insarhub.analyzer.Hyp3_SBAS.run
            options:
                members: false
                heading_level: 5

    - **提交（HPC / SLURM 模式）**

        继承自 `Mintpy_SBAS_Base_Analyzer`，将完整 MintPy 流程作为单个 sbatch 作业提交。

        ```python
        analyzer.submit_hpc()
        ```

    - **清理**

        删除时序处理过程中生成的中间处理文件

        ```python
        analyzer.cleanup()
        ```

        ::: insarhub.analyzer.Mintpy_SBAS_Base_Analyzer.cleanup
            options:
                members: true
                show_source: false
                heading_level: 5

=== "ISCE_SBAS"

    `ISCE_SBAS` 分析器扩展自 `Mintpy_SBAS_Base_Analyzer`，专为 ISCE2 `stackSentinel` 输出预配置。`prep_data()` 自动发现 `isce/` 目录中的干涉图和几何数据，并将 MintPy 配置写入 `mintpy/.mintpy.cfg`。所有 MintPy 输出写入 `workdir/mintpy/`。

    ::: insarhub.analyzer.isce_sbas.ISCE_SBAS
        options:
            members: false
            heading_level: 0

    ### 使用方法

    - **创建分析器**

        ```python
        from insarhub import Analyzer

        analyzer = Analyzer.create('ISCE_SBAS', workdir='/your/work/dir')
        ```

        或使用显式配置：

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

    - **准备数据**

        自动发现 ISCE2 输出并写入 `mintpy/.mintpy.cfg`。

        ```python
        analyzer.prep_data()
        ```

        ::: insarhub.analyzer.isce_sbas.ISCE_SBAS.prep_data
            options:
                members: false
                show_source: false
                heading_level: 5

    - **运行**

        运行 MintPy SBAS 时序分析。所有输出写入 `workdir/mintpy/`。

        ```python
        analyzer.run()
        ```

        ::: insarhub.analyzer.isce_sbas.ISCE_SBAS.run
            options:
                members: false
                show_source: false
                heading_level: 5

    - **提交（HPC / SLURM 模式）**

        继承自 `Mintpy_SBAS_Base_Analyzer`，将完整 MintPy 流程作为单个 sbatch 作业提交。

        ```python
        analyzer.submit_hpc()
        ```

    - **清理**

        删除 `load_data` 后不再需要的大型 ISCE2 中间目录和输入数据。
        删除 `isce/coarse_interferograms/`、`isce/ESD/`、`isce/coreg_secondarys/`、`isce/interferograms/`、`slc/` 和 `dem/`。

        ```python
        analyzer.cleanup()
        ```

        ::: insarhub.analyzer.isce_sbas.ISCE_SBAS.cleanup
            options:
                members: false
                show_source: false
                heading_level: 5

=== "GMTSAR_MINTPY_SBAS"

    `GMTSAR_MINTPY_SBAS` 分析器对 `GMTSAR_S1` 处理器生成的相干堆叠运行 MintPy SBAS 时序分析。它将 GMTSAR 的地理编码 `*_ll.grd` 产品和 `baseline_table.dat` 交给 MintPy 自带的 `prep_gmtsar.py` 加载器（通过 `mintpy.load.*` 键），因此无需任何公共配准参考即可工作——每对干涉图已经共享同一地理网格。它是 `ISCE_SBAS` 的 MintPy 对应物，唯一区别在于如何配置 `load_*` 路径。输出写入 `workdir/gmtsar_mintpy/`（独立目录，不会与同一工作目录中的 Hyp3/ISCE MintPy 运行相互覆盖）。

    ::: insarhub.analyzer.gmtsar_mintpy_sbas.GMTSAR_MINTPY_SBAS
        options:
            members: false
            heading_level: 0

    ### 使用方法

    - **创建分析器**

        ```python
        from insarhub import Analyzer

        analyzer = Analyzer.create('GMTSAR_MINTPY_SBAS', workdir='/your/work/dir')
        ```

        或使用显式配置：

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

    - **准备数据**

        发现 GMTSAR 输出（stack_mode 的 `merge/<julian_pair>/`，或 p2p 的 `gmtsar/<ref>_<sec>/merge/`）并写入 MintPy 配置。对于 p2p 输出，它将合并后的 `unwrap_ll.grd`/`corr_ll.grd` 暂存为 MintPy 期望的 `<pair>/unwrap_ll.grd` 结构（符号链接，无需复制数 GB 网格），并保留 GMTSAR 的儒略日 `yyyyddd_yyyyddd` 目录命名，`prep_gmtsar.py` 据此推导配对日期。

        ```python
        analyzer.prep_data()
        ```

        ::: insarhub.analyzer.gmtsar_mintpy_sbas.GMTSAR_MINTPY_SBAS.prep_data
            options:
                members: false
                show_source: false
                heading_level: 5

    - **运行**

        运行 MintPy SBAS 时序分析。所有输出写入 `workdir/gmtsar_mintpy/`。

        ```python
        analyzer.run()
        ```

        ::: insarhub.analyzer.gmtsar_mintpy_sbas.GMTSAR_MINTPY_SBAS.run
            options:
                members: false
                show_source: false
                heading_level: 5

    - **提交（HPC / SLURM 模式）**

        继承自 `Mintpy_SBAS_Base_Analyzer`，将完整 MintPy 流程作为单个 sbatch 作业提交（脚本写入 `workdir/gmtsar_mintpy/mintpy_sbas.sbatch`）。

        ```python
        analyzer.submit_hpc()
        ```

    - **清理**

        ```python
        analyzer.cleanup()
        ```

        ::: insarhub.analyzer.mintpy_base.Mintpy_SBAS_Base_Analyzer.cleanup
            options:
                members: true
                show_source: false
                heading_level: 5

=== "GMTSAR_SBAS"

    `GMTSAR_SBAS` 分析器在 `GMTSAR_S1` stack_mode 堆叠上运行 **GMTSAR 自带的原生 SBAS 反演**（`prep_sbas` + `sbas` C 二进制程序）——不涉及 MintPy。它读取 `workdir/gmtsar/`（`intf.in`、`baseline_table.dat`、`intf/<pair>/`），并在 `workdir/gmtsar_sbas/` 下以雷达坐标生成每个日期的累计位移（`disp_*.grd`）和线性速度（`vel.grd`）。

    由于反演是 GMTSAR 的 C 二进制程序，配置中的 `gmtsar_root` 和 `gmtsar_env_bin` **均为必需项**——`sbas` 二进制程序和 `gmt` 来自 GMTSAR 自身的安装/conda 环境，而非 InSARHub。

    ::: insarhub.analyzer.gmtsar_sbas.GMTSAR_SBAS
        options:
            members: false
            heading_level: 0

    ### 使用方法

    - **创建分析器**

        ```python
        from insarhub import Analyzer

        analyzer = Analyzer.create('GMTSAR_SBAS', workdir='/your/work/dir',
                                   gmtsar_root='/path/to/gmtsar',
                                   gmtsar_env_bin='/path/to/conda/envs/gmtsar/bin')
        ```

        或使用显式配置：

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

    - **准备数据**

        从堆叠的 `baseline_table.dat` 构建 `intf.tab` 和 `scene.tab`，然后回显待运行的 `sbas intf.tab scene.tab N S xdim ydim` 命令行。

        ```python
        analyzer.prep_data()
        ```

        ::: insarhub.analyzer.gmtsar_sbas.GMTSAR_SBAS.prep_data
            options:
                members: false
                show_source: false
                heading_level: 5

    - **运行**

        运行 `sbas` 反演，将进度实时输出到控制台及 `workdir/gmtsar_sbas/` 下的 `sbas.log`。

        ```python
        analyzer.run()
        ```

        ::: insarhub.analyzer.gmtsar_sbas.GMTSAR_SBAS.run
            options:
                members: false
                show_source: false
                heading_level: 5

=== "Dolphin_SBAS"

    `Dolphin_SBAS` 分析器对 `ISCE3_Burst` 生成的解缠干涉图堆叠运行 dolphin 的 `timeseries.run`。同一分析器同时服务于该处理器的**两种**缠绕相位估计器（`ifg_mode="network"` 与 `ifg_mode="phase_link"`）——两者的线性反演完全相同；区别仅在于用于选择参考点和掩膜低质量像素的质量栅格（`phase_link` 用时序相干性，`network` 用逐对相关性的时间平均）。估计器选择从堆叠的 `ifg_manifest.json` 读取，绝不会在此处重复指定。输出写入 `workdir/timeseries/`。

    ::: insarhub.analyzer.dolphin_sbas.Dolphin_SBAS
        options:
            members: false
            heading_level: 0

    ### 使用方法

    - **创建分析器**

        ```python
        from insarhub import Analyzer

        analyzer = Analyzer.create('Dolphin_SBAS', workdir='/your/work/dir')
        ```

        或使用显式配置：

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

    - **运行**

        对当前工作目录中的堆叠运行 dolphin 时序反演（累计位移、速度、残差）。

        ```python
        analyzer.run()
        ```

        ::: insarhub.analyzer.dolphin_sbas.Dolphin_SBAS.run
            options:
                members: false
                show_source: false
                heading_level: 5
