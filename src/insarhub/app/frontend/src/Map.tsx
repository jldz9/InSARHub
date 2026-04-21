import { useEffect, useRef } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { bboxToWkt, geometryToWkt, getGeometryBbox, type Bbox } from './geoUtils'
import type { DrawMode, Basemap } from './MapToolbar'
import type { RasterOverlay } from './JobQueueDrawer'

interface Props {
  footprints?:        GeoJSON.FeatureCollection | null
  highlightStackKey?: string | null
  aoi?:               Bbox | null
  aoiGeojson?:        GeoJSON.Feature | null
  drawMode:           DrawMode
  basemap:            Basemap
  footprintOpacity:   number
  rasterOverlay?:     RasterOverlay | null
  tsClickPoint?:      [number, number] | null
  onAoiDrawn:         (wkt: string, bbox: Bbox, feature?: GeoJSON.Feature) => void
  onMouseMove?:       (coords: { lat: number; lng: number } | null) => void
  onFootprintClick?:  (feature: GeoJSON.Feature) => void
  onRasterPixel?:     (val: number | null) => void
  onMapClick?:        (lat: number, lng: number) => void
  onMapClickAny?:     () => void   // fires on every non-draw-mode click, including over footprints
}

const EMPTY_FC: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] }

const BASEMAP_TILES: Record<Basemap, { tiles: string[]; overlay?: string[]; attribution: string }> = {
  // Carto Voyager — English labels, no API key required
  osm:       { tiles: ['https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png',
                        'https://b.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png',
                        'https://c.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png'],
                attribution: '© OpenStreetMap contributors © CARTO' },
  // Esri World Imagery — satellite, no text labels
  satellite: { tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
                overlay: ['https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}'],
                attribution: '© Esri, Maxar, Earthstar Geographics' },
  // Esri World Topo — English labels, no API key required
  topo:      { tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}'],
                attribution: '© Esri, HERE, Garmin, USGS' },
}

export default function Map({
  footprints, highlightStackKey, aoi, aoiGeojson, drawMode, basemap,
  footprintOpacity, rasterOverlay, tsClickPoint, onAoiDrawn, onMouseMove, onFootprintClick, onRasterPixel, onMapClick, onMapClickAny,
}: Props) {
  const containerRef        = useRef<HTMLDivElement>(null)
  const mapRef              = useRef<maplibregl.Map | null>(null)
  const drawModeRef         = useRef(drawMode)
  const onAoiDrawnRef       = useRef(onAoiDrawn)
  const onFootprintClickRef = useRef(onFootprintClick)
  const rasterOverlayRef    = useRef(rasterOverlay ?? null)
  const onRasterPixelRef    = useRef(onRasterPixel)
  const onMapClickRef       = useRef(onMapClick)
  const onMapClickAnyRef    = useRef(onMapClickAny)
  const boxStartRef         = useRef<[number, number] | null>(null)
  const polyPointsRef       = useRef<[number, number][]>([])
  const mousePosRef         = useRef<[number, number]>([0, 0])
  const stackHighlightIds   = useRef<Set<number | string>>(new Set())

  useEffect(() => { drawModeRef.current = drawMode }, [drawMode])
  useEffect(() => { onAoiDrawnRef.current = onAoiDrawn }, [onAoiDrawn])
  useEffect(() => { onFootprintClickRef.current = onFootprintClick }, [onFootprintClick])
  useEffect(() => { rasterOverlayRef.current = rasterOverlay ?? null }, [rasterOverlay])
  useEffect(() => { onRasterPixelRef.current = onRasterPixel }, [onRasterPixel])
  useEffect(() => { onMapClickRef.current = onMapClick }, [onMapClick])
  useEffect(() => { onMapClickAnyRef.current = onMapClickAny }, [onMapClickAny])

  // Highlight all footprints belonging to a hovered stack key
  useEffect(() => {
    const map = mapRef.current
    if (!map?.isStyleLoaded()) return
    stackHighlightIds.current.forEach(id => {
      map.setFeatureState({ source: 'footprints', id }, { hover: false })
    })
    stackHighlightIds.current.clear()
    if (!highlightStackKey) return
    map.querySourceFeatures('footprints').forEach(f => {
      if (f.properties?._stack === highlightStackKey && f.id != null) {
        map.setFeatureState({ source: 'footprints', id: f.id }, { hover: true })
        stackHighlightIds.current.add(f.id)
      }
    })
  }, [highlightStackKey])

  // ── Init map ──────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return

    const bm  = BASEMAP_TILES[basemap]
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: { basemap: { type: 'raster', tiles: bm.tiles, tileSize: 256, attribution: bm.attribution } },
        layers:  [{ id: 'basemap', type: 'raster', source: 'basemap',
          paint: { 'raster-brightness-max': 0.88 } }],
      },
      center: [-105, 39],
      zoom: 4,
      doubleClickZoom: false,
    })

    map.dragRotate.disable()
    map.dragPan.disable()

    // ── Right-click pan ───────────────────────────────────────────────────
    const canvas = map.getCanvas()
    let isPanning = false
    let lastPanPos = { x: 0, y: 0 }

    canvas.addEventListener('mousedown', (e) => {
      if (e.button !== 2) return
      e.preventDefault()
      isPanning = true
      lastPanPos = { x: e.clientX, y: e.clientY }
      canvas.style.cursor = 'grabbing'
    })

    const onPanMove = (e: MouseEvent) => {
      if (!isPanning) return
      const rect = canvas.getBoundingClientRect()
      const from = map.unproject([lastPanPos.x - rect.left, lastPanPos.y - rect.top])
      const to   = map.unproject([e.clientX   - rect.left, e.clientY   - rect.top])
      const c = map.getCenter()
      map.setCenter([c.lng - (to.lng - from.lng), c.lat - (to.lat - from.lat)])
      lastPanPos = { x: e.clientX, y: e.clientY }
    }

    const onPanUp = (e: MouseEvent) => {
      if (e.button !== 2 || !isPanning) return
      isPanning = false
      canvas.style.cursor = drawModeRef.current ? 'crosshair' : 'default'
    }

    window.addEventListener('mousemove', onPanMove)
    window.addEventListener('mouseup',   onPanUp)
    canvas.addEventListener('contextmenu', (e) => e.preventDefault())

    map.addControl(new maplibregl.NavigationControl(), 'top-right')
    map.addControl(new maplibregl.ScaleControl(), 'bottom-left')

    map.on('load', () => {
      // ── SAR footprints — blue outline, no fill ──────────────────────────
      map.addSource('footprints', { type: 'geojson', data: EMPTY_FC, generateId: true })

      // Transparent fill — makes the polygon interior clickable/hoverable
      map.addLayer({ id: 'footprints-fill', type: 'fill', source: 'footprints',
        paint: {
          'fill-color': ['match', ['get', 'flightDirection'],
            'ASCENDING',  '#f39c12',
            'DESCENDING', '#00bcd4',
            '#aaaaaa',
          ],
          'fill-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 0.12, 0],
        } })

      map.addLayer({ id: 'footprints-line', type: 'line', source: 'footprints',
        paint: {
          'line-color': ['match', ['get', 'flightDirection'],
            'ASCENDING',  '#f39c12',   // orange
            'DESCENDING', '#00bcd4',   // cyan/blue
            '#aaaaaa',                 // fallback
          ],
          'line-width': 2,
          'line-opacity': footprintOpacity,
        } })

      // ── Finished AOI (box/polygon) — red outline ────────────────────────
      map.addSource('aoi', { type: 'geojson', data: EMPTY_FC })
      map.addLayer({ id: 'aoi-fill', type: 'fill', source: 'aoi',
        paint: { 'fill-color': '#e53935', 'fill-opacity': 0.08 } })
      map.addLayer({ id: 'aoi-line', type: 'line', source: 'aoi',
        paint: { 'line-color': '#e53935', 'line-width': 2 } })

      // ── Pin marker ──────────────────────────────────────────────────────
      map.addSource('pin', { type: 'geojson', data: EMPTY_FC })
      map.addLayer({ id: 'pin-circle', type: 'circle', source: 'pin',
        paint: { 'circle-radius': 7, 'circle-color': '#e53935',
                 'circle-stroke-width': 2, 'circle-stroke-color': '#fff' } })

      // ── Timeseries click marker ──────────────────────────────────────
      map.addSource('ts-click', { type: 'geojson', data: EMPTY_FC })
      map.addLayer({ id: 'ts-click-ring', type: 'circle', source: 'ts-click',
        paint: { 'circle-radius': 9, 'circle-color': 'rgba(0,0,0,0)',
                 'circle-stroke-width': 2.5, 'circle-stroke-color': '#ffffff' } })
      map.addLayer({ id: 'ts-click-dot', type: 'circle', source: 'ts-click',
        paint: { 'circle-radius': 5, 'circle-color': '#facc15',
                 'circle-stroke-width': 1.5, 'circle-stroke-color': '#000000' } })

      // ── Box drag preview — blue dashed ──────────────────────────────────
      map.addSource('box-preview', { type: 'geojson', data: EMPTY_FC })
      map.addLayer({ id: 'box-preview-fill', type: 'fill', source: 'box-preview',
        paint: { 'fill-color': '#4fc3f7', 'fill-opacity': 0.05 } })
      map.addLayer({ id: 'box-preview-line', type: 'line', source: 'box-preview',
        paint: { 'line-color': '#4fc3f7', 'line-width': 2, 'line-dasharray': [2, 1.5] } })

      // ── Polygon in-progress — blue dashed outline + vertex dots ────────
      map.addSource('poly-drawing', { type: 'geojson', data: EMPTY_FC })
      map.addLayer({ id: 'poly-drawing-line', type: 'line', source: 'poly-drawing',
        paint: { 'line-color': '#4fc3f7', 'line-width': 2, 'line-dasharray': [2, 1.5] } })
      map.addLayer({ id: 'poly-drawing-points', type: 'circle', source: 'poly-drawing',
        filter: ['==', '$type', 'Point'],
        paint: { 'circle-radius': 5, 'circle-color': '#4fc3f7',
                 'circle-stroke-width': 2, 'circle-stroke-color': '#fff' } })

      // ── Initial basemap overlay (e.g. satellite labels) ──────────────
      if (bm.overlay) {
        map.addSource('basemap-overlay', { type: 'raster', tiles: bm.overlay, tileSize: 256 })
        map.addLayer({ id: 'basemap-overlay', type: 'raster', source: 'basemap-overlay' },
          'footprints-line')
      }

      // ── Footprint highlight (hovered / selected) ─────────────────────
      map.addLayer({ id: 'footprints-hover', type: 'line', source: 'footprints',
        paint: {
          'line-color': '#ffffff',
          'line-width': 3,
          'line-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 1, 0],
        } })

      // ── Footprint click & hover events ───────────────────────────────
      let hoveredId: string | number | null = null

      function onFootprintMouseMove(e: maplibregl.MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) {
        if (drawModeRef.current) return
        map.getCanvas().style.cursor = 'pointer'
        const f = e.features?.[0]
        if (!f) return
        if (hoveredId !== null) map.setFeatureState({ source: 'footprints', id: hoveredId }, { hover: false })
        hoveredId = f.id ?? null
        if (hoveredId !== null) map.setFeatureState({ source: 'footprints', id: hoveredId }, { hover: true })
      }

      function onFootprintMouseLeave() {
        if (hoveredId !== null) map.setFeatureState({ source: 'footprints', id: hoveredId }, { hover: false })
        hoveredId = null
        if (!drawModeRef.current) map.getCanvas().style.cursor = 'default'
      }

      function onFootprintClick(e: maplibregl.MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) {
        if (drawModeRef.current) return
        const f = e.features?.[0]
        if (f) onFootprintClickRef.current?.(f as unknown as GeoJSON.Feature)
      }

      map.on('mousemove',  'footprints-fill', onFootprintMouseMove)
      map.on('mousemove',  'footprints-line', onFootprintMouseMove)
      map.on('mouseleave', 'footprints-fill', onFootprintMouseLeave)
      map.on('mouseleave', 'footprints-line', onFootprintMouseLeave)
      map.on('click',      'footprints-fill', onFootprintClick)
      map.on('click',      'footprints-line', onFootprintClick)

      // General map click
      map.on('click', (e) => {
        if (drawModeRef.current) return
        // Always fire for close-all-panels (fires even over footprints)
        onMapClickAnyRef.current?.()
        const hits = map.queryRenderedFeatures(e.point, { layers: ['footprints-fill'] })
        if (hits.length > 0) return
        // Only fire timeseries / other empty-map actions when no footprint hit
        onMapClickRef.current?.(e.lngLat.lat, e.lngLat.lng)
      })
    })

    // ── Helper: update polygon-in-progress preview ────────────────────────
    function updatePolyPreview(pts: [number, number][], cursor?: [number, number]) {
      const src = map.getSource('poly-drawing') as maplibregl.GeoJSONSource
      if (!src) return
      const allPts = cursor ? [...pts, cursor] : pts
      if (allPts.length < 2) { src.setData(EMPTY_FC); return }
      src.setData({
        type: 'FeatureCollection',
        features: [
          // Line connecting all points + back to first to preview closure
          { type: 'Feature', properties: {}, geometry: {
            type: 'LineString',
            coordinates: [...allPts, allPts[0]],
          }},
          // Vertex dots
          ...pts.map(p => ({
            type: 'Feature' as const, properties: {},
            geometry: { type: 'Point' as const, coordinates: p },
          })),
        ],
      })
    }

    function clearPolyPreview() {
      ;(map.getSource('poly-drawing') as maplibregl.GeoJSONSource)?.setData(EMPTY_FC)
    }

    // ── Box click-click ───────────────────────────────────────────────────
    map.on('click', (e) => {
      if (drawModeRef.current !== 'box') return
      if (!boxStartRef.current) {
        // First click — store start
        boxStartRef.current = [e.lngLat.lng, e.lngLat.lat]
      } else {
        // Second click — complete the box
        const start = boxStartRef.current
        boxStartRef.current = null
        map.getCanvas().style.cursor = 'crosshair'
        ;(map.getSource('box-preview') as maplibregl.GeoJSONSource)?.setData(EMPTY_FC)
        const bbox: Bbox = [
          Math.min(start[0], e.lngLat.lng), Math.min(start[1], e.lngLat.lat),
          Math.max(start[0], e.lngLat.lng), Math.max(start[1], e.lngLat.lat),
        ]
        const [w, s, e2, n] = bbox
        ;(map.getSource('aoi') as maplibregl.GeoJSONSource)?.setData({
          type: 'FeatureCollection', features: [{ type: 'Feature', properties: {},
            geometry: { type: 'Polygon', coordinates: [[[w,s],[e2,s],[e2,n],[w,n],[w,s]]] },
          }],
        })
        onAoiDrawnRef.current(bboxToWkt(bbox), bbox)
      }
    })

    map.on('mousemove', (e) => {
      const pt: [number, number] = [e.lngLat.lng, e.lngLat.lat]
      mousePosRef.current = pt
      onMouseMove?.({ lat: e.lngLat.lat, lng: e.lngLat.lng })

      // Raster pixel lookup
      const ov = rasterOverlayRef.current
      if (ov) {
        const [W, S, E, N] = ov.bounds
        const lng = e.lngLat.lng, lat = e.lngLat.lat
        if (lng >= W && lng <= E && lat >= S && lat <= N) {
          // Project to Mercator for correct pixel lookup (PNG is in EPSG:3857)
          const R = 6378137
          const toMercX = (lon: number) => lon * Math.PI / 180 * R
          const toMercY = (la: number) => Math.log(Math.tan(Math.PI / 4 + la * Math.PI / 360)) * R
          const mX = toMercX(lng), mW = toMercX(W), mE = toMercX(E)
          const mY = toMercY(lat), mN = toMercY(N), mS = toMercY(S)
          const col = Math.floor((mX - mW) / (mE - mW) * ov.width)
          const row = Math.floor((mN - mY) / (mN - mS) * ov.height)
          const val = ov.pixelData[row * ov.width + col]
          onRasterPixelRef.current?.((ov.nodata !== null && val === ov.nodata) ? null : val)
        } else {
          onRasterPixelRef.current?.(null)
        }
      }

      // Box preview
      if (boxStartRef.current && drawModeRef.current === 'box') {
        const [w, e2] = boxStartRef.current[0] < pt[0]
          ? [boxStartRef.current[0], pt[0]] : [pt[0], boxStartRef.current[0]]
        const [s, n]  = boxStartRef.current[1] < pt[1]
          ? [boxStartRef.current[1], pt[1]] : [pt[1], boxStartRef.current[1]]
        ;(map.getSource('box-preview') as maplibregl.GeoJSONSource)?.setData({
          type: 'FeatureCollection', features: [{
            type: 'Feature', properties: {},
            geometry: { type: 'Polygon', coordinates: [[[w,s],[e2,s],[e2,n],[w,n],[w,s]]] },
          }],
        })
      }

      // Polygon: update cursor edge
      if (drawModeRef.current === 'polygon' && polyPointsRef.current.length > 0) {
        updatePolyPreview(polyPointsRef.current, pt)
      }
    })

    map.on('mouseleave', () => onMouseMove?.(null))


    // ── Polygon: single click adds vertex ─────────────────────────────────
    map.on('click', (e) => {
      if (drawModeRef.current !== 'polygon') return
      const pt: [number, number] = [e.lngLat.lng, e.lngLat.lat]
      polyPointsRef.current = [...polyPointsRef.current, pt]
      updatePolyPreview(polyPointsRef.current, mousePosRef.current)
    })

    // ── Polygon: double-click finishes ────────────────────────────────────
    map.on('dblclick', (e) => {
      if (drawModeRef.current !== 'polygon') return
      e.preventDefault()
      // dblclick fires after 2 clicks — remove the last click point added by the second click
      const pts = polyPointsRef.current.slice(0, -1)
      polyPointsRef.current = []
      clearPolyPreview()
      if (pts.length < 3) return

      const coords = [...pts, pts[0]]  // close ring
      const feature: GeoJSON.Feature = {
        type: 'Feature', properties: {},
        geometry: { type: 'Polygon', coordinates: [coords] },
      }
      const wkt  = geometryToWkt(feature.geometry)
      const bbox = getGeometryBbox(feature.geometry)
      // Show shape immediately; zoom handled by React [aoi, aoiGeojson] effect
      ;(map.getSource('aoi') as maplibregl.GeoJSONSource)?.setData({
        type: 'FeatureCollection', features: [feature],
      })
      onAoiDrawnRef.current(wkt, bbox, feature)
    })

    // ── Pin: single click ─────────────────────────────────────────────────
    map.on('click', (e) => {
      if (drawModeRef.current !== 'pin') return
      const pt: [number, number] = [e.lngLat.lng, e.lngLat.lat]
      const feature: GeoJSON.Feature = {
        type: 'Feature', properties: {},
        geometry: { type: 'Point', coordinates: pt },
      }
      const wkt  = `POINT (${pt[0]} ${pt[1]})`
      const bbox: Bbox = [pt[0] - 0.1, pt[1] - 0.1, pt[0] + 0.1, pt[1] + 0.1]
      // Show pin immediately
      ;(map.getSource('aoi') as maplibregl.GeoJSONSource)?.setData(EMPTY_FC)
      ;(map.getSource('pin') as maplibregl.GeoJSONSource)?.setData({
        type: 'FeatureCollection', features: [feature],
      })
      onAoiDrawnRef.current(wkt, bbox, feature)
    })

    mapRef.current = map
    return () => {
      window.removeEventListener('mousemove', onPanMove)
      window.removeEventListener('mouseup',   onPanUp)
      map.remove()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Cursor on draw mode change ────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    map.getCanvas().style.cursor = drawMode ? 'crosshair' : 'default'
    // Cancel any in-progress polygon when mode changes away
    if (drawMode !== 'polygon') {
      polyPointsRef.current = []
      ;(map.getSource('poly-drawing') as maplibregl.GeoJSONSource | undefined)?.setData(EMPTY_FC)
    }
    if (drawMode !== 'box') {
      boxStartRef.current = null
      ;(map.getSource('box-preview') as maplibregl.GeoJSONSource | undefined)?.setData(EMPTY_FC)
    }
  }, [drawMode])

  // ── Basemap swap ──────────────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current
    if (!map?.isStyleLoaded()) return

    const bm = BASEMAP_TILES[basemap]

    // update base tiles
    ;(map.getSource('basemap') as any)?.setTiles(bm.tiles)

    // remove previous overlay
    if (map.getLayer('basemap-overlay')) map.removeLayer('basemap-overlay')
    if (map.getSource('basemap-overlay')) map.removeSource('basemap-overlay')

    // add overlay if satellite has labels
    if (bm.overlay) {
      map.addSource('basemap-overlay', {
        type: 'raster',
        tiles: bm.overlay,
        tileSize: 256,
      })

      map.addLayer(
        {
          id: 'basemap-overlay',
          type: 'raster',
          source: 'basemap-overlay',
        },
        'footprints-line' // keep overlay under footprints
      )
    }
  }, [basemap])

  // ── Footprints ────────────────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current
    if (!map?.isStyleLoaded()) return
    ;(map.getSource('footprints') as maplibregl.GeoJSONSource | undefined)
      ?.setData(footprints ?? EMPTY_FC)
  }, [footprints])

  // ── Footprint opacity ─────────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current
    if (!map?.isStyleLoaded()) return
    map.setPaintProperty('footprints-line', 'line-opacity', footprintOpacity)
  }, [footprintOpacity])

  // ── Raster overlay ────────────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    const applyOverlay = () => {
      try {
        if (map.getLayer('raster-overlay')) map.removeLayer('raster-overlay')
        if (map.getSource('raster-overlay')) map.removeSource('raster-overlay')

        if (!rasterOverlay) return

        const [W, S, E, N] = rasterOverlay.bounds


        if (!isFinite(W) || !isFinite(S) || !isFinite(E) || !isFinite(N) || W >= E || S >= N) {
          console.error('[InSARHub] invalid raster overlay bounds — skipping:', rasterOverlay.bounds)
          return
        }

        map.addSource('raster-overlay', {
          type: 'image', url: rasterOverlay.url,
          coordinates: [[W, N], [E, N], [E, S], [W, S]],
        } as any)
        map.addLayer({ id: 'raster-overlay', type: 'raster', source: 'raster-overlay',
          paint: { 'raster-opacity': 0.85, 'raster-resampling': 'nearest' } }, 'ts-click-ring')
        map.fitBounds([[W, S], [E, N]], { padding: 40, duration: 0 })
      } catch (err) {
        console.error('[InSARHub] raster-overlay error:', err)
      }
    }

    if (map.isStyleLoaded()) {
      applyOverlay()
    } else {
      map.once('load', applyOverlay)
      return () => { map.off('load', applyOverlay) }
    }
  }, [rasterOverlay])

  // ── Timeseries click marker ──────────────────────────────────────────────
  useEffect(() => {
    const src = mapRef.current?.getSource('ts-click') as maplibregl.GeoJSONSource | undefined
    if (!src) return
    if (tsClickPoint) {
      src.setData({ type: 'FeatureCollection', features: [{
        type: 'Feature', properties: {},
        geometry: { type: 'Point', coordinates: tsClickPoint },
      }]})
    } else {
      src.setData(EMPTY_FC)
    }
  }, [tsClickPoint])

  // ── Finished AOI display ──────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current
    if (!map?.isStyleLoaded()) return

    const aoiSrc = map.getSource('aoi') as maplibregl.GeoJSONSource | undefined
    const pinSrc = map.getSource('pin') as maplibregl.GeoJSONSource | undefined

    // Pin mode
    if (aoiGeojson?.geometry.type === 'Point') {
      aoiSrc?.setData(EMPTY_FC)
      pinSrc?.setData({ type: 'FeatureCollection', features: [aoiGeojson] })
      const [lng, lat] = aoiGeojson.geometry.coordinates as number[]
      map.flyTo({ center: [lng, lat], zoom: 8 })
      return
    }

    pinSrc?.setData(EMPTY_FC)

    // Drawn polygon from polygon tool or shapefile
    if (aoiGeojson) {
      aoiSrc?.setData({ type: 'FeatureCollection', features: [aoiGeojson] })
      const bbox = getGeometryBbox(aoiGeojson.geometry)
      if (map.isStyleLoaded()) map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 60 })
      return
    }

    // Box / typed coords — use aoi bbox
    if (!aoi) { aoiSrc?.setData(EMPTY_FC); return }
    const [w, s, e, n] = aoi
    aoiSrc?.setData({ type: 'FeatureCollection', features: [{
      type: 'Feature', properties: {},
      geometry: { type: 'Polygon', coordinates: [[[w,s],[e,s],[e,n],[w,n],[w,s]]] },
    }]})
  }, [aoi, aoiGeojson])

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
}