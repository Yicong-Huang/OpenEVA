import { useState, useMemo } from 'react'
import { useLiveClock } from './useLiveClock'

/** Time window in minutes from midnight (local time). */
export interface TimeWindow {
  start: number  // e.g., 630 = 10:30am
  end: number    // e.g., 780 = 1:00pm
}

interface PluginCollapseOptions {
  /**
   * Time window when the plugin should auto-expand.
   * Outside this window it auto-collapses.
   * Omit to never auto-collapse (e.g., PR plugin -- always expanded by default).
   */
  activeWindow?: TimeWindow
  /** Override: force expanded even outside the time window (e.g., active order, alerts). */
  forceExpanded?: boolean
}

interface PluginCollapseResult {
  collapsed: boolean
  toggle: () => void
}

// Shared clock: all plugins re-render together every 60s
const TICK_MS = 60_000

function nowMinutes(): number {
  const now = new Date()
  return now.getHours() * 60 + now.getMinutes()
}

/**
 * Shared hook for plugin collapse/expand behavior.
 *
 * - No `activeWindow` -> default expanded (never auto-collapses).
 * - With `activeWindow` -> auto-expand during the window, auto-collapse outside.
 * - `forceExpanded` overrides time-based collapse (e.g., active UberEats order, lunch alerts).
 * - User can always manually toggle; the override sticks until the component remounts.
 */
export function usePluginCollapse(options: PluginCollapseOptions = {}): PluginCollapseResult {
  const { activeWindow, forceExpanded = false } = options
  // null = user hasn't manually toggled yet, use auto logic
  const [userOverride, setUserOverride] = useState<boolean | null>(null)

  // Shared 60s tick so all plugins re-evaluate time together
  useLiveClock(TICK_MS)

  const collapsed = useMemo(() => {
    // User manually toggled -> respect it
    if (userOverride !== null) return userOverride

    // Force expanded (active order, alerts, etc.)
    if (forceExpanded) return false

    // No active window -> default expanded
    if (!activeWindow) return false

    // Time-window based: expanded inside window, collapsed outside
    const mins = nowMinutes()
    const inWindow = mins >= activeWindow.start && mins < activeWindow.end
    return !inWindow
  }, [userOverride, forceExpanded, activeWindow])

  const toggle = () => setUserOverride((prev) => (prev === null ? !collapsed : !prev))

  return { collapsed, toggle }
}
