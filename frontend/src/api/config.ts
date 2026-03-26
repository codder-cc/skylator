import { apiFetch } from './client'

export const configApi = {
  getRaw: () =>
    apiFetch<{ yaml: string }>('/config/raw').then((r) => r.yaml),

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
