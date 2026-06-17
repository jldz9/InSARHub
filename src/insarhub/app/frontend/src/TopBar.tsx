import { useState, useEffect, useRef } from 'react'
import type { Theme } from './theme'
import { Icons } from './assets/icons'

interface Props {
  downloaderType:          string
  downloaderOptions:       string[]
  onDownloaderTypeChange:  (type: string) => void
  aoiWkt:                  string | null
  onAoiWktChange:   (wkt: string) => void
  startDate:        string
  endDate:          string
  onDatesChange:    (start: string, end: string) => void
  onSearch:         () => void
  searching:        boolean
  theme:            Theme
  onThemeToggle:    () => void
  onFiltersOpen:    () => void
  hasActiveFilters: boolean
  onJobsOpen:       () => void
  jobsOpen:         boolean
  onSettingsOpen:   () => void
}

export default function TopBar({
  downloaderType, downloaderOptions, onDownloaderTypeChange,
  aoiWkt, onAoiWktChange, startDate, endDate, onDatesChange,
  onSearch, searching,
  theme: t, onThemeToggle, onFiltersOpen, hasActiveFilters, onJobsOpen, jobsOpen, onSettingsOpen,
}: Props) {
  const [wktInput, setWktInput] = useState(aoiWkt ?? '')
  const [eggClicks, setEggClicks]     = useState(0)
  const [unlocked, setUnlocked]       = useState(false)
  const [waveCount, setWaveCount]     = useState(0)
  const NYAN_GIFS = ['/nyan.gif', '/nyan2.gif', '/nyan3.gif', '/nyan4.gif', '/nyan5.gif', '/nyan6.gif']
  const [cats, setCats] = useState<{ id: number; top: number; gif: string }[]>([])
  const catId       = useRef(0)
  const audioRef    = useRef<HTMLAudioElement | null>(null)
  const unlockedAt  = useRef<number>(0)

  function startMusic() {
    if (audioRef.current) return
    const audio = new Audio('/nyan-music.mp3')
    audio.loop   = true
    audio.volume = 0
    audio.play().catch(() => {})
    audioRef.current = audio
    const target = 0.5
    const step   = target / 40          // reach target over 40 ticks
    const timer  = setInterval(() => {
      if (!audioRef.current) { clearInterval(timer); return }
      const next = Math.min(audioRef.current.volume + step, target)
      audioRef.current.volume = next
      if (next >= target) clearInterval(timer)
    }, 80)                              // 40 × 80ms ≈ 3.2s fade-in
  }

  function stopMusic() {
    if (!audioRef.current) return
    audioRef.current.pause()
    audioRef.current.currentTime = 0
    audioRef.current = null
  }

  function spawnWave(count: number) {
    for (let i = 0; i < count; i++) {
      const top   = Math.random() * (window.innerHeight - 80)
      const gif   = NYAN_GIFS[Math.floor(Math.random() * NYAN_GIFS.length)]
      const id    = catId.current++
      const delay = i * 30
      setTimeout(() => {
        setCats(prev => [...prev, { id, top, gif }])
        setTimeout(() => setCats(prev => prev.filter(c => c.id !== id)), 2500)
      }, delay)
    }
  }

  function handleBrandClick() {
    if (unlocked) {
      const next = waveCount + 1
      setWaveCount(next)
      spawnWave(next)
      if (Date.now() - unlockedAt.current > 500) startMusic()
      return
    }
    const next = eggClicks + 1
    if (next >= 7) {
      setEggClicks(0)
      setUnlocked(true)
      setWaveCount(1)
      unlockedAt.current = Date.now()
      spawnWave(1)
    } else {
      setEggClicks(next)
    }
  }

  useEffect(() => { setWktInput(aoiWkt ?? '') }, [aoiWkt])

  useEffect(() => {
    if (unlocked && cats.length === 0) {
      stopMusic()
      setUnlocked(false)
      setWaveCount(0)
      setEggClicks(0)
    }
  }, [cats.length, unlocked])

  function handleWktBlur() {
    if (wktInput.trim()) onAoiWktChange(wktInput.trim())
  }

  const inputStyle: React.CSSProperties = {
    background: t.inputBg, border: `1px solid ${t.inputBorder}`,
    color: t.text, borderRadius: 3, padding: '3px 6px', fontSize: 12,
    colorScheme: t.isDark ? 'dark' : 'light',
  }
  const dividerStyle: React.CSSProperties = {
    width: 1, height: 24, background: t.divider, margin: '0 2px', flexShrink: 0,
  }
  const labelStyle: React.CSSProperties = {
    color: t.textMuted, fontSize: 11, whiteSpace: 'nowrap',
  }

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, zIndex: 150,
      background: t.bg, borderBottom: `1px solid ${t.border}`,
      display: 'flex', alignItems: 'center', gap: 10, padding: '6px 14px',
      height: 48,
    }}>
      <style>{`
        @keyframes insarFringe {
          0%   { color: #7c3aed; text-shadow: 0 0 8px #7c3aed; }
          17%  { color: #ef4444; text-shadow: 0 0 8px #ef4444; }
          34%  { color: #f59e0b; text-shadow: 0 0 8px #f59e0b; }
          50%  { color: #22c55e; text-shadow: 0 0 8px #22c55e; }
          67%  { color: #06b6d4; text-shadow: 0 0 8px #06b6d4; }
          84%  { color: #3b82f6; text-shadow: 0 0 8px #3b82f6; }
          100% { color: #7c3aed; text-shadow: 0 0 8px #7c3aed; }
        }
        .insar-egg { animation: insarFringe 0.6s linear infinite; }
        @keyframes nyanFly {
          0%   { transform: translateX(-60px); }
          100% { transform: translateX(110vw); }
        }
        .nyan-cat { animation: nyanFly 2.8s linear forwards; }
      `}</style>

      {/* Nyan Cat easter egg */}
      {cats.map(cat => (
        <div key={cat.id} className="nyan-cat" style={{
          position: 'fixed', top: cat.top, left: 0,
          zIndex: 9999, pointerEvents: 'none',
          display: 'flex', alignItems: 'center',
        }}>
          <img src={cat.gif} style={{ height: 56, imageRendering: 'pixelated', flexShrink: 0 }} />
        </div>
      ))}

      {/* Brand */}
      <span
        className={cats.length > 0 ? 'insar-egg' : ''}
        onClick={handleBrandClick}
        style={{
          fontWeight: 800, fontSize: 17,
          color: cats.length > 0 ? undefined : t.accent,
          marginRight: 6, whiteSpace: 'nowrap',
          cursor: 'pointer', userSelect: 'none',
        }}
      >
        InSARHub
      </span>

      <div style={dividerStyle} />

      {/* Downloader */}
      <span style={labelStyle}>Downloader</span>
      <select
        value={downloaderType}
        onChange={e => onDownloaderTypeChange(e.target.value)}
        style={{ ...inputStyle, fontFamily: 'monospace', fontSize: 11, cursor: 'pointer',
                 colorScheme: t.isDark ? 'dark' : 'light', width: 90 }}
      >
        {downloaderOptions.map(d => <option key={d} value={d}>{d}</option>)}
      </select>

      <div style={dividerStyle} />

      {/* AOI WKT */}
      <span style={labelStyle}>Area of Interest</span>
      <input
        style={{ ...inputStyle, width: 120, fontFamily: 'monospace', fontSize: 11 }}
        placeholder="Draw or paste WKT…"
        value={wktInput}
        onChange={e => setWktInput(e.target.value)}
        onBlur={handleWktBlur}
        title={wktInput}
      />

      <div style={dividerStyle} />

      {/* Dates — shared with Filters panel */}
      <span style={labelStyle}>Start</span>
      <input type="date" style={{ ...inputStyle, width: 112 }}
        value={startDate}
        onChange={e => onDatesChange(e.target.value, endDate)} />

      <span style={labelStyle}>End</span>
      <input type="date" style={{ ...inputStyle, width: 112 }}
        value={endDate}
        onChange={e => onDatesChange(startDate, e.target.value)} />

      <div style={dividerStyle} />

      {/* Search */}
      <button
        onClick={onSearch}
        disabled={searching}
        style={{
          padding: '5px 18px', background: t.btnActiveBg,
          color: t.isDark ? '#e0f0ff' : t.btnActiveFg,
          border: `1px solid ${t.btnActiveBorder}`, borderRadius: 3,
          fontWeight: 700, fontSize: 13, cursor: 'pointer', letterSpacing: 1,
          whiteSpace: 'nowrap',
        }}
      >
        {searching ? 'Searching…' : 'SEARCH'}
      </button>

      {/* Filters button */}
      <button
        onClick={onFiltersOpen}
        title="Search filters"
        style={{
          padding: '4px 12px',
          background: hasActiveFilters ? t.btnActiveBg : 'transparent',
          color: hasActiveFilters ? (t.isDark ? '#e0f0ff' : t.btnActiveFg) : t.text,
          border: `1px solid ${hasActiveFilters ? t.btnActiveBorder : t.border}`,
          borderRadius: 3, cursor: 'pointer', fontSize: 12,
          display: 'inline-flex', alignItems: 'center', gap: 5, whiteSpace: 'nowrap',
        }}
      >
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
          <path d="M2 4h12M4 8h8M6 12h4" />
        </svg>
        Filters{hasActiveFilters ? ' •' : ''}
      </button>

      {/* Jobs button */}
      <button
        onClick={onJobsOpen}
        title="Job folders"
        style={{
          padding: '4px 12px',
          background: jobsOpen ? t.btnActiveBg : 'transparent',
          color: jobsOpen ? (t.isDark ? '#e0f0ff' : t.btnActiveFg) : t.text,
          border: `1px solid ${jobsOpen ? t.btnActiveBorder : t.border}`,
          borderRadius: 3, cursor: 'pointer', fontSize: 12,
          display: 'inline-flex', alignItems: 'center', gap: 5, whiteSpace: 'nowrap',
        }}
      >
        <svg xmlns="http://www.w3.org/2000/svg" height="14" viewBox="0 -960 960 960" width="14" fill="currentColor">
          <path d="M200-120q-33 0-56.5-23.5T120-200v-560q0-33 23.5-56.5T200-840h168q13-36 43.5-58t68.5-22q38 0 68.5 22t43.5 58h168q33 0 56.5 23.5T840-760v560q0 33-23.5 56.5T760-120H200Zm0-80h560v-560H200v560Zm80-80h280v-80H280v80Zm0-160h400v-80H280v80Zm0-160h400v-80H280v80Zm221.5-198.5Q510-807 510-820t-8.5-21.5Q493-850 480-850t-21.5 8.5Q450-833 450-820t8.5 21.5Q467-790 480-790t21.5-8.5ZM200-200v-560 560Z"/>
        </svg>
        Jobs
      </button>

      {/* Settings — right-aligned */}
      <button
        onClick={onSettingsOpen}
        title="Settings"
        style={{
          marginLeft: 'auto',
          display: 'flex', alignItems: 'center',
          padding: '4px 8px',
          background: 'transparent',
          border: `1px solid ${t.border}`,
          borderRadius: 20,
          cursor: 'pointer',
          color: t.textMuted,
        }}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33
                   1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33
                   l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4
                   h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06
                   A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51
                   a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9
                   a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      </button>

      {/* Theme toggle — icon only */}
      <button
        onClick={onThemeToggle}
        title={t.isDark ? 'Switch to light mode' : 'Switch to dark mode'}
        style={{
          display: 'flex', alignItems: 'center',
          padding: '4px 8px',
          background: 'transparent',
          border: `1px solid ${t.border}`,
          borderRadius: 20,
          cursor: 'pointer',
          color: t.textMuted,
        }}
      >
        {t.isDark
          ? <Icons.Dark  size={16} className="text-yellow-500" />
          : <Icons.Light size={16} className="text-indigo-600" />}
      </button>
    </div>
  )
}