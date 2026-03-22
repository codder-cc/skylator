import { useQuery } from '@tanstack/react-query'
import { useMachinesStore } from '@/stores/machinesStore'
import { workersApi } from '@/api/workers'
import { QK } from '@/lib/queryKeys'

/**
 * Returns the resolved list of machine labels based on the current mode.
 *
 * - 'smart': uses all alive registered workers
 * - 'custom': uses only the labels explicitly selected in the store
 *
 * Returns [] when no workers are available — callers should show a warning.
 */
export function useMachines(): string[] {
  const { mode, custom } = useMachinesStore()

  const { data: workers = [] } = useQuery({
    queryKey: QK.workers(),
    queryFn: workersApi.list,
    staleTime: 30_000,
  })

  if (mode === 'smart') {
    return workers.filter((w) => w.alive).map((w) => w.label)
  }

  // 'custom' mode — return only selected labels that are still alive
  const aliveLabels = new Set(workers.filter((w) => w.alive).map((w) => w.label))
  return custom.filter((l) => aliveLabels.has(l))
}
