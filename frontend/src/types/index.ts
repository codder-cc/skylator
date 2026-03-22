export type JobStatus = 'pending' | 'running' | 'done' | 'failed' | 'cancelled'

export type ModStatus = 'unknown' | 'no_strings' | 'pending' | 'partial' | 'done'

export interface JobProgress {
  current: number
  total: number
  message: string
  sub_step?: string
}

export interface StringUpdate {
  key: string
  esp: string
  translation: string
  status: string
  quality_score: number | null
}

export interface WorkerStatus {
  label: string
  done: number
  tps: number
  current_text: string
  current_key: string
  alive: boolean
}

export interface Job {
  id: string
  name: string
  job_type: string
  status: JobStatus
  progress: JobProgress | null
  created_at: number
  started_at: number | null
  finished_at: number | null
  pct: number
  elapsed: number | null
  eta_seconds: number | null
  log_lines: string[]
  string_updates: StringUpdate[]
  new_string_updates: StringUpdate[]
  worker_updates: WorkerStatus[]
  error: string | null
  params: Record<string, unknown>
  mod_name: string
}

export interface ModInfo {
  folder_name: string
  total_strings: number
  translated_strings: number
  pending_strings: number
  pct: number
  status: ModStatus
  esp_files: string[]
  bsa_files: string[]
  has_meta_ini: boolean
  nexus_mod_id: number | null
  cached_at: number | null
}

export interface StringEntry {
  key: string
  esp: string
  original: string
  translation: string
  status: string
  quality_score: number | null
  dict_match?: boolean
}

export interface WorkerInfo {
  label: string
  url: string
  platform: string
  model: string | null
  gpu: string | null
  backend_type: string
  capabilities: string[]
  last_seen: number
  current_task: string | null
  models: string[]
  alive: boolean
}

export interface GpuInfo {
  available: boolean
  name: string | null
  total_mb: number
  used_mb: number
  free_mb: number
  pct: number
  sm: number | null
}

export interface Stats {
  total_mods: number
  mods_translated: number
  mods_partial: number
  mods_pending: number
  total_strings: number
  translated_strings: number
  pending_strings: number
  pct_complete: number
}

export interface TokenStats {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  calls: number
}

export interface BackupEntry {
  id: string
  mod_name: string
  label: string
  created_at: number
  size_bytes: number
  path: string
  type: string
}

export interface SetupReport {
  ts: number
  status: string
  exit_code: number
  machine: string
  os: string
  log: string
}
