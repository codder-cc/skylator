import { apiFetch, apiPost } from './client'
import type { ModInfo, StringEntry, ReservationInfo, StringHistoryEntry } from '@/types'

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
  sort_by?: string
  sort_dir?: string
  rec_type?: string
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
    if (params.sort_by) qs.set('sort_by', params.sort_by)
    if (params.sort_dir) qs.set('sort_dir', params.sort_dir)
    if (params.rec_type) qs.set('rec_type', params.rec_type)
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

  fixUntranslatable: (name: string) =>
    apiPost<{ ok: boolean; fixed: number }>(
      `/api/mods/${encodeURIComponent(name)}/fix-untranslatable`,
    ),

  resetTranslations: (name: string) =>
    apiPost<{ ok: boolean; reset: number }>(
      `/api/mods/${encodeURIComponent(name)}/reset-translations`,
    ),

  getReservations: (name: string) =>
    apiFetch<{ reservations: ReservationInfo[] }>(
      `/api/mods/${encodeURIComponent(name)}/reservations`,
    ),

  getStringHistory: (stringId: number) =>
    apiFetch<{ history: StringHistoryEntry[] }>(
      `/api/strings/${stringId}/history`,
    ),

  approveString: (stringId: number) =>
    apiPost<{ ok: boolean; quality_score: number | null }>(
      `/api/strings/${stringId}/approve`,
    ),

  approveBulk: (modName: string, ids: number[]) =>
    apiPost<{ ok: boolean; approved: number }>(
      `/api/mods/${encodeURIComponent(modName)}/strings/approve-bulk`,
      { ids },
    ),

  getConflicts: (modName: string) =>
    apiFetch<{ original: string; translations: string; variant_count: number; occurrence_count: number }[]>(
      `/api/mods/${encodeURIComponent(modName)}/strings/conflicts`,
    ),

  resolveConflict: (modName: string, original: string, translation: string) =>
    apiPost<{ ok: boolean; updated: number }>(
      `/api/mods/${encodeURIComponent(modName)}/strings/resolve-conflict`,
      { original, translation },
    ),

  getRecTypes: (name: string) =>
    apiFetch<{ rec_types: string[] }>(
      `/mods/${encodeURIComponent(name)}/rec_types`,
      { headers: { Accept: 'application/json' } },
    ),

  replaceStrings: (
    name: string,
    body: { find: string; replace: string; esp?: string; scope?: string },
  ) => apiPost<{ ok: boolean; count: number }>(
    `/mods/${encodeURIComponent(name)}/strings/replace`,
    body,
  ),

  syncDuplicates: (
    name: string,
    body: { original: string; translation: string; status: string; quality_score: number | null },
  ) => apiPost<{ ok: boolean; count: number }>(
    `/mods/${encodeURIComponent(name)}/strings/sync-duplicates`,
    body,
  ),
}
