=== "默认"

    ??? note "创建新环境（推荐）"

        ```bash
        conda create -n insarhub python=3.12
        conda activate insarhub
        ```

    !!! note "Windows：请使用 Python 3.11"
        在 Windows 上目前仅支持 Python 3.11 —— 创建上面的环境时请使用 `python=3.11`。Linux 和 macOS 同时支持 3.11 和 3.12。

    ```bash
    conda install insarhub -c conda-forge
    ```

    或使用 pip（需先通过 conda 安装 GDAL）：

    ```bash
    conda install gdal
    pip install insarhub
    ```

=== "ISCE2 处理器"

    通过 ISCE2 `stackSentinel` 添加本地干涉图处理。

    !!! note "平台支持"
        ISCE2 仅支持 Linux 和 macOS（x86_64）。Windows 与 Apple Silicon 原生不支持 —— 请使用 WSL2 或 Linux HPC 集群。

    先安装 InSARHub，再将 ISCE2 添加到同一环境：

    ```bash
    conda install insarhub -c conda-forge
    conda install isce2 -c conda-forge
    ```

    使用 pip：

    ```bash

    conda install gdal isce2
    pip install insarhub
    ```

    验证 ISCE2 是否安装正确：

    ```bash
    python -c "import isce; print(isce.__version__)"
    ```

=== "ISCE3 + Dolphin 处理器"

    通过 ISCE3 / COMPASS（地理编码 CSLC）与 dolphin（相位链接 + 时序）添加本地 burst 处理。

    !!! note "平台支持"
        ISCE3 / COMPASS / dolphin 工具链仅支持 Linux 和 macOS（x86_64）—— 其他平台请使用 WSL2 或 Linux HPC 集群。

    !!! note "受限的 numpy 版本"
        COMPASS 锁定 `numpy<2`，因此整个工具链可在一次 conda 求解中解析完成（isce3、dolphin、gdal 都保持在 numpy 1.26）。`isce3` 固定为 CPU 版本，InSARHub 将来会测试 GPU 功能并支持 GPU 处理。

    ```bash
    conda create -n isce3_dolphin python=3.12
    conda activate isce3_dolphin
    conda install -c conda-forge insarhub "isce3=*=*cpu*" compass sardem dolphin snaphu burst2safe gdal
    ```

    验证工具链可正常导入：

    ```bash
    python -c "import isce3, compass, dolphin; print('ok')"
    ```

=== "GMTSAR 处理器"

    通过 GMTSAR 添加本地干涉图处理，并配合 MintPy 时序分析。
    !!! note "平台支持"
        `--system conda-linux-full` **仅支持 Linux x86_64** —— 其他平台请使用 WSL2 或 Linux HPC 集群。

    ```bash
    # 1. 克隆完整的 GMTSAR 仓库并编译（会创建名为 "gmtsar" 的 conda 环境）
    git clone https://github.com/gmtsar/gmtsar.git
    cd gmtsar
    python3 gmtsar/python/install.py --system conda-linux-full

    # 2. 将 InSARHub + MintPy 添加到同一环境
    conda activate gmtsar
    conda install -c conda-forge insarhub mintpy

    # 3. 将 GMTSAR 的可执行文件加入 PATH（写入 shell 配置以持久化）
    export GMTSAR=$(pwd)
    export PATH=$GMTSAR/bin:$PATH
    ```

    验证：

    ```bash
    which p2p_processing && gmt --version
    python -c "import insarhub, mintpy; print('ok')"
    ```

---

### 在容器中运行

InSARHub 通过 **Docker** 支持容器，安装请参见[官方指南](https://docs.docker.com/get-started/get-docker/)。

你可以选择完全在容器中运行，或在本地安装基础版 `InSARHub`，再通过 `--container` 运行各个处理器/分析器。

InSARHub 目前支持：

=== "Hyp3 + Mintpy"
    默认的 InSARHub 容器，支持通过 HyP3 进行 Sentinel-1 处理，并通过 MintPy 进行时序分析。

    ```bash
    ghcr.io/jldz9/insarhub-base:dev

    ```

=== "ISCE2 + MintPy"

    涵盖 `ISCE2_S1` 处理器与 `ISCE2_Mintpy_SBAS` 分析器。

    ```bash
    ghcr.io/jldz9/insarhub-isce2-mintpy:dev
    ```

=== "ISCE3 + Dolphin"

    涵盖 `ISCE3_Burst`（Sentinel-1 burst）与 `ISCE3_NISAR`（NISAR GSLC），以及 `ISCE3_Dolphin_PL` 分析器。

    ```bash
    ghcr.io/jldz9/insarhub-isce3-dolphin:dev

    ```

=== "GMTSAR + Mintpy"

    涵盖 `GMTSAR_S1` 处理器与 GMTSAR 分析器。

    ```bash
    ghcr.io/jldz9/insarhub-gmtsar-mintpy:dev
    ```
---

### 开发环境配置

=== "默认"

    ```bash
    git clone https://github.com/jldz9/InSARHub.git
    cd InSARHub
    conda env create -f environment.yml -n insar_dev
    conda activate insar_dev
    pip install -e .
    ```

    !!! note "Windows：请使用 Python 3.11"
        `environment.yml` 允许 Python 3.11 或 3.12，但 Windows 上目前仅支持 3.11。如果求解选中了 3.12，请在运行 `conda env create` 前把 `environment.yml` 中的 `python` 行改为 `python=3.11`。

=== "ISCE2 处理器"

    ```bash
    git clone https://github.com/jldz9/InSARHub.git
    cd InSARHub
    conda env create -f environment.yml -n insar_dev
    conda activate insar_dev
    conda install -c conda-forge "numpy<2.0" isce2
    pip install -e .
    ```

=== "ISCE3 + Dolphin 处理器"

    ```bash
    git clone https://github.com/jldz9/InSARHub.git
    cd InSARHub
    conda create -n isce3_dolphin python=3.12
    conda activate isce3_dolphin
    conda install -c conda-forge "isce3=*=*cpu*" compass sardem dolphin snaphu burst2safe gdal
    pip install -e .
    ```

=== "GMTSAR 处理器"

    ```bash
    # 从源码编译 GMTSAR 到名为 "gmtsar" 的 conda 环境
    git clone https://github.com/gmtsar/gmtsar.git
    cd gmtsar
    python3 gmtsar/python/install.py --system conda-linux-full
    export GMTSAR=$(pwd) && export PATH=$GMTSAR/bin:$PATH

    # 将 MintPy + InSARHub（可编辑）添加到该环境
    conda activate gmtsar
    conda install -c conda-forge mintpy
    git clone https://github.com/jldz9/InSARHub.git
    cd InSARHub
    pip install -e .
    ```

??? note "使用 mamba 加速依赖解析"

    如果你已安装 [mamba](https://mamba.readthedocs.io/en/latest/installation/mamba-installation.html)，可将上述任意命令中的 `conda` 替换为 `mamba`。
