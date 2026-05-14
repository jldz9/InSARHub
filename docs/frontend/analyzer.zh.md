# 分析器

处理器完成所有干涉图处理后，分析器面板将对生成的产品进行时序分析。

## 初始化分析器

所有提交的任务完成并在处理器面板中显示 `SUCCEEDED` 后，打开同一任务文件夹中的**运行分析器**选项卡。从下拉菜单中选择分析器类型（例如 `Hyp3_SBAS`），然后点击**初始化**以初始化分析器工作区。这将准备运行时序分析所需的配置和目录结构。

<!-- screenshot: analyzer panel overview -->
![分析器面板](fig/analyzer_light.png#only-light){: .doc-img}
![分析器面板](fig/analyzer_dark.png#only-dark){: .doc-img}
/// caption
运行分析器选项卡 — 选择分析器类型并点击初始化开始。
///

初始化完成后，任务文件夹上会出现带有所选分析器名称（例如 `Hyp3_SBAS`）的**分析器**标签。点击该标签打开分析器面板，继续配置和处理。

<!-- screenshot: analyzer tag on job folder -->
![分析器标签](fig/analyzer_tag_light.png#only-light){: .doc-img style="width: 60%"}
![分析器标签](fig/analyzer_tag_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
初始化后任务文件夹上出现分析器标签。点击打开分析器面板。
///

---

## 配置

进入分析器面板后，可选择要运行的时序分析步骤。点击**更改配置**切换到配置选项卡，每种分析器类型（例如 `Hyp3_SBAS`）都有独立保存的设置。

有关所有分析器参数和选项的完整说明，请参阅[分析器参考](../advanced/analyzer.md)。

<!-- screenshot: analyzer config panel -->
![分析器配置](fig/analyzer_config_light.png#only-light){: .doc-img style="width: 60%"}
![分析器配置](fig/analyzer_config_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
分析器选项卡。
///

---

## 运行步骤

选择要运行的步骤并点击**运行**。步骤按顺序执行，进度显示在日志中。

<!-- screenshot: analyzer running with log output -->
![分析器运行中](fig/analyzer_running_light.png#only-light){: .doc-img style="width: 60%"}
![分析器运行中](fig/analyzer_running_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
分析器正在运行所有步骤。
///

## 编辑网络

初始化分析器并至少运行 `load_data` 步骤后，分析器面板中会出现**编辑网络**按钮。点击打开网络编辑器，显示当前加载到 MintPy 中的干涉图网络，每条边叠加了相干性值。

![编辑网络按钮](fig/analyzer_edit_network_button_light.png#only-light){: .doc-img style="width: 60%"}
![编辑网络按钮](fig/analyzer_edit_network_button_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
load_data 完成后，编辑网络按钮出现在分析器面板中。
///

与配对选择阶段不同，此处无法拖动创建新配对 — 干涉图已处理完成，只有已下载的配对可用。您可以：

- **点击活动边** 从网络中删除该配对
- **点击已删除的边** 重新添加
- **⚙ 参数** — 配置 MintPy `modify_network` 约束条件，点击**运行 modify_network** 让 MintPy 自动筛选网络

![编辑网络图](fig/analyzer_edit_network_graph_light.png#only-light){: .doc-img}
![编辑网络图](fig/analyzer_edit_network_graph_dark.png#only-dark){: .doc-img}
/// caption
网络编辑器显示每条边的相干性值。点击边可删除或重新添加。
///

| 参数 | 说明 |
|-----------|-------------|
| **最大时间基线** | 删除超过此时间间隔的配对（天） |
| **最大垂直基线** | 删除超过此垂直基线的配对（米） |
| **开始日期** | 排除此日期之前的获取（YYYYMMDD） |
| **结束日期** | 排除此日期之后的获取（YYYYMMDD） |
| **排除日期** | 以空格分隔的单个日期列表（YYYYMMDD） |
| **基于相干性** | 启用基于相干性的网络修改（`yes` / `no` / `auto`） |
| **最小相干性** | 保留配对的最小平均相干性阈值 |
| **保留最小生成树** | 删除低相干性配对时保留最小生成树 |

![modify_network 参数](fig/analyzer_edit_network_parameter_light.png#only-light){: .doc-img style="width: 60%"}
![modify_network 参数](fig/analyzer_edit_network_parameter_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
modify_network 配置的 ⚙ 参数对话框。
///

运行后，图形刷新以反映更新后的网络。然后可以继续在修改后的网络上运行后续分析步骤。

---

## 概览

运行 `reference_point` 和 `quick_overview` 步骤后，分析器面板中会出现**概览**按钮。点击打开概览抽屉，可将 MintPy 诊断图层直接绘制在地图上。

![概览浅色](fig/analyzer_overview_light.png#only-light){: .doc-img style="width: 60%"}
![概览深色](fig/analyzer_overview_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
分析器面板中的概览按钮和诊断图层控件。
///

概览抽屉提供四个诊断图层：

| 按钮 | 文件 | 显示内容 |
|--------|------|---------------|
| 平均空间相干性 | `avgSpatialCoh.h5` | 所有干涉图的逐像素平均相干性 — 高值表示可靠像素 |
| 平均相位速度 | `avgPhaseVelocity.h5` | 平均相位变化率 — 快速检查相干形变模式 |
| 解缠误差计数 | `numTriNonzeroIntAmbiguity.h5` | 非零整数模糊度的三角闭合数 — 高计数表示解缠不可靠 |
| 连通分量掩模 | `maskConnComp.h5` | 属于连通解缠分量的像素 — 孤立像素被掩蔽 |

点击任意按钮将该图层叠加在地图上。再次点击隐藏。悬停在地图上可在色标中读取像素值。

<!-- insert picture: overview drawer with diagnostic overlay on map -->

---

## 查看结果

`velocity` 和 `geocode` 步骤成功完成后，分析器面板中会出现**查看结果**按钮。点击打开结果查看器，将计算得到的速度图叠加在主地图上。

点击速度叠加层上的任意点，提取并显示该位置的位移时序。

<!-- screenshot: view results panel -->
![查看结果](fig/results_light.png#only-light){: .doc-img style="width: 60%"}
![查看结果](fig/results_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
查看结果按钮
///

有关结果查看器的更多详情，请参阅[结果查看器](results.md)页面。

---

## 清理

点击**清理**在分析后释放磁盘空间。这将删除临时工作目录（`tmp/` 和 `clip/`）以及处理过程中提取的任务文件夹中的所有 `.zip` 压缩包。MintPy 输出和配置文件将被保留。

<!-- screenshot: cleanup confirmation -->
![清理](fig/analyzer_cleanup_light.png#only-light){: .doc-img style="width: 60%"}
![清理](fig/analyzer_cleanup_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
清理从工作目录中删除中间 HDF5 文件。
///
