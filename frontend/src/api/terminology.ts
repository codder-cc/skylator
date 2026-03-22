import { apiFetch, apiPost } from './client'

export const termsApi = {
  get: () =>
    apiFetch<Record<string, string>>('/terminology/', {
      headers: { Accept: 'application/json' },
    }),

  save: (terms: Record<string, string>) =>
    apiPost<{ ok: boolean }>('/terminology/save', { terms }),

  add: (en: string, ru: string) =>
    apiPost<{ ok: boolean }>('/terminology/add', { en, ru }),

  delete: (en: string) =>
    apiPost<{ ok: boolean }>('/terminology/delete', { en }),
}
