import { useMemo, useState, useRef, useEffect, useCallback } from 'react'
import type { Theme } from './theme'
import { useResizable, ResizeHandle } from './useResizable'

const API = import.meta.env.DEV ? 'http://localhost:8080' : ''

interface StackSummary {
  stackKey:              string
  path:                  number
  frame:                 number
  sceneCount:            number
  startDate:             string
  endDate:               string
  flightDirection:       string
  platform:              string
  representativeFeature: GeoJSON.Feature
}

interface Props {
  footprints:       GeoJSON.FeatureCollection
  theme:            Theme
  selectedStackKey: string | null
  workdir:          string
  aoiWkt:           string | null
  downloaderType:   string
  onStackHover:     (key: string | null) => void
  onStackClick:     (feature: GeoJSON.Feature) => void
  onCheckedChange:  (keys: string[]) => void
  onClose:          () => void
}

function parseStack(key: string): { path: number; frame: number } {
  const m = key.match(/\(?\s*(\d+)\s*,\s*(\d+)\s*\)?/)
  return m ? { path: parseInt(m[1]), frame: parseInt(m[2]) } : { path: 0, frame: 0 }
}

export default function StackSummaryDrawer({
  footprints, theme: t, selectedStackKey, workdir, aoiWkt, downloaderType,
  onStackHover, onStackClick, onCheckedChange, onClose,
}: Props) {
  const { width, onHandleMouseDown } = useResizable(260)

  const stacks = useMemo<StackSummary[]>(() => {
    const map = new Map<string, StackSummary>()
    for (const feature of footprints.features) {
      const key = feature.properties?._stack as string | undefined
      if (!key) continue
      if (!map.has(key)) {
        const { path, frame } = parseStack(key)
        map.set(key, {
          stackKey:              key,
          path, frame,
          sceneCount:            0,
          startDate:             '',
          endDate:               '',
          flightDirection:       (feature.properties?.flightDirection as string) ?? '',
          platform:              (feature.properties?.platform as string) ?? '',
          representativeFeature: feature,
        })
      }
      const s = map.get(key)!
      s.sceneCount++
      const date = ((feature.properties?.startTime as string) ?? '').slice(0, 10)
      if (date) {
        if (!s.startDate || date < s.startDate) s.startDate = date
        if (!s.endDate   || date > s.endDate)   s.endDate   = date
      }
    }
    return Array.from(map.values()).sort((a, b) => a.path - b.path || a.frame - b.frame)
  }, [footprints])

  // ── Multi-select + trigger state ───────────────────────────────────────────
  const [checked,   setChecked]   = useState<Set<string>>(new Set())
  const [triggered, setTriggered] = useState<Set<string>>(new Set())

  const emitChecked = useCallback((next: Set<string>) => {
    onCheckedChange(Array.from(next))
  }, [onCheckedChange])

  const toggleCheck = (key: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setChecked(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      emitChecked(next)
      return next
    })
  }

  const toggleAll = () => {
    setChecked(prev => {
      const next = prev.size === stacks.length ? new Set<string>() : new Set(stacks.map(s => s.stackKey))
      emitChecked(next)
      return next
    })
  }

  // ── Merged download job polling ─────────────────────────────────────────────
  const [_dlJobId, setDlJobId]  = useState<string | null>(null)
  const [dlStatus, setDlStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [dlMsg,    setDlMsg]    = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  const startMergedDownload = async () => {
    const selectedStacks = stacks.filter(s => checked.has(s.stackKey))
    if (!selectedStacks.length) return
    setTriggered(new Set(checked))

    const body = {
      workdir,
      downloaderType,
      download_slc:   true,
      download_orbit: true,
      stacks: selectedStacks.map(s => ({
        relativeOrbit:   s.path,
        frame:           s.frame,
        start:           s.startDate,
        end:             s.endDate,
        wkt:             aoiWkt ?? undefined,
        flightDirection: s.flightDirection || undefined,
        platform:        s.platform || undefined,
      })),
    }

    try {
      const r   = await fetch(`${API}/api/download-merged`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const { job_id } = await r.json()
      setDlJobId(job_id)
      setDlStatus('running')
      setDlMsg('Starting…')

      if (pollRef.current) clearInterval(pollRef.current)
      pollRef.current = setInterval(async () => {
        const res = await fetch(`${API}/api/jobs/${job_id}`)
        const job = await res.json()
        setDlMsg(job.message)
        if (job.status === 'done' || job.status === 'error') {
          clearInterval(pollRef.current!)
          setDlStatus(job.status)
          setDlJobId(null)
        }
      }, 1500)
    } catch (e) {
      setDlStatus('error')
      setDlMsg(String(e))
    }
  }

  const checkedCount = checked.size
  const dlColor = dlStatus === 'done' ? '#4caf50' : dlStatus === 'error' ? '#e53935' : t.accent

  return (
    <div style={{
      position: 'relative', width, height: '100%',
      background: t.bg, borderLeft: `1px solid ${t.border}`,
      display: 'flex', flexDirection: 'column',
    }}>
      <ResizeHandle onMouseDown={onHandleMouseDown} />

      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '8px 12px', borderBottom: `1px solid ${t.border}`,
        background: t.bg2, flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <input
            type="checkbox"
            checked={checkedCount === stacks.length && stacks.length > 0}
            ref={el => { if (el) el.indeterminate = checkedCount > 0 && checkedCount < stacks.length }}
            onChange={toggleAll}
            style={{ accentColor: t.accent, cursor: 'pointer' }}
            title="Select all stacks"
          />
          <span style={{ color: t.text, fontWeight: 700, fontSize: 13 }}>
            {stacks.length} Stack{stacks.length !== 1 ? 's' : ''}
          </span>
        </div>
        <button
          onClick={onClose}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: t.textMuted, fontSize: 18, lineHeight: 1, padding: '0 2px' }}
        >×</button>
      </div>

      {/* Merged download bar — shown when any stack is checked */}
      {checkedCount > 0 && (
        <div style={{
          padding: '8px 12px', borderBottom: `1px solid ${t.border}`,
          background: `${t.accent}11`, flexShrink: 0,
          display: 'flex', flexDirection: 'column', gap: 6,
        }}>
          <button
            onClick={startMergedDownload}
            disabled={dlStatus === 'running'}
            style={{
              width: '100%', padding: '6px 10px',
              background: dlStatus === 'running' ? t.inputBg : t.accent,
              color: '#fff', border: 'none', borderRadius: 4,
              cursor: dlStatus === 'running' ? 'not-allowed' : 'pointer',
              fontWeight: 600, fontSize: 12,
            }}
          >
            {dlStatus === 'running'
              ? 'Downloading…'
              : `Download SLC + Orbit (${checkedCount} stack${checkedCount !== 1 ? 's' : ''}) → merged/`}
          </button>
          {dlStatus !== 'idle' && (
            <span style={{ fontSize: 10, color: dlColor, wordBreak: 'break-all' }}>{dlMsg}</span>
          )}
        </div>
      )}

      {/* Stack list */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {stacks.map(s => {
          const dir      = (s.flightDirection ?? '').toUpperCase()
          const dirColor = dir === 'ASCENDING' ? '#f39c12' : dir === 'DESCENDING' ? '#00bcd4' : t.textMuted
          const isActive      = s.stackKey === selectedStackKey
          const isChecked     = checked.has(s.stackKey)
          const isTriggered   = triggered.has(s.stackKey)
          const triggerColor  = dlStatus === 'done' ? '#4caf50' : dlStatus === 'error' ? '#e53935' : t.accent
          const triggerLabel  = dlStatus === 'running' ? '⬇' : dlStatus === 'done' ? '✓' : dlStatus === 'error' ? '✗' : '⬇'
          return (
            <div
              key={s.stackKey}
              onClick={() => onStackClick(s.representativeFeature)}
              onMouseEnter={() => onStackHover(s.stackKey)}
              onMouseLeave={() => onStackHover(null)}
              style={{
                padding: '10px 12px',
                borderBottom: `1px solid ${t.border}`,
                cursor: 'pointer',
                background: isTriggered
                  ? `${triggerColor}33`
                  : isChecked
                    ? `${t.accent}33`
                    : isActive ? t.inputBg : 'transparent',
                borderLeft: isTriggered
                  ? `3px solid ${triggerColor}`
                  : isChecked
                    ? `3px solid ${t.accent}`
                    : '3px solid transparent',
                boxSizing: 'border-box',
                display: 'flex', alignItems: 'flex-start', gap: 8,
              }}
            >
              <input
                type="checkbox"
                checked={isChecked}
                onClick={e => toggleCheck(s.stackKey, e)}
                onChange={() => {}}
                style={{ accentColor: t.accent, cursor: 'pointer', marginTop: 2, flexShrink: 0 }}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 5 }}>
                  <span style={{ color: t.text, fontWeight: 600, fontSize: 12 }}>
                    Path {s.path} · Frame {s.frame}
                  </span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    {isTriggered && (
                      <span style={{
                        color: triggerColor, fontSize: 10, fontWeight: 700,
                        background: `${triggerColor}22`, borderRadius: 3, padding: '1px 5px',
                      }}>
                        {triggerLabel}
                      </span>
                    )}
                    <span style={{
                      color: dirColor, fontSize: 10, fontWeight: 700,
                      background: `${dirColor}22`, borderRadius: 3, padding: '1px 6px',
                    }}>
                      {dir === 'ASCENDING' ? 'ASC' : dir === 'DESCENDING' ? 'DESC' : dir || '—'}
                    </span>
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span style={{ color: t.textMuted, fontSize: 11 }}>
                    {s.startDate} – {s.endDate}
                  </span>
                  <span style={{
                    color: t.accent, fontSize: 11, fontWeight: 600,
                    background: `${t.accent}22`, borderRadius: 3, padding: '1px 6px',
                  }}>
                    {s.sceneCount}
                  </span>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
