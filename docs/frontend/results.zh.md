# 结果查看器

分析完成后，InSARHub 可将 MintPy 结果直接显示在地图上。

## 加载速度图

1. 打开含有 MintPy 输出（`velocity.h5`）的任务文件夹
2. 打开**查看结果**面板
3. 从列表中选择时序文件（例如 `timeseries_ERA5_ramp_demErr.h5`）
4. 点击**绘图**

<!-- screenshot: view results panel with ts file selected -->
![查看结果面板](fig/results_panel_light.png#only-light){: .doc-img style="width: 60%"}
![查看结果面板](fig/results_panel_dark.png#only-dark){: .doc-img style="width: 60%"}
/// caption
结果面板，已选择时序文件，准备绘图。
///

速度图以彩色叠加层显示在地图上，色标自动缩放至数据的第 98 百分位（红色 = 沉降，蓝色 = 抬升）。零位移像素为透明。

<!-- screenshot: velocity map overlaid on map -->
![速度图](fig/velocity_map_light.png#only-light){: .doc-img-wide }
![速度图](fig/velocity_map_dark.png#only-dark){: .doc-img-wide }
/// caption
视线方向速度图叠加在底图上。
///

---

## 像素时序

点击速度图上的任意像素，绘制该位置的位移时序。

<!-- screenshot: timeseries drawer open at bottom -->
![时序抽屉](fig/timeseries_light.png#only-light){: .doc-img-wide }
![时序抽屉](fig/timeseries_dark.png#only-dark){: .doc-img-wide }
/// caption
所点击像素的位移时序。第一个日期设为零（相对位移）。
///

时序抽屉：

- 以 **cm** 为单位显示相对于第一个获取日期的位移
- 跟随应用的浅色/深色主题
- 点击 × 按钮关闭

---

## 色标

速度色标为对称色标，自动缩放至场景中绝对速度值的**第 98 百分位**：

- **红色** — 负速度（沉降 / 远离卫星运动）
- **白色** — 接近零位移
- **蓝色** — 正速度（抬升 / 朝向卫星运动）
- **透明** — 恰好为零（无数据或参考像素）
