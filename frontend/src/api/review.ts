import { apiFetch, apiPost } from './client'

export interface ReviewIssue {
  kind: 'glossary' | 'tokens' | 'untranslated' | 'markup' | 'low_score'
  detail: string
  term?: string
  expected?: string
}

export interface ReviewRow {
  id: number
  mod_name: string
  esp_name: string
  key: string
  original: string
  translation: string
  quality_score: number | null
  source: string | null
  /** Why this string is in the queue — a dropped token is not the same problem as a
   *  glossary slip, and the reviewer decides differently for each. */
  issues: ReviewIssue[]
}

export interface ReviewTermCount {
  term: string
  expected: string
  count: number
}

export const reviewApi = {
  // needs_review strings across the whole pack, worst-quality first.
  queue: (params?: { mod?: string; max_quality?: number; limit?: number; term?: string }) => {
    const q = new URLSearchParams()
    if (params?.mod) q.set('mod', params.mod)
    if (params?.max_quality != null) q.set('max_quality', String(params.max_quality))
    if (params?.limit != null) q.set('limit', String(params.limit))
    if (params?.term) q.set('term', params.term)
    const qs = q.toString()
    return apiFetch<{ total: number; strings: ReviewRow[]; terms: ReviewTermCount[] }>(
      `/api/review/queue${qs ? `?${qs}` : ''}`,
    )
  },

  approve: (ids: number[]) =>
    apiPost<{ ok: boolean; approved: number }>('/api/review/approve', { ids }),

  /** Send back to pending so the fleet translates it again. */
  reject: (ids: number[]) =>
    apiPost<{ ok: boolean; rejected: number }>('/api/review/reject', { ids }),

  /** Correct the text and accept it in one step. */
  edit: (id: number, translation: string) =>
    apiPost<{ ok: boolean }>('/api/review/edit', { id, translation }),
}
