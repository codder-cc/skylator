import { cn } from '@/lib/utils'
import type { JobStatus, ModStatus } from '@/types'

type Status = JobStatus | ModStatus | string

const STATUS_CONFIG: Record<string, { label: string; className: string }> = {
  // Job statuses
  pending:    { label: 'Pending',    className: 'bg-text-muted/20 text-text-muted border-text-muted/30' },
  running:    { label: 'Running',    className: 'bg-accent/20 text-accent border-accent/30' },
  paused:     { label: 'Paused',     className: 'bg-sky-500/20 text-sky-400 border-sky-500/30' },
  done:       { label: 'Done',       className: 'bg-success/20 text-success border-success/30' },
  failed:     { label: 'Failed',     className: 'bg-danger/20 text-danger border-danger/30' },
  cancelled:          { label: 'Cancelled',  className: 'bg-warning/20 text-warning border-warning/30' },
  offline_dispatched: { label: 'Offline',    className: 'bg-violet-500/20 text-violet-400 border-violet-500/30' },
  // Mod statuses
  unknown:    { label: 'Unknown',    className: 'bg-text-muted/20 text-text-muted border-text-muted/30' },
  no_strings: { label: 'No Strings', className: 'bg-text-muted/10 text-text-muted/60 border-text-muted/20' },
  partial:    { label: 'Partial',    className: 'bg-warning/20 text-warning border-warning/30' },
  // String statuses
  translated: { label: 'Translated', className: 'bg-success/20 text-success border-success/30' },
  review:     { label: 'Review',     className: 'bg-accent2/20 text-accent2 border-accent2/30' },
  ai:         { label: 'AI',         className: 'bg-accent/20 text-accent border-accent/30' },
  dict:       { label: 'Dict',       className: 'bg-success/10 text-success/80 border-success/20' },
  empty:      { label: 'Empty',      className: 'bg-text-muted/10 text-text-muted/60 border-text-muted/20' },
}

interface StatusBadgeProps {
  status: Status
  className?: string
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const config = STATUS_CONFIG[status] ?? {
    label: status,
    className: 'bg-text-muted/20 text-text-muted border-text-muted/30',
  }

  return (
    <span
      className={cn(
        'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border',
        config.className,
        className,
      )}
    >
      {status === 'running' && (
        <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse mr-1.5" />
      )}
      {config.label}
    </span>
  )
}
