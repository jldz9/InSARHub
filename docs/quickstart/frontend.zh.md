# Web UI

InSARHub 内置了一个 Web 界面，让您无需编写 Python 脚本即可在浏览器中完成完整的 InSAR 工作流。

## 启动

安装 InSARHub 后，使用以下命令启动 Web 服务器：

```bash
insarhub-app
```

然后在浏览器中打开 **[http://127.0.0.1:8080](http://127.0.0.1:8080)**。

选项：

```bash
insarhub-app -w /data/bryce    # 设置工作目录
insarhub-app --host 0.0.0.0   # 向本地网络开放
insarhub-app --port 9090       # 更改端口
insarhub-app --version         # 打印版本并退出
```

`-w` / `--workdir` 参数预设工作目录，无需在启动后手动配置。若省略，则使用运行 `insarhub-app` 时所在的目录。

## 在 HPC 上运行

InSARHub 后端使用FastAPI, 所以您可以使用端口转发来在HPC上运行后端而使用本地管理前端

**步骤：**

1. 在 VS Code 中安装 [Remote - SSH](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-ssh) 扩展
2. 连接到 HPC 登录节点：**Connect to Host → + → `user@hpc.example.edu`**
3. 在 VS Code 终端（现在运行于 HPC 上）启动 InSARHub：
   ```bash
   insarhub-app -w /your/hpc/workdir
   ```
4. VS Code 检测到开放端口后会提示转发 — 点击**在浏览器中打开**，或在 **端口** 标签页手动添加端口 `8080`
5. 在本地浏览器打开 **[http://127.0.0.1:8080](http://127.0.0.1:8080)**

UI 在本地浏览器中运行，所有数据、处理和文件 I/O 均在 HPC 节点上执行。

!!! tip "计算节点 vs 登录节点"
    在登录节点启动 `insarhub-app` 时，建议在处理器和分析器面板中启用 **HPC 模式**，将处理步骤以 `sbatch` 任务提交至集群。否则在登录节点上直接运行大量计算可能导致管理员封禁账号。

---

## 界面概览

<!-- screenshot: full app overview -->
![Web UI 概览](../frontend/fig/overview_light.png#only-light){: .doc-img-wide}
![Web UI 概览](../frontend/fig/overview_dark.png#only-dark){: .doc-img-wide}
/// caption
InSARHub Web UI — 地图、工具栏和任务面板。
///

---

## 顶部工具栏

顶部工具栏包含主要搜索控件：

| 控件 | 说明 |
|---------|-------------|
| **开始 / 结束日期** | SAR 场景搜索的日期范围 |
| **搜索** | 对当前 AOI 执行 ASF 场景搜索 |
| **设置** | 打开全局设置面板 |
| **任务** | 打开任务文件夹抽屉 |
| **主题** | 切换深色 / 浅色模式 |

---

## 绘制研究区域（AOI）

点击地图左侧的绘图工具：

| 工具 | 行为 |
|------|----------|
| ⬜ **矩形框** | 第一次点击设置第一个角，移动鼠标预览，再次点击完成 |
| ⬡ **多边形** | 点击添加顶点，双击关闭 |
| 📍 **点** | 点击放置一个点 |
| 📂 **Shapefile** | 上传 `.zip` shapefile |

再次点击当前工具可取消绘制。

## 地图导航

| 操作 | 方式 |
|--------|-----|
| **平移** | 右键点击并拖动 |
| **缩放** | 滚轮或使用 +/− 按钮 |
| **点击覆盖范围** | 左键点击查看场景详情 |

---

## 搜索与结果

1. 在地图上绘制 AOI
2. 在顶部工具栏设置日期范围
3. 点击**搜索** — 场景覆盖范围以彩色轮廓显示在地图上
4. 点击任意轮廓查看场景详情（轨道、帧号、日期、极化方式）

---

## 设置

点击 ⚙ 设置按钮进行配置：

- **常规** — 工作目录、下载线程数
- **认证** — Earthdata 和 CDSE 凭据
- **下载器** — 下载器类型和参数
- **处理器** — 处理器类型和参数
- **分析器** — 各分析器配置（每种分析器类型独立存储设置）

---

## 任务文件夹

点击**任务**打开任务文件夹抽屉。InSARHub 扫描工作目录并列出所有包含已识别工作流文件的子文件夹。

每个文件夹显示可点击的角色标签：

| 标签 | 含义 |
|-----|---------------|
| **下载器** | 文件夹含 `downloader_config.json` |
| **处理器** | 文件夹含 `hyp3_jobs.json` |
| **分析器** | 文件夹含 `mintpy.cfg` |

点击标签打开对应角色的面板。点击 🗑 删除整个任务文件夹。

---

## 后续步骤

各面板的详细使用说明，请参阅：

[搜索与下载](../frontend/search.md){.md-button}
[处理器](../frontend/processor.md){.md-button}
[分析器](../frontend/analyzer.md){.md-button}
[结果查看器](../frontend/results.md){.md-button}
