# 处理器

通过搜索面板中的**添加任务**添加任务后，任务文件夹会出现在**任务**抽屉中。点击工具栏右上角的**任务**按钮打开抽屉。

<!-- screenshot: jobs button in toolbar -->
![任务按钮](fig/jobs_button_light.png#only-light){: .doc-img}
![任务按钮](fig/jobs_button_dark.png#only-dark){: .doc-img}
/// caption
工具栏右上角的**任务**按钮打开任务文件夹抽屉。
///


---

然后点击任务文件夹上的下载器标签（例如 **S1_SLC**）打开其详情面板。

<!-- screenshot: clicking downloader tag on job folder -->
![下载器标签](fig/downloader_tag_light.png#only-light){: .doc-img style="width: 60%"}
![下载器标签](fig/downloader_tag_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
点击任务文件夹上的下载器标签打开其详情面板。
///

## 选择配对

构建精心设计的干涉配对网络是 InSAR 时序分析的关键步骤。合理选择的 SBAS 网络在时间和垂直基线约束之间取得平衡，在确保整个场景堆叠时间连通性的同时最大化相干性。

点击**编辑网络**打开交互式基线-时间图编辑器。

![编辑网络](fig/processor_edit_network_light.png#only-light){: .doc-img style="width: 50%"}
![编辑网络](fig/processor_edit_network_dark.png#only-dark){: .doc-img style="width: 50%"}
/// caption
选择编辑网络打开网络修改窗口
///

网络图为交互式。**拖动**从一个场景节点到另一个节点创建新配对。**点击**现有边从网络中删除它。**悬停**在任意边上查看其时间基线、垂直基线和质量评分。

![网络图](fig/network_modify_light.gif#only-light){: .doc-img }
![网络图](fig/network_modify_dark.gif#only-dark){: .doc-img }
/// caption
基线-时间图显示干涉图网络。点击任意边切换，或在节点间拖动添加新配对。
///

边的颜色反映预计算的配对质量评分 — 绿色边质量高，黄色中等，红色较差。
**悬停**在任意边上查看其时间基线、垂直基线和质量评分。

网络编辑器支持两种工作流：

**手动编辑** — **点击**任意边（干涉图配对）切换激活或删除状态。**拖动**从一个场景节点到另一个节点创建新配对。点击**保存**将更新的配对列表持久化到任务文件夹。

**自动配对选择** — 点击 **⚙ 参数**从场景堆叠自动生成网络：

| 参数 | 说明 |
|-----------|-------------|
| **目标时间基线** | 以逗号分隔的目标时间间隔（天），用于配对 |
| **容差** | 与各目标基线的允许偏差（天） |
| **最大时间基线** | 时间基线的硬性上限（天） |
| **最大垂直基线** | 垂直基线的硬性上限（米） |
| **最小连接数** | 每个场景必须参与的最少干涉图数量 |
| **最大连接数** | 每个场景的最大干涉图数量 |
| **强制连通网络** | 添加额外配对以确保无孤立节点 |

**查看配对** — 列出所有选定的干涉图配对及其时间和垂直基线值。

<!-- screenshot: view pairs -->
![查看配对](fig/view_pairs_light.png#only-light){: .doc-img style="width: 60%"}
![查看配对](fig/view_pairs_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
选定干涉图配对列表，包含基线信息。
///

---

## 衰减图

点击**衰减图**打开相干性衰减图抽屉。这将在主地图上叠加季节性 S1 全球相干性图，在提交任务前快速了解研究区域的预期相干性。

![衰减图按钮](fig/decay_maps_button_light.png#only-light){: .doc-img style="width: 60%"}
![衰减图按钮](fig/decay_maps_button_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
下载器任务面板中的衰减图按钮。
///

每个可用季节和极化方式均有列出。点击三个波段按钮之一在地图上叠加显示：

| 波段 | 符号 | 显示内容 |
|------|--------|---------------|
| **1** | γ∞ PS 基底 | 永久散射体相干性基底 — 无论时间间隔多长都会持续的最低相干性 |
| **2** | γ0 初始相干性 | 获取时的初始相干性 — 值越高表示短基线相干性越好 |
| **3** | τ 衰减 | 去相干时间常数（天） — 值越大表示相干性持续越久 |

![衰减图叠加](fig/decay_maps_overlay_light.png#only-light){: .doc-img}
![衰减图叠加](fig/decay_maps_overlay_dark.png#only-dark){: .doc-img}
/// caption
叠加在底图上的相干性衰减图。悬停在地图上读取像素值。
///

再次点击同一按钮隐藏叠加层。点击不同波段切换图层。

---

## 查看数据

干涉图下载完成后，点击处理器面板中的**查看数据**打开数据浏览器。这列出了从下载的 `.zip` 压缩包中提取的所有 HyP3 产品文件，并可直接在地图上叠加显示。

![查看数据按钮](fig/view_data_button_light.png#only-light){: .doc-img style="width: 60%"}
![查看数据按钮](fig/view_data_button_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
处理器面板中的查看数据按钮。
///

每个干涉图配对列出其可用的产品文件：

| 类型 | 说明 |
|------|-------------|
| `unw_phase` | 解缠干涉相位 |
| `corr` | 干涉图相干性 |
| `dem` | 处理中使用的数字高程模型 |
| `lv_theta` | 视线向仰角 |
| `lv_phi` | 视线向方位角 |
| `water_mask` | 水体掩模 |

点击任意文件将其渲染为地图上的栅格叠加层。再次点击隐藏。

![查看数据叠加](fig/view_data_overlay_light.png#only-light){: .doc-img}
![查看数据叠加](fig/view_data_overlay_dark.png#only-dark){: .doc-img}
/// caption
叠加在底图上的 HyP3 干涉图产品。
///

---

## 提交任务

配对网络审核满意后，点击**处理**打开处理器选择对话框。

<!-- screenshot: click process button -->
![处理按钮](fig/process_button_light.png#only-light){: .doc-img style="width: 60%"}
![处理按钮](fig/process_button_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
点击**处理**将干涉图配对提交至 HyP3。
///

<!-- screenshot: processor selection dialog -->
![处理器选择](fig/processor_dialog_light.png#only-light){: .doc-img style="width: 60%"}
![处理器选择](fig/processor_dialog_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
处理器选择对话框。选择处理器（例如 `Hyp3_S1`）并确认以将所有配对提交至 HyP3。
///

!!! tip "提交前先测试"
    对于向外部服务器提交任务，在处理器对话框中勾选**试运行**，可在不提交真实任务的情况下验证环境和凭据。成功的试运行输出类似于：

    ```
    [Dry run] Would submit 65 pairs via Hyp3_S1 from p93_f121
    ```

    建议在首次提交前执行此操作以确保一切配置正确。

有关所有处理器参数和选项的完整说明，请参阅[处理器参考](../advanced/processor.md)。

任务成功提交后，任务文件夹面板中会出现带有处理器名称的**处理器**标签，表示该堆叠的 HyP3 处理已激活。

<!-- screenshot: processor tab appears -->
![处理器标签](fig/processor_tab_light.png#only-light){: .doc-img style="width: 60%"}
![处理器标签](fig/processor_tab_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
任务成功提交后，任务文件夹面板中出现**处理器**标签。
///

---

## 监控任务

任务提交后，任务文件会自动保存到任务文件夹，下次打开处理器面板时默认加载。即使关闭应用程序后也可恢复监控。

处理器面板顶部的下拉菜单列出了任务文件夹下找到的所有任务文件，包括初始提交文件（`hyp3_jobs.json`）和后续**重试**操作生成的重试文件。从列表中选择不同文件可检查或监控特定提交。

点击**刷新**检查 HyP3 所有已提交任务的最新状态。每个任务显示以下状态之一：

| 状态 | 含义 |
|--------|---------|
| `RUNNING` | 任务正在 HyP3 上积极处理 |
| `SUCCEEDED` | 处理成功完成 |
| `FAILED` | 处理失败 |

<!-- screenshot: job status list -->
![任务状态](fig/processor_status_light.png#only-light){: .doc-img style="width: 80%"}
![任务状态](fig/processor_status_dark.png#only-dark){: .doc-img style="width: 80%"}
/// caption
处理器任务面板
///

如有任务显示 `FAILED`，点击**重试**重新提交。任务显示 `SUCCEEDED` 后，点击**下载**将处理好的干涉图获取到工作目录。

---

## 其他操作

| 按钮 | 说明 |
|--------|-------------|
| **重试** | 将所有失败任务重新提交至 HyP3 |
| **下载** | 将所有成功的干涉图下载到工作目录 |
| **监控** | 持续轮询 HyP3 直到所有任务完成，然后自动下载 |
| **积分** | 查看剩余的 HyP3 处理积分 |

---

所有任务成功且干涉图下载完成后，前往分析器面板运行 InSAR 时序分析。

[分析器](analyzer.md){.md-button}
