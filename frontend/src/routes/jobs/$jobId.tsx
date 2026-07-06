import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { QK } from '@/lib/queryKeys'
import { jobsApi } from '@/api/jobs'
import { workersApi } from '@/api/workers'
import { useJobStream } from '@/hooks/useJobStream'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { SourceBadge } from '@/components/shared/SourceBadge'
import { ProgressBar } from '@/components/shared/ProgressBar'
import { LogViewer } from '@/components/shared/LogViewer'
import { TimeAgo } from '@/components/shared/TimeAgo'
import { Breadcrumbs } from '@/components/shared/Breadcrumbs'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import {
  PauseButton, CancelButton, ResumeButton, DispatchBackButton,
  DispatchOfflineButton, RetryButton, CollectButton,
} from '@/components/jobs/JobActions'
import { JOB_TERMINAL_STATUSES } from '@/lib/constants'
import { cn } from '@/lib/utils'
import type { Job, WorkerStatus, StringUpdate } from '@/types'
import {
  Clock,
  Timer,
  Zap,
  Hash,
  Activity,
  XCircle,
  RefreshCw,
  SkipForward,
  RotateCcw,
  Pause,
  Plus,
  X,
  ArrowDownToLine,
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

function WorkersTable({ workers, updates }: { workers: WorkerStatus[]; updates: StringUpdate[] }) {
  if (workers.length === 0) return null

  // Avg quality score per worker from string_updates
  const workerScores: Record<string, { sum: number; count: number }> = {}
  for (const u of updates) {
    if (u.machine_label && u.quality_score != null) {
      const w = workerScores[u.machine_label] ?? { sum: 0, count: 0 }
      w.sum += u.quality_score
      w.count++
      workerScores[u.machine_label] = w
    }
  }

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
              <th className="text-right pb-2 font-medium">Avg score</th>
              <th className="text-right pb-2 pr-4 font-medium">tok/s</th>
              <th className="text-left pb-2 font-medium">Current string</th>
            </tr>
          </thead>
          <tbody>
            {workers.map((w) => {
              const sc = workerScores[w.label]
              const avgScore = sc && sc.count > 0 ? Math.round(sc.sum / sc.count) : null
              return (
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
                  <td className="py-2 text-right pr-2">
                    {avgScore != null ? (
                      <span className={cn(
                        'font-mono text-xs tabular-nums font-semibold',
                        avgScore >= 80 ? 'text-success' : avgScore >= 50 ? 'text-warning' : 'text-danger',
                      )}>
                        {avgScore}
                      </span>
                    ) : (
                      <span className="text-text-muted/40 text-xs">—</span>
                    )}
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
              )
            })}
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
  const isRunning  = job.status === 'running'

  // Live tok/s while running (sum across workers); settled avg after completion
  const liveTps    = workers.reduce((s, w) => s + (w.tps ?? 0), 0)
  const tpsDisplay = isRunning ? liveTps : (job.tps_avg ?? 0)

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

      {/* tok/s — live during run, avg after completion */}
      <div className="flex items-start gap-2">
        <Zap size={14} className={cn('shrink-0 mt-0.5', tpsDisplay > 0 ? 'text-accent' : 'text-text-muted')} />
        <div>
          <div className="text-xs text-text-muted uppercase tracking-wide mb-0.5">
            {isRunning ? 'tok/s' : 'tok/s avg'}
          </div>
          <div className={cn('font-mono text-sm tabular-nums font-semibold', tpsDisplay > 0 ? 'text-accent' : 'text-text-muted')}>
            {tpsDisplay > 0 ? tpsDisplay.toFixed(1) : '—'}
          </div>
        </div>
      </div>

      {/* Tokens generated (shown when available) */}
      <div className="flex items-start gap-2">
        <Activity size={14} className="text-text-muted shrink-0 mt-0.5" />
        <div>
          <div className="text-xs text-text-muted uppercase tracking-wide mb-0.5">
            {isRunning ? 'Rate' : 'Tokens'}
          </div>
          <div className="font-mono text-sm text-text-muted tabular-nums">
            {isRunning
              ? (liveTps > 0 && job.eta_seconds && job.eta_seconds > 0 && progressTotal > progressCurrent
                  ? `${Math.round((progressTotal - progressCurrent) / (job.eta_seconds / 60))}/min`
                  : '—')
              : ((job.tokens_generated ?? 0) > 0
                  ? `${((job.tokens_generated ?? 0) / 1000).toFixed(1)}k`
                  : '—')}
          </div>
        </div>
      </div>

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

// ── String updates panel ──────────────────────────────────────────────────────

const SOURCE_PILL: Record<string, string> = {
  ai:             'bg-violet-500/15 text-violet-300',
  cache:          'bg-sky-500/15 text-sky-300',
  dict:           'bg-teal-500/15 text-teal-300',
  manual:         'bg-amber-500/15 text-amber-300',
  untranslatable: 'bg-slate-500/15 text-slate-300',
}

function SourceBreakdown({ updates }: { updates: StringUpdate[] }) {
  const counts: Record<string, number> = {}
  for (const u of updates) {
    const s = u.source ?? 'ai'
    counts[s] = (counts[s] ?? 0) + 1
  }
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {Object.entries(counts).map(([src, n]) => (
        <span key={src} className={cn('text-[10px] px-1.5 py-0.5 rounded font-medium', SOURCE_PILL[src] ?? 'bg-bg-card2 text-text-muted')}>
          {n} {src}
        </span>
      ))}
    </div>
  )
}

function StringUpdatesPanel({ updates }: { updates: StringUpdate[] }) {
  if (updates.length === 0) return null
  // Show most recent 50
  const recent = updates.slice(-50).reverse()
  return (
    <div className="card p-4">
      <div className="flex items-center gap-3 mb-3 flex-wrap">
        <h3 className="text-xs font-semibold text-text-muted uppercase tracking-wide flex items-center gap-2">
          <Hash size={12} />
          Recent Translations ({updates.length} total)
        </h3>
        <SourceBreakdown updates={updates} />
      </div>
      <div className="overflow-x-auto max-h-64 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-bg-card">
            <tr className="text-text-muted/60 uppercase text-[10px] tracking-wide border-b border-border-subtle">
              <th className="text-left px-2 py-1.5">Key</th>
              <th className="text-left px-2 py-1.5">Source</th>
              <th className="text-left px-2 py-1.5">Machine</th>
              <th className="text-left px-2 py-1.5">Translation</th>
            </tr>
          </thead>
          <tbody>
            {recent.map((u, i) => (
              <tr key={i} className="border-t border-border-subtle/30 hover:bg-bg-card2/20">
                <td className="px-2 py-1.5 font-mono text-text-muted/70 max-w-[140px] truncate" title={u.key}>
                  {u.key.split(':').slice(-1)[0] ?? u.key}
                </td>
                <td className="px-2 py-1.5">
                  <SourceBadge source={u.source} />
                </td>
                <td className="px-2 py-1.5 text-text-muted/60 font-mono whitespace-nowrap">
                  {u.machine_label ?? '—'}
                </td>
                <td className="px-2 py-1.5 text-text-main max-w-[300px] truncate" title={u.translation}>
                  {u.translation || <span className="italic text-text-muted/40">empty</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Worker assignment panel ───────────────────────────────────────────────────

function WorkerAssignmentPanel({ job }: { job: Job }) {
  const isTranslate = job.job_type.includes('translate')
  if (!isTranslate) return null

  const qc       = useQueryClient()
  const navigate = useNavigate()
  const { data: workers = [] } = useQuery({ queryKey: QK.workers(), queryFn: workersApi.list })

  const assigned   = (job.assigned_machines ?? []) as string[]
  const unassigned = workers.filter(w => w.alive && !assigned.includes(w.label)).map(w => w.label)
  const canEdit    = job.status === 'paused' || job.status === 'running'

  const assignMut = useMutation({
    mutationFn: (label: string) => jobsApi.assign(job.id, [label]),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: QK.jobs() })
      qc.invalidateQueries({ queryKey: QK.job(job.id) })
      if (data.resumed && data.job_id) {
        navigate({ to: '/jobs/$jobId', params: { jobId: data.job_id! } })
      }
    },
  })

  const unassignMut = useMutation({
    mutationFn: (label: string) => jobsApi.unassign(job.id, [label]),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.jobs() })
      qc.invalidateQueries({ queryKey: QK.job(job.id) })
    },
  })

  return (
    <div className="card p-4">
      <h3 className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-3 flex items-center gap-2">
        <Activity size={12} />
        Assigned Workers
      </h3>
      {assigned.length === 0 && job.status === 'paused' && (
        <p className="text-xs text-warning mb-3">No workers assigned — assign a worker to resume automatically.</p>
      )}
      <div className="flex flex-wrap gap-2">
        {assigned.map(label => (
          <span key={label} className="inline-flex items-center gap-1 px-2 py-1 rounded bg-accent/15 text-accent text-xs font-mono border border-accent/30">
            {label}
            {canEdit && (
              <button
                onClick={() => unassignMut.mutate(label)}
                disabled={unassignMut.isPending}
                className="ml-0.5 hover:text-danger transition-colors"
                title="Unassign"
              >
                <X size={10} />
              </button>
            )}
          </span>
        ))}
        {canEdit && unassigned.map(label => (
          <button
            key={label}
            onClick={() => assignMut.mutate(label)}
            disabled={assignMut.isPending}
            className="inline-flex items-center gap-1 px-2 py-1 rounded bg-bg-card2 text-text-muted text-xs font-mono border border-border-subtle hover:border-accent/40 hover:text-accent transition-colors"
            title="Assign"
          >
            <Plus size={10} />
            {label}
          </button>
        ))}
        {!canEdit && assigned.length === 0 && (
          <span className="text-xs text-text-muted/50 italic">none</span>
        )}
      </div>
    </div>
  )
}

// ── Cancel / Resume buttons ───────────────────────────────────────────────────

// Live funnel — assigned → delivered → translated → pending. Derived from durable
// assignments on the host, so it stays accurate across master restarts and detach/reattach.
function TallyCard({ jobId, live }: { jobId: string; live: boolean }) {
  const { data: t } = useQuery({
    queryKey: QK.jobTally(jobId),
    queryFn: () => jobsApi.tally(jobId),
    refetchInterval: live ? 5_000 : false,
  })
  if (!t || (t.assigned === 0 && t.translated === 0 && t.pending === 0)) return null

  const cells: Array<[string, number, string]> = [
    ['Assigned', t.assigned, 'text-text-main'],
    ['Delivered', t.delivered, 'text-accent'],
    ['Translated', t.translated, 'text-success'],
    ['Pending', t.pending, 'text-warning'],
    ['Needs review', t.needs_review, 'text-warning'],
  ]
  return (
    <div className="card p-4">
      <div className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-3">
        Progress funnel
      </div>
      <div className="grid grid-cols-3 sm:grid-cols-5 gap-4">
        {cells.map(([label, val, color]) => (
          <div key={label}>
            <div className={cn('text-2xl font-bold font-mono', color)}>{val}</div>
            <div className="text-xs text-text-muted">{label}</div>
          </div>
        ))}
      </div>
      <TallySourceBreakdown counts={t.source_counts} total={t.translated} />
    </div>
  )
}

// UID2/UID4 — where the delivered translations came from. Makes "chained" reuse visible:
// fresh AI inference vs cache / cross-mod dispatch reuse / consensus / dictionary.
const TALLY_SOURCE_META: Record<string, { label: string; color: string }> = {
  ai:              { label: 'AI inference', color: 'bg-accent' },
  cache:           { label: 'Cache reuse',  color: 'bg-violet-500' },
  dispatch_cache:  { label: 'Dispatch cache', color: 'bg-violet-400' },
  dispatch_shared: { label: 'Cross-mod reuse', color: 'bg-sky-500' },
  consensus:       { label: 'Consensus',    color: 'bg-emerald-500' },
  dict:            { label: 'Dictionary',   color: 'bg-amber-500' },
  manual:          { label: 'Manual',       color: 'bg-pink-500' },
}
function TallySourceBreakdown({ counts, total }: { counts?: Record<string, number>; total: number }) {
  if (!counts) return null
  const entries = Object.entries(counts).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1])
  if (entries.length === 0) return null
  const sum = entries.reduce((s, [, n]) => s + n, 0) || 1
  return (
    <div className="mt-4 pt-3 border-t border-border-subtle">
      <div className="text-[11px] font-semibold text-text-muted uppercase tracking-wide mb-2">
        Translation source {total > 0 && <span className="font-normal normal-case">· reuse {Math.round((sum - (counts.ai ?? 0)) / sum * 100)}%</span>}
      </div>
      <div className="flex h-2 rounded overflow-hidden bg-bg-base mb-2">
        {entries.map(([k, n]) => (
          <div key={k} className={cn('h-full', TALLY_SOURCE_META[k]?.color ?? 'bg-text-muted')}
               style={{ width: `${(n / sum) * 100}%` }} title={`${TALLY_SOURCE_META[k]?.label ?? k}: ${n}`} />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
        {entries.map(([k, n]) => (
          <span key={k} className="flex items-center gap-1.5 text-text-muted">
            <span className={cn('w-2 h-2 rounded-sm', TALLY_SOURCE_META[k]?.color ?? 'bg-text-muted')} />
            {TALLY_SOURCE_META[k]?.label ?? k} <span className="font-mono text-text-main">{n}</span>
          </span>
        ))}
      </div>
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
    return <div className="card p-6 text-text-muted text-center">Loading job…</div>
  }

  return (
    <div className="space-y-4">
      {/* Breadcrumbs */}
      <Breadcrumbs items={[
        { label: 'Jobs', to: '/jobs' },
        { label: job.name },
      ]} />

      {/* Header */}
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-bold text-text-main flex-1 min-w-0 truncate">
          {job.name}
        </h1>
        <StatusBadge status={job.status} />
        {job.status === 'running' && job.job_type.includes('translate') &&
          (job.assigned_machines?.length ?? 0) > 0 && (
          <DispatchOfflineButton jobId={jobId} />
        )}
        {job.status === 'running' && <PauseButton jobId={jobId} />}
        {job.status === 'running' && <CancelButton jobId={jobId} />}
        {job.status === 'paused' && (
          <>
            <ResumeButton jobId={jobId} />
            <CancelButton jobId={jobId} />
          </>
        )}
        {job.status === 'offline_dispatched' && (
          <>
            <CollectButton jobId={jobId} />
            <DispatchBackButton jobId={jobId} />
            <CancelButton jobId={jobId} />
          </>
        )}
        {(job.status === 'failed' || job.status === 'cancelled' || job.status === 'paused')
          && job.job_type.includes('translate') && (
          <CollectButton jobId={jobId} />
        )}
        {(job.status === 'failed' || job.status === 'cancelled') && (
          <>
            <RetryButton jobId={jobId} />
            {job.job_type.includes('translate') && <ResumeButton jobId={jobId} />}
          </>
        )}
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

      {/* Progress funnel (assigned → delivered → translated → pending) */}
      {job.job_type.includes('translate') && <TallyCard jobId={jobId} live={!isTerminal} />}

      {/* Offline dispatched banner */}
      {job.status === 'offline_dispatched' && (
        <div className="card p-4 border border-violet-500/30 bg-violet-500/5">
          <div className="text-xs font-semibold text-violet-400 uppercase tracking-wide mb-1 flex items-center gap-2">
            <ArrowDownToLine size={12} />
            Offline Mode
          </div>
          <div className="text-sm text-text-muted">
            {job.progress?.message || `Awaiting results from ${(job.params?.offline_job_ids as string[] | undefined)?.length ?? 1} worker(s)`}
          </div>
        </div>
      )}

      {/* Shared strings — waiting on other jobs */}
      {Object.keys(job.waiting_on_jobs ?? {}).length > 0 && (
        <div className="card p-4 border border-accent/30 bg-accent/5">
          <div className="text-xs font-semibold text-accent uppercase tracking-wide mb-2 flex items-center gap-2">
            <Hash size={12} />
            Shared Strings
          </div>
          <p className="text-sm text-text-muted mb-2">
            {Object.values(job.waiting_on_jobs!).reduce((a, b) => a + b, 0)} string(s) being translated by other job(s):
          </p>
          <ul className="space-y-1">
            {Object.entries(job.waiting_on_jobs!).map(([ownerJobId, count]) => (
              <li key={ownerJobId} className="text-sm flex items-center gap-2">
                <Link
                  to="/jobs/$jobId"
                  params={{ jobId: ownerJobId }}
                  className="font-mono text-accent hover:underline"
                >
                  {ownerJobId.slice(0, 8)}…
                </Link>
                <span className="text-text-muted">— {count} shared hash{count !== 1 ? 'es' : ''}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Progress bar */}
      {(job.status === 'running' || job.status === 'offline_dispatched' || (job.pct > 0 && job.pct < 100)) && job.progress && (
        <div className="card p-4">
          <ProgressBar
            pct={job.pct}
            message={job.progress.message}
            subStep={job.progress.sub_step}
          />
        </div>
      )}

      {/* Workers */}
      <WorkersTable workers={job.worker_updates ?? []} updates={job.string_updates ?? []} />

      {/* Worker assignment */}
      <WorkerAssignmentPanel job={job} />

      {/* String updates */}
      <StringUpdatesPanel updates={job.string_updates ?? []} />

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
