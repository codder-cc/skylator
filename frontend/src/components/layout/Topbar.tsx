import { useRouterState } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { QK } from '@/lib/queryKeys'
import { jobsApi } from '@/api/jobs'
import { statsApi } from '@/api/stats'
import { Cpu } from 'lucide-react'
import { cn } from '@/lib/utils'

function getPageTitle(pathname: string): string {
  if (pathname === '/') return 'Dashboard'
  if (pathname.startsWith('/mods')) {
    const parts = pathname.split('/').filter(Boolean)
    if (parts.length === 1) return 'Mods'
    if (parts.length === 2) return `Mod: ${decodeURIComponent(parts[1])}`
    if (parts[2] === 'strings') return `Strings — ${decodeURIComponent(parts[1])}`
    return 'Mods'
  }
  if (pathname.startsWith('/jobs')) {
    const parts = pathname.split('/').filter(Boolean)
    if (parts.length === 1) return 'Jobs'
    return `Job: ${parts[1]}`
  }
  const titles: Record<string, string> = {
    '/servers':     'Servers',
    '/config':      'Config',
    '/terminology': 'Terminology',
    '/backups':     'Backups',
    '/tools':       'Tools',
    '/logs':        'Logs',
  }
  return titles[pathname] ?? pathname
}

export function Topbar() {
  const routerState = useRouterState()
  const pathname = routerState.location.pathname
  const title = getPageTitle(pathname)

  const { data: jobs = [] } = useQuery({
    queryKey: QK.jobs(),
    queryFn: jobsApi.list,
    refetchInterval: 5_000,
  })

  const { data: gpu } = useQuery({
    queryKey: QK.gpu(),
    queryFn: statsApi.gpu,
    refetchInterval: 5_000,
  })

  const runningJobs = jobs.filter((j) => j.status === 'running')

  return (
    <header className="flex items-center gap-4 px-6 py-3 border-b border-border-default bg-bg-sidebar">
      {/* Page title */}
      <h2 className="flex-1 text-base font-semibold text-text-main truncate">{title}</h2>

      {/* Running jobs badge */}
      {runningJobs.length > 0 && (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-danger/20 text-danger border border-danger/30">
          <span className="w-1.5 h-1.5 rounded-full bg-danger animate-pulse" />
          {runningJobs.length} running
        </span>
      )}

      {/* GPU indicator */}
      {gpu?.available && (
        <div className="flex items-center gap-2 text-xs text-text-muted">
          <Cpu className="w-3.5 h-3.5" />
          <div className="flex items-center gap-1.5">
            <div className="w-20 h-1.5 bg-bg-card2 rounded-full overflow-hidden">
              <div
                className={cn(
                  'h-full rounded-full transition-all',
                  gpu.pct > 90 ? 'bg-danger' : gpu.pct > 70 ? 'bg-warning' : 'bg-accent',
                )}
                style={{ width: `${gpu.pct}%` }}
              />
            </div>
            <span
              className={cn(
                'font-mono tabular-nums',
                gpu.pct > 90 ? 'text-danger' : gpu.pct > 70 ? 'text-warning' : 'text-text-muted',
              )}
            >
              {gpu.pct.toFixed(0)}%
            </span>
          </div>
        </div>
      )}
    </header>
  )
}
