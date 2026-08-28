# 后端贡献指南

后端为纯 Python 实现，包含 FastAPI 路由、处理器/分析器类、CLI 以及共享工具。

## 环境搭建

```bash
conda env create -f environment.yml
conda activate insarhub
pip install -e ".[dev]"
```

## 架构概览

InSARHub 使用注册表模式。所有带有 `name` 属性的 `Processor`、`Analyzer`、`Downloader` 子类均会被自动发现，并可通过 `Processor.create("MyName", cfg)` 调用。

```
CloudProcessor (ABC) ──► Hyp3Base   ──► Hyp3_S1
LocalProcessor (ABC) ──► ISCE2_Base ──► ISCE2_S1
                       ├──► GMTSAR_S1
                       └──► ISCE3_Base ──► ISCE3_Burst

BaseDownloader (ABC) ──► ASF_Base_Downloader ──► S1_SLC
                                             └──► S1_Burst

BaseAnalyzer (ABC) ──► Mintpy_SBAS_Base_Analyzer ──► Hyp3_Mintpy_SBAS
                                                   ├──► ISCE2_Mintpy_SBAS
                                                   └──► GMTSAR_Mintpy_SBAS
                    ├──► GMTSAR_SBAS   （GMTSAR 自带的 sbas 二进制程序，无需 MintPy）
                    └──► ISCE3_Dolphin_PL  （面向 ISCE3_Burst 的 dolphin timeseries）
```

每个中间基类（`Hyp3Base`、`ISCE2_Base`、`ISCE3_Base`、`ASF_Base_Downloader`、`Mintpy_SBAS_Base_Analyzer`）已实现所有共享基础设施——认证、任务跟踪、HPC 提交、文件 I/O。具体的叶子类只需实现 `submit()`（分析器还需实现 `prep_data()`/`run()`）来处理传感器特定的逻辑。`GMTSAR_S1` 和 `ISCE3_Burst` 还将大型、仅限单一处理器的辅助逻辑拆分为私有的同级模块（`processor/_gmtsar_esd_network.py` 用于 GMTSAR 的网络 ESD 配准，`processor/isce3_base.py` 用于 ISCE3 的阶段/HPC 机制）。

CLI（`cli/main.py`）和 GUI 路由（`app/routes/`）都是对同一套 Python API 的薄封装——任何在 CLI 中可运行的工作流，在浏览器中同样可以运行。它们共享的已保存任务发现/处理器重载逻辑位于 `utils/local_processor_reload.py`（而非 `core/`），从而使两个界面保持一致，避免逐渐偏离。

## 路径规范

所有子目录路径均集中定义在 `config/paths.py` 中。**不要**直接硬编码 `workdir / "hyp3"` 等路径，请使用数据类属性：

```python
from insarhub.config.paths import Hyp3Paths, ISCEPaths, MintPyPaths

Hyp3Paths(workdir).output_dir       # workdir/hyp3
Hyp3Paths(workdir).jobs_file        # workdir/hyp3_jobs.json

ISCEPaths(workdir).isce_dir         # workdir/isce
ISCEPaths(workdir).slc_dir          # workdir/slc
ISCEPaths(workdir).dem_dir          # workdir/dem

MintPyPaths(workdir).mintpy_dir     # workdir/mintpy
MintPyPaths(workdir).tmp_dir        # workdir/mintpy/tmp
MintPyPaths(workdir).clip_dir       # workdir/mintpy/clip

GMTSARPaths(workdir).case_dir       # workdir/gmtsar
ISCE3Paths(workdir).cropped_gslc_dir  # workdir/cropped_gslc
```

每个处理器家族都有自己的数据类（`Hyp3Paths`、`ISCEPaths`、`GMTSARPaths`、`ISCE3Paths`）。每个本地处理器写出的后台执行器产物由共享常量命名，并按家族暴露，从而落在各自处理器的目录下——切勿硬编码：

```python
from insarhub.config.paths import EXECUTOR_LOG, EXECUTOR_PID

ISCEPaths(workdir).executor_log     # workdir/isce/executor.log
GMTSARPaths(workdir).executor_log   # workdir/gmtsar/executor.log
ISCE3Paths(workdir).executor_log    # workdir/executor.log （ISCE3 写在根目录）
```

如果新处理器写入新的子目录，需在 `config/paths.py` 中添加对应的数据类。

## 容器执行（`INSARHUB_CONTAINER_CHILD`）

设置了 `config.container` 的本地处理器/分析器不会在主机上运行工具，而是**在镜像内部重新调用同一条 `insarhub` 命令**（`_reinvoke_via_container` / `_run_via_container`），并以完全相同的路径挂载工作目录。子进程会被打上环境变量 **`INSARHUB_CONTAINER_CHILD=1`**。

任何决定是否容器化、后台 fork 或重新提交的代码，都**必须同时**检查 `config.container` **和**该环境变量，从而让主机只容器化一次、子进程直接执行工作：

```python
if self.config.container and not os.environ.get("INSARHUB_CONTAINER_CHILD"):
    return self._reinvoke_via_container("submit")   # 主机：启动容器
# ...在无容器的主机上，或在容器子进程内部才会到达这里...
```

漏掉 `INSARHUB_CONTAINER_CHILD` 这一半就是经典的 "docker not found" 报错：子进程仍能看到 `config.container`（它会被持久化到 `insarhub_config.json` 以便重试），于是试图再启动一个容器——docker-in-docker——但镜像内部没有 docker。同一处守卫还会强制子进程**同步**运行（不 fork+detach），从而避免 `docker run --rm` 在工作完成前拆掉容器。

## 添加新处理器

创建 `src/insarhub/processor/myprocessor.py`，设置 `name`，在 `config/defaultconfig.py` 中添加配置数据类。继承对应基类——各基类已处理所有共享基础设施，子类只需实现 `submit()`：

### 添加新基础处理器

如需引入全新的中间基类（例如支持 HyP3 和 ISCE2 以外的后端 API），直接继承 `insarhub/core/base.py` 中的 ABC：

- `CloudProcessor` — 用于向外部 API 提交任务的云端处理器
- `LocalProcessor` — 用于逐步执行 shell 命令的本地处理器

实现所有抽象方法，再为每种传感器子类化新基类。

=== "CloudProcessor"

    ```python
    # src/insarhub/processor/mycloud_base.py
    from insarhub.core.base import CloudProcessor
    from insarhub.config import MyCloud_Base_Config

    class MyCloud_Base(CloudProcessor):
        # 不设置 `name`——基类不应自动注册
        default_config = MyCloud_Base_Config

        def __init__(self, config=None):
            super().__init__(config)
            self.client = MyCloudAPIClient(
                username=self.config.username,
                password=self.config.password,
            )

        def submit(self): ...
        def refresh(self): ...
        def download(self, *args, **kwargs): ...
        def retry(self): ...
        def watch(self): ...
        def save(self, path=None): ...
        def check_credits(self): ...
    ```

=== "LocalProcessor"

    ```python
    # src/insarhub/processor/mylocal_base.py
    from insarhub.core.base import LocalProcessor
    from insarhub.config import MyLocal_Base_Config

    class MyLocal_Base(LocalProcessor):
        # 不设置 `name`——基类不应自动注册
        default_config = MyLocal_Base_Config

        def submit(self): ...   # 生成运行脚本，暂存输入
        def refresh(self): ...  # 重新扫描 .done / .fail 步骤标记
        def retry(self): ...    # 清除 .fail 标记并重新运行
        def watch(self): ...    # 阻塞直到所有步骤完成
        def save(self, path=None): ...
    ```

### 扩展现有基础处理器

=== "Hyp3Base"

    `Hyp3Base` 已处理 Earthdata 认证、多用户积分池轮换、任务提交队列、`refresh()`、`download()`、`retry()`、`watch()` 和 `save()`。子类只需实现 `submit()`——准备任务载荷并调用 `_submit_job_queue`。

    ```python
    # src/insarhub/processor/hyp3_mysensor.py
    from insarhub.processor.hyp3_base import Hyp3Base
    from insarhub.config import MyHyp3Config

    class Hyp3_MySensor(Hyp3Base):
        name = "Hyp3_MySensor"
        description = "MySensor 的 HyP3 处理。"
        compatible_downloader = "MySensor_SLC"
        default_config = MyHyp3Config

        def __init__(self, config: MyHyp3Config | None = None):
            super().__init__(config)
            self.cost = self.client.costs()["MY_JOB_TYPE"]["cost_table"]["default"]

        def submit(self):
            job_queue = [
                {
                    "job_type": "MY_JOB_TYPE",
                    "job_parameters": {"granules": [ref, sec], "looks": self.config.looks},
                    "name": f"{self.config.name_prefix}_{ref[:15]}",
                }
                for ref, sec in self.config.pairs
            ]
            return self._submit_job_queue(job_queue)
    ```

    Config — 在 `config/defaultconfig.py` 中继承 `Hyp3_Base_Config`：

    ```python
    @dataclass
    class MyHyp3Config(Hyp3_Base_Config):
        looks: str = "20x4"

        _ui_groups = [{"id": "job", "label": "任务"}]
        _ui_fields  = [
            {"group": "job", "key": "looks", "label": "视数",
             "type": "select", "options": ["20x4", "10x2"]},
        ]
    ```

=== "ISCE2_Base"

    `ISCE2_Base` 处理所有运行文件执行、逐步状态跟踪（`.done`/`.fail` 标记）、通过 SLURM 的滑动窗口 HPC 提交、`refresh()`、`retry()`、`watch()` 和 `save()`。子类只需实现 `submit()`——设置 ISCE2 输入命名空间并生成运行脚本，再调用 `_step_executor`。

    ```python
    # src/insarhub/processor/isce_mysensor.py
    from insarhub.processor.isce2_base import ISCE2_Base
    from insarhub.config import ISCE_MySensor_Config
    from insarhub.config.paths import ISCEPaths

    class ISCE_MySensor(ISCE2_Base):
        name = "ISCE_MySensor"
        description = "MySensor 的 ISCE2 处理。"
        compatible_downloader = "MySensor_SLC"
        default_config = ISCE_MySensor_Config

        def submit(self):
            ISCEPaths(self.workdir).isce_dir.mkdir(parents=True, exist_ok=True)

            # 为传感器构建 ISCE2 输入命名空间，生成 run_files/
            inps = self._build_inps_namespace()
            self._run_stack_tool(inps)

            # 交给基类——它会发现 run_files/ 并逐步执行
            self._step_executor(self.steps)
    ```

    Config — 继承 `ISCE2_Base_Config`，为新字段添加 `_ui_groups` / `_ui_fields`。

## 添加新下载器

创建 `src/insarhub/downloader/mysensor_slc.py`。继承 `ASF_Base_Downloader`，它已处理 ASF 认证、场景搜索、覆盖范围绘制、含质量评分的配对选择和并行文件下载。只有需要额外后处理步骤时才覆盖 `download()`。

### 添加新基础下载器

如需支持 ASF 以外的数据存档，直接继承 `insarhub/core/base.py` 中的 `BaseDownloader`。实现所有抽象方法，再为每种产品类型子类化新基类。

```python
# src/insarhub/downloader/myarchive_base.py
from insarhub.core.base import BaseDownloader
from insarhub.config import MyArchive_Base_Config

class MyArchive_Base(BaseDownloader):
    # 不设置 `name`——基类不应自动注册
    default_config = MyArchive_Base_Config

    def search(self, *args, **kwargs): ...   # 查询存档，填充 self.active_results
    def download(self, *args, **kwargs): ... # 下载文件到工作目录
    def filter(self, *args, **kwargs): ...   # 按用户条件筛选 active_results
    def footprint(self, *args, **kwargs): ...# 返回 GeoJSON 覆盖范围（用于地图显示）
    def summary(self, *args, **kwargs): ...  # 返回人类可读的结果摘要
    def reset(self, *args, **kwargs): ...    # 清除搜索状态
```

### 扩展现有基础下载器

=== "ASF_Base_Downloader"

    ```python
    # src/insarhub/downloader/mysensor_slc.py
    from insarhub.downloader.asf_base import ASF_Base_Downloader
    from insarhub.config import MySensor_SLC_Config

    class MySensor_SLC(ASF_Base_Downloader):
        name = "MySensor_SLC"
        description = "MySensor SLC 通过 ASF 搜索和下载。"
        default_config = MySensor_SLC_Config

        def download(self, save_path=None, max_workers=4,
                     download_aux=False, stop_event=None, on_progress=None):
            super().download(save_path=save_path, max_workers=max_workers,
                             stop_event=stop_event, on_progress=on_progress)
            if download_aux:
                self._download_aux_files()

        def _download_aux_files(self):
            ...
    ```

    `search()` 调用后，`self.active_results` 持有 ASF 搜索结果列表，`self.config.workdir` 为已解析的工作目录。

## 添加新分析器

创建 `src/insarhub/analyzer/mysensor_sbas.py`。继承 `Mintpy_SBAS_Base_Analyzer`，它处理 MintPy 配置文件写入、`run()`（将输出写入 `mintpy_dir`）、诊断地理编码和 `cleanup()`。只需实现 `prep_data()`——暂存输入文件并连接 `load_*` 配置字段。

### 添加新基础分析器

如需支持 MintPy 以外的时序分析包，直接继承 `insarhub/core/base.py` 中的 `BaseAnalyzer`。实现所有抽象方法，再为每种输入数据格式子类化新基类。

```python
# src/insarhub/analyzer/myts_base.py
from insarhub.core.base import BaseAnalyzer
from insarhub.config import MyTS_Base_Config

class MyTS_Base(BaseAnalyzer):
    # 不设置 `name`——基类不应自动注册
    default_config = MyTS_Base_Config

    def run(self): ...  # 执行时序分析
```

### 扩展现有基础分析器

=== "Mintpy_SBAS_Base_Analyzer"

    ```python
    # src/insarhub/analyzer/mysensor_sbas.py
    from insarhub.analyzer.mintpy_base import Mintpy_SBAS_Base_Analyzer
    from insarhub.config import MySensor_SBAS_Config

    class MySensor_SBAS(Mintpy_SBAS_Base_Analyzer):
        name = "MySensor_SBAS"
        description = "使用 MintPy 对 MySensor 产品进行 SBAS 时序分析。"
        compatible_processor = "MySensor_Processor"
        default_config = MySensor_SBAS_Config

        def prep_data(self):
            self._collect_and_stage_files()   # 解包/收集到 self.tmp_dir

            # 连接 MintPy load_* 字段
            self.config.load_unwFile      = str(self.tmp_dir / "*" / "unw_phase.tif")
            self.config.load_corFile      = str(self.tmp_dir / "*" / "corr.tif")
            self.config.load_demFile      = str(self.tmp_dir / "*" / "dem.tif")

            super().prep_data()   # 写入 .mintpy.cfg

        def _collect_and_stage_files(self):
            ...
    ```

    `run()` 继承自基类，将所有 MintPy 输出写入 `self.mintpy_dir`（`workdir/mintpy/`）。

## 在 GUI 中暴露设置项

配置字段通过配置数据类上的 `_ui_groups` 和 `_ui_fields` 自动显示在 Web UI 设置面板中，无需修改 React 代码：

```python
@dataclass
class MyProcessorConfig:
    max_workers: int = 4

    _ui_groups = [{"id": "job", "label": "任务"}]
    _ui_fields = [
        {"group": "job", "key": "max_workers", "label": "最大并发数",
         "type": "number", "min": 1, "max": 32},
    ]
```

支持的字段类型：`"number"`、`"text"`、`"boolean"`、`"select"`（需添加 `"options": [...]`）。

## 添加 FastAPI 路由

路由位于 `app/routes/` 目录下。长时间运行的操作通过 `asyncio.to_thread` 在后台线程中执行，通过 `state._jobs[job_id]` 传递进度：

```python
@router.post("/api/my-action")
async def my_action(req: MyRequest, background_tasks: BackgroundTasks):
    job_id, _ = _new_job("Starting…")
    background_tasks.add_task(_run_my_action, job_id, req)
    return {"job_id": job_id}

async def _run_my_action(job_id: str, req: MyRequest):
    def run():
        try:
            # ... 执行工作 ...
            state._jobs[job_id]["progress"] = 50
            _finish_job(job_id, status="done", message="完成。")
        except Exception as e:
            state._stop_events.pop(job_id, None)
            _finish_job(job_id, status="error", message=str(e))
    await asyncio.to_thread(run)
```

在成功和错误路径中都必须 pop `state._stop_events[job_id]`。

## 测试

### 通用冒烟测试

`test/e2e/universal_smoke_test.py` 是针对每一条流水线、基于真实（已完成的）工作目录的“通过/不通过”检查，同时覆盖三个界面：

1. **Python 导入** — 在全新进程中导入每一个包/子模块（捕捉损坏的导入或循环导入）。
2. **CLI** — `--list-*` 命令、每条流水线的 `--list-options`，以及通过子进程运行 `processor … refresh`。
3. **API** — 启动一个临时 `uvicorn` 服务器，用 `requests` 通过 HTTP 访问（无需 `httpx`）：`/api/health`、`/api/folder-local-jobs`、`/api/folder-local-action` refresh、`/api/analyzer-steps`。

它会从每个工作目录的 `insarhub_config.json` 自动识别处理器/分析器，因此无需逐条流水线配置即可覆盖 `GMTSAR_S1`、`ISCE2_S1`、`ISCE3_Burst` 及其分析器。缺失的可选依赖（isce2、gmt/gmtsar、dolphin/compass、slurm）会将相应检查标记为 `SKIP` 而非 `FAIL`。

```bash
python test/e2e/universal_smoke_test.py \
    --scan-dir /path/to/real/workdirs \
    --workdir /path/to/p56 \
    --mode both          # cli | api | both
```

任一检查 `FAIL` 时退出码非零。使用 `--json` 获取机器可读输出，或设置 `INSARHUB_TEST_SCAN_DIRS` 指向你的工作目录，以替代 `--scan-dir` 参数。

### 单元测试

`test/` 包含各模块的 pytest 测试套件（`test_config.py`、`test_gmtsar_s1.py`、`test_utils_config_io.py` 等），通过 `pytest test/` 运行。`e2e/` 下的脚本（`cli_e2e_*.sh`、`api_e2e_*.py`）**不会**被 pytest 自动运行——它们驱动真实的 ASF 搜索/下载/处理，仅在需要完整端到端运行时直接调用。

## 代码风格

- 不写解释代码*做什么*的注释——只写解释*为什么*的注释（隐藏约束、绕过特定 Bug、微妙的不变量）。
- 不为不可能发生的场景添加错误处理。
- 所有工作目录子路径使用 `Hyp3Paths` / `ISCEPaths` / `MintPyPaths`。
- 优先修改已有文件，而非创建新的抽象层。
