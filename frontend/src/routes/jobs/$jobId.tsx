import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { QK } from '@/lib/queryKeys'
import { jobsApi } from '@/api/jobs'
import { useJobStream } from '@/hooks/useJobStream'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { ProgressBar } from '@/components/shared/ProgressBar'
import { LogViewer } from '@/components/shared/LogViewer'
import { TimeAgo } from '@/components/shared/TimeAgo'
import { JOB_TERMINAL_STATUSES } from '@/lib/constants'
import { cn } from '@/lib/utils'
import type { WorkerStatus } from '@/types'

// ── Workers table ─────────────────────────────────────────────────────────────

function WorkersTable({ workers }: { workers: WorkerStatus[] }) {
  if (workers.length === 0) return null
  return (
    <div className="card p-4">
      <h3 className="text-sm font-semibold text-text-muted mb-3 uppercase tracking-wide">Translation Machines</h3>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-text-muted text-xs border-b border-border-subtle">
            <th className="text-left pb-2 font-medium">Machine</th>
            <th className="text-right pb-2 font-medium">Done</th>
            <th className="text-right pb-2 font-medium">TPS</th>
            <th className="text-left pb-2 pl-4 font-medium">Current String</th>
          </tr>
        </thead>
        <tbody>
          {workers.map((w) => (
            <tr key={w.label} className={cn('border-b border-border-subtle/30', !w.alive && 'opacity-40')}>
              <td className="py-2 pr-4">
                <span className={cn('inline-block w-2 h-2 rounded-full mr-2', w.alive ? 'bg-success animate-pulse' : 'bg-danger')} />
                <span className="font-mono text-xs text-text-main">{w.label}</span>
              </td>
              <td className="py-2 text-right font-mono text-xs tabular-nums text-text-muted">{w.done}</td>
              <td className="py-2 text-right font-mono text-xs tabular-nums text-accent">{w.tps > 0 ? w.tps.toFixed(1) : '—'}</td>
              <td className="py-2 pl-4 text-xs text-text-muted truncate max-w-xs">{w.current_text || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Job detail page ───────────────────────────────────────────────────────────

function JobDetailPage() {
  const { jobId } = Route.useParams()

  const { data: job } = useQuery({
    queryKey: QK.job(jobId),
    queryFn: () => jobsApi.get(jobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (!status) return 5_000
      return JOB_TERMINAL_STATUSES.includes(status as (typeof JOB_TERMINAL_STATUSES)[number])
        ? false
        : 5_000
    },
  })

  const isTerminal = job
    ? JOB_TERMINAL_STATUSES.includes(job.status as (typeof JOB_TERMINAL_STATUSES)[number])
    : false

  useJobStream(jobId, !isTerminal)

  if (!job) {
    return (
      <div className="card p-6 text-text-muted text-center">Loading job...</div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold text-text-main flex-1 min-w-0 truncate">
          {job.name}
        </h1>
        <StatusBadge status={job.status} />
      </div>

      <div className="card p-4 grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <div>
          <div className="label mb-1">Type</div>
          <div className="text-text-main font-mono">{job.job_type}</div>
        </div>
        <div>
          <div className="label mb-1">Created</div>
          <div className="text-text-main"><TimeAgo ts={job.created_at} /></div>
        </div>
        {job.started_at && (
          <div>
            <div className="label mb-1">Started</div>
            <div className="text-text-main"><TimeAgo ts={job.started_at} /></div>
          </div>
        )}
        {job.finished_at && (
          <div>
            <div className="label mb-1">Finished</div>
            <div className="text-text-main"><TimeAgo ts={job.finished_at} /></div>
          </div>
        )}
      </div>

      {job.status === 'running' && job.progress && (
        <div className="card p-4">
          <ProgressBar
            pct={job.pct}
            message={job.progress.message}
            subStep={job.progress.sub_step}
          />
        </div>
      )}

      <WorkersTable workers={job.worker_updates ?? []} />

      {job.error && (
        <div className="card p-4 border-danger/50 bg-danger/5">
          <div className="label mb-2 text-danger">Error</div>
          <div className="font-mono text-sm text-danger">{job.error}</div>
        </div>
      )}

      <div className="card p-4">
        <div className="label mb-2">Logs</div>
        <LogViewer lines={job.log_lines} autoScroll={!isTerminal} />
      </div>
    </div>
  )
}

export const Route = createFileRoute('/jobs/$jobId')({
  component: JobDetailPage,
})
