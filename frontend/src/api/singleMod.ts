import { apiFetch } from './client'

export interface SingleModSession {
  session_id: string
  mod_name: string
  zip_name: string
  total: number
  esp_files: Array<{ esp: string; count: number; error?: string }>
}

export interface SingleStringsParams {
  esp?: string
  status?: string
  q?: string
  scope?: string
  limit?: number
  offset?: number
}

export interface SingleStringsResponse {
  strings: import('@/types').StringEntry[]
  total: number
}

export const singleModApi = {
  upload: async (file: File): Promise<SingleModSession> => {
    const fd = new FormData()
    fd.append('file', file)
    const r = await fetch('/api/single-mod/upload', { method: 'POST', body: fd })
    const json = await r.json()
    if (!r.ok) throw new Error(json.error ?? 'Upload failed')
    return json as SingleModSession
  },

  getStrings: (sessionId: string, params: SingleStringsParams = {}) => {
    const qs = new URLSearchParams()
    if (params.esp)    qs.set('esp',    params.esp)
    if (params.status) qs.set('status', params.status)
    if (params.q)      qs.set('q',      params.q)
    if (params.scope)  qs.set('scope',  params.scope)
    if (params.limit  != null) qs.set('limit',  String(params.limit))
    if (params.offset != null) qs.set('offset', String(params.offset))
    const query = qs.toString()
    return apiFetch<SingleStringsResponse>(
      `/api/single-mod/${encodeURIComponent(sessionId)}/strings${query ? `?${query}` : ''}`,
    )
  },

  updateString: (
    sessionId: string,
    payload: { key: string; esp: string; translation: string },
  ) =>
    apiFetch<{ ok: boolean; quality_score: number | null; status: string | null }>(
      `/api/single-mod/${encodeURIComponent(sessionId)}/strings/update`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),

  translateOne: (
    sessionId: string,
    payload: { key: string; esp: string; original: string; machines?: string[]; force_ai?: boolean },
  ) =>
    apiFetch<{
      ok: boolean
      translation: string
      quality_score: number | null
      status: string
      token_issues?: string[]
      from_dict?: boolean
      logs?: string[]
      error?: string
    }>(
      `/api/single-mod/${encodeURIComponent(sessionId)}/strings/translate-one`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),

  translateBulk: (sessionId: string, payload: { machines?: string[] } = {}) =>
    apiFetch<{ ok: boolean; job_id: string }>(
      `/api/single-mod/${encodeURIComponent(sessionId)}/translate`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),

  deleteSession: (sessionId: string) =>
    apiFetch<{ ok: boolean }>(
      `/api/single-mod/${encodeURIComponent(sessionId)}`,
      { method: 'DELETE' },
    ),

  downloadUrl: (sessionId: string) =>
    `/api/single-mod/${encodeURIComponent(sessionId)}/download`,
}
