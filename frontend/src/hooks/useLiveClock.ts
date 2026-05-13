import { useState, useEffect } from 'react'

/**
 * Forces a re-render at the given interval so that timeAgo values stay fresh.
 * Call at the top of any component that displays relative timestamps.
 */
export function useLiveClock(intervalMs = 1000) {
  const [, setTick] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => setTick(t => t + 1), intervalMs)
    return () => clearInterval(timer)
  }, [intervalMs])
}
