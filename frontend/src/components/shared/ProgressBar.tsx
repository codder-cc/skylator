import { cn } from '@/lib/utils'

interface ProgressBarProps {
  pct: number
  message?: string
  subStep?: string
  className?: string
}

export function ProgressBar({ pct, message, subStep, className }: ProgressBarProps) {
  const clamped = Math.max(0, Math.min(100, pct))

  const barColor =
    clamped >= 100
      ? 'bg-success'
      : clamped >= 75
      ? 'bg-accent'
      : clamped >= 40
      ? 'bg-warning'
      : 'bg-accent'

  return (
    <div className={cn('space-y-1.5', className)}>
      {(message || subStep) && (
        <div className="flex items-center justify-between text-xs">
          <div className="min-w-0 flex-1">
            {message && (
              <span className="text-text-muted truncate block">{message}</span>
            )}
            {subStep && (
              <span className="text-text-muted/60 truncate block text-[11px]">{subStep}</span>
            )}
          </div>
          <span
            className={cn(
              'ml-3 font-mono tabular-nums font-medium shrink-0',
              clamped >= 100 ? 'text-success' : clamped >= 40 ? 'text-accent' : 'text-warning',
            )}
          >
            {clamped.toFixed(1)}%
          </span>
        </div>
      )}
      <div className="h-2 bg-bg-card2 rounded-full overflow-hidden">
        <div
          className={cn('h-full rounded-full transition-all duration-300', barColor)}
          style={{ width: `${clamped}%` }}
        />
      </div>
    </div>
  )
}
