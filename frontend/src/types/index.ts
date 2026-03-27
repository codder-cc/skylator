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
  source?: string
  machine_label?: string
}

export interface WorkerStatus {
  label: string
  done: number
  tps: number
  current_text: string
  // current_key removed — backend never sends this field
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
  tokens_generated?: number
  tps_avg?: number
}

export interface ModFileInfo {
  path: string
  name: string
  size_bytes: number
  ext: string
  has_russian: boolean
  is_localized: boolean
}

export interface ModInfo {
  folder_name: string
  total_strings: number
  translated_strings: number
  pending_strings: number
  needs_review_strings: number
  pct: number
  status: ModStatus
  esp_files: ModFileInfo[]
  bsa_files: ModFileInfo[]
  mcm_loose: ModFileInfo[]
  untranslatable_strings: number
  has_meta_ini: boolean
  nexus_mod_id: number | null
  cached_at: number | null
}

export interface StringEntry {
  id: number
  key: string
  esp: string
  original: string
  translation: string
  status: string
  quality_score: number | null
  dict_match?: boolean
  source?: string           // 'ai' | 'cache' | 'dict' | 'manual' | 'imported'
  machine_label?: string    // worker label that produced this translation
  translated_at?: number    // unix timestamp
  reserved_by?: string      // job_id that holds the reservation, if any
  dup_count?: number
}

export interface ReservationInfo {
  string_id: number
  key: string
  esp: string
  reserved_by: string       // job_id
  machine_label: string
  reserved_at: number
  expires_at: number
}

export interface StringHistoryEntry {
  id: number
  string_id: number
  translation: string
  status: string
  quality_score: number | null
  source: string
  machine_label: string | null
  job_id: string | null
  created_at: number
}

export interface CachedModel {
  name: string
  path: string
  size_mb: number
  backend: string   // "mlx" | "llamacpp"
}

export interface WorkerHardware {
  ram_total_mb: number
  ram_free_mb: number
  vram_total_mb: number    // 0 for Apple Silicon (unified memory)
  vram_free_mb: number
  unified_memory: boolean  // true for Apple Silicon: RAM is GPU memory
  cpu_name: string
  cpu_cores: number
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
  models: CachedModel[]
  stats: { tps_avg: number; tps_last: number; queue_depth: number; jobs_completed: number } | null
  hardware?: WorkerHardware
  alive: boolean
}

export interface BenchmarkSampleResult {
  label: string
  elapsed_sec: number
  tps: number
  cyrillic_ok: boolean
  token_preserved: boolean
  output: string
}

export interface BenchmarkResult {
  results: BenchmarkSampleResult[]
  tps_avg: number
  recommended_params: {
    batch_size: number
    n_ctx: number
    n_batch: number
  }
  error?: string
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

export interface TokenPerf {
  ok: boolean
  calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  last_completion_tokens: number
  tps_last: number
  tps_avg: number
  last_elapsed_sec: number
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

export interface CheckpointEntry {
  checkpoint_id: string
  mod_name: string
  created_at: number
  string_count: number
}

export interface SetupReport {
  ts: number
  status: string
  exit_code: number
  machine: string
  os: string
  log: string
}
