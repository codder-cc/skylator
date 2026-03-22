import { apiFetch } from './client'
import type { Stats, GpuInfo, TokenStats } from '@/types'

export const statsApi = {
  get:          () => apiFetch<Stats>('/api/stats'),
  gpu:          () => apiFetch<GpuInfo>('/api/gpu'),
  tokens:       () => apiFetch<TokenStats>('/api/tokens/stats'),
  resetTokens:  () => apiFetch<{ ok: boolean }>('/api/tokens/reset', { method: 'POST' }),
}
