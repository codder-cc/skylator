import { createFileRoute } from '@tanstack/react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect, useRef } from 'react'
import { workersApi } from '@/api/workers'
import { QK } from '@/lib/queryKeys'
import { timeAgo, cn } from '@/lib/utils'
import type { WorkerInfo, SetupReport, CachedModel } from '@/types'
import {
  Server,
  RefreshCw,
  Upload,
  PowerOff,
  ChevronDown,
  ChevronUp,
  Wifi,
  WifiOff,
  AlertCircle,
  CheckCircle,
  Loader2,
  X,
  Trash2,
} from 'lucide-react'

// ── Presets ───────────────────────────────────────────────────────────────────
interface Preset {
  label: string
  repo_id: string
  gguf_filename: string
  backend_type: 'llamacpp' | 'mlx'
}

const PRESETS: Preset[] = [
  {
    label: 'Qwen3.5-27B 4bit MLX — Huihui (Apple Silicon)',
    repo_id: 'mlx-community/Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-4bit',
    gguf_filename: '',
    backend_type: 'mlx',
  },
  {
    label: 'Qwen3.5-27B Q4_K_M GGUF — Huihui (CUDA / Metal)',
    repo_id: 'Sepolian/Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-Q4_K_M',
    gguf_filename: 'Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-Q4_K_M.gguf',
    backend_type: 'llamacpp',
  },
  {
    label: 'Qwen3.5-27B 4bit MLX — Instruct',
    repo_id: 'mlx-community/Qwen3.5-27B-Instruct-4bit',
    gguf_filename: '',
    backend_type: 'mlx',
  },
  {
    label: 'Qwen3.5-27B Q4_K_M GGUF — Instruct',
    repo_id: 'huihui-ai/Qwen3.5-27B-Instruct-GGUF',
    gguf_filename: 'Qwen3.5-27B-Instruct-Q4_K_M.gguf',
    backend_type: 'llamacpp',
  },
  {
    label: 'Qwen3-14B Q4_K_M GGUF',
    repo_id: 'bartowski/Qwen3-14B-GGUF',
    gguf_filename: 'Qwen3-14B-Q4_K_M.gguf',
    backend_type: 'llamacpp',
  },
  { label: 'Custom', repo_id: '', gguf_filename: '', backend_type: 'llamacpp' },
]

// ── Load Model Dialog ─────────────────────────────────────────────────────────
interface LoadModelDialogProps {
  worker: WorkerInfo
  onClose: () => void
}

function LoadModelDialog({ worker, onClose }: LoadModelDialogProps) {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'hf' | 'local'>('hf')
  const [presetIdx, setPresetIdx] = useState(0)
  const [repoId, setRepoId] = useState(PRESETS[0].repo_id)
  const [ggufFile, setGgufFile] = useState(PRESETS[0].gguf_filename)
  const [localPath, setLocalPath] = useState('')
  const [backendType, setBackendType] = useState<'llamacpp' | 'mlx'>(PRESETS[0].backend_type)
  const [gpuLayers, setGpuLayers] = useState(-1)
  const [nCtx, setNCtx] = useState(8192)
  const [batchSize, setBatchSize] = useState(12)
  const [maxNewTokens, setMaxNewTokens] = useState(2048)
  const [loadStatus, setLoadStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [errorMsg, setErrorMsg] = useState('')

  const handlePreset = (idx: number) => {
    setPresetIdx(idx)
    const p = PRESETS[idx]
    setRepoId(p.repo_id)
    setGgufFile(p.gguf_filename)
    setBackendType(p.backend_type)
  }

  const selectCached = (m: CachedModel) => {
    setTab('local')
    setLocalPath(m.path)
    setBackendType(m.backend === 'mlx' ? 'mlx' : 'llamacpp')
    setPresetIdx(PRESETS.length - 1) // Custom
  }

  const handleSubmit = async () => {
    setLoadStatus('loading')
    setErrorMsg('')
    try {
      const body: Record<string, unknown> = {
        backend_type: backendType,
        n_gpu_layers: gpuLayers,
        n_ctx: nCtx,
        batch_size: batchSize,
        max_new_tokens: maxNewTokens,
      }
      if (tab === 'hf') {
        body.repo_id = repoId
        if (ggufFile) body.gguf_filename = ggufFile
        body.model = ggufFile || repoId
      } else {
        body.model_path = localPath
        body.model = localPath
      }
      await workersApi.loadModel(worker.label, body as Parameters<typeof workersApi.loadModel>[1])
      setLoadStatus('success')
      qc.invalidateQueries({ queryKey: QK.workers() })
      setTimeout(onClose, 1200)
    } catch (e: unknown) {
      setLoadStatus('error')
      setErrorMsg(e instanceof Error ? e.message : 'Unknown error')
    }
  }

  const cachedModels: CachedModel[] = Array.isArray(worker.models) ? worker.models : []
  const isDownload = tab === 'hf' && !cachedModels.some(
    (m) => m.name === (ggufFile || repoId.split('/').pop())
  )

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-bg-card border border-border-subtle rounded-lg w-full max-w-xl shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border-subtle">
          <h2 className="font-semibold text-text-main">Load Model — {worker.label}</h2>
          <button onClick={onClose} className="text-text-muted hover:text-text-main">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {/* Preset */}
          <div>
            <label className="block text-xs text-text-muted mb-1">Preset</label>
            <select
              value={presetIdx}
              onChange={(e) => handlePreset(Number(e.target.value))}
              className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main"
            >
              {PRESETS.map((p, i) => (
                <option key={p.label} value={i}>{p.label}</option>
              ))}
            </select>
          </div>

          {/* Tab bar */}
          <div className="flex gap-2">
            {(['hf', 'local'] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={cn(
                  'px-3 py-1.5 rounded text-xs font-medium',
                  tab === t
                    ? 'bg-accent text-bg-base'
                    : 'bg-bg-card2 text-text-muted hover:text-text-main',
                )}
              >
                {t === 'hf' ? 'HuggingFace' : 'Local Path'}
              </button>
            ))}
          </div>

          {tab === 'hf' ? (
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-text-muted mb-1">Repo ID</label>
                <input
                  value={repoId}
                  onChange={(e) => setRepoId(e.target.value)}
                  className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main font-mono"
                  placeholder="org/model-name"
                />
              </div>
              <div>
                <label className="block text-xs text-text-muted mb-1">GGUF Filename</label>
                <input
                  value={ggufFile}
                  onChange={(e) => setGgufFile(e.target.value)}
                  className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main font-mono"
                  placeholder="model-Q4_K_M.gguf (blank for MLX)"
                />
              </div>
            </div>
          ) : (
            <div>
              <label className="block text-xs text-text-muted mb-1">Model Path</label>
              <input
                value={localPath}
                onChange={(e) => setLocalPath(e.target.value)}
                className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main font-mono"
                placeholder="/path/to/model.gguf"
              />
            </div>
          )}

          {/* Cached models badges */}
          {cachedModels.length > 0 && (
            <div>
              <label className="block text-xs text-text-muted mb-1">Cached on worker — click to load from local path</label>
              <div className="flex flex-wrap gap-1.5">
                {cachedModels.map((m) => (
                  <button
                    key={m.path}
                    onClick={() => selectCached(m)}
                    title={`${m.path} · ${m.size_mb} MB`}
                    className="px-2 py-0.5 rounded text-xs bg-success/20 text-success border border-success/30 hover:bg-success/30 font-mono"
                  >
                    {m.name}
                    <span className="ml-1 opacity-60 text-[10px] uppercase">{m.backend}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Advanced options */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-text-muted mb-1">Backend</label>
              <select
                value={backendType}
                onChange={(e) => setBackendType(e.target.value as 'llamacpp' | 'mlx')}
                className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main"
              >
                <option value="llamacpp">llamacpp</option>
                <option value="mlx">mlx</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-text-muted mb-1">GPU Layers</label>
              <input
                type="number"
                value={gpuLayers}
                onChange={(e) => setGpuLayers(Number(e.target.value))}
                className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main"
              />
            </div>
            <div>
              <label className="block text-xs text-text-muted mb-1">Context (n_ctx)</label>
              <input
                type="number"
                value={nCtx}
                onChange={(e) => setNCtx(Number(e.target.value))}
                className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main"
              />
            </div>
            <div>
              <label className="block text-xs text-text-muted mb-1">Batch Size</label>
              <input
                type="number"
                value={batchSize}
                onChange={(e) => setBatchSize(Number(e.target.value))}
                className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main"
              />
            </div>
            <div className="col-span-2">
              <label className="block text-xs text-text-muted mb-1">Max New Tokens</label>
              <input
                type="number"
                value={maxNewTokens}
                onChange={(e) => setMaxNewTokens(Number(e.target.value))}
                className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main"
              />
            </div>
          </div>

          {/* Status messages */}
          {loadStatus === 'loading' && (
            <div className="flex items-center gap-2 text-sm text-accent">
              <Loader2 className="w-4 h-4 animate-spin" />
              {isDownload
                ? 'Downloading + loading model — may take 10–30 min depending on model size…'
                : 'Loading model into memory — may take 1–2 min…'}
            </div>
          )}
          {loadStatus === 'success' && (
            <div className="flex items-center gap-2 text-sm text-success">
              <CheckCircle className="w-4 h-4" />
              Model loaded successfully.
            </div>
          )}
          {loadStatus === 'error' && (
            <div className="flex items-center gap-2 text-sm text-danger">
              <AlertCircle className="w-4 h-4" />
              {errorMsg}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-5 py-4 border-t border-border-subtle">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded text-sm text-text-muted hover:text-text-main bg-bg-card2 border border-border-subtle"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={loadStatus === 'loading'}
            className="px-4 py-2 rounded text-sm font-medium bg-accent text-bg-base hover:opacity-90 disabled:opacity-50"
          >
            {loadStatus === 'loading' ? 'Loading…' : 'Load Model'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Setup Report Row ──────────────────────────────────────────────────────────
function SetupReportRow({ report }: { report: SetupReport }) {
  const [expanded, setExpanded] = useState(false)
  const isOk = report.status === 'success' || report.status === 'ok'

  return (
    <div className="border border-border-subtle rounded mb-2 overflow-hidden">
      <button
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-bg-card2 transition-colors"
        onClick={() => setExpanded((v) => !v)}
      >
        <span
          className={cn(
            'px-2 py-0.5 rounded text-xs font-medium flex-shrink-0',
            isOk ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger',
          )}
        >
          {report.status}
        </span>
        <span className="text-sm text-text-main font-medium">{report.machine}</span>
        <span className="text-xs text-text-muted">{report.os}</span>
        <span className="text-xs text-text-muted ml-auto">{timeAgo(report.ts)}</span>
        <span className="text-xs text-text-muted">exit: {report.exit_code}</span>
        {expanded
          ? <ChevronUp className="w-3 h-3 text-text-muted flex-shrink-0" />
          : <ChevronDown className="w-3 h-3 text-text-muted flex-shrink-0" />}
      </button>
      {expanded && (
        <div className="border-t border-border-subtle px-4 py-3 bg-bg-base">
          <pre className="text-xs font-mono text-text-muted whitespace-pre-wrap max-h-64 overflow-auto leading-relaxed">
            {report.log}
          </pre>
        </div>
      )}
    </div>
  )
}

// ── Worker Row ────────────────────────────────────────────────────────────────
interface WorkerRowProps {
  worker: WorkerInfo
  onLoad: (worker: WorkerInfo) => void
}

function WorkerRow({ worker, onLoad }: WorkerRowProps) {
  const qc = useQueryClient()
  const unloadMut = useMutation({
    mutationFn: () => workersApi.unloadModel(worker.label),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.workers() }),
  })

  return (
    <tr className="border-t border-border-subtle hover:bg-bg-card2/50 transition-colors">
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'w-2 h-2 rounded-full flex-shrink-0',
              worker.alive ? 'bg-success' : 'bg-danger',
            )}
          />
          <span className="text-sm font-medium text-text-main">{worker.label}</span>
        </div>
      </td>
      <td className="px-4 py-3 text-xs text-text-muted font-mono">{worker.url}</td>
      <td className="px-4 py-3 text-xs text-text-main">{worker.model ?? '—'}</td>
      <td className="px-4 py-3 text-xs text-text-muted">{worker.gpu ?? '—'}</td>
      <td className="px-4 py-3">
        <span
          className={cn(
            'px-1.5 py-0.5 rounded text-[10px] font-mono uppercase',
            worker.backend_type === 'mlx'
              ? 'bg-accent/20 text-accent'
              : 'bg-accent/10 text-accent',
          )}
        >
          {worker.backend_type || 'llamacpp'}
        </span>
      </td>
      <td className="px-4 py-3 text-xs text-text-muted">{timeAgo(worker.last_seen)}</td>
      <td
        className="px-4 py-3 text-xs text-text-muted max-w-[160px] truncate"
        title={worker.current_task ?? ''}
      >
        {worker.current_task ?? '—'}
      </td>
      <td className="px-4 py-3">
        <div className="flex gap-2">
          <button
            onClick={() => onLoad(worker)}
            className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 transition-colors"
          >
            <Upload className="w-3 h-3" />
            Load Model
          </button>
          {worker.model && (
            <button
              onClick={() => unloadMut.mutate()}
              disabled={unloadMut.isPending}
              className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-danger/20 text-danger border border-danger/30 hover:bg-danger/30 disabled:opacity-50 transition-colors"
            >
              {unloadMut.isPending
                ? <Loader2 className="w-3 h-3 animate-spin" />
                : <PowerOff className="w-3 h-3" />}
              Unload
            </button>
          )}
        </div>
      </td>
    </tr>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────
function ServersPage() {
  const qc = useQueryClient()
  const [loadModalWorker, setLoadModalWorker] = useState<WorkerInfo | null>(null)
  const [scanPoll, setScanPoll] = useState(false)
  const scanTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const workersQ = useQuery({
    queryKey: QK.workers(),
    queryFn: workersApi.list,
    refetchInterval: 10_000,
  })

  const serversQ = useQuery({
    queryKey: QK.servers(),
    queryFn: workersApi.getServers,
    refetchInterval: scanPoll ? 2_000 : false,
  })

  const reportsQ = useQuery({
    queryKey: QK.setupReports(),
    queryFn: workersApi.getSetupReports,
    refetchInterval: 10_000,
  })

  const scanMut = useMutation({
    mutationFn: workersApi.scanLan,
    onSuccess: () => {
      setScanPoll(true)
      if (scanTimerRef.current) clearTimeout(scanTimerRef.current)
      scanTimerRef.current = setTimeout(() => setScanPoll(false), 30_000)
      qc.invalidateQueries({ queryKey: QK.servers() })
    },
  })

  const clearReportsMut = useMutation({
    mutationFn: workersApi.clearSetupReports,
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.setupReports() }),
  })

  useEffect(() => {
    return () => {
      if (scanTimerRef.current) clearTimeout(scanTimerRef.current)
    }
  }, [])

  const workers = workersQ.data ?? []
  const servers = serversQ.data ?? []
  const reports = reportsQ.data ?? []

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-text-main">Servers</h1>
        <button
          onClick={() => qc.invalidateQueries({ queryKey: QK.workers() })}
          className="flex items-center gap-2 px-3 py-2 rounded text-sm bg-bg-card border border-border-subtle text-text-muted hover:text-text-main transition-colors"
        >
          <RefreshCw className={cn('w-4 h-4', workersQ.isFetching && 'animate-spin')} />
          Refresh
        </button>
      </div>

      {/* Registered Workers card */}
      <div className="bg-bg-card border border-border-subtle rounded-lg">
        <div className="flex items-center gap-2 px-5 py-4 border-b border-border-subtle">
          <Server className="w-4 h-4 text-accent" />
          <h2 className="font-semibold text-text-main">Registered Workers</h2>
          <span className="ml-auto text-xs text-text-muted">
            {workers.length} worker{workers.length !== 1 ? 's' : ''}
          </span>
        </div>
        {workers.length === 0 ? (
          <div className="px-5 py-10 text-center text-text-muted text-sm">
            {workersQ.isLoading ? 'Loading…' : 'No workers registered yet.'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="text-xs text-text-muted">
                  {['Label', 'URL', 'Model', 'GPU', 'Backend', 'Last Seen', 'Current Task', 'Actions'].map((h) => (
                    <th key={h} className="px-4 py-3 font-medium whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {workers.map((w) => (
                  <WorkerRow key={w.label} worker={w} onLoad={setLoadModalWorker} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* LAN Discovered Servers card */}
      <div className="bg-bg-card border border-border-subtle rounded-lg">
        <div className="flex items-center gap-2 px-5 py-4 border-b border-border-subtle">
          <Wifi className="w-4 h-4 text-accent" />
          <h2 className="font-semibold text-text-main">LAN Discovered Servers</h2>
          <button
            onClick={() => scanMut.mutate()}
            disabled={scanMut.isPending}
            className="ml-auto flex items-center gap-2 px-3 py-1.5 rounded text-xs bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 disabled:opacity-50 transition-colors"
          >
            {scanMut.isPending || scanPoll
              ? <Loader2 className="w-3 h-3 animate-spin" />
              : <Wifi className="w-3 h-3" />}
            {scanPoll ? 'Scanning…' : 'Scan LAN'}
          </button>
        </div>
        <div className="p-5">
          {servers.length === 0 ? (
            <p className="text-sm text-text-muted">
              {scanPoll
                ? 'Scanning local network…'
                : 'Click "Scan LAN" to discover Skylator workers on your network.'}
            </p>
          ) : (
            <div className="space-y-2">
              {servers.map((s) => (
                <div
                  key={s.url}
                  className="flex items-center gap-3 px-4 py-3 bg-bg-card2 rounded border border-border-subtle"
                >
                  {s.reachable ? (
                    <Wifi className="w-4 h-4 text-success flex-shrink-0" />
                  ) : (
                    <WifiOff className="w-4 h-4 text-danger flex-shrink-0" />
                  )}
                  <span className="text-sm font-mono text-text-main">{s.url}</span>
                  <span className={cn('text-xs', s.reachable ? 'text-success' : 'text-danger')}>
                    {s.reachable ? 'reachable' : 'unreachable'}
                  </span>
                  {s.label && (
                    <span className="text-xs text-text-muted ml-auto">{s.label}</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Remote Setup Reports card */}
      <div className="bg-bg-card border border-border-subtle rounded-lg">
        <div className="flex items-center gap-2 px-5 py-4 border-b border-border-subtle">
          <AlertCircle className="w-4 h-4 text-warning" />
          <h2 className="font-semibold text-text-main">Remote Setup Reports</h2>
          <span className="ml-1 text-xs text-text-muted">
            {reports.length} report{reports.length !== 1 ? 's' : ''}
          </span>
          {reports.length > 0 && (
            <button
              onClick={() => clearReportsMut.mutate()}
              disabled={clearReportsMut.isPending}
              className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded text-xs bg-danger/20 text-danger border border-danger/30 hover:bg-danger/30 disabled:opacity-50 transition-colors"
            >
              <Trash2 className="w-3 h-3" />
              Clear All
            </button>
          )}
        </div>
        <div className="p-5">
          {reports.length === 0 ? (
            <p className="text-sm text-text-muted">No setup reports available.</p>
          ) : (
            reports.map((r, i) => <SetupReportRow key={i} report={r} />)
          )}
        </div>
      </div>

      {/* Load Model Modal */}
      {loadModalWorker && (
        <LoadModelDialog
          worker={loadModalWorker}
          onClose={() => setLoadModalWorker(null)}
        />
      )}
    </div>
  )
}

export const Route = createFileRoute('/servers')({
  component: ServersPage,
})
