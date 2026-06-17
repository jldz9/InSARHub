import { parseStack } from './ScenePanel'
import type { Theme } from './theme'
import { useResizable, ResizeHandle } from './useResizable'

interface Props {
  stackKey:      string
  scenes:        GeoJSON.Feature[]
  theme:         Theme
  selectedScene: GeoJSON.Feature | null
  onClose:       () => void
  onSceneClick:  (f: GeoJSON.Feature) => void
}

function shortDate(iso: string | undefined): string {
  if (!iso) return '—'
  // ISO "2021-03-15T..." → "2021-03-15"
  return iso.slice(0, 10)
}

/**
 * Derive a concise product-type label from ASF properties.
 * Examples: "S1A SLC", "S1B BURST", "S1A GRD"
 */
function productLabel(p: Record<string, any>): string {
  // Platform: "Sentinel-1A" → "S1A", "Sentinel-1B" → "S1B", already short → keep
  const raw      = (p.platform ?? p.sensor ?? '') as string
  const platform = raw.replace(/sentinel-?/i, 'S').replace(/\s+/g, '').toUpperCase()
                      || raw.slice(0, 4)

  // Product type from processingLevel or granuleType
  const level = (p.processingLevel ?? '') as string
  const gtype = (p.granuleType     ?? '') as string

  let type = ''
  if (/burst/i.test(gtype) || /burst/i.test(level)) {
    type = 'BURST'
  } else if (/slc/i.test(level) || /slc/i.test(gtype)) {
    type = 'SLC'
  } else if (/grd/i.test(level) || /grd/i.test(gtype)) {
    type = 'GRD'
  } else if (level) {
    type = level.toUpperCase().slice(0, 6)
  }

  return [platform, type].filter(Boolean).join(' ')
}

export default function StackSceneList({
  stackKey, scenes, theme: t, selectedScene, onClose, onSceneClick,
}: Props) {
  const { width, onHandleMouseDown } = useResizable(240)
  const stack = parseStack(stackKey)

  const sorted = [...scenes].sort((a, b) => {
    const ta = a.properties?.startTime ?? ''
    const tb = b.properties?.startTime ?? ''
    return tb.localeCompare(ta)
  })

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
        <div>
          <div style={{ color: t.text, fontWeight: 600, fontSize: 13 }}>
            {stack ? `Path ${stack.path} · Frame ${stack.frame}` : 'Stack Scenes'}
          </div>
          <div style={{ color: t.textMuted, fontSize: 11 }}>
            {scenes.length} scene{scenes.length !== 1 ? 's' : ''}
          </div>
        </div>
        <button onClick={onClose} style={{
          background: 'none', border: 'none', cursor: 'pointer',
          color: t.textMuted, fontSize: 18, lineHeight: 1, padding: '0 2px',
        }}>×</button>
      </div>

      {/* Scene list */}
      <div style={{ overflowY: 'auto', flex: 1 }}>
        {sorted.map((f, i) => {
          const p          = f.properties ?? {}
          const isSelected = selectedScene?.properties?.sceneName === p.sceneName

          return (
            <button key={i} onClick={() => onSceneClick(f)} style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              width: '100%', textAlign: 'left',
              padding: '8px 12px',
              background: isSelected ? t.btnActiveBg : 'transparent',
              border: 'none',
              borderBottom: `1px solid ${t.divider}`,
              cursor: 'pointer',
            }}>
              <div>
                <div style={{ color: isSelected ? t.accent : t.text, fontSize: 12, fontWeight: 500 }}>
                  {shortDate(p.startTime)}
                </div>
                <div style={{ color: t.textMuted, fontSize: 11 }}>
                  {productLabel(p)}
                </div>
              </div>
              {isSelected && (
                <span style={{ color: t.accent, fontSize: 14 }}>›</span>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}
