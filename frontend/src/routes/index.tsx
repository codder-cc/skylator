import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { QK } from '@/lib/queryKeys'
import { statsApi } from '@/api/stats'
import { StatCard } from '@/components/shared/StatCard'
import { GpuWidget } from '@/components/shared/GpuWidget'
import { pctColor } from '@/lib/utils'
import {
  Layers,
  CheckCircle,
  Clock,
  AlertCircle,
  Type,
} from 'lucide-react'

function DashboardPage() {
  const { data: stats } = useQuery({
    queryKey: QK.stats(),
    queryFn: statsApi.get,
    refetchInterval: 15_000,
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-text-main">Dashboard</h1>
        <GpuWidget />
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

      {!stats && (
        <div className="card p-8 text-center text-text-muted">
          Loading statistics...
        </div>
      )}
    </div>
  )
}

export const Route = createFileRoute('/')({
  component: DashboardPage,
})
