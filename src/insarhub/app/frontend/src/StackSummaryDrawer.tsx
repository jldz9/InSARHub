import { useMemo } from 'react'
import type { Theme } from './theme'

interface StackSummary {
  stackKey:              string
  path:                  number
  frame:                 number
  sceneCount:            number
  startDate:             string
  endDate:               string
  flightDirection:       string
  representativeFeature: GeoJSON.Feature
}

interface Props {
  footprints:       GeoJSON.FeatureCollection
  theme:            Theme
  selectedStackKey: string | null
  onStackHover:     (key: string | null) => void
  onStackClick:     (feature: GeoJSON.Feature) => void
  onClose:          () => void
}

function parseStack(key: string): { path: number; frame: number } {
  const m = key.match(/\(?\s*(\d+)\s*,\s*(\d+)\s*\)?/)
  return m ? { path: parseInt(m[1]), frame: parseInt(m[2]) } : { path: 0, frame: 0 }
}

export default function StackSummaryDrawer({
  footprints, theme: t, selectedStackKey, onStackHover, onStackClick, onClose,
}: Props) {
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

  return (
    <div style={{
      width: 260, height: '100%',
      background: t.bg, borderLeft: `1px solid ${t.border}`,
      display: 'flex', flexDirection: 'column',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '8px 12px', borderBottom: `1px solid ${t.border}`,
        background: t.bg2, flexShrink: 0,
      }}>
        <span style={{ color: t.text, fontWeight: 700, fontSize: 13 }}>
          {stacks.length} Stack{stacks.length !== 1 ? 's' : ''}
        </span>
        <button
          onClick={onClose}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: t.textMuted, fontSize: 18, lineHeight: 1, padding: '0 2px' }}
        >×</button>
      </div>

      {/* Stack list */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {stacks.map(s => {
          const dir      = (s.flightDirection ?? '').toUpperCase()
          const dirColor = dir === 'ASCENDING' ? '#f39c12' : dir === 'DESCENDING' ? '#00bcd4' : t.textMuted
          const isActive = s.stackKey === selectedStackKey
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
                background: isActive ? t.inputBg : 'transparent',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 5 }}>
                <span style={{ color: t.text, fontWeight: 600, fontSize: 12 }}>
                  Path {s.path} · Frame {s.frame}
                </span>
                <span style={{
                  color: dirColor, fontSize: 10, fontWeight: 700,
                  background: `${dirColor}22`, borderRadius: 3, padding: '1px 6px',
                }}>
                  {dir === 'ASCENDING' ? 'ASC' : dir === 'DESCENDING' ? 'DESC' : dir || '—'}
                </span>
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
          )
        })}
      </div>
    </div>
  )
}
