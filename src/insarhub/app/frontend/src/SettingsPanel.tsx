import { useState, useEffect, useRef } from 'react'
import type { Theme } from './theme'

const API = import.meta.env.DEV ? 'http://localhost:8000' : ''

// ── Types ──────────────────────────────────────────────────────────────────

interface FieldMeta {
  key:      string
  label:    string
  type:     'select' | 'bool' | 'bool_str' | 'number' | 'auto_number' | 'text'
  default:  any
  options?: string[]
  min?:     number
  max?:     number
  step?:    number
  hint?:    string
}

interface ComponentMeta {
  label:                string
  description:          string
  fields:               FieldMeta[]
  groups?:              Array<{ label: string; fields: string[] }>
  compatible_downloader?: string | null
  compatible_processor?:  string | null
}

interface WorkflowsData {
  downloaders: Record<string, ComponentMeta>
  processors:  Record<string, ComponentMeta>
  analyzers:   Record<string, ComponentMeta>
}

interface ServerSettings {
  workdir:              string
  max_download_workers: number
  downloader:           string
  downloader_config:    Record<string, any>
  processor:            string
  processor_config:     Record<string, any>
  analyzer:             string
  analyzer_configs:     Record<string, Record<string, any>>
}

interface PoolAccount {
  username:           string
  credits_remaining?: number
  credits_per_month?: number
  error?:             string
}

interface AuthStatus {
  earthdata_connected?: boolean
  cdse_connected?:      boolean
  cds_connected?:       boolean
  credit_pool_exists?:  boolean
  hyp3?:                PoolAccount
  credit_pool:          PoolAccount[]
}

interface Props {
  theme:                   Theme
  onClose:                 () => void
  downloaderType:          string
  onDownloaderTypeChange:  (type: string) => void
  startDate:               string
  endDate:                 string
  aoiWkt:                  string | null
  onDatesChange:           (start: string, end: string) => void
  onAoiWktChange:          (wkt: string | null) => void
  initialTab?:             Tab
  initialAnalyzerType?:    string
}

type Tab = 'general' | 'auth' | 'downloader' | 'processor' | 'analyzer'



export default function SettingsPanel({ theme: t, onClose, downloaderType, onDownloaderTypeChange,
  startDate, endDate, aoiWkt, onDatesChange, onAoiWktChange,
  initialTab, initialAnalyzerType }: Props) {
  const [tab,         setTab]         = useState<Tab>(initialTab ?? 'general')
  const [loading,     setLoading]     = useState(true)
  const [authLoading, setAuthLoading] = useState(false)
  const [expandedCred, setExpandedCred] = useState<'earthdata'|'cdse'|'cds'|'hyp3'|'pool'|null>(null)
  const [credUser,  setCredUser]  = useState('')
  const [credPass,  setCredPass]  = useState('')
  const [credToken, setCredToken] = useState('')
  const [credSaving, setCredSaving] = useState(false)
  const [credMsg,   setCredMsg]   = useState('')
  const [saving,      setSaving]      = useState(false)
  const [saveMsg,     setSaveMsg]     = useState('')

  // General
  const [workdir,    setWorkdir]    = useState('')
  const [maxWorkers, setMaxWorkers] = useState(3)

  // Downloader (downloaderType is controlled by parent via prop)
  const [downloaderConfig, setDownloaderConfig] = useState<Record<string, any>>({})

  // Processor
  const [processorType,   setProcessorType]   = useState('Hyp3_InSAR')
  const [processorConfig, setProcessorConfig] = useState<Record<string, any>>({})

  // Analyzer — each type stores its own config independently
  const [analyzerType,       setAnalyzerType]       = useState('Hyp3_SBAS')
  const [analyzerConfig,     setAnalyzerConfig]     = useState<Record<string, any>>({})
  const [allAnalyzerConfigs, setAllAnalyzerConfigs] = useState<Record<string, Record<string, any>>>({})

  // Workflow/component metadata from server
  const [meta, setMeta] = useState<WorkflowsData | null>(null)

  // Auth
  const [auth,   setAuth]   = useState<AuthStatus>({ credit_pool: [] })
  const esRef = useRef<EventSource | null>(null)

  function startAuthStream() {
    esRef.current?.close()
    setAuth({ credit_pool: [] })
    setAuthLoading(true)
    const es = new EventSource(`${API}/api/auth-status/stream`)
    esRef.current = es
    es.onmessage = (e) => {
      const msg = JSON.parse(e.data)
      if (msg.type === 'netrc') {
        setAuth(prev => ({ ...prev, earthdata_connected: msg.earthdata_connected,
          cdse_connected: msg.cdse_connected, credit_pool_exists: msg.credit_pool_exists }))
      } else if (msg.type === 'cds') {
        setAuth(prev => ({ ...prev, cds_connected: msg.data.connected }))
      } else if (msg.type === 'main') {
        setAuth(prev => ({ ...prev, hyp3: msg.data }))
      } else if (msg.type === 'pool') {
        setAuth(prev => ({ ...prev, credit_pool: [...prev.credit_pool, msg.data] }))
      } else if (msg.type === 'done') {
        setAuthLoading(false); es.close()
      }
    }
    es.onerror = () => { setAuthLoading(false); es.close() }
  }

  useEffect(() => {
    // Fetch settings, metadata in parallel
    fetch(`${API}/api/settings`).then(r => r.json()).then((s: ServerSettings) => {
      setWorkdir(s.workdir)
      setMaxWorkers(s.max_download_workers)
      setDownloaderConfig(s.downloader_config)
      setProcessorType(s.processor)
      setProcessorConfig(s.processor_config)
      const allCfgs = s.analyzer_configs ?? {}
      const activeType = initialAnalyzerType ?? s.analyzer
      setAllAnalyzerConfigs(allCfgs)
      setAnalyzerType(activeType)
      setAnalyzerConfig(allCfgs[activeType] ?? {})
      setLoading(false)
    }).catch(() => setLoading(false))

    fetch(`${API}/api/workflows`).then(r => r.json()).then(setMeta).catch(() => {})

    startAuthStream()
    return () => esRef.current?.close()
  }, [])

  // ── Compatibility helpers ─────────────────────────────────────────────────
  function isCompatible(compat: string | null | undefined, value: string) {
    return !compat || compat === 'all' || compat === value
  }

  function filterCompatible<T extends ComponentMeta>(
    map: Record<string, T>,
    attr: 'compatible_downloader' | 'compatible_processor',
    value: string,
  ): Record<string, T> {
    return Object.fromEntries(
      Object.entries(map).filter(([, info]) => isCompatible(info[attr], value))
    )
  }

  function applyDefaults(map: Record<string, ComponentMeta>, name: string): Record<string, any> {
    const defs: Record<string, any> = {}
    map[name]?.fields.forEach(f => { defs[f.key] = f.default })
    return defs
  }

  // When downloader changes → cascade to compatible processor → compatible analyzer
  function handleDownloaderTypeChange(type: string) {
    setOpenGroups(new Set())
    setDownloaderConfig(applyDefaults(meta?.downloaders ?? {}, type))
    onDownloaderTypeChange(type)
    if (!meta) return

    const compatProcs = Object.keys(filterCompatible(meta.processors, 'compatible_downloader', type))
    const nextProc = compatProcs.includes(processorType) ? processorType : (compatProcs[0] ?? processorType)
    if (nextProc !== processorType) {
      setProcessorType(nextProc)
      setProcessorConfig(applyDefaults(meta.processors, nextProc))
    }
    const compatAnals = Object.keys(filterCompatible(meta.analyzers, 'compatible_processor', nextProc))
    const nextAnal = compatAnals.includes(analyzerType) ? analyzerType : (compatAnals[0] ?? analyzerType)
    if (nextAnal !== analyzerType) {
      setAnalyzerType(nextAnal)
      setAnalyzerConfig(applyDefaults(meta.analyzers, nextAnal))
    }
  }

  // When processor changes → cascade to compatible analyzer
  function handleProcessorTypeChange(type: string) {
    setProcessorType(type)
    setOpenGroups(new Set())
    setProcessorConfig(applyDefaults(meta?.processors ?? {}, type))
    if (!meta) return

    const compatAnals = Object.keys(filterCompatible(meta.analyzers, 'compatible_processor', type))
    const nextAnal = compatAnals.includes(analyzerType) ? analyzerType : (compatAnals[0] ?? analyzerType)
    if (nextAnal !== analyzerType) {
      setAnalyzerType(nextAnal)
      setAnalyzerConfig(applyDefaults(meta.analyzers, nextAnal))
    }
  }

  function handleAnalyzerTypeChange(type: string) {
    // Persist current edits before switching
    setAllAnalyzerConfigs(prev => ({ ...prev, [analyzerType]: analyzerConfig }))
    setAnalyzerType(type)
    setOpenGroups(new Set())
    // Load saved config for this type, fall back to defaults
    setAnalyzerConfig(allAnalyzerConfigs[type] ?? applyDefaults(meta?.analyzers ?? {}, type))
  }

  async function handleSave() {
    setSaving(true); setSaveMsg('')
    try {
      const res = await fetch(`${API}/api/settings`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workdir,
          max_download_workers: maxWorkers,
          downloader: downloaderType,
          downloader_config: effectiveDownloaderConfig,
          processor: processorType,
          processor_config: processorConfig,
          analyzer: analyzerType,
          analyzer_config: analyzerConfig,
        }),
      })
      if (!res.ok) throw new Error(await res.text())
      const updated: ServerSettings = await res.json()
      setWorkdir(updated.workdir)
      setAllAnalyzerConfigs(updated.analyzer_configs ?? {})
      setSaveMsg('Saved')
      setTimeout(() => setSaveMsg(''), 2500)
    } catch (e) {
      setSaveMsg(`Error: ${e}`)
    } finally {
      setSaving(false)
    }
  }

  // ── Styles ────────────────────────────────────────────────────────────────
  const inputStyle: React.CSSProperties = {
    boxSizing: 'border-box', padding: '5px 9px', fontSize: 12,
    background: t.inputBg, color: t.text,
    border: `1px solid ${t.inputBorder}`, borderRadius: 4, outline: 'none',
  }
  const labelStyle: React.CSSProperties = {
    display: 'block', color: t.textMuted, fontSize: 11, marginBottom: 4,
  }
  const paramLabelStyle: React.CSSProperties = {
    display: 'block', color: t.textMuted, fontSize: 11,
    fontFamily: 'monospace', marginBottom: 4,
  }
  const fieldStyle: React.CSSProperties = { marginBottom: 14 }
  const hintStyle:  React.CSSProperties = { color: t.textMuted, fontSize: 11, marginTop: 3 }

  const tabBtn = (id: Tab, label: string) => (
    <button key={id} onClick={() => setTab(id)} style={{
      padding: '6px 14px', fontSize: 12, fontWeight: 500,
      background: tab === id ? t.btnActiveBg : 'transparent',
      color:      tab === id ? t.accent       : t.textMuted,
      border: 'none',
      borderBottom: tab === id ? `2px solid ${t.accent}` : '2px solid transparent',
      cursor: 'pointer', whiteSpace: 'nowrap',
    }}>{label}</button>
  )

  // ── Dynamic field renderer ─────────────────────────────────────────────────
  function renderField(f: FieldMeta, config: Record<string, any>,
                        setter: (k: string, v: any) => void) {
    const val = config[f.key] ?? f.default
    return (
      <div key={f.key} style={fieldStyle}>
        <label style={{ ...paramLabelStyle, display: 'flex', alignItems: 'center', gap: 4 }}>
          {f.label}
          {f.hint && (
            <span title={f.hint} style={{
              cursor: 'help', color: t.textMuted, fontSize: 10,
              border: `1px solid ${t.divider}`, borderRadius: '50%',
              width: 13, height: 13, lineHeight: '13px', textAlign: 'center',
              display: 'inline-block', flexShrink: 0, userSelect: 'none',
            }}>?</span>
          )}
        </label>
        {f.type === 'select' && (
          <select
            value={val ?? ''}
            onChange={e => setter(f.key, e.target.value)}
            style={{ ...inputStyle, width: '100%' }}
          >
            {f.options!.map(o => <option key={o} value={o}>{o === '' ? '(any)' : o}</option>)}
          </select>
        )}
        {f.type === 'bool' && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input type="checkbox" checked={val === true}
              onChange={e => setter(f.key, e.target.checked)}
              style={{ accentColor: t.accent, width: 14, height: 14 }} />
            <span style={{ color: t.text, fontSize: 12 }}>
              {val === true ? 'Enabled' : val === false ? 'Disabled' : '(not set)'}
            </span>
          </label>
        )}
        {f.type === 'bool_str' && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input type="checkbox" checked={val === 'yes'}
              onChange={e => setter(f.key, e.target.checked ? 'yes' : 'no')}
              style={{ accentColor: t.accent, width: 14, height: 14 }} />
            <span style={{ color: t.text, fontSize: 12 }}>{val === 'yes' ? 'Yes' : 'No'}</span>
          </label>
        )}
        {f.type === 'number' && (
          <input type="number" value={val} min={f.min} max={f.max} step={f.step ?? 1}
            onChange={e => { const n = parseFloat(e.target.value); if (!isNaN(n)) setter(f.key, n) }}
            style={{ ...inputStyle, width: 100 }} />
        )}
        {f.type === 'auto_number' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <input type="number" value={val === 'auto' || val == null ? '' : val}
              min={f.min} max={f.max} step={f.step ?? 1}
              placeholder="auto"
              onChange={e => setter(f.key, e.target.value === ''
                ? (f.default == null ? null : 'auto')
                : parseFloat(e.target.value))}
              style={{ ...inputStyle, width: 90 }} />
            {val !== 'auto' && val != null && (
              <button onClick={() => setter(f.key, f.default == null ? null : 'auto')}
                style={{ fontSize: 11, color: t.textMuted, background: 'none', border: 'none',
                         cursor: 'pointer', padding: 0 }}>
                reset
              </button>
            )}
          </div>
        )}
        {f.type === 'text' && (
          <input type="text" value={val ?? ''}
            onChange={e => setter(f.key, e.target.value)}
            style={{ ...inputStyle, width: '100%' }} />
        )}
      </div>
    )
  }

  // Merge TopBar values over server config so all three share App.tsx state
  const effectiveDownloaderConfig = {
    ...downloaderConfig,
    start: startDate || downloaderConfig.start,
    end:   endDate   || downloaderConfig.end,
    intersectsWith: aoiWkt ?? downloaderConfig.intersectsWith,
  }

  const setDownloaderField = (k: string, v: any) => {
    if (k === 'start')          { onDatesChange(v, endDate);   return }
    if (k === 'end')            { onDatesChange(startDate, v); return }
    if (k === 'intersectsWith') { onAoiWktChange(v || null);   return }
    setDownloaderConfig(c => ({ ...c, [k]: v }))
  }
  const setProcessorField  = (k: string, v: any) => setProcessorConfig(c => ({ ...c, [k]: v }))
  const setAnalyzerField   = (k: string, v: any) => setAnalyzerConfig(c => ({ ...c, [k]: v }))

  // ── Collapsible group state ────────────────────────────────────────────────
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set())
  const toggleGroup = (label: string) =>
    setOpenGroups(prev => {
      const next = new Set(prev)
      next.has(label) ? next.delete(label) : next.add(label)
      return next
    })

  // ── Grouped field renderer ─────────────────────────────────────────────────
  function renderGroupedFields(
    compMeta: ComponentMeta,
    config: Record<string, any>,
    setter: (k: string, v: any) => void,
  ) {
    if (!compMeta.groups) {
      return compMeta.fields.map(f => renderField(f, config, setter))
    }
    const byKey = Object.fromEntries(compMeta.fields.map(f => [f.key, f]))
    return compMeta.groups.map(grp => {
      const isOpen = openGroups.has(grp.label)
      return (
        <div key={grp.label} style={{ marginBottom: 4 }}>
          <button
            onClick={() => toggleGroup(grp.label)}
            style={{
              width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              background: 'none', border: 'none', cursor: 'pointer',
              padding: '6px 0', marginBottom: isOpen ? 8 : 0,
              borderBottom: `1px solid ${t.divider}`,
            }}
          >
            <span style={{
              color: t.textMuted, fontSize: 10, textTransform: 'uppercase',
              letterSpacing: '0.07em', fontWeight: 700,
            }}>{grp.label}</span>
            <span style={{ color: t.textMuted, fontSize: 10 }}>{isOpen ? '▲' : '▼'}</span>
          </button>
          {isOpen && (
            <div style={{ paddingTop: 4, marginBottom: 12 }}>
              {grp.fields.map(key => byKey[key] && renderField(byKey[key], config, setter))}
            </div>
          )}
        </div>
      )
    })
  }

  // ── Auth helpers ──────────────────────────────────────────────────────────
  const hyp3       = auth.hyp3
  const creditPool = auth.credit_pool
  const poolExists = auth.credit_pool_exists

  const openCred = (key: typeof expandedCred) => {
    setExpandedCred(expandedCred === key ? null : key)
    setCredUser(''); setCredPass(''); setCredToken(''); setCredMsg('')
  }

  const saveCred = async (endpoint: string, body: Record<string, string>) => {
    setCredSaving(true); setCredMsg('')
    try {
      const res = await fetch(`${API}${endpoint}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) { const d = await res.json(); setCredMsg(d.detail ?? 'Error'); return }
      setCredMsg('Saved'); setExpandedCred(null)
      startAuthStream()
    } catch (e) { setCredMsg(String(e)) }
    finally { setCredSaving(false) }
  }

  const inp2: React.CSSProperties = {
    background: t.inputBg, border: `1px solid ${t.inputBorder}`, color: t.text,
    borderRadius: 4, padding: '4px 8px', fontSize: 12, width: '100%',
    boxSizing: 'border-box', colorScheme: t.isDark ? 'dark' : 'light',
  }

  const credForm = (key: typeof expandedCred, fields: React.ReactNode, onSave: () => void, last = false) => (
    <div style={{ borderBottom: last ? 'none' : `1px solid ${t.divider}` }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '9px 14px', cursor: 'pointer' }} onClick={() => openCred(key)}>
        <span style={{ color: t.text, fontSize: 13 }}>{key === 'earthdata' ? 'NASA Earthdata Login' : key === 'cdse' ? 'Copernicus Data Space (CDSE)' : key === 'cds' ? 'Copernicus Climate Data Store (CDS)' : key === 'hyp3' ? 'HyP3 (NASA ASF)' : 'HyP3 Credit Pool'}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {expandedCred !== key && (() => { const connected = key === 'earthdata' ? auth.earthdata_connected : key === 'cdse' ? auth.cdse_connected : key === 'cds' ? auth.cds_connected : key === 'hyp3' ? (hyp3 === undefined ? undefined : !hyp3?.error) : (auth.credit_pool_exists ?? undefined); return connected === undefined ? <span style={{ color: t.textMuted, fontSize: 12 }}>Checking…</span> : <span style={{ fontSize: 12, fontWeight: 600, color: connected ? '#4caf50' : '#e53935' }}>{connected ? '✓ Connected' : '✕ Not connected'}</span> })()}
          <span style={{ color: t.textMuted, fontSize: 11 }}>{expandedCred === key ? '▲' : '▼'}</span>
        </div>
      </div>
      {expandedCred === key && (
        <div style={{ padding: '0 14px 12px', display: 'flex', flexDirection: 'column', gap: 6 }}>
          {fields}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 2 }}>
            <button onClick={onSave} disabled={credSaving} style={{
              padding: '4px 14px', background: t.btnActiveBg, color: t.accent,
              border: `1px solid ${t.btnActiveBorder}`, borderRadius: 5, fontSize: 12,
              fontWeight: 600, cursor: credSaving ? 'wait' : 'pointer',
            }}>{credSaving ? 'Saving…' : 'Save'}</button>
            <button onClick={() => setExpandedCred(null)} style={{
              padding: '4px 10px', background: 'transparent', color: t.textMuted,
              border: `1px solid ${t.border}`, borderRadius: 5, fontSize: 12, cursor: 'pointer',
            }}>Cancel</button>
            {credMsg && <span style={{ fontSize: 11, color: credMsg === 'Saved' ? '#4caf50' : '#e53935' }}>{credMsg}</span>}
          </div>
        </div>
      )}
    </div>
  )


  // ── Component type selector ───────────────────────────────────────────────
  const typeSelector = (
    current: string,
    options: Record<string, ComponentMeta>,
    onChange: (t: string) => void,
  ) => (
    <div style={{ marginBottom: 16 }}>
      <label style={{ ...labelStyle, display: 'flex', alignItems: 'center', gap: 4 }}>
        Type
        {options[current]?.description && (
          <span title={options[current].description} style={{
            cursor: 'help', color: t.textMuted, fontSize: 10,
            border: `1px solid ${t.divider}`, borderRadius: '50%',
            width: 13, height: 13, lineHeight: '13px', textAlign: 'center',
            display: 'inline-block', flexShrink: 0, userSelect: 'none',
          }}>?</span>
        )}
      </label>
      <select value={current} onChange={e => onChange(e.target.value)}
              style={{ ...inputStyle, width: '100%', fontFamily: 'monospace' }}>
        {Object.keys(options).map(k => (
          <option key={k} value={k}>{k}</option>
        ))}
      </select>
    </div>
  )

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <>
      <div onClick={onClose} style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', zIndex: 200,
      }} />

      <div style={{
        position: 'fixed', top: '50%', left: '50%',
        transform: 'translate(-50%, -50%)',
        width: 500, maxHeight: '85vh',
        background: t.bg, border: `1px solid ${t.border}`, borderRadius: 10,
        display: 'flex', flexDirection: 'column',
        boxShadow: '0 8px 40px rgba(0,0,0,0.4)', zIndex: 201, overflow: 'hidden',
      }}>

        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '13px 18px', borderBottom: `1px solid ${t.border}`,
          background: t.bg2, flexShrink: 0,
        }}>
          <span style={{ color: t.text, fontWeight: 700, fontSize: 15 }}>Settings</span>
          <button onClick={onClose} style={{
            background: 'none', border: 'none', cursor: 'pointer',
            color: t.textMuted, fontSize: 20, lineHeight: 1, padding: '0 4px',
          }}>×</button>
        </div>

        {/* Tabs */}
        <div style={{
          display: 'flex', borderBottom: `1px solid ${t.border}`,
          background: t.bg2, flexShrink: 0, overflowX: 'auto',
        }}>
          {tabBtn('general',    'General')}
          {tabBtn('auth',       'Auth')}
          {tabBtn('downloader', 'Downloader')}
          {tabBtn('processor',  'Processor')}
          {tabBtn('analyzer',   'Analyzer')}
        </div>

        {/* Tab content */}
        <div style={{ overflowY: 'auto', padding: '18px 20px 8px', flex: 1 }}>
          {loading && tab !== 'auth' ? (
            <div style={{ color: t.textMuted, textAlign: 'center', padding: '40px 0' }}>Loading…</div>

          ) : tab === 'general' ? (
            <div style={fieldStyle}>
              <label style={labelStyle}>Work Directory</label>
              <div style={{ display: 'flex', gap: 6 }}>
                <input style={{ ...inputStyle, flex: 1 }} value={workdir}
                  onChange={e => setWorkdir(e.target.value)} placeholder="/path/to/workdir" />
                <button
                  onClick={() =>
                    fetch(`${API}/api/pick-folder`)
                      .then(r => r.json())
                      .then(d => { if (d.path) setWorkdir(d.path) })
                  }
                  title="Browse for folder"
                  style={{ ...inputStyle, width: 'auto', padding: '0 10px', cursor: 'pointer', flexShrink: 0 }}
                >Browse…</button>
              </div>
              <div style={hintStyle}>Downloaded scenes and processed results are saved here.</div>
            </div>

          ) : tab === 'auth' ? (<>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
              <button onClick={startAuthStream} disabled={authLoading} style={{
                display: 'inline-flex', alignItems: 'center', gap: 5,
                padding: '5px 12px', fontSize: 12, background: 'transparent',
                color: authLoading ? t.textMuted : t.accent,
                border: `1px solid ${authLoading ? t.border : t.btnActiveBorder}`,
                borderRadius: 5, cursor: authLoading ? 'wait' : 'pointer',
              }}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M23 4v6h-6"/><path d="M1 20v-6h6"/>
                  <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
                </svg>
                {authLoading ? 'Checking…' : 'Refresh'}
              </button>
            </div>
            <div style={{ background: t.bg2, borderRadius: 6, border: `1px solid ${t.border}`,
                          overflow: 'hidden', marginBottom: 16 }}>
              {credForm('earthdata',
                <>
                  <input style={inp2} placeholder="Username" value={credUser} onChange={e => setCredUser(e.target.value)} autoComplete="username" />
                  <input style={inp2} placeholder="Password" type="password" value={credPass} onChange={e => setCredPass(e.target.value)} autoComplete="current-password" />
                </>,
                () => saveCred('/api/credentials/earthdata', { username: credUser, password: credPass })
              )}
              {credForm('cdse',
                <>
                  <input style={inp2} placeholder="Username" value={credUser} onChange={e => setCredUser(e.target.value)} autoComplete="username" />
                  <input style={inp2} placeholder="Password" type="password" value={credPass} onChange={e => setCredPass(e.target.value)} autoComplete="current-password" />
                </>,
                () => saveCred('/api/credentials/cdse', { username: credUser, password: credPass })
              )}
              {credForm('cds',
                <input style={inp2} placeholder="API Token" value={credToken} onChange={e => setCredToken(e.target.value)} />,
                () => saveCred('/api/credentials/cds', { token: credToken })
              )}
              {credForm('hyp3',
                <>
                  <input style={inp2} placeholder="Username" value={credUser} onChange={e => setCredUser(e.target.value)} autoComplete="username" />
                  <input style={inp2} placeholder="Password" type="password" value={credPass} onChange={e => setCredPass(e.target.value)} autoComplete="current-password" />
                </>,
                () => saveCred('/api/credentials/earthdata', { username: credUser, password: credPass })
              )}
              {credForm('pool',
                <>
                  <input style={inp2} placeholder="Username" value={credUser} onChange={e => setCredUser(e.target.value)} autoComplete="username" />
                  <input style={inp2} placeholder="Password" type="password" value={credPass} onChange={e => setCredPass(e.target.value)} autoComplete="current-password" />
                </>,
                () => saveCred('/api/credentials/credit-pool', { username: credUser, password: credPass }),
                true
              )}
            </div>
            <div style={{ color: t.textMuted, fontSize: 11, marginBottom: 16, lineHeight: 1.6 }}>
              Credentials are read from <code>~/.netrc</code>.
            </div>
            {hyp3 && !hyp3.error && (
              <>
                <div style={{ color: t.textMuted, fontSize: 10, textTransform: 'uppercase',
                              letterSpacing: '0.06em', marginBottom: 6, fontWeight: 600 }}>
                  HyP3 Main Account
                </div>
                <div style={{ background: t.bg2, borderRadius: 6, border: `1px solid ${t.border}`,
                              overflow: 'hidden', marginBottom: 16 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between',
                                padding: '8px 14px', borderBottom: `1px solid ${t.divider}` }}>
                    <span style={{ color: t.textMuted, fontSize: 12 }}>User</span>
                    <span style={{ color: t.text, fontSize: 12 }}>{hyp3.username}</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 14px' }}>
                    <span style={{ color: t.textMuted, fontSize: 12 }}>Credits Remaining</span>
                    <span style={{ color: t.accent, fontSize: 13, fontWeight: 700 }}>
                      {hyp3.credits_remaining?.toLocaleString() ?? '—'}
                    </span>
                  </div>
                </div>
              </>
            )}
            {poolExists && (creditPool.length > 0 || authLoading) && (
              <>
                <div style={{ color: t.textMuted, fontSize: 10, textTransform: 'uppercase',
                              letterSpacing: '0.06em', marginBottom: 6, fontWeight: 600 }}>
                  HyP3 Credit Pool
                </div>
                <div style={{ background: t.bg2, borderRadius: 6, border: `1px solid ${t.border}`,
                              overflow: 'hidden' }}>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr auto',
                                gap: '0 16px', padding: '6px 14px', borderBottom: `1px solid ${t.divider}` }}>
                    <span style={{ color: t.textMuted, fontSize: 11, fontWeight: 600 }}>Username</span>
                    <span style={{ color: t.textMuted, fontSize: 11, fontWeight: 600, textAlign: 'right' }}>Remaining</span>
                  </div>
                  {creditPool.map((acct, i) => (
                    <div key={acct.username} style={{
                      display: 'grid', gridTemplateColumns: '1fr auto',
                      gap: '0 16px', padding: '7px 14px', alignItems: 'center',
                      borderBottom: i < creditPool.length - 1 ? `1px solid ${t.divider}` : 'none',
                    }}>
                      <span style={{ color: t.text, fontSize: 12, fontFamily: 'monospace' }}>{acct.username}</span>
                      {acct.error
                        ? <span style={{ color: '#e53935', fontSize: 11 }}>error</span>
                        : <span style={{ color: t.accent, fontSize: 12, fontWeight: 700, textAlign: 'right' }}>
                            {acct.credits_remaining?.toLocaleString() ?? '—'}
                          </span>}
                    </div>
                  ))}
                </div>
              </>
            )}
          </>) : tab === 'processor' ? (() => {
            const procOptions = meta
              ? filterCompatible(meta.processors, 'compatible_downloader', downloaderType)
              : null
            return (<>
              {procOptions
                ? Object.keys(procOptions).length === 0
                  ? <div style={{ color: t.textMuted, fontSize: 12, padding: '8px 0' }}>
                      No compatible processors for <code>{downloaderType}</code>.
                    </div>
                  : typeSelector(processorType, procOptions, handleProcessorTypeChange)
                : <div style={{ color: t.textMuted, fontSize: 12 }}>Loading…</div>}
              {procOptions?.[processorType] && (
                <div style={{ borderTop: `1px solid ${t.border}`, paddingTop: 14, marginTop: 4 }}>
                  {renderGroupedFields(procOptions[processorType], processorConfig, setProcessorField)}
                </div>
              )}
            </>)
          })() : tab === 'analyzer' ? (() => {
            const analOptions = meta
              ? filterCompatible(meta.analyzers, 'compatible_processor', processorType)
              : null
            return (<>
              {analOptions
                ? Object.keys(analOptions).length === 0
                  ? <div style={{ color: t.textMuted, fontSize: 12, padding: '8px 0' }}>
                      No compatible analyzers for <code>{processorType}</code>.
                    </div>
                  : typeSelector(analyzerType, analOptions, handleAnalyzerTypeChange)
                : <div style={{ color: t.textMuted, fontSize: 12 }}>Loading…</div>}
              {analOptions?.[analyzerType] && (
                <div style={{ borderTop: `1px solid ${t.border}`, paddingTop: 14, marginTop: 4 }}>
                  {renderGroupedFields(analOptions[analyzerType], analyzerConfig, setAnalyzerField)}
                </div>
              )}
            </>)
          })() : tab === 'downloader' ? (<>
            {meta?.downloaders
              ? typeSelector(downloaderType, meta.downloaders, handleDownloaderTypeChange)
              : <div style={{ color: t.textMuted, fontSize: 12 }}>Loading…</div>}
            {meta?.downloaders[downloaderType] && (
              <div style={{ borderTop: `1px solid ${t.border}`, paddingTop: 14, marginTop: 4 }}>
                {renderGroupedFields(meta.downloaders[downloaderType], effectiveDownloaderConfig, setDownloaderField)}
              </div>
            )}
            <div style={{ ...fieldStyle, marginTop: 16, borderTop: `1px solid ${t.border}`, paddingTop: 14 }}>
              <label style={labelStyle}>Parallel Download Workers</label>
              <input type="number" min={1} max={99} value={maxWorkers}
                onChange={e => setMaxWorkers(Math.max(1, parseInt(e.target.value) || 1))}
                style={{ ...inputStyle, width: 80 }} />
              <div style={hintStyle}>Simultaneous file downloads. Recommended: 3–5.</div>
            </div>
          </>) : null}
        </div>

        {/* Footer — hidden on auth tab */}
        {tab !== 'auth' && (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
            gap: 10, padding: '12px 20px',
            borderTop: `1px solid ${t.border}`, background: t.bg2, flexShrink: 0,
          }}>
            <a href="https://jldz9.github.io/InSARHub" target="_blank" rel="noopener noreferrer"
              style={{
                marginRight: 'auto', display: 'inline-flex', alignItems: 'center', gap: 5,
                fontSize: 12, color: t.textMuted, textDecoration: 'none',
                padding: '4px 10px', border: `1px solid ${t.border}`,
                borderRadius: 6, cursor: 'pointer',
              }}>
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" style={{ width: 13, height: 13, fill: 'currentColor' }}>
                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 17h-2v-2h2v2zm2.07-7.75-.9.92C13.45 12.9 13 13.5 13 15h-2v-.5c0-1.1.45-2.1 1.17-2.83l1.24-1.26c.37-.36.59-.86.59-1.41 0-1.1-.9-2-2-2s-2 .9-2 2H8c0-2.21 1.79-4 4-4s4 1.79 4 4c0 .88-.36 1.68-.93 2.25z"/>
              </svg>
              Docs
            </a>
            {saveMsg && (
              <span style={{ fontSize: 12,
                color: saveMsg.startsWith('Error') ? '#e53935' : '#4caf50' }}>
                {saveMsg}
              </span>
            )}
            <button onClick={onClose} style={{
              padding: '6px 18px', background: 'transparent',
              color: t.textMuted, border: `1px solid ${t.border}`,
              borderRadius: 6, fontSize: 12, cursor: 'pointer',
            }}>Cancel</button>
            <button onClick={handleSave} disabled={saving} style={{
              padding: '6px 22px', background: t.btnActiveBg, color: t.accent,
              border: `1px solid ${t.btnActiveBorder}`,
              borderRadius: 6, fontSize: 12, fontWeight: 600,
              cursor: saving ? 'wait' : 'pointer',
            }}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        )}
      </div>
    </>
  )
}
