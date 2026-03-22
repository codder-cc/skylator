import { createFileRoute, Link } from '@tanstack/react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
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
import {
  ChevronLeft,
  Clock,
  Timer,
  Zap,
  Hash,
  Activity,
  XCircle,
  RefreshCw,
} from 'lucide-react'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtSeconds(s: number | null | undefined): string {
  if (s == null || s <= 0) return '—'
  if (s < 60) return `${Math.round(s)}s`
  const m = Math.floor(s / 60)
  const rem = Math.round(s % 60)
  if (m < 60) return rem > 0 ? `${m}m ${rem}s` : `${m}m`
  const h = Math.floor(m / 60)
  const rm = m % 60
  return rm > 0 ? `${h}h ${rm}m` : `${h}h`
}

function MetaCell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs text-text-muted font-medium uppercase tracking-wide mb-1">{label}</div>
      <div className="text-sm text-text-main font-mono">{children}</div>
    </div>
  )
}

// ── Workers table ─────────────────────────────────────────────────────────────

function WorkersTable({ workers }: { workers: WorkerStatus[] }) {
  if (workers.length === 0) return null
  return (
    <div className="card p-4">
      <h3 className="text-xs font-semibold text-text-muted mb-3 uppercase tracking-wide flex items-center gap-2">
        <Activity size={12} />
        Translation Machines
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-text-muted text-xs border-b border-border-subtle">
              <th className="text-left pb-2 font-medium">Machine</th>
              <th className="text-right pb-2 font-medium">Strings done</th>
              <th className="text-right pb-2 pr-4 font-medium">tok/s</th>
              <th className="text-left pb-2 font-medium">Current string</th>
            </tr>
          </thead>
          <tbody>
            {workers.map((w) => (
              <tr key={w.label} className={cn('border-b border-border-subtle/30 last:border-0', !w.alive && 'opacity-40')}>
                <td className="py-2 pr-4 whitespace-nowrap">
                  <span className={cn(
                    'inline-block w-2 h-2 rounded-full mr-2 shrink-0',
                    w.alive ? 'bg-success animate-pulse' : 'bg-text-muted',
                  )} />
                  <span className="font-mono text-xs text-text-main">{w.label}</span>
                </td>
                <td className="py-2 text-right font-mono text-xs tabular-nums text-text-muted pr-2">
                  {w.done}
                </td>
                <td className="py-2 text-right pr-4">
                  <span className={cn(
                    'font-mono text-xs tabular-nums font-semibold',
                    w.tps > 0 ? 'text-accent' : 'text-text-muted/50',
                  )}>
                    {w.tps > 0 ? w.tps.toFixed(1) : '—'}
                  </span>
                </td>
                <td className="py-2 text-xs text-text-muted truncate max-w-xs">
                  {w.current_text || <span className="opacity-40 italic">idle</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Timing + throughput card ──────────────────────────────────────────────────

function TimingCard({ job }: { job: ReturnType<typeof useQuery<Awaited<ReturnType<typeof jobsApi.get>>>>['data'] }) {
  if (!job) return null

  const workers = job.worker_updates ?? []
  const totalDone  = workers.reduce((s, w) => s + (w.done ?? 0), 0)
  const tpsTotal   = workers.reduce((s, w) => s + (w.tps  ?? 0), 0)
  const isRunning  = job.status === 'running'

  const progressCurrent = job.progress?.current ?? totalDone
  const progressTotal   = job.progress?.total   ?? 0

  return (
    <div className="card p-4 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
      {/* Elapsed */}
      <div className="flex items-start gap-2">
        <Clock size={14} className="text-text-muted shrink-0 mt-0.5" />
        <div>
          <div className="text-xs text-text-muted uppercase tracking-wide mb-0.5">Elapsed</div>
          <div className="font-mono text-sm text-text-main tabular-nums">
            {fmtSeconds(job.elapsed)}
          </div>
        </div>
      </div>

      {/* ETA */}
      <div className="flex items-start gap-2">
        <Timer size={14} className={cn('shrink-0 mt-0.5', isRunning ? 'text-accent' : 'text-text-muted')} />
        <div>
          <div className="text-xs text-text-muted uppercase tracking-wide mb-0.5">ETA</div>
          <div className={cn('font-mono text-sm tabular-nums', isRunning ? 'text-accent' : 'text-text-muted')}>
            {isRunning ? fmtSeconds(job.eta_seconds) : '—'}
          </div>
        </div>
      </div>

      {/* Strings done */}
      <div className="flex items-start gap-2">
        <Hash size={14} className="text-text-muted shrink-0 mt-0.5" />
        <div>
          <div className="text-xs text-text-muted uppercase tracking-wide mb-0.5">Strings</div>
          <div className="font-mono text-sm text-text-main tabular-nums">
            {progressTotal > 0
              ? <><span className="text-success">{progressCurrent}</span><span className="text-text-muted/60"> / {progressTotal}</span></>
              : progressCurrent > 0 ? progressCurrent : '—'}
          </div>
        </div>
      </div>

      {/* Live TPS (sum across all workers) */}
      <div className="flex items-start gap-2">
        <Zap size={14} className={cn('shrink-0 mt-0.5', tpsTotal > 0 ? 'text-accent' : 'text-text-muted')} />
        <div>
          <div className="text-xs text-text-muted uppercase tracking-wide mb-0.5">tok/s</div>
          <div className={cn('font-mono text-sm tabular-nums font-semibold', tpsTotal > 0 ? 'text-accent' : 'text-text-muted')}>
            {tpsTotal > 0 ? tpsTotal.toFixed(1) : '—'}
          </div>
        </div>
      </div>

      {/* Throughput prediction: if tps > 0 and eta known, show strings/min */}
      {isRunning && tpsTotal > 0 && progressTotal > progressCurrent && (
        <div className="flex items-start gap-2">
          <Activity size={14} className="text-text-muted shrink-0 mt-0.5" />
          <div>
            <div className="text-xs text-text-muted uppercase tracking-wide mb-0.5">Rate</div>
            <div className="font-mono text-sm text-text-muted tabular-nums">
              {job.eta_seconds && job.eta_seconds > 0
                ? `${Math.round((progressTotal - progressCurrent) / (job.eta_seconds / 60))}/min`
                : '—'}
            </div>
          </div>
        </div>
      )}

      {/* % complete */}
      <div className="flex items-start gap-2">
        <div>
          <div className="text-xs text-text-muted uppercase tracking-wide mb-0.5">Complete</div>
          <div className={cn('font-mono text-sm font-semibold tabular-nums',
            job.pct >= 100 ? 'text-success' : job.pct > 0 ? 'text-accent' : 'text-text-muted')}>
            {job.pct.toFixed(1)}%
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Cancel button ─────────────────────────────────────────────────────────────

function CancelButton({ jobId }: { jobId: string }) {
  const qc = useQueryClient()
  const mut = useMutation({
    mutationFn: () => jobsApi.cancel(jobId),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.job(jobId) }),
  })

  return (
    <button
      onClick={() => mut.mutate()}
      disabled={mut.isPending}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-danger/20 text-danger border border-danger/30 hover:bg-danger/30 disabled:opacity-50 transition-colors"
    >
      {mut.isPending ? <RefreshCw size={11} className="animate-spin" /> : <XCircle size={11} />}
      Cancel
    </button>
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
    return <div className="card p-6 text-text-muted text-center">Loading job…</div>
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Link to="/jobs" className="text-text-muted hover:text-text-main transition-colors">
          <ChevronLeft size={18} />
        </Link>
        <h1 className="text-xl font-bold text-text-main flex-1 min-w-0 truncate">
          {job.name}
        </h1>
        <StatusBadge status={job.status} />
        {job.status === 'running' && <CancelButton jobId={jobId} />}
      </div>

      {/* Meta grid */}
      <div className="card p-4 grid grid-cols-2 sm:grid-cols-4 gap-4">
        <MetaCell label="Type">{job.job_type}</MetaCell>
        <MetaCell label="Created"><TimeAgo ts={job.created_at} /></MetaCell>
        {job.started_at  && <MetaCell label="Started"><TimeAgo ts={job.started_at} /></MetaCell>}
        {job.finished_at && <MetaCell label="Finished"><TimeAgo ts={job.finished_at} /></MetaCell>}
      </div>

      {/* Timing / throughput */}
      <TimingCard job={job} />

      {/* Progress bar */}
      {(job.status === 'running' || (job.pct > 0 && job.pct < 100)) && job.progress && (
        <div className="card p-4">
          <ProgressBar
            pct={job.pct}
            message={job.progress.message}
            subStep={job.progress.sub_step}
          />
        </div>
      )}

      {/* Workers */}
      <WorkersTable workers={job.worker_updates ?? []} />

      {/* Error */}
      {job.error && (
        <div className="card p-4 border border-danger/30 bg-danger/5">
          <div className="text-xs font-semibold text-danger uppercase tracking-wide mb-2">Error</div>
          <div className="font-mono text-sm text-danger whitespace-pre-wrap">{job.error}</div>
        </div>
      )}

      {/* Logs */}
      <div className="card p-4">
        <div className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-2">Logs</div>
        <LogViewer lines={job.log_lines} autoScroll={!isTerminal} />
      </div>
    </div>
  )
}

export const Route = createFileRoute('/jobs/$jobId')({
  component: JobDetailPage,
})
