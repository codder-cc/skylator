import { createRootRouteWithContext, Outlet } from '@tanstack/react-router'
import type { QueryClient } from '@tanstack/react-query'
import { Sidebar } from '@/components/layout/Sidebar'
import { Topbar } from '@/components/layout/Topbar'
import { ErrorBoundary } from '@/components/shared/ErrorBoundary'
import { useSSE } from '@/hooks/useSSE'
import { useQueryClient } from '@tanstack/react-query'
import { useJobsStore } from '@/stores/jobsStore'
import { QK } from '@/lib/queryKeys'
import { JOB_TERMINAL_STATUSES } from '@/lib/constants'
import type { Job, StringUpdate } from '@/types'
import { Toaster } from 'sonner'

interface RouterContext {
  queryClient: QueryClient
}

function AppShell() {
  const queryClient = useQueryClient()
  const upsertJob = useJobsStore((s) => s.upsertJob)

  useSSE('/api/jobs/stream-all', (data) => {
    let job: Job
    try {
      job = JSON.parse(data) as Job
    } catch {
      return
    }
    upsertJob(job)
    queryClient.setQueryData<Job[]>(QK.jobs(), (old) => {
      if (!old) return [job]
      const idx = old.findIndex((j) => j.id === job.id)
      if (idx === -1) return [...old, job]
      const next = [...old]
      next[idx] = job
      return next
    })
    queryClient.setQueryData(QK.job(job.id), job)

    // Propagate new string updates to the mod's live update cache
    const newUpdates: StringUpdate[] = job.new_string_updates ?? []
    const modName = job.mod_name || (job.params?.mod_name as string | undefined) || ''
    if (newUpdates.length > 0 && modName) {
      queryClient.setQueryData<StringUpdate[]>(
        QK.modLiveUpdates(modName),
        (old = []) => {
          const next = [...old, ...newUpdates]
          return next.length > 5000 ? next.slice(next.length - 5000) : next
        },
      )
    }

    // On terminal status: invalidate mod list, mod detail, stats, and mod strings
    if (JOB_TERMINAL_STATUSES.includes(job.status as (typeof JOB_TERMINAL_STATUSES)[number])) {
      void queryClient.invalidateQueries({ queryKey: QK.stats() })
      void queryClient.invalidateQueries({ queryKey: QK.mods() })
      if (modName) {
        void queryClient.invalidateQueries({ queryKey: QK.mod(modName) })
        void queryClient.invalidateQueries({ queryKey: ['mods', modName, 'strings'] })
        void queryClient.invalidateQueries({ queryKey: QK.modReservations(modName) })
      }
    }
  })

  return (
    <div className="flex h-screen bg-bg-base text-text-main overflow-hidden">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0">
        <Topbar />
        <main className="flex-1 overflow-auto p-6">
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
      <Toaster
        position="bottom-right"
        toastOptions={{
          style: {
            background: 'var(--color-bg-card)',
            border: '1px solid var(--color-border)',
            color: 'var(--color-text-main)',
          },
        }}
      />
    </div>
  )
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: AppShell,
})
