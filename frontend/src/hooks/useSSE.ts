import { useEffect, useRef, useState } from 'react'
import { SSE_RECONNECT_DELAY } from '@/lib/constants'

export function useSSE(
  url: string,
  onMessage: (data: string) => void,
  enabled = true,
): { connected: boolean } {
  const [connected, setConnected] = useState(false)
  const onMessageRef = useRef(onMessage)
  onMessageRef.current = onMessage

  useEffect(() => {
    if (!enabled) {
      setConnected(false)
      return
    }

    let es: EventSource | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let destroyed = false

    function connect() {
      if (destroyed) return
      es = new EventSource(url)

      es.onopen = () => {
        if (!destroyed) setConnected(true)
      }

      es.onmessage = (event: MessageEvent<string>) => {
        const data: string = event.data
        // Skip heartbeat/comment-only lines
        if (!data || data.startsWith(': ping')) return
        onMessageRef.current(data)
      }

      es.onerror = () => {
        if (destroyed) return
        setConnected(false)
        es?.close()
        es = null
        reconnectTimer = setTimeout(connect, SSE_RECONNECT_DELAY)
      }
    }

    connect()

    return () => {
      destroyed = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      es?.close()
      setConnected(false)
    }
  }, [url, enabled])

  return { connected }
}
