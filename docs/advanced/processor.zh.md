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

=== "ISCE_S1"

    ISCE_S1 处理器在本地运行 ISCE2 `stackSentinel`，从下载的 SLC `.SAFE` 文件生成 Sentinel-1 干涉图。它生成一系列编号运行脚本并顺序执行，在每个步骤内并行运行独立命令。

    - **导入处理器**

        ```python
        from insarhub import Processor
        ```

    - **创建处理器**

        ```python
        from insarhub.config import ISCE_S1_Config

        cfg = ISCE_S1_Config(
            workdir='/data/p100_f466',
            bbox=[33.0, 38.0, -120.0, -115.0],   # [南, 北, 西, 东]
        )
        pairs = [('20200101', '20200113'), ('20200113', '20200125')]
        processor = Processor.create('ISCE_S1', pairs=pairs, config=cfg)
        ```

        ::: insarhub.config.defaultconfig.ISCE_S1_Config
            options:
                members: false
                show_source: false
                heading_level: 0

    - **提交（本地模式）**

        生成运行脚本并在后台进程中开始顺序执行。立即返回；使用 `refresh()` 监控进度。

        ```python
        jobs = processor.submit()
        ```

        ::: insarhub.processor.isce_s1.ISCE_S1.submit
            options:
                members: false
                show_source: false
                heading_level: 5

    - **提交（HPC / SLURM 模式）**

        设置 `hpc_mode=True` 启用滑动窗口 SLURM 管理器。每个步骤提交一个轻量级管理器作业，管理器随时保持最多 `max_concurrent_hpc` 个子作业同时运行，有空槽时立即补充。命令数相同的连续步骤自动合并为单个组管理器。步骤之间通过 `--dependency=afterok` 串联。每个 sbatch 脚本按命令记录带耗时秒数的 `START`/`DONE`/`FAIL` 日志。

        ```python
        cfg = ISCE_S1_Config(
            workdir='/data/p100_f466',
            bbox=[33.0, 38.0, -120.0, -115.0],
            hpc_mode=True,
            max_concurrent_hpc=12,   # 默认值；根据集群公平份额限制调整
        )
        processor = Processor.create('ISCE_S1', pairs=pairs, config=cfg)
        processor.submit()
        ```

        `retry()` 从已保存的作业元数据（`slurm_job_ids` / `hpc_manager` / `hpc_array`）自动检测 HPC 模式，无需再次传入 `hpc_mode=True`。

    - **试运行**

        预览运行脚本和路径检查，不执行任何操作。

        ```python
        cfg = ISCE_S1_Config(
            workdir='/data/p100_f466',
            bbox=[33.0, 38.0, -120.0, -115.0],
            dry_run=True,
        )
        processor = Processor.create('ISCE_S1', pairs=pairs, config=cfg)
        processor.submit()
        ```

    - **刷新**

        从磁盘读取步骤和命令状态。

        ```python
        jobs = processor.refresh()
        ```

        ::: insarhub.processor.isce_base.ISCE_Base.refresh
            options:
                members: false
                show_source: false
                heading_level: 5

    - **重试失败步骤**

        重新运行所有状态为 `FAILED` 的步骤。

        ```python
        processor.retry()
        ```

        ::: insarhub.processor.isce_base.ISCE_Base.retry
            options:
                members: false
                show_source: false
                heading_level: 5

    - **取消**

        终止正在运行的后台进程（本地模式）或对所有活动 SLURM 任务执行 `scancel`（HPC 模式）。

        ```python
        processor.cancel()
        ```

        ::: insarhub.processor.isce_base.ISCE_Base.cancel
            options:
                members: false
                show_source: false
                heading_level: 5

    - **监控**

        定期轮询步骤状态，直到所有步骤完成。

        ```python
        processor.watch(refresh_interval=60)
        ```

        ::: insarhub.processor.isce_base.ISCE_Base.watch
            options:
                members: false
                show_source: false
                heading_level: 5

    - **保存 / 加载**

        任务状态在 `submit()` 后自动保存。从已保存的任务文件重新加载并恢复：

        ```python
        cfg = ISCE_S1_Config(
            workdir='/data/p100_f466',
            saved_job_path='/data/p100_f466/isce/isce_jobs_<timestamp>.json',
        )
        processor = Processor.create('ISCE_S1', pairs=[], config=cfg)
        processor.refresh()   # 或 .retry()、.cancel()、.watch()
        ```

*[HyP3]: Hybrid Pluggable Processing Pipeline
