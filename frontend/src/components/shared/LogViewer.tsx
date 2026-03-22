import { useEffect, useRef } from 'react'
import { logLineClass } from '@/lib/utils'
import { cn } from '@/lib/utils'

interface LogViewerProps {
  lines: string[]
  maxHeight?: string
  autoScroll?: boolean
  className?: string
}

export function LogViewer({
  lines,
  maxHeight = '400px',
  autoScroll = false,
  className,
}: LogViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const atBottomRef = useRef(true)

  // Track whether user has scrolled away from bottom
  function handleScroll() {
    const el = containerRef.current
    if (!el) return
    const threshold = 60
    atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < threshold
  }

  // Auto-scroll to bottom when new lines arrive
  useEffect(() => {
    if (!autoScroll) return
    const el = containerRef.current
    if (!el) return
    if (atBottomRef.current) {
      el.scrollTop = el.scrollHeight
    }
  }, [lines, autoScroll])

  if (lines.length === 0) {
    return (
      <div
        className={cn(
          'font-mono text-xs text-text-muted/50 p-3 bg-bg-base rounded-md flex items-center justify-center',
          className,
        )}
        style={{ maxHeight, minHeight: 80 }}
      >
        No log output yet.
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className={cn(
        'font-mono text-xs leading-5 bg-bg-base rounded-md p-3 overflow-y-auto',
        className,
      )}
      style={{ maxHeight }}
    >
      {lines.map((line, i) => (
        <div key={i} className={cn('whitespace-pre-wrap break-all', logLineClass(line))}>
          {line}
        </div>
      ))}
    </div>
  )
}
