import { apiFetch, apiPost } from './client'
import type { BackupEntry } from '@/types'

export const backupsApi = {
  list: () =>
    apiFetch<BackupEntry[]>('/backups/'),

  create: (modName: string, label?: string) =>
    apiPost<{ ok: boolean; backup_id: string }>('/backups/create', {
      mod_name: modName,
      label,
    }),

  restore: (id: string) =>
    apiPost<{ ok: boolean }>(`/backups/${encodeURIComponent(id)}/restore`),

  delete: (id: string) =>
    apiPost<{ ok: boolean }>(`/backups/${encodeURIComponent(id)}/delete`),

  download: (id: string) =>
    apiFetch<Blob>(`/backups/${encodeURIComponent(id)}/download`, {
      headers: { Accept: '*/*' },
    }),
}
