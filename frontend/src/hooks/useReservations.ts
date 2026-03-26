import { useQuery } from '@tanstack/react-query'
import { modsApi } from '@/api/mods'
import { QK } from '@/lib/queryKeys'
import type { Job } from '@/types'

/**
 * Polls /api/mods/<name>/reservations every 5s while any job for this mod is
 * active. Returns a Set of reserved string keys (not IDs — keys are what the
 * strings page rows identify by).
 */
export function useReservations(modName: string, jobs: Job[]): Set<string> {
  const hasActiveJob = jobs.some(
    (j) =>
      (j.status === 'running' || j.status === 'pending') &&
      (j.mod_name === modName || (j.params?.mod_name as string) === modName),
  )

  const { data } = useQuery({
    queryKey: QK.modReservations(modName),
    queryFn: () => modsApi.getReservations(modName),
    enabled: hasActiveJob,
    refetchInterval: hasActiveJob ? 5_000 : false,
    staleTime: 4_000,
  })

  if (!data?.reservations) return new Set()
  return new Set(data.reservations.map((r) => r.key))
}
