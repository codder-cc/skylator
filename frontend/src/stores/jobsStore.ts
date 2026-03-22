import { create } from 'zustand'
import type { Job } from '@/types'

interface JobsState {
  jobs: Record<string, Job>
  upsertJob: (job: Job) => void
  removeJob: (id: string) => void
  setJobs: (jobs: Job[]) => void
  clear: () => void
}

export const useJobsStore = create<JobsState>()((set) => ({
  jobs: {},

  upsertJob: (job) =>
    set((state) => ({
      jobs: { ...state.jobs, [job.id]: job },
    })),

  removeJob: (id) =>
    set((state) => {
      const next = { ...state.jobs }
      delete next[id]
      return { jobs: next }
    }),

  setJobs: (jobs) =>
    set({
      jobs: Object.fromEntries(jobs.map((j) => [j.id, j])),
    }),

  clear: () => set({ jobs: {} }),
}))
