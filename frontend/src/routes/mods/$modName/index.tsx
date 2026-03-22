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
  ScanSearch,
  ShieldCheck,
  Cpu,
  ChevronDown,
  ChevronUp,
  Download,
  Wand2,
  ExternalLink,
} from 'lucide-react'
import { useState } from 'react'
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

type StepStatus = 'done' | 'partial' | 'pending' | 'inactive' | 'running'

interface PipelineStep {
  label: string
  icon: React.ReactNode
  status: StepStatus
  tooltip?: string
  onClick?: () => void
  isPending?: boolean
}

function StepDot({ status }: { status: StepStatus }) {
  return (
    <span
      className={cn(
        'w-2 h-2 rounded-full shrink-0',
        status === 'done' && 'bg-success',
        status === 'partial' && 'bg-warning',
        status === 'pending' && 'bg-accent',
        status === 'running' && 'bg-accent animate-pulse',
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
            <div className="flex flex-col items-center gap-1.5 flex-1 min-w-0">
              <button
                onClick={step.onClick}
                disabled={!step.onClick || step.isPending}
                className={cn(
                  'flex items-center justify-center w-9 h-9 rounded-full border-2 transition-all',
                  step.status === 'done' && 'border-success bg-success/10 text-success',
                  step.status === 'partial' && 'border-warning bg-warning/10 text-warning',
                  step.status === 'pending' && 'border-accent bg-accent/10 text-accent',
                  step.status === 'running' && 'border-accent bg-accent/20 text-accent',
                  step.status === 'inactive' && 'border-border-subtle bg-bg-card2 text-text-muted/40',
                  step.onClick && !step.isPending && 'hover:scale-110 cursor-pointer hover:shadow-md',
                  (!step.onClick || step.isPending) && 'cursor-default',
                )}
                title={step.tooltip}
              >
                {step.isPending
                  ? <RefreshCw size={14} className="animate-spin" />
                  : step.icon}
              </button>
              <div className="flex items-center gap-1">
                <StepDot status={step.status} />
                <span
                  className={cn(
                    'text-xs font-medium truncate',
                    step.status === 'inactive' ? 'text-text-muted/40' : 'text-text-muted',
                    step.onClick && 'hover:text-text-main',
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

// ── Validation panel ─────────────────────────────────────────────────────────

function ValidationPanel({ modName }: { modName: string }) {
  const [expanded, setExpanded] = useState(false)
  const qc = useQueryClient()

  const { data, isLoading, isFetching } = useQuery({
    queryKey: QK.modValidation(modName),
    queryFn: () => modsApi.getValidation(modName),
    enabled: expanded,
    staleTime: 30_000,
  })

  return (
    <div className="card overflow-hidden">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2 px-4 py-3 hover:bg-bg-card2 transition-colors text-left"
      >
        <ShieldCheck size={14} className="text-accent shrink-0" />
        <span className="text-sm font-semibold text-text-main uppercase tracking-wide flex-1">
          Validation
        </span>
        {isFetching && <RefreshCw size={12} className="animate-spin text-text-muted" />}
        {expanded
          ? <ChevronUp size={14} className="text-text-muted" />
          : <ChevronDown size={14} className="text-text-muted" />}
      </button>

      {expanded && (
        <div className="border-t border-border-subtle p-4 space-y-3">
          {isLoading ? (
            <p className="text-sm text-text-muted">Loading…</p>
          ) : !data?.ok ? (
            <div className="space-y-2">
              <p className="text-sm text-text-muted italic">
                {data?.error ?? 'No validation data. Run the Validate step first.'}
              </p>
            </div>
          ) : (
            <div className="space-y-3 text-sm">
              {Object.entries(data)
                .filter(([k]) => k !== 'ok')
                .map(([k, v]) => (
                  <div key={k} className="flex items-start gap-2">
                    <span className="text-text-muted w-32 shrink-0 font-medium">{k}</span>
                    <span className="text-text-main font-mono text-xs break-all">
                      {JSON.stringify(v)}
                    </span>
                  </div>
                ))}
            </div>
          )}
          <button
            onClick={() => qc.invalidateQueries({ queryKey: QK.modValidation(modName) })}
            className="flex items-center gap-1.5 text-xs text-text-muted hover:text-text-main"
          >
            <RefreshCw size={11} />
            Refresh
          </button>
        </div>
      )}
    </div>
  )
}

// ── Nexus + Context panel ─────────────────────────────────────────────────────

function NexusContextPanel({ modName, encodedName }: { modName: string; encodedName: string }) {
  const [expanded, setExpanded] = useState(false)
  const [showRaw, setShowRaw] = useState(false)
  const qc = useQueryClient()

  // Raw Nexus description (fast — just reads disk cache)
  const nexusQ = useQuery({
    queryKey: QK.modNexus(modName),
    queryFn: () => modsApi.getNexusRaw(modName),
    enabled: expanded,
    staleTime: 300_000,
  })

  // AI summary + custom context
  const contextQ = useQuery({
    queryKey: QK.modContext(modName),
    queryFn: () => modsApi.getContext(modName),
    enabled: expanded,
    staleTime: 60_000,
  })

  // Fetch from Nexus API (no LLM, fast)
  const fetchMut = useMutation({
    mutationFn: () => modsApi.fetchNexus(modName),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.modNexus(modName) }),
  })

  // Regenerate AI summary (slow — calls LLM)
  const summarizeMut = useMutation({
    mutationFn: () => modsApi.getContext(modName, true),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.modContext(modName) }),
  })

  const nexus   = nexusQ.data
  const context = contextQ.data

  return (
    <div className="card overflow-hidden">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2 px-4 py-3 hover:bg-bg-card2 transition-colors text-left"
      >
        <Globe size={14} className="text-accent shrink-0" />
        <span className="text-sm font-semibold text-text-main uppercase tracking-wide flex-1">
          Nexus &amp; AI Context
        </span>
        {nexus?.ok && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-success/20 text-success border border-success/30 font-mono mr-1">
            cached {nexus.age_hours}h ago
          </span>
        )}
        {expanded
          ? <ChevronUp size={14} className="text-text-muted" />
          : <ChevronDown size={14} className="text-text-muted" />}
      </button>

      {expanded && (
        <div className="border-t border-border-subtle divide-y divide-border-subtle">

          {/* ── Section 1: Raw Nexus description ───────────────────────────── */}
          <div className="p-4 space-y-3">
            <div className="flex items-center gap-2">
              <Globe size={13} className="text-text-muted" />
              <span className="text-xs font-semibold text-text-muted uppercase tracking-wide flex-1">
                Nexus Description (original)
              </span>
              {nexus?.ok && nexus.mod_id && (
                <a
                  href={`https://www.nexusmods.com/skyrimspecialedition/mods/${nexus.mod_id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1 text-[10px] text-accent hover:underline"
                >
                  <ExternalLink size={10} />
                  Nexus page
                </a>
              )}
              <button
                onClick={() => fetchMut.mutate()}
                disabled={fetchMut.isPending}
                className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-bg-card2 border border-border-subtle text-text-muted hover:text-text-main disabled:opacity-50 transition-colors"
              >
                {fetchMut.isPending
                  ? <RefreshCw size={10} className="animate-spin" />
                  : <Download size={10} />}
                {fetchMut.isPending ? 'Fetching…' : 'Fetch from Nexus'}
              </button>
            </div>

            {fetchMut.isError && (
              <p className="text-xs text-danger">{String((fetchMut.error as Error)?.message)}</p>
            )}

            {nexusQ.isLoading ? (
              <p className="text-xs text-text-muted">Loading…</p>
            ) : !nexus?.ok ? (
              <p className="text-xs text-text-muted italic">
                {nexus?.error ?? 'Not cached yet.'}
                {' '}Click "Fetch from Nexus" to download.
              </p>
            ) : (
              <div className="space-y-2">
                {nexus.name && (
                  <p className="text-sm font-semibold text-text-main">{nexus.name}</p>
                )}
                <button
                  onClick={() => setShowRaw((v) => !v)}
                  className="text-xs text-accent hover:underline flex items-center gap-1"
                >
                  {showRaw ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
                  {showRaw ? 'Hide' : 'Show'} original description
                </button>
                {showRaw && nexus.description && (
                  <pre className="text-xs text-text-muted font-mono whitespace-pre-wrap bg-bg-base p-3 rounded-md max-h-48 overflow-auto leading-relaxed">
                    {nexus.description}
                  </pre>
                )}
              </div>
            )}
          </div>

          {/* ── Section 2: AI Summary ────────────────────────────────────────── */}
          <div className="p-4 space-y-3">
            <div className="flex items-center gap-2">
              <Wand2 size={13} className="text-text-muted" />
              <span className="text-xs font-semibold text-text-muted uppercase tracking-wide flex-1">
                AI Summary (auto-generated)
              </span>
              <button
                onClick={() => summarizeMut.mutate()}
                disabled={summarizeMut.isPending}
                className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-bg-card2 border border-border-subtle text-text-muted hover:text-text-main disabled:opacity-50 transition-colors"
                title="Re-generate summary using LLM (slow — up to 10 min)"
              >
                {summarizeMut.isPending
                  ? <><RefreshCw size={10} className="animate-spin" /><span className="animate-pulse">Summarising…</span></>
                  : <><Wand2 size={10} />Make Summary</>}
              </button>
            </div>

            {summarizeMut.isError && (
              <p className="text-xs text-danger">{String((summarizeMut.error as Error)?.message)}</p>
            )}

            {contextQ.isLoading ? (
              <p className="text-xs text-text-muted">Loading…</p>
            ) : context?.auto_context ? (
              <pre className="text-xs text-text-muted font-mono whitespace-pre-wrap bg-bg-base p-3 rounded-md max-h-32 overflow-auto">
                {context.auto_context}
              </pre>
            ) : (
              <p className="text-xs text-text-muted italic">
                No summary yet. Fetch from Nexus first, then click "Make Summary".
              </p>
            )}
          </div>

          {/* ── Section 3: Custom context link ──────────────────────────────── */}
          <div className="p-4 flex items-center justify-between">
            <div>
              <p className="text-xs font-semibold text-text-muted uppercase tracking-wide">Custom Context</p>
              <p className="text-xs text-text-muted mt-0.5">
                {context?.context ? 'Custom instructions set' : 'No custom instructions'}
              </p>
            </div>
            <Link
              to="/mods/$modName/context"
              params={{ modName: encodedName }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 transition-colors"
            >
              <BookOpen size={11} />
              Edit Context
            </Link>
          </div>

        </div>
      )}
    </div>
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

  function makeJobMutation(type: string, extra?: Record<string, unknown>) {
    return useMutation({ // eslint-disable-line react-hooks/rules-of-hooks
      mutationFn: () =>
        jobsApi.create({
          type,
          mods: [decodedName],
          options: { machines },
          ...extra,
        }),
      onSuccess: (data) => {
        if (data.ok && data.job_id) {
          queryClient.invalidateQueries({ queryKey: QK.jobs() })
          navigate({ to: '/jobs/$jobId', params: { jobId: data.job_id } })
        }
      },
    })
  }

  const scanMut          = makeJobMutation('scan')
  const translateAllMut  = makeJobMutation('translate_mod')
  const translateEspMut  = makeJobMutation('translate_mod', { scope: 'esp' })
  const translateBsaMut  = makeJobMutation('translate_mod', { scope: 'bsa' })
  const validateMut      = makeJobMutation('validate')
  const applyMut         = makeJobMutation('apply_mod')
  const fetchNexusMut    = makeJobMutation('fetch_nexus')

  // ── Pipeline steps ─────────────────────────────────────────────────────────

  const pipelineSteps: PipelineStep[] = mod
    ? [
        {
          label: 'Scan',
          icon: <ScanSearch size={16} />,
          status: mod.cached_at ? 'done' : 'pending',
          tooltip: mod.cached_at ? `Scanned ${timeAgo(mod.cached_at)}` : 'Click to scan mod files',
          onClick: () => scanMut.mutate(),
          isPending: scanMut.isPending,
        },
        {
          label: 'Nexus',
          icon: <Globe size={16} />,
          status: 'inactive',
          tooltip: 'Fetch Nexus Mods description for AI context',
          onClick: () => fetchNexusMut.mutate(),
          isPending: fetchNexusMut.isPending,
        },
        {
          label: 'Translate',
          icon: <Play size={16} />,
          status: mod.pct >= 100 ? 'done' : mod.pct > 0 ? 'partial' : 'pending',
          tooltip: `${mod.pct.toFixed(1)}% translated — click to translate`,
          onClick: () => translateAllMut.mutate(),
          isPending: translateAllMut.isPending,
        },
        {
          label: 'Validate',
          icon: <ShieldCheck size={16} />,
          status: 'inactive',
          tooltip: 'Run validation — checks token preservation and quality scores',
          onClick: () => validateMut.mutate(),
          isPending: validateMut.isPending,
        },
        {
          label: 'Apply ESP',
          icon: <Cpu size={16} />,
          status: 'inactive',
          tooltip: 'Apply translations to ESP binary files',
          onClick: () => applyMut.mutate(),
          isPending: applyMut.isPending,
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

  const needsReview = mod.needs_review_strings ?? 0

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

      {/* Pipeline — each step is clickable */}
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
        <Link
          to="/mods/$modName/strings"
          params={{ modName }}
          search={{ scope: 'all', status: 'needs_review', q: '', page: 1 }}
          className={cn(
            'card p-4 block transition-colors',
            needsReview > 0 ? 'hover:bg-warning/5 cursor-pointer' : 'pointer-events-none',
          )}
        >
          <div className="text-xs text-text-muted mb-1 font-medium uppercase tracking-wide">
            Needs Review
          </div>
          <div className={cn(
            'text-2xl font-bold font-mono',
            needsReview > 0 ? 'text-warning' : 'text-text-muted',
          )}>
            {needsReview}
          </div>
          {needsReview > 0 && (
            <div className="text-[10px] text-warning/70 mt-1">click to view →</div>
          )}
        </Link>
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
            <p className="text-text-muted text-sm italic">No files indexed. Run Scan first.</p>
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
                label="Translate BSA / MCM"
              />
            )}
            <ActionButton
              onClick={() => scanMut.mutate()}
              isPending={scanMut.isPending}
              icon={<ScanSearch size={14} />}
              label="Scan Files"
            />
            <ActionButton
              onClick={() => validateMut.mutate()}
              isPending={validateMut.isPending}
              icon={<ShieldCheck size={14} />}
              label="Validate Translations"
            />
            <ActionButton
              onClick={() => applyMut.mutate()}
              isPending={applyMut.isPending}
              icon={<Cpu size={14} />}
              label="Apply to ESP"
            />
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
              Edit AI Context
            </Link>
          </div>
        </div>
      </div>

      {/* Collapsible panels */}
      <NexusContextPanel modName={decodedName} encodedName={modName} />
      <ValidationPanel modName={decodedName} />

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
