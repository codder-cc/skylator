import { apiFetch, apiPost } from './client'

// ── account & browser session ─────────────────────────────────────────────────

export interface BrowserStatus {
  enabled?: boolean
  running?: boolean
  /** null when Chrome is not running — nobody asked it, so nobody knows. */
  logged_in?: boolean | null
  /** Whether a sign-in is needed: a live check, or an unexpired remembered one. */
  ready?: boolean
  headless?: boolean
  profile_dir?: string
  chrome_path?: string
  chrome_error?: string
  last_error?: string
  last_session?: { name?: string; user_id?: number | string; expires?: number; at?: number }
  reason?: string
  verified?: boolean
}

export interface NexusAccount {
  ok: boolean
  name?: string
  user_id?: number
  is_premium?: boolean
  is_supporter?: boolean
  game?: string
  link_mode?: string
  download_dir?: string
  uses_browser?: boolean
  /** The only field the UI should branch on: can downloads run without the user. */
  unattended?: boolean
  browser?: BrowserStatus
  rate_limit?: {
    hourly_remaining: number
    daily_remaining: number
    hourly_limit: number
    daily_limit: number
    exhausted: boolean
  }
}

// ── search ────────────────────────────────────────────────────────────────────

export interface ModHit {
  mod_id: number
  name: string
  summary: string
  author: string
  version: string
  status: string
  adult: boolean
  downloads: number
  endorsements: number
  category: string
  uploader: string
  game: string
  picture_url: string
  updated_at: string
  available: boolean
}

export interface TranslationHit extends ModHit {
  score: number
  name_overlap: number
  covers_title: boolean
  marked: boolean
  extra_words: number
}

export type SearchMode = 'stemmed' | 'exact' | 'contains'

export interface SearchResponse {
  ok: boolean
  query: string
  mode: SearchMode
  total: number
  count: number
  offset: number
  results: ModHit[]
}

// ── downloads ─────────────────────────────────────────────────────────────────

export interface DownloadItem {
  id: string
  state: 'queued' | 'resolving' | 'waiting_link' | 'downloading' | 'done' | 'skipped' | 'failed' | 'cancelled'
  name: string
  mod_id: number
  file_id: number | null
  file_name: string | null
  version: string | null
  size_bytes: number
  progress: { downloaded: number; total: number; pct: number; speed_bps: number; eta_seconds: number } | null
  result: { path: string; size_bytes: number; elapsed: number; skipped: boolean; mirror: string } | null
  error: string
  nexus_url: string | null
}

export interface DownloadSnapshot {
  items: DownloadItem[]
  counts: Record<string, number>
  total: number
  finished: number
  pct: number
  bytes_total: number
  bytes_done: number
  provider: string
  batch_id?: string
  dest_dir?: string
}

// ── translate from mod ────────────────────────────────────────────────────────

export type MergeAction = 'fill' | 'conflict' | 'same' | 'rejected' | 'unmatched'

export type AssetKind = 'esp' | 'mcm' | 'bsa-mcm' | 'swf'

export interface MergeCandidate {
  esp_name: string
  key: string
  action: MergeAction
  donor_text: string
  original: string
  current: string
  status: string
  match: 'exact' | 'local_id' | ''
  reason: string
  string_id: number | null
  kind: AssetKind
}

export interface TransferPlan {
  ok: boolean
  plan_id: string
  mod_name: string
  language: string
  donor_mod_id: number | null
  donor_name: string
  archive: string
  archive_bytes: number
  extracted: { files: number; bytes: number; tool: string }
  harvest: {
    plugin_count: number
    source_count: number
    string_count: number
    by_kind: Partial<Record<AssetKind, number>>
    failed: string[]
    plugins: Array<{ esp_name: string; name: string; kind: AssetKind; strings: number; localized: boolean; error: string }>
  }
  plan: {
    mod_name: string
    language: string
    donor_total: number
    our_total: number
    counts: Partial<Record<MergeAction, number>>
    counts_by_kind: Partial<Record<AssetKind, Partial<Record<MergeAction, number>>>>
    usable: number
    candidates: MergeCandidate[]
    truncated: number
  } | null
  applied: Record<string, unknown>
  cleaned: boolean
  elapsed: number
  error: string
  chosen_donor?: TranslationHit
}

export interface ApplyResult {
  ok: boolean
  mod: string
  applied: number
  skipped: number
  status: string
  overwrite: boolean
  dict_entries: number
  esps: string[]
}

const qs = (params: Record<string, string | number | boolean | undefined>) => {
  const s = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '' && v !== false) s.set(k, String(v))
  }
  const out = s.toString()
  return out ? `?${out}` : ''
}

export interface NexusSettings {
  ok: boolean
  download_dir: string
  donor_dir: string
  game: string
  link_mode: string
  max_concurrent: number
  browser_enabled: boolean
  browser_profile_dir: string
  archive_tool_path: string
}

export const nexusApi = {
  account: () => apiFetch<NexusAccount>('/api/nexus/account'),

  settings: () => apiFetch<NexusSettings>('/api/nexus/settings'),

  /** Persists into config.yaml and reloads it, so the value survives a restart. */
  saveSettings: (changes: Partial<Pick<NexusSettings, 'download_dir' | 'donor_dir' | 'link_mode' | 'max_concurrent' | 'archive_tool_path' | 'browser_enabled'>>) =>
    apiPost<{ ok: boolean; saved: Record<string, unknown>; download_dir: string }>(
      '/api/nexus/settings', changes,
    ),

  browser: (verify = false) =>
    apiFetch<BrowserStatus & { ok: boolean }>(`/api/nexus/browser${verify ? '?verify=1' : ''}`),

  /** Blocks server-side until somebody signs in, so give it a long window. */
  browserLogin: (waitSeconds = 600) =>
    apiPost<BrowserStatus & { ok: boolean }>('/api/nexus/browser/login', {
      wait_seconds: waitSeconds,
    }),

  browserClose: () => apiPost<{ ok: boolean; closed: boolean }>('/api/nexus/browser/close', {}),

  search: (params: {
    q: string
    mode?: SearchMode
    language?: string
    author?: string
    category?: string
    adult?: boolean
    count?: number
    offset?: number
  }) => apiFetch<SearchResponse>(`/api/nexus/search${qs(params)}`),

  random: (params?: { count?: number; min_downloads?: number; language?: string; seed?: number }) =>
    apiFetch<{ ok: boolean; count: number; results: ModHit[] }>(`/api/nexus/random${qs(params ?? {})}`),

  files: (modId: number) =>
    apiFetch<{ ok: boolean; mod_id: number; files: Array<{ file_id: number; name: string; file_name: string; version: string; category: string; size_bytes: number; is_primary: boolean }> }>(
      `/api/nexus/mods/${modId}/files`,
    ),

  download: (items: Array<{ mod_id: number; file_id?: number; label?: string }>, destDir?: string) =>
    apiPost<{ ok: boolean; batch_id: string; count: number; dest_dir: string }>(
      '/api/nexus/downloads',
      { items, dest_dir: destDir || undefined },
    ),

  batch: (batchId: string) => apiFetch<DownloadSnapshot & { ok: boolean }>(`/api/nexus/downloads/${batchId}`),

  cancelBatch: (batchId: string) =>
    apiPost<{ ok: boolean; cancelled: number }>(`/api/nexus/downloads/${batchId}/cancel`, {}),

  // ── translate from mod ──────────────────────────────────────────────────────

  donors: (mod: string, language = 'Russian', count = 10) =>
    apiFetch<{ ok: boolean; mod: string; language: string; count: number; results: TranslationHit[] }>(
      `/api/nexus/donors${qs({ mod, language, count })}`,
    ),

  transferPlan: (body: {
    mod: string
    donor_mod_id?: number
    donor_file_id?: number
    archive_path?: string
    language?: string
    keep_archive?: boolean
    sample?: number
  }) => apiPost<TransferPlan>('/api/nexus/transfer/plan', body),

  transferCandidates: (planId: string, action?: MergeAction, offset = 0, limit = 100) =>
    apiFetch<{ ok: boolean; total: number; offset: number; candidates: MergeCandidate[] }>(
      `/api/nexus/transfer/plan/${planId}${qs({ action, offset, limit })}`,
    ),

  transferApply: (body: {
    plan_id: string
    overwrite?: boolean
    status?: 'needs_review' | 'translated'
    only_keys?: Array<[string, string]>
  }) => apiPost<ApplyResult>('/api/nexus/transfer/apply', body),

  /**
   * Queue the transfer on the job system instead of holding the request open.
   * Returns at once; watch /jobs/<job_id>/stream and then read the plan by plan_id.
   */
  transferJob: (body: {
    mod: string
    donor_mod_id?: number
    language?: string
    apply?: boolean
    overwrite?: boolean
    status?: 'needs_review' | 'translated'
    keep_archive?: boolean
    include_mcm?: boolean
    include_swf?: boolean
  }) => apiPost<{ ok: boolean; job_id: string; plan_id: string }>('/api/nexus/transfer/job', body),

  transferOneshot: (body: {
    mod: string
    language?: string
    donor_mod_id?: number
    overwrite?: boolean
    status?: 'needs_review' | 'translated'
  }) => apiPost<TransferPlan>('/api/nexus/transfer/oneshot', body),
}
