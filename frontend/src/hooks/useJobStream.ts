import { useQueryClient } from '@tanstack/react-query'
import { useSSE } from './useSSE'
import { QK } from '@/lib/queryKeys'
import { JOB_TERMINAL_STATUSES } from '@/lib/constants'
import type { Job } from '@/types'

export function useJobStream(jobId: string, enabled: boolean): void {
  const queryClient = useQueryClient()

  useSSE(
    `/jobs/${jobId}/stream`,
    (data) => {
      let job: Job
      try {
        job = JSON.parse(data) as Job
      } catch {
        return
      }

      // Write full job into TanStack Query cache
      queryClient.setQueryData(QK.job(jobId), job)

      // Update jobs list
      queryClient.setQueryData<Job[]>(QK.jobs(), (old) => {
        if (!old) return old
        const idx = old.findIndex((j) => j.id === jobId)
        if (idx === -1) return [...old, job]
        const next = [...old]
        next[idx] = job
        return next
      })

      // On terminal status: invalidate job + mod data so final state is refetched
      // (modLiveUpdates and jobs list are handled by the global stream in __root.tsx)
      if (JOB_TERMINAL_STATUSES.includes(job.status as (typeof JOB_TERMINAL_STATUSES)[number])) {
        const modName = job.mod_name || (job.params?.mod_name as string | undefined) || ''
        void queryClient.invalidateQueries({ queryKey: QK.job(jobId) })
        void queryClient.invalidateQueries({ queryKey: QK.stats() })
        void queryClient.invalidateQueries({ queryKey: QK.mods() })
        if (modName) {
          void queryClient.invalidateQueries({ queryKey: QK.mod(modName) })
          void queryClient.invalidateQueries({ queryKey: ['mods', modName, 'strings'] })
          void queryClient.invalidateQueries({ queryKey: QK.modReservations(modName) })
        }
      }
    },
    enabled,
  )
}
