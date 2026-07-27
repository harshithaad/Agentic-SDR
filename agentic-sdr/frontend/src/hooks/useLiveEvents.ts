import { useEffect, useRef, useState } from 'react'

/**
 * Subscribes to the backend's SSE stream of lead transitions (/api/events).
 * Every event bumps `tick`; components refetch on tick change, so the UI
 * reacts in real time while HTTP polling is demoted to a slow fallback.
 */
export default function useLiveEvents(): { tick: number; connected: boolean } {
  const [tick, setTick] = useState(0)
  const [connected, setConnected] = useState(false)
  const sourceRef = useRef<EventSource | null>(null)

  useEffect(() => {
    let retryTimer: ReturnType<typeof setTimeout> | null = null

    const connect = () => {
      const source = new EventSource('/api/events')
      sourceRef.current = source
      source.onopen = () => setConnected(true)
      source.addEventListener('lead', () => setTick((t) => t + 1))
      source.onerror = () => {
        setConnected(false)
        source.close()
        retryTimer = setTimeout(connect, 5000)
      }
    }
    connect()

    return () => {
      if (retryTimer) clearTimeout(retryTimer)
      sourceRef.current?.close()
    }
  }, [])

  return { tick, connected }
}
