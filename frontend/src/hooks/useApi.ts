import { useState, useEffect, useCallback, useRef } from 'react'

interface UseApiResult<T> {
  data: T | null
  loading: boolean
  error: string | null
  refetch: () => void
  mutate: (updater: (prev: T | null) => T | null) => void
}

export function useApi<T>(url: string | null): UseApiResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(!!url)
  const [error, setError] = useState<string | null>(null)
  const lastJsonRef = useRef<string>('')
  const initialFetchDone = useRef(false)

  const fetchData = useCallback(async () => {
    if (!url) return
    // Only show loading spinner on initial fetch, not refreshes
    if (!initialFetchDone.current) setLoading(true)
    try {
      const resp = await fetch(url)
      if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`)
      const json = await resp.text()
      // Only update state if data actually changed (prevents unnecessary re-renders)
      if (json !== lastJsonRef.current) {
        lastJsonRef.current = json
        setData(JSON.parse(json))
      }
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setData(null)
    } finally {
      setLoading(false)
      initialFetchDone.current = true
    }
  }, [url])

  useEffect(() => {
    initialFetchDone.current = false
    lastJsonRef.current = ''
    fetchData()
  }, [fetchData])

  const mutate = useCallback((updater: (prev: T | null) => T | null) => {
    setData(updater)
    lastJsonRef.current = ''  // invalidate cache so next refetch picks up real data
  }, [])

  return { data, loading, error, refetch: fetchData, mutate }
}
