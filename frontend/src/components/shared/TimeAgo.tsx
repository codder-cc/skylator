import { useState, useEffect } from 'react'
import { timeAgo } from '@/lib/utils'

interface TimeAgoProps {
  ts: number
  className?: string
}

export function TimeAgo({ ts, className }: TimeAgoProps) {
  const [display, setDisplay] = useState(() => timeAgo(ts))

  useEffect(() => {
    setDisplay(timeAgo(ts))
    const interval = setInterval(() => {
      setDisplay(timeAgo(ts))
    }, 10_000)
    return () => clearInterval(interval)
  }, [ts])

  return (
    <time
      dateTime={new Date(ts * 1000).toISOString()}
      title={new Date(ts * 1000).toLocaleString()}
      className={className}
    >
      {display}
    </time>
  )
}
