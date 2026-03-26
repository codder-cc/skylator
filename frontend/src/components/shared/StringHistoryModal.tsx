import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { modsApi } from '@/api/mods'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/utils'
import { SourceBadge } from './SourceBadge'
import type { StringHistoryEntry } from '@/types'

interface StringHistoryModalProps {
  stringId: number
  stringKey: string
  onClose: () => void
}

function ScoreChip({ score }: { score: number | null }) {
  if (score === null) return <span className="text-text-muted/40 text-xs">—</span>
  const cls =
    score >= 80 ? 'text-success' : score >= 50 ? 'text-warning' : 'text-danger'
  return <span className={cn('font-mono text-xs tabular-nums', cls)}>{score}</span>
}

export function StringHistoryModal({ stringId, stringKey, onClose }: StringHistoryModalProps) {
  const { data, isLoading } = useQuery({
    queryKey: QK.stringHistory(stringId),
    queryFn: () => modsApi.getStringHistory(stringId),
  })

  const history: StringHistoryEntry[] = data?.history ?? []

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-bg-card border border-border-subtle rounded-xl shadow-2xl w-full max-w-2xl max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border-subtle shrink-0">
          <div>
            <h2 className="text-sm font-semibold text-text-main">Translation History</h2>
            <p className="text-xs text-text-muted font-mono truncate max-w-[400px]" title={stringKey}>
              {stringKey}
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-md text-text-muted hover:text-text-main hover:bg-bg-card2 transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1 px-4 py-3">
          {isLoading ? (
            <div className="text-center text-text-muted text-sm py-8">Loading…</div>
          ) : history.length === 0 ? (
            <div className="text-center text-text-muted text-sm py-8">No history recorded yet.</div>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-text-muted/60 uppercase text-[10px] tracking-wide border-b border-border-subtle">
                  <th className="text-left px-2 py-1.5">When</th>
                  <th className="text-left px-2 py-1.5">Source</th>
                  <th className="text-left px-2 py-1.5">Status</th>
                  <th className="text-right px-2 py-1.5">Score</th>
                  <th className="text-left px-2 py-1.5">Translation</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => {
                  const date = new Date(h.created_at * 1000)
                  const ts = date.toLocaleString()
                  return (
                    <tr key={h.id} className="border-t border-border-subtle hover:bg-bg-card2/30">
                      <td className="px-2 py-2 text-text-muted/70 whitespace-nowrap" title={ts}>
                        {ts}
                      </td>
                      <td className="px-2 py-2">
                        <SourceBadge source={h.source} />
                      </td>
                      <td className="px-2 py-2 text-text-muted capitalize">
                        {h.status?.replace('_', ' ') ?? '—'}
                      </td>
                      <td className="px-2 py-2 text-right">
                        <ScoreChip score={h.quality_score} />
                      </td>
                      <td className="px-2 py-2 text-text-main max-w-[300px]">
                        <div className="max-h-16 overflow-y-auto whitespace-pre-wrap leading-relaxed">
                          {h.translation || <span className="italic text-text-muted/40">empty</span>}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}
