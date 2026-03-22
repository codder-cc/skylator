import { useQuery } from '@tanstack/react-query'
import { QK } from '@/lib/queryKeys'
import { statsApi } from '@/api/stats'
import { Cpu } from 'lucide-react'
import { cn } from '@/lib/utils'
import { humanSize } from '@/lib/utils'

export function GpuWidget() {
  const { data: gpu, isLoading } = useQuery({
    queryKey: QK.gpu(),
    queryFn: statsApi.gpu,
    refetchInterval: 5_000,
  })

  if (isLoading || !gpu) {
    return null
  }

  if (!gpu.available) {
    return (
      <div className="flex items-center gap-2 text-xs text-text-muted card px-3 py-2">
        <Cpu className="w-3.5 h-3.5" />
        <span>No GPU</span>
      </div>
    )
  }

  const pctColor =
    gpu.pct > 90 ? 'text-danger' : gpu.pct > 70 ? 'text-warning' : 'text-text-muted'

  const barColor =
    gpu.pct > 90 ? 'bg-danger' : gpu.pct > 70 ? 'bg-warning' : 'bg-accent'

  return (
    <div className="card px-3 py-2 flex items-center gap-3 text-xs">
      <Cpu className="w-3.5 h-3.5 text-accent shrink-0" />
      <div className="flex flex-col gap-1 min-w-[120px]">
        <div className="flex items-center justify-between gap-2">
          <span className="text-text-muted truncate max-w-[140px]">
            {gpu.name ?? 'GPU'}
          </span>
          <span className={cn('font-mono tabular-nums font-medium shrink-0', pctColor)}>
            {gpu.pct.toFixed(0)}%
          </span>
        </div>
        <div className="h-1.5 bg-bg-card2 rounded-full overflow-hidden">
          <div
            className={cn('h-full rounded-full transition-all', barColor)}
            style={{ width: `${gpu.pct}%` }}
          />
        </div>
        <div className="flex items-center justify-between text-text-muted/70">
          <span>{humanSize(gpu.used_mb * 1024 * 1024)} used</span>
          <span>{humanSize(gpu.total_mb * 1024 * 1024)} total</span>
        </div>
      </div>
    </div>
  )
}
