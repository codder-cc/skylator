import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ClipboardCheck, CheckCheck } from 'lucide-react'
import { reviewApi } from '@/api/review'
import { cn } from '@/lib/utils'

// ── Pack-wide QA review queue (G11) ──────────────────────────────────────────
function qualityClass(q: number | null) {
  if (q == null) return 'text-text-muted'
  if (q >= 80) return 'text-success'
  if (q >= 50) return 'text-warning'
  return 'text-danger'
}

function ReviewPage() {
  const qc = useQueryClient()
  const [maxQ, setMaxQ] = useState<number | ''>('')
  const [selected, setSelected] = useState<Set<number>>(new Set())

  const { data } = useQuery({
    queryKey: ['review', maxQ],
    queryFn: () => reviewApi.queue({ max_quality: maxQ === '' ? undefined : maxQ, limit: 300 }),
    refetchInterval: 10000,
  })
  const approveMut = useMutation({
    mutationFn: (ids: number[]) => reviewApi.approve(ids),
    onSuccess: () => { setSelected(new Set()); qc.invalidateQueries({ queryKey: ['review'] }) },
  })

  const rows = data?.strings ?? []
  const toggle = (id: number) =>
    setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <ClipboardCheck className="w-5 h-5 text-accent" />
        <h1 className="text-2xl font-bold text-text-main">Review queue</h1>
        <span className="text-sm text-text-muted">{data?.total ?? 0} need review (pack-wide)</span>
        <div className="ml-auto flex items-center gap-2">
          <label className="text-xs text-text-muted">max quality</label>
          <input
            type="number" value={maxQ}
            onChange={(e) => setMaxQ(e.target.value === '' ? '' : Number(e.target.value))}
            placeholder="any" className="w-20 px-2 py-1 rounded text-sm bg-bg-base border border-border-subtle"
          />
          <button
            onClick={() => approveMut.mutate(Array.from(selected))}
            disabled={selected.size === 0 || approveMut.isPending}
            className="flex items-center gap-1 px-3 py-1.5 rounded text-sm font-medium bg-success/20 text-success border border-success/30 hover:bg-success/30 disabled:opacity-50"
          >
            <CheckCheck className="w-4 h-4" />Approve {selected.size || ''}
          </button>
        </div>
      </div>

      <div className="card divide-y divide-border-subtle">
        {rows.length === 0 && (
          <div className="p-6 text-center text-text-muted text-sm">Nothing to review 🎉</div>
        )}
        {rows.map((r) => (
          <div key={r.id} className="px-4 py-2 flex gap-3 items-start hover:bg-bg-card2/30">
            <input type="checkbox" className="mt-1" checked={selected.has(r.id)} onChange={() => toggle(r.id)} />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 text-[10px] text-text-muted mb-1">
                <span className="font-mono truncate max-w-[16rem]" title={r.mod_name}>{r.mod_name}</span>
                <span className="font-mono">{r.esp_name}</span>
                <span className={cn('ml-auto font-mono', qualityClass(r.quality_score))}>
                  q{r.quality_score ?? '—'}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div className="text-text-muted whitespace-pre-wrap break-words">{r.original}</div>
                <div className="text-text-main whitespace-pre-wrap break-words">{r.translation}</div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export const Route = createFileRoute('/review')({ component: ReviewPage })
