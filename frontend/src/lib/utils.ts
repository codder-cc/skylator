import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

export function timeAgo(ts: number): string {
  const now = Date.now()
  const diffMs = now - ts * 1000
  const diffSec = Math.floor(diffMs / 1000)

  if (diffSec < 10) return 'just now'
  if (diffSec < 60) return `${diffSec}s ago`

  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`

  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`

  const diffDay = Math.floor(diffHr / 24)
  return `${diffDay}d ago`
}

export function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

export function logLineClass(line: string): string {
  const upper = line.toUpperCase()
  if (upper.includes('ERROR') || upper.includes('CRITICAL')) return 'text-danger'
  if (upper.includes('WARN')) return 'text-warning'
  if (upper.includes('DEBUG')) return 'text-text-muted/50'
  if (upper.includes('INFO')) return 'text-text-muted'
  return 'text-text-main'
}

export function pctColor(pct: number): string {
  if (pct >= 100) return 'text-success'
  if (pct >= 75) return 'text-accent'
  if (pct >= 40) return 'text-warning'
  return 'text-danger'
}
