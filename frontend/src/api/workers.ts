import { apiFetch, apiPost } from './client'
import type { WorkerInfo, SetupReport } from '@/types'

interface ModelLoadBody {
  model: string
  n_gpu_layers?: number
  context_size?: number
  [key: string]: unknown
}

interface LanServer {
  url: string
  label: string
  reachable: boolean
}

export const workersApi = {
  list: () =>
    apiFetch<WorkerInfo[]>('/api/workers'),

  register: (label: string, data: Record<string, unknown>) =>
    apiPost<{ ok: boolean }>(`/api/workers/${encodeURIComponent(label)}/register`, data),

  getModels: (label: string) =>
    apiFetch<{ models: string[] }>(`/api/workers/${encodeURIComponent(label)}/models`),

  loadModel: (label: string, body: ModelLoadBody) =>
    apiPost<{ ok: boolean; job_id?: string }>(
      `/api/workers/${encodeURIComponent(label)}/model/load`,
      body,
    ),

  unloadModel: (label: string) =>
    apiPost<{ ok: boolean }>(
      `/api/workers/${encodeURIComponent(label)}/model/unload`,
    ),

  scanLan: () =>
    apiPost<{ servers: LanServer[] }>('/servers/scan'),

  getServers: () =>
    apiFetch<LanServer[]>('/api/servers'),

  getSetupReports: () =>
    apiFetch<SetupReport[]>('/api/setup-reports'),

  clearSetupReports: () =>
    apiPost<{ ok: boolean }>('/api/setup-reports/clear'),
}
