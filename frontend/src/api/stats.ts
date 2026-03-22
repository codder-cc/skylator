import { apiFetch } from './client'
import type { Stats, GpuInfo, TokenStats, TokenPerf } from '@/types'

export const statsApi = {
  get:          () => apiFetch<Stats>('/api/stats'),
  gpu:          () => apiFetch<GpuInfo>('/api/gpu'),
  tokens:       () => apiFetch<TokenStats>('/api/tokens/stats'),
  perf:         () => apiFetch<TokenPerf>('/api/tokens/perf'),
  resetTokens:  () => apiFetch<{ ok: boolean }>('/api/tokens/reset', { method: 'POST' }),
}
