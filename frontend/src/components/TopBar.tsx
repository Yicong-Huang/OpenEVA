import { useState, useRef, useEffect } from 'react'
import { AuthStatus } from './status/AuthStatus'
import { SessionIndicator } from './status/SessionIndicator'
import { AIUsageStatus } from './status/AIUsageStatus'
import { EventStatus } from './status/EventStatus'
import { GlobalSearch } from './GlobalSearch'
import { SettingsModal } from './SettingsModal'
import { useClickOutside } from '../hooks/useClickOutside'
import { useTheme } from '../hooks/useTheme'

interface TopBarProps {
  unreadEventCount?: number
  onEventsOpened?: () => void
  onEventNavigate?: (event: import('../types').EvaEvent) => void
  onNavigate?: (projectId: string | null, view: string) => void
  onSelectTask?: (taskId: string | null) => void
  onSelectPR?: (pr: { repo: string; number: number; taskId?: string; projectId?: string } | null) => void
  onSelectReview?: (url: string | null) => void
  onSelectTicket?: (ticket: { key: string; instance?: string } | null) => void
}

function MenuDropdown() {
  const [open, setOpen] = useState(false)
  const [showSettings, setShowSettings] = useState<false | 'setup' | 'repos' | 'themes' | 'plugins' | 'intervals'>(false)
  const ref = useRef<HTMLDivElement>(null)
  useClickOutside(ref, () => setOpen(false))
  const { theme, toggle: toggleTheme } = useTheme()

  // SetupBanner (and other components) can request opening Settings at
  // a specific tab via `eva-open-settings` CustomEvent.
  useEffect(() => {
    const onOpen = (e: Event) => {
      const detail = (e as CustomEvent).detail || {}
      const tab = detail.tab && typeof detail.tab === 'string' ? detail.tab : 'setup'
      setShowSettings(tab)
    }
    window.addEventListener('eva-open-settings', onOpen)
    return () => window.removeEventListener('eva-open-settings', onOpen)
  }, [])

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        className="menu-btn"
        onClick={() => setOpen(!open)}
        title="Menu"
        data-testid="menu-btn"
      >
        &#9776;
      </button>
      {open && (
        <div
          className="menu-dropdown open"
          style={{
            display: 'block',
            position: 'absolute',
            top: 32,
            right: 0,
          }}
        >
          <div
            className="menu-item"
            data-testid="theme-toggle"
            onClick={() => { toggleTheme(); setOpen(false) }}
          >
            {theme === 'dark' ? 'Light Mode' : 'Dark Mode'}
          </div>
          <div
            className="menu-item"
            data-testid="menu-settings"
            onClick={() => { setShowSettings('repos'); setOpen(false) }}
          >
            Settings
          </div>
          <div className="menu-item" onClick={() => setOpen(false)}>
            Help
          </div>
        </div>
      )}
      {showSettings && (
        <SettingsModal
          initialTab={showSettings}
          onClose={() => setShowSettings(false)}
        />
      )}
    </div>
  )
}

const Separator = () => (
  <span style={{ width: 1, height: 18, background: 'var(--border)', flexShrink: 0 }} />
)

export function TopBar({
  unreadEventCount,
  onEventsOpened,
  onEventNavigate,
  onNavigate,
  onSelectTask,
  onSelectPR,
  onSelectReview,
  onSelectTicket,
}: TopBarProps) {
  return (
    <div className="top-bar" data-testid="top-bar">
      <span className="top-bar-title" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <img src="/favicon.svg" width={18} height={18} alt="" style={{ borderRadius: 3 }} />
        OpenEVA
      </span>
      {onNavigate && (
        <div style={{ flex: 1, display: 'flex', justifyContent: 'center' }}>
          <GlobalSearch
            onNavigate={onNavigate}
            onSelectTask={onSelectTask}
            onSelectPR={onSelectPR}
            onSelectReview={onSelectReview}
            onSelectTicket={onSelectTicket}
          />
        </div>
      )}
      <div className="top-bar-right">
        <SessionIndicator />
        <Separator />
        <AuthStatus />
        <Separator />
        <AIUsageStatus />
        <Separator />
        <EventStatus sseUnread={unreadEventCount} onOpened={onEventsOpened} onNavigate={onEventNavigate} />
        <Separator />
        <MenuDropdown />
      </div>
    </div>
  )
}
