import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { QK } from '@/lib/queryKeys'
import { jobsApi } from '@/api/jobs'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { TimeAgo } from '@/components/shared/TimeAgo'
import { ProgressBar } from '@/components/shared/ProgressBar'
import { Link } from '@tanstack/react-router'

function JobsPage() {
  const { data: jobs = [] } = useQuery({
    queryKey: QK.jobs(),
    queryFn: jobsApi.list,
    refetchInterval: 5_000,
  })

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-text-main">Jobs</h1>

      {jobs.length === 0 && (
        <div className="card p-6 text-text-muted text-center">No jobs yet.</div>
      )}

      <div className="space-y-2">
        {jobs.map((job) => (
          <Link
            key={job.id}
            to="/jobs/$jobId"
            params={{ jobId: job.id }}
            className="card p-4 flex items-center gap-4 hover:bg-bg-card2 transition-colors no-underline block"
          >
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-sm font-medium text-text-main truncate">{job.name}</span>
                <StatusBadge status={job.status} />
              </div>
              {job.status === 'running' && job.progress && (
                <ProgressBar
                  pct={job.pct}
                  message={job.progress.message}
                />
              )}
            </div>
            <div className="text-xs text-text-muted whitespace-nowrap">
              <TimeAgo ts={job.created_at} />
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}

export const Route = createFileRoute('/jobs/')({
  component: JobsPage,
})
