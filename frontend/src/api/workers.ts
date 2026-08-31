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

export interface ModelDefault {
  spec: {
    repo_id?: string
    gguf_filename?: string
    model_path?: string
    backend_type?: string
    n_ctx?: number
    [key: string]: unknown
  }
  suspended: boolean
  updated_at: number
}

/** When a machine may translate. Days are Mon=0 … Sun=6; a window whose start is later
 *  than its end runs through midnight and belongs to the day it started on. Times are
 *  the machine's own local clock. */
export interface ScheduleWindow {
  days: number[]
  start: string
  end: string
}

export interface AgentSchedule {
  /** always — work whenever there is work.
   *  paused — stop after the current batch.
   *  schedule — the windows are the ONLY hours it may work.
   *  busy — the windows are hours it may NOT work; free the rest of the time. */
  mode: 'always' | 'paused' | 'schedule' | 'busy'
  windows: ScheduleWindow[]
}

export interface ScheduleState {
  schedule: AgentSchedule
  working: boolean
  summary: string
  next_change: string | null
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

  // A4 — download/stage a model on the worker WITHOUT loading it into VRAM (pre-provision).
  downloadModel: (label: string, body: ModelLoadBody) =>
    apiPost<{ ok: boolean; downloaded?: boolean; path?: string }>(
      `/api/workers/${encodeURIComponent(label)}/model/download`,
      body,
    ),

  unloadModel: (label: string) =>
    apiPost<{ ok: boolean }>(
      `/api/workers/${encodeURIComponent(label)}/model/unload`,
    ),

  // Durable per-agent default models — auto-restored when an agent comes up empty.
  getModelDefaults: () =>
    apiFetch<{ defaults: Record<string, ModelDefault> }>('/api/workers/model-defaults'),

  clearModelDefault: (label: string) =>
    apiFetch<{ ok: boolean }>(
      `/api/workers/${encodeURIComponent(label)}/model/default`,
      { method: 'DELETE' },
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

  // Working hours. The host stores them and hands them to the agent on its next poll;
  // the agent is what actually enforces them, so a pause holds even if the host dies.
  getSchedules: () =>
    apiFetch<Record<string, ScheduleState>>('/api/workers/schedules'),

  setSchedule: (label: string, schedule: AgentSchedule) =>
    apiPost<ScheduleState>(
      `/api/workers/${encodeURIComponent(label)}/schedule`,
      schedule as unknown as Record<string, unknown>,
    ),

  // Cancel an offline job directly on a worker — works when the host job is gone.
  cancelOfflineJob: (label: string, offlineJobId: string) =>
    apiPost<{ ok: boolean; ack: boolean }>(
      `/api/workers/${encodeURIComponent(label)}/cancel-offline`,
      { offline_job_id: offlineJobId },
    ),
}
