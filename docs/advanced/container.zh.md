# 容器运行

本地处理器与分析器（ISCE2、GMTSAR、ISCE3、MintPy）都各自依赖庞大的 SAR 软件。与其在主机上安装这些依赖，不如将 `container`（命令行 `--container`，或配置字段 / GUI 的 "Run in Container" 复选框）设为一个 `.sif`/Apptainer 镜像或 Docker 镜像引用，InSARHub 便会在该镜像**内部**运行整个流程。

## 重新调用，而非库调用

主机进程**不会** import ISCE2/GMTSAR/dolphin。当设置了 `container` 时，它会在镜像内部重新调用**同一条 `insarhub` 命令**（处理器用 `_reinvoke_via_container`，分析器用 `_run_via_container`），大致相当于：

```
docker run --rm -e INSARHUB_CONTAINER_CHILD=1 -v <workdir>:<workdir> <image> \
    insarhub <processor|analyzer> ... <same args>
```

工作目录以**完全相同的路径**挂载进容器，因此子进程写出的每个结果文件都会落在主机上、与本机原生运行时完全一致——主机和容器对所有路径的理解是一致的。

## 为何不会嵌套（`INSARHUB_CONTAINER_CHILD`）

子进程会被打上环境变量 `INSARHUB_CONTAINER_CHILD=1`。每一处决定是否容器化的代码都会**同时**检查 `config.container` **以及**该变量是否缺失，于是：

- **主机**（设置了 `container`，变量未设）→ 启动容器（仅一次），并且
- **子进程**（变量已设）→ 即便其配置中 `container` 仍然存在，也**直接**执行真正的工作。

如果没有这项检查，子进程——它在持久化配置中仍能看到 `container`——就会试图再启动一个 `docker run`（docker-in-docker），但镜像内部并没有 docker；这正是经典的 *"docker not found"* 报错。同一处守卫还会让子进程在进程内**同步**运行（不 fork+detach），从而避免 `docker run --rm` 在流程结束前就把容器拆掉。

## 主机与镜像的要求

运行命令行/应用的**主机**需要在 `PATH` 上有容器运行时（`docker`，或用于 `.sif` 的 `apptainer`/`singularity`）。**镜像本身**则不需要——它只需要在 SAR 软件栈之外安装了 `insarhub`。`docker/Dockerfile.*` 构建了官方镜像：

| 镜像 | 软件栈 |
|---|---|
| `insarhub-isce2-mintpy` | ISCE2 `stackSentinel` + MintPy |
| `insarhub-gmtsar-mintpy` | GMTSAR + MintPy |
| `insarhub-isce3-dolphin` | ISCE3 / COMPASS + dolphin |
| `insarhub-base` | InSARHub + MintPy（HyP3 分析器） |

## 持久化

你选择的 `container` **会**被写入工作目录的 `insarhub_config.json`，因此后续的 `retry`/`refresh`/`cancel` 无需再次传入即可进入同一镜像。不带取值的 `--container` 会解析为处理器/分析器的 `container_default`——这是每个后端固定的建议镜像（GUI "Run in Container" 复选框预填的镜像），它本身**永远不会**被持久化；只有你实际选择的 `container` 才会被保存。

## 用法

=== "命令行"

    每次调用都传入 `--container <路径或镜像>`。在命令行上它是逐次调用的开关（类似 `--dry-run`），所以在 `submit`/`refresh`/`retry`/`watch`/`cancel` 时都要重复传入：

    ```bash
    insarhub processor submit  -N ISCE2_S1 -w /data/p100_f466 \
        --bbox 33.0 38.0 -120.0 -115.0 \
        --container ghcr.io/jldz9/insarhub-isce2-mintpy:dev
    insarhub processor refresh -N ISCE2_S1 -w /data/p100_f466 \
        --container ghcr.io/jldz9/insarhub-isce2-mintpy:dev
    ```

    分析器在 `run` 动作**之后**接受 `--container`；不带取值的 `--container` 使用默认镜像：

    ```bash
    insarhub analyzer -N ISCE2_Mintpy_SBAS -w /data/p100_f466 run --container
    ```

=== "Python API"

    在配置上设置 `container`；`submit()` / `run()` 便会在其内部重新调用：

    ```python
    cfg = ISCE2_S1_Config(
        workdir='/data/p100_f466',
        bbox=[37.74, 38.00, -113.05, -112.68],
        container='ghcr.io/jldz9/insarhub-isce2-mintpy:dev',
    )
    processor = Processor.create('ISCE2_S1', pairs=pairs, config=cfg)
    processor.submit()   # 在容器内部运行
    ```

## HPC 模式

当 `container` 与 `hpc_mode` 同时设置时，只有每个**阶段的子作业**在容器内运行；sbatch 管理器的调度框架（提交/轮询循环、链式提交）仍留在主机上。参见 [HPC (SLURM)](hpc.md)。
