import { apiFetch, apiPost } from './client'

export interface ModelEstimate {
  n_ctx: number
  weights_mb: number
  kv_cache_mb: number
  overhead_mb: number
  total_mb: number
  approx: boolean
  vram_mb?: number
  fit?: 'full' | 'tight' | 'no' | 'unknown'
  headroom_mb?: number
  max_n_ctx?: number
}

export interface CatalogModel {
  id: string
  name: string
  backend: string
  repo_id: string
  gguf_filename: string
  params_b: number
  file_size_mb: number
  n_layers: number
  n_kv_heads: number
  head_dim: number
  default_n_ctx: number
  max_n_ctx: number
  notes: string
  estimate: ModelEstimate
}

export interface DispatchResult {
  ok: boolean
  dispatched: { label: string; ok: boolean; chunk_id?: string; error?: string }[]
}

export const modelsApi = {
  // Curated catalog enriched with a per-agent fit verdict when vramMb is given.
  catalog: (vramMb = 0) =>
    apiFetch<{ models: CatalogModel[] }>(`/api/models/catalog?vram_mb=${vramMb}`).then((d) => d.models),

  estimate: (params: Record<string, string | number>) => {
    const q = new URLSearchParams(
      Object.entries(params).map(([k, v]) => [k, String(v)]),
    ).toString()
    return apiFetch<ModelEstimate>(`/api/models/estimate?${q}`)
  },

  // Fan a download/load out to many agents at once.
  dispatch: (body: { model: Record<string, unknown>; targets: string[] | 'all'; load?: boolean }) =>
    apiPost<DispatchResult>('/api/models/dispatch', body),
}
