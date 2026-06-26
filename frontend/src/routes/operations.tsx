import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { Activity, Cpu, Zap, AlertTriangle } from 'lucide-react'
import { QK } from '@/lib/queryKeys'
import { workersApi } from '@/api/workers'
import { cn } from '@/lib/utils'

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
