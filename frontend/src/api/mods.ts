import { apiFetch, apiPost } from './client'
import type { ModInfo, StringEntry } from '@/types'

interface ModsListParams {
  status?: string
  q?: string
}

interface ModStringsParams {
  scope?: string
  status?: string
  q?: string
  page?: number
  per?: number
}

interface ModStringsResponse {
  strings: StringEntry[]
  total: number
  page: number
  per: number
  pages: number
  scope_counts?: Record<string, number>
}

export const modsApi = {
  list: (params?: ModsListParams) => {
    const qs = new URLSearchParams()
    if (params?.status) qs.set('status', params.status)
    if (params?.q) qs.set('q', params.q)
    const query = qs.toString()
    return apiFetch<ModInfo[]>(`/api/mods${query ? `?${query}` : ''}`)
  },

  get: (name: string) =>
    apiFetch<ModInfo>(`/api/mods/${encodeURIComponent(name)}`),

  getStrings: (name: string, params: ModStringsParams) => {
    const qs = new URLSearchParams()
    if (params.scope && params.scope !== 'all') qs.set('scope', params.scope)
    if (params.status) qs.set('status', params.status)
    if (params.q) qs.set('q', params.q)
    if (params.page !== undefined) qs.set('page', String(params.page))
    if (params.per !== undefined) qs.set('per', String(params.per))
    const query = qs.toString()
    return apiFetch<ModStringsResponse>(
      `/mods/${encodeURIComponent(name)}/strings${query ? `?${query}` : ''}`,
      { headers: { Accept: 'application/json' } },
    )
  },

  updateString: (
    name: string,
    body: { key: string; esp: string; translation: string },
  ) => apiPost(`/mods/${encodeURIComponent(name)}/strings/update`, body),

  translateOne: (
    name: string,
    body: {
      key: string
      esp: string
      original: string
      force_ai: boolean
      params?: Record<string, unknown>
      machines?: string[]
    },
  ) => apiPost(`/api/mods/${encodeURIComponent(name)}/strings/translate-one`, body),

  getContext: (name: string, force = false) =>
    apiFetch<{ ok: boolean; context: string; auto_context?: string; from_cache?: boolean }>(
      `/api/mods/${encodeURIComponent(name)}/context${force ? '?force=1' : ''}`,
    ),

  saveContext: (name: string, context: string) =>
    apiPost(`/api/mods/${encodeURIComponent(name)}/context`, { context }),

  getValidation: (name: string) =>
    apiFetch<{ ok: boolean; error?: string; [key: string]: unknown }>(
      `/api/mods/${encodeURIComponent(name)}/validation`,
    ),

  getNexusRaw: (name: string) =>
    apiFetch<{ ok: boolean; mod_id?: number; name?: string; description?: string; fetched_at?: number; age_hours?: number; error?: string }>(
      `/api/mods/${encodeURIComponent(name)}/nexus`,
    ),

  fetchNexus: (name: string) =>
    apiPost<{ ok: boolean; mod_id?: number; name?: string; description?: string; error?: string }>(
      `/api/mods/${encodeURIComponent(name)}/nexus/fetch`,
    ),
}
