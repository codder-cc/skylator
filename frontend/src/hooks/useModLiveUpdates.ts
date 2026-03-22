import { useQueryClient, useQuery } from '@tanstack/react-query'
import { QK } from '@/lib/queryKeys'
import type { StringUpdate } from '@/types'

/**
 * Returns live string updates for a specific mod from the shared stream-all cache.
 * Updates are written by useJobStream (called from jobs/$jobId.tsx or __root.tsx).
 */
export function useModLiveUpdates(modName: string): StringUpdate[] {
  const { data } = useQuery<StringUpdate[]>({
    queryKey: QK.modLiveUpdates(modName),
    queryFn: () => [],          // never fetches — only written by useJobStream
    staleTime: Infinity,
    gcTime: 5 * 60 * 1000,
  })
  return data ?? []
}

export function useClearModLiveUpdates(modName: string): () => void {
  const queryClient = useQueryClient()
  return () => {
    queryClient.setQueryData(QK.modLiveUpdates(modName), [])
  }
}
