import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Sparkles, ArrowRight, Cpu, Zap, Layers } from 'lucide-react'
import { planApi, PROFILES, type QualityProfile } from '@/api/models'
import { jobsApi } from '@/api/jobs'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/utils'

const PROFILE_META: Record<QualityProfile, { label: string; blurb: string }> = {
  fast:     { label: 'Fast',     blurb: 'One small model for everything — max throughput' },
  balanced: { label: 'Balanced', blurb: 'Small→large text routed to 7B / 14B / 27B' },
  quality:  { label: 'Quality',  blurb: 'The big 27B model for every string' },
  auto:     { label: 'Auto',     blurb: 'Phased: small text first on a fast model, switch up for hard text' },
}

const TIER_COLOR: Record<string, string> = {
  small: 'text-success', medium: 'text-warning', large: 'text-danger',
}

/**
 * VM4 — quality/auto model selector + phased-plan preview + start.
 *
 * Lets the user pick how much model muscle to spend (Fast / Balanced / Quality / Auto),
 * previews exactly which model translates which difficulty tier and how many model
 * switches that implies, then launches the `auto_translate` job.
 */
export function AutoTranslatePanel({ modName, machines }: { modName: string; machines: string[] }) {
  const [profile, setProfile] = useState<QualityProfile>('balanced')
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: plan, isFetching } = useQuery({
    queryKey: QK.translatePlan(modName, profile),
    queryFn: () => planApi.preview(modName, profile),
    enabled: !!modName,
    staleTime: 15_000,
  })

  const startMut = useMutation({
    mutationFn: () =>
      jobsApi.create({ type: 'auto_translate', mods: [modName], options: { machines, profile } }),
    onSuccess: (data) => {
      if (data.ok && data.job_id) {
        queryClient.invalidateQueries({ queryKey: QK.jobs() })
        navigate({ to: '/jobs/$jobId', params: { jobId: data.job_id } })
      }
    },
  })

  const noMachines = machines.length === 0
  const nothing = plan && plan.total === 0

  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 mb-3">
        <Sparkles className="w-4 h-4 text-accent" />
        <h3 className="text-sm font-semibold text-text-main">Auto / variable-model translate</h3>
        {plan && plan.total > 0 && (
          <span className="text-xs text-text-muted">{plan.total} pending</span>
        )}
      </div>

      {/* Profile selector */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3">
        {PROFILES.map((p) => (
          <button
            key={p}
            onClick={() => setProfile(p)}
            className={cn(
              'text-left rounded border px-2.5 py-2 transition-colors',
              profile === p
                ? 'border-accent bg-accent/10'
                : 'border-border-subtle hover:border-border-strong',
            )}
          >
            <div className="text-xs font-semibold text-text-main">{PROFILE_META[p].label}</div>
            <div className="text-[10px] text-text-muted leading-tight mt-0.5">{PROFILE_META[p].blurb}</div>
          </button>
        ))}
      </div>

      {/* Plan preview — which model runs which tier, in order */}
      {nothing ? (
        <p className="text-xs text-text-muted py-2">Nothing pending to translate for this mod.</p>
      ) : plan ? (
        <div className="rounded bg-bg-base/60 border border-border-subtle p-3 mb-3">
          <div className="flex items-center gap-3 text-[11px] text-text-muted mb-2">
            <span className="flex items-center gap-1"><Layers className="w-3 h-3" />{plan.phases.length} phase(s)</span>
            <span className="flex items-center gap-1"><Cpu className="w-3 h-3" />{plan.model_loads} model(s)</span>
            <span className="flex items-center gap-1"><Zap className="w-3 h-3" />{plan.model_switches} switch(es)</span>
          </div>
          <div className="space-y-1.5">
            {plan.phases.map((ph, i) => (
              <div key={ph.tier} className="flex items-center gap-2 text-xs">
                <span className="text-text-muted font-mono w-4">{i + 1}</span>
                <span className={cn('font-medium w-16', TIER_COLOR[ph.tier])}>{ph.tier}</span>
                <span className="font-mono text-text-muted">{ph.count}</span>
                <ArrowRight className="w-3 h-3 text-text-muted/50" />
                <span className="text-text-main truncate flex-1" title={ph.model}>{ph.model}</span>
                <span className="text-[10px] text-text-muted font-mono shrink-0">
                  {Math.round(ph.n_ctx / 1024)}k · t{ph.temperature}
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <p className="text-xs text-text-muted py-2">{isFetching ? 'Planning…' : 'No plan available.'}</p>
      )}

      <div className="flex items-center gap-3">
        <button
          onClick={() => startMut.mutate()}
          disabled={noMachines || nothing || startMut.isPending || !plan}
          className="btn-primary text-xs disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
        >
          <Sparkles className="w-3.5 h-3.5" />
          {startMut.isPending ? 'Starting…' : `Start ${PROFILE_META[profile].label} translate`}
        </button>
        {noMachines && (
          <span className="text-[11px] text-warning">Select a translation machine first.</span>
        )}
      </div>
    </div>
  )
}
