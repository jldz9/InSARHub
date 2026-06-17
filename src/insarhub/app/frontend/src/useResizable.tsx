import { useState, useRef } from 'react'

export function useResizable(initial: number, min = 160, max = 800) {
  const [width, setWidth] = useState(initial)
  const startRef = useRef<{ x: number; w: number } | null>(null)

  function onHandleMouseDown(e: React.MouseEvent) {
    e.preventDefault()
    startRef.current = { x: e.clientX, w: width }
    const onMove = (ev: MouseEvent) => {
      if (!startRef.current) return
      setWidth(Math.max(min, Math.min(max, startRef.current.w + startRef.current.x - ev.clientX)))
    }
    const onUp = () => {
      startRef.current = null
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  return { width, onHandleMouseDown }
}

export function ResizeHandle({ onMouseDown }: { onMouseDown: (e: React.MouseEvent) => void }) {
  return (
    <div
      onMouseDown={onMouseDown}
      style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 5, cursor: 'ew-resize', zIndex: 1 }}
      onMouseEnter={e => (e.currentTarget.style.background = 'rgba(128,128,128,0.25)')}
      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
    />
  )
}
