import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { type Theme } from './theme'
import { useResizable, ResizeHandle } from './useResizable'
import { API } from './api'

export function parseStack(s: string): { path: number; frame: number } | null {
  const m = s.match(/\(\s*(\d+)\s*,\s*(\d+)\s*\)/)
  if (!m) return null
  return { path: parseInt(m[1]), frame: parseInt(m[2]) }
}

// Stack identity for one footprint. SLC stacks encode "(path, frame)" in
// `_stack`; burst stacks carry no frame, so the flattened ASF burst
// properties (pathNumber, subswath, relativeBurstID) are read instead — a
// burst stack is one fixed burst position that repeats every revisit.
export interface StackInfo {
  path: number
  frame: number
  isBurst: boolean
  subswath?: string
  burstID?: number
}

export function stackInfo(p: Record<string, any>): StackInfo {
  const slc = parseStack(p._stack ?? '')
  if (slc) return { path: slc.path, frame: slc.frame, isBurst: false }
  const path = parseInt(p.pathNumber ?? '', 10) || 0
  const sw   = String(p.subswath ?? '').toUpperCase()
  // OPERA relative burst ID — the "official" burst number, unique per
  // subswath across the orbit (unlike the 0-based burstIndex position that
  // repeats for every subswath).
  const rbi  = p.relativeBurstID != null ? Number(p.relativeBurstID) : undefined
  return { path, frame: 0, isBurst: true, subswath: sw, burstID: rbi }
}


// Downloading is deliberately NOT offered here. The stack panel's job is
// selection: pick a stack, hit Add Job, and the download runs from the job
// folder (Jobs drawer -> Download). Two entry points to the same transfer meant
// the map path and the folder path could each write the folder's
// insarhub_config.json with a different idea of what the stack was, and the map
// path had no folder to attribute progress or a failure to. One path, one
// config, one place to watch it. Applies to every downloader, present and
// future -- there is no per-downloader opt-in.
interface Props {
  feature:      GeoJSON.Feature
  theme:        Theme
  stackStart?: string
  stackEnd?:   string
  stackCount:   number | null
  stackPlatform?: string
  stackUrls:    string[]
  workdir:       string
  aoiWkt?:       string | null
  downloaderType: string
  stackOpen:     boolean
  onClose:      () => void
  onStackClick: () => void
}

const row = (t: Theme, label: string, value: React.ReactNode) => (
  <div key={label} style={{ display: 'flex', gap: 8, padding: '4px 0',
                borderBottom: `1px solid ${t.divider}` }}>
    <span style={{ width: 106, flexShrink: 0, color: t.textMuted, fontSize: 11,
                   textTransform: 'uppercase', letterSpacing: '0.04em', paddingTop: 1 }}>
      {label}
    </span>
    <span style={{ color: t.text, fontSize: 13, wordBreak: 'break-all' }}>{value}</span>
  </div>
)

export default function ScenePanel({
  feature, theme: t, stackStart, stackEnd,
  stackCount, stackPlatform, workdir, aoiWkt, downloaderType, stackOpen, onClose, onStackClick,
}: Props) {
  const { t: tr } = useTranslation()
  const { width, onHandleMouseDown } = useResizable(280)
  const p     = feature.properties ?? {}
  const stack = stackInfo(p)

  const [ajStatus,  setAjStatus]  = useState<'idle'|'running'|'done'|'error'>('idle')
  const [ajMessage, setAjMessage] = useState('')

  // Reset Add Job status when feature changes
  useEffect(() => { setAjStatus('idle'); setAjMessage('') }, [feature])

  const handleAddJob = useCallback(async () => {
    if (!stack || !stackStart || !stackEnd) return
    setAjStatus('running')
    try {
      const res = await fetch(`${API}/api/add-job`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workdir,
          relativeOrbit: stack.path,
          // frame is meaningless for bursts (ASF returns none) — the folder
          // is named per-burst from subswath + burst_id instead.
          frame: stack.isBurst ? undefined : stack.frame,
          subswath:    stack.isBurst ? stack.subswath : undefined,
          burst_id: stack.isBurst ? stack.burstID : undefined,
          start: stackStart,
          end: stackEnd,
          wkt: aoiWkt ?? null,
          flightDirection: (feature.properties?.flightDirection as string) ?? null,
          // platform intentionally omitted — the clicked feature is one scene
          // out of the whole stack, and a track/frame can span a satellite
          // handover (e.g. Sentinel-1C → Sentinel-1D); filtering the stack's
          // search to one scene's platform would silently drop the rest.
          downloaderType,
        }),
      })
      const d = await res.json()
      if (!res.ok) { setAjStatus('error'); setAjMessage(d.detail ?? tr('scenePanel.error')); return }
      setAjStatus('done')
      setAjMessage(d.path ?? d.name ?? '')
    } catch (e) {
      setAjStatus('error')
      setAjMessage(String(e))
    }
  }, [stack, stackStart, stackEnd, workdir, aoiWkt, feature.properties, tr])

  return (
    <div style={{
      position: 'relative', width, height: '100%',
      background: t.bg,
      borderLeft: `1px solid ${t.border}`,
      display: 'flex', flexDirection: 'column',
      boxShadow: '-4px 0 16px rgba(0,0,0,0.25)',
    }}>
      <ResizeHandle onMouseDown={onHandleMouseDown} />
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '10px 14px',
        borderBottom: `1px solid ${t.border}`,
        background: t.bg2, flexShrink: 0,
      }}>
        <span style={{ color: t.text, fontWeight: 600, fontSize: 13 }}>{tr('scenePanel.stackInfo')}</span>
        <button onClick={onClose} style={{
          background: 'none', border: 'none', cursor: 'pointer',
          color: t.textMuted, fontSize: 18, lineHeight: 1, padding: '0 2px',
        }}>×</button>
      </div>

      {/* Stack badge */}
      {stack && (
        <div style={{
          padding: '8px 14px', background: t.bg2,
          borderBottom: `1px solid ${t.border}`,
          display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0,
        }}>
          <span style={{
            background: t.btnActiveBg, color: t.accent,
            borderRadius: 4, padding: '2px 8px', fontSize: 12, fontWeight: 600,
          }}>
            {stack.isBurst && stack.subswath && stack.burstID != null
              ? tr('scenePanel.burstStack', { path: stack.path, subswath: stack.subswath, burstID: stack.burstID })
              : tr('scenePanel.pathFrame', { path: stack.path, frame: stack.frame })}
          </span>
        </div>
      )}

      {/* Properties */}
      <div style={{ overflowY: 'auto', padding: '8px 14px', flex: 1 }}>
        <div>
          {/* SCENES — clickable to open stack list */}
          {row(t, tr('scenePanel.scenes'), stackCount ?? '…')}

          {row(t, tr('topBar.start'), stackStart ?? '—')}
          {row(t, tr('topBar.end'),   stackEnd   ?? '—')}
          {row(t, tr('scenePanel.direction'),    p.flightDirection ?? '—')}
          {row(t, tr('searchFilters.fields.platform'), stackPlatform || p.platform || '—')}
          {row(t, tr('scenePanel.beamMode'),    p.beamModeType    ?? p.beamMode ?? '—')}
          {row(t, tr('scenePanel.polarization'), p.polarization    ?? '—')}
          {p.processingLevel && row(t, tr('scenePanel.level'), p.processingLevel)}
        </div>

        {/* View Detail */}
        <div style={{ marginTop: 14 }}>
          <button
            onClick={onStackClick}
            style={{
              display: 'block', width: '100%', padding: '8px 0', textAlign: 'center',
              background: stackOpen ? t.btnActiveBg : 'transparent',
              color: stackOpen ? t.accent : t.text,
              border: `1px solid ${stackOpen ? t.btnActiveBorder : t.border}`,
              borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
            }}
          >
            {stackOpen ? tr('scenePanel.hideDetail') : tr('scenePanel.viewDetail')}
          </button>
        </div>

        {/* Add Job — run select_pairs for this stack */}
        {stack && stackStart && stackEnd && (
          <div style={{ marginTop: 8 }}>
            <button
              onClick={handleAddJob}
              disabled={ajStatus === 'running'}
              style={{
                display: 'block', width: '100%', padding: '8px 0', textAlign: 'center',
                background: ajStatus === 'done'    ? '#1b3a2a'
                          : ajStatus === 'error'   ? '#b71c1c'
                          : ajStatus === 'running' ? t.bg2
                          : '#0d3b6e',
                color: ajStatus === 'done'    ? '#a5d6a7'
                     : ajStatus === 'error'   ? '#ef9a9a'
                     : ajStatus === 'running' ? t.textMuted
                     : '#90caf9',
                border: `1px solid ${ajStatus === 'done' ? '#2e7d32' : ajStatus === 'error' ? '#c62828' : '#1565c0'}`,
                borderRadius: 6, fontSize: 12, fontWeight: 600,
                cursor: ajStatus === 'running' ? 'wait' : 'pointer',
              }}
            >
              {ajStatus === 'running' ? tr('scenePanel.selectingPairs')
              : ajStatus === 'done'   ? tr('scenePanel.jobAdded')
              : ajStatus === 'error'  ? tr('scenePanel.retry')
              : tr('scenePanel.addJob')}
            </button>
            {ajMessage && (
              <div style={{
                color: ajStatus === 'done' ? '#4caf50' : ajStatus === 'error' ? '#e53935' : t.textMuted,
                fontSize: 11, marginTop: 5,
              }}>{ajMessage}</div>
            )}
          </div>
        )}

      </div>
    </div>
  )
}
