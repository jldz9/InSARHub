import { useState, useEffect, useRef } from 'react'
import type { Theme } from './theme'
import { NetworkEditor } from './NetworkEditor'

// ── Raster overlay passed up to the map ──────────────────────────────────────

export interface RasterOverlay {
  id:        string
  url:       string          // blob URL of rendered canvas
  bounds:    [number, number, number, number]  // [west, south, east, north]
  pixelData: Float32Array
  width:     number
  height:    number
  nodata:    number | null
  type:      string
  label:     string
  vmin:      number
  vmax:      number
  source?:   { kind: 'mintpy'; folderPath: string; tsFile: string | null }
}

const API = import.meta.env.DEV ? 'http://localhost:8000' : ''

// ── Resizable drawer width ─────────────────────────────────────────────────
function useResizable(initial: number, min = 160, max = 700) {
  const [width, setWidth] = useState(initial)
  const startRef = useRef<{ x: number; w: number } | null>(null)
  function onHandleMouseDown(e: React.MouseEvent) {
    e.preventDefault()
    startRef.current = { x: e.clientX, w: width }
    const onMove = (ev: MouseEvent) => {
      if (!startRef.current) return
      setWidth(Math.max(min, Math.min(max, startRef.current.w + startRef.current.x - ev.clientX)))
    }
    const onUp = () => {
      startRef.current = null
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }
  return { width, onHandleMouseDown }
}

const ResizeHandle = ({ onMouseDown }: { onMouseDown: (e: React.MouseEvent) => void }) => (
  <div
    onMouseDown={onMouseDown}
    style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 5, cursor: 'ew-resize', zIndex: 1 }}
    onMouseEnter={e => (e.currentTarget.style.background = 'rgba(128,128,128,0.25)')}
    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
  />
)

// Persist active download job IDs across L2 drawer unmount/remount
const _dlJobs:    Map<string, string> = new Map()
const _orbitJobs: Map<string, string> = new Map()

interface JobFolder {
  name:     string
  path:     string
  tags:     string[]
  workflow: Record<string, string>
}

interface FolderDetails {
  downloader_config: Record<string, any> | null
  has_pairs:         boolean
  network_image:     string | null
}

interface Props {
  theme:          Theme
  workdir:        string
  mapClickSignal: number
  onClose:        () => void
  onRasterSelect: (overlay: RasterOverlay | null) => void
  onSettingsOpen: (analyzerType: string) => void
}

// Color per workflow role
const ROLE_COLORS: Record<string, { bg: string; color: string; border: string }> = {
  downloader: { bg: '#0d3b6e', color: '#90caf9', border: '#1565c0' },
  processor:  { bg: '#4a2500', color: '#ffcc80', border: '#e65100' },
  analyzer:   { bg: '#1b3a2a', color: '#a5d6a7', border: '#2e7d32' },
}
const ROLE_FALLBACK = { bg: '#1e1e2e', color: '#aaa', border: '#444' }

// Config fields to display and their labels (in order)
const CFG_FIELDS: { key: string; label: string }[] = [
  { key: 'start',          label: 'Start' },
  { key: 'end',            label: 'End' },
  { key: 'relativeOrbit',  label: 'Path' },
  { key: 'frame',          label: 'Frame' },
  { key: 'flightDirection',label: 'Direction' },
  { key: 'intersectsWith', label: 'AOI' },
  { key: 'dataset',        label: 'Dataset' },
  { key: 'platform',       label: 'Platform' },
  { key: 'maxResults',     label: 'Max Results' },
  { key: 'beamMode',       label: 'Beam Mode' },
  { key: 'polarization',   label: 'Polarization' },
]

function fmtVal(key: string, val: any): string {
  if (val === null || val === undefined || val === '') return ''
  if (key === 'intersectsWith' && typeof val === 'string')
    return val.length > 40 ? val.slice(0, 38) + '…' : val
  if (Array.isArray(val)) return val.join(', ')
  return String(val)
}


// ── L4: Pair detail drawer ────────────────────────────────────────────────────

function parseScene(name: string) {
  const dateStr = name?.slice(17, 25) ?? ''
  const timeStr = name?.slice(26, 32) ?? ''
  const orbitRaw = name?.slice(49, 55) ?? ''
  return {
    platform: name?.slice(0, 3) ?? '',
    date: dateStr.match(/^\d{8}$/) ? `${dateStr.slice(0,4)}-${dateStr.slice(4,6)}-${dateStr.slice(6,8)}` : dateStr,
    time: timeStr.match(/^\d{6}$/) ? `${timeStr.slice(0,2)}:${timeStr.slice(2,4)}:${timeStr.slice(4,6)}` : timeStr,
    orbit: orbitRaw ? String(parseInt(orbitRaw, 10)) : '',
    full: name ?? '',
  }
}

interface PairDetailProps { theme: Theme; ref_: string; sec: string; onClose: () => void; rightOffset: number }

function PairDetailDrawer({ theme: t, ref_, sec, onClose, rightOffset }: PairDetailProps) {
  const { width, onHandleMouseDown } = useResizable(300)
  const r = parseScene(ref_)
  const s = parseScene(sec)
  const dtDays = (r.date && s.date)
    ? Math.round(Math.abs(new Date(s.date).getTime() - new Date(r.date).getTime()) / 86400000)
    : null
  const [copiedKey, setCopiedKey] = useState<string | null>(null)

  function copyVal(key: string, val: string) {
    if (!val || val === '—') return
    navigator.clipboard.writeText(val)
    setCopiedKey(key)
    setTimeout(() => setCopiedKey(null), 1200)
  }

  const cfgRow = (label: string, val: string) => (
    <div key={label} onClick={() => copyVal(label, val)}
      style={{ display: 'flex', gap: 8, padding: '5px 0', borderBottom: `1px solid ${t.divider}`,
               cursor: val ? 'copy' : 'default' }}>
      <span style={{ width: 80, flexShrink: 0, color: t.textMuted, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em' }}>{label}</span>
      <span style={{ color: copiedKey === label ? '#4caf50' : t.text, fontSize: 11, fontFamily: 'monospace',
                     transition: 'color 0.2s' }}>{val || '—'}{copiedKey === label ? ' ✓' : ''}</span>
    </div>
  )

  const sceneCard = (label: string, accent: string, sc: ReturnType<typeof parseScene>) => (
    <div>
      <div style={{ color: accent, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 700, marginBottom: 6 }}>{label}</div>
      {cfgRow('Platform', sc.platform)}
      {cfgRow('Date', sc.date)}
      {cfgRow('Time UTC', sc.time)}
      {cfgRow('Abs. Orbit', sc.orbit)}
      <div style={{ padding: '5px 0' }}>
        <span style={{ width: 80, flexShrink: 0, color: t.textMuted, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em', display: 'block', marginBottom: 2 }}>Scene ID</span>
        <span style={{ color: t.textMuted, fontSize: 9, fontFamily: 'monospace', wordBreak: 'break-all' }} title={sc.full}>{sc.full}</span>
      </div>
    </div>
  )

  return (
    <div style={{
      position: 'fixed', top: 48, right: rightOffset, bottom: 0, width,
      background: t.bg, borderLeft: `1px solid ${t.border}`,
      display: 'flex', flexDirection: 'column', zIndex: 114,
      boxShadow: '-4px 0 20px rgba(0,0,0,0.25)',
    }}>
      <ResizeHandle onMouseDown={onHandleMouseDown} />
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '10px 14px', borderBottom: `1px solid ${t.border}`,
        background: t.bg2, flexShrink: 0,
      }}>
        <span style={{ color: t.text, fontWeight: 600, fontSize: 12 }}>Pair Detail</span>
        <button onClick={onClose} style={{ background: 'none', border: 'none',
          cursor: 'pointer', color: t.textMuted, fontSize: 20, lineHeight: 1, padding: '0 4px' }}>×</button>
      </div>

      {dtDays !== null && (
        <div style={{ padding: '8px 14px', background: t.bg2, borderBottom: `1px solid ${t.border}`, flexShrink: 0 }}>
          <span style={{ color: t.textMuted, fontSize: 11 }}>Temporal baseline: </span>
          <span style={{ color: t.accent, fontWeight: 700, fontSize: 13 }}>{dtDays} days</span>
        </div>
      )}

      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 16 }}>
        {sceneCard('Reference', '#90caf9', r)}
        <div style={{ borderTop: `1px solid ${t.border}`, paddingTop: 12 }}>
          {sceneCard('Secondary', '#a5d6a7', s)}
        </div>
      </div>
    </div>
  )
}

// ── L3: Pairs list drawer ─────────────────────────────────────────────────────

interface PairsDrawerProps { theme: Theme; folderPath: string; onClose: () => void; rightOffset: number }

function PairsDrawer({ theme: t, folderPath, onClose, rightOffset }: PairsDrawerProps) {
  const { width, onHandleMouseDown } = useResizable(280)
  const [pairs,       setPairs]       = useState<string[][]>([])
  const [count,       setCount]       = useState(0)
  const [fname,       setFname]       = useState('')
  const [loading,     setLoading]     = useState(true)
  const [error,       setError]       = useState('')
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null)

  useEffect(() => {
    setLoading(true)
    fetch(`${API}/api/folder-pairs?path=${encodeURIComponent(folderPath)}`)
      .then(r => r.json())
      .then(d => {
        if (d.detail) { setError(d.detail); setLoading(false); return }
        setPairs(d.pairs ?? [])
        setCount(d.count ?? 0)
        setFname(d.file ?? '')
        setLoading(false)
      })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [folderPath])

  const extractDate = (name: string) => {
    const d = name?.slice(17, 25)
    return d?.match(/^\d{8}$/) ? `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)}` : (name ?? '')
  }

  return (
    <>
      {selectedIdx !== null && pairs[selectedIdx] && (
        <PairDetailDrawer
          theme={t}
          ref_={pairs[selectedIdx][0]}
          sec={pairs[selectedIdx][1]}
          onClose={() => setSelectedIdx(null)}
          rightOffset={rightOffset + width}
        />
      )}

      <div style={{
        position: 'fixed', top: 48, right: rightOffset, bottom: 0, width,
        background: t.bg, borderLeft: `1px solid ${t.border}`,
        display: 'flex', flexDirection: 'column', zIndex: 113,
        boxShadow: '-4px 0 20px rgba(0,0,0,0.25)',
      }}>
        <ResizeHandle onMouseDown={onHandleMouseDown} />
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '10px 14px', borderBottom: `1px solid ${t.border}`,
          background: t.bg2, flexShrink: 0,
        }}>
          <div>
            <span style={{ color: t.text, fontWeight: 600, fontSize: 12 }}>Pairs</span>
            {count > 0 && (
              <span style={{ color: t.textMuted, fontSize: 10, marginLeft: 8 }}>{count} pairs · {fname}</span>
            )}
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none',
            cursor: 'pointer', color: t.textMuted, fontSize: 20, lineHeight: 1, padding: '0 4px' }}>×</button>
        </div>

        <div style={{ flex: 1, overflowY: 'auto' }}>
          {loading ? (
            <div style={{ color: t.textMuted, fontSize: 11, textAlign: 'center', padding: '32px 0' }}>Loading…</div>
          ) : error ? (
            <div style={{ color: '#e53935', fontSize: 11, padding: 14 }}>{error}</div>
          ) : pairs.map(([ref, sec], i) => {
            const isSelected = selectedIdx === i
            return (
              <button key={i} onClick={() => setSelectedIdx(isSelected ? null : i)} style={{
                width: '100%', display: 'flex', alignItems: 'center', gap: 8,
                padding: '6px 14px', borderBottom: `1px solid ${t.divider}`,
                background: isSelected ? t.btnActiveBg : i % 2 === 0 ? 'transparent' : t.bg2,
                border: 'none', cursor: 'pointer', textAlign: 'left',
                fontSize: 11, fontFamily: 'monospace',
              }}>
                <span style={{ color: t.textMuted, width: 28, textAlign: 'right', flexShrink: 0 }}>{i + 1}</span>
                <span style={{ color: isSelected ? t.accent : t.isDark ? '#90caf9' : '#1565c0' }}>{extractDate(ref)}</span>
                <span style={{ color: t.textMuted }}>↔</span>
                <span style={{ color: isSelected ? t.accent : t.isDark ? '#a5d6a7' : '#2e7d32' }}>{extractDate(sec)}</span>
              </button>
            )
          })}
        </div>
      </div>
    </>
  )
}

// ── Process Modal ────────────────────────────────────────────────────────────

interface FieldMeta { key: string; label: string; type: string; default: any; options?: string[]; min?: number; max?: number; step?: number; hint?: string }
interface ProcMeta  { label: string; fields: FieldMeta[]; groups?: Array<{ label: string; fields: string[] }>; compatible_downloader?: string | null }

interface ProcessModalProps { theme: Theme; folderPath: string; downloaderType: string; onClose: () => void; onDone: () => void }

function ProcessModal({ theme: t, folderPath, downloaderType, onClose, onDone }: ProcessModalProps) {
  const [loading,     setLoading]     = useState(true)
  const [procType,    setProcType]    = useState('')
  const [procOptions, setProcOptions] = useState<Record<string, ProcMeta>>({})
  const [procConfig,  setProcConfig]  = useState<Record<string, any>>({})
  const [dryRun,      setDryRun]      = useState(false)
  const [status,      setStatus]      = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [message,     setMessage]     = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    Promise.all([
      fetch(`${API}/api/settings`).then(r => r.json()),
      fetch(`${API}/api/workflows`).then(r => r.json()),
    ]).then(([settings, workflows]) => {
      const allProcs: Record<string, ProcMeta> = workflows.processors ?? {}
      const compat = Object.fromEntries(
        Object.entries(allProcs).filter(([, m]) =>
          !m.compatible_downloader || m.compatible_downloader === 'all' || m.compatible_downloader === downloaderType
        )
      )
      setProcOptions(compat)
      const cur = settings.processor
      const sel = compat[cur] ? cur : (Object.keys(compat)[0] ?? '')
      setProcType(sel)
      setProcConfig(settings.processor_config ?? {})
      setLoading(false)
    }).catch(() => setLoading(false))
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [downloaderType])

  function handleProcTypeChange(type: string) {
    setProcType(type)
    const defaults: Record<string, any> = {}
    procOptions[type]?.fields.forEach(f => { defaults[f.key] = f.default })
    setProcConfig(defaults)
  }

  async function handleRun() {
    setStatus('running'); setMessage('Submitting…')
    try {
      const res = await fetch(`${API}/api/folder-process`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_path: folderPath, processor_type: procType, processor_config: procConfig, dry_run: dryRun }),
      })
      if (!res.ok) { const d = await res.json(); setStatus('error'); setMessage(d.detail ?? 'Error'); return }
      const { job_id } = await res.json()
      pollRef.current = setInterval(async () => {
        const r = await fetch(`${API}/api/jobs/${job_id}`)
        const job = await r.json()
        setMessage(job.message ?? '')
        if (job.status === 'done')  { clearInterval(pollRef.current!); setStatus('done') }
        else if (job.status === 'error') { clearInterval(pollRef.current!); setStatus('error') }
      }, 1500)
    } catch (e) { setStatus('error'); setMessage(String(e)) }
  }

  const currentMeta = procOptions[procType]
  const inp: React.CSSProperties = {
    background: t.inputBg, border: `1px solid ${t.inputBorder}`,
    color: t.text, borderRadius: 4, padding: '4px 8px',
    fontSize: 12, width: '100%', boxSizing: 'border-box',
    colorScheme: t.isDark ? 'dark' : 'light',
  }
  const lbl: React.CSSProperties = { color: t.textMuted, fontSize: 10, marginBottom: 3, display: 'block', textTransform: 'uppercase', letterSpacing: '0.04em' }

  function renderFieldInput(f: FieldMeta) {
    const val = procConfig[f.key] ?? f.default
    const set = (v: any) => setProcConfig(c => ({ ...c, [f.key]: v }))
    if (f.type === 'bool') return (
      <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
        <input type="checkbox" checked={!!val} onChange={e => set(e.target.checked)}
          style={{ accentColor: t.accent, width: 14, height: 14 }} />
        <span style={{ color: t.text, fontSize: 11 }}>{val ? 'Yes' : 'No'}</span>
      </label>
    )
    if (f.type === 'select') return (
      <select value={val ?? ''} onChange={e => set(e.target.value)} style={inp}>
        {f.options!.map(o => <option key={o} value={o}>{o || '(any)'}</option>)}
      </select>
    )
    if (f.type === 'number') return (
      <input type="number" value={val ?? ''} min={f.min} max={f.max} step={f.step ?? 1}
        onChange={e => set(parseFloat(e.target.value))} style={{ ...inp, width: 90 }} />
    )
    return <input type="text" value={val ?? ''} onChange={e => set(e.target.value)} style={inp} />
  }

  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 210 }} />
      <div style={{
        position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
        width: 500, maxHeight: '85vh',
        background: t.bg, border: `1px solid ${t.border}`, borderRadius: 8,
        display: 'flex', flexDirection: 'column',
        boxShadow: '0 8px 40px rgba(0,0,0,0.5)', zIndex: 211, overflow: 'hidden',
      }}>
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '11px 18px', borderBottom: `1px solid ${t.border}`, background: t.bg2, flexShrink: 0,
        }}>
          <span style={{ color: t.text, fontWeight: 700, fontSize: 14 }}>Submit to Processor</span>
          <button onClick={onClose} style={{ background: 'none', border: 'none',
            cursor: 'pointer', color: t.textMuted, fontSize: 20, lineHeight: 1, padding: '0 4px' }}>×</button>
        </div>

        {loading ? (
          <div style={{ color: t.textMuted, fontSize: 12, textAlign: 'center', padding: '40px 0' }}>Loading…</div>
        ) : (
          <div style={{ overflowY: 'auto', padding: '16px 18px', flex: 1, display: 'flex', flexDirection: 'column', gap: 14 }}>
            {/* Processor type */}
            <div>
              <label style={lbl}>Processor</label>
              <select value={procType} onChange={e => handleProcTypeChange(e.target.value)}
                style={{ ...inp, fontFamily: 'monospace' }}>
                {Object.keys(procOptions).map(k => <option key={k} value={k}>{k}</option>)}
              </select>
            </div>

            {/* Grouped parameter fields */}
            {currentMeta?.groups?.map(grp => {
              const byKey = Object.fromEntries(currentMeta.fields.map(f => [f.key, f]))
              const grpFields = grp.fields.map(k => byKey[k]).filter(Boolean)
              if (!grpFields.length) return null
              return (
                <div key={grp.label}>
                  <div style={{
                    color: t.textMuted, fontSize: 10, textTransform: 'uppercase',
                    letterSpacing: '0.07em', fontWeight: 700, marginBottom: 8,
                    paddingBottom: 4, borderBottom: `1px solid ${t.divider}`,
                  }}>{grp.label}</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                    {grpFields.map(f => (
                      <div key={f.key} title={f.hint}>
                        <label style={lbl}>{f.label}</label>
                        {renderFieldInput(f)}
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}

            {message && (
              <div style={{
                color: status === 'done' ? '#4caf50' : status === 'error' ? '#e53935' : t.textMuted,
                fontSize: 11, fontFamily: 'monospace',
              }}>{message}</div>
            )}
          </div>
        )}

        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '10px 18px', borderTop: `1px solid ${t.border}`, background: t.bg2, flexShrink: 0,
        }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
            <input type="checkbox" checked={dryRun} onChange={e => { setDryRun(e.target.checked); if (status !== 'running') setStatus('idle') }}
              style={{ accentColor: '#ffb74d', width: 13, height: 13 }} />
            <span style={{ color: t.textMuted, fontSize: 11 }}>Dry run</span>
          </label>
          <div style={{ display: 'flex', gap: 8 }}>
            <button onClick={() => { if (status === 'done' && !dryRun) onDone(); else onClose() }} style={{
              padding: '5px 16px', background: 'transparent', color: t.textMuted,
              border: `1px solid ${t.border}`, borderRadius: 6, fontSize: 12, cursor: 'pointer',
            }}>{status === 'done' ? 'Close' : 'Cancel'}</button>
            <button onClick={status === 'done' ? (dryRun ? onClose : onDone) : handleRun} disabled={loading || status === 'running'} style={{
              padding: '5px 20px', borderRadius: 6, fontSize: 12, fontWeight: 600,
              background: status === 'done' ? '#1b3a2a' : status === 'error' ? '#b71c1c' : dryRun ? '#2a2000' : '#4a2500',
              color:      status === 'done' ? '#a5d6a7' : status === 'error' ? '#ef9a9a' : dryRun ? '#ffeb80' : '#ffcc80',
              border: `1px solid ${status === 'done' ? '#2e7d32' : status === 'error' ? '#c62828' : dryRun ? '#f9a825' : '#e65100'}`,
              cursor: loading || status === 'running' ? 'default' : 'pointer',
            }}>
              {status === 'running' ? (dryRun ? '⟳ Checking…' : '⟳ Submitting…') : status === 'done' ? '✓ Done' : status === 'error' ? '✕ Retry' : dryRun ? 'Dry Run' : 'Submit'}
            </button>
          </div>
        </div>
      </div>
    </>
  )
}

// ── Analyzer Panel (MintPy step runner) ──────────────────────────────────────

interface AnalyzerPanelProps { theme: Theme; folderPath: string; analyzerType: string; onSettingsOpen: (analyzerType: string) => void }

function AnalyzerPanel({ theme: t, folderPath, analyzerType, onSettingsOpen }: AnalyzerPanelProps) {
  const [steps,       setSteps]      = useState<string[]>([])
  const [checked,     setChecked]    = useState<Set<string>>(new Set())
  const [loading,     setLoading]    = useState(true)
  const [runMsg,      setRunMsg]     = useState('')
  const [runStat,     setRunStat]    = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [progress,    setProgress]   = useState(0)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [cleanupMsg,  setCleanupMsg]  = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const _storageKey = `analyzer_job:${folderPath}`

  function _saveState(jobId: string, msg: string, pct: number) {
    localStorage.setItem(_storageKey, JSON.stringify({ jobId, msg, pct }))
  }
  function _clearState() { localStorage.removeItem(_storageKey) }

  function _startPolling(job_id: string) {
    setActiveJobId(job_id)
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`${API}/api/jobs/${job_id}`)
        if (!r.ok) {
          // 404 = server restarted, job is gone
          clearInterval(pollRef.current!); setRunStat('error')
          setRunMsg('Job not found — server may have restarted. Please re-run.')
          setActiveJobId(null); _clearState()
          return
        }
        const job = await r.json()
        const msg = job.message ?? ''
        const pct = job.progress ?? 0
        setRunMsg(msg)
        setProgress(pct)
        _saveState(job_id, msg, pct)
        if (job.status === 'done') {
          clearInterval(pollRef.current!); setRunStat('done'); setProgress(100)
          setActiveJobId(null); _clearState()
        } else if (job.status === 'error') {
          clearInterval(pollRef.current!); setRunStat('error')
          setActiveJobId(null); _clearState()
        }
      } catch { /* network blip — keep polling */ }
    }, 1500)
  }

  // On mount / folder change: reset state, load steps, then reconnect any in-progress job
  useEffect(() => {
    setRunStat('idle')
    setRunMsg('')
    setProgress(0)
    setActiveJobId(null)
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
    setLoading(true)
    fetch(`${API}/api/analyzer-steps?analyzer_type=${encodeURIComponent(analyzerType)}`)
      .then(r => r.json())
      .then(d => {
        const s: string[] = d.steps ?? []
        setSteps(s)
        setChecked(new Set(s))
        setLoading(false)

        // Reconnect to a running job if one was saved for this folder
        const saved = localStorage.getItem(`analyzer_job:${folderPath}`)
        if (saved) {
          try {
            const { jobId, msg, pct } = JSON.parse(saved)
            // Verify job still exists on server before restoring running state
            fetch(`${API}/api/jobs/${jobId}`).then(r => {
              if (!r.ok) { localStorage.removeItem(`analyzer_job:${folderPath}`); return }
              return r.json()
            }).then(job => {
              if (!job) return
              if (job.status === 'done' || job.status === 'error') {
                localStorage.removeItem(`analyzer_job:${folderPath}`)
                setRunStat(job.status); setRunMsg(job.message ?? msg); setProgress(job.progress ?? pct)
              } else {
                setRunStat('running'); setRunMsg(msg ?? ''); setProgress(pct ?? 0)
                _startPolling(jobId)
              }
            }).catch(() => localStorage.removeItem(`analyzer_job:${folderPath}`))
          } catch { localStorage.removeItem(`analyzer_job:${folderPath}`) }
        }
      })
      .catch(() => setLoading(false))
  }, [analyzerType, folderPath])

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  function toggleAll(val: boolean) {
    setChecked(val ? new Set(steps) : new Set())
  }

  function toggle(step: string) {
    setChecked(prev => {
      const next = new Set(prev)
      next.has(step) ? next.delete(step) : next.add(step)
      return next
    })
  }

  function runAnalyzer() {
    const selected = steps.filter(s => checked.has(s))
    if (selected.length === 0) return
    setRunStat('running')
    setProgress(0)
    setRunMsg('Submitting…')
    fetch(`${API}/api/folder-run-analyzer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_path: folderPath, analyzer_type: analyzerType, steps: selected }),
    })
      .then(r => r.json())
      .then(({ job_id }) => { _saveState(job_id, 'Submitting…', 0); _startPolling(job_id) })
      .catch(e => { setRunStat('error'); setRunMsg(String(e)) })
  }

  function stopAnalyzer() {
    if (!activeJobId) return
    fetch(`${API}/api/jobs/${activeJobId}/stop`, { method: 'POST' }).catch(() => {})
  }

  const rc = ROLE_COLORS.analyzer
  const busy = runStat === 'running'

  if (loading) return <span style={{ color: t.textMuted, fontSize: 11 }}>Loading steps…</span>
  if (steps.length === 0) return <span style={{ color: t.textMuted, fontSize: 11 }}>No steps available for this analyzer.</span>

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* Step checklist */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ color: t.textMuted, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em' }}>Steps</div>
        <div style={{ display: 'flex', gap: 6 }}>
          <button onClick={() => onSettingsOpen(analyzerType)} style={{ fontSize: 10, padding: '1px 6px', borderRadius: 3, cursor: 'pointer', background: 'transparent', color: t.accent, border: `1px solid ${t.border}` }}>Config</button>
          <button onClick={() => toggleAll(true)} style={{ fontSize: 10, padding: '1px 6px', borderRadius: 3, cursor: 'pointer', background: 'transparent', color: t.textMuted, border: `1px solid ${t.border}` }}>All</button>
          <button onClick={() => toggleAll(false)} style={{ fontSize: 10, padding: '1px 6px', borderRadius: 3, cursor: 'pointer', background: 'transparent', color: t.textMuted, border: `1px solid ${t.border}` }}>None</button>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        {steps.map((step, i) => (
          <label key={step} style={{ display: 'flex', alignItems: 'center', gap: 7, cursor: busy ? 'default' : 'pointer', opacity: busy ? 0.6 : 1 }}>
            <input
              type="checkbox"
              checked={checked.has(step)}
              disabled={busy}
              onChange={() => toggle(step)}
              style={{ accentColor: rc.bg, cursor: busy ? 'default' : 'pointer' }}
            />
            <span style={{ fontSize: 11, color: t.text, fontFamily: 'monospace' }}>
              <span style={{ color: t.textMuted, marginRight: 4 }}>{String(i + 1).padStart(2, '0')}</span>
              {step}
            </span>
          </label>
        ))}
      </div>

      {/* Progress bar */}
      {busy && (
        <div style={{ background: t.bg2, borderRadius: 3, border: `1px solid ${t.divider}`, overflow: 'hidden', height: 6 }}>
          <div style={{ height: '100%', width: `${progress}%`, background: rc.bg, transition: 'width 0.4s ease' }} />
        </div>
      )}

      {/* Run / Stop button */}
      {busy ? (
        <button onClick={stopAnalyzer} style={{
          padding: '6px 0', fontSize: 11, borderRadius: 4, cursor: 'pointer',
          background: '#e53935', color: '#fff', border: '1px solid #e53935',
        }}>Stop</button>
      ) : (
        <button
          disabled={checked.size === 0}
          onClick={runAnalyzer}
          style={{
            padding: '6px 0', fontSize: 11, borderRadius: 4,
            cursor: checked.size === 0 ? 'default' : 'pointer',
            background: rc.bg, color: rc.color, border: `1px solid ${rc.border}`,
            opacity: checked.size === 0 ? 0.5 : 1,
          }}
        >
          Run {checked.size} step{checked.size !== 1 ? 's' : ''}
        </button>
      )}

      {/* Cleanup button */}
      <button
        disabled={busy}
        onClick={() => {
          if (!confirm('Remove tmp dirs and zip archives in this folder?')) return
          setCleanupMsg('Cleaning…')
          fetch(`${API}/api/folder-analyzer-cleanup`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder_path: folderPath, analyzer_type: analyzerType, steps: [] }),
          })
            .then(r => r.json())
            .then(() => setCleanupMsg('Cleaned'))
            .catch(e => setCleanupMsg(`Error: ${e}`))
        }}
        style={{
          padding: '6px 0', fontSize: 11, borderRadius: 4,
          cursor: busy ? 'default' : 'pointer', opacity: busy ? 0.5 : 1,
          background: 'transparent', color: t.textMuted, border: `1px solid ${t.border}`,
        }}
        title="Remove tmp dirs and zip archives"
      >Cleanup</button>
      {cleanupMsg && <div style={{ fontSize: 10, color: t.textMuted }}>{cleanupMsg}</div>}

      {/* Status message */}
      {runMsg && (
        <div style={{
          fontSize: 10, fontFamily: 'monospace', padding: '5px 8px', borderRadius: 3,
          background: t.bg2, border: `1px solid ${t.divider}`,
          color: runStat === 'done' ? '#4caf50' : runStat === 'error' ? '#e53935' : t.textMuted,
          whiteSpace: 'pre', overflowX: 'auto', maxHeight: 300, overflowY: 'auto',
        }}>{runMsg}</div>
      )}
    </div>
  )
}


// ── Server-rendered TIF → RasterOverlay ──────────────────────────────────────

async function renderTif(
  zipPath: string, filename: string, typeHint: string, label: string,
): Promise<RasterOverlay> {
  const url = `${API}/api/render-tif?zip=${encodeURIComponent(zipPath)}&file=${encodeURIComponent(filename)}&type_hint=${encodeURIComponent(typeHint)}`
  const resp = await fetch(url)
  if (!resp.ok) throw new Error(`render-tif ${resp.status}`)
  const d = await resp.json()

  // PNG blob URL
  const pngBytes = Uint8Array.from(atob(d.png_b64), c => c.charCodeAt(0))
  const imgUrl   = URL.createObjectURL(new Blob([pngBytes], { type: 'image/png' }))

  // Float32 pixel data for hover
  const rawBuf   = Uint8Array.from(atob(d.pixel_b64), c => c.charCodeAt(0)).buffer
  const pixelData = new Float32Array(rawBuf)

  const id = `${zipPath}|${filename}`
  return {
    id,
    url:   imgUrl,
    bounds: d.bounds as [number,number,number,number],
    pixelData,
    width:  d.pixel_width,
    height: d.pixel_height,
    nodata: d.nodata,
    type:   d.type,
    label,
    vmin:   d.vmin,
    vmax:   d.vmax,
  }
}

// ── L3: Interferogram Viewer Drawer ──────────────────────────────────────────

interface IfgPair {
  name:   string
  zip:    string
  files:  { filename: string; type: string }[]
  bounds: [number, number, number, number] | null
}

interface IfgViewerProps {
  theme:          Theme
  folderPath:     string
  onClose:        () => void
  onRasterSelect: (overlay: RasterOverlay | null) => void
  rightOffset:    number
}

function IfgViewerDrawer({ theme: t, folderPath, onClose, onRasterSelect, rightOffset }: IfgViewerProps) {
  const { width, onHandleMouseDown } = useResizable(300)
  const [pairs,        setPairs]       = useState<IfgPair[]>([])
  const [loading,      setLoading]     = useState(true)
  const [error,        setError]       = useState('')
  const [active,       setActive]      = useState<string | null>(null)
  const [decoding,     setDecoding]    = useState(false)
  const [expandedPair, setExpandedPair] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    fetch(`${API}/api/folder-ifg-list?path=${encodeURIComponent(folderPath)}`)
      .then(r => r.json())
      .then(d => { if (d.detail) { setError(d.detail); setLoading(false); return } setPairs(d.pairs ?? []); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }, [folderPath])

  async function handleFileClick(pair: IfgPair, f: { filename: string; type: string }) {
    const key = `${pair.zip}|${f.filename}`
    if (active === key) { setActive(null); onRasterSelect(null); return }
    if (!pair.bounds) { setActive(key); return }
    setActive(key); setDecoding(true)
    try {
      const label   = `${pair.name} · ${f.type}`
      const overlay = await renderTif(pair.zip, f.filename, f.type, label)
      onRasterSelect(overlay)
    } catch(e) { console.error(e) }
    setDecoding(false)
  }

  const TYPE_COLORS: Record<string, string> = {
    unw_phase: '#7986cb', corr: '#80cbc4', dem: '#a5d6a7',
    lv_theta: '#ffcc80', lv_phi: '#f48fb1', water_mask: '#90caf9',
  }

  return (
    <div style={{
      position: 'fixed', top: 48, right: rightOffset, bottom: 0, width,
      background: t.bg, borderLeft: `1px solid ${t.border}`,
      display: 'flex', flexDirection: 'column', zIndex: 113,
      boxShadow: '-4px 0 20px rgba(0,0,0,0.25)',
    }}>
      <ResizeHandle onMouseDown={onHandleMouseDown} />
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '10px 14px', borderBottom: `1px solid ${t.border}`,
        background: t.bg2, flexShrink: 0,
      }}>
        <span style={{ color: t.text, fontWeight: 600, fontSize: 12 }}>Data</span>
        <button onClick={onClose} style={{
          background: 'none', border: 'none', cursor: 'pointer',
          color: t.textMuted, fontSize: 20, lineHeight: 1, padding: '0 4px',
        }}>×</button>
      </div>

      {decoding && (
        <div style={{ padding: '4px 14px', background: '#0d3b6e', fontSize: 10, color: '#90caf9' }}>
          Decoding TIF…
        </div>
      )}

      <div style={{ flex: 1, overflowY: 'auto' }}>
        {loading ? (
          <div style={{ color: t.textMuted, fontSize: 11, textAlign: 'center', padding: '32px 0' }}>Loading…</div>
        ) : error ? (
          <div style={{ color: '#e53935', fontSize: 11, padding: 14 }}>{error}</div>
        ) : pairs.length === 0 ? (
          <div style={{ color: t.textMuted, fontSize: 11, textAlign: 'center', padding: '32px 0' }}>
            No zip files found. Download results first.
          </div>
        ) : pairs.map(pair => {
          const isExpanded = expandedPair === pair.zip
          return (
            <div key={pair.zip} style={{ borderBottom: `1px solid ${t.divider}` }}>
              {/* Pair header — click to expand/collapse */}
              <button
                onClick={() => setExpandedPair(isExpanded ? null : pair.zip)}
                style={{
                  width: '100%', display: 'flex', alignItems: 'center', gap: 6,
                  padding: '6px 14px', background: t.bg2,
                  border: 'none', cursor: 'pointer', textAlign: 'left',
                }}
              >
                <span style={{ fontSize: 9, color: t.textMuted, flexShrink: 0 }}>
                  {isExpanded ? '▾' : '▸'}
                </span>
                <span style={{ color: t.text, fontSize: 10, fontFamily: 'monospace' }}>{pair.name}</span>
              </button>
              {/* Files — only shown when expanded */}
              {isExpanded && pair.files.map(f => {
                const key = `${pair.zip}|${f.filename}`
                const isActive = active === key
                const color = TYPE_COLORS[f.type] ?? t.textMuted
                return (
                  <button key={f.filename}
                    onClick={() => handleFileClick(pair, f)}
                    style={{
                      width: '100%', display: 'flex', alignItems: 'center', gap: 8,
                      padding: '5px 14px 5px 28px',
                      background: isActive ? t.btnActiveBg : 'transparent',
                      border: 'none', cursor: 'pointer', textAlign: 'left',
                    }}
                  >
                    <span style={{
                      display: 'inline-block', width: 8, height: 8, borderRadius: 2,
                      background: color, flexShrink: 0,
                    }} />
                    <span style={{ fontSize: 10, fontFamily: 'monospace', color: isActive ? t.accent : t.text }}>
                      {f.type}
                    </span>
                    {!pair.bounds && (
                      <span style={{ fontSize: 9, color: t.textMuted, marginLeft: 'auto' }}>no bounds</span>
                    )}
                  </button>
                )
              })}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Processor Panel (HyP3 job actions) ───────────────────────────────────────

interface Hyp3File { name: string; total: number; users: string[] }

interface ProcessorPanelProps { theme: Theme; folderPath: string; processorType: string; onFolderRefresh: () => void; ifgViewerOpen: boolean; onViewIfgToggle: () => void }

function ProcessorPanel({ theme: t, folderPath, processorType, onFolderRefresh, ifgViewerOpen, onViewIfgToggle }: ProcessorPanelProps) {
  const [files,      setFiles]      = useState<Hyp3File[]>([])
  const [loading,    setLoading]    = useState(true)
  const [selected,   setSelected]   = useState('')
  const [actionMsg,     setActionMsg]     = useState('')
  const [actionStat,    setActionStat]    = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [actionProgress, setActionProgress] = useState(0)
  const [currentAction,  setCurrentAction]  = useState('')
  const [activeJobId,    setActiveJobId]    = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const [analyzers,        setAnalyzers]        = useState<string[]>([])
  const [selectedAnalyzer, setSelectedAnalyzer] = useState('')
  const [analyzerMsg,      setAnalyzerMsg]      = useState('')
  const [analyzerStat,     setAnalyzerStat]     = useState<'idle' | 'ok' | 'error'>('idle')

  function loadFiles() {
    setLoading(true)
    fetch(`${API}/api/folder-hyp3-jobs?path=${encodeURIComponent(folderPath)}`)
      .then(r => r.json())
      .then(d => {
        const fs: Hyp3File[] = d.files ?? []
        setFiles(fs)
        setSelected(prev => {
          if (fs.length === 0) return ''
          if (prev && fs.some(f => f.name === prev)) return prev
          return fs[fs.length - 1].name
        })
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }

  useEffect(() => {
    Promise.all([
      fetch(`${API}/api/settings`).then(r => r.json()),
      fetch(`${API}/api/workflows`).then(r => r.json()),
    ]).then(([settings, workflows]) => {
      const names: string[] = Object.keys(workflows.analyzers ?? {})
      setAnalyzers(names)
      setSelectedAnalyzer(settings.analyzer || names[0] || '')
    }).catch(() => {})
  }, [])

  function runInitAnalyzer() {
    if (!selectedAnalyzer) return
    setAnalyzerStat('idle')
    setAnalyzerMsg('Initializing…')
    fetch(`${API}/api/folder-init-analyzer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_path: folderPath, analyzer_type: selectedAnalyzer }),
    })
      .then(r => r.json())
      .then(d => {
        if (d.ok) {
          setAnalyzerStat('ok')
          setAnalyzerMsg(`Analyzer set to ${d.analyzer}`)
          onFolderRefresh()
        } else {
          setAnalyzerStat('error')
          setAnalyzerMsg(d.detail ?? 'Failed')
        }
      })
      .catch(e => { setAnalyzerStat('error'); setAnalyzerMsg(String(e)) })
  }

  useEffect(() => { loadFiles() }, [folderPath])
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  function runAction(action: string) {
    if (!selected) return
    setCurrentAction(action)
    setActionStat('running')
    setActionProgress(0)
    setActionMsg(action === 'refresh' ? 'Refreshing…' : action === 'retry' ? 'Retrying…' : 'Downloading…')
    fetch(`${API}/api/folder-hyp3-action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_path: folderPath, job_file: selected, action, processor_type: processorType }),
    })
      .then(r => r.json())
      .then(({ job_id }) => {
        setActiveJobId(job_id)
        pollRef.current = setInterval(async () => {
          const r = await fetch(`${API}/api/jobs/${job_id}`)
          const job = await r.json()
          setActionMsg(job.message ?? '')
          setActionProgress(job.progress ?? 0)
          if (job.status === 'done') {
            clearInterval(pollRef.current!); setActionStat('done'); setActionProgress(100); setActiveJobId(null)
            if (action === 'retry') loadFiles()
          } else if (job.status === 'error') {
            clearInterval(pollRef.current!); setActionStat('error'); setActiveJobId(null)
          }
        }, 1500)
      })
      .catch(e => { setActionStat('error'); setActionMsg(String(e)) })
  }

  function stopDownload() {
    if (!activeJobId) return
    fetch(`${API}/api/jobs/${activeJobId}/stop`, { method: 'POST' }).catch(() => {})
  }

  const busy = actionStat === 'running'
  const rc = ROLE_COLORS.processor

  const btnStyle = (active: boolean): React.CSSProperties => ({
    flex: 1, padding: '6px 0', fontSize: 11, borderRadius: 4, cursor: busy ? 'default' : 'pointer',
    background: active ? rc.bg : 'transparent',
    color: active ? rc.color : t.text,
    border: `1px solid ${active ? rc.border : t.border}`,
    opacity: busy ? 0.6 : 1,
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {loading ? (
        <span style={{ color: t.textMuted, fontSize: 11 }}>Loading…</span>
      ) : files.length === 0 ? (
        <span style={{ color: t.textMuted, fontSize: 11 }}>No HyP3 job files found.</span>
      ) : (
        <>
          {/* File selector */}
          <div>
            <div style={{ color: t.textMuted, fontSize: 10, marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.04em' }}>Job file</div>
            <select
              value={selected}
              onChange={e => { setSelected(e.target.value); setActionStat('idle'); setActionMsg('') }}
              style={{
                width: '100%', background: t.inputBg, border: `1px solid ${t.inputBorder}`,
                color: t.text, borderRadius: 4, padding: '4px 6px', fontSize: 11,
                fontFamily: 'monospace', colorScheme: t.isDark ? 'dark' : 'light',
              }}
            >
              {files.map(f => (
                <option key={f.name} value={f.name}>{f.name} ({f.total} jobs)</option>
              ))}
            </select>
          </div>

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: 6 }}>
            <button disabled={busy} onClick={() => runAction('refresh')} style={btnStyle(false)}>
              Refresh
            </button>
            <button disabled={busy} onClick={() => runAction('retry')} style={btnStyle(false)}>
              Retry
            </button>
            {currentAction === 'download' && actionStat === 'running' ? (
              <button onClick={stopDownload} style={{ ...btnStyle(true), background: '#e53935', borderColor: '#e53935', color: '#fff' }}>
                Stop
              </button>
            ) : (
              <button disabled={busy} onClick={() => runAction('download')} style={btnStyle(true)}>
                Download
              </button>
            )}
          </div>

          {/* View Data */}
          <button
            onClick={() => onViewIfgToggle()}
            style={{
              width: '100%', padding: '6px 12px', fontSize: 11, textAlign: 'left',
              background: ifgViewerOpen ? '#0d3b6e' : 'transparent',
              color: ifgViewerOpen ? '#90caf9' : t.text,
              border: `1px solid ${ifgViewerOpen ? '#1565c0' : t.border}`,
              borderRadius: 4, cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 8,
            }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/>
              <polyline points="21 15 16 10 5 21"/>
            </svg>
            {ifgViewerOpen ? 'Hide Data' : 'View Data'}
          </button>

          {/* Progress bar — shown during download */}
          {currentAction === 'download' && actionStat === 'running' && (
            <div style={{ background: t.bg2, borderRadius: 3, border: `1px solid ${t.divider}`, overflow: 'hidden', height: 6 }}>
              <div style={{
                height: '100%', width: `${actionProgress}%`,
                background: ROLE_COLORS.processor.bg, transition: 'width 0.4s ease',
              }} />
            </div>
          )}

          {/* Status message */}
          {actionMsg && (
            <div style={{
              fontSize: 10, fontFamily: 'monospace', padding: '5px 8px', borderRadius: 3,
              background: t.bg2, border: `1px solid ${t.divider}`,
              color: actionStat === 'done' ? '#4caf50' : actionStat === 'error' ? '#e53935' : t.textMuted,
              whiteSpace: 'pre', overflowX: 'auto', maxHeight: 300, overflowY: 'auto',
            }}>{actionMsg}</div>
          )}

          {/* ── Analyzer section ── */}
          {analyzers.length > 0 && (
            <div style={{ borderTop: `1px solid ${t.divider}`, marginTop: 4, paddingTop: 8 }}>
              <div style={{ color: t.textMuted, fontSize: 10, marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.04em' }}>Run Analyzer</div>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <select
                  value={selectedAnalyzer}
                  onChange={e => { setSelectedAnalyzer(e.target.value); setAnalyzerStat('idle'); setAnalyzerMsg('') }}
                  style={{
                    flex: 1, fontSize: 11, padding: '3px 5px', borderRadius: 4,
                    background: t.bg2, color: t.text, border: `1px solid ${t.border}`,
                  }}
                >
                  {analyzers.map(a => <option key={a} value={a}>{a}</option>)}
                </select>
                <button onClick={runInitAnalyzer} style={{
                  fontSize: 11, padding: '3px 10px', borderRadius: 4, cursor: 'pointer',
                  background: ROLE_COLORS.analyzer.bg, color: ROLE_COLORS.analyzer.color,
                  border: `1px solid ${ROLE_COLORS.analyzer.border}`,
                }}>Init</button>
              </div>
              {analyzerMsg && (
                <div style={{
                  marginTop: 4, fontSize: 10, fontFamily: 'monospace', padding: '3px 6px', borderRadius: 3,
                  background: t.bg2, border: `1px solid ${t.divider}`,
                  color: analyzerStat === 'ok' ? '#4caf50' : analyzerStat === 'error' ? '#e53935' : t.textMuted,
                }}>{analyzerMsg}</div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}


// ── L3: MintPy Results Viewer ─────────────────────────────────────────────────

interface MintpyViewerProps {
  theme:          Theme
  folderPath:     string
  tsList:         string[]
  hidden:         boolean
  onClose:        () => void
  onRasterSelect: (overlay: RasterOverlay | null) => void
  rightOffset:    number
}

function MintpyViewerDrawer({ theme: t, folderPath, tsList, hidden, onClose, onRasterSelect, rightOffset }: MintpyViewerProps) {
  const { width, onHandleMouseDown } = useResizable(260)
  const [active,         setActive]         = useState(false)
  const [decoding,       setDecoding]       = useState(false)
  const [error,          setError]          = useState('')
  const [boundsInfo,     setBoundsInfo]     = useState<string>('')
  const [selectedTsFile, setSelectedTsFile] = useState<string | null>(tsList[0] ?? null)
  const rc = ROLE_COLORS.analyzer

  async function handleVelocityClick() {
    if (active) { setActive(false); onRasterSelect(null); setBoundsInfo(''); return }
    setDecoding(true); setError('')
    try {
      const resp = await fetch(`${API}/api/render-velocity?path=${encodeURIComponent(folderPath)}`)
      if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.detail ?? `HTTP ${resp.status}`) }
      const d = await resp.json()
      const [W, S, E, N] = d.bounds as number[]
      setBoundsInfo(`W:${W?.toFixed(3)} S:${S?.toFixed(3)} E:${E?.toFixed(3)} N:${N?.toFixed(3)}`)
      const pngBytes  = Uint8Array.from(atob(d.png_b64), c => c.charCodeAt(0))
      const imgUrl    = URL.createObjectURL(new Blob([pngBytes], { type: 'image/png' }))
      const pixelData = new Float32Array(Uint8Array.from(atob(d.pixel_b64), c => c.charCodeAt(0)).buffer)
      onRasterSelect({
        id:        `mintpy:${folderPath}:velocity`,
        url:       imgUrl,
        bounds:    d.bounds as [number, number, number, number],
        pixelData,
        width:     d.pixel_width,
        height:    d.pixel_height,
        nodata:    null,
        type:      'velocity',
        label:     `Velocity (${d.unit ?? 'm/year'})`,
        vmin:      d.vmin,
        vmax:      d.vmax,
        source:    { kind: 'mintpy', folderPath, tsFile: selectedTsFile },
      })
      setActive(true)
    } catch (e) { setError(String(e)) }
    setDecoding(false)
  }

  return (
    <div style={{
      position: 'fixed', top: 48, right: rightOffset, bottom: 0, width,
      background: t.bg, borderLeft: `1px solid ${t.border}`,
      display: 'flex', flexDirection: 'column', zIndex: 113,
      boxShadow: '-4px 0 20px rgba(0,0,0,0.25)',
      visibility: hidden ? 'hidden' : 'visible',
      pointerEvents: hidden ? 'none' : 'auto',
    }}>
      <ResizeHandle onMouseDown={onHandleMouseDown} />
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '10px 14px', borderBottom: `1px solid ${t.border}`,
        background: t.bg2, flexShrink: 0,
      }}>
        <span style={{ color: t.text, fontWeight: 600, fontSize: 12 }}>Results</span>
        <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer',
          color: t.textMuted, fontSize: 20, lineHeight: 1, padding: '0 4px' }}>×</button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '10px 14px', display: 'flex', flexDirection: 'column', gap: 8 }}>
        {/* Time series file selector */}
        {tsList.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 10, color: t.textMuted, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Time Series File
            </span>
            {tsList.map(f => (
              <button
                key={f}
                onClick={() => setSelectedTsFile(f)}
                style={{
                  width: '100%', padding: '5px 10px', borderRadius: 4, textAlign: 'left',
                  fontSize: 10, fontFamily: 'monospace', cursor: 'pointer',
                  background: selectedTsFile === f ? rc.bg : 'transparent',
                  color: selectedTsFile === f ? rc.color : t.textMuted,
                  border: `1px solid ${selectedTsFile === f ? rc.border : t.border}`,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}
                title={f}
              >
                {selectedTsFile === f ? '● ' : '○ '}{f}
              </button>
            ))}
          </div>
        )}

        {error && <span style={{ color: '#e53935', fontSize: 10 }}>{error}</span>}

        {boundsInfo && (
          <span style={{ fontSize: 9, color: t.textMuted, fontFamily: 'monospace', wordBreak: 'break-all' }}>
            {boundsInfo}
          </span>
        )}

        {active && (
          <div style={{
            padding: '8px 10px', borderRadius: 4, fontSize: 10, lineHeight: 1.6,
            background: t.bg2, border: `1px solid ${t.divider}`, color: t.textMuted,
          }}>
            Click on the velocity map to extract the time series at that location.
          </div>
        )}
      </div>

      {/* Plot button fixed at the bottom */}
      <div style={{ padding: '10px 14px', borderTop: `1px solid ${t.border}`, flexShrink: 0 }}>
        <button
          onClick={handleVelocityClick}
          disabled={decoding}
          style={{
            width: '100%', padding: '8px 12px', borderRadius: 4,
            background: active ? rc.bg : t.btnActiveBg,
            color: active ? rc.color : t.btnActiveFg,
            border: `1px solid ${active ? rc.border : t.btnActiveBorder}`,
            cursor: decoding ? 'wait' : 'pointer', fontSize: 12, fontWeight: 600,
          }}
        >
          {decoding ? 'Loading…' : active ? 'Hide Velocity' : 'Plot'}
        </button>
      </div>
    </div>
  )
}


// ── L2: Role drawer ───────────────────────────────────────────────────────────

interface L2Props {
  theme:             Theme
  job:               JobFolder
  role:              string
  cls:               string
  hidden:            boolean
  mapClickSignal:    number
  onClose:           () => void
  onFolderRefresh:   () => void
  onRasterSelect:    (overlay: RasterOverlay | null) => void
  onSettingsOpen:    (analyzerType: string) => void
  rightOffset:       number
}

function JobRoleDrawer({ theme: t, job, role, cls, hidden, mapClickSignal, onClose, onFolderRefresh, onRasterSelect, onSettingsOpen, rightOffset }: L2Props) {
  const { width, onHandleMouseDown } = useResizable(240)
  const rc = ROLE_COLORS[role] ?? ROLE_FALLBACK

  // Downloader-specific state
  const [details,      setDetails]      = useState<FolderDetails | null>(null)
  const [detLoading,   setDetLoading]   = useState(false)
  const [netEditorOpen, setNetEditorOpen] = useState(false)
  const [pairsOpen,    setPairsOpen]    = useState(false)
  const [procOpen,     setProcOpen]     = useState(false)
  const [dlJobId,      setDlJobId]      = useState<string | null>(() => _dlJobs.get(job.path) ?? null)
  const [dlStatus,     setDlStatus]     = useState<string>('')
  const [orbitJobId,   setOrbitJobId]   = useState<string | null>(() => _orbitJobs.get(job.path) ?? null)
  const [orbitStatus,  setOrbitStatus]  = useState<string>('')
  const [ifgViewerOpen,     setIfgViewerOpen]     = useState(false)
  const [mintpyViewerOpen,     setMintpyViewerOpen]     = useState(false)
  const [mintpyViewerEverOpen, setMintpyViewerEverOpen] = useState(false)
  const [mintpyHasData,     setMintpyHasData]     = useState(false)
  const [mintpyTsList,      setMintpyTsList]      = useState<string[]>([])

  // Close all L3/L4 sub-panels when the map is clicked
  useEffect(() => {
    if (mapClickSignal === 0) return
    setPairsOpen(false)

    setProcOpen(false)
    setIfgViewerOpen(false)
    setMintpyViewerOpen(false)
    setNetEditorOpen(false)
  }, [mapClickSignal])

  function loadDetails() {
    setDetLoading(true)
    fetch(`${API}/api/folder-details?path=${encodeURIComponent(job.path)}`)
      .then(r => r.json())
      .then(d => { setDetails(d); setDetLoading(false) })
      .catch(() => setDetLoading(false))
  }

  useEffect(() => {
    if (role !== 'downloader') return
    loadDetails()
  }, [job.path, role])

  useEffect(() => {
    if (role !== 'analyzer') return
    fetch(`${API}/api/mintpy-check?path=${encodeURIComponent(job.path)}`)
      .then(r => r.json())
      .then(d => {
        const tsList: string[] = Array.isArray(d.timeseries_files) ? d.timeseries_files : []
        setMintpyTsList(tsList)
        setMintpyHasData(d.has_velocity && tsList.length > 0)
      })
      .catch(() => {})
  }, [job.path, role])

  // Poll download job
  useEffect(() => {
    if (!dlJobId) return
    _dlJobs.set(job.path, dlJobId)
    const id = setInterval(() => {
      fetch(`${API}/api/jobs/${dlJobId}`)
        .then(r => r.json())
        .then(d => {
          setDlStatus(d.message ?? '')
          if (d.status === 'done' || d.status === 'error') {
            clearInterval(id)
            _dlJobs.delete(job.path)
            setDlJobId(null)
          }
        })
        .catch(() => { clearInterval(id); _dlJobs.delete(job.path); setDlJobId(null) })
    }, 1500)
    return () => clearInterval(id)
  }, [dlJobId])

  function handleDownload() {
    setDlStatus('Starting…')
    fetch(`${API}/api/folder-download`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_path: job.path }),
    })
      .then(r => r.json())
      .then(d => { if (d.job_id) setDlJobId(d.job_id) })
      .catch(e => setDlStatus(String(e)))
  }

  // Poll orbit download job
  useEffect(() => {
    if (!orbitJobId) return
    _orbitJobs.set(job.path, orbitJobId)
    const id = setInterval(() => {
      fetch(`${API}/api/jobs/${orbitJobId}`)
        .then(r => r.json())
        .then(d => {
          setOrbitStatus(d.message ?? '')
          if (d.status === 'done' || d.status === 'error') {
            clearInterval(id)
            _orbitJobs.delete(job.path)
            setOrbitJobId(null)
          }
        })
        .catch(() => { clearInterval(id); _orbitJobs.delete(job.path); setOrbitJobId(null) })
    }, 1500)
    return () => clearInterval(id)
  }, [orbitJobId])

  function handleDownloadOrbit() {
    setOrbitStatus('Starting…')
    fetch(`${API}/api/folder-download-orbit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_path: job.path }),
    })
      .then(r => r.json())
      .then(d => { if (d.job_id) setOrbitJobId(d.job_id) })
      .catch(e => setOrbitStatus(String(e)))
  }

  function handleStopDownload() {
    if (!dlJobId) return
    fetch(`${API}/api/jobs/${dlJobId}/stop`, { method: 'POST' }).catch(() => {})
    _dlJobs.delete(job.path)
    setDlJobId(null)
    setDlStatus('Stopped.')
  }

  function handleStopOrbit() {
    if (!orbitJobId) return
    fetch(`${API}/api/jobs/${orbitJobId}/stop`, { method: 'POST' }).catch(() => {})
    _orbitJobs.delete(job.path)
    setOrbitJobId(null)
    setOrbitStatus('Stopped.')
  }

  const cfgRows = role === 'downloader' && details?.downloader_config
    ? CFG_FIELDS
        .map(({ key, label }) => {
          const raw = details.downloader_config![key]
          return { label, val: fmtVal(key, raw), full: String(raw ?? '') }
        })
        .filter(r => r.val !== '')
    : []

  const [copiedCfgKey, setCopiedCfgKey] = useState<string | null>(null)
  function copyCfgVal(label: string, val: string) {
    navigator.clipboard.writeText(val)
    setCopiedCfgKey(label)
    setTimeout(() => setCopiedCfgKey(null), 1200)
  }

  return (
    <>

      {/* Interactive network editor */}
      {!hidden && netEditorOpen && (
        <NetworkEditor
          theme={t}
          folderPath={job.path}
          onClose={() => setNetEditorOpen(false)}
          onSaved={() => { setNetEditorOpen(false); loadDetails() }}
          initParamsOpen={!details?.has_pairs}
        />
      )}

      {/* L3 pairs drawer */}
      {!hidden && pairsOpen && (
        <PairsDrawer
          theme={t}
          folderPath={job.path}
          onClose={() => setPairsOpen(false)}
          rightOffset={rightOffset + width}
        />
      )}

      {/* L3 MintPy results viewer — kept mounted once opened so collapsed state survives */}
      {mintpyViewerEverOpen && (
        <MintpyViewerDrawer
          theme={t}
          folderPath={job.path}
          tsList={mintpyTsList}
          hidden={hidden}
          onClose={() => { setMintpyViewerOpen(false); setMintpyViewerEverOpen(false) }}
          onRasterSelect={onRasterSelect}
          rightOffset={rightOffset + width}
        />
      )}

      {/* L3 interferogram viewer */}
      {!hidden && ifgViewerOpen && (
        <IfgViewerDrawer
          theme={t}
          folderPath={job.path}
          onClose={() => setIfgViewerOpen(false)}
          onRasterSelect={onRasterSelect}
          rightOffset={rightOffset + width}
        />
      )}

      {/* Process modal */}
      {!hidden && procOpen && (
        <ProcessModal
          theme={t}
          folderPath={job.path}
          downloaderType={job.workflow.downloader || cls}
          onClose={() => setProcOpen(false)}
          onDone={() => { setProcOpen(false); loadDetails() }}
        />
      )}

      <div style={{
        position: 'fixed', top: 48, right: rightOffset, bottom: 0,
        width,
        background: t.bg, borderLeft: `1px solid ${t.border}`,
        display: 'flex', flexDirection: 'column',
        zIndex: 112,
        boxShadow: '-4px 0 20px rgba(0,0,0,0.25)',
        visibility: hidden ? 'hidden' : 'visible',
        pointerEvents: hidden ? 'none' : 'auto',
      }}>
        <ResizeHandle onMouseDown={onHandleMouseDown} />
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '12px 16px', borderBottom: `1px solid ${t.border}`,
          background: t.bg2, flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{
              fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 3,
              background: rc.bg, color: rc.color, border: `1px solid ${rc.border}`,
              textTransform: 'capitalize',
            }}>{role}</span>
            <span style={{ color: t.text, fontWeight: 700, fontSize: 13 }}>{cls}</span>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none',
            cursor: 'pointer', color: t.textMuted, fontSize: 20, lineHeight: 1, padding: '0 4px' }}>×</button>
        </div>

        {/* Folder context */}
        <div style={{
          padding: '6px 16px', borderBottom: `1px solid ${t.divider}`,
          background: t.bg2, flexShrink: 0,
        }}>
          <span style={{ color: t.textMuted, fontSize: 10 }}>Folder: </span>
          <span style={{ color: t.text, fontSize: 11, fontFamily: 'monospace' }}>{job.name}</span>
        </div>

        {/* Content */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '10px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>

          {/* ── Downloader: config params ── */}
          {role === 'downloader' && (
            detLoading ? (
              <span style={{ color: t.textMuted, fontSize: 11 }}>Loading…</span>
            ) : cfgRows.length > 0 ? (
              <div style={{ border: `1px solid ${t.border}`, borderRadius: 4, overflow: 'hidden' }}>
                {cfgRows.map(({ label, val, full }, i) => (
                  <div key={label} onClick={() => copyCfgVal(label, full)}
                    style={{
                      display: 'flex', gap: 8, padding: '5px 10px',
                      background: i % 2 === 0 ? t.bg : t.bg2,
                      borderBottom: i < cfgRows.length - 1 ? `1px solid ${t.divider}` : 'none',
                      cursor: 'copy',
                    }}>
                    <span style={{ color: t.textMuted, fontSize: 10, width: 72, flexShrink: 0 }}>{label}</span>
                    <span style={{
                      color: copiedCfgKey === label ? '#4caf50' : t.text,
                      fontSize: 10, fontFamily: 'monospace',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      transition: 'color 0.2s',
                    }} title={val}>{val}{copiedCfgKey === label ? ' ✓' : ''}</span>
                  </div>
                ))}
              </div>
            ) : null
          )}

          {/* ── Downloader: edit network (always visible for downloader jobs) ── */}
          {role === 'downloader' && (
            <button
              onClick={() => setNetEditorOpen(true)}
              style={{
                width: '100%', padding: '7px 12px', fontSize: 11, textAlign: 'left',
                background: netEditorOpen ? '#1a2e1a' : 'transparent',
                color: netEditorOpen ? '#a5d6a7' : t.text,
                border: `1px solid ${netEditorOpen ? '#2e7d32' : t.border}`,
                borderRadius: 4, cursor: 'pointer',
                display: 'flex', alignItems: 'center', gap: 8,
              }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="5" cy="12" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="19" cy="19" r="2"/>
                <line x1="7" y1="11" x2="17" y2="6"/><line x1="7" y1="13" x2="17" y2="18"/>
              </svg>
              Edit Network
            </button>
          )}

          {/* ── Downloader: view pairs (L3 drawer) ── */}
          {role === 'downloader' && details?.has_pairs && (
            <button
              onClick={() => setPairsOpen(o => !o)}
              style={{
                width: '100%', padding: '7px 12px', fontSize: 11, textAlign: 'left',
                background: pairsOpen ? rc.bg : 'transparent',
                color: pairsOpen ? rc.color : t.text,
                border: `1px solid ${pairsOpen ? rc.border : t.border}`,
                borderRadius: 4, cursor: 'pointer',
                display: 'flex', alignItems: 'center', gap: 8,
              }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/>
                <line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/>
                <line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>
              </svg>
              {pairsOpen ? 'Hide Pairs' : 'View Pairs'}
            </button>
          )}

          {/* Select Pairs is now inside Edit Network (⚙ Parameters panel) */}

          {/* ── Downloader: process ── */}
          {role === 'downloader' && (
            <button
              onClick={() => setProcOpen(o => !o)}
              style={{
                width: '100%', padding: '7px 12px', fontSize: 11, textAlign: 'left',
                background: procOpen ? '#4a2500' : 'transparent',
                color: procOpen ? '#ffcc80' : t.text,
                border: `1px solid ${procOpen ? '#e65100' : t.border}`,
                borderRadius: 4, cursor: 'pointer',
                display: 'flex', alignItems: 'center', gap: 8,
              }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="3"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
                <path d="M4.93 4.93a10 10 0 0 0 0 14.14"/>
              </svg>
              Process
            </button>
          )}

          {/* ── Downloader: download button ── */}
          {role === 'downloader' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {dlJobId ? (
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <button
                    onClick={handleStopDownload}
                    style={{
                      flex: '0 0 auto', padding: '7px 12px', fontSize: 11,
                      background: '#e53935', color: '#fff',
                      border: '1px solid #e53935',
                      borderRadius: 4, cursor: 'pointer',
                    }}
                  >
                    ■ Stop
                  </button>
                  {dlStatus && (
                    <span style={{ fontSize: 10, color: t.textMuted, fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{dlStatus}</span>
                  )}
                </div>
              ) : (
                <>
                  <button
                    onClick={handleDownload}
                    style={{
                      width: '100%', padding: '7px 12px', fontSize: 11,
                      background: rc.bg, color: rc.color,
                      border: `1px solid ${rc.border}`,
                      borderRadius: 4, cursor: 'pointer',
                      display: 'flex', alignItems: 'center', gap: 8,
                    }}
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                      <polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
                    </svg>
                    Download
                  </button>
                  {dlStatus && (
                    <span style={{ fontSize: 10, color: t.textMuted, fontFamily: 'monospace' }}>{dlStatus}</span>
                  )}
                </>
              )}
              {cls === 'S1_SLC' && (
                <>
                  {orbitJobId ? (
                    <button
                      onClick={handleStopOrbit}
                      style={{
                        width: '100%', padding: '7px 12px', fontSize: 11,
                        background: '#e53935', color: '#fff',
                        border: '1px solid #e53935',
                        borderRadius: 4, cursor: 'pointer',
                        display: 'flex', alignItems: 'center', gap: 8,
                      }}
                    >
                      Stop
                    </button>
                  ) : (
                    <button
                      onClick={handleDownloadOrbit}
                      style={{
                        width: '100%', padding: '7px 12px', fontSize: 11,
                        background: 'transparent', color: t.text,
                        border: `1px solid ${t.border}`,
                        borderRadius: 4, cursor: 'pointer',
                        display: 'flex', alignItems: 'center', gap: 8,
                      }}
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(-30 12 12)"/>
                        <ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(30 12 12)"/>
                        <circle cx="12" cy="12" r="2"/>
                      </svg>
                      Download Orbit Files
                    </button>
                  )}
                  {orbitStatus && (
                    <span style={{ fontSize: 10, color: t.textMuted, fontFamily: 'monospace' }}>{orbitStatus}</span>
                  )}
                </>
              )}
            </div>
          )}

          {/* ── Processor: HyP3 job actions ── */}
          {role === 'processor' && (
            <ProcessorPanel
              theme={t}
              folderPath={job.path}
              processorType={cls}
              onFolderRefresh={onFolderRefresh}
              ifgViewerOpen={ifgViewerOpen}
              onViewIfgToggle={() => setIfgViewerOpen(o => !o)}
            />
          )}

          {/* ── Analyzer: MintPy step runner ── */}
          {role === 'analyzer' && (
            <>
              <AnalyzerPanel theme={t} folderPath={job.path} analyzerType={cls} onSettingsOpen={onSettingsOpen} />
              {mintpyHasData && (
                <button
                  onClick={() => { setMintpyViewerOpen(o => !o); setMintpyViewerEverOpen(true) }}
                  style={{
                    width: '100%', padding: '6px 12px', fontSize: 11, textAlign: 'left',
                    background: mintpyViewerOpen ? ROLE_COLORS.analyzer.bg : 'transparent',
                    color: mintpyViewerOpen ? ROLE_COLORS.analyzer.color : t.text,
                    border: `1px solid ${mintpyViewerOpen ? ROLE_COLORS.analyzer.border : t.border}`,
                    borderRadius: 4, cursor: 'pointer',
                    display: 'flex', alignItems: 'center', gap: 8,
                  }}
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
                  </svg>
                  {mintpyViewerOpen ? 'Hide Results' : 'View Results'}
                </button>
              )}
            </>
          )}

          {/* ── Other roles: placeholder ── */}
          {role !== 'downloader' && role !== 'processor' && role !== 'analyzer' && (
            <span style={{ color: t.textMuted, fontSize: 11 }}>Actions coming soon.</span>
          )}
        </div>
      </div>
    </>
  )
}

// ── Main Drawer ───────────────────────────────────────────────────────────────

export default function JobQueueDrawer({ theme: t, workdir, mapClickSignal, onClose, onRasterSelect, onSettingsOpen }: Props) {
  const { width: l1Width, onHandleMouseDown: onL1Handle } = useResizable(260)
  const [jobs,    setJobs]    = useState<JobFolder[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState('')
  const [l2,        setL2]        = useState<{ job: JobFolder; role: string; cls: string } | null>(null)
  const [l2Visible, setL2Visible] = useState(false)
  const [minimized, setMinimized] = useState(false)
  const l2VisibleBeforeMinimize   = useRef(false)

  const loadJobs = () => {
    setLoading(true)
    setError('')
    fetch(`${API}/api/job-folders`)
      .then(r => r.json())
      .then(d => { setJobs(d.jobs ?? []); setLoading(false) })
      .catch(e => { setError(String(e)); setLoading(false) })
  }

  useEffect(() => { loadJobs() }, [workdir])

  return (
    <>
      {/* Standalone ‹‹‹ tab — only shown when minimized, lives outside hidden containers */}
      {minimized && (
        <button
          onClick={() => { setMinimized(false); setL2Visible(l2VisibleBeforeMinimize.current) }}
          title="Restore drawers"
          style={{
            position: 'fixed', right: 0, top: '50%', transform: 'translateY(-50%)',
            width: 28, height: 72, zIndex: 120,
            background: t.bg2, border: `1px solid ${t.border}`,
            borderRight: 'none', borderRadius: '6px 0 0 6px',
            cursor: 'pointer', color: t.textMuted, fontSize: 11,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            writingMode: 'vertical-rl', letterSpacing: 2,
          }}
        >‹‹‹</button>
      )}

      {/* Backdrop — click minimizes (hides) all drawers without unmounting */}
      {!minimized && (
        <div
          onClick={() => { l2VisibleBeforeMinimize.current = l2Visible; setL2Visible(false); setMinimized(true) }}
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.3)', zIndex: 110 }}
        />
      )}

      {/* L2 drawer — always mounted while l2 is set; hidden via CSS when not visible or minimized */}
      {l2 && (
        <JobRoleDrawer
          theme={t}
          job={l2.job}
          role={l2.role}
          cls={l2.cls}
          hidden={!l2Visible || minimized}
          mapClickSignal={mapClickSignal}
          rightOffset={l1Width}
          onClose={() => { setL2(null); setL2Visible(false) }}
          onFolderRefresh={loadJobs}
          onRasterSelect={onRasterSelect}
          onSettingsOpen={onSettingsOpen}
        />
      )}

      {/* Main drawer */}
      <div style={{
        position: 'fixed', top: 48, right: 0, bottom: 0,
        width: l1Width,
        background: t.bg, borderLeft: `1px solid ${t.border}`,
        display: 'flex', flexDirection: 'column',
        zIndex: 111,
        boxShadow: '-4px 0 24px rgba(0,0,0,0.3)',
        visibility: minimized ? 'hidden' : 'visible',
        pointerEvents: minimized ? 'none' : 'auto',
      }}>
        <ResizeHandle onMouseDown={onL1Handle} />
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '12px 16px', borderBottom: `1px solid ${t.border}`,
          background: t.bg2, flexShrink: 0,
        }}>
          <div>
            <span style={{ color: t.text, fontWeight: 700, fontSize: 14 }}>Job Folders</span>
            <span style={{ color: t.textMuted, fontSize: 11, marginLeft: 8 }}>{workdir}</span>
          </div>
          <button
            onClick={() => { setL2(null); setL2Visible(false); onClose() }}
            style={{ background: 'none', border: 'none', cursor: 'pointer',
                     color: t.textMuted, fontSize: 20, lineHeight: 1, padding: '0 4px' }}
          >×</button>
        </div>

        {/* Content */}
        <div style={{ overflowY: 'auto', flex: 1, padding: '10px 0' }}>
          {loading ? (
            <div style={{ color: t.textMuted, fontSize: 12, textAlign: 'center', padding: '40px 0' }}>
              Loading…
            </div>
          ) : error ? (
            <div style={{ color: '#e53935', fontSize: 12, padding: '16px' }}>{error}</div>
          ) : jobs.length === 0 ? (
            <div style={{ color: t.textMuted, fontSize: 12, textAlign: 'center', padding: '40px 16px' }}>
              No subfolders found in workdir.
            </div>
          ) : jobs.map(job => {
            const wfEntries = Object.entries(job.workflow).filter(([k]) => k !== 'updated_at')
            return (
              <div key={job.path} style={{ padding: '10px 16px', borderBottom: `1px solid ${t.divider}` }}>
                {/* Folder name + remove button */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: wfEntries.length ? 6 : 0 }}>
                  <span style={{
                    color: t.text, fontSize: 12, fontFamily: 'monospace',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1,
                  }} title={job.path}>
                    {job.name}
                  </span>
                  <button
                    title="Remove job folder"
                    onClick={() => {
                      if (!confirm(`Delete "${job.name}" and all its contents?`)) return
                      fetch(`${API}/api/job-folder?path=${encodeURIComponent(job.path)}`, { method: 'DELETE' })
                        .then(r => { if (r.ok) { if (l2?.job.path === job.path) { setL2(null); setL2Visible(false) } loadJobs() } })
                        .catch(() => {})
                    }}
                    style={{
                      background: 'none', border: 'none', cursor: 'pointer',
                      color: t.textMuted, padding: '0 2px', fontSize: 14, lineHeight: 1, flexShrink: 0,
                    }}
                  >🗑</button>
                </div>

                {/* Clickable role tags — ordered downloader → processor → analyzer */}
                {wfEntries.length > 0 && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
                    {(['downloader', 'processor', 'analyzer'] as const)
                      .filter(role => job.workflow[role])
                      .map((role, idx) => {
                        const cls     = job.workflow[role]
                        const rc      = ROLE_COLORS[role] ?? ROLE_FALLBACK
                        const isActive = l2?.job.path === job.path && l2?.role === role
                        return (
                          <div key={role} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                            {idx > 0 && (
                              <span style={{ color: t.textMuted, fontSize: 10, userSelect: 'none' }}>→</span>
                            )}
                            <button
                              onClick={() => {
                                if (isActive && l2Visible) {
                                  setL2Visible(false)
                                } else {
                                  setL2({ job, role, cls })
                                  setL2Visible(true)
                                }
                              }}
                              title={`${role}: ${cls}`}
                              style={{
                                fontSize: 10, fontWeight: 600,
                                padding: '2px 7px', borderRadius: 3,
                                background: isActive ? rc.color : rc.bg,
                                color:      isActive ? rc.bg    : rc.color,
                                border:     `1px solid ${rc.border}`,
                                cursor: 'pointer',
                              }}
                            >
                              {cls}
                            </button>
                          </div>
                        )
                      })}
                  </div>
                )}
              </div>
            )
          })}
        </div>

        {/* Footer */}
        <div style={{
          padding: '10px 16px', borderTop: `1px solid ${t.border}`,
          background: t.bg2, flexShrink: 0,
        }}>
          <button
            onClick={loadJobs}
            disabled={loading}
            style={{
              width: '100%', padding: '6px 0', fontSize: 12,
              background: 'transparent', color: loading ? t.textMuted : t.accent,
              border: `1px solid ${loading ? t.border : t.btnActiveBorder}`,
              borderRadius: 5, cursor: loading ? 'wait' : 'pointer',
            }}
          >
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
      </div>
    </>
  )
}
