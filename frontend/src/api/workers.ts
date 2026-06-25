import { apiFetch, apiPost } from './client'
import type { WorkerInfo, SetupReport, CachedModel, BenchmarkResult, AssignmentsOverview } from '@/types'

interface ModelLoadBody {
  model: string
  backend_type?: string
  n_gpu_layers?: number
  n_ctx?: number
  batch_size?: number
  max_new_tokens?: number
  repo_id?: string
  gguf_filename?: string
  model_path?: string
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
    apiFetch<{ models: CachedModel[] }>(`/api/workers/${encodeURIComponent(label)}/models`),

  loadModel: (label: string, body: ModelLoadBody) =>
    apiPost<{ ok: boolean; job_id?: string }>(
      `/api/workers/${encodeURIComponent(label)}/model/load`,
      body,
    ),

  unloadModel: (label: string) =>
    apiPost<{ ok: boolean }>(
      `/api/workers/${encodeURIComponent(label)}/model/unload`,
    ),

  benchmark: (label: string) =>
    apiPost<BenchmarkResult>(`/api/workers/${encodeURIComponent(label)}/benchmark`, {}),

  scanLan: () =>
    apiPost<{ servers: LanServer[] }>('/servers/scan'),

  getServers: () =>
    apiFetch<{ servers: LanServer[]; scanning: boolean }>('/api/servers')
      .then((d) => d.servers ?? []),

  getSetupReports: () =>
    apiFetch<SetupReport[]>('/api/setup-reports'),

  clearSetupReports: () =>
    apiPost<{ ok: boolean }>('/api/setup-reports/clear'),

  requestOtaUpdate: (label: string) =>
    apiPost<{ ok: boolean; chunk_id?: string }>(
      `/api/workers/${encodeURIComponent(label)}/ota-update`,
    ),

  // Phase 7 — operator action: immediately orphan a dead agent's active assignments so
  // its undelivered strings become reassignable (instead of waiting the multi-day horizon).
  abandon: (label: string) =>
    apiPost<{ ok: boolean; orphaned: string[]; reassignable: number }>(
      `/api/workers/${encodeURIComponent(label)}/abandon`,
    ),

  // Gap 4 — fleet observability: per-assignment funnel + liveness tiers + aggregate.
  assignments: () =>
    apiFetch<AssignmentsOverview>('/api/assignments'),
}
