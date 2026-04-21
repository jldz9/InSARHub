// NetworkEditor.tsx — interferogram pair network editor, Pixi.js v8 renderer.
// X-axis = acquisition date, Y-axis = perpendicular baseline.
// Click an edge to toggle it active / removed. Scroll to zoom. Drag to pan.

import React, { useEffect, useRef, useState, useCallback } from 'react'
import {
  Application, Container, Graphics, Text, TextStyle, Point, Rectangle,
} from 'pixi.js'

const API = import.meta.env.DEV ? 'http://localhost:8000' : ''

// ── Types ─────────────────────────────────────────────────────────────────────

interface Theme {
  bg: string; bg2: string; border: string; text: string
  textMuted: string; accent: string; inputBg: string; inputBorder: string
}

interface RawNode { id: string; date: string; bperp: number }
interface LayoutNode extends RawNode { x: number; y: number }

interface Edge {
  ref: string; sec: string
  active: boolean
  dt: number        // temporal baseline (days)
  bperpDiff: number // perp baseline difference (m)
}

interface StackData { nodes: RawNode[]; pairs: [string, string][] }

interface Props {
  theme: Theme
  folderPath: string
  onClose: () => void
  onSaved: () => void
  initParamsOpen?: boolean   // auto-open the Parameters dialog (e.g. no pairs yet)
  overrideDataUrl?: string   // if set, fetch network data from this URL instead
  readOnly?: boolean         // hide save/edit controls
  saveUrl?: string           // if set, POST active pairs here instead of default save endpoint
  analyzerType?: string      // if set with saveUrl, Parameters shows modify_network config
}

interface LayoutMeta {
  tMin: number; tMax: number   // ms timestamps
  bMin: number; bMax: number   // perpendicular baseline metres
  iW: number;  iH: number      // inner plot dimensions at layout time
}

interface PixiState {
  app: Application
  world: Container
  edgeGfx: Graphics
  nodeGfx: Graphics
  previewGfx: Graphics   // drag-to-create pair preview line
  labelCtr: Container
  axisGfx: Graphics    // screen-space tick lines
  xLabels: Text[]      // pool of date tick labels (x-axis)
  yLabels: Text[]      // pool of baseline tick labels (y-axis)
  axisTitleX: Text
  axisTitleY: Text
  isDragging: boolean
  didDrag: boolean
  dragStart: { x: number; y: number }
  worldStart: { x: number; y: number }
}

// ── Geometry helpers ──────────────────────────────────────────────────────────

/** Parse a CSS hex color (#rrggbb) to a Pixi number. */
function cssHex(css: string): number {
  return parseInt(css.replace('#', ''), 16)
}

/** True when the background is perceptually dark. */
function isDark(bg: string): boolean {
  const h = bg.replace('#', '')
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16)
  return (r * 299 + g * 587 + b * 114) / 1000 < 128
}

function daysBetween(a: string, b: string): number {
  if (!a || !b) return 0
  return Math.abs((new Date(b).getTime() - new Date(a).getTime()) / 86_400_000)
}

function ptSegDist(px: number, py: number,
                   ax: number, ay: number,
                   bx: number, by: number): number {
  const dx = bx - ax, dy = by - ay
  const len2 = dx * dx + dy * dy
  if (len2 === 0) return Math.hypot(px - ax, py - ay)
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2))
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy))
}

const PAD = { l: 60, r: 20, t: 24, b: 44 }

function computeLayout(rawNodes: RawNode[], W: number, H: number): LayoutNode[] {
  if (!rawNodes.length) return []
  const times  = rawNodes.map(n => new Date(n.date).getTime())
  const bperps = rawNodes.map(n => n.bperp)
  const tMin = Math.min(...times),  tMax = Math.max(...times)
  const bMin = Math.min(...bperps), bMax = Math.max(...bperps)
  const tRange = tMax - tMin || 1
  const bRange = bMax - bMin || 1
  const iW = W - PAD.l - PAD.r
  const iH = H - PAD.t - PAD.b
  return rawNodes.map(n => ({
    ...n,
    x: PAD.l + (new Date(n.date).getTime() - tMin) / tRange * iW,
    y: PAD.t + iH - (n.bperp - bMin) / bRange * iH,
  }))
}

/** Temporal-baseline → 0xRRGGBB colour (warm short / cool long). */
/** HSL → RGB (all inputs/outputs 0–1 except h which is 0–360). */
/**
 * Quality-risk colour ramp: blue (0 = good) → yellow (0.5) → red (1 = bad).
 * Matches the Python _QUALITY_CMAP used in plot_pair_network.
 */
// Two score scales:
//   Coherence mode (analyzer): 0–1 float  — Good ≥0.6, Risky 0.3–0.6, Bad <0.3
//   Quality mode (pair quality): 0–100 int — Good ≥60,  Risky 30–59,   Bad <30
function qualityCategory(score: number): 'good' | 'risky' | 'bad' {
  if (score <= 1) {
    if (score >= 0.6) return 'good'
    if (score >= 0.3) return 'risky'
    return 'bad'
  }
  if (score >= 60) return 'good'
  if (score >= 30) return 'risky'
  return 'bad'
}

const _CAT_HEX  = { good: 0x4caf50, risky: 0xffc107, bad: 0xf44336 } as const
const _CAT_CSS  = { good: '#4caf50', risky: '#ffc107', bad: '#f44336' } as const
const _CAT_LABEL = { good: 'Good',   risky: 'Risky',   bad: 'Bad'    } as const

function qualityHex(score: number): number {
  return _CAT_HEX[qualityCategory(score)]
}

function qualityCSS(score: number, alpha: number): string {
  const hex = _CAT_CSS[qualityCategory(score)]
  // parse hex to rgba
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return `rgba(${r},${g},${b},${alpha})`
}

/** Return quality score (1=good, 0=bad) for an edge, or null if not yet loaded. */
function edgeScore(e: Edge, quality: Record<string, number> | null): number | null {
  if (!quality) return null
  const v = quality[`${e.ref}:${e.sec}`] ?? quality[`${e.sec}:${e.ref}`]
  return v ?? null
}

const _UNSCORED_HEX = 0x888888
const _UNSCORED_CSS = '#888888'

/** Draw a dashed line on a Graphics object (all segments, then one stroke call). */
function dashLine(
  g: Graphics, x1: number, y1: number, x2: number, y2: number,
  dashLen: number, gapLen: number,
): void {
  const len = Math.hypot(x2 - x1, y2 - y1)
  if (len === 0) return
  const nx = (x2 - x1) / len, ny = (y2 - y1) / len
  let d = 0, drawing = true
  while (d < len) {
    const seg = Math.min(drawing ? dashLen : gapLen, len - d)
    if (drawing) {
      g.moveTo(x1 + nx * d, y1 + ny * d)
        .lineTo(x1 + nx * (d + seg), y1 + ny * (d + seg))
    }
    d += seg
    drawing = !drawing
  }
}

// ── Main component ────────────────────────────────────────────────────────────

export function NetworkEditor({ theme: t, folderPath, onClose, onSaved, initParamsOpen = false, overrideDataUrl, readOnly = false, saveUrl, analyzerType }: Props) {
  // React UI state
  const [stacks,      setStacks]      = useState<Record<string, StackData> | null>(null)
  const [activeKey,   setActiveKey]   = useState('')
  const [activeCount, setActiveCount] = useState(0)
  const [totalCount,  setTotalCount]  = useState(0)
  const [saving,      setSaving]      = useState(false)
  const [error,       setError]       = useState('')
  const [dbStatus,    setDbStatus]    = useState<'idle' | 'building' | 'ready' | 'error'>('idle')
  const [hovEdge,     setHovEdge]     = useState<Edge | null>(null)
  const [hovNode,     setHovNode]     = useState<LayoutNode | null>(null)
  const [mousePos,    setMousePos]    = useState<{ x: number; y: number } | null>(null)

  // Pair-selection parameters (mirrors SelectPairs)
  const [paramsOpen,   setParamsOpen]   = useState(initParamsOpen)
  const [dtTargets,           setDtTargets]           = useState('6, 12, 24, 36, 48, 72, 96')
  const [dtTol,               setDtTol]               = useState(3)
  const [dtMax,               setDtMax]               = useState(120)
  const [pbMax,               setPbMax]               = useState(150)
  const [minDegree,           setMinDegree]           = useState(3)
  const [maxDegree,           setMaxDegree]           = useState(5)
  const [forceConnect,        setForceConnect]        = useState(true)
  const [avoidLowQuality,     setAvoidLowQuality]     = useState(true)
  const [snowThreshold,       setSnowThreshold]       = useState(0.5)
  const [precipMmThreshold,   setPrecipMmThreshold]   = useState(25.0)
  const [updating,     setUpdating]     = useState(false)
  const [updateMsg,    setUpdateMsg]    = useState('')

  // modify_network parameters (MintPy mode — only used when saveUrl + analyzerType set)
  const mintpyMode = !!(saveUrl && analyzerType)
  const [mnTempBaseMax,    setMnTempBaseMax]    = useState('auto')
  const [mnPerpBaseMax,    setMnPerpBaseMax]    = useState('auto')
  const [mnStartDate,      setMnStartDate]      = useState('auto')
  const [mnEndDate,        setMnEndDate]        = useState('auto')
  const [mnExcludeDate,    setMnExcludeDate]    = useState('auto')
  const [mnMinCoherence,   setMnMinCoherence]   = useState('auto')
  const [mnCohBased,       setMnCohBased]       = useState('auto')
  const [mnKeepMST,        setMnKeepMST]        = useState('auto')
  const [mnRunning,        setMnRunning]        = useState(false)
  const [mnMsg,            setMnMsg]            = useState('')

  // Load current modify_network config from folder on open (MintPy mode)
  useEffect(() => {
    if (!mintpyMode) return
    fetch(`${API}/api/folder-config?path=${encodeURIComponent(folderPath)}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        const cfg = d?.analyzer?.config ?? {}
        if (cfg.network_tempBaseMax !== undefined) setMnTempBaseMax(String(cfg.network_tempBaseMax))
        if (cfg.network_perpBaseMax !== undefined) setMnPerpBaseMax(String(cfg.network_perpBaseMax))
        if (cfg.network_startDate   !== undefined) setMnStartDate(String(cfg.network_startDate))
        if (cfg.network_endDate     !== undefined) setMnEndDate(String(cfg.network_endDate))
        if (cfg.network_excludeDate !== undefined) setMnExcludeDate(String(cfg.network_excludeDate))
        if (cfg.network_minCoherence!== undefined) setMnMinCoherence(String(cfg.network_minCoherence))
        if (cfg.network_coherenceBased !== undefined) setMnCohBased(String(cfg.network_coherenceBased))
        if (cfg.network_keepMinSpanTree!== undefined) setMnKeepMST(String(cfg.network_keepMinSpanTree))
      })
      .catch(() => {})
  }, [mintpyMode, folderPath])
  const [qualityScores,    setQualityScores]     = useState<Record<string, number> | null>(null)
  const [qualityFactors,   setQualityFactors]    = useState<Record<string, any> | null>(null)
  const qualityFactorsRef  = useRef<Record<string, any> | null>(null)
  // Scores/factors for manually drawn edges — persisted across quality re-fetches
  const manualScoresRef  = useRef<Record<string, number>>({})
  const manualFactorsRef = useRef<Record<string, any>>({})

  // Refs — mutated without re-renders
  const nodesRef      = useRef<LayoutNode[]>([])
  const edgesRef      = useRef<Edge[]>([])
  const hoveredRef    = useRef(-1)
  const hovNodeRef    = useRef(-1)
  const drawingFromRef = useRef(-1)                              // node index drag started from
  const drawCursorRef  = useRef<{ x: number; y: number } | null>(null)  // cursor in world space
  const pixiRef       = useRef<PixiState | null>(null)
  const containerRef  = useRef<HTMLDivElement>(null)
  const layoutMetaRef = useRef<LayoutMeta | null>(null)
  const themeRef      = useRef(t)

  useEffect(() => {
    themeRef.current = t
    const ps = pixiRef.current
    if (!ps) return
    ps.app.renderer.background.color = cssHex(t.bg)
    // Update axis label text colors to match theme
    const dark = isDark(t.bg)
    const tickColor  = dark ? 0xaaaaaa : 0x444444
    const titleColor = dark ? 0x888888 : 0x555555
    ps.xLabels.forEach(l => (l.style.fill = tickColor))
    ps.yLabels.forEach(l => (l.style.fill = tickColor))
    ps.axisTitleX.style.fill = titleColor
    ps.axisTitleY.style.fill = titleColor
  }, [t])

  // ── Redraw (called after any state mutation in refs) ─────────────────────────
  // Pixi ticks at 60 fps automatically; we just need to update the Graphics objects.

  const redraw = useCallback(() => {
    const ps = pixiRef.current
    if (!ps) return
    const { world, edgeGfx, nodeGfx, labelCtr } = ps
    const nodes  = nodesRef.current
    const edges  = edgesRef.current
    const hov    = hoveredRef.current
    const nodeMap = new Map(nodes.map(n => [n.id, n]))

    const _edgeScore = (e: Edge): number | null => edgeScore(e, qualityRef.current)

    // s = current zoom scale; divide all screen-space sizes by s so they stay
    // constant in CSS pixels regardless of zoom level.
    const s = world.scale.x

    // ── Grid lines ─────────────────────────────────────────────────────────────
    const W = ps.app.screen.width
    const H = ps.app.screen.height
    const dark = isDark(themeRef.current.bg)
    const gridColor  = dark ? 0xffffff : 0x000000
    const nodeColor  = dark ? 0x3dd6f5 : 0x1976d2
    const hovColor   = dark ? 0xffd54f : 0xff8f00
    const edgeHovClr = dark ? 0xffffff : 0x000000

    const gridGfx = world.children[0] as Graphics
    gridGfx.clear()
    for (let i = 0; i <= 8; i++) {
      const y = PAD.t + (H - PAD.t - PAD.b) * (i / 8)
      gridGfx.moveTo(0, y).lineTo(W, y)
    }
    gridGfx.stroke({ width: 1 / s, color: gridColor, alpha: 0.04 })

    // ── Edges ───────────────────────────────────────────────────────────────────
    edgeGfx.clear()
    const hovN = hovNodeRef.current
    const hovNodeId = hovN >= 0 ? nodes[hovN]?.id : null

    // When a node is hovered, collect its connected edge indices for highlighting
    const connectedEdgeSet = new Set<number>()
    const connectedNodeIds = new Set<string>()
    if (hovNodeId) {
      for (let i = 0; i < edges.length; i++) {
        const e = edges[i]
        if (e.active && (e.ref === hovNodeId || e.sec === hovNodeId)) {
          connectedEdgeSet.add(i)
          connectedNodeIds.add(e.ref === hovNodeId ? e.sec : e.ref)
        }
      }
    }

    const hasNodeHov = hovNodeId !== null
    const removedPaths: [number, number, number, number][] = []

    for (let i = 0; i < edges.length; i++) {
      const e  = edges[i]
      const n1 = nodeMap.get(e.ref), n2 = nodeMap.get(e.sec)
      if (!n1 || !n2) continue

      if (i === hov) {
        edgeGfx.moveTo(n1.x, n1.y).lineTo(n2.x, n2.y)
        edgeGfx.stroke({ width: 2.5 / s, color: edgeHovClr, alpha: 1 })
      } else if (hasNodeHov && connectedEdgeSet.has(i)) {
        // Connected to hovered node — brighter and thicker
        edgeGfx.moveTo(n1.x, n1.y).lineTo(n2.x, n2.y)
        edgeGfx.stroke({ width: 2.5 / s, color: (() => { const sc = _edgeScore(e); return sc === null ? _UNSCORED_HEX : qualityHex(sc) })(), alpha: 1 })
      } else if (e.active) {
        edgeGfx.moveTo(n1.x, n1.y).lineTo(n2.x, n2.y)
        // Dim unrelated edges when a node is hovered
        edgeGfx.stroke({ width: 1.5 / s, color: (() => { const sc = _edgeScore(e); return sc === null ? _UNSCORED_HEX : qualityHex(sc) })(), alpha: hasNodeHov ? 0.15 : 0.75 })
      } else {
        removedPaths.push([n1.x, n1.y, n2.x, n2.y])
      }
    }
    // Draw all removed edges as dashed red lines (dash lengths in world-space)
    const dashLen = 6 / s
    for (const [x1, y1, x2, y2] of removedPaths) {
      dashLine(edgeGfx, x1, y1, x2, y2, dashLen, dashLen)
    }
    if (removedPaths.length > 0) {
      edgeGfx.stroke({ width: 1 / s, color: 0xdc3c3c, alpha: hasNodeHov ? 0.15 : 0.5 })
    }

    // ── Nodes ───────────────────────────────────────────────────────────────────
    nodeGfx.clear()
    const r = 5 / s
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i]
      const isHov      = i === hovN
      const isConnected = connectedNodeIds.has(node.id)
      const dimmed      = hasNodeHov && !isHov && !isConnected
      const fill  = isHov ? hovColor : isConnected ? hovColor : nodeColor
      const ring  = isHov ? 8 / s : isConnected ? 6 / s : r
      nodeGfx.circle(node.x, node.y, ring).fill({ color: fill, alpha: dimmed ? 0.25 : 1 })
      nodeGfx.circle(node.x, node.y, ring).stroke({ width: 1.5 / s, color: edgeHovClr, alpha: isHov ? 1 : isConnected ? 0.9 : dimmed ? 0.15 : 0.7 })
    }

    // ── Labels — keep constant screen size and reposition below (possibly scaled) node ──
    for (const child of labelCtr.children) {
      child.scale.set(1 / s)
    }
    // Re-anchor labels below the (now constant-radius) node
    const nodes_ = nodesRef.current
    let li = 0
    for (const node of nodes_) {
      if (node.date.length < 10) continue
      const lbl = labelCtr.children[li++]
      if (!lbl) break
      lbl.x = node.x - (lbl.width * (1 / s)) / 2
      lbl.y = node.y + (r + 2 / s)
    }

    // ── Drag-to-create preview line ──────────────────────────────────────────
    const { previewGfx } = ps
    previewGfx.clear()
    const drawFrom   = drawingFromRef.current
    const drawCursor = drawCursorRef.current
    if (drawFrom >= 0 && drawCursor) {
      const src = nodes[drawFrom]
      if (src) {
        previewGfx.moveTo(src.x, src.y).lineTo(drawCursor.x, drawCursor.y)
        previewGfx.stroke({ width: 2 / s, color: 0x4caf50, alpha: 0.85 })
        // Ring around valid target node
        const tgt = hovNodeRef.current >= 0 && hovNodeRef.current !== drawFrom ? nodes[hovNodeRef.current] : null
        if (tgt) {
          previewGfx.circle(tgt.x, tgt.y, 10 / s).stroke({ width: 2 / s, color: 0x4caf50, alpha: 1 })
        }
      }
    }
  }, [])


  // ── Build node labels ────────────────────────────────────────────────────────

  const buildLabels = useCallback(() => {
    const ps = pixiRef.current
    if (!ps) return
    const { labelCtr } = ps
    // Destroy old labels
    labelCtr.removeChildren().forEach(c => c.destroy())
    const style = new TextStyle({ fontSize: 12, fill: 0xffffff, fontFamily: 'monospace' })
    for (const node of nodesRef.current) {
      if (node.date.length < 10) continue
      const lbl = new Text({ text: node.date.slice(5), style })
      lbl.alpha = 0.45
      lbl.x = node.x - lbl.width / 2
      lbl.y = node.y + 9
      labelCtr.addChild(lbl)
    }
  }, [])

  // ── Lookup DB scores for edges missing from qualityRef ───────────────────────

  function lookupMissingScores() {
    const edges = edgesRef.current
    const existing = qualityRef.current ?? {}
    const missing = edges.filter(e =>
      !((`${e.ref}:${e.sec}` in existing) || (`${e.sec}:${e.ref}` in existing))
    )
    if (missing.length === 0) return
    const keys = missing.flatMap(e => [`${e.ref}:${e.sec}`, `${e.sec}:${e.ref}`]).join(',')
    fetch(`${API}/api/pair-quality-db/lookup?path=${encodeURIComponent(folderPathRef.current)}&pairs=${encodeURIComponent(keys)}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d?.scores || Object.keys(d.scores).length === 0) return
        qualityRef.current = { ...(qualityRef.current ?? {}), ...d.scores }
        qualityFactorsRef.current = { ...(qualityFactorsRef.current ?? {}), ...(d.factors ?? {}) }
        setQualityScores(prev => ({ ...(prev ?? {}), ...d.scores }))
        setQualityFactors(prev => ({ ...(prev ?? {}), ...(d.factors ?? {}) }))
        redraw()
      })
      .catch(() => {})
  }

  // ── Layout ──────────────────────────────────────────────────────────────────

  const buildLayout = useCallback((stackData: StackData) => {
    const ps = pixiRef.current
    if (!ps) return
    const W = ps.app.screen.width
    const H = ps.app.screen.height
    const layouted  = computeLayout(stackData.nodes, W, H)
    const nodeMap   = new Map(layouted.map(n => [n.id, n]))
    nodesRef.current = layouted

    // Store layout metadata so the axis ticker can map world coords ↔ data values
    const times  = stackData.nodes.map(n => new Date(n.date).getTime())
    const bperps = stackData.nodes.map(n => n.bperp)
    layoutMetaRef.current = {
      tMin: Math.min(...times), tMax: Math.max(...times),
      bMin: Math.min(...bperps), bMax: Math.max(...bperps),
      iW: W - PAD.l - PAD.r, iH: H - PAD.t - PAD.b,
    }

    const edges: Edge[] = stackData.pairs.map(([ref, sec]) => {
      const n1 = nodeMap.get(ref), n2 = nodeMap.get(sec)
      const d12 = `${ref}_${sec}`
      const dropped = droppedPairsRef.current.size > 0 && droppedPairsRef.current.has(d12)
      return {
        ref, sec, active: !dropped,
        dt:        daysBetween(n1?.date ?? '', n2?.date ?? ''),
        bperpDiff: Math.abs((n1?.bperp ?? 0) - (n2?.bperp ?? 0)),
      }
    })
    edgesRef.current = edges
    // Apply coherence override as quality scores so edges are coloured by coherence.
    // API returns "YYYYMMDD_YYYYMMDD" keys; edgeScore() expects "YYYYMMDD:YYYYMMDD".
    if (cohOverrideRef.current) {
      const converted: Record<string, number> = {}
      for (const [k, v] of Object.entries(cohOverrideRef.current)) {
        converted[k.replace('_', ':')] = v
      }
      qualityRef.current = converted
      setQualityScores(converted)
    }
    setActiveCount(edges.filter(e => e.active).length)
    setTotalCount(edges.length)
    hoveredRef.current = -1
    setHovEdge(null)
    // After edges are set, look up DB scores for any pairs not in the quality JSON
    if (!saveUrlRef.current) setTimeout(() => lookupMissingScores(), 0)

    // Reset view
    ps.world.position.set(0, 0)
    ps.world.scale.set(1)

    buildLabels()
    redraw()
  }, [redraw, buildLabels])

  // ── Pixi initialisation ──────────────────────────────────────────────────────

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    let cancelled = false

    const app = new Application()
    app.init({
      backgroundColor: cssHex(themeRef.current.bg),
      antialias: true,
      autoDensity: true,   // <-- handles DPR: CSS pixels for events, device pixels for rendering
      resolution: window.devicePixelRatio || 1,
      resizeTo: container,  // auto-resize canvas with container
    }).then(() => {
      if (cancelled) { app.destroy(true); return }

      // ---- Canvas setup ----
      const canvas = app.canvas as HTMLCanvasElement
      canvas.style.display = 'block'
      canvas.style.width   = '100%'
      canvas.style.height  = '100%'
      canvas.style.cursor  = 'grab'
      container.appendChild(canvas)

      // ---- Scene graph ----
      const world = new Container()
      const gridGfx  = new Graphics()   // child[0] — read in redraw()
      const edgeGfx  = new Graphics()
      const nodeGfx  = new Graphics()
      const labelCtr = new Container()
      const previewGfx = new Graphics()
      world.addChild(gridGfx, edgeGfx, nodeGfx, labelCtr, previewGfx)
      app.stage.addChild(world)

      // ---- Axis overlay (screen-space, updated every ticker tick) ----
      const axisGfx = new Graphics()
      const tickLblStyle  = new TextStyle({ fontSize: 13, fill: 0xaaaaaa, fontFamily: 'monospace' })
      const titleLblStyle = new TextStyle({ fontSize: 14, fill: 0x888888, fontFamily: 'system-ui, sans-serif' })
      const xLabels: Text[] = Array.from({ length: 14 }, () => {
        const t = new Text({ text: '', style: tickLblStyle }); t.visible = false
        app.stage.addChild(t); return t
      })
      const yLabels: Text[] = Array.from({ length: 10 }, () => {
        const t = new Text({ text: '', style: tickLblStyle }); t.visible = false
        app.stage.addChild(t); return t
      })
      const axisTitleX = new Text({ text: 'Acquisition Date', style: titleLblStyle })
      const axisTitleY = new Text({ text: '⊥ baseline (m)', style: titleLblStyle })
      axisTitleX.alpha = 0.35; axisTitleY.alpha = 0.35
      app.stage.addChild(axisGfx, axisTitleX, axisTitleY)

      // Ticker: redraw axes every frame so they stay in sync with pan/zoom
      const DAY_MS = 86_400_000
      const X_INTERVALS = [15,30,60,90,180,365,730,1825].map(d => d * DAY_MS)
      const Y_INTERVALS = [5,10,20,50,100,200,500,1000,2000,5000]

      app.ticker.add(() => {
        const meta = layoutMetaRef.current
        const W = app.screen.width
        const H = app.screen.height
        axisGfx.clear()

        if (!meta) return

        const { tMin, tMax, bMin, bMax, iW, iH } = meta
        const tRange = tMax - tMin || 1
        const bRange = bMax - bMin || 1
        const s  = world.scale.x
        const wx = world.x, wy = world.y
        const axDark      = isDark(themeRef.current.bg)
        const axisLineClr = axDark ? 0xffffff : 0x000000
        const axisLineA   = axDark ? 0.15 : 0.2
        const tickClr     = axDark ? 0xffffff : 0x000000
        const tickA       = axDark ? 0.3 : 0.35

        // Helpers: data value → screen pixel
        const sx = (t: number) => (PAD.l + (t - tMin) / tRange * iW) * s + wx
        const sy = (b: number) => (PAD.t + iH - (b - bMin) / bRange * iH) * s + wy

        // Axis lines fixed in screen space — never move with pan/zoom
        const axisX = PAD.l          // fixed screen x for Y axis
        const axisY = H - PAD.b      // fixed screen y for X axis

        axisGfx.moveTo(axisX, PAD.t).lineTo(axisX, axisY)
        axisGfx.moveTo(axisX, axisY).lineTo(W - PAD.r, axisY)
        axisGfx.stroke({ width: 1, color: axisLineClr, alpha: axisLineA })

        // ── X axis: date ticks — positions move with pan, labels show current values ──
        // Visible time range: invert sx() at screen x = axisX and W-PAD.r
        const visT0 = tMin + ((axisX      - wx) / s - PAD.l) / iW * tRange
        const visT1 = tMin + ((W - PAD.r  - wx) / s - PAD.l) / iW * tRange
        const xSpan = visT1 - visT0
        const xIv   = X_INTERVALS.find(iv => xSpan / iv >= 2 && xSpan / iv <= 10)
                   ?? X_INTERVALS[X_INTERVALS.length - 1]
        const t0    = Math.ceil(visT0 / xIv) * xIv
        let xi = 0
        for (let t = t0; t <= visT1 && xi < xLabels.length; t += xIv) {
          const px = sx(t)
          if (px < axisX || px > W - PAD.r) continue
          axisGfx.moveTo(px, axisY).lineTo(px, axisY + 5)
          const lbl = xLabels[xi++]
          const d = new Date(t)
          lbl.text = xIv < 60 * DAY_MS
            ? `${String(d.getUTCMonth()+1).padStart(2,'0')}/${String(d.getUTCDate()).padStart(2,'0')}`
            : `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}`
          lbl.x = px - lbl.width / 2
          lbl.y = axisY + 7
          lbl.visible = true
        }
        for (; xi < xLabels.length; xi++) xLabels[xi].visible = false
        axisGfx.stroke({ width: 1, color: tickClr, alpha: tickA })

        // ── Y axis: baseline ticks — positions move with pan, labels show current values ──
        // Visible baseline range: invert sy() at screen y = PAD.t and axisY
        const visB1 = bMin + (PAD.t + iH - (PAD.t  - wy) / s) / iH * bRange
        const visB0 = bMin + (PAD.t + iH - (axisY  - wy) / s) / iH * bRange
        const bSpan = Math.abs(visB1 - visB0)
        const yIv   = Y_INTERVALS.find(iv => bSpan / iv >= 2 && bSpan / iv <= 8)
                   ?? Y_INTERVALS[Y_INTERVALS.length - 1]
        const b0    = Math.ceil(Math.min(visB0, visB1) / yIv) * yIv
        let yi = 0
        for (let b = b0; b <= Math.max(visB0, visB1) && yi < yLabels.length; b += yIv) {
          const py = sy(b)
          if (py < PAD.t || py > axisY) continue
          axisGfx.moveTo(axisX - 5, py).lineTo(axisX, py)
          const lbl = yLabels[yi++]
          lbl.text = String(Math.round(b))
          lbl.x = axisX - lbl.width - 7
          lbl.y = py - lbl.height / 2
          lbl.visible = lbl.x >= 0
        }
        for (; yi < yLabels.length; yi++) yLabels[yi].visible = false
        axisGfx.stroke({ width: 1, color: tickClr, alpha: tickA })

        // ── Axis titles (fixed positions) ────────────────────────────────────
        axisTitleX.x = axisX + (W - PAD.r - axisX) / 2 - axisTitleX.width / 2
        axisTitleX.y = H - 13
        axisTitleY.x = 2
        axisTitleY.y = PAD.t + (axisY - PAD.t) / 2 - axisTitleY.height / 2
      })

      // ---- Enable stage events (for pan/hover) ----
      app.stage.eventMode = 'static'
      app.stage.hitArea   = new Rectangle(0, 0, 1e9, 1e9)
      // Update hitArea on resize so events still reach the stage
      app.renderer.on('resize', () => {
        ;(app.stage.hitArea as Rectangle).width  = app.screen.width
        ;(app.stage.hitArea as Rectangle).height = app.screen.height
      })

      const ps: PixiState = {
        app, world, edgeGfx, nodeGfx, previewGfx, labelCtr,
        axisGfx, xLabels, yLabels, axisTitleX, axisTitleY,
        isDragging: false, didDrag: false,
        dragStart: { x: 0, y: 0 },
        worldStart: { x: 0, y: 0 },
      }
      pixiRef.current = ps

      // If data fetch resolved before Pixi finished init, build layout now.
      // (The fetch useEffect checked pixiRef.current and skipped — catch up here.)
      const initKey    = activeKeyRef.current
      const initStacks = stacksRef.current
      if (initKey && initStacks && initStacks[initKey]) {
        buildLayout(initStacks[initKey])
      } else {
        redraw()
      }

      // Suppress context menu so right-click drag works without interruption
      canvas.addEventListener('contextmenu', (ev) => ev.preventDefault())

      // ---- Pointer events ----
      // Right-click (button 2) = pan; left-click (button 0) = select/toggle edge

      app.stage.on('pointerdown', (ev) => {
        if (ev.button === 2) {
          // Right-click: start pan
          ps.isDragging = true
          ps.didDrag    = false
          ps.dragStart  = { x: ev.globalX, y: ev.globalY }
          ps.worldStart = { x: world.x, y: world.y }
          canvas.style.cursor = 'grabbing'
          return
        }
        if (ev.button === 0 && !saveUrlRef.current) {
          // Left-click on a node: start drag-to-create (disabled in mintpy mode)
          const wp  = world.toLocal(new Point(ev.globalX, ev.globalY))
          const thr = 8 / world.scale.x
          const nodes = nodesRef.current
          for (let i = 0; i < nodes.length; i++) {
            if (Math.hypot(wp.x - nodes[i].x, wp.y - nodes[i].y) < thr) {
              drawingFromRef.current = i
              drawCursorRef.current  = { x: wp.x, y: wp.y }
              canvas.style.cursor = 'crosshair'
              redraw()
              return
            }
          }
        }
      })

      app.stage.on('pointermove', (ev) => {
        if (ps.isDragging) {
          const dx = ev.globalX - ps.dragStart.x
          const dy = ev.globalY - ps.dragStart.y
          if (Math.abs(dx) + Math.abs(dy) > 3) ps.didDrag = true
          world.x = ps.worldStart.x + dx
          world.y = ps.worldStart.y + dy
          return
        }

        const wp    = world.toLocal(new Point(ev.globalX, ev.globalY))
        const thr   = 8 / world.scale.x
        const nodes = nodesRef.current
        const edges = edgesRef.current
        setMousePos({ x: ev.globalX, y: ev.globalY })

        if (drawingFromRef.current >= 0) {
          // Drag-to-create mode: update preview cursor + highlight target node
          drawCursorRef.current = { x: wp.x, y: wp.y }
          let targetNode = -1
          for (let i = 0; i < nodes.length; i++) {
            if (i === drawingFromRef.current) continue
            if (Math.hypot(wp.x - nodes[i].x, wp.y - nodes[i].y) < thr) { targetNode = i; break }
          }
          if (targetNode !== hovNodeRef.current) {
            hovNodeRef.current = targetNode
            setHovNode(targetNode >= 0 ? nodes[targetNode] : null)
          }
          canvas.style.cursor = targetNode >= 0 ? 'crosshair' : 'crosshair'
          redraw()
          return
        }

        // Normal hover: nodes first, then edges
        let bestNode = -1
        for (let i = 0; i < nodes.length; i++) {
          if (Math.hypot(wp.x - nodes[i].x, wp.y - nodes[i].y) < thr) { bestNode = i; break }
        }
        let bestEdge = -1
        if (bestNode < 0) {
          const nodeMap = new Map(nodes.map(n => [n.id, n]))
          for (let i = 0; i < edges.length; i++) {
            const e = edges[i]
            const n1 = nodeMap.get(e.ref), n2 = nodeMap.get(e.sec)
            if (!n1 || !n2) continue
            if (ptSegDist(wp.x, wp.y, n1.x, n1.y, n2.x, n2.y) < thr) { bestEdge = i; break }
          }
        }
        const changed = bestNode !== hovNodeRef.current || bestEdge !== hoveredRef.current
        if (changed) {
          hovNodeRef.current = bestNode
          hoveredRef.current = bestEdge
          canvas.style.cursor = (bestNode >= 0 || bestEdge >= 0) ? 'pointer' : 'default'
          setHovNode(bestNode >= 0 ? nodes[bestNode] : null)
          setHovEdge(bestEdge >= 0 ? edges[bestEdge] : null)
          redraw()
        }
      })

      app.stage.on('pointerup', (ev) => {
        if (ev.button === 2) {
          ps.isDragging = false
          canvas.style.cursor = 'default'
          return
        }

        if (ev.button === 0) {
          if (drawingFromRef.current >= 0) {
            // Finish drag-to-create
            const fromIdx = drawingFromRef.current
            drawingFromRef.current = -1
            drawCursorRef.current  = null
            const toIdx = hovNodeRef.current
            hovNodeRef.current = -1
            setHovNode(null)

            if (toIdx >= 0 && toIdx !== fromIdx) {
              const nodes = nodesRef.current
              const edges = edgesRef.current
              const src = nodes[fromIdx], tgt = nodes[toIdx]
              const existingIdx = edges.findIndex(e =>
                (e.ref === src.id && e.sec === tgt.id) ||
                (e.ref === tgt.id && e.sec === src.id)
              )
              if (existingIdx >= 0) {
                edges[existingIdx].active = true
              } else {
                edges.push({
                  ref: src.id, sec: tgt.id, active: true,
                  dt: daysBetween(src.date, tgt.date),
                  bperpDiff: Math.abs(src.bperp - tgt.bperp),
                })
                setTotalCount(edges.length)
                // Look up precomputed quality score for the new pair from DB.
                // If DB isn't ready yet, retry once the status shows complete.
                const pairKey = `${src.id}:${tgt.id}`
                const altKey  = `${tgt.id}:${src.id}`
                const applyDragScore = (d: any) => {
                  if (!d?.scores || Object.keys(d.scores).length === 0) return false
                  manualScoresRef.current  = { ...manualScoresRef.current,  ...d.scores }
                  manualFactorsRef.current = { ...manualFactorsRef.current, ...(d.factors ?? {}) }
                  qualityRef.current = { ...(qualityRef.current ?? {}), ...d.scores }
                  qualityFactorsRef.current = { ...(qualityFactorsRef.current ?? {}), ...(d.factors ?? {}) }
                  setQualityScores(prev => ({ ...(prev ?? {}), ...d.scores }))
                  setQualityFactors(prev => ({ ...(prev ?? {}), ...(d.factors ?? {}) }))
                  redraw()
                  return true
                }
                const lookupUrl = `${API}/api/pair-quality-db/lookup?path=${encodeURIComponent(folderPathRef.current)}&pairs=${encodeURIComponent(pairKey)},${encodeURIComponent(altKey)}`
                fetch(lookupUrl)
                  .then(r => r.ok ? r.json() : null)
                  .then(d => {
                    if (applyDragScore(d)) return
                    // DB may still be building — poll status and retry once complete
                    const poll = setInterval(() => {
                      fetch(`${API}/api/pair-quality-db/status?path=${encodeURIComponent(folderPathRef.current)}`)
                        .then(r => r.ok ? r.json() : null)
                        .then(s => {
                          if (!s?.complete) return
                          clearInterval(poll)
                          dbAvailableRef.current = true
                          fetch(lookupUrl).then(r => r.ok ? r.json() : null).then(applyDragScore).catch(() => {})
                        })
                        .catch(() => clearInterval(poll))
                    }, 3000)
                    // Stop polling after 2 minutes regardless
                    setTimeout(() => clearInterval(poll), 120_000)
                  })
                  .catch(() => {})
              }
              setActiveCount(edges.filter(x => x.active).length)
            }
            canvas.style.cursor = 'default'
            redraw()
            return
          }

          // Left-click on edge: toggle
          const wp  = world.toLocal(new Point(ev.globalX, ev.globalY))
          const thr = 8 / world.scale.x
          const nodes   = nodesRef.current
          const edges   = edgesRef.current
          const nodeMap = new Map(nodes.map(n => [n.id, n]))
          for (let i = 0; i < edges.length; i++) {
            const e = edges[i]
            const n1 = nodeMap.get(e.ref), n2 = nodeMap.get(e.sec)
            if (!n1 || !n2) continue
            if (ptSegDist(wp.x, wp.y, n1.x, n1.y, n2.x, n2.y) < thr) {
              edges[i].active = !edges[i].active
              setActiveCount(edges.filter(x => x.active).length)
              setHovEdge({ ...edges[i] })
              redraw()
              break
            }
          }
        }
      })

      app.stage.on('pointerleave', () => {
        ps.isDragging      = false
        hoveredRef.current = -1
        hovNodeRef.current = -1
        drawingFromRef.current = -1
        drawCursorRef.current  = null
        setHovEdge(null)
        setHovNode(null)
        setMousePos(null)
        canvas.style.cursor = 'default'
        redraw()
      })

      // ---- Wheel zoom ----
      canvas.addEventListener('wheel', (ev) => {
        ev.preventDefault()
        const factor   = ev.deltaY < 0 ? 1.12 : 1 / 1.12
        const newScale = Math.max(0.1, Math.min(40, world.scale.x * factor))
        // Zoom around mouse cursor
        const wp = world.toLocal(new Point(ev.offsetX, ev.offsetY))
        world.scale.set(newScale)
        world.x = ev.offsetX - wp.x * newScale
        world.y = ev.offsetY - wp.y * newScale
        redraw()   // recompute scale-compensated edge/node/label sizes
      }, { passive: false })

      // ---- Re-layout on resize ----
      app.renderer.on('resize', () => {
        // Re-layout only if we have data
        if (nodesRef.current.length > 0) {
          const stacksSnap = stacksRef.current
          const key        = activeKeyRef.current
          if (stacksSnap && key && stacksSnap[key]) {
            buildLayout(stacksSnap[key])
          }
        }
      })

      // Initial redraw (empty, but sets background)
      redraw()
    })

    return () => {
      cancelled = true
      if (pixiRef.current) {
        pixiRef.current.app.destroy(true, { children: true })
        pixiRef.current = null
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])  // run once on mount

  // Refs to stacks/activeKey for use inside the resize handler closure
  const stacksRef       = useRef<Record<string, StackData> | null>(null)
  const activeKeyRef    = useRef('')
  const folderPathRef   = useRef(folderPath)
  const saveUrlRef      = useRef(saveUrl)
  useEffect(() => { saveUrlRef.current = saveUrl }, [saveUrl])
  // Quality scores keyed by "ref:sec" — null means not yet loaded
  const qualityRef      = useRef<Record<string, number> | null>(null)
  // Whether the precomputed full pair DB is available for this folder
  const dbAvailableRef  = useRef(false)
  useEffect(() => { stacksRef.current = stacks },       [stacks])
  useEffect(() => { activeKeyRef.current = activeKey }, [activeKey])
  useEffect(() => { folderPathRef.current = folderPath }, [folderPath])

  // ── Load data ───────────────────────────────────────────────────────────────

  const droppedPairsRef = useRef<Set<string>>(new Set())
  const cohOverrideRef  = useRef<Record<string, number> | null>(null)
  const [reloadTick, setReloadTick] = useState(0)

  useEffect(() => {
    const url = overrideDataUrl ?? `${API}/api/folder-network-data?path=${encodeURIComponent(folderPath)}`
    fetch(url)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
      .then(d => {
        const data: Record<string, StackData> = d.stacks ?? {}
        // Store dropped pairs and coherence from mintpy network response
        droppedPairsRef.current = new Set<string>(d.dropped_pairs ?? [])
        cohOverrideRef.current  = d.coherence ?? null
        // When opened in nodes-only mode, strip all pairs so canvas starts clean
        if (initParamsOpen) {
          for (const key of Object.keys(data)) data[key] = { ...data[key], pairs: [] }
        }
        setStacks(data)
        const first = Object.keys(data)[0] ?? ''
        setActiveKey(first)
        if (first && pixiRef.current) buildLayout(data[first])
      })
      .catch(e => setError(String(e)))
  }, [folderPath, buildLayout, reloadTick])

  // ── Stack switch ─────────────────────────────────────────────────────────────

  useEffect(() => {
    if (!stacks || !activeKey || !stacks[activeKey] || !pixiRef.current) return
    buildLayout(stacks[activeKey])
  }, [activeKey, stacks, buildLayout])

  // ── Fetch quality scores ──────────────────────────────────────────────────────

  useEffect(() => {
    // In mintpy mode coherence values come from the override URL — skip pair-quality fetch
    if (saveUrlRef.current) return
    qualityRef.current = null
    dbAvailableRef.current = false

    // Load scores for selected pairs (fast — from pre-written JSON)
    fetch(`${API}/api/pair-quality?path=${encodeURIComponent(folderPath)}`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
      .then(d => {
        qualityRef.current = { ...(d.scores ?? {}), ...manualScoresRef.current }
        qualityFactorsRef.current = { ...(d.factors ?? {}), ...manualFactorsRef.current }
        setQualityScores({ ...(d.scores ?? {}), ...manualScoresRef.current })
        setQualityFactors({ ...(d.factors ?? {}), ...manualFactorsRef.current })
        redraw()
        lookupMissingScores()
      })
      .catch(() => {})

    // Check if the quality DB is ready; poll while it's still building
    let pollTimer: ReturnType<typeof setInterval> | null = null
    const checkDb = () => {
      fetch(`${API}/api/pair-quality-db/status?path=${encodeURIComponent(folderPath)}`)
        .then(r => r.ok ? r.json() : null)
        .then(d => {
          if (!d?.exists) { setDbStatus('idle'); return }
          if (d.complete) {
            dbAvailableRef.current = true
            setDbStatus('ready')
            if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
          } else {
            setDbStatus('building')
          }
        })
        .catch(() => {})
    }
    checkDb()
    pollTimer = setInterval(checkDb, 4000)
    return () => { if (pollTimer) clearInterval(pollTimer) }
  }, [folderPath, redraw])


  // ── Actions ──────────────────────────────────────────────────────────────────

  function handleReset() {
    edgesRef.current.forEach(e => { e.active = true })
    setActiveCount(edgesRef.current.length)
    hoveredRef.current = -1
    setHovEdge(null)
    redraw()
  }

  function handleFit() {
    const ps = pixiRef.current
    if (!ps) return
    ps.world.position.set(0, 0)
    ps.world.scale.set(1)
  }

  async function handleUpdate() {
    const dtArr = dtTargets.split(',').map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n))
    setUpdating(true); setUpdateMsg('Starting…'); setError('')
    try {
      const res = await fetch(`${API}/api/folder-select-pairs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          folder_path: folderPath,
          dt_targets: dtArr, dt_tol: dtTol,
          dt_max: dtMax, pb_max: pbMax,
          min_degree: minDegree, max_degree: maxDegree,
          force_connect: forceConnect,
          avoid_low_quality_days: avoidLowQuality,
          snow_threshold: snowThreshold,
          precip_mm_threshold: precipMmThreshold,
        }),
      })
      if (!res.ok) throw new Error(await res.text())
      const { job_id } = await res.json()

      // Poll until done
      while (true) {
        await new Promise(r => setTimeout(r, 1500))
        const jr = await fetch(`${API}/api/jobs/${job_id}`)
        if (!jr.ok) throw new Error(`Job poll failed: HTTP ${jr.status}`)
        const job = await jr.json()
        if (job.message) setUpdateMsg(job.message)
        if (job.status === 'failed' || job.status === 'error') throw new Error(job.error ?? job.message ?? 'Job failed')
        if (job.status === 'done') {
          // Kick off background DB polling before unblocking the UI
          const dbJobIds: string[] = job.data?.db_job_ids ?? []
          if (dbJobIds.length > 0) {
            setDbStatus('building')
            ;(async () => {
              let anyError = false
              try {
                await Promise.all(dbJobIds.map(async (dbId) => {
                  while (true) {
                    await new Promise(r => setTimeout(r, 2000))
                    const r = await fetch(`${API}/api/jobs/${dbId}`)
                    if (!r.ok) { anyError = true; break }
                    const j = await r.json()
                    if (j.status === 'error') { anyError = true; break }
                    if (j.status === 'done') break
                  }
                }))
              } catch {
                anyError = true
              } finally {
                setDbStatus(anyError ? 'error' : 'ready')
              }
            })()
          }
          break
        }
      }

      setUpdateMsg('Refreshing network…')
      const dr = await fetch(`${API}/api/folder-network-data?path=${encodeURIComponent(folderPath)}`)
      if (!dr.ok) throw new Error(`HTTP ${dr.status}`)
      const data: Record<string, StackData> = (await dr.json()).stacks ?? {}
      setStacks(data)
      const key = activeKey || Object.keys(data)[0] || ''
      if (key) setActiveKey(key)
      if (key && data[key] && pixiRef.current) buildLayout(data[key])
      // Refresh quality scores after new pairs are selected — read pre-written JSON (fast path)
      qualityRef.current = null
      qualityFactorsRef.current = null
      fetch(`${API}/api/pair-quality?path=${encodeURIComponent(folderPath)}`)
        .then(r => r.ok ? r.json() : null)
        .then(d => {
          if (d) {
            qualityRef.current = d.scores ?? {}
            qualityFactorsRef.current = d.factors ?? {}
            setQualityScores(d.scores ?? {})
            setQualityFactors(d.factors ?? {})
            redraw()
          }
        })
        .catch(() => {})
      setUpdateMsg('Done')
    } catch (e) {
      setError(String(e)); setUpdateMsg('')
    } finally {
      setUpdating(false)
    }
  }

  async function handleSave() {
    setSaving(true); setError('')
    const activePairs = edgesRef.current.filter(e => e.active).map(e => [e.ref, e.sec])
    try {
      let res: Response
      if (saveUrl) {
        // Mintpy mode: send flat list of active date12 strings to update dropIfgram
        const active_pairs = activePairs.map(([ref, sec]) => `${ref}_${sec}`)
        res = await fetch(`${API}${saveUrl}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ folder_path: folderPath, active_pairs }),
        })
      } else {
        res = await fetch(`${API}/api/folder-save-pairs`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ folder_path: folderPath, pairs: { [activeKey]: activePairs } }),
        })
      }
      if (!res.ok) throw new Error(await res.text())
      onSaved(); onClose()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const stackKeys = Object.keys(stacks ?? {})

  // ── UI ───────────────────────────────────────────────────────────────────────

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 200,
        background: 'rgba(0,0,0,0.87)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
      onClick={onClose}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: '92vw', height: '90vh',
          background: '#0c1220', borderRadius: 8,
          border: `1px solid ${t.border}`,
          boxShadow: '0 12px 60px rgba(0,0,0,0.7)',
          display: 'flex', flexDirection: 'column',
        }}
      >
        {/* ── Header ─────────────────────────────────────────────────────────── */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '7px 12px', borderBottom: `1px solid ${t.border}`,
          background: t.bg2, borderRadius: '8px 8px 0 0', flexShrink: 0,
        }}>
          <span style={{ color: t.text, fontWeight: 700, fontSize: 13 }}>
            Edit Pair Network
          </span>

          {stackKeys.length > 1 && (
            <select
              value={activeKey}
              onChange={e => setActiveKey(e.target.value)}
              style={{
                background: t.inputBg, border: `1px solid ${t.inputBorder}`,
                color: t.text, borderRadius: 4, padding: '2px 6px', fontSize: 11,
              }}
            >
              {stackKeys.map(k => <option key={k} value={k}>{k}</option>)}
            </select>
          )}

          <span style={{
            background: '#132a4a', color: '#90caf9',
            borderRadius: 10, padding: '1px 10px', fontSize: 11, fontVariantNumeric: 'tabular-nums',
          }}>
            {activeCount} / {totalCount} pairs active
          </span>

          <div style={{ flex: 1 }} />

          <button onClick={() => setParamsOpen(o => !o)} style={{
            padding: '3px 10px', fontSize: 11, borderRadius: 4, cursor: 'pointer',
            background: paramsOpen ? t.inputBg : 'transparent',
            color: paramsOpen ? t.text : t.textMuted,
            border: `1px solid ${paramsOpen ? t.accent : t.border}`,
          }}>⚙ Parameters</button>

          <button onClick={handleReset} title="Restore all pairs" style={{
            padding: '3px 10px', fontSize: 11, borderRadius: 4, cursor: 'pointer',
            background: 'transparent', color: t.textMuted, border: `1px solid ${t.border}`,
          }}>Reset</button>

          <button onClick={handleFit} title="Fit to window" style={{
            padding: '3px 10px', fontSize: 11, borderRadius: 4, cursor: 'pointer',
            background: 'transparent', color: t.textMuted, border: `1px solid ${t.border}`,
          }}>Fit</button>

          {!readOnly && <button onClick={handleSave} disabled={saving} style={{
            padding: '4px 14px', fontSize: 12, borderRadius: 4,
            fontWeight: 600, cursor: saving ? 'default' : 'pointer',
            background: saving ? '#1a2a40' : '#0d3b6e',
            color: saving ? t.textMuted : '#90caf9',
            border: '1px solid #1565c0',
          }}>{saving ? 'Saving…' : 'Confirm & Save'}</button>}

          <button onClick={onClose} style={{
            background: 'none', border: 'none', cursor: 'pointer',
            color: t.textMuted, fontSize: 20, lineHeight: 1, padding: '0 4px',
          }}>×</button>
        </div>

        {/* ── Canvas area ─────────────────────────────────────────────────────── */}
        <div ref={containerRef} style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>

          {/* ── Parameters floating dialog ─────────────────────────────────────── */}
          {paramsOpen && (() => {
            const inp: React.CSSProperties = {
              background: t.inputBg, border: `1px solid ${t.inputBorder}`,
              color: t.text, borderRadius: 4, padding: '4px 8px',
              fontSize: 12, width: '100%', boxSizing: 'border-box',
            }
            const lbl: React.CSSProperties = {
              color: t.textMuted, fontSize: 10, marginBottom: 3, display: 'block',
              textTransform: 'uppercase', letterSpacing: '0.04em',
            }

            // ── MintPy mode: modify_network config ──────────────────────────────
            if (mintpyMode) {
              const sel: React.CSSProperties = { ...inp, cursor: 'pointer' }
              async function handleRunModifyNetwork() {
                if (!analyzerType) return
                setMnRunning(true)
                setMnMsg('Saving config…')
                try {
                  // 1. Patch config
                  const patch = {
                    network_tempBaseMax:    mnTempBaseMax,
                    network_perpBaseMax:    mnPerpBaseMax,
                    network_startDate:      mnStartDate,
                    network_endDate:        mnEndDate,
                    network_excludeDate:    mnExcludeDate,
                    network_minCoherence:   mnMinCoherence,
                    network_coherenceBased: mnCohBased,
                    network_keepMinSpanTree: mnKeepMST,
                  }
                  const patchRes = await fetch(`${API}/api/folder-config?path=${encodeURIComponent(folderPath)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ analyzer_config: patch }),
                  })
                  if (!patchRes.ok) throw new Error('Config save failed')

                  // 2. Run modify_network step
                  setMnMsg('Running modify_network…')
                  const runRes = await fetch(`${API}/api/folder-run-analyzer`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ folder_path: folderPath, analyzer_type: analyzerType, steps: ['modify_network'] }),
                  })
                  if (!runRes.ok) throw new Error('Run failed')
                  const { job_id } = await runRes.json()

                  // 3. Poll until done
                  await new Promise<void>((resolve, reject) => {
                    const iv = setInterval(async () => {
                      const s = await fetch(`${API}/api/jobs/${job_id}`).then(r => r.json())
                      setMnMsg(s.message?.split('\n').pop() ?? 'Running…')
                      if (s.status === 'done') { clearInterval(iv); resolve() }
                      if (s.status === 'error') { clearInterval(iv); reject(new Error(s.message)) }
                    }, 1500)
                  })
                  setMnMsg('Done')
                  setReloadTick(n => n + 1)
                } catch (e) {
                  setMnMsg(`Error: ${e}`)
                } finally {
                  setMnRunning(false)
                }
              }

              return (
                <>
                  <div onClick={() => setParamsOpen(false)} style={{ position: 'absolute', inset: 0, zIndex: 20 }} />
                  <div style={{
                    position: 'absolute', top: 12, right: 12, zIndex: 21, width: 420,
                    background: t.bg, border: `1px solid ${t.border}`, borderRadius: 8,
                    boxShadow: '0 8px 40px rgba(0,0,0,0.55)',
                    display: 'flex', flexDirection: 'column', overflow: 'hidden',
                  }}>
                    <div style={{
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                      padding: '9px 14px', borderBottom: `1px solid ${t.border}`, background: t.bg2,
                    }}>
                      <span style={{ color: t.text, fontWeight: 700, fontSize: 13 }}>modify_network Parameters</span>
                      <button onClick={() => setParamsOpen(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: t.textMuted, fontSize: 18, lineHeight: 1, padding: '0 2px' }}>×</button>
                    </div>
                    <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                        <div>
                          <label style={lbl}>Max temporal baseline (days)</label>
                          <input style={inp} value={mnTempBaseMax} onChange={e => setMnTempBaseMax(e.target.value)} placeholder="auto" />
                        </div>
                        <div>
                          <label style={lbl}>Max ⊥ baseline (m)</label>
                          <input style={inp} value={mnPerpBaseMax} onChange={e => setMnPerpBaseMax(e.target.value)} placeholder="auto" />
                        </div>
                        <div>
                          <label style={lbl}>Start date (YYYYMMDD)</label>
                          <input style={inp} value={mnStartDate} onChange={e => setMnStartDate(e.target.value)} placeholder="auto" />
                        </div>
                        <div>
                          <label style={lbl}>End date (YYYYMMDD)</label>
                          <input style={inp} value={mnEndDate} onChange={e => setMnEndDate(e.target.value)} placeholder="auto" />
                        </div>
                      </div>
                      <div>
                        <label style={lbl}>Exclude dates (space-separated YYYYMMDD)</label>
                        <input style={inp} value={mnExcludeDate} onChange={e => setMnExcludeDate(e.target.value)} placeholder="auto" />
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
                        <div>
                          <label style={lbl}>Coherence-based</label>
                          <select style={sel} value={mnCohBased} onChange={e => setMnCohBased(e.target.value)}>
                            {['auto', 'yes', 'no'].map(o => <option key={o} value={o}>{o}</option>)}
                          </select>
                        </div>
                        <div>
                          <label style={lbl}>Min coherence</label>
                          <input style={inp} value={mnMinCoherence} onChange={e => setMnMinCoherence(e.target.value)} placeholder="auto" />
                        </div>
                        <div>
                          <label style={lbl}>Keep min span tree</label>
                          <select style={sel} value={mnKeepMST} onChange={e => setMnKeepMST(e.target.value)}>
                            {['auto', 'yes', 'no'].map(o => <option key={o} value={o}>{o}</option>)}
                          </select>
                        </div>
                      </div>
                    </div>
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: 10,
                      padding: '10px 16px', borderTop: `1px solid ${t.border}`, background: t.bg2,
                    }}>
                      {mnMsg && <span style={{ fontSize: 11, fontFamily: 'monospace', flex: 1, color: mnMsg.startsWith('Error') ? '#e53935' : mnMsg === 'Done' ? '#4caf50' : t.textMuted }}>{mnMsg}</span>}
                      <button onClick={handleRunModifyNetwork} disabled={mnRunning} style={{
                        marginLeft: 'auto', padding: '5px 20px', fontSize: 12, borderRadius: 6, fontWeight: 600,
                        cursor: mnRunning ? 'default' : 'pointer',
                        background: mnRunning ? t.inputBg : '#1b5e20',
                        color: mnRunning ? t.textMuted : '#a5d6a7',
                        border: '1px solid #388e3c',
                      }}>{mnRunning ? '⟳ Running…' : 'Run modify_network'}</button>
                    </div>
                  </div>
                </>
              )
            }

            // ── Default mode: pair selection parameters ─────────────────────────
            return (
              <>
                {/* backdrop — click outside to close */}
                <div
                  onClick={() => setParamsOpen(false)}
                  style={{ position: 'absolute', inset: 0, zIndex: 20 }}
                />
                {/* floating panel */}
                <div style={{
                  position: 'absolute', top: 12, right: 12, zIndex: 21,
                  width: 480,
                  background: t.bg, border: `1px solid ${t.border}`, borderRadius: 8,
                  boxShadow: '0 8px 40px rgba(0,0,0,0.55)',
                  display: 'flex', flexDirection: 'column', overflow: 'hidden',
                }}>
                  {/* header */}
                  <div style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '9px 14px', borderBottom: `1px solid ${t.border}`,
                    background: t.bg2,
                  }}>
                    <span style={{ color: t.text, fontWeight: 700, fontSize: 13 }}>
                      Pair Selection Parameters
                    </span>
                    <button onClick={() => setParamsOpen(false)} style={{
                      background: 'none', border: 'none', cursor: 'pointer',
                      color: t.textMuted, fontSize: 18, lineHeight: 1, padding: '0 2px',
                    }}>×</button>
                  </div>
                  {/* body */}
                  <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
                    <div>
                      <label style={lbl}>Target temporal baselines (days, comma-separated)</label>
                      <input style={inp} value={dtTargets} onChange={e => setDtTargets(e.target.value)} />
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 10 }}>
                      <div>
                        <label style={lbl}>Tolerance (days)</label>
                        <input type="number" style={inp} value={dtTol} min={0}
                          onChange={e => setDtTol(parseInt(e.target.value) || 0)} />
                      </div>
                      <div>
                        <label style={lbl}>Max temporal (days)</label>
                        <input type="number" style={inp} value={dtMax} min={1}
                          onChange={e => setDtMax(parseInt(e.target.value) || 1)} />
                      </div>
                      <div>
                        <label style={lbl}>Max ⊥ baseline (m)</label>
                        <input type="number" style={inp} value={pbMax} min={0} step={10}
                          onChange={e => setPbMax(parseFloat(e.target.value) || 0)} />
                      </div>
                      <div>
                        <label style={lbl}>Min connections</label>
                        <input type="number" style={inp} value={minDegree} min={1}
                          onChange={e => setMinDegree(parseInt(e.target.value) || 1)} />
                      </div>
                      <div>
                        <label style={lbl}>Max connections</label>
                        <input type="number" style={inp} value={maxDegree} min={1}
                          onChange={e => setMaxDegree(parseInt(e.target.value) || 1)} />
                      </div>
                    </div>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 12, color: t.text }}>
                      <input type="checkbox" checked={forceConnect}
                        onChange={e => setForceConnect(e.target.checked)}
                        style={{ accentColor: t.accent, width: 14, height: 14 }} />
                      Force connected network
                    </label>
                    <div style={{ borderTop: `1px solid ${t.border}`, paddingTop: 10 }}>
                      <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 12, color: t.text, marginBottom: 8 }}>
                        <input type="checkbox" checked={avoidLowQuality}
                          onChange={e => setAvoidLowQuality(e.target.checked)}
                          style={{ accentColor: t.accent, width: 14, height: 14 }} />
                        Avoid low-quality acquisition days
                        <span style={{ color: t.textMuted, fontSize: 10 }}>(fetches weather &amp; snow)</span>
                      </label>
                      {avoidLowQuality && (
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginLeft: 20 }}>
                          <div>
                            <label style={lbl}>Snow cover threshold (0–1)</label>
                            <input type="number" style={inp} value={snowThreshold} min={0} max={1} step={0.05}
                              onChange={e => setSnowThreshold(parseFloat(e.target.value) || 0)} />
                          </div>
                          <div>
                            <label style={lbl}>3-day precip threshold (mm)</label>
                            <input type="number" style={inp} value={precipMmThreshold} min={0} step={5}
                              onChange={e => setPrecipMmThreshold(parseFloat(e.target.value) || 0)} />
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                  {/* footer */}
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 16px', borderTop: `1px solid ${t.border}`, background: t.bg2,
                  }}>
                    {updateMsg && (
                      <span style={{
                        fontSize: 11, fontFamily: 'monospace', flex: 1,
                        color: updateMsg === 'Done' ? '#4caf50' : t.textMuted,
                      }}>{updateMsg}</span>
                    )}
                    <button onClick={handleUpdate} disabled={updating} style={{
                      marginLeft: 'auto', padding: '5px 20px', fontSize: 12,
                      borderRadius: 6, fontWeight: 600,
                      cursor: updating ? 'default' : 'pointer',
                      background: updating ? t.inputBg : '#1b5e20',
                      color: updating ? t.textMuted : '#a5d6a7',
                      border: '1px solid #388e3c',
                    }}>{updating ? '⟳ Running…' : 'Update Network'}</button>
                  </div>
                </div>
              </>
            )
          })()}
          {!stacks && !error && (
            <div style={{
              position: 'absolute', inset: 0, pointerEvents: 'none',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: t.textMuted, fontSize: 12,
            }}>Loading network data…</div>
          )}
          {error && (
            <div style={{
              position: 'absolute', inset: 0, pointerEvents: 'none',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: '#e53935', fontSize: 12,
            }}>{error}</div>
          )}

          {/* Edge quality-risk legend + class selector */}
          <div style={{
            position: 'absolute', top: 10, right: 10, zIndex: 10,
            background: t.bg2, border: `1px solid ${t.border}`, borderRadius: 6,
            padding: '6px 10px', fontSize: 12, color: t.textMuted,
            display: 'flex', flexDirection: 'column', gap: 4,
          }}>
            <span style={{ color: t.text, fontWeight: 600, marginBottom: 2 }}>{saveUrl ? 'Coherence' : 'Pair quality'}</span>
            {(saveUrl
              ? [{ score: 0.8, label: 'Good' }, { score: 0.45, label: 'Risky' }, { score: 0.1, label: 'Bad' }]
              : [{ score: 100, label: 'Good' }, { score: 50,   label: 'Risky' }, { score: 0,   label: 'Bad' }]
            ).map(({ score, label }) => (
              <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 6, pointerEvents: 'none' }}>
                <div style={{ width: 28, height: 3, background: qualityCSS(score, 0.9), borderRadius: 1 }} />
                <span>{label}</span>
              </div>
            ))}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2, pointerEvents: 'none' }}>
              <div style={{ width: 28, height: 0, borderTop: `2px dashed #e57373` }} />
              <span style={{ color: '#e57373' }}>removed</span>
            </div>

          </div>

          {/* Floating SLC tooltip on node hover */}
          {hovNode && mousePos && (() => {
            const nodeEdges = edgesRef.current.filter(e => e.ref === hovNode.id || e.sec === hovNode.id)
            const active    = nodeEdges.filter(e => e.active).length
            const container = containerRef.current
            const cw = container?.clientWidth  ?? 800
            const ch = container?.clientHeight ?? 600
            const flip_x = mousePos.x + 220 > cw
            const flip_y = mousePos.y + 140 > ch
            return (
              <div style={{
                position: 'absolute',
                left:  flip_x ? mousePos.x - 218 : mousePos.x + 14,
                top:   flip_y ? mousePos.y - 130 : mousePos.y + 10,
                zIndex: 30, pointerEvents: 'none',
                background: t.bg2,
                border: `1px solid ${t.border}`,
                borderRadius: 6, padding: '8px 12px',
                boxShadow: '0 4px 20px rgba(0,0,0,0.25)',
                minWidth: 204, fontSize: 12, color: t.text,
              }}>
                <div style={{ fontWeight: 700, marginBottom: 6, color: t.accent, fontSize: 12 }}>
                  SLC Scene
                </div>
                <div style={{
                  fontFamily: 'monospace', fontSize: 10, color: t.textMuted,
                  wordBreak: 'break-all', marginBottom: 8, lineHeight: 1.5,
                }}>
                  {hovNode.id}
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 12px' }}>
                  <span style={{ color: t.textMuted }}>Date</span>
                  <span style={{ fontFamily: 'monospace' }}>{hovNode.date}</span>
                  <span style={{ color: t.textMuted }}>⊥ baseline</span>
                  <span style={{ fontFamily: 'monospace' }}>{hovNode.bperp.toFixed(1)} m</span>
                  <span style={{ color: t.textMuted }}>Pairs</span>
                  <span style={{ color: active > 0 ? '#4caf50' : '#e57373' }}>
                    {active} active / {nodeEdges.length} total
                  </span>
                </div>
              </div>
            )
          })()}

          {/* Floating edge tooltip on edge hover */}
          {hovEdge && !hovNode && mousePos && (() => {
            const fct = qualityFactors?.[`${hovEdge.ref}:${hovEdge.sec}`]
                     ?? qualityFactors?.[`${hovEdge.sec}:${hovEdge.ref}`]
            const sc  = edgeScore(hovEdge, qualityScores)
            const cat = sc !== null ? qualityCategory(sc) : null

            // Detect scoring mode from factor keys
            const isCohMode = fct && 'coherence_source' in fct
            const isLcMode  = fct && 'contributions' in fct

            // Normalise hard kills: coherence_score uses singular string, lc_score uses array
            const _KILL_LABEL: Record<string, string> = {
              water_dominant:   'Water dominant (>50%)',
              snow_ice_dominant:'Snow/ice dominant (>40%)',
              heavy_rain:       'Heavy rain (>30 mm/day)',
              wet_snow:         'Wet snow (temp >0°C + snow >30%)',
              fresh_snowfall:   'Fresh snowfall (Δcover >50%)',
              heavy_snow_cover: 'Heavy snow cover (>90%)',
              fire:             'Fire detected (FIRMS)',
            }
            const _killLabel = (k: string) => _KILL_LABEL[k] ?? k.replace(/_/g, ' ')
            const kills: string[] = isCohMode
              ? (fct?.hard_kill ? [_killLabel(String(fct.hard_kill))] : [])
              : (fct?.hard_kills ?? [])
            const warnings: string[] = fct?.warnings ?? []

            // Coherence source badge
            const cohSrc     = fct?.coherence_source as string | undefined
            const cohSrcLabel = cohSrc === 's3' ? 'Global S1 coherence' : cohSrc === 'failed' ? 'NDVI/LC' : cohSrc === 'climatology' ? 'Climatology' : undefined
            const cohSrcColor = cohSrc === 's3' ? '#4caf50' : cohSrc === 'failed' ? '#90caf9' : '#ffc107'

            // Coherence segments from _coherence.py: [(dt, season, coh), ...]
            const segments: [number, string, number][] = fct?.coherence_segments ?? []

            // Penalty breakdown dict from backend (quality fraction lost per feature)
            const cohPenalties: [string, number][] = isCohMode
              ? Object.entries((fct?.penalties ?? {}) as Record<string, number>)
                  .filter(([, v]) => v > 0.001)
                  .sort((a, b) => b[1] - a[1])
              : []

            const lcContribs: Record<string, number> = isLcMode ? (fct?.contributions ?? {}) : {}

            const container = containerRef.current
            const cw = container?.clientWidth  ?? 800
            const ch = container?.clientHeight ?? 600
            const flip_x = mousePos.x + 270 > cw
            const flip_y = mousePos.y + 280 > ch
            return (
              <div style={{
                position: 'absolute',
                left: flip_x ? mousePos.x - 268 : mousePos.x + 14,
                top:  flip_y ? mousePos.y - 270 : mousePos.y + 10,
                zIndex: 30, pointerEvents: 'none',
                background: t.bg2, border: `1px solid ${t.border}`,
                borderRadius: 6, padding: '8px 12px',
                boxShadow: '0 4px 20px rgba(0,0,0,0.25)',
                minWidth: 240, fontSize: 12, color: t.text,
              }}>
                <div style={{ fontWeight: 700, marginBottom: 6, color: t.accent }}>{saveUrl ? 'Interferogram' : 'Pair Quality'}</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 12px', marginBottom: 8 }}>
                  <span style={{ color: t.textMuted }}>Ref</span>
                  <span style={{ fontFamily: 'monospace', fontSize: 10 }}>{saveUrl ? hovEdge.ref : hovEdge.ref.slice(17, 25)}</span>
                  <span style={{ color: t.textMuted }}>Sec</span>
                  <span style={{ fontFamily: 'monospace', fontSize: 10 }}>{saveUrl ? hovEdge.sec : hovEdge.sec.slice(17, 25)}</span>
                  <span style={{ color: t.textMuted }}>Δt</span>
                  <span>{Math.round(hovEdge.dt)} days</span>
                  <span style={{ color: t.textMuted }}>⊥ baseline</span>
                  <span>{Math.round(hovEdge.bperpDiff)} m</span>
                  {sc !== null && cat && <>
                    <span style={{ color: t.textMuted }}>{saveUrl ? 'Coherence' : 'Score'}</span>
                    <span style={{ color: _CAT_CSS[cat], fontWeight: 700 }}>
                      {_CAT_LABEL[cat]} ({Number.isInteger(sc) ? sc : sc.toFixed(2)})
                    </span>
                  </>}
                  {/* Coherence mode: show source + expected coherence */}
                  {isCohMode && cohSrcLabel && <>
                    <span style={{ color: t.textMuted }}>Source</span>
                    <span style={{ color: cohSrcColor, fontSize: 10 }}>{cohSrcLabel}</span>
                  </>}
                  {isCohMode && fct?.coherence_abs != null && <>
                    <span style={{ color: t.textMuted }}>Abs. coherence</span>
                    <span style={{ fontFamily: 'monospace' }}>{Number(fct.coherence_abs).toFixed(3)}</span>
                  </>}
                  {isCohMode && (fct?.rho_inf ?? 0) > 0 && <>
                    <span style={{ color: t.textMuted }}>PS floor (ρ∞)</span>
                    <span style={{ fontFamily: 'monospace' }}>{Number(fct!.rho_inf).toFixed(3)}</span>
                  </>}
                  {isCohMode && fct?.coherence_same_season === false && <>
                    <span style={{ color: t.textMuted }}>Seasons</span>
                    <span style={{ color: '#ffc107', fontSize: 10 }}>
                      {fct?.coherence_season_d1} → {fct?.coherence_season_d2}
                    </span>
                  </>}
                </div>

                {/* Cross-season segments */}
                {isCohMode && segments.length > 1 && (
                  <div style={{ fontSize: 10, color: t.textMuted, marginBottom: 6 }}>
                    <div style={{ fontWeight: 600, marginBottom: 2, color: t.text }}>Segments:</div>
                    {segments.map(([dt, season, coh], i) => (
                      <div key={i} style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                        <span>{dt}d {season}</span>
                        <span style={{ fontFamily: 'monospace' }}>coh {Number(coh).toFixed(3)}</span>
                      </div>
                    ))}
                  </div>
                )}

                {kills.length > 0 && (
                  <div style={{ color: '#f44336', fontSize: 10, marginBottom: 6 }}>
                    <div style={{ fontWeight: 600, marginBottom: 2 }}>Hard kills:</div>
                    {kills.map(k => <div key={k}>✕ {k.replace(/_/g, ' ')}</div>)}
                  </div>
                )}
                {warnings.length > 0 && (
                  <div style={{ color: '#ffc107', fontSize: 10, marginBottom: 6 }}>
                    <div style={{ fontWeight: 600, marginBottom: 2 }}>Warnings:</div>
                    {warnings.map(w => <div key={w}>⚠ {w.replace(/_/g, ' ')}</div>)}
                  </div>
                )}

                {/* Coherence mode: environmental penalties (quality % lost per feature) */}
                {isCohMode && cohPenalties.length > 0 && (
                  <div style={{ fontSize: 10, color: t.textMuted }}>
                    <div style={{ fontWeight: 600, marginBottom: 4, color: t.text }}>Quality penalties:</div>
                    {cohPenalties.map(([k, v]) => {
                      // Show the raw sensor value alongside the quality loss
                      const rawMap: Record<string, string> = {
                        snow:        `max(d1=${((fct?.snow_cover_d1 ?? 0) * 100).toFixed(0)}%, d2=${((fct?.snow_cover_d2 ?? 0) * 100).toFixed(0)}%, Δ=${((fct?.delta_snow ?? 0) * 100).toFixed(0)}%)`,
                        precip_d1:   fct?.precip_3day_d1 != null ? `${Number(fct.precip_3day_d1).toFixed(1)} mm/3d` : '',
                        precip_d2:   fct?.precip_3day_d2 != null ? `${Number(fct.precip_3day_d2).toFixed(1)} mm/3d` : '',
                        freeze_thaw: fct?.temp_max_d1 != null && fct?.temp_max_d2 != null
                          ? `${Number(fct.temp_max_d1).toFixed(0)}°→${Number(fct.temp_max_d2).toFixed(0)}°C` : '',
                      }
                      return (
                        <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                          <span>
                            {k.replace(/_/g, ' ')}
                            {rawMap[k] ? <span style={{ color: t.textMuted, marginLeft: 4 }}>({rawMap[k]})</span> : null}
                          </span>
                          <span style={{ color: '#f44336', fontFamily: 'monospace', flexShrink: 0 }}>−{(v * 100).toFixed(1)}%</span>
                        </div>
                      )
                    })}
                  </div>
                )}

                {/* LC mode: contributions dict */}
                {isLcMode && Object.keys(lcContribs).length > 0 && (
                  <div style={{ fontSize: 10, color: t.textMuted }}>
                    <div style={{ fontWeight: 600, marginBottom: 4, color: t.text }}>Penalties:</div>
                    {Object.entries(lcContribs)
                      .filter(([, v]) => Math.abs(v) > 0.001)
                      .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
                      .map(([k, v]) => (
                        <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                          <span>{k.replace(/_/g, ' ')}</span>
                          <span style={{ color: v > 0 ? '#f44336' : '#4caf50', fontFamily: 'monospace' }}>
                            {v > 0 ? '-' : '+'}{v.toFixed(3)}
                          </span>
                        </div>
                      ))}
                  </div>
                )}
              </div>
            )
          })()}

          {/* Pixi canvas is appended here by the useEffect */}
        </div>

        {/* ── Footer / tooltip bar ─────────────────────────────────────────────── */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 20,
          padding: '4px 12px', borderTop: `1px solid ${t.border}`,
          background: t.bg2, borderRadius: '0 0 8px 8px',
          color: t.textMuted, fontSize: 10, flexShrink: 0, minHeight: 26,
        }}>
          {hovNode ? (
            <span style={{ color: t.textMuted }}>● {hovNode.date} — hover for details</span>
          ) : hovEdge ? (
            <>
              <span style={{ color: '#90caf9', fontFamily: 'monospace' }}>
                {hovEdge.ref.slice(0, 8)}…{hovEdge.ref.slice(17, 25)}
              </span>
              <span>→</span>
              <span style={{ color: '#90caf9', fontFamily: 'monospace' }}>
                {hovEdge.sec.slice(0, 8)}…{hovEdge.sec.slice(17, 25)}
              </span>
              <span>Δt {Math.round(hovEdge.dt)} d</span>
              <span>⊥ {Math.round(hovEdge.bperpDiff)} m</span>
              {(() => {
                const fct2 = qualityFactors?.[`${hovEdge.ref}:${hovEdge.sec}`]
                          ?? qualityFactors?.[`${hovEdge.sec}:${hovEdge.ref}`]
                const sc = edgeScore(hovEdge, qualityScores)
                if (sc === null) return <span style={{ color: _UNSCORED_CSS }}>● Unscored</span>
                const cat = qualityCategory(sc)
                const fct = fct2
                const isCohMode = fct && 'coherence_source' in fct
                const _KILL_LABEL: Record<string, string> = {
                  water_dominant:   'Water dominant',
                  heavy_rain:       'Heavy rain (>30 mm)',
                  heavy_snow_cover: 'Heavy snow cover (>90%)',
                  deep_snow:        'Deep snow (>50 cm)',
                }
                const _killLabel = (k: string) => _KILL_LABEL[k] ?? k.replace(/_/g, ' ')
                const kills: string[] = isCohMode
                  ? (fct?.hard_kill ? [_killLabel(String(fct.hard_kill))] : [])
                  : (fct?.hard_kills ?? [])
                const warnings: string[] = fct?.warnings ?? []
                const contribs: Record<string, number> = (!isCohMode && fct?.contributions) ? fct.contributions : {}
                // Coherence mode footer: show source badge + expected coh
                const cohSrc = fct?.coherence_source as string | undefined
                const cohSrcLabel = cohSrc === 's3' ? 'S1 Global' : cohSrc === 'failed' ? 'NDVI/LC' : cohSrc === 'climatology' ? 'Clim' : undefined
                const cohSrcColor = cohSrc === 's3' ? '#4caf50' : '#ffc107'
                const sameSeason  = fct?.coherence_same_season as boolean | undefined
                return (
                  <>
                    <span style={{ color: _CAT_CSS[cat], fontWeight: 600 }}>
                      ● {_CAT_LABEL[cat]} ({sc.toFixed(2)})
                    </span>
                    {isCohMode && cohSrcLabel && (
                      <span style={{ color: cohSrcColor, fontSize: 10 }}>[{cohSrcLabel}]</span>
                    )}
                    {isCohMode && sameSeason === false && (
                      <span style={{ color: '#ffc107', fontSize: 10 }}>
                        ⚠ cross-season
                      </span>
                    )}
                    {kills.length > 0 && (
                      <span style={{ color: '#f44336', fontSize: 10 }}>
                        ✕ {kills.join(', ')}
                      </span>
                    )}
                    {warnings.length > 0 && (
                      <span style={{ color: '#ffc107', fontSize: 10 }}>
                        ⚠ {warnings.join(', ')}
                      </span>
                    )}
                    {!isCohMode && kills.length === 0 && Object.keys(contribs).length > 0 && (
                      <span style={{ color: t.textMuted, fontSize: 10 }}>
                        {Object.entries(contribs)
                          .filter(([, v]) => Math.abs(v) > 0.001)
                          .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
                          .slice(0, 4)
                          .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${v > 0 ? '-' : '+'}${v.toFixed(3)}`)
                          .join('  ')}
                      </span>
                    )}
                  </>
                )
              })()}
              <span style={{ color: hovEdge.active ? '#4caf50' : '#e57373' }}>
                {hovEdge.active ? '● active' : '○ removed'}
              </span>
              <span style={{ color: t.textMuted, marginLeft: 'auto' }}>
                click to {hovEdge.active ? 'remove' : 'restore'}
              </span>
            </>
          ) : (
            <>
              <span>Left-click edge to toggle</span>
              <span>Drag node→node to add pair</span>
              <span>Scroll to zoom</span>
              <span>Right-drag to pan</span>
              {error && <span style={{ color: '#e53935', marginLeft: 'auto' }}>{error}</span>}
              {!error && dbStatus === 'building' && (
                <span style={{ color: '#ffc107', marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ display: 'inline-block', width: 7, height: 7, borderRadius: '50%', background: '#ffc107', animation: 'pulse 1.2s ease-in-out infinite' }} />
                  Building DB…
                </span>
              )}
              {!error && dbStatus === 'error' && (
                <span style={{ color: '#e53935', marginLeft: 'auto' }}>DB build failed</span>
              )}
            </>
          )}
        </div>
      </div>

      {/* ── DB build indicator — bottom-right corner ───────────────────────── */}
      {dbStatus === 'building' && (
        <div style={{
          position: 'absolute', bottom: 36, right: 16,
          background: 'rgba(20,28,44,0.92)',
          border: '1px solid #ffc107',
          borderRadius: 6, padding: '6px 12px',
          display: 'flex', alignItems: 'center', gap: 8,
          fontSize: 12, color: '#ffc107',
          pointerEvents: 'none',
          boxShadow: '0 2px 12px rgba(0,0,0,0.5)',
        }}>
          <span style={{
            display: 'inline-block', width: 7, height: 7, borderRadius: '50%',
            background: '#ffc107', animation: 'pulse 1.2s ease-in-out infinite',
          }} />
          Building pair database…
        </div>
      )}
    </div>
  )
}
