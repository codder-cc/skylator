import { useEffect, useRef, useState } from 'react'

const BACKOFF_BASE  = 3_000   // 3 s initial delay
const BACKOFF_MAX   = 60_000  // 60 s ceiling

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
    let attempt = 0

    function connect() {
      if (destroyed) return
      es = new EventSource(url)

      es.onopen = () => {
        if (!destroyed) {
          setConnected(true)
          attempt = 0  // reset backoff on successful connection
        }
      }

      es.onmessage = (event: MessageEvent<string>) => {
        const data: string = event.data
        if (!data || data.startsWith(': ping')) return
        onMessageRef.current(data)
      }

      es.onerror = () => {
        if (destroyed) return
        setConnected(false)
        es?.close()
        es = null
        // Exponential backoff: 3s, 6s, 12s, 24s, 48s, then cap at 60s
        const delay = Math.min(BACKOFF_BASE * Math.pow(2, attempt), BACKOFF_MAX)
        attempt++
        reconnectTimer = setTimeout(connect, delay)
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
