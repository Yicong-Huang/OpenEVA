import { useEffect, useRef, useCallback } from 'react'

export function useSSE(url: string | null, onMessage: (data: string) => void) {
  const sourceRef = useRef<EventSource | null>(null)
  const onMessageRef = useRef(onMessage)
  onMessageRef.current = onMessage

  useEffect(() => {
    if (!url) return
    const source = new EventSource(url)
    sourceRef.current = source
    source.onmessage = (evt) => onMessageRef.current(evt.data)
    source.onerror = () => source.close()
    return () => source.close()
  }, [url])

  const close = useCallback(() => {
    sourceRef.current?.close()
    sourceRef.current = null
  }, [])

  return { close }
}
