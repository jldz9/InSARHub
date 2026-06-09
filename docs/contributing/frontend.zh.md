# 前端贡献指南

前端是基于 React 19 + TypeScript 的单页应用，使用 Vite 构建。所有与后端的通信均通过 `/api/*` 端点进行。

## 环境搭建

如尚未安装 Node.js，通过 conda 安装：

```bash
conda install -c conda-forge nodejs
```

安装依赖并启动开发服务器：

```bash
cd src/insarhub/app/frontend
npm install
npm run dev        # 开发服务器运行在 :5173，代理 /api → :8080
```

在另一个终端中，切换到 InSARHub 根目录并使用 uvicorn 启动后端：

```bash
cd /path/to/InSARHub
uvicorn insarhub.app.api:app --reload --port 8080
```

`--reload` 会在源码变更时自动重启服务器。开发期间保持此终端开启。

构建生产版本（输出至 `src/insarhub/app/static/`）：

```bash
npm run build
```

## 模块说明

### 入口与全局

| 文件 | 作用 |
|---|---|
| `main.tsx` | React 入口，将 `<App>` 挂载到 `#root`。 |
| `App.tsx` | 根组件。持有所有全局状态：搜索结果、选中的堆叠/场景、AOI、亮/暗主题、栅格叠加图层。负责顶层布局组合和顶层 API 调用（搜索、配对选择）。 |
| `theme.ts` | 亮色/暗色主题 token 对象（`DARK`、`LIGHT`），包含颜色、边框、强调色。以 props 形式传递给所有组件，无需 CSS-in-JS 或 MUI。 |
| `geoUtils.ts` | 几何工具函数：`geometryToWkt`、`bboxToWkt`、`getGeometryBbox`。将 GeoJSON 几何体转换为后端搜索 API 所需的 WKT 字符串。 |

### 地图

| 文件 | 作用 |
|---|---|
| `Map.tsx` | MapLibre GL 封装。将堆叠覆盖范围渲染为 GeoJSON 图层、AOI 多边形和栅格叠加图层（处理结果）。处理所有绘制模式（矩形/多边形/点），完成后通过 `onAoiDrawn` 回调返回 WKT。报告鼠标坐标和栅格像素值。 |
| `MapToolbar.tsx` | 左侧悬浮工具栏：绘制模式选择（矩形/多边形/点/无）、清除 AOI、Shapefile 上传、实时鼠标坐标显示、栅格像素值读取。 |
| `DrawToolbar.tsx` | 地图左边缘的极简悬浮绘制按钮，对绘制模式状态和文件 `<input>` 的轻量封装。 |
| `BasemapSwitcher.tsx` | 悬浮底图切换下拉框（街道图 / 卫星图 / 地形图）。 |

### 搜索与场景选择

| 文件 | 作用 |
|---|---|
| `TopBar.tsx` | 应用顶栏。包含下载器类型选择器、AOI WKT 文本输入、日期范围选择器、搜索按钮、过滤徽章、任务抽屉开关、设置开关和主题切换。 |
| `SearchFilters.tsx` | 高级过滤弹出面板：飞行方向、路径/帧范围、最大结果数、场景名称文件上传（解析 `.txt`/`.csv`）。导出 `Filters` 类型和 `DEFAULT_FILTERS` 供 `App.tsx` 使用。 |
| `StackSummaryDrawer.tsx` | 搜索返回的所有堆叠列表（按路径/帧分组）。显示每个堆叠的场景数、日期范围和飞行方向。点击行打开对应堆叠的 `ScenePanel`。 |
| `ScenePanel.tsx` | 单堆叠详情侧边栏。显示堆叠中所有 SLC 场景，触发配对选择（`/api/select-pairs`），提供下载和轨道下载控件，以及配对质量评分入口。在重新挂载时保持活动任务 ID。 |
| `StackSceneList.tsx` | `ScenePanel` 内使用的紧凑场景列表。每行显示获取日期、卫星平台（S1A/S1B）和产品类型。点击打开 `SceneDetailPanel`。 |
| `SceneDetailPanel.tsx` | 单场景元数据卡片：获取时间、文件大小、轨道方向、平台、ASF 链接。 |

### 任务与结果

| 文件 | 作用 |
|---|---|
| `JobQueueDrawer.tsx` | 主要任务管理面板（可调整宽度）。浏览工作目录子文件夹，显示每个文件夹的状态，并提供文件夹级操作：提交处理器、刷新 HyP3 状态、下载、重试、运行分析器。同时处理将栅格叠加图层（速度/时间序列）加载到地图上。导出 `RasterOverlay` 类型供 `App.tsx` 和 `Map.tsx` 使用。 |
| `NetworkEditor.tsx` | 基于 **Pixi.js v8** 渲染的交互式干涉图配对网络图。X 轴为获取日期，Y 轴为垂直基线。点击边切换启用/移除状态。滚轮缩放，拖拽平移。通过回调将配对编辑结果传回 `JobQueueDrawer`。 |

### 设置

| 文件 | 作用 |
|---|---|
| `SettingsPanel.tsx` | 设置弹窗。从 `/api/settings-schema` 获取 `_ui_groups` / `_ui_fields` schema，动态渲染下载器、处理器和分析器的配置字段。通过 `/api/settings` 保存更改。仅在需要自定义 UI 行为时（文件选择器、弹窗、联动字段）才需修改此文件。 |

### 工具

| 文件 | 作用 |
|---|---|
| `StatusBar.tsx` | 底部状态栏，显示后台任务消息和动画进度条（0–100）。`message` 为空时隐藏。 |
| `assets/icons.tsx` | 工具栏和按钮中使用的内联 SVG 图标组件。 |

> **注意：** `SearchBar.tsx` 存在于源代码中但未被任何地方导入——它是未使用的遗留代码。

## 与后端通信

所有 API 调用均通过 `fetch` 发起。长时间运行的任务会立即返回 `job_id`；前端轮询 `/api/job-status/{job_id}` 直到 `status` 变为 `"done"` 或 `"error"`。

```typescript
// 提交任务
const res = await fetch('/api/my-action', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
});
const { job_id } = await res.json();

// 轮询完成状态
const poll = setInterval(async () => {
  const s = await fetch(`/api/job-status/${job_id}`).then(r => r.json());
  if (s.status === 'done' || s.status === 'error') {
    clearInterval(poll);
    // 处理结果
  }
}, 1000);
```

## 设置面板

`SettingsPanel.tsx` 根据后端 `_ui_groups` / `_ui_fields` schema 动态渲染配置字段——添加新的后端配置字段时无需修改 React 代码。Schema 通过 `/api/settings-schema` 获取。

只有在需要自定义 UI 行为时（如弹窗、文件选择器、联动字段）才向 `SettingsPanel.tsx` 添加代码。

## 添加新组件

1. 创建 `src/MyComponent.tsx`。
2. 状态优先保持局部——仅在多个组件需要共享时才提升至 `App.tsx`。
3. 使用 MUI 组件保持与现有 UI 的一致性（`@mui/material`）。
4. 亮色/暗色模式由 MUI 主题处理——避免硬编码颜色值，使用 `theme.palette.*`。

## Vite 代理

开发服务器将 `/api/*` 代理至 `http://127.0.0.1:8080`（在 `vite.config.ts` 中配置）。开发时前端和后端需同时运行。

## 构建输出

`npm run build` 执行 `tsc -b && vite build`。输出文件位于 `src/insarhub/app/static/`，由 FastAPI 作为静态文件提供。准备发布时需将构建产物一并提交。

## 代码风格

- 启用 TypeScript 严格模式——避免使用 `any`。
- 优先使用函数式组件和 Hooks。
- 保持组件职责单一——若组件超过约 200 行，考虑拆分。
- 不写解释代码做什么的注释；变量和函数名应自解释。
