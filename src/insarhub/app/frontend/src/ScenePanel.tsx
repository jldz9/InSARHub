import { useState, useRef, useEffect, useCallback } from 'react'
import type { Theme } from './theme'
import { useResizable, ResizeHandle } from './useResizable'

export function parseStack(s: string): { path: number; frame: number } | null {
  const m = s.match(/\(\s*(\d+)\s*,\s*(\d+)\s*\)/)
  if (!m) return null
  return { path: parseInt(m[1]), frame: parseInt(m[2]) }
}

const API = import.meta.env.DEV ? 'http://localhost:8080' : ''

// Persist active job IDs across ScenePanel unmount/remount, keyed by stack key "(path, frame)"
const _dlJobs:    Map<string, string> = new Map()
const _orbitJobs: Map<string, string> = new Map()

interface Props {
  feature:      GeoJSON.Feature
  theme:        Theme
  stackStart?: string
  stackEnd?:   string
  stackCount:   number | null
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
  stackCount, workdir, aoiWkt, downloaderType, stackOpen, onClose, onStackClick,
}: Props) {
  const { width, onHandleMouseDown } = useResizable(280)
  const p     = feature.properties ?? {}
  const stack = parseStack(p._stack ?? '')

  const stackKey = p._stack ?? ''

  const [dlStatus,  setDlStatus]  = useState<'idle'|'downloading'|'done'|'error'>(
    () => _dlJobs.has(stackKey) ? 'downloading' : 'idle'
  )
  const [dlMessage, setDlMessage] = useState('')
  const pollRef    = useRef<ReturnType<typeof setInterval> | null>(null)
  const dlJobIdRef = useRef<string | null>(_dlJobs.get(stackKey) ?? null)

  const [ajStatus,  setAjStatus]  = useState<'idle'|'running'|'done'|'error'>('idle')
  const [ajMessage, setAjMessage] = useState('')

  const [orbitStatus,  setOrbitStatus]  = useState<'idle'|'running'|'done'|'error'>(
    () => _orbitJobs.has(stackKey) ? 'running' : 'idle'
  )
  const [orbitMessage, setOrbitMessage] = useState('')
  const orbitPollRef  = useRef<ReturnType<typeof setInterval> | null>(null)
  const orbitJobIdRef = useRef<string | null>(_orbitJobs.get(stackKey) ?? null)

  // Resume polling for any in-flight jobs when this panel mounts
  useEffect(() => {
    const dlId = dlJobIdRef.current
    if (dlId) {
      pollRef.current = setInterval(async () => {
        try {
          const r = await fetch(`${API}/api/jobs/${dlId}`)
          const job = await r.json()
          setDlMessage(job.message)
          if (job.status === 'done') {
            clearInterval(pollRef.current!); dlJobIdRef.current = null
            _dlJobs.delete(stackKey); setDlStatus('done')
          } else if (job.status === 'error') {
            clearInterval(pollRef.current!); dlJobIdRef.current = null
            _dlJobs.delete(stackKey); setDlStatus('error')
          }
        } catch { clearInterval(pollRef.current!); _dlJobs.delete(stackKey); setDlStatus('error') }
      }, 1500)
    }
    const orbitId = orbitJobIdRef.current
    if (orbitId) {
      orbitPollRef.current = setInterval(async () => {
        try {
          const r = await fetch(`${API}/api/jobs/${orbitId}`)
          const job = await r.json()
          setOrbitMessage(job.message ?? '')
          if (job.status === 'done') {
            clearInterval(orbitPollRef.current!); orbitJobIdRef.current = null
            _orbitJobs.delete(stackKey); setOrbitStatus('done')
          } else if (job.status === 'error') {
            clearInterval(orbitPollRef.current!); orbitJobIdRef.current = null
            _orbitJobs.delete(stackKey); setOrbitStatus('error')
          }
        } catch { clearInterval(orbitPollRef.current!); _orbitJobs.delete(stackKey); setOrbitStatus('error') }
      }, 1500)
    }
    return () => {
      clearInterval(pollRef.current!)
      clearInterval(orbitPollRef.current!)
    }
  }, [stackKey])

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
          frame: stack.frame,
          start: stackStart,
          end: stackEnd,
          wkt: aoiWkt ?? null,
          flightDirection: (feature.properties?.flightDirection as string) ?? null,
          platform: (feature.properties?.platform as string) ?? null,
          downloaderType,
        }),
      })
      const d = await res.json()
      if (!res.ok) { setAjStatus('error'); setAjMessage(d.detail ?? 'Error'); return }
      setAjStatus('done')
      setAjMessage(d.path ?? d.name ?? '')
    } catch (e) {
      setAjStatus('error')
      setAjMessage(String(e))
    }
  }, [stack, stackStart, stackEnd, workdir, aoiWkt, feature.properties])

  function handleStopDownload() {
    if (pollRef.current) clearInterval(pollRef.current)
    if (dlJobIdRef.current) {
      fetch(`${API}/api/jobs/${dlJobIdRef.current}/stop`, { method: 'POST' }).catch(() => {})
      dlJobIdRef.current = null
    }
    _dlJobs.delete(stackKey)
    setDlStatus('idle')
    setDlMessage('Stopped.')
  }

  async function handleDownloadStack() {
    if (!stack || !stackStart || !stackEnd) return
    setDlStatus('downloading')
    setDlMessage('Searching scenes…')
    try {
      const res  = await fetch(`${API}/api/download-stack`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workdir,
          relativeOrbit: stack.path,
          frame: stack.frame,
          start: stackStart,
          end: stackEnd,
          wkt: aoiWkt ?? null,
          flightDirection: (feature.properties?.flightDirection as string) ?? null,
          platform: (feature.properties?.platform as string) ?? null,
          downloaderType,
        }),
      })
      const data = await res.json()
      if (!res.ok) {
        setDlStatus('error')
        setDlMessage(data.detail ?? `Error ${res.status}`)
        return
      }
      const { job_id } = data
      if (!job_id) { setDlStatus('error'); setDlMessage('No job ID returned'); return }
      dlJobIdRef.current = job_id
      _dlJobs.set(stackKey, job_id)
      pollRef.current = setInterval(async () => {
        try {
          const r   = await fetch(`${API}/api/jobs/${job_id}`)
          const job = await r.json()
          setDlMessage(job.message)
          if (job.status === 'done') {
            clearInterval(pollRef.current!)
            dlJobIdRef.current = null; _dlJobs.delete(stackKey)
            setDlStatus('done')
          } else if (job.status === 'error') {
            clearInterval(pollRef.current!)
            dlJobIdRef.current = null; _dlJobs.delete(stackKey)
            setDlStatus('error')
          }
        } catch {
          clearInterval(pollRef.current!)
          _dlJobs.delete(stackKey)
          setDlStatus('error')
          setDlMessage('Lost connection to server')
        }
      }, 1500)
    } catch (e) {
      setDlStatus('error')
      setDlMessage(String(e))
    }
  }

  function handleStopOrbit() {
    if (orbitPollRef.current) clearInterval(orbitPollRef.current)
    if (orbitJobIdRef.current) {
      fetch(`${API}/api/jobs/${orbitJobIdRef.current}/stop`, { method: 'POST' }).catch(() => {})
      orbitJobIdRef.current = null
    }
    _orbitJobs.delete(stackKey)
    setOrbitStatus('idle')
    setOrbitMessage('Stopped.')
  }

  async function handleDownloadOrbit() {
    if (!stack || !stackStart || !stackEnd) return
    setOrbitStatus('running')
    setOrbitMessage('Starting…')
    try {
      const res = await fetch(`${API}/api/download-orbit-stack`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workdir,
          relativeOrbit: stack.path,
          frame: stack.frame,
          start: stackStart,
          end: stackEnd,
          wkt: aoiWkt ?? null,
          flightDirection: (feature.properties?.flightDirection as string) ?? null,
          platform: (feature.properties?.platform as string) ?? null,
          downloaderType,
        }),
      })
      const { job_id } = await res.json()
      orbitJobIdRef.current = job_id
      _orbitJobs.set(stackKey, job_id)
      orbitPollRef.current = setInterval(async () => {
        const r   = await fetch(`${API}/api/jobs/${job_id}`)
        const job = await r.json()
        setOrbitMessage(job.message ?? '')
        if (job.status === 'done') {
          clearInterval(orbitPollRef.current!)
          orbitJobIdRef.current = null; _orbitJobs.delete(stackKey)
          setOrbitStatus('done')
        } else if (job.status === 'error') {
          clearInterval(orbitPollRef.current!)
          orbitJobIdRef.current = null; _orbitJobs.delete(stackKey)
          setOrbitStatus('error')
        }
      }, 1500)
    } catch (e) {
      setOrbitStatus('error')
      setOrbitMessage(String(e))
    }
  }

  const dlStatusColor = dlStatus === 'done' ? '#4caf50' : dlStatus === 'error' ? '#e53935' : t.textMuted

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
        <span style={{ color: t.text, fontWeight: 600, fontSize: 13 }}>Stack Info</span>
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
            Path {stack.path} · Frame {stack.frame}
          </span>
        </div>
      )}

      {/* Properties */}
      <div style={{ overflowY: 'auto', padding: '8px 14px', flex: 1 }}>
        <div>
          {/* SCENES — clickable to open stack list */}
          {row(t, 'Scenes', stackCount ?? '…')}

          {row(t, 'Start', stackStart ?? '—')}
          {row(t, 'End',   stackEnd   ?? '—')}
          {row(t, 'Direction',    p.flightDirection ?? '—')}
          {row(t, 'Platform',     p.platform        ?? '—')}
          {row(t, 'Beam Mode',    p.beamModeType    ?? p.beamMode ?? '—')}
          {row(t, 'Polarization', p.polarization    ?? '—')}
          {p.processingLevel && row(t, 'Level', p.processingLevel)}
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
            {stackOpen ? '◂ Hide Detail' : '▸ View Detail'}
          </button>
        </div>

        {/* Download Stack */}
        <div style={{ marginTop: 8 }}>
          {dlStatus === 'downloading' ? (
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <button
                onClick={handleStopDownload}
                style={{
                  flex: 1, padding: '8px 0', textAlign: 'center',
                  background: '#e53935', color: '#fff',
                  border: '1px solid #e53935',
                  borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                }}
              >
                Stop
              </button>
              {dlMessage && (
                <span style={{
                  fontSize: 11, color: t.accent, fontFamily: 'monospace',
                  background: t.btnActiveBg, border: `1px solid ${t.btnActiveBorder}`,
                  borderRadius: 4, padding: '4px 8px', whiteSpace: 'nowrap',
                }}>
                  {dlMessage}
                </span>
              )}
            </div>
          ) : (
            <button
              onClick={handleDownloadStack}
              disabled={!stack || !stackStart || !stackEnd}
              style={{
                display: 'block', width: '100%', padding: '8px 0', textAlign: 'center',
                background: dlStatus === 'done'  ? '#1b5e20'
                          : dlStatus === 'error' ? '#b71c1c'
                          : t.btnActiveBg,
                color: dlStatus === 'done'  ? '#a5d6a7'
                     : dlStatus === 'error' ? '#ef9a9a'
                     : t.accent,
                border: `1px solid ${t.btnActiveBorder}`,
                borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
              }}
            >
              {dlStatus === 'done'  ? '✓ Stack Downloaded'
             : dlStatus === 'error' ? '✕ Retry'
             : `↓ Download Stack`}
            </button>
          )}
          {dlMessage && dlStatus !== 'downloading' && (
            <div style={{ color: dlStatusColor, fontSize: 11, marginTop: 5 }}>{dlMessage}</div>
          )}
        </div>

        {/* Download Orbit Files — S1_SLC only */}
        {downloaderType === 'S1_SLC' && stack && stackStart && stackEnd && (
          <div style={{ marginTop: 8 }}>
            {orbitStatus === 'running' ? (
              <button
                onClick={handleStopOrbit}
                style={{
                  display: 'block', width: '100%', padding: '8px 0', textAlign: 'center',
                  background: '#e53935', color: '#fff',
                  border: '1px solid #e53935',
                  borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                }}
              >
                Stop
              </button>
            ) : (
              <button
                onClick={orbitStatus === 'error' ? handleDownloadOrbit : handleDownloadOrbit}
                style={{
                  display: 'block', width: '100%', padding: '8px 0', textAlign: 'center',
                  background: orbitStatus === 'done'  ? '#1b3a2a'
                            : orbitStatus === 'error' ? '#b71c1c'
                            : 'transparent',
                  color: orbitStatus === 'done'  ? '#a5d6a7'
                       : orbitStatus === 'error' ? '#ef9a9a'
                       : t.text,
                  border: `1px solid ${t.border}`,
                  borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                }}
              >
                {orbitStatus === 'done'  ? '✓ Orbit Files Downloaded'
               : orbitStatus === 'error' ? '✕ Retry'
               : '⬡ Download Orbit Files'}
              </button>
            )}
            {orbitMessage && (
              <div style={{
                color: orbitStatus === 'done' ? '#4caf50' : orbitStatus === 'error' ? '#e53935' : t.textMuted,
                fontSize: 11, marginTop: 5,
              }}>{orbitMessage}</div>
            )}
          </div>
        )}

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
              {ajStatus === 'running' ? '⟳ Selecting Pairs…'
              : ajStatus === 'done'   ? '✓ Job Added'
              : ajStatus === 'error'  ? '✕ Retry'
              : '+ Add Job'}
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
