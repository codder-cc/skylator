import { cn } from '@/lib/utils'
import type { ReactNode } from 'react'

interface StatCardProps {
  icon: ReactNode
  value: string | number
  label: string
  color?: string
  accent?: boolean
}

export function StatCard({ icon, value, label, color, accent }: StatCardProps) {
  return (
    <div
      className={cn(
        'card p-4 relative overflow-hidden',
        'border-t-2',
        accent ? 'border-t-accent2' : 'border-t-accent',
      )}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="text-text-muted">{icon}</div>
      </div>
      <div className={cn('text-2xl font-bold tabular-nums', color ?? 'text-text-main')}>
        {value}
      </div>
      <div className="text-xs text-text-muted mt-1 font-medium uppercase tracking-wide">
        {label}
      </div>
    </div>
  )
}
