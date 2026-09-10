import { useEffect, useState } from 'react'
import type { Theme } from './theme'

export type Status = { id: number; text: string; kind: 'info' | 'error' } | null

/** How long a message stays fully visible before it starts fading, and how
 * long the fade itself takes. The CSS below and the unmount timer share these,
 * so they must stay in step. */
const VISIBLE_MS = 5000
const FADE_MS    = 450

interface Props {
  status: Status
  theme:  Theme
}

/** Transient message line along the bottom of the map.
 *
 * Search progress and result summaries, plus the AOI/file errors that were
 * previously written to a piece of state nothing rendered -- so a failed
 * shapefile upload or an unparseable WKT string looked like nothing had
 * happened at all.
 *
 * Overlaid on the map rather than inserted into the column above it, so
 * showing or hiding a message never reflows the map. It fades out on its own
 * and is click-through, so it never needs dismissing and never swallows a
 * click meant for the map underneath. */
export default function StatusBar({ status, theme }: Props) {
  if (!status) return null
  // Keyed on the message id so each message gets a fresh instance: the
  // animation replays and the fade timer restarts even when the same text
  // is shown twice in a row.
  return <StatusMessage key={status.id} status={status} theme={theme} />
}

function StatusMessage({ status, theme: t }: { status: NonNullable<Status>; theme: Theme }) {
  const [gone, setGone] = useState(false)
  const isError = status.kind === 'error'

  useEffect(() => {
    const id = setTimeout(() => setGone(true), VISIBLE_MS + FADE_MS)
    return () => clearTimeout(id)
  }, [])

  if (gone) return null

  return (
    <>
      <style>{`
        @keyframes insarStatusIn {
          from { opacity: 0; transform: translate(-50%, 10px); }
          to   { opacity: 1; transform: translate(-50%, 0);    }
        }
        @keyframes insarStatusNudge {
          0%, 100% { transform: translate(-50%, 0);             }
          25%      { transform: translate(calc(-50% - 3px), 0); }
          75%      { transform: translate(calc(-50% + 3px), 0); }
        }
        @keyframes insarStatusOut {
          from { opacity: 1; transform: translate(-50%, 0);   }
          to   { opacity: 0; transform: translate(-50%, 6px); }
        }
        .insar-status {
          animation: insarStatusIn 0.18s ease-out,
                     insarStatusOut ${FADE_MS}ms ease-in ${VISIBLE_MS}ms forwards;
        }
        .insar-status-error {
          animation: insarStatusIn 0.18s ease-out,
                     insarStatusNudge 0.22s ease-in-out 0.18s,
                     insarStatusOut ${FADE_MS}ms ease-in ${VISIBLE_MS}ms forwards;
        }
        @media (prefers-reduced-motion: reduce) {
          .insar-status, .insar-status-error {
            animation: insarStatusOut ${FADE_MS}ms ease-in ${VISIBLE_MS}ms forwards;
          }
        }
      `}</style>

      <div
        className={isError ? 'insar-status-error' : 'insar-status'}
        style={{
          position: 'absolute', bottom: 16, left: '50%', transform: 'translate(-50%, 0)',
          zIndex: 100, maxWidth: 'min(680px, calc(100% - 32px))',
          // Click-through: the map underneath stays fully usable while a
          // message is on screen.
          pointerEvents: 'none',
          padding: '6px 12px', borderRadius: 6,
          background: t.bg,
          border: `1px solid ${isError ? '#e53935' : t.border}`,
          boxShadow: t.isDark ? '0 2px 10px rgba(0,0,0,0.5)' : '0 2px 10px rgba(0,0,0,0.15)',
          color: isError ? '#e53935' : t.text,
          fontSize: 12, lineHeight: 1.45,
        }}>
        {status.text}
      </div>
    </>
  )
}
