import { useQuery } from '@tanstack/react-query'
import { useMachinesStore } from '@/stores/machinesStore'
import { workersApi } from '@/api/workers'
import { QK } from '@/lib/queryKeys'

/**
 * Returns the resolved list of machine labels based on the current mode.
 *
 * - 'local': uses only the local worker (label 'local')
 * - 'smart': uses all alive workers
 * - 'custom': uses only the labels explicitly selected in the store
 */
export function useMachines(): string[] {
  const { mode, custom } = useMachinesStore()

  const { data: workers = [] } = useQuery({
    queryKey: QK.workers(),
    queryFn: workersApi.list,
    staleTime: 30_000,
  })

  if (mode === 'local') {
    return ['local']
  }

  if (mode === 'smart') {
    const alive = workers.filter((w) => w.alive).map((w) => w.label)
    return alive.length > 0 ? alive : ['local']
  }

  // 'custom' mode — return only selected labels that are still alive
  const aliveLabels = new Set(workers.filter((w) => w.alive).map((w) => w.label))
  const filtered = custom.filter((l) => aliveLabels.has(l))
  return filtered.length > 0 ? filtered : ['local']
}
