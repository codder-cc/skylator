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

      // Write into TanStack Query cache
      queryClient.setQueryData(QK.job(jobId), job)

      // Also update in jobs list cache if present
      queryClient.setQueryData<Job[]>(QK.jobs(), (old) => {
        if (!old) return old
        const idx = old.findIndex((j) => j.id === jobId)
        if (idx === -1) return [...old, job]
        const next = [...old]
        next[idx] = job
        return next
      })

      // Stop streaming when terminal status reached
      if (JOB_TERMINAL_STATUSES.includes(job.status as (typeof JOB_TERMINAL_STATUSES)[number])) {
        // Invalidate to trigger a fresh fetch for final state
        void queryClient.invalidateQueries({ queryKey: QK.job(jobId) })
      }
    },
    enabled,
  )
}
