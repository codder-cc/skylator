import { createRootRouteWithContext, Outlet } from '@tanstack/react-router'
import type { QueryClient } from '@tanstack/react-query'
import { Sidebar } from '@/components/layout/Sidebar'
import { Topbar } from '@/components/layout/Topbar'
import { useSSE } from '@/hooks/useSSE'
import { useQueryClient } from '@tanstack/react-query'
import { useJobsStore } from '@/stores/jobsStore'
import { QK } from '@/lib/queryKeys'
import type { Job, StringUpdate } from '@/types'

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
  })

  return (
    <div className="flex h-screen bg-bg-base text-text-main overflow-hidden">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0">
        <Topbar />
        <main className="flex-1 overflow-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: AppShell,
})
