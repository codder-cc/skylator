import { cn } from '@/lib/utils'
import { SOURCE_COLORS } from '@/lib/constants'

interface SourceBadgeProps {
  source: string | undefined | null
  className?: string
}

const SOURCE_LABELS: Record<string, string> = {
  ai:       'AI',
  cache:    'Cache',
  dict:     'Dict',
  manual:   'Manual',
  imported: 'Import',
}

export function SourceBadge({ source, className }: SourceBadgeProps) {
  if (!source) return null
  const color = SOURCE_COLORS[source] ?? 'text-text-muted/60'
  const label = SOURCE_LABELS[source] ?? source
  return (
    <span
      className={cn('text-[10px] font-mono font-medium uppercase tracking-wide', color, className)}
      title={`Source: ${source}`}
    >
      {label}
    </span>
  )
}
