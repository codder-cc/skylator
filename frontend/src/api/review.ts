import { apiFetch, apiPost } from './client'

export interface ReviewRow {
  id: number
  mod_name: string
  esp_name: string
  key: string
  original: string
  translation: string
  quality_score: number | null
  source: string | null
}

export const reviewApi = {
  // G11 — needs_review strings across the whole pack, worst-quality first.
  queue: (params?: { mod?: string; max_quality?: number; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.mod) q.set('mod', params.mod)
    if (params?.max_quality != null) q.set('max_quality', String(params.max_quality))
    if (params?.limit != null) q.set('limit', String(params.limit))
    const qs = q.toString()
    return apiFetch<{ total: number; strings: ReviewRow[] }>(`/api/review/queue${qs ? `?${qs}` : ''}`)
  },

  approve: (ids: number[]) =>
    apiPost<{ ok: boolean; approved: number }>('/api/review/approve', { ids }),
}
