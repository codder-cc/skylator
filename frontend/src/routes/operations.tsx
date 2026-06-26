import { createFileRoute, Link } from '@tanstack/react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Activity, Cpu, Zap, AlertTriangle, ArrowDownToLine, Download, RotateCcw } from 'lucide-react'
import { QK } from '@/lib/queryKeys'
import { workersApi } from '@/api/workers'
import { jobsApi } from '@/api/jobs'
import { apiFetch } from '@/api/client'
import { cn } from '@/lib/utils'

interface CampaignEstimate {
  pending: number; eta_human: string; eta_seconds: number; fleet_tps: number; agents: number
}

// ── Active translate jobs: pull-done-now (collect) + export + resume (B2/B3) ──
function ActiveJobCard({ jobId, name, status }: { jobId: string; name: string; status: string }) {
  const qc = useQueryClient()
  const { data: t } = useQuery({
    queryKey: QK.jobTally(jobId), queryFn: () => jobsApi.tally(jobId), refetchInterval: 5000,
  })
  const collectMut = useMutation({
    mutationFn: () => jobsApi.collect(jobId),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.jobs() }),
  })
  const resumeMut = useMutation({
    mutationFn: () => jobsApi.resume(jobId),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.jobs() }),
  })
  const dispatchBackMut = useMutation({
    mutationFn: () => jobsApi.dispatchBack(jobId),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.jobs() }),
  })

  const handleExport = async () => {
    const data = await jobsApi.export(jobId)
    const blob = new Blob([JSON.stringify(data.strings, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `job-${jobId.slice(0, 8)}-translations.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 mb-2">
        <Link to="/jobs/$jobId" params={{ jobId }} className="text-sm font-medium text-text-main hover:text-accent truncate">
          {name}
        </Link>
        <span className="text-[10px] px-1.5 rounded bg-bg-card2 text-text-muted">{status}</span>
        {t && (
          <span className="ml-auto text-xs text-text-muted font-mono">
            {t.delivered}/{t.assigned || t.translated + t.pending} · {t.translated} done · {t.pending} pending
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => collectMut.mutate()}
          disabled={collectMut.isPending}
          title="Deploy every translated string to the game files now — don't wait for the run to finish"
          className="flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium bg-success/20 text-success border border-success/30 hover:bg-success/30 disabled:opacity-50"
        >
          <ArrowDownToLine className="w-3 h-3" />Pull done now
        </button>
        <button
          onClick={handleExport}
          title="Download the done translations as JSON (no deploy)"
          className="flex items-center gap-1 px-2.5 py-1 rounded text-xs bg-bg-card2 text-text-muted border border-border-subtle hover:text-text-main"
        >
          <Download className="w-3 h-3" />Export JSON
        </button>
        {status === 'paused' && (
          <button
            onClick={() => resumeMut.mutate()}
            disabled={resumeMut.isPending}
            className="flex items-center gap-1 px-2.5 py-1 rounded text-xs bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 disabled:opacity-50"
          >
            <RotateCcw className="w-3 h-3" />Resume
          </button>
        )}
        {status === 'offline_dispatched' && (
          <button
            onClick={() => dispatchBackMut.mutate()}
            disabled={dispatchBackMut.isPending}
            className="flex items-center gap-1 px-2.5 py-1 rounded text-xs bg-violet-500/20 text-violet-400 border border-violet-500/30 hover:bg-violet-500/30 disabled:opacity-50"
          >
            <ArrowDownToLine className="w-3 h-3" />Dispatch back
          </button>
        )}
      </div>
    </div>
  )
}

// ── Live operations dashboard (B1) ───────────────────────────────────────────
// Everything here derives from DURABLE state (assignments + heartbeat health), so it stays
// correct across agent reconnect / master restart — an agent that went offline and came back
// shows its resumed progress automatically.

function pct(n: number, d: number) {
  return d > 0 ? Math.round((n / d) * 100) : 0
}

function OperationsPage() {
  const { data: workers = [] } = useQuery({
    queryKey: QK.workers(), queryFn: workersApi.list, refetchInterval: 4000,
  })
  const { data: assign } = useQuery({
    queryKey: QK.assignments(), queryFn: workersApi.assignments, refetchInterval: 8000,
  })
  const { data: jobs = [] } = useQuery({
    queryKey: QK.jobs(), queryFn: jobsApi.list, refetchInterval: 4000,
  })
  const { data: campaign } = useQuery({
    queryKey: ['campaign'],
    queryFn: () => apiFetch<CampaignEstimate>('/api/campaign/estimate'),
    refetchInterval: 15000,
  })
  const activeJobs = jobs.filter(
    (j) => ['running', 'offline_dispatched', 'paused'].includes(j.status) &&
           (j.job_type || '').includes('translate'),
  )

  // Per-agent assignment progress (sum across that agent's assignments).
  const byAgent: Record<string, { total: number; delivered: number }> = {}
  for (const a of assign?.assignments ?? []) {
    const e = (byAgent[a.agent_id] ??= { total: 0, delivered: 0 })
    e.total += a.total
    e.delivered += a.delivered
  }
  const agg = assign?.aggregate
  const totalTps = workers.reduce((s, w) => s + (w.stats?.tps_last ?? 0), 0)
  const alive = workers.filter((w) => w.alive)

  const tierClass = (t?: string) =>
    t === 'no' || t === 'presumed_dead' ? 'text-danger'
      : t === 'tight' || t === 'disconnected' ? 'text-warning' : 'text-success'

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Activity className="w-5 h-5 text-accent" />
        <h1 className="text-2xl font-bold text-text-main">Operations</h1>
        <span className="ml-auto text-sm text-text-muted">
          {alive.length}/{workers.length} agents live · {totalTps.toFixed(1)} tok/s total
        </span>
      </div>

      {campaign && campaign.pending > 0 && (
        <div className="card p-3 flex items-center gap-3 text-sm">
          <span className="text-text-muted">Backlog ETA</span>
          <span className="font-mono font-semibold text-accent">≈ {campaign.eta_human}</span>
          <span className="text-text-muted">
            for {campaign.pending.toLocaleString()} pending strings on {campaign.agents} agent(s)
          </span>
          <span className="ml-auto text-[11px] text-text-muted">approx</span>
        </div>
      )}

      {/* Global funnel */}
      {agg && (
        <div className="card p-4 grid grid-cols-3 sm:grid-cols-5 gap-4">
          {([
            ['Assigned', agg.total, 'text-text-main'],
            ['Delivered', agg.delivered, 'text-accent'],
            ['Active', agg.active, 'text-success'],
            ['Disconnected', agg.disconnected, 'text-warning'],
            ['Presumed dead', agg.presumed_dead, 'text-danger'],
          ] as Array<[string, number, string]>).map(([label, val, color]) => (
            <div key={label}>
              <div className={cn('text-2xl font-bold font-mono', color)}>{val}</div>
              <div className="text-xs text-text-muted">{label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Active translate jobs — pull partial results / resume without waiting */}
      {activeJobs.length > 0 && (
        <div className="space-y-2">
          <h2 className="text-sm font-semibold text-text-muted uppercase tracking-wide">
            Active translation jobs
          </h2>
          {activeJobs.map((j) => (
            <ActiveJobCard key={j.id} jobId={j.id} name={j.name} status={j.status} />
          ))}
        </div>
      )}

      {/* Per-agent live activity */}
      <div className="space-y-2">
        {workers.length === 0 && (
          <div className="card p-6 text-center text-text-muted text-sm">
            No agents registered. Add a worker on the Servers page.
          </div>
        )}
        {workers.map((w) => {
          const prog = byAgent[w.label] ?? { total: 0, delivered: 0 }
          const dl = w.download_progress
          const h = w.health ?? {}
          return (
            <div key={w.label} className="card p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className={cn('w-2 h-2 rounded-full', w.alive ? 'bg-success' : 'bg-danger')} />
                <span className="text-sm font-semibold text-text-main">{w.label}</span>
                <Cpu className="w-3 h-3 text-text-muted" />
                <span className="text-xs text-text-muted truncate max-w-[14rem]" title={w.model ?? ''}>
                  {w.model || 'no model'}
                </span>
                {h.disk_full && <span className="px-1 rounded text-[9px] bg-danger/20 text-danger">disk full</span>}
                {h.stalled && <span className="px-1 rounded text-[9px] bg-warning/20 text-warning">stalled</span>}
                {h.idle_starved && <span className="px-1 rounded text-[9px] bg-bg-card2 text-text-muted border border-border-subtle">idle</span>}
                <span className="ml-auto flex items-center gap-1 text-xs text-accent font-mono">
                  <Zap className="w-3 h-3" />{(w.stats?.tps_last ?? 0).toFixed(1)} t/s
                </span>
              </div>

              {/* RT4 — live memory usage + context window */}
              {(() => {
                const hw = w.hardware
                if (!hw) return null
                const total = hw.unified_memory ? hw.ram_total_mb : hw.vram_total_mb
                const free  = hw.unified_memory ? hw.ram_free_mb : hw.vram_free_mb
                const used  = total > 0 ? total - free : 0
                const usedPct = total > 0 ? Math.round((used / total) * 100) : 0
                const nctx = w.stats?.n_ctx ?? 0
                if (total <= 0 && !nctx) return null
                return (
                  <div className="flex items-center gap-3 mb-2 text-[10px] text-text-muted">
                    {total > 0 && (
                      <div className="flex items-center gap-1 flex-1 max-w-[16rem]">
                        <span>{hw.unified_memory ? 'Mem' : 'VRAM'}</span>
                        <div className="flex-1 h-1.5 rounded bg-bg-base overflow-hidden">
                          <div className={cn('h-full transition-all',
                                 usedPct > 92 ? 'bg-danger' : usedPct > 80 ? 'bg-warning' : 'bg-accent')}
                               style={{ width: `${usedPct}%` }} />
                        </div>
                        <span className="font-mono">{(used / 1024).toFixed(1)}/{(total / 1024).toFixed(1)}G</span>
                      </div>
                    )}
                    {nctx > 0 && <span className="font-mono">ctx {Math.round(nctx / 1024)}k</span>}
                  </div>
                )
              })()}

              {/* Current string being translated */}
              {w.current_task && (
                <div className="text-xs text-text-muted mb-2 truncate" title={w.current_task}>
                  ▶ {w.current_task}
                </div>
              )}

              {/* Assignment progress */}
              {prog.total > 0 && (
                <div>
                  <div className="flex justify-between text-[10px] text-text-muted mb-0.5">
                    <span>Assigned work</span>
                    <span>{prog.delivered}/{prog.total} ({pct(prog.delivered, prog.total)}%)</span>
                  </div>
                  <div className="h-1.5 rounded bg-bg-base overflow-hidden">
                    <div className="h-full bg-accent transition-all"
                         style={{ width: `${pct(prog.delivered, prog.total)}%` }} />
                  </div>
                </div>
              )}

              {/* Download progress */}
              {dl?.stage === 'downloading' && (
                <div className="mt-2">
                  <div className="flex justify-between text-[10px] text-text-muted mb-0.5">
                    <span className="truncate">⬇ {dl.model}</span>
                    <span>{dl.pct != null ? `${dl.pct}%` : `${dl.downloaded_mb ?? 0} MB`}</span>
                  </div>
                  <div className="h-1.5 rounded bg-bg-base overflow-hidden">
                    <div className="h-full bg-success transition-all" style={{ width: `${dl.pct ?? 30}%` }} />
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>

      <p className="text-[11px] text-text-muted flex items-center gap-1">
        <AlertTriangle className="w-3 h-3" />
        Progress is derived from durable assignments + heartbeats — it stays accurate across an
        agent reconnect or a master restart.
      </p>
    </div>
  )
}

export const Route = createFileRoute('/operations')({ component: OperationsPage })
