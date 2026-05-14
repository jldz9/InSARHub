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

        脚本写入 `<workdir>/mintpy/mintpy_sbas.sbatch`，作业状态保存至 `mintpy/mintpy_job.json`。默认资源：`time=24:00:00`、`ntasks=1`、`cpus_per_task=16`、`mem=128G`、`partition=all`。通过配置中的 `hpc_sbatch_opts` 覆盖：

        ```python
        cfg = Mintpy_SBAS_Base_Config(
            workdir="/your/work/dir",
            load_processor="hyp3",
            hpc_mode=True,
            hpc_sbatch_opts={"time": "48:00:00", "mem": "256G", "partition": "gpu"},
        )
        analyzer = Analyzer.create('Hyp3_SBAS', config=cfg)
        analyzer.submit_hpc()
        ```

        ::: insarhub.analyzer.mintpy_base.Mintpy_SBAS_Base_Analyzer.submit_hpc
            options:
                members: false
                show_source: false
                heading_level: 5

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
