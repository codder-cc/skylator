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
