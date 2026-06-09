# Frontend Contributing Guide

The frontend is a React 19 + TypeScript single-page app built with Vite. It communicates with the FastAPI backend exclusively via `/api/*` endpoints.

## Setup

Install Node.js via conda if not already available:

```bash
conda install -c conda-forge nodejs
```

Then install dependencies and start the dev server:

```bash
cd src/insarhub/app/frontend
npm install
npm run dev        # dev server on :5173, proxies /api → :8080
```

In a separate terminal, navigate to the InSARHub root and start the backend with uvicorn:

```bash
cd /path/to/InSARHub
uvicorn insarhub.app.api:app --reload --port 8080
```

`--reload` restarts the server automatically on source changes. Keep this terminal open while developing.

To build for production (output goes into `src/insarhub/app/static/`):

```bash
npm run build
```

## Module Reference

### Entry & Global

| File | Role |
|---|---|
| `main.tsx` | React entry point. Mounts `<App>` into `#root`. |
| `App.tsx` | Root component. Owns all global state: search results, selected stack/scene, AOI, dark/light theme, raster overlay. Composes the full layout and issues top-level API calls (search, pair selection). |
| `theme.ts` | Light/dark theme token objects (`DARK`, `LIGHT`) — colors, borders, accents. Passed as props to all components; no CSS-in-JS or MUI required. |
| `geoUtils.ts` | Geometry helpers: `geometryToWkt`, `bboxToWkt`, `getGeometryBbox`. Converts GeoJSON geometry to WKT strings for the backend search API. |

### Map

| File | Role |
|---|---|
| `Map.tsx` | MapLibre GL wrapper. Renders stack footprints as a GeoJSON layer, AOI polygon, and raster overlays (processed results). Handles all draw modes (box/polygon/pin), fires `onAoiDrawn` with WKT on completion. Reports hover coordinates and pixel values from raster overlays. |
| `MapToolbar.tsx` | Left-side floating toolbar: draw mode selector (box / polygon / pin / none), AOI clear button, shapefile upload, live mouse-coordinate display, and raster pixel value readout. |
| `DrawToolbar.tsx` | Minimal floating draw buttons overlaid on the map left edge. Thin wrapper around draw mode state and shapefile `<input>`. |
| `BasemapSwitcher.tsx` | Floating basemap toggle dropdown (Street / Satellite / Topo). |

### Search & Scene Selection

| File | Role |
|---|---|
| `TopBar.tsx` | App header. Contains downloader type selector, AOI WKT text input, date range pickers, search button, filter badge, jobs drawer toggle, settings toggle, and theme toggle. |
| `SearchFilters.tsx` | Advanced filter popover: flight direction, path/frame range, max results, and granule-name file upload (parses `.txt`/`.csv`). Exposes `Filters` type and `DEFAULT_FILTERS` used by `App.tsx`. |
| `StackSummaryDrawer.tsx` | List of all stacks returned by search (grouped by path/frame). Displays scene count, date range, and flight direction per stack. Click a row to open `ScenePanel` for that stack. |
| `ScenePanel.tsx` | Per-stack detail sidebar. Shows all SLC scenes in the stack, triggers pair selection (`/api/select-pairs`), exposes download and orbit-download controls, and links to pair quality scoring. Persists active job IDs across remounts. |
| `StackSceneList.tsx` | Compact scene list used inside `ScenePanel`. Renders acquisition date, platform (S1A/S1B), and product type per row. Click to open `SceneDetailPanel`. |
| `SceneDetailPanel.tsx` | Single-scene metadata card: acquisition time, file size, orbit direction, platform, ASF URL. |

### Jobs & Results

| File | Role |
|---|---|
| `JobQueueDrawer.tsx` | Main job management panel (resizable). Browses workdir subfolders, shows per-folder status, and exposes per-folder actions: submit processor, refresh HyP3 status, download, retry, run analyzer. Also handles loading raster overlays (velocity/timeseries) onto the map. Owns `RasterOverlay` type exported to `App.tsx` and `Map.tsx`. |
| `NetworkEditor.tsx` | Interactive interferogram pair network graph rendered with **Pixi.js v8**. X-axis = acquisition date, Y-axis = perpendicular baseline. Click an edge to toggle it active/removed. Scroll to zoom, drag to pan. Communicates pair edits back to `JobQueueDrawer` via callback. |

### Settings

| File | Role |
|---|---|
| `SettingsPanel.tsx` | Settings modal. Fetches the `_ui_groups` / `_ui_fields` schema from `/api/settings-schema` and renders downloader, processor, and analyzer config fields dynamically. Saves changes to the backend via `/api/settings`. Only needs code changes for custom UI behaviour (file pickers, modals, dependent fields). |

### Utilities

| File | Role |
|---|---|
| `StatusBar.tsx` | Bottom status bar showing a background job message and animated progress bar (0–100). Hidden when `message` is empty. |
| `assets/icons.tsx` | Inline SVG icon components used across the toolbar and buttons. |

> **Note:** `SearchBar.tsx` exists in the source tree but is not imported anywhere — it is unused legacy code.

## Backend Communication

All API calls go through `fetch`. Long-running jobs return a `job_id` immediately; the frontend polls `/api/job-status/{job_id}` until `status` is `"done"` or `"error"`.

```typescript
// Submit a job
const res = await fetch('/api/my-action', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
});
const { job_id } = await res.json();

// Poll for completion
const poll = setInterval(async () => {
  const s = await fetch(`/api/job-status/${job_id}`).then(r => r.json());
  if (s.status === 'done' || s.status === 'error') {
    clearInterval(poll);
    // handle result
  }
}, 1000);
```

## Settings Panel

`SettingsPanel.tsx` renders config fields dynamically from the backend's `_ui_groups` / `_ui_fields` schema — no code changes needed when adding a new backend config field. The schema is fetched from `/api/settings-schema`.

Only add React code to `SettingsPanel.tsx` when you need custom UI behaviour (e.g. a modal, a file picker, a dependent field).

## Adding a New Component

1. Create `src/MyComponent.tsx`.
2. Keep state local unless multiple components need it — lift to `App.tsx` only when necessary.
3. Use MUI components for consistency with the existing UI (`@mui/material`).
4. Dark/light mode is handled by the MUI theme — avoid hardcoded color values; use `theme.palette.*` instead.

## Vite Proxy

The dev server proxies `/api/*` to `http://127.0.0.1:8080` (configured in `vite.config.ts`). This means the frontend dev server and the backend must both be running during development.

## Build Output

`npm run build` runs `tsc -b && vite build`. Output lands in `src/insarhub/app/static/` and is served by FastAPI as static files. Commit the built output when preparing a release.

## Code Style

- TypeScript strict mode is enabled — avoid `any`.
- Prefer functional components and hooks.
- Keep components focused — if a component exceeds ~200 lines, consider splitting it.
- Do not add comments explaining what the code does; variable and function names should be self-explanatory.
