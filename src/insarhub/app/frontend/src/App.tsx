import { useState, useRef, useCallback, useMemo, useEffect } from 'react'
import Map from './Map'
import TopBar from './TopBar'
import MapToolbar, { type DrawMode, type Basemap } from './MapToolbar'
import SearchFilters, { type Filters, DEFAULT_FILTERS, hasActiveFilters } from './SearchFilters'
import BasemapSwitcher from './BasemapSwitcher'
import ScenePanel from './ScenePanel'
import StackSceneList from './StackSceneList'
import SceneDetailPanel from './SceneDetailPanel'
import SettingsPanel from './SettingsPanel'
import JobQueueDrawer, { type RasterOverlay } from './JobQueueDrawer'
import { bboxToWkt, geometryToWkt, getGeometryBbox, type Bbox } from './geoUtils'
import { DARK, LIGHT } from './theme'
import shpjs from 'shpjs'

const API = import.meta.env.DEV ? 'http://localhost:8000' : ''

// ── Colorbar ────────────────────────────────────────────────────────────────
function colormapGradient(type: string): string {
  if (type === 'unw_phase') {
    // HSV rainbow
    return 'linear-gradient(to top, hsl(0,100%,50%), hsl(60,100%,50%), hsl(120,100%,50%), hsl(180,100%,50%), hsl(240,100%,50%), hsl(300,100%,50%), hsl(360,100%,50%))'
  }
  if (type === 'corr') {
    return 'linear-gradient(to top, #000, #fff)'
  }
  if (type === 'velocity') {
    // matches render.py: rgb(0,0,220) → rgb(255,255,255) → rgb(220,0,0)
    return 'linear-gradient(to top, #0000dc, #ffffff, #dc0000)'
  }
  return 'linear-gradient(to top, #440154, #31688e, #35b779, #fde725)'
}

function Colorbar({ overlay }: { overlay: import('./JobQueueDrawer').RasterOverlay }) {
  const fmt = (v: number) => Math.abs(v) >= 1000 || (Math.abs(v) < 0.01 && v !== 0)
    ? v.toExponential(2) : v.toFixed(2)
  return (
    <div style={{
      position: 'absolute', bottom: 32, right: 12, zIndex: 500,
      display: 'flex', flexDirection: 'row', alignItems: 'stretch', gap: 4,
      background: 'rgba(0,0,0,0.55)', borderRadius: 6, padding: '8px 10px',
      pointerEvents: 'none',
    }}>
      <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'space-between',
                    alignItems: 'flex-end', fontSize: 11, color: '#eee', minWidth: 40 }}>
        <span>{fmt(overlay.vmax)}</span>
        <span style={{ color: '#aaa', fontSize: 10 }}>{overlay.label || overlay.type}</span>
        <span>{fmt(overlay.vmin)}</span>
      </div>
      <div style={{
        width: 14, minHeight: 120,
        background: colormapGradient(overlay.type),
        borderRadius: 3, border: '1px solid rgba(255,255,255,0.2)',
      }} />
    </div>
  )
}

// ── Time Series Drawer ────────────────────────────────────────────────────────
interface TsData { dates: string[]; values: number[]; file: string; unit: string }

function TimeSeriesDrawer({ data, onClose, theme: t }: { data: TsData; onClose: () => void; theme: import('./theme').Theme }) {
  const raw    = data.values.map(v => isFinite(v) ? v * 100 : NaN)
  const first  = raw.find(v => isFinite(v)) ?? 0
  const vals_mm = raw.map(v => isFinite(v) ? v - first : NaN)
  const valid   = vals_mm.filter(v => isFinite(v))
  if (valid.length === 0) return null

  const W = 600, H = 150
  const PAD = { t: 12, r: 16, b: 28, l: 52 }
  const iW  = W - PAD.l - PAD.r
  const iH  = H - PAD.t - PAD.b

  // MintPy dates are YYYYMMDD — convert to ISO before parsing
  const parseDate = (d: string) => {
    if (d.length === 8 && !d.includes('-'))
      return new Date(`${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)}`).getTime()
    return new Date(d).getTime()
  }
  const ts   = data.dates.map(parseDate)
  const tMin = ts.reduce((a, b) => Math.min(a, b), Infinity)
  const tMax = ts.reduce((a, b) => Math.max(a, b), -Infinity)
  const vMin = Math.min(...valid), vMax = Math.max(...valid)
  const vRange = vMax - vMin || 1

  const sx = (t: number) => ((t - tMin) / (tMax - tMin || 1)) * iW
  const sy = (v: number) => iH - ((v - vMin) / vRange) * iH

  const pts = data.dates.map((d, i) => isFinite(vals_mm[i])
    ? [sx(new Date(d).getTime()), sy(vals_mm[i])] as [number, number]
    : null
  ).filter(Boolean) as [number, number][]

  const polyline = pts.map(([x, y]) => `${x},${y}`).join(' ')

  const n    = data.dates.length
  const step = Math.max(1, Math.floor(n / 6))
  const xLabels = data.dates
    .map((d, i) => {
      const iso = d.length === 8 && !d.includes('-')
        ? `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)}` : d
      return { x: sx(parseDate(d)), label: iso.slice(0, 7), i }
    })
    .filter(({ i }) => i % step === 0 || i === n - 1)

  const yLabels = [0, 0.5, 1].map(f => ({
    y: sy(vMin + f * vRange),
    label: (vMin + f * vRange).toFixed(1),
  }))

  const gridColor = t.isDark ? '#222' : '#ddd'
  return (
    <div style={{
      position: 'fixed', bottom: 0, left: 0, right: 0, height: 210,
      background: t.bg2, borderTop: `1px solid ${t.border}`,
      display: 'flex', flexDirection: 'column', zIndex: 600,
      boxShadow: '0 -4px 20px rgba(0,0,0,0.4)',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', padding: '5px 14px',
        borderBottom: `1px solid ${t.border}`, flexShrink: 0,
      }}>
        <span style={{ color: t.accent, fontSize: 11, fontWeight: 600 }}>Time Series</span>
        <span style={{ color: t.textMuted, fontSize: 10, marginLeft: 8 }}>{data.file} · {data.unit} · relative to first date</span>
        <button onClick={onClose} style={{
          marginLeft: 'auto', background: 'none', border: 'none',
          color: t.textMuted, cursor: 'pointer', fontSize: 18, lineHeight: 1,
        }}>×</button>
      </div>
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
        <svg width={W} height={H}>
          <g transform={`translate(${PAD.l},${PAD.t})`}>
            {/* grid lines */}
            {yLabels.map(({ y }) => (
              <line key={y} x1={0} y1={y} x2={iW} y2={y} stroke={gridColor} strokeWidth={1} />
            ))}
            {/* zero line */}
            {vMin < 0 && vMax > 0 && (
              <line x1={0} y1={sy(0)} x2={iW} y2={sy(0)} stroke={t.border} strokeWidth={1} strokeDasharray="4 2" />
            )}
            {/* line */}
            <polyline points={polyline} fill="none" stroke={t.accent} strokeWidth={1.5} />
            {/* dots */}
            {pts.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={2.5} fill={t.accent} />)}
            {/* axes */}
            <line x1={0} y1={iH} x2={iW} y2={iH} stroke={t.border} strokeWidth={1} />
            <line x1={0} y1={0}  x2={0}  y2={iH} stroke={t.border} strokeWidth={1} />
            {/* x labels */}
            {xLabels.map(({ x, label }) => (
              <text key={label} x={x} y={iH + 18} textAnchor="middle" style={{ fontSize: 9, fill: t.textMuted }}>{label}</text>
            ))}
            {/* y labels */}
            {yLabels.map(({ y, label }) => (
              <text key={label} x={-6} y={y + 4} textAnchor="end" style={{ fontSize: 9, fill: t.textMuted }}>{label}</text>
            ))}
            {/* y axis unit */}
            <text x={-38} y={iH / 2} textAnchor="middle"
              transform={`rotate(-90, -38, ${iH / 2})`}
              style={{ fontSize: 9, fill: t.textMuted }}>cm</text>
          </g>
        </svg>
      </div>
    </div>
  )
}

export default function App() {
  // Theme
  const [isDark, setIsDark] = useState(true)
  const theme = isDark ? DARK : LIGHT

  // Search state
  const [searching,   setSearching]   = useState(false)
  const [_resultCount, setResultCount] = useState('')
  const [footprints,  setFootprints]  = useState<GeoJSON.FeatureCollection | null>(null)
  const [_sessionId,  setSessionId]   = useState<string | null>(null)

  // AOI state
  const [aoi,        setAoi]        = useState<Bbox>([-180, -90, 180, 90])
  const [aoiWkt,     setAoiWkt]     = useState<string | null>(null)
  const [aoiGeoJson, setAoiGeoJson] = useState<GeoJSON.Feature | null>(null)

  // Map UI state
  const [drawMode,        setDrawMode]        = useState<DrawMode>(null)
  const [basemap,         setBasemap]         = useState<Basemap>('satellite')
  const [mouseCoords,     setMouseCoords]     = useState<{ lat: number; lng: number } | null>(null)
  const [selectedFeature, setSelectedFeature] = useState<GeoJSON.Feature | null>(null)
  const [stackOpen,       setStackOpen]       = useState(false)
  const [detailScene,     setDetailScene]     = useState<GeoJSON.Feature | null>(null)
  const [workdir,         setWorkdir]         = useState('.')
  const [settingsOpen,    setSettingsOpen]    = useState(false)
  const [settingsInitialTab,          setSettingsInitialTab]          = useState<'general' | 'auth' | 'downloader' | 'processor' | 'analyzer'>('general')
  const [settingsInitialAnalyzerType, setSettingsInitialAnalyzerType] = useState<string | undefined>(undefined)
  const [jobsOpen,        setJobsOpen]        = useState(false)
  const [rasterOverlay,   setRasterOverlay]   = useState<RasterOverlay | null>(null)
  const [rasterPixelVal,  setRasterPixelVal]  = useState<number | null>(null)
  const [downloaderType,    setDownloaderType]    = useState('S1_SLC')
  const [downloaderOptions, setDownloaderOptions] = useState<string[]>(['S1_SLC'])
  const [tsData,            setTsData]            = useState<TsData | null>(null)
  const [tsClickPoint,      setTsClickPoint]      = useState<[number, number] | null>(null)
  const [mapClickSignal,    setMapClickSignal]    = useState(0)

  // Revoke previous blob URL when raster overlay is replaced or cleared
  useEffect(() => {
    return () => { if (rasterOverlay?.url?.startsWith('blob:')) URL.revokeObjectURL(rasterOverlay.url) }
  }, [rasterOverlay])

  // Fetch server settings + available downloaders once on mount
  useEffect(() => {
    fetch(`${API}/api/settings`)
      .then(r => r.json())
      .then(d => { setWorkdir(d.workdir); setDownloaderType(d.downloader) })
      .catch(() => {})
    fetch(`${API}/api/workflows`)
      .then(r => r.json())
      .then(d => { if (d.downloaders) setDownloaderOptions(Object.keys(d.downloaders)) })
      .catch(() => {})
  }, [])

  // Scenes belonging to the same stack as the selected footprint
  const stackScenes = useMemo(() => {
    if (!selectedFeature || !footprints) return []
    const key = selectedFeature.properties?._stack
    return footprints.features.filter(f => f.properties?._stack === key)
  }, [selectedFeature, footprints])

  // Actual date range of the stack (earliest / latest scene acquisition)
  const stackDateRange = useMemo(() => {
    const dates = stackScenes
      .map(f => f.properties?.startTime as string | undefined)
      .filter(Boolean)
      .map(s => s!.slice(0, 10))   // YYYY-MM-DD
      .sort()
    if (!dates.length) return { start: undefined, end: undefined }
    return { start: dates[0], end: dates[dates.length - 1] }
  }, [stackScenes])

  // Filter state
  const [filters,      setFilters]      = useState<Filters>(DEFAULT_FILTERS)
  const [filtersOpen,  setFiltersOpen]  = useState(false)

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // ── Polling helper ────────────────────────────────────────────────────────
  const pollJob = useCallback((jobId: string, onDone: (data: any) => void) => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      const res = await fetch(`${API}/api/jobs/${jobId}`)
      const job = await res.json()
      if (job.status === 'done' || job.status === 'error') {
        clearInterval(pollRef.current!)
        setSearching(false)
        if (job.status === 'done') onDone(job.data)
        else setResultCount(`Error: ${job.message}`)
      }
    }, 1500)
  }, [])

  // ── Universal close-all — fires on EVERY non-draw-mode map click ────────────
  // Intentionally does NOT touch selectedFeature/stackOpen/detailScene:
  // Maplibre fires layer-specific handlers (footprint click → setSelectedFeature)
  // BEFORE the general handler, so clearing selectedFeature here would race with
  // a footprint selection. Scene-panel clearing is handled in handleMapClick below.
  function handleMapClickAny() {
    setJobsOpen(false)
    setSettingsOpen(false)
    setFiltersOpen(false)
    setMapClickSignal(n => n + 1)
  }

  // ── Empty-map click (no footprint hit) ────────────────────────────────────
  async function handleMapClick(lat: number, lng: number) {
    // Clear scene panels only on bare map click (no footprint under cursor)
    setSelectedFeature(null)
    setStackOpen(false)
    setDetailScene(null)

    if (rasterOverlay?.source?.kind !== 'mintpy') return
    try {
      const tsParam = rasterOverlay.source.tsFile
        ? `&ts_file=${encodeURIComponent(rasterOverlay.source.tsFile)}`
        : ''
      const r = await fetch(`${API}/api/timeseries-pixel?path=${encodeURIComponent(rasterOverlay.source.folderPath)}&lat=${lat}&lon=${lng}${tsParam}`)
      if (!r.ok) return
      const d = await r.json()
      if (Array.isArray(d.dates) && d.dates.length > 0) {
        setTsData(d)
        setTsClickPoint([lng, lat])
      }
    } catch { /* ignore fetch errors */ }
  }

  // ── Search ────────────────────────────────────────────────────────────────
  async function handleSearch() {
    const byName = filters.granuleNames && filters.granuleNames.length > 0
    if (!byName && (!filters.startDate || !filters.endDate)) {
      setResultCount('Set start and end dates in Filters')
      setFiltersOpen(true)
      return
    }
    setSearching(true)
    setDrawMode(null)
    setResultCount('Searching…')

    const wkt = aoiWkt ?? bboxToWkt(aoi)

    const res = await fetch(`${API}/api/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        west: aoi[0], south: aoi[1], east: aoi[2], north: aoi[3],
        wkt, start: filters.startDate || null, end: filters.endDate || null,
        maxResults:      filters.maxResults ? parseInt(filters.maxResults) : 2000,
        flightDirection: filters.flightDirection  || null,
        pathStart:       filters.pathStart  ? parseInt(filters.pathStart)  : null,
        pathEnd:         filters.pathEnd    ? parseInt(filters.pathEnd)    : null,
        frameStart:      filters.frameStart ? parseInt(filters.frameStart) : null,
        frameEnd:        filters.frameEnd   ? parseInt(filters.frameEnd)   : null,
        granule_names:   byName ? filters.granuleNames : null,
      }),
    })
    const { job_id } = await res.json()
    pollJob(job_id, (data) => {
      setResultCount(data.summary)
      setFootprints(data.geojson)
      setSessionId(data.session_id)
    })
  }

  // ── AOI drawn on map ──────────────────────────────────────────────────────
  function handleAoiDrawn(wkt: string, bbox: Bbox, feature?: GeoJSON.Feature) {
    setAoiWkt(wkt)
    setAoi(bbox)
    setAoiGeoJson(feature ?? null)
    setDrawMode(null)
  }

  function handleAoiWktChange(wkt: string | null) {
    setAoiWkt(wkt)
    setAoiGeoJson(null)
  }

  function handleClearAoi() {
    setAoiWkt(null)
    setAoiGeoJson(null)
    setAoi([-180, -90, 180, 90])
    setFootprints(null)
    setResultCount('')
    setSelectedFeature(null)
  }

  // ── Shapefile upload ──────────────────────────────────────────────────────
  async function handleShapefileUpload(file: File) {
    try {
      const ext    = file.name.split('.').pop()?.toLowerCase()
      const buffer = await file.arrayBuffer()
      let feature: GeoJSON.Feature | undefined

      if (ext === 'gpkg') {
        // Backend parses GeoPackage via geopandas (base64 JSON — no multipart needed)
        const b64 = btoa(String.fromCharCode(...new Uint8Array(buffer)))
        const res = await fetch(`${API}/api/parse-aoi`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filename: file.name, data: b64 }),
        })
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }))
          setResultCount(`GeoPackage error: ${err.detail}`)
          return
        }
        const { feature: f } = await res.json()
        feature = f
      } else {
        const geojson = await shpjs(buffer)
        const fc      = Array.isArray(geojson) ? geojson[0] : geojson
        feature = fc.features[0]
      }

      if (!feature) { setResultCount('No features found in file'); return }
      const wkt  = geometryToWkt(feature.geometry)
      const bbox = getGeometryBbox(feature.geometry)
      handleAoiDrawn(wkt, bbox, feature)   // pass feature → polygon shape preserved
    } catch (err) {
      setResultCount(`File error: ${err}`)
    }
  }

  return (
    <div style={{ position: 'relative', width: '100vw', height: '100vh', overflow: 'hidden' }}>

      <TopBar
        downloaderType={downloaderType}
        downloaderOptions={downloaderOptions}
        onDownloaderTypeChange={setDownloaderType}
        aoiWkt={aoiWkt}
        onAoiWktChange={handleAoiWktChange}
        startDate={filters.startDate}
        endDate={filters.endDate}
        onDatesChange={(s, e) => setFilters(f => ({ ...f, startDate: s, endDate: e }))}
        onSearch={() => handleSearch()}
        searching={searching}
        theme={theme}
        onThemeToggle={() => {
          setIsDark(d => {
            const next = !d
            setBasemap(next ? 'satellite' : 'osm')
            return next
          })
        }}
        onFiltersOpen={() => setFiltersOpen(true)}
        hasActiveFilters={hasActiveFilters(filters)}
        onJobsOpen={() => setJobsOpen(o => !o)}
        jobsOpen={jobsOpen}
        onSettingsOpen={() => setSettingsOpen(true)}
      />

      <MapToolbar
        drawMode={drawMode}
        onDrawModeChange={setDrawMode}
        onClearAoi={handleClearAoi}
        onShapefileUpload={handleShapefileUpload}
        mouseCoords={mouseCoords}
        rasterValue={rasterPixelVal}
        theme={theme}
      />

      {/* Map */}
      <div style={{ position: 'absolute', top: 84, left: 0, right: 0, bottom: 0 }}>
        <Map
          footprints={footprints}
          aoi={aoi}
          aoiGeojson={aoiGeoJson}
          drawMode={drawMode}
          basemap={basemap}
          footprintOpacity={0.5}
          rasterOverlay={rasterOverlay}
          tsClickPoint={tsClickPoint}
          onAoiDrawn={handleAoiDrawn}
          onMouseMove={setMouseCoords}
          onFootprintClick={setSelectedFeature}
          onRasterPixel={setRasterPixelVal}
          onMapClick={handleMapClick}
          onMapClickAny={handleMapClickAny}
        />
        <BasemapSwitcher
          basemap={basemap}
          onBasemapChange={setBasemap}
          theme={theme}
        />
        {rasterOverlay && <Colorbar overlay={rasterOverlay} />}
      </div>

      {/* Cascading scene panels — L3 · L2 · L1 (left to right) */}
      {selectedFeature && (
        <div style={{
          position: 'absolute', top: 84, right: 0, bottom: 0,
          display: 'flex', flexDirection: 'row', zIndex: 110,
        }}>
          {detailScene && (
            <SceneDetailPanel
              feature={detailScene}
              theme={theme}
              workdir={workdir}
              onClose={() => setDetailScene(null)}
            />
          )}
          {stackOpen && (
            <StackSceneList
              stackKey={selectedFeature.properties?._stack ?? ''}
              scenes={stackScenes}
              theme={theme}
              selectedScene={detailScene}
              onClose={() => { setStackOpen(false); setDetailScene(null) }}
              onSceneClick={setDetailScene}
            />
          )}
          <ScenePanel
            feature={selectedFeature}
            theme={theme}
            stackStart={stackDateRange.start}
            stackEnd={stackDateRange.end}
            stackCount={stackScenes.length}
            stackUrls={stackScenes.map(f => f.properties?.url).filter(Boolean)}
            workdir={workdir}
            aoiWkt={aoiWkt}
            downloaderType={downloaderType}
            stackOpen={stackOpen}
            onClose={() => { setSelectedFeature(null); setStackOpen(false); setDetailScene(null) }}
            onStackClick={() => { setStackOpen(o => !o); setDetailScene(null) }}
          />
        </div>
      )}

      {/* Job queue drawer */}
      {jobsOpen && (
        <JobQueueDrawer
          theme={theme}
          workdir={workdir}
          mapClickSignal={mapClickSignal}
          onClose={() => setJobsOpen(false)}
          onRasterSelect={setRasterOverlay}
        />
      )}

      {/* Settings panel */}
      {settingsOpen && (
        <SettingsPanel
          theme={theme}
          downloaderType={downloaderType}
          onDownloaderTypeChange={setDownloaderType}
          startDate={filters.startDate}
          endDate={filters.endDate}
          aoiWkt={aoiWkt}
          onDatesChange={(s, e) => setFilters(f => ({ ...f, startDate: s, endDate: e }))}
          onAoiWktChange={handleAoiWktChange}
          initialTab={settingsInitialTab}
          initialAnalyzerType={settingsInitialAnalyzerType}
          onClose={() => {
            setSettingsOpen(false)
            setSettingsInitialTab('general')
            setSettingsInitialAnalyzerType(undefined)
            fetch(`${API}/api/settings`).then(r => r.json()).then(d => { setWorkdir(d.workdir); setDownloaderType(d.downloader) }).catch(() => {})
          }}
        />
      )}

      {/* MintPy time series drawer */}
      {tsData && <TimeSeriesDrawer data={tsData} onClose={() => { setTsData(null); setTsClickPoint(null) }} theme={theme} />}

      {/* Filter panel — mounts fresh each open so draft resets */}
      {filtersOpen && (
        <SearchFilters
          open={filtersOpen}
          filters={filters}
          theme={theme}
          onClose={() => setFiltersOpen(false)}
          onApply={setFilters}
        />
      )}

    </div>
  )
}