import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import type { Theme } from './theme'
import { API } from './api'

// Downloader-declared schema fields/groups are plain strings from the backend
// (see BaseDownloader.search_filter_schema) — this maps the ones S1_SLC ships
// today to real translation keys; any future downloader's fields we don't yet
// have a mapping for still render (via i18next's `defaultValue`), just untranslated.
const FIELD_LABEL_KEYS: Record<string, string> = {
  flightDirection: 'searchFilters.fields.flightDirection',
  platform:        'searchFilters.fields.platform',
  relativeOrbit:   'searchFilters.fields.path',
  asfFrame:        'searchFilters.fields.frame',
}
const GROUP_LABEL_KEYS: Record<string, string> = {
  'Additional Filters':      'searchFilters.additionalFilters',
  'Path and Frame Filters':  'searchFilters.pathAndFrameFilters',
}

// One extra search-filter field as declared by a downloader's own
// `search_filter_schema` (see GET /api/downloader-schema). The frontend never
// hardcodes which fields a downloader has — it renders whatever the backend
// reports, generically, per `kind`.
interface SchemaField {
  name:     string            // config dataclass field name to set
  label:    string
  kind:     'select' | 'range' | 'number' | 'text'
  group:    string            // section heading to render under
  choices?: string[]          // required when kind === 'select'
}

export interface Filters {
  startDate:       string
  endDate:         string
  maxResults:      string
  granuleNames:    string[]   // parsed scene names (empty = not used)
  granuleFileName: string     // display name of the uploaded file
  // Resolved, ready-to-send downloader-specific field overrides — keyed by
  // schema field name, values already shaped for the API (e.g. a "range" kind
  // resolves to a plain number[] here, not two separate start/end strings).
  overrides: Record<string, unknown>
}

export const DEFAULT_FILTERS: Filters = {
  startDate:       '',
  endDate:         '',
  maxResults:      '2000',
  granuleNames:    [],
  granuleFileName: '',
  overrides:       {},
}

export function hasActiveFilters(f: Filters): boolean {
  return !!(Object.keys(f.overrides).length > 0 ||
            (f.maxResults && f.maxResults !== '2000') ||
            f.granuleNames.length > 0)
}

interface Props {
  open:           boolean
  filters:        Filters
  theme:          Theme
  downloaderType: string
  onClose:        () => void
  onApply:        (f: Filters) => void
}


function rangeArray(start: number, end: number): number[] {
  const [lo, hi] = start <= end ? [start, end] : [end, start]
  return Array.from({ length: hi - lo + 1 }, (_, i) => lo + i)
}

// Split on whitespace / commas / newlines, strip extensions, keep name-like tokens.
// Shared by file upload (csv/txt) and the manual-entry textarea below.
function parseNamesText(text: string): string[] {
  const tokens = text.split(/[\s,]+/).map(s => s.trim()).filter(Boolean)
  const nameRe = /^[A-Za-z0-9][A-Za-z0-9_\-]{19,}$/
  return [...new Set(tokens.map(t => t.includes('.') ? t.replace(/\.[^.]+$/, '') : t).filter(t => nameRe.test(t)))]
}

// Renders one downloader-declared filter field, generically, based on `kind`.
// A "range" field renders as two grid cells (Start/End) — the surrounding
// parent is a CSS grid, so the two <div>s inside this fragment still lay out
// as independent grid items.
function SchemaFieldInput({ field, rawValues, setFieldValue, label, input }: {
  field: SchemaField
  rawValues: Record<string, string>
  setFieldValue: (key: string, value: string) => void
  label: React.CSSProperties
  input: React.CSSProperties
}) {
  const { t: tr } = useTranslation()
  const fieldLabel = tr(FIELD_LABEL_KEYS[field.name] ?? `_unmapped.${field.name}`, { defaultValue: field.label })

  if (field.kind === 'range') {
    return (
      <>
        <div>
          <label style={label}>{tr('searchFilters.rangeStart', { label: fieldLabel })}</label>
          <input type="number" style={input} placeholder="—"
            value={rawValues[`${field.name}_start`] ?? ''}
            onChange={e => setFieldValue(`${field.name}_start`, e.target.value)} />
        </div>
        <div>
          <label style={label}>{tr('searchFilters.rangeEnd', { label: fieldLabel })}</label>
          <input type="number" style={input} placeholder="—"
            value={rawValues[`${field.name}_end`] ?? ''}
            onChange={e => setFieldValue(`${field.name}_end`, e.target.value)} />
        </div>
      </>
    )
  }
  if (field.kind === 'select') {
    return (
      <div>
        <label style={label}>{fieldLabel}</label>
        <select style={{ ...input, cursor: 'pointer' }}
          value={rawValues[field.name] ?? ''}
          onChange={e => setFieldValue(field.name, e.target.value)}>
          <option value="">{tr('searchFilters.any')}</option>
          {(field.choices ?? []).map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
    )
  }
  return (
    <div>
      <label style={label}>{fieldLabel}</label>
      <input type={field.kind === 'number' ? 'number' : 'text'} style={input} placeholder="—"
        value={rawValues[field.name] ?? ''}
        onChange={e => setFieldValue(field.name, e.target.value)} />
    </div>
  )
}

export default function SearchFilters({ open, filters, theme: t, downloaderType, onClose, onApply }: Props) {
  const { t: tr } = useTranslation()
  const [draft, setDraft]           = useState<Filters>(filters)
  const [uploading, setUploading]   = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [manualText, setManualText] = useState('')
  const [schema, setSchema]         = useState<SchemaField[]>([])
  // Controlled-input text values for schema-driven fields, keyed by field.name
  // (or `${field.name}_start` / `${field.name}_end` for "range" kind) — kept
  // separate from draft.overrides, which holds the already-resolved values
  // ready to send (numbers, arrays) rather than raw textbox strings.
  const [rawValues, setRawValues]   = useState<Record<string, string>>({})
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Fetch this downloader's extra filter fields whenever it changes, and seed
  // rawValues from whatever overrides are already in the incoming filters
  // (e.g. re-opening the modal after a previous Apply).
  useEffect(() => {
    let cancelled = false
    fetch(`${API}/api/downloader-schema?downloaderType=${encodeURIComponent(downloaderType)}`)
      .then(r => r.json())
      .then(data => {
        if (cancelled) return
        const fields: SchemaField[] = data.fields ?? []
        setSchema(fields)
        const seeded: Record<string, string> = {}
        for (const f of fields) {
          const val = filters.overrides[f.name]
          if (val == null) continue
          if (f.kind === 'range' && Array.isArray(val) && val.length > 0) {
            seeded[`${f.name}_start`] = String(val[0])
            seeded[`${f.name}_end`]   = String(val[val.length - 1])
          } else if (f.kind !== 'range') {
            seeded[f.name] = String(val)
          }
        }
        setRawValues(seeded)
      })
      .catch(() => setSchema([]))
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [downloaderType])

  if (!open) return null

  // Recompute draft.overrides from the current rawValues + schema — called
  // after every keystroke on a schema-driven field so Apply always sends
  // already-resolved values (range pairs → number[], numbers → number, etc).
  function recomputeOverrides(next: Record<string, string>) {
    const resolved: Record<string, unknown> = {}
    for (const f of schema) {
      if (f.kind === 'range') {
        const startStr = next[`${f.name}_start`]
        if (!startStr) continue
        const endStr = next[`${f.name}_end`] || startStr
        const start = parseInt(startStr), end = parseInt(endStr)
        if (!Number.isNaN(start) && !Number.isNaN(end)) resolved[f.name] = rangeArray(start, end)
      } else {
        const v = next[f.name]
        if (!v) continue
        resolved[f.name] = f.kind === 'number' ? parseFloat(v) : v
      }
    }
    setDraft(d => ({ ...d, overrides: resolved }))
  }

  function setFieldValue(key: string, value: string) {
    setRawValues(prev => {
      const next = { ...prev, [key]: value }
      recomputeOverrides(next)
      return next
    })
  }

  function handleManualNamesChange(text: string) {
    setManualText(text)
    setDraft(d => ({ ...d, granuleNames: parseNamesText(text), granuleFileName: '' }))
  }

  async function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    setUploadError('')
    try {
      const suffix = file.name.split('.').pop()?.toLowerCase() ?? ''
      if (['csv', 'txt'].includes(suffix)) {
        const text  = await file.text()
        const names = parseNamesText(text)
        setDraft(d => ({ ...d, granuleNames: names, granuleFileName: file.name }))
        setManualText(names.join('\n'))
      } else {
        // Send to backend for XLSX or other formats
        const form = new FormData()
        form.append('file', file)
        const res = await fetch(`${API}/api/parse-granule-file`, { method: 'POST', body: form })
        const data = await res.json()
        if (!res.ok) { setUploadError(data.detail ?? 'Parse error'); return }
        setDraft(d => ({ ...d, granuleNames: data.names, granuleFileName: file.name }))
        setManualText((data.names as string[]).join('\n'))
      }
    } catch (err) {
      setUploadError(String(err))
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const sectionHead: React.CSSProperties = {
    background: t.isDark ? '#252540' : '#c8cdd4',
    color: t.text, padding: '7px 16px',
    fontWeight: 700, fontSize: 12, letterSpacing: '0.05em',
    borderTop: `1px solid ${t.border}`, borderBottom: `1px solid ${t.border}`,
  }
  const label: React.CSSProperties = {
    color: t.textMuted, fontSize: 11, marginBottom: 5, display: 'block',
  }
  const input: React.CSSProperties = {
    background: t.inputBg, border: `1px solid ${t.inputBorder}`,
    color: t.text, borderRadius: 4, padding: '5px 8px',
    fontSize: 12, width: '100%', boxSizing: 'border-box',
    colorScheme: t.isDark ? 'dark' : 'light',
  }

  return (
    <>
      {/* Backdrop */}
      <div onClick={onClose} style={{
        position: 'fixed', inset: 0, zIndex: 100,
        background: 'rgba(0,0,0,0.45)',
      }} />

      {/* Modal */}
      <div style={{
        position: 'fixed', top: '50%', left: '50%', zIndex: 101,
        transform: 'translate(-50%, -50%)',
        background: t.bg2, border: `1px solid ${t.border}`,
        borderRadius: 8, width: 480, maxHeight: '90vh',
        boxShadow: '0 8px 40px rgba(0,0,0,0.45)',
        overflow: 'hidden', display: 'flex', flexDirection: 'column',
      }}>

        {/* Header */}
        <div style={{
          background: t.isDark ? '#1a1a2e' : '#d4dae3',
          padding: '11px 16px',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          borderBottom: `1px solid ${t.border}`,
        }}>
          <span style={{ fontWeight: 700, fontSize: 15, color: t.text }}>{tr('searchFilters.title')}</span>
          <button onClick={onClose} style={{
            background: 'transparent', border: 'none',
            color: t.textMuted, cursor: 'pointer', fontSize: 20, lineHeight: 1, padding: 0,
          }}>×</button>
        </div>

        {/* Scrollable body — keeps header + footer pinned so a tall filter set
            fits on short screens (1080p) instead of overflowing off-screen. */}
        <div style={{ overflowY: 'auto', minHeight: 0, flex: 1 }}>

        {/* ── Date Filters ── */}
        <div style={sectionHead}>{tr('searchFilters.dateFilters')}</div>
        <div style={{ padding: '14px 16px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <div>
            <label style={label}>{tr('searchFilters.startDate')}</label>
            <input type="date" style={input}
              value={draft.startDate}
              onChange={e => setDraft(d => ({ ...d, startDate: e.target.value }))} />
          </div>
          <div>
            <label style={label}>{tr('searchFilters.endDate')}</label>
            <input type="date" style={input}
              value={draft.endDate}
              onChange={e => setDraft(d => ({ ...d, endDate: e.target.value }))} />
          </div>
        </div>

        {/* ── Additional Filters — Max Results (universal) + this downloader's own fields ── */}
        <div style={sectionHead}>{tr(GROUP_LABEL_KEYS['Additional Filters'], { defaultValue: 'Additional Filters' })}</div>
        <div style={{ padding: '14px 16px', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 14 }}>
          <div>
            <label style={label}>{tr('searchFilters.maxResults')}</label>
            <input type="number" style={input} min={1} max={10000}
              value={draft.maxResults}
              onChange={e => setDraft(d => ({ ...d, maxResults: e.target.value }))} />
          </div>
          {schema.filter(f => f.group === 'Additional Filters').map(f => (
            <SchemaFieldInput key={f.name} field={f} rawValues={rawValues}
              setFieldValue={setFieldValue} label={label} input={input} />
          ))}
        </div>

        {/* ── Every other downloader-declared group (e.g. Path and Frame Filters) ── */}
        {Array.from(new Set(schema.map(f => f.group).filter(g => g !== 'Additional Filters'))).map(group => (
          <div key={group}>
            <div style={sectionHead}>{tr(GROUP_LABEL_KEYS[group] ?? `_unmapped.${group}`, { defaultValue: group })}</div>
            <div style={{ padding: '14px 16px 18px', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 14 }}>
              {schema.filter(f => f.group === group).map(f => (
                <SchemaFieldInput key={f.name} field={f} rawValues={rawValues}
                  setFieldValue={setFieldValue} label={label} input={input} />
              ))}
            </div>
          </div>
        ))}

        {/* ── Granule Names ── */}
        <div style={sectionHead}>{tr('searchFilters.granuleNames')} <span style={{ fontWeight: 400, fontSize: 11, opacity: 0.7 }}>{tr('searchFilters.granuleNamesHint')}</span></div>
        <div style={{ padding: '14px 16px 16px' }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
            <input ref={fileInputRef} type="file" accept=".csv,.xlsx,.xls,.txt"
              style={{ display: 'none' }} onChange={handleFileUpload} />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              style={{
                background: t.btnActiveBg, border: `1px solid ${t.btnActiveBorder}`,
                color: t.accent, borderRadius: 4, padding: '5px 12px',
                cursor: uploading ? 'wait' : 'pointer', fontSize: 12, fontWeight: 600,
              }}
            >
              {uploading ? tr('searchFilters.parsing') : tr('searchFilters.uploadFile')}
            </button>
            <span style={{ fontSize: 11, color: t.textMuted }}>CSV, XLSX, TXT</span>
            {draft.granuleNames.length > 0 && (
              <button
                onClick={() => { setDraft(d => ({ ...d, granuleNames: [], granuleFileName: '' })); setManualText('') }}
                style={{
                  marginLeft: 'auto', background: 'transparent',
                  border: `1px solid ${t.border}`, color: t.textMuted,
                  borderRadius: 4, padding: '3px 10px', cursor: 'pointer', fontSize: 11,
                }}
              >{tr('searchFilters.clear')}</button>
            )}
          </div>
          {uploadError && (
            <div style={{ color: '#e53935', fontSize: 11, marginBottom: 6 }}>{uploadError}</div>
          )}

          <label style={label}>{tr('searchFilters.typeOrPasteNames')}</label>
          <textarea
            style={{ ...input, minHeight: 70, resize: 'vertical', fontFamily: 'monospace', lineHeight: 1.4 }}
            placeholder="S1C_IW_SLC__1SDV_20250626T010032_20250626T010100_002947_00604C_2350"
            value={manualText}
            onChange={e => handleManualNamesChange(e.target.value)}
          />

          {draft.granuleNames.length > 0 ? (
            <div style={{
              background: t.isDark ? '#0d1b0d' : '#e8f5e9',
              border: `1px solid ${t.isDark ? '#2e7d32' : '#a5d6a7'}`,
              borderRadius: 4, padding: '6px 10px', fontSize: 11, color: '#4caf50', marginTop: 8,
            }}>
              {draft.granuleFileName && <span style={{ fontWeight: 600 }}>{draft.granuleFileName} — </span>}
              {tr('searchFilters.scenesLoaded', { count: draft.granuleNames.length })}
            </div>
          ) : (
            <div style={{ fontSize: 11, color: t.textMuted, marginTop: 8 }}>
              {tr('searchFilters.noGranuleNames')}
            </div>
          )}
        </div>
        </div>{/* end scrollable body */}

        {/* Footer */}
        <div style={{
          padding: '10px 16px',
          borderTop: `1px solid ${t.border}`,
          background: t.isDark ? '#1a1a2e' : '#d4dae3',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <button onClick={() => { setDraft(DEFAULT_FILTERS); setRawValues({}); setManualText('') }} style={{
            background: 'transparent', border: `1px solid ${t.border}`,
            color: t.textMuted, borderRadius: 4, padding: '5px 14px',
            cursor: 'pointer', fontSize: 12,
          }}>{tr('searchFilters.clearAll')}</button>

          <div style={{ display: 'flex', gap: 8 }}>
            <button onClick={onClose} style={{
              background: 'transparent', border: `1px solid ${t.border}`,
              color: t.text, borderRadius: 4, padding: '5px 14px',
              cursor: 'pointer', fontSize: 12,
            }}>{tr('searchFilters.cancel')}</button>
            <button onClick={() => { onApply(draft); onClose() }} style={{
              background: t.btnActiveBg, border: `1px solid ${t.btnActiveBorder}`,
              color: t.isDark ? '#e0f0ff' : t.btnActiveFg,
              borderRadius: 4, padding: '5px 18px',
              cursor: 'pointer', fontSize: 12, fontWeight: 700,
            }}>{tr('searchFilters.update')}</button>
          </div>
        </div>
      </div>
    </>
  )
}