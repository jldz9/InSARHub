import { useTranslation } from 'react-i18next'
import type { Theme } from './theme'

export type DrawMode = 'box' | 'polygon' | 'pin' | null
export type Basemap  = 'satellite' | 'topo'

interface Props {
  drawMode:         DrawMode
  theme:            Theme
  onDrawModeChange: (m: DrawMode) => void
  onClearAoi:       () => void
  onShapefileUpload:(file: File) => void
  mouseCoords:      { lat: number; lng: number } | null
  rasterValue?:     number | null
}

// ── Flat SVG icons ─────────────────────────────────────────────────────────

function IconBox() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="1.5" y="3.5" width="13" height="9" rx="1" strokeDasharray="3 1.8" />
    </svg>
  )
}

function IconPolygon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="8,1.5 14.5,6 12,14 4,14 1.5,6" />
    </svg>
  )
}

function IconPin() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="currentColor">
      <path d="M8 1.5a4.5 4.5 0 0 1 4.5 4.5c0 3.2-4.5 8.5-4.5 8.5S3.5 9.2 3.5 6A4.5 4.5 0 0 1 8 1.5zm0 2.5a2 2 0 1 0 0 4 2 2 0 0 0 0-4z" />
    </svg>
  )
}

function IconUpload() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 5h3.5l1.5 1.5H14V13H2V5z" />
      <path d="M8 7.5v3.5M6.5 9l1.5-1.5L9.5 9" />
    </svg>
  )
}

function IconDelete() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 4.5h10M6 4.5V3h4v1.5M5.5 4.5V13h5V4.5" />
      <path d="M7 7v3.5M9 7v3.5" />
    </svg>
  )
}

// ── Draw tool config ────────────────────────────────────────────────────────

const DRAW_TOOLS: { mode: DrawMode; icon: React.ReactNode; titleKey: string }[] = [
  { mode: 'box',     icon: <IconBox />,     titleKey: 'mapToolbar.dragBoxAoi' },
  { mode: 'polygon', icon: <IconPolygon />, titleKey: 'mapToolbar.drawPolygonAoi' },
  { mode: 'pin',     icon: <IconPin />,     titleKey: 'mapToolbar.placePointAoi' },
]

// ── Component ───────────────────────────────────────────────────────────────

export default function MapToolbar({
  drawMode, theme, onDrawModeChange, onClearAoi, onShapefileUpload, mouseCoords, rasterValue,
}: Props) {
  const t = theme
  const { t: tr } = useTranslation()

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (f) onShapefileUpload(f)
    e.target.value = ''
  }

  return (
    <div style={{
      position: 'absolute', top: 48, left: 0, right: 0, zIndex: 15,
      background: t.bg2, borderBottom: `1px solid ${t.border}`,
      display: 'flex', alignItems: 'center', gap: 4, padding: '4px 14px',
      height: 36,
    }}>

      <Section label={tr('mapToolbar.areaOfInterest')} t={t}>
        {DRAW_TOOLS.map(({ mode, icon, titleKey }) => (
          <ToolBtn key={mode!} icon={icon} title={tr(titleKey)}
            active={drawMode === mode} t={t}
            onClick={() => onDrawModeChange(drawMode === mode ? null : mode)} />
        ))}
        <label style={btnStyle(false, t)} title={tr('mapToolbar.uploadShapefile')}>
          <IconUpload />
          <input type="file" accept=".zip,.shp,.gpkg" style={{ display: 'none' }} onChange={handleFile} />
        </label>
        <ToolBtn icon={<IconDelete />} title={tr('mapToolbar.clearAoi')} active={false} t={t} onClick={onClearAoi} />
      </Section>

      {/* ── Legend + coords (right-aligned) ── */}
      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 14 }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: t.text }}>
          <span style={{ width: 22, height: 3, background: '#00bcd4', display: 'inline-block', borderRadius: 2 }} />
          {tr('mapToolbar.descending')}
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: t.text }}>
          <span style={{ width: 22, height: 3, background: '#f39c12', display: 'inline-block', borderRadius: 2 }} />
          {tr('mapToolbar.ascending')}
        </span>
        {mouseCoords && (
          <span style={{ color: t.textMuted, fontSize: 11, fontFamily: 'monospace', marginLeft: 8 }}>
            lat {mouseCoords.lat.toFixed(4)}°&nbsp;&nbsp;lon {mouseCoords.lng.toFixed(4)}°
          </span>
        )}
        {rasterValue != null && (
          <span style={{ color: '#90caf9', fontSize: 11, fontFamily: 'monospace', marginLeft: 4 }}>
            val {rasterValue.toFixed(4)}
          </span>
        )}
      </div>
    </div>
  )
}

function Section({ label, children, t }: { label: string; children: React.ReactNode; t: Theme }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
      <span style={{ color: t.textMuted, fontSize: 11, fontWeight: 600, marginRight: 5,
        whiteSpace: 'nowrap', letterSpacing: '0.02em' }}>
        {label}
      </span>
      {children}
    </div>
  )
}

function ToolBtn({ icon, title, active, onClick, t }:
  { icon: React.ReactNode; title: string; active: boolean; onClick: () => void; t: Theme }) {
  return (
    <button title={title} onClick={onClick} style={btnStyle(active, t)}>
      {icon}
    </button>
  )
}

function btnStyle(active: boolean, t: Theme): React.CSSProperties {
  return {
    width: 28, height: 26,
    background: active ? t.btnActiveBg : 'transparent',
    color: active ? t.btnActiveFg : t.text,
    border: `1px solid ${active ? t.btnActiveBorder : t.border}`,
    borderRadius: 3, cursor: 'pointer',
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    padding: 0,
  }
}