import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Basemap } from './MapToolbar'
import type { Theme } from './theme'

const OPTIONS: { id: Basemap; labelKey: string }[] = [
  { id: 'satellite', labelKey: 'basemapSwitcher.satellite' },
  { id: 'topo',      labelKey: 'basemapSwitcher.topo' },
]

function IconLayers() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none"
      stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d="M1.5 5.5 8 2.5l6.5 3-6.5 3z" />
      <path d="M1.5 9.5l6.5 3 6.5-3" />
      <path d="M1.5 7l6.5 3 6.5-3" />
    </svg>
  )
}

interface Props {
  basemap:         Basemap
  onBasemapChange: (b: Basemap) => void
  theme:           Theme
}

export default function BasemapSwitcher({ basemap, onBasemapChange, theme: t }: Props) {
  const [open, setOpen] = useState(false)
  const { t: tr } = useTranslation()

  return (
    /* Positioned to the right of MapLibre's scale bar (bottom-left corner) */
    <div style={{ position: 'absolute', bottom: 6, left: 92, zIndex: 10 }}>

      {/* Popup — opens upward */}
      {open && (
        <div style={{
          position: 'absolute', bottom: 36, left: 0,
          background: t.bg, border: `1px solid ${t.border}`,
          borderRadius: 6, overflow: 'hidden',
          boxShadow: '0 4px 14px rgba(0,0,0,0.4)',
          minWidth: 96,
        }}>
          {OPTIONS.map(({ id, labelKey }, i) => (
            <button
              key={id}
              onClick={() => { onBasemapChange(id); setOpen(false) }}
              style={{
                display: 'block', width: '100%',
                padding: '7px 14px', textAlign: 'left',
                background: basemap === id ? t.btnActiveBg : 'transparent',
                color:      basemap === id ? (t.isDark ? '#e0f0ff' : t.btnActiveFg) : t.text,
                border: 'none', cursor: 'pointer', fontSize: 12,
                fontWeight: basemap === id ? 700 : 400,
                borderBottom: i < OPTIONS.length - 1 ? `1px solid ${t.border}` : 'none',
              }}
            >
              {tr(labelKey)}
            </button>
          ))}
        </div>
      )}

      {/* Round icon button */}
      <button
        title={tr('basemapSwitcher.switchBasemap')}
        onClick={() => setOpen(o => !o)}
        style={{
          width: 30, height: 30, borderRadius: '50%',
          background: t.bg,
          border: `1px solid ${t.border}`,
          color: t.text,
          cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          boxShadow: '0 1px 5px rgba(0,0,0,0.35)',
          padding: 0,
        }}
      >
        <IconLayers />
      </button>
    </div>
  )
}