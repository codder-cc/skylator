import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { QK } from '@/lib/queryKeys'
import { jobsApi } from '@/api/jobs'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { TimeAgo } from '@/components/shared/TimeAgo'
import { ProgressBar } from '@/components/shared/ProgressBar'
import { Skeleton } from '@/components/shared/Skeleton'
import { Link } from '@tanstack/react-router'
import { SkipForward, RefreshCw, ArrowDownToLine } from 'lucide-react'
import { cn } from '@/lib/utils'
import { JOB_ACTIVE_STATUSES } from '@/lib/constants'

function InlineDispatchBackButton({ jobId, onClick }: { jobId: string; onClick: (e: React.MouseEvent) => void }) {
  const qc  = useQueryClient()
  const mut = useMutation({
    mutationFn: () => jobsApi.dispatchBack(jobId),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.jobs() }),
  })
  return (
    <button
      onClick={(e) => { onClick(e); mut.mutate() }}
      disabled={mut.isPending}
      className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium bg-violet-500/20 text-violet-400 border border-violet-500/30 hover:bg-violet-500/30 disabled:opacity-50 transition-colors shrink-0"
    >
      {mut.isPending ? <RefreshCw size={9} className="animate-spin" /> : <ArrowDownToLine size={9} />}
      Dispatch Back
    </button>
  )
}

function InlineResumeButton({ jobId, onClick }: { jobId: string; onClick: (e: React.MouseEvent) => void }) {
  const navigate   = useNavigate()
  const qc         = useQueryClient()
  const mut = useMutation({
    mutationFn: () => jobsApi.resume(jobId),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: QK.jobs() })
      if (data.ok && data.job_id) {
        navigate({ to: '/jobs/$jobId', params: { jobId: data.job_id } })
      }
    },
  })
  return (
    <button
      onClick={(e) => { onClick(e); mut.mutate() }}
      disabled={mut.isPending}
      className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 disabled:opacity-50 transition-colors shrink-0"
    >
      {mut.isPending ? <RefreshCw size={9} className="animate-spin" /> : <SkipForward size={9} />}
      Resume
    </button>
  )
}

function JobsPage() {
  const qc = useQueryClient()

  // Force fresh fetch on every visit so inline jobs (translate-one) appear immediately
  useEffect(() => {
    qc.invalidateQueries({ queryKey: QK.jobs() })
  }, [qc])

  const { data: jobs = [], isLoading } = useQuery({
    queryKey: QK.jobs(),
    queryFn: jobsApi.list,
    refetchInterval: (query) => {
      const data = query.state.data ?? []
      const hasActive = data.some((j) => (JOB_ACTIVE_STATUSES as readonly string[]).includes(j.status))
      return hasActive ? 2_000 : 5_000
    },
    refetchOnMount: 'always',
  })

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-text-main">Jobs</h1>

      {isLoading && (
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="card p-4 animate-pulse flex items-center gap-4">
              <div className="flex-1 space-y-2">
                <div className="flex items-center gap-2">
                  <Skeleton className="h-4 w-48" />
                  <Skeleton className="h-5 w-16 rounded-full" />
                </div>
              </div>
              <Skeleton className="h-3 w-16" />
            </div>
          ))}
        </div>
      )}

      {!isLoading && jobs.length === 0 && (
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
                {/* Assigned worker chips */}
                {(job.assigned_machines ?? []).length > 0 && (
                  <div className="flex items-center gap-1 flex-wrap">
                    {(job.assigned_machines ?? []).map(m => (
                      <span key={m} className="text-[10px] px-1.5 py-0.5 rounded font-mono bg-accent/10 text-accent/70 border border-accent/20">
                        {m}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              {job.status === 'running' && job.progress && (
                <ProgressBar
                  pct={job.pct}
                  message={job.progress.message}
                />
              )}
              {job.status === 'paused' && (
                <div className="text-xs text-sky-400/70 mt-0.5">
                  {job.error || 'Paused'}
                </div>
              )}
            </div>
            <div className="flex items-center gap-2">
              {job.status === 'paused' && job.job_type.includes('translate') && (
                <InlineResumeButton jobId={job.id} onClick={(e) => e.preventDefault()} />
              )}
              {job.status === 'offline_dispatched' && (
                <InlineDispatchBackButton jobId={job.id} onClick={(e) => e.preventDefault()} />
              )}
              <div className="text-xs text-text-muted whitespace-nowrap">
                <TimeAgo ts={job.created_at} />
              </div>
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
