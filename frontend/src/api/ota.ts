import { apiFetch, apiPost } from './client'

export interface OtaStatus {
  branch: string
  commit: string
  behind: number
  pending_commits: string[]
}

export interface OtaUpdateResult {
  ok: boolean
  steps: { step: string; ok: boolean; output: string }[]
  restarting?: boolean
  error?: string
}

export const otaApi = {
  status: () => apiFetch<OtaStatus>('/api/ota/status'),
  update: () => apiPost<OtaUpdateResult>('/api/ota/update', {}),
  hostCommit: () => apiFetch<{ commit: string }>('/api/ota/host-commit'),
}
