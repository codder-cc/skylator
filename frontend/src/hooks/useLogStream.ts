import { useState, useCallback } from 'react'
import { useSSE } from './useSSE'

const MAX_LINES = 2000

export function useLogStream(
  url: string,
  enabled = true,
): { lines: string[]; connected: boolean } {
  const [lines, setLines] = useState<string[]>([])

  const onMessage = useCallback((data: string) => {
    setLines((prev) => {
      const next = [...prev, data]
      if (next.length > MAX_LINES) {
        return next.slice(next.length - MAX_LINES)
      }
      return next
    })
  }, [])

  const { connected } = useSSE(url, onMessage, enabled)

  return { lines, connected }
}
