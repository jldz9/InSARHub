import { useState, useRef } from 'react'
import type { Theme } from './theme'
import { useResizable, ResizeHandle } from './useResizable'

interface Props {
  feature: GeoJSON.Feature
  theme:   Theme
  workdir: string
  onClose: () => void
}

const API = import.meta.env.DEV ? 'http://localhost:8080' : ''

function fmtTime(iso: string | undefined | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const mm   = String(d.getUTCMonth() + 1).padStart(2, '0')
  const dd   = String(d.getUTCDate()).padStart(2, '0')
  const yyyy = d.getUTCFullYear()
  const hh   = String(d.getUTCHours()).padStart(2, '0')
  const min  = String(d.getUTCMinutes()).padStart(2, '0')
  const ss   = String(d.getUTCSeconds()).padStart(2, '0')
  return `${mm}/${dd}/${yyyy}, ${hh}:${min}:${ss}Z`
}

function fmtBytes(b: number | undefined | null): string {
  if (!b) return '—'
  const gb = b / 1073741824
  return gb >= 1 ? `${gb.toFixed(2)} GB` : `${(b / 1048576).toFixed(1)} MB`
}

function fmtCoord(v: number | undefined | null, suffix: string): string {
  if (v == null) return '—'
  return `${Number(v).toFixed(4)}° ${suffix}`
}

function fmtList(v: string[] | string | undefined | null): string {
  if (!v) return '—'
  if (Array.isArray(v)) return v.join(', ') || '—'
  return String(v)
}

export default function SceneDetailPanel({ feature, theme: t, workdir, onClose }: Props) {
  const { width, onHandleMouseDown } = useResizable(320)
  const p = feature.properties ?? {}

  const [dlStatus,  setDlStatus]  = useState<'idle'|'downloading'|'done'|'error'>('idle')
  const [dlMessage, setDlMessage] = useState('')
  const [copied,    setCopied]    = useState(false)
  const pollRef  = useRef<ReturnType<typeof setInterval> | null>(null)
  const jobIdRef = useRef<string | null>(null)

  async function handleDownload() {
    if (!p.url) return
    setDlStatus('downloading')
    setDlMessage('Starting…')
    try {
      const res  = await fetch(`${API}/api/download-scene`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: p.url, workdir }),
      })
      const { job_id } = await res.json()
      jobIdRef.current = job_id
      pollRef.current = setInterval(async () => {
        const r   = await fetch(`${API}/api/jobs/${job_id}`)
        const job = await r.json()
        setDlMessage(job.message)
        if (job.status === 'done') {
          clearInterval(pollRef.current!)
          jobIdRef.current = null
          setDlStatus(job.message === 'Stopped.' ? 'idle' : 'done')
        } else if (job.status === 'error') {
          clearInterval(pollRef.current!)
          jobIdRef.current = null
          setDlStatus('error')
        }
      }, 1500)
    } catch (e) {
      setDlStatus('error')
      setDlMessage(String(e))
    }
  }

  async function handleStop() {
    const id = jobIdRef.current
    if (!id) return
    clearInterval(pollRef.current!)
    pollRef.current = null
    jobIdRef.current = null
    setDlStatus('idle')
    setDlMessage('')
    await fetch(`${API}/api/jobs/${id}/stop`, { method: 'POST' })
  }

  function handleCopyUrl() {
    if (!p.url) return
    navigator.clipboard.writeText(p.url).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  // ── Layout helpers ────────────────────────────────────────────────────────
  const section = (title: string, children: React.ReactNode) => (
    <div style={{ marginBottom: 12 }}>
      <div style={{
        color: t.textMuted, fontSize: 10, textTransform: 'uppercase',
        letterSpacing: '0.06em', marginBottom: 4, fontWeight: 600,
      }}>
        {title}
      </div>
      <div style={{
        background: t.bg2, borderRadius: 6,
        border: `1px solid ${t.border}`, overflow: 'hidden',
      }}>
        {children}
      </div>
    </div>
  )

  const field = (label: string, value: React.ReactNode, last = false) => (
    <div style={{
      display: 'flex', alignItems: 'baseline', gap: 6,
      padding: '5px 10px',
      borderBottom: last ? 'none' : `1px solid ${t.divider}`,
    }}>
      <span style={{ color: t.textMuted, fontSize: 11, flexShrink: 0, width: 120 }}>{label}</span>
      <span style={{ color: t.accent, fontSize: 11, fontWeight: 500 }}>•</span>
      <span style={{ color: t.text, fontSize: 11, wordBreak: 'break-all' }}>{value}</span>
    </div>
  )

  const dlStatusColor = dlStatus === 'done' ? '#4caf50' : dlStatus === 'error' ? '#e53935' : t.textMuted

  return (
    <div style={{
      position: 'relative', width, height: '100%',
      background: t.bg,
      borderLeft: `1px solid ${t.border}`,
      display: 'flex', flexDirection: 'column',
      boxShadow: '-4px 0 12px rgba(0,0,0,0.2)',
    }}>
      <ResizeHandle onMouseDown={onHandleMouseDown} />
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '10px 12px',
        borderBottom: `1px solid ${t.border}`,
        background: t.bg2, flexShrink: 0,
      }}>
        <span style={{ color: t.text, fontWeight: 600, fontSize: 13 }}>Scene Detail</span>
        <button onClick={onClose} style={{
          background: 'none', border: 'none', cursor: 'pointer',
          color: t.textMuted, fontSize: 18, lineHeight: 1, padding: '0 2px',
        }}>×</button>
      </div>

      {/* Granule name */}
      <div style={{
        padding: '8px 12px', background: t.bg2,
        borderBottom: `1px solid ${t.border}`, flexShrink: 0,
      }}>
        <div style={{ color: t.textMuted, fontSize: 10, textTransform: 'uppercase',
                      letterSpacing: '0.05em', marginBottom: 3 }}>Granule</div>
        <div style={{ color: t.text, fontSize: 10, wordBreak: 'break-all',
                      fontFamily: 'monospace', lineHeight: 1.5 }}>
          {p.sceneName ?? p.fileID ?? '—'}
        </div>
      </div>

      <div style={{ overflowY: 'auto', padding: '10px 12px', flex: 1 }}>

        {section('Acquisition', <>
          {field('Radar Frequency', 'C-band')}
          {field('Start Time',      fmtTime(p.startTime))}
          {field('Stop Time',       fmtTime(p.stopTime))}
          {field('Processing Date', fmtTime(p.processingDate), true)}
        </>)}

        {section('Sensor / Orbit', <>
          {field('Platform',         p.platform      ?? '—')}
          {field('Sensor',           p.sensor        ?? '—')}
          {field('Beam Mode',        p.beamModeType  ?? p.beamMode ?? '—')}
          {field('Path',             p.pathNumber    ?? '—')}
          {field('Frame',            p.frameNumber   ?? '—')}
          {field('Flight Direction', p.flightDirection ?? '—')}
          {field('Absolute Orbit',   p.orbit         ?? '—')}
          {field('Polarization',     p.polarization  ?? '—', true)}
        </>)}

        {section('Geometry', <>
          {field('Center Lat', fmtCoord(p.centerLat, 'N/S'))}
          {field('Center Lon', fmtCoord(p.centerLon, 'E/W'), true)}
        </>)}

        {(p.perpendicularBaseline != null || p.temporalBaseline != null) &&
          section('Baseline', <>
            {p.perpendicularBaseline != null &&
              field('Perp. Baseline', `${Number(p.perpendicularBaseline).toFixed(1)} m`)}
            {p.temporalBaseline != null &&
              field('Temporal Baseline', `${p.temporalBaseline} days`, true)}
          </>)
        }

        {section('Processing', <>
          {field('Level',        p.processingLevel ?? '—')}
          {field('Granule Type', p.granuleType     ?? '—')}
          {field('PGE Version',  p.pgeVersion      ?? '—')}
          {field('Group ID',     p.groupID         ?? '—', true)}
        </>)}

        {section('File', <>
          {field('File Size', fmtBytes(p.bytes))}
          {field('MD5',
            <span style={{ fontFamily: 'monospace', fontSize: 10 }}>{p.md5sum ?? '—'}</span>
          )}
          {field('S3 URLs', fmtList(p.s3Urls), true)}
        </>)}

        {/* Download section */}
        {p.url && (
          <div style={{ marginTop: 4 }}>
            {/* Action buttons */}
            <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
              {dlStatus === 'downloading' ? <>
                <button
                  onClick={handleStop}
                  style={{
                    flex: 1, padding: '7px 0',
                    background: '#b71c1c', color: '#ef9a9a',
                    border: '1px solid #e53935',
                    borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                  }}
                >
                  ■ Stop
                </button>
                {dlMessage && (
                  <span style={{
                    display: 'flex', alignItems: 'center',
                    color: t.textMuted, fontSize: 11, fontFamily: 'monospace',
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  }}>{dlMessage}</span>
                )}
              </> : (
              <button
                onClick={dlStatus === 'error' ? handleDownload : handleDownload}
                style={{
                  flex: 1, padding: '7px 0',
                  background: dlStatus === 'done'   ? '#1b5e20'
                             : dlStatus === 'error' ? '#b71c1c'
                             : t.btnActiveBg,
                  color: dlStatus === 'done'   ? '#a5d6a7'
                       : dlStatus === 'error'  ? '#ef9a9a'
                       : t.accent,
                  border: `1px solid ${t.btnActiveBorder}`,
                  borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                }}
              >
                {dlStatus === 'done'  ? '✓ Downloaded'
                : dlStatus === 'error' ? '✕ Retry'
                : '↓ Download'}
              </button>
              )}

              {/* Copy URL */}
              <button
                onClick={handleCopyUrl}
                title="Copy download URL"
                style={{
                  padding: '7px 10px',
                  background: 'transparent',
                  color: copied ? '#4caf50' : t.textMuted,
                  border: `1px solid ${t.border}`,
                  borderRadius: 6, fontSize: 12, cursor: 'pointer',
                }}
              >
                {copied ? '✓' : '⎘'}
              </button>
            </div>

            {/* Status message (done / error only — progress shown inline while downloading) */}
            {dlMessage && dlStatus !== 'downloading' && (
              <div style={{ color: dlStatusColor, fontSize: 11, marginBottom: 6 }}>
                {dlMessage}
              </div>
            )}

            {/* Browse image link */}
            {p.browse && (
              <a href={p.browse} target="_blank" rel="noreferrer" style={{
                display: 'block', textAlign: 'center',
                padding: '7px 0',
                background: 'transparent', color: t.textMuted,
                border: `1px solid ${t.border}`,
                borderRadius: 6, fontSize: 12, fontWeight: 500,
                textDecoration: 'none',
              }}>
                ⬚ Browse Image
              </a>
            )}
          </div>
        )}

      </div>
    </div>
  )
}
