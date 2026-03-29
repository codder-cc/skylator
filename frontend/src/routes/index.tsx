import { createFileRoute, useNavigate, Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { QK } from '@/lib/queryKeys'
import { statsApi } from '@/api/stats'
import { jobsApi } from '@/api/jobs'
import { workersApi } from '@/api/workers'
import { StatCard } from '@/components/shared/StatCard'
import { GpuWidget } from '@/components/shared/GpuWidget'
import { pctColor, cn } from '@/lib/utils'
import type { Job } from '@/types'
import {
  Layers,
  CheckCircle,
  Clock,
  AlertCircle,
  Type,
  ChevronDown,
  X,
  Zap,
  RotateCcw,
  Activity,
} from 'lucide-react'

// ── Token Performance Widget ──────────────────────────────────────────────────

function TokenPerfWidget() {
  const qc = useQueryClient()

  const { data: perf, isFetching } = useQuery({
    queryKey: QK.tokenPerf(),
    queryFn: statsApi.perf,
    refetchInterval: 5_000,
  })

  const resetMut = useMutation({
    mutationFn: statsApi.resetTokens,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.tokenPerf() })
      qc.invalidateQueries({ queryKey: QK.tokenStats() })
    },
  })

  const p = perf

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Activity size={14} className="text-accent" />
        <span className="text-sm font-semibold text-text-main">Translation Engine</span>
        {isFetching && <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse ml-1" />}
        <div className="flex-1" />
        <button
          onClick={() => resetMut.mutate()}
          disabled={resetMut.isPending}
          className="flex items-center gap-1 text-xs text-text-muted hover:text-text-main transition-colors disabled:opacity-50"
          title="Reset token counters"
        >
          <RotateCcw size={11} className={resetMut.isPending ? 'animate-spin' : ''} />
          Reset
        </button>
      </div>

      {!p || !p.ok ? (
        <p className="text-xs text-text-muted italic">No inference stats yet this session.</p>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          <div>
            <div className="text-xs text-text-muted uppercase tracking-wide mb-0.5">tok/s (last)</div>
            <div className={cn('text-lg font-bold font-mono tabular-nums',
              p.tps_last > 0 ? 'text-accent' : 'text-text-muted')}>
              {p.tps_last > 0 ? p.tps_last.toFixed(1) : '—'}
            </div>
          </div>
          <div>
            <div className="text-xs text-text-muted uppercase tracking-wide mb-0.5">tok/s (avg)</div>
            <div className={cn('text-lg font-bold font-mono tabular-nums',
              p.tps_avg > 0 ? 'text-success' : 'text-text-muted')}>
              {p.tps_avg > 0 ? p.tps_avg.toFixed(1) : '—'}
            </div>
          </div>
          <div>
            <div className="text-xs text-text-muted uppercase tracking-wide mb-0.5">Last batch</div>
            <div className="text-lg font-bold font-mono tabular-nums text-text-main">
              {p.last_completion_tokens > 0 ? p.last_completion_tokens.toLocaleString() : '—'}
              <span className="text-xs text-text-muted font-normal ml-1">tok</span>
            </div>
          </div>
          <div>
            <div className="text-xs text-text-muted uppercase tracking-wide mb-0.5">Last elapsed</div>
            <div className="text-lg font-bold font-mono tabular-nums text-text-main">
              {p.last_elapsed_sec > 0
                ? p.last_elapsed_sec < 60
                  ? `${p.last_elapsed_sec.toFixed(1)}s`
                  : `${(p.last_elapsed_sec / 60).toFixed(1)}m`
                : '—'}
            </div>
          </div>
          <div>
            <div className="text-xs text-text-muted uppercase tracking-wide mb-0.5">Total tokens</div>
            <div className="text-lg font-bold font-mono tabular-nums text-text-main">
              {p.total_tokens > 0 ? (p.total_tokens / 1000).toFixed(1) : '—'}
              <span className="text-xs text-text-muted font-normal ml-1">k</span>
            </div>
          </div>
          <div>
            <div className="text-xs text-text-muted uppercase tracking-wide mb-0.5">Calls</div>
            <div className="text-lg font-bold font-mono tabular-nums text-text-muted">
              {p.calls}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Currently Translating card ────────────────────────────────────────────────

function CurrentlyTranslatingCard() {
  const { data: jobs = [] } = useQuery<Job[]>({
    queryKey: QK.jobs(),
    queryFn: jobsApi.list,
    refetchInterval: 4_000,
    staleTime: 3_000,
  })

  const running = jobs.filter((j) => j.status === 'running')
  if (running.length === 0) return null

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-success animate-pulse shrink-0" />
        <span className="text-sm font-semibold text-text-main">Currently Translating</span>
        <span className="text-xs text-text-muted ml-1">{running.length} active</span>
      </div>

      <div className="space-y-3">
        {running.map((job) => {
          const workers = job.worker_updates ?? []
          const activeWorkers = workers.filter((w) => w.alive && w.tps > 0)
          const totalTps = workers.reduce((s, w) => s + (w.tps ?? 0), 0)

          return (
            <div key={job.id} className="border border-border-subtle/50 rounded-lg p-3 space-y-2">
              {/* Job header */}
              <div className="flex items-center gap-2 min-w-0">
                <Link
                  to="/jobs/$jobId"
                  params={{ jobId: job.id }}
                  className="text-sm font-medium text-accent hover:underline truncate flex-1 min-w-0"
                >
                  {job.name}
                </Link>
                <span className="text-xs font-mono tabular-nums text-text-muted shrink-0">
                  {job.pct.toFixed(1)}%
                </span>
                {totalTps > 0 && (
                  <span className="flex items-center gap-0.5 text-xs font-mono tabular-nums font-semibold text-accent shrink-0">
                    <Zap size={10} />
                    {totalTps.toFixed(1)}
                  </span>
                )}
              </div>

              {/* Progress bar */}
              {job.pct > 0 && (
                <div className="h-1 bg-bg-card2 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-accent rounded-full transition-all duration-500"
                    style={{ width: `${job.pct}%` }}
                  />
                </div>
              )}

              {/* Worker rows */}
              {workers.length > 0 && (
                <div className="space-y-1">
                  {workers.map((w) => (
                    <div key={w.label} className={cn('flex items-center gap-2 text-xs', !w.alive && 'opacity-40')}>
                      <span className={cn(
                        'w-1.5 h-1.5 rounded-full shrink-0',
                        w.alive ? 'bg-success' : 'bg-text-muted',
                      )} />
                      <span className="font-mono text-text-muted/70 shrink-0 w-24 truncate">{w.label}</span>
                      <span className={cn(
                        'font-mono tabular-nums shrink-0',
                        w.tps > 0 ? 'text-accent' : 'text-text-muted/40',
                      )}>
                        {w.tps > 0 ? `${w.tps.toFixed(1)} t/s` : '—'}
                      </span>
                      {w.current_text && (
                        <span className="text-text-muted/60 truncate min-w-0">{w.current_text}</span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Batch Modal ───────────────────────────────────────────────────────────────

type BatchScope = 'all' | 'esp' | 'mcm' | 'bsa' | 'review' | 'pending'

const SCOPE_OPTIONS: { value: BatchScope; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'esp', label: 'ESP only' },
  { value: 'mcm', label: 'MCM only' },
  { value: 'bsa', label: 'BSA only' },
  { value: 'review', label: 'Review only' },
  { value: 'pending', label: 'Pending only' },
]

interface BatchModalProps {
  onClose: () => void
}

function BatchModal({ onClose }: BatchModalProps) {
  const navigate = useNavigate()
  const [scope, setScope] = useState<BatchScope>('all')
  const [force, setForce] = useState(false)
  const [resume, setResume] = useState(true)
  const [machines, setMachines] = useState<string[]>([])
  const [offline, setOffline] = useState(false)

  const { data: workers = [] } = useQuery({
    queryKey: QK.workers(),
    queryFn: workersApi.list,
    staleTime: 30_000,
  })

  const createMut = useMutation({
    mutationFn: () =>
      jobsApi.create({
        job_type: 'translate_all',
        options: { scope, force, resume, machines, ...(offline && machines.length > 0 && { offline: true }) },
      }),
    onSuccess: () => {
      onClose()
      void navigate({ to: '/jobs' })
    },
  })

  const toggleMachine = (id: string) => {
    setMachines((prev) =>
      prev.includes(id) ? prev.filter((m) => m !== id) : [...prev, id],
    )
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-bg-base/80 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="card p-6 w-full max-w-md space-y-5 shadow-2xl">
        {/* Modal header */}
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-text-main">Configure Batch Translation</h2>
          <button onClick={onClose} className="text-text-muted hover:text-text-main transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* Scope */}
        <div className="space-y-2">
          <p className="text-xs font-semibold text-text-muted uppercase tracking-wide">Scope</p>
          <div className="grid grid-cols-3 gap-2">
            {SCOPE_OPTIONS.map((opt) => (
              <label
                key={opt.value}
                className={cn(
                  'flex items-center gap-2 px-3 py-2 rounded-lg border text-sm cursor-pointer transition-colors',
                  scope === opt.value
                    ? 'border-accent/60 bg-accent/10 text-accent'
                    : 'border-border-subtle bg-bg-card2 text-text-muted hover:text-text-main',
                )}
              >
                <input
                  type="radio"
                  name="scope"
                  value={opt.value}
                  checked={scope === opt.value}
                  onChange={() => setScope(opt.value)}
                  className="sr-only"
                />
                {opt.label}
              </label>
            ))}
          </div>
        </div>

        {/* Options */}
        <div className="space-y-2">
          <p className="text-xs font-semibold text-text-muted uppercase tracking-wide">Options</p>
          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={force}
              onChange={(e) => setForce(e.target.checked)}
              className="w-4 h-4 rounded border-border-subtle bg-bg-card2 accent-accent"
            />
            <span className="text-sm text-text-main">Force re-translate (bypass cache)</span>
          </label>
          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={resume}
              onChange={(e) => setResume(e.target.checked)}
              className="w-4 h-4 rounded border-border-subtle bg-bg-card2 accent-accent"
            />
            <span className="text-sm text-text-main">Skip already-completed mods</span>
          </label>
          {machines.length > 0 && (
            <label className="flex items-center gap-3 cursor-pointer">
              <input
                type="checkbox"
                checked={offline}
                onChange={(e) => setOffline(e.target.checked)}
                className="w-4 h-4 rounded border-border-subtle bg-bg-card2 accent-accent"
              />
              <span className="text-sm text-text-main">Offline translate (dispatch to workers)</span>
            </label>
          )}
        </div>

        {/* Machines */}
        <div className="space-y-2">
          <p className="text-xs font-semibold text-text-muted uppercase tracking-wide">Machines</p>
          <div className="space-y-1.5">
            {workers.length === 0 && (
              <p className="text-xs text-text-muted italic">No workers registered. Start a worker server and connect it to this host.</p>
            )}
            {workers.map((w) => (
              <label key={w.label} className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={machines.includes(w.label)}
                  onChange={() => toggleMachine(w.label)}
                  className="w-4 h-4 rounded border-border-subtle bg-bg-card2 accent-accent"
                />
                <span className="text-sm text-text-main">{w.label}</span>
                {w.alive && (
                  <span className="text-xs text-success bg-success/10 px-1.5 py-0.5 rounded border border-success/30">
                    online
                  </span>
                )}
              </label>
            ))}
          </div>
        </div>

        {/* Footer */}
        {createMut.isError && (
          <p className="text-xs text-danger">
            Failed: {String((createMut.error as Error)?.message ?? 'Unknown error')}
          </p>
        )}
        <div className="flex gap-3 justify-end pt-1">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-sm text-text-muted hover:text-text-main border border-border-subtle hover:bg-bg-card2 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => createMut.mutate()}
            disabled={createMut.isPending || machines.length === 0}
            className="px-5 py-2 rounded-lg text-sm font-medium bg-accent text-white hover:bg-accent/80 disabled:opacity-50 transition-colors"
          >
            {createMut.isPending ? 'Starting…' : 'Start'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

function DashboardPage() {
  const [showBatchModal, setShowBatchModal] = useState(false)

  const { data: rawStats } = useQuery({
    queryKey: QK.stats(),
    queryFn: statsApi.get,
    refetchInterval: 15_000,
  })
  // Normalise field names — backend was renamed in a deploy; accept both old and new keys
  const stats = rawStats
    ? (() => {
        const r = rawStats as unknown as Record<string, number>
        return {
          ...rawStats,
          mods_translated:    rawStats.mods_translated    ?? r['mods_done']  ?? 0,
          translated_strings: rawStats.translated_strings ?? r['translated'] ?? 0,
          pending_strings:    rawStats.pending_strings    ?? r['pending']    ?? 0,
          pct_complete:       rawStats.pct_complete       ?? r['pct']        ?? 0,
        }
      })()
    : undefined

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-text-main">Dashboard</h1>
        <div className="flex items-center gap-2">
          <GpuWidget />
          <div className="flex rounded-lg overflow-hidden border border-accent/40">
            <button
              onClick={() => setShowBatchModal(true)}
              className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-accent/20 text-accent hover:bg-accent/30 transition-colors"
            >
              Translate All
            </button>
            <button
              onClick={() => setShowBatchModal(true)}
              className="flex items-center px-2 py-2 bg-accent/20 text-accent hover:bg-accent/30 transition-colors border-l border-accent/30"
              aria-label="Configure batch"
            >
              <ChevronDown size={14} />
            </button>
          </div>
        </div>
      </div>

      {stats && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard
              icon={<Layers className="w-5 h-5" />}
              value={stats.total_mods}
              label="Total Mods"
            />
            <StatCard
              icon={<CheckCircle className="w-5 h-5 text-success" />}
              value={stats.mods_translated}
              label="Translated"
              color="text-success"
            />
            <StatCard
              icon={<AlertCircle className="w-5 h-5 text-warning" />}
              value={stats.mods_partial}
              label="Partial"
              color="text-warning"
            />
            <StatCard
              icon={<Clock className="w-5 h-5 text-text-muted" />}
              value={stats.mods_pending}
              label="Pending"
            />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <StatCard
              icon={<Type className="w-5 h-5" />}
              value={stats.total_strings.toLocaleString()}
              label="Total Strings"
            />
            <StatCard
              icon={<CheckCircle className="w-5 h-5 text-success" />}
              value={stats.translated_strings.toLocaleString()}
              label="Translated Strings"
              color="text-success"
            />
            <StatCard
              icon={<Clock className="w-5 h-5 text-text-muted" />}
              value={stats.pending_strings.toLocaleString()}
              label="Pending Strings"
            />
          </div>

          <div className="card p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm text-text-muted font-medium">Overall Progress</span>
              <span className={`text-lg font-bold ${pctColor(stats.pct_complete)}`}>
                {stats.pct_complete.toFixed(1)}%
              </span>
            </div>
            <div className="h-3 bg-bg-card2 rounded-full overflow-hidden">
              <div
                className="h-full bg-accent rounded-full transition-all duration-500"
                style={{ width: `${stats.pct_complete}%` }}
              />
            </div>
          </div>
        </>
      )}

      <TokenPerfWidget />

      <CurrentlyTranslatingCard />

      {!stats && (
        <div className="card p-8 text-center text-text-muted">
          Loading statistics...
        </div>
      )}

      {showBatchModal && <BatchModal onClose={() => setShowBatchModal(false)} />}
    </div>
  )
}

export const Route = createFileRoute('/')({
  component: DashboardPage,
})
