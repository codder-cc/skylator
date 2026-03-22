import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ChevronLeft,
  Play,
  FileText,
  Archive,
  Eye,
  CheckCircle,
  Clock,
  AlertTriangle,
  RefreshCw,
  BookOpen,
  Globe,
} from 'lucide-react'
import { modsApi } from '@/api/mods'
import { jobsApi } from '@/api/jobs'
import { QK } from '@/lib/queryKeys'
import { cn, timeAgo } from '@/lib/utils'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { ProgressBar } from '@/components/shared/ProgressBar'
import { TimeAgo } from '@/components/shared/TimeAgo'
import { useMachines } from '@/hooks/useMachines'

export const Route = createFileRoute('/mods/$modName/')({
  component: ModDetailPage,
})

// ── Pipeline step types ──────────────────────────────────────────────────────

type StepStatus = 'done' | 'partial' | 'pending' | 'inactive'

interface PipelineStep {
  label: string
  icon: React.ReactNode
  status: StepStatus
  tooltip?: string
}

function StepDot({ status }: { status: StepStatus }) {
  return (
    <span
      className={cn(
        'w-2 h-2 rounded-full shrink-0',
        status === 'done' && 'bg-success',
        status === 'partial' && 'bg-warning',
        status === 'pending' && 'bg-accent',
        status === 'inactive' && 'bg-text-muted/30',
      )}
    />
  )
}

function PipelineSteps({ steps }: { steps: PipelineStep[] }) {
  return (
    <div className="card p-4">
      <div className="flex items-center">
        {steps.map((step, i) => (
          <div key={step.label} className="flex items-center flex-1 min-w-0">
            <div
              className={cn(
                'flex flex-col items-center gap-1.5 px-2 py-1 flex-1 min-w-0',
              )}
              title={step.tooltip}
            >
              <div
                className={cn(
                  'flex items-center justify-center w-9 h-9 rounded-full border-2 transition-colors',
                  step.status === 'done' && 'border-success bg-success/10 text-success',
                  step.status === 'partial' && 'border-warning bg-warning/10 text-warning',
                  step.status === 'pending' && 'border-accent bg-accent/10 text-accent',
                  step.status === 'inactive' && 'border-border-subtle bg-bg-card2 text-text-muted/40',
                )}
              >
                {step.icon}
              </div>
              <div className="flex items-center gap-1">
                <StepDot status={step.status} />
                <span
                  className={cn(
                    'text-xs font-medium truncate',
                    step.status === 'inactive' ? 'text-text-muted/40' : 'text-text-muted',
                  )}
                >
                  {step.label}
                </span>
              </div>
            </div>
            {i < steps.length - 1 && (
              <div
                className={cn(
                  'h-px w-6 shrink-0 mx-1',
                  step.status === 'done' ? 'bg-success/40' : 'bg-border-subtle',
                )}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Stat card ────────────────────────────────────────────────────────────────

function StatCard({
  label,
  value,
  colorClass,
}: {
  label: string
  value: number | string
  colorClass?: string
}) {
  return (
    <div className="card p-4">
      <div className="text-xs text-text-muted mb-1 font-medium uppercase tracking-wide">{label}</div>
      <div className={cn('text-2xl font-bold font-mono', colorClass ?? 'text-text-main')}>
        {value}
      </div>
    </div>
  )
}

// ── Action button ────────────────────────────────────────────────────────────

function ActionButton({
  onClick,
  isPending,
  disabled,
  icon,
  label,
  variant = 'default',
}: {
  onClick: () => void
  isPending?: boolean
  disabled?: boolean
  icon: React.ReactNode
  label: string
  variant?: 'default' | 'primary'
}) {
  return (
    <button
      onClick={onClick}
      disabled={isPending || disabled}
      className={cn(
        'flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-colors w-full',
        variant === 'primary'
          ? 'bg-accent/20 text-accent hover:bg-accent/30 border border-accent/30'
          : 'bg-bg-card2 text-text-muted hover:text-text-main hover:bg-bg-card2/80 border border-border-subtle',
        (isPending || disabled) && 'opacity-50 cursor-not-allowed',
      )}
    >
      {isPending ? <RefreshCw size={14} className="animate-spin shrink-0" /> : icon}
      {label}
    </button>
  )
}

// ── Main page ────────────────────────────────────────────────────────────────

function ModDetailPage() {
  const { modName } = Route.useParams()
  const decodedName = decodeURIComponent(modName)
  const navigate = useNavigate()
  const machines = useMachines()
  const queryClient = useQueryClient()

  const { data: mod, isLoading } = useQuery({
    queryKey: QK.mod(decodedName),
    queryFn: () => modsApi.get(decodedName),
    staleTime: 30_000,
  })

  const { data: allJobs = [] } = useQuery({
    queryKey: QK.jobs(),
    queryFn: jobsApi.list,
    refetchInterval: 10_000,
  })

  const recentJobs = allJobs
    .filter((j) => {
      const mods = ((j as unknown as Record<string, unknown>)['mods'] as string[] | undefined) ?? []
      return mods.includes(decodedName) || j.name.includes(decodedName)
    })
    .sort((a, b) => b.created_at - a.created_at)
    .slice(0, 5)

  // ── Mutations ──────────────────────────────────────────────────────────────

  function makeTranslateMutation(type: string, extraBody?: Record<string, unknown>) {
    return useMutation({ // eslint-disable-line react-hooks/rules-of-hooks
      mutationFn: () =>
        jobsApi.create({
          type,
          mods: [decodedName],
          options: { machines },
          ...extraBody,
        }),
      onSuccess: (data) => {
        if (data.ok && data.job_id) {
          queryClient.invalidateQueries({ queryKey: QK.jobs() })
          navigate({ to: '/jobs/$jobId', params: { jobId: data.job_id } })
        }
      },
    })
  }

  const translateAllMut = makeTranslateMutation('translate_mod')
  const translateEspMut = makeTranslateMutation('translate_mod', { scope: 'esp' })
  const translateBsaMut = makeTranslateMutation('translate_mod', { scope: 'bsa' })

  // ── Pipeline steps ─────────────────────────────────────────────────────────

  const pipelineSteps: PipelineStep[] = mod
    ? [
        {
          label: 'Scan',
          icon: <Globe size={16} />,
          status: mod.cached_at ? 'done' : 'pending',
          tooltip: mod.cached_at
            ? `Scanned ${timeAgo(mod.cached_at)}`
            : 'Not yet scanned',
        },
        {
          label: 'Context',
          icon: <BookOpen size={16} />,
          status: 'inactive',
          tooltip: 'Context enrichment (future)',
        },
        {
          label: 'Translate',
          icon: <Play size={16} />,
          status:
            mod.pct >= 100
              ? 'done'
              : mod.pct > 0
              ? 'partial'
              : 'pending',
          tooltip: `${mod.pct.toFixed(1)}% translated`,
        },
        {
          label: 'Validate',
          icon: <CheckCircle size={16} />,
          status: 'inactive',
          tooltip: 'Validation (future)',
        },
        {
          label: 'Apply',
          icon: <Archive size={16} />,
          status: 'inactive',
          tooltip: 'Apply patches (future)',
        },
      ]
    : []

  // ── Loading state ──────────────────────────────────────────────────────────

  if (isLoading) {
    return (
      <div className="space-y-5 animate-pulse">
        <div className="h-8 bg-bg-card rounded w-64" />
        <div className="card p-6 h-24" />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="card p-4 h-20" />
          ))}
        </div>
      </div>
    )
  }

  if (!mod) {
    return (
      <div className="card p-8 text-center text-text-muted">
        <AlertTriangle size={32} className="mx-auto mb-3 text-warning" />
        <p>Mod not found: <code className="text-text-main">{decodedName}</code></p>
        <Link to="/mods" search={{ status: 'all', q: '' }} className="mt-4 inline-block text-accent hover:underline text-sm">
          ← Back to mods
        </Link>
      </div>
    )
  }

  const needsReview = mod.total_strings - mod.translated_strings - mod.pending_strings

  return (
    <div className="space-y-5">
      {/* Page header */}
      <div className="flex items-center gap-3">
        <Link
          to="/mods"
          search={{ status: 'all', q: '' }}
          className="flex items-center gap-1 text-sm text-text-muted hover:text-text-main transition-colors"
        >
          <ChevronLeft size={16} />
          Mods
        </Link>
        <span className="text-border-subtle">/</span>
        <h1 className="text-xl font-bold text-text-main truncate flex-1 min-w-0" title={decodedName}>
          {decodedName}
        </h1>
        <StatusBadge status={mod.status} />
      </div>

      {/* Pipeline */}
      <PipelineSteps steps={pipelineSteps} />

      {/* Progress */}
      {mod.total_strings > 0 && (
        <div className="card p-4">
          <ProgressBar
            pct={mod.pct}
            message={`${mod.translated_strings} of ${mod.total_strings} strings translated`}
          />
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total Strings" value={mod.total_strings} />
        <StatCard label="Translated" value={mod.translated_strings} colorClass="text-success" />
        <StatCard
          label="Needs Review"
          value={Math.max(0, needsReview)}
          colorClass={needsReview > 0 ? 'text-warning' : 'text-text-muted'}
        />
        <StatCard
          label="Pending"
          value={mod.pending_strings}
          colorClass={mod.pending_strings > 0 ? 'text-accent' : 'text-text-muted'}
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* Files section */}
        <div className="card p-4 space-y-3">
          <h2 className="text-sm font-semibold text-text-main uppercase tracking-wide">Files</h2>
          {mod.esp_files.length === 0 && mod.bsa_files.length === 0 ? (
            <p className="text-text-muted text-sm italic">No files indexed yet.</p>
          ) : (
            <div className="space-y-1">
              {mod.esp_files.map((f) => (
                <div key={f.name} className="flex items-center gap-2 text-sm text-text-muted py-1">
                  <FileText size={13} className="shrink-0 text-accent/60" />
                  <span className="truncate font-mono text-xs" title={f.path}>{f.name}</span>
                </div>
              ))}
              {mod.bsa_files.map((f) => (
                <div key={f.name} className="flex items-center gap-2 text-sm text-text-muted py-1">
                  <Archive size={13} className="shrink-0 text-warning/60" />
                  <span className="truncate font-mono text-xs" title={f.path}>{f.name}</span>
                </div>
              ))}
            </div>
          )}
          {mod.cached_at && (
            <p className="text-xs text-text-muted/60 pt-1 border-t border-border-subtle">
              Last scanned: <TimeAgo ts={mod.cached_at} />
            </p>
          )}
        </div>

        {/* Actions card */}
        <div className="card p-4 space-y-3">
          <h2 className="text-sm font-semibold text-text-main uppercase tracking-wide">Actions</h2>
          <div className="space-y-2">
            <ActionButton
              onClick={() => translateAllMut.mutate()}
              isPending={translateAllMut.isPending}
              icon={<Play size={14} />}
              label="Translate Mod"
              variant="primary"
            />
            <ActionButton
              onClick={() => translateEspMut.mutate()}
              isPending={translateEspMut.isPending}
              icon={<FileText size={14} />}
              label="Translate ESP Only"
            />
            {mod.bsa_files.length > 0 && (
              <ActionButton
                onClick={() => translateBsaMut.mutate()}
                isPending={translateBsaMut.isPending}
                icon={<Archive size={14} />}
                label="Translate BSA"
              />
            )}
            <Link
              to="/mods/$modName/strings"
              params={{ modName }}
              search={{ scope: 'all', status: 'all', q: '', page: 1 }}
              className={cn(
                'flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-colors w-full',
                'bg-bg-card2 text-text-muted hover:text-text-main hover:bg-bg-card2/80 border border-border-subtle',
              )}
            >
              <Eye size={14} />
              View Strings
            </Link>
            <Link
              to="/mods/$modName/context"
              params={{ modName }}
              className={cn(
                'flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-colors w-full',
                'bg-bg-card2 text-text-muted hover:text-text-main hover:bg-bg-card2/80 border border-border-subtle',
              )}
            >
              <BookOpen size={14} />
              Edit Context
            </Link>
          </div>
        </div>
      </div>

      {/* Recent jobs */}
      <div className="space-y-3">
        <h2 className="text-sm font-semibold text-text-main uppercase tracking-wide">Recent Jobs</h2>
        {recentJobs.length === 0 ? (
          <div className="card p-4 text-text-muted text-sm text-center italic">
            No jobs for this mod yet.
          </div>
        ) : (
          <div className="space-y-2">
            {recentJobs.map((job) => (
              <Link
                key={job.id}
                to="/jobs/$jobId"
                params={{ jobId: job.id }}
                className="card p-3 flex items-center gap-3 hover:bg-bg-card2 transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-text-main truncate">{job.name}</span>
                    <StatusBadge status={job.status} />
                  </div>
                  <div className="text-xs text-text-muted mt-0.5 flex items-center gap-1">
                    <Clock size={11} />
                    <TimeAgo ts={job.created_at} />
                  </div>
                </div>
                {job.status === 'running' && (
                  <div className="w-24 shrink-0">
                    <ProgressBar pct={job.pct} />
                  </div>
                )}
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
