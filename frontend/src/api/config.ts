import { apiFetch } from './client'

export const configApi = {
  getRaw: () =>
    apiFetch<string>('/config/raw', {
      headers: { Accept: 'text/plain' },
    }),

  save: (yaml: string) =>
    apiFetch<{ ok: boolean }>('/config/save', {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: yaml,
    }),

  validate: (yaml: string) =>
    apiFetch<{ ok: boolean; errors?: string[] }>('/config/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: yaml,
    }),
}
