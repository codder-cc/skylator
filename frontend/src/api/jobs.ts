import { apiFetch, apiPost } from './client'
import type { Job } from '@/types'

export const jobsApi = {
  list: () =>
    apiFetch<Job[]>('/api/jobs'),

  get: (id: string) =>
    apiFetch<Job>(`/api/jobs/${id}`),

  getLogs: (id: string, since = 0) =>
    apiFetch<{ lines: string[]; total: number }>(`/api/jobs/${id}/logs?since=${since}`),

  create: (body: Record<string, unknown>) =>
    apiPost<{ ok: boolean; job_id: string }>('/jobs/create', body),

  cancel: (id: string) =>
    apiPost<{ ok: boolean }>(`/jobs/${id}/cancel`),

  pause: (id: string) =>
    apiPost<{ ok: boolean }>(`/jobs/${id}/pause`),

  resume: (id: string) =>
    apiPost<{ ok: boolean; job_id: string }>(`/jobs/${id}/resume`),

  retry: (id: string) =>
    apiPost<{ ok: boolean; job_id: string }>(`/jobs/${id}/retry`),

  assign: (id: string, machines: string[]) =>
    apiPost<{ ok: boolean; resumed: boolean; job_id?: string }>(`/jobs/${id}/assign`, { machines }),

  unassign: (id: string, machines: string[]) =>
    apiPost<{ ok: boolean }>(`/jobs/${id}/unassign`, { machines }),

  clear: () =>
    apiPost<{ ok: boolean }>('/jobs/clear'),

  dispatchBack: (id: string) =>
    apiPost<{ ok: boolean; warnings?: string[] }>(`/jobs/${id}/dispatch-back`),

  dispatchOffline: (id: string, machines?: string[]) =>
    apiPost<{ ok: boolean; job_id: string }>(
      `/jobs/${id}/dispatch-offline`,
      machines ? { machines } : {},
    ),

  // Phase 8 — partial results: live funnel + "deploy what we have"
  tally: (id: string) =>
    apiFetch<JobTally>(`/jobs/${id}/tally`),

  collect: (id: string) =>
    apiPost<{ ok: boolean; applied_jobs: { mod: string; job_id: string }[] }>(
      `/jobs/${id}/collect`,
    ),

  // B2 — pull the done translations as JSON (partial export, no deploy).
  export: (id: string) =>
    apiFetch<{ job_id: string; count: number; strings: Record<string, unknown>[] }>(
      `/jobs/${id}/export`,
    ),
}

export interface JobTally {
  job_id: string
  status: string
  assigned: number
  delivered: number
  translated: number
  pending: number
  needs_review: number
  mods: string[]
  // UID2 — where the delivered translations came from (ai/cache/dispatch/consensus/dict…)
  source_counts?: Record<string, number>
}
