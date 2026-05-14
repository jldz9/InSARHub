# 搜索与下载

## 设置

默认情况下，工作目录为运行 `insarhub-app` 时所在的目录，用户可在设置中指定工作目录。

<!-- screenshot: settings panel open -->
![设置面板](fig/settings_light.png#only-light){: .doc-img}
![设置面板](fig/settings_dark.png#only-dark){: .doc-img}
/// caption
设置面板，显示工作目录和 API 配置。
///

---

## 搜索场景

1. 在地图上绘制 AOI
2. 设置日期范围并选择下载器（默认为 `S1_SLC`）
3. 点击**搜索**

<!-- screenshot: search panel with results -->
![搜索面板](fig/search_light.png#only-light){: .doc-img}
![搜索面板](fig/search_dark.png#only-dark){: .doc-img}
/// caption
搜索面板，显示可用的 Sentinel-1 堆叠。
///

搜索结果以覆盖范围轮廓显示在地图上。点击任意轮廓打开**场景详情**面板，显示获取元数据，包括平台、轨道、波束模式、极化方式、文件大小和该场景的下载选项。

在场景详情面板中点击 **▸ 查看详情** 展开任务抽屉，显示堆叠中所有单独场景的完整列表。点击 **◂ 隐藏详情** 收起。

<!-- screenshot: search results footprints on map -->
![搜索结果](fig/search_results_light.png#only-light){: .doc-img style="width: 60%"}
![搜索结果](fig/search_results_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
地图上显示的搜索结果覆盖范围。点击轮廓查看场景详情。
///

---

## 下载

点击**下载堆叠**开始将所选堆叠的场景下载至工作目录。下载过程中实时显示进度，可随时停止。

有关所有下载器参数和选项的完整说明，请参阅[下载器参考](../advanced/downloader.md)。

## 下载轨道文件

点击**下载轨道文件**下载堆叠对应的精密轨道文件。轨道文件是精确 InSAR 处理所必需的，将与场景数据一起保存在工作目录中。

## 添加任务

点击**添加任务**将所选堆叠注册为任务面板中的任务。这会保存堆叠配置以便将来下载和处理，不会立即开始下载。

---

添加任务后，前往处理器面板选择干涉图配对并提交处理。

[处理器](processor.md){.md-button}
