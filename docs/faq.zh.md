# 常见问题

以下是反复出现的问题，多数来自 [issue 列表](https://github.com/jldz9/InSARHub/issues)。
点击问题即可展开答案。

## 安装

??? question "`pip install -e .` 到底装了什么？"

    **末尾的点不能省略** —— 它表示“当前目录下的包”
    （[#2](https://github.com/jldz9/InSARHub/issues/2)）。请先激活环境，并在仓库根目录执行：

    ```bash
    conda activate insarhub
    cd InSARHub
    pip install -e .
    ```

    `-e` 表示*可编辑*安装：环境直接指向你的源码目录，改代码后无需重新安装即可生效。
    只有在开发 InSARHub 本身时才需要它；普通使用请按[安装](quickstart/install.md)从
    conda-forge 安装。

??? question "支持哪些卫星？"

    目前支持 Sentinel-1 与 NISAR（[#1](https://github.com/jldz9/InSARHub/issues/1)）：

    | 数据产品 | 搜索与下载 | 处理 |
    |---|---|---|
    | Sentinel-1 SLC | 支持 | HyP3、ISCE2、GMTSAR |
    | Sentinel-1 Burst | 支持 | ISCE3 / COMPASS + dolphin |
    | NISAR GSLC | 支持 | ISCE3 + dolphin |
    | NISAR RSLC、GUNW | 支持 | 暂无 —— 尚无处理器可消费 |

    NISAR RSLC 与 GUNW 已在 Web 界面中隐藏，正是出于这个原因：下载量很大，下载完却无法
    处理。命令行与 Python API 仍可正常使用。

    ALOS、ERS 等其他平台暂不支持。

## Web 界面

??? question "`insarhub-app` 打印的链接打不开"

    `insarhub-app` 会输出类似 `http://127.0.0.1:8080` 的地址，但浏览器打开后是连接失败、
    空白页，或是别的程序的界面。

    **8080** 是非常常用的默认端口，多半已被机器上的其他程序占用。换一个端口启动：

    ```bash
    insarhub-app --port 12345
    ```

    然后打开程序**新输出**的地址，而不是原来那个。任何空闲端口都可以。

    还可以检查这些：

    - **打开它实际打印的地址。** 使用 `--host 0.0.0.0` 时其他机器也能访问，但你要输入的
      仍然是该主机自己的地址。
    - **远程或 HPC 环境。** 如果 `insarhub-app` 运行在远程机器上，需要先做端口转发：
      `ssh -L 12345:127.0.0.1:12345 user@host`，然后在本机打开
      `http://127.0.0.1:12345`。
    - **是空白页而不是连接失败。** 说明服务已启动，但前端资源缺失 —— 请重新安装
      InSARHub，参见[安装](quickstart/install.md)。

## 搜索与下载

??? question "搜索能找到影像，下载却报 “Search does not return any result”"

    **0.4.0 已修复**（[#7](https://github.com/jldz9/InSARHub/issues/7)）。请升级：

    ```bash
    conda install -c conda-forge insarhub
    ```

    `asf_search` 13.0.0 改变了判断 frame 字段的逻辑，其 `should_use_asf_frame()` 不再识别
    通用的 `platform=SENTINEL-1` 查询，导致 frame 过滤条件被静默地当作 **ESA** frame 号去
    检索，从而匹配不到任何结果 —— 这正是“搜索正常、下载为空”的原因。InSARHub 现在显式
    使用 `asfFrame`，在 `asf_search` 12.x 和 13.x 上行为一致。

    若你此前固定了 `asf_search=12.3.2` 作为临时方案，现在可以取消该约束。

## 处理

??? question "ISCE2 处理中途停止，终端进程直接消失"

    有两种完全不同的原因，但表现一样。

    **固定在某一步静默崩溃**是 0.3.x 的缺陷
    （[#6](https://github.com/jldz9/InSARHub/issues/6)）：`_fix_cmd` 未能去掉 ISCE2 写入
    `run_files` 每一行末尾的 `&`，命令因而立即以“成功”返回，真正的工作却被遗弃。
    **0.4.0 已修复。**

    **停止的步骤不固定、且多发生在大数据栈上，通常是内核的 OOM killer。**
    InSARHub 会并行执行 `run_files` 的各行，而 ISCE2 的若干步骤非常吃内存，总量可能超出
    物理内存。降低并发：

    ```bash
    insarhub processor -N ISCE2_S1 -w /path/to/workdir submit --max_workers 2
    ```

    `--max_workers` 控制除 `topo`（`run_01`）以外的所有步骤；`topo` 只有一条命令，需用
    `--num_proc4topo`。详见[处理器](advanced/processor.md)。

    要区分这两种情况，用 `INSARHUB_DEBUG=1` 运行，并查看工作目录下的 `executor.log`。
    被 OOM 杀死时该步骤会停留在 `RUNNING` 且没有报错，`dmesg | grep -i "killed process"`
    可以看到被杀的进程。

??? question "`--select-pair` 提示不会生成配对网络图"

    对 **S1_Burst** 和 **NISAR** 产品来说这是预期行为。ASF 不为它们提供垂直基线，因此无法
    绘制“基线—时间”网络图。

    配对本身仍会生成，处理也不受影响：ISCE3 处理器通过 dolphin 的相位链接自行构建干涉
    网络，依据的是时间与索引约束，而非垂直基线阈值。参见
    [配对质量评分](advanced/pair_quality.md)。

## 日志

??? question "如何查看 InSARHub 正在做什么？"

    默认情况下它很安静 —— 只输出命令本身的结果，以及警告和错误。设置 `INSARHUB_DEBUG=1`
    可以打开完整日志：

    ```bash
    INSARHUB_DEBUG=1 insarhub processor -N ISCE2_S1 -w /path/to/workdir submit
    ```

    命令行、`insarhub-app` 和 `import insarhub` 都适用。详见
    [日志](advanced/cli_reference.md#logging)。
