import { createFileRoute } from '@tanstack/react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect, useRef } from 'react'
import { workersApi } from '@/api/workers'
import { QK } from '@/lib/queryKeys'
import { timeAgo, cn } from '@/lib/utils'
import type { WorkerInfo, SetupReport, CachedModel, BenchmarkResult } from '@/types'
import {
  MODEL_CATALOG,
  estimateVram,
  recommendedGpuLayers,
  getRecommendedPresets,
  type ModelEntry,
} from '@/lib/modelCatalog'
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
  Cpu,
  MemoryStick,
  Play,
  ArrowDownCircle,
  GitCommit,
  GitBranch,
} from 'lucide-react'
import { otaApi } from '@/api/ota'

// ── Resource bar ──────────────────────────────────────────────────────────────
function ResourceBar({
  label,
  usedMb,
  totalMb,
  note,
}: {
  label: string
  usedMb: number
  totalMb: number
  note?: string
}) {
  if (!totalMb || totalMb <= 0 || !Number.isFinite(usedMb)) return null
  const pct     = Math.min(100, Math.round((usedMb / totalMb) * 100))
  const barColor =
    pct >= 90 ? 'bg-danger' : pct >= 75 ? 'bg-warning' : 'bg-success'
  const usedGb  = (usedMb  / 1024).toFixed(1)
  const totalGb = (totalMb / 1024).toFixed(1)
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-text-muted w-12 shrink-0">{label}</span>
      <div className="flex-1 h-1.5 bg-bg-base rounded-full overflow-hidden">
        <div className={cn('h-full rounded-full transition-all', barColor)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-text-muted whitespace-nowrap">
        {usedGb} / {totalGb} GB
        {note && <span className="opacity-60"> {note}</span>}
      </span>
    </div>
  )
}

// ── Load Model Dialog ─────────────────────────────────────────────────────────
interface LoadModelDialogProps {
  worker: WorkerInfo
  onClose: () => void
}

function LoadModelDialog({ worker, onClose }: LoadModelDialogProps) {
  const qc       = useQueryClient()
  const hw       = worker.hardware
  const presets  = hw ? getRecommendedPresets(hw) : MODEL_CATALOG.map((m) => ({ ...m, fit: 'full' as const, recommended: false }))

  // Pick first recommended preset, or first entry
  const defaultPreset = presets.find((p) => p.recommended) ?? presets[0]

  const [tab, setTab]               = useState<'hf' | 'local'>('hf')
  const [selectedId, setSelectedId] = useState<string>(defaultPreset.id)
  const [repoId, setRepoId]         = useState(defaultPreset.repoId)
  const [ggufFile, setGgufFile]     = useState(defaultPreset.ggufFilename)
  const [localPath, setLocalPath]   = useState('')
  const [backendType, setBackendType] = useState<'llamacpp' | 'mlx'>(defaultPreset.backend)
  const [gpuLayers, setGpuLayers]   = useState(defaultPreset.defaultParams.n_gpu_layers ?? -1)
  const [nCtx, setNCtx]             = useState(defaultPreset.defaultParams.n_ctx)
  const [nBatch, setNBatch]         = useState(defaultPreset.defaultParams.n_batch ?? 512)
  const [batchSize, setBatchSize]   = useState(defaultPreset.defaultParams.batch_size)
  const [maxNewTokens, setMaxNewTokens] = useState(2048)
  const [draftRepoId, setDraftRepoId]  = useState(defaultPreset.draftRepoId ?? '')
  const [loadStatus, setLoadStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [errorMsg, setErrorMsg]     = useState('')

  const handlePresetChange = (id: string) => {
    const p = presets.find((e) => e.id === id)
    if (!p) return
    setSelectedId(id)
    setRepoId(p.repoId)
    setGgufFile(p.ggufFilename)
    setBackendType(p.backend)
    setGpuLayers(p.defaultParams.n_gpu_layers ?? -1)
    setNCtx(p.defaultParams.n_ctx)
    setNBatch(p.defaultParams.n_batch ?? 512)
    setBatchSize(p.defaultParams.batch_size)
    setDraftRepoId(p.draftRepoId ?? '')
  }

  const selectCached = (m: CachedModel) => {
    setTab('local')
    setLocalPath(m.path)
    setBackendType(m.backend === 'mlx' ? 'mlx' : 'llamacpp')
    setSelectedId('__custom__')
  }

  // Live VRAM estimate
  const catalogEntry: ModelEntry | undefined =
    MODEL_CATALOG.find((m) => m.id === selectedId)

  const vramEst = catalogEntry && backendType === 'llamacpp'
    ? estimateVram(catalogEntry, gpuLayers, nCtx)
    : null

  const vramAvailMb = hw
    ? (hw.unified_memory ? hw.ram_free_mb : hw.vram_free_mb)
    : 0
  const vramTotalMb = hw
    ? (hw.unified_memory ? hw.ram_total_mb : hw.vram_total_mb)
    : 0
  const vramUsedMb  = vramAvailMb > 0 && vramTotalMb > 0
    ? vramTotalMb - vramAvailMb
    : 0

  const handleSubmit = async () => {
    setLoadStatus('loading')
    setErrorMsg('')
    try {
      const body: Record<string, unknown> = {
        backend_type:   backendType,
        n_gpu_layers:   gpuLayers,
        n_ctx:          nCtx,
        n_batch:        nBatch,
        batch_size:     batchSize,
        max_new_tokens: maxNewTokens,
      }
      if (draftRepoId) body.draft_repo_id = draftRepoId
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 overflow-y-auto py-4">
      <div className="bg-bg-card border border-border-subtle rounded-lg w-full max-w-xl shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border-subtle">
          <h2 className="font-semibold text-text-main">Load Model — {worker.label}</h2>
          <button onClick={onClose} className="text-text-muted hover:text-text-main">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {/* Hardware info */}
          {hw && (
            <div className="p-3 bg-bg-base rounded border border-border-subtle space-y-1.5">
              <div className="flex items-center gap-2 text-xs text-text-muted mb-1">
                <Cpu className="w-3 h-3" />
                <span>{hw.cpu_name} ({hw.cpu_cores}c)</span>
                {hw.unified_memory && (
                  <span className="px-1.5 py-0.5 rounded text-[10px] bg-accent/20 text-accent">unified</span>
                )}
              </div>
              <ResourceBar
                label={hw.unified_memory ? 'Memory' : 'RAM'}
                usedMb={hw.ram_total_mb - hw.ram_free_mb}
                totalMb={hw.ram_total_mb}
              />
              {!hw.unified_memory && hw.vram_total_mb > 0 && (
                <ResourceBar
                  label="VRAM"
                  usedMb={hw.vram_total_mb - hw.vram_free_mb}
                  totalMb={hw.vram_total_mb}
                />
              )}
            </div>
          )}

          {/* Preset */}
          <div>
            <label className="block text-xs text-text-muted mb-1">Model Preset</label>
            <select
              value={selectedId}
              onChange={(e) => handlePresetChange(e.target.value)}
              className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main"
            >
              {presets.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.recommended ? '★ ' : p.fit === 'full' ? '✓ ' : p.fit === 'partial' ? '⚠ ' : '✗ '}
                  {p.label}
                  {' · '}
                  {Math.round(p.sizeMb / 1024)} GB
                </option>
              ))}
              <option value="__custom__">Custom</option>
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
              {backendType === 'mlx' && (
                <div>
                  <label className="block text-xs text-text-muted mb-1">Draft Model Repo (speculative decoding, optional)</label>
                  <input
                    value={draftRepoId}
                    onChange={(e) => setDraftRepoId(e.target.value)}
                    className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main font-mono"
                    placeholder="mlx-community/Qwen2.5-1.5B-Instruct-4bit"
                  />
                </div>
              )}
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
            {backendType === 'llamacpp' && (
              <div>
                <label className="block text-xs text-text-muted mb-1">
                  GPU Layers
                  {hw && catalogEntry && backendType === 'llamacpp' && (
                    <button
                      type="button"
                      className="ml-2 text-accent hover:underline"
                      onClick={() => {
                        const rec = recommendedGpuLayers(catalogEntry, vramAvailMb, nCtx)
                        setGpuLayers(rec)
                      }}
                    >
                      auto
                    </button>
                  )}
                </label>
                <input
                  type="number"
                  value={gpuLayers}
                  onChange={(e) => setGpuLayers(Number(e.target.value))}
                  className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main"
                />
              </div>
            )}
            <div>
              <label className="block text-xs text-text-muted mb-1">Context (n_ctx)</label>
              <input
                type="number"
                value={nCtx}
                onChange={(e) => setNCtx(Number(e.target.value))}
                className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main"
              />
            </div>
            {backendType === 'llamacpp' && (
              <div>
                <label className="block text-xs text-text-muted mb-1">Prefill Batch (n_batch)</label>
                <input
                  type="number"
                  value={nBatch}
                  onChange={(e) => setNBatch(Number(e.target.value))}
                  className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main"
                />
              </div>
            )}
            <div>
              <label className="block text-xs text-text-muted mb-1">Batch Size</label>
              <input
                type="number"
                value={batchSize}
                onChange={(e) => setBatchSize(Number(e.target.value))}
                className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main"
              />
            </div>
            <div>
              <label className="block text-xs text-text-muted mb-1">Max New Tokens</label>
              <input
                type="number"
                value={maxNewTokens}
                onChange={(e) => setMaxNewTokens(Number(e.target.value))}
                className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main"
              />
            </div>
          </div>

          {/* VRAM estimate preview */}
          {vramEst && vramTotalMb > 0 && (
            <div className="p-3 bg-bg-base rounded border border-border-subtle space-y-2">
              <div className="text-xs text-text-muted font-medium">VRAM estimate</div>
              <ResourceBar
                label={hw?.unified_memory ? 'Memory' : 'VRAM'}
                usedMb={vramUsedMb + vramEst.totalMb}
                totalMb={vramTotalMb}
                note={`+${Math.round(vramEst.totalMb / 1024 * 10) / 10} GB (model ${Math.round(vramEst.modelMb / 1024 * 10) / 10} + kv ${Math.round(vramEst.kvMb / 1024 * 10) / 10})`}
              />
              {vramEst.layersOnCpu > 0 && (
                <div className="text-xs text-warning flex items-center gap-1">
                  <AlertCircle className="w-3 h-3" />
                  {vramEst.layersOnCpu} layer{vramEst.layersOnCpu !== 1 ? 's' : ''} on CPU — inference will be slower
                </div>
              )}
              {vramEst.layersOnCpu === 0 && (
                <div className="text-xs text-success flex items-center gap-1">
                  <CheckCircle className="w-3 h-3" />
                  All layers fit in {hw?.unified_memory ? 'memory' : 'VRAM'}
                </div>
              )}
            </div>
          )}

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

// ── Benchmark Panel ───────────────────────────────────────────────────────────
function BenchmarkPanel({ worker, onClose }: { worker: WorkerInfo; onClose: () => void }) {
  const qc = useQueryClient()
  const [result, setResult] = useState<BenchmarkResult | null>(null)
  const [running, setRunning] = useState(false)
  const [err, setErr] = useState('')

  const applyMut = useMutation({
    mutationFn: (params: Record<string, unknown>) =>
      workersApi.loadModel(worker.label, params as Parameters<typeof workersApi.loadModel>[1]),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.workers() }),
  })

  const run = async () => {
    setRunning(true)
    setErr('')
    setResult(null)
    try {
      const r = await workersApi.benchmark(worker.label)
      if (r.error) setErr(r.error)
      else setResult(r)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setRunning(false)
    }
  }

  const applyRecommended = () => {
    if (!result) return
    const p = result.recommended_params
    const body: Record<string, unknown> = {
      backend_type:   worker.backend_type || 'llamacpp',
      batch_size:     p.batch_size,
      n_ctx:          p.n_ctx,
      n_batch:        p.n_batch,
      n_gpu_layers:   -1,
      max_new_tokens: 2048,
    }
    applyMut.mutate(body)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-bg-card border border-border-subtle rounded-lg w-full max-w-lg shadow-2xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border-subtle">
          <h2 className="font-semibold text-text-main">Benchmark — {worker.label}</h2>
          <button onClick={onClose} className="text-text-muted hover:text-text-main">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {!result && !running && (
            <p className="text-sm text-text-muted">
              Runs 3 sample batches (short strings, medium sentences, token-heavy text),
              measures TPS, and checks translation quality.
            </p>
          )}
          {running && (
            <div className="flex items-center gap-2 text-accent">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span className="text-sm">Running benchmark — may take 1–3 minutes…</span>
            </div>
          )}
          {err && (
            <div className="flex items-center gap-2 text-sm text-danger">
              <AlertCircle className="w-3 h-3" />
              {err}
            </div>
          )}
          {result && (
            <div className="space-y-3">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-text-muted border-b border-border-subtle">
                    <th className="py-1 text-left">Sample</th>
                    <th className="py-1 text-right">Time</th>
                    <th className="py-1 text-right">TPS</th>
                    <th className="py-1 text-center">Cyrillic</th>
                    <th className="py-1 text-center">Tokens</th>
                  </tr>
                </thead>
                <tbody>
                  {result.results.map((r) => (
                    <tr key={r.label} className="border-b border-border-subtle/40">
                      <td className="py-1.5 text-text-main capitalize">{r.label}</td>
                      <td className="py-1.5 text-right text-text-muted">{r.elapsed_sec}s</td>
                      <td className="py-1.5 text-right text-text-main font-mono">{r.tps}</td>
                      <td className="py-1.5 text-center">
                        {r.cyrillic_ok
                          ? <CheckCircle className="w-3.5 h-3.5 text-success mx-auto" />
                          : <AlertCircle className="w-3.5 h-3.5 text-danger mx-auto" />}
                      </td>
                      <td className="py-1.5 text-center">
                        {r.token_preserved
                          ? <CheckCircle className="w-3.5 h-3.5 text-success mx-auto" />
                          : <AlertCircle className="w-3.5 h-3.5 text-warning mx-auto" />}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="text-sm text-text-main">
                Average TPS: <span className="font-mono font-semibold">{result.tps_avg}</span>
              </div>
              {result.recommended_params && (
                <div className="text-xs text-text-muted bg-bg-base rounded p-2 font-mono">
                  Recommended: batch_size={result.recommended_params.batch_size}&nbsp;
                  n_ctx={result.recommended_params.n_ctx}&nbsp;
                  n_batch={result.recommended_params.n_batch}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 px-5 py-4 border-t border-border-subtle">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded text-sm text-text-muted hover:text-text-main bg-bg-card2 border border-border-subtle"
          >
            Close
          </button>
          {result?.recommended_params && (
            <button
              onClick={applyRecommended}
              disabled={applyMut.isPending}
              className="px-4 py-2 rounded text-sm bg-bg-card2 border border-border-subtle text-text-main hover:bg-bg-card disabled:opacity-50"
            >
              {applyMut.isPending ? 'Applying…' : 'Apply Recommended'}
            </button>
          )}
          <button
            onClick={run}
            disabled={running || !worker.model}
            className="px-4 py-2 rounded text-sm font-medium bg-accent text-bg-base hover:opacity-90 disabled:opacity-50"
          >
            {running ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Run Benchmark'}
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
  hostCommit: string
  onLoad: (worker: WorkerInfo) => void
  onBenchmark: (worker: WorkerInfo) => void
}

function WorkerRow({ worker, hostCommit, onLoad, onBenchmark }: WorkerRowProps) {
  const qc = useQueryClient()
  const hw  = worker.hardware
  const unloadMut = useMutation({
    mutationFn: () => workersApi.unloadModel(worker.label),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.workers() }),
  })
  const otaMut = useMutation({
    mutationFn: () => workersApi.requestOtaUpdate(worker.label),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.workers() }),
  })

  const workerCommit = worker.commit ?? ''
  const upToDate = hostCommit && workerCommit && workerCommit === hostCommit
  const otaStatus = worker.ota_status ?? 'idle'
  const isUpdating = otaStatus === 'updating' || otaStatus === 'restarting' || otaMut.isPending

  return (
    <tr className="border-t border-border-subtle hover:bg-bg-card2/50 transition-colors">
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'w-2 h-2 rounded-full flex-shrink-0',
              worker.alive
                ? 'bg-success'
                : otaStatus === 'restarting'
                  ? 'bg-warning animate-pulse'
                  : 'bg-danger',
            )}
          />
          <span className="text-sm font-medium text-text-main">{worker.label}</span>
        </div>
      </td>
      <td className="px-4 py-3 text-xs text-text-muted font-mono">{worker.url}</td>
      <td className="px-4 py-3 text-xs text-text-main">{worker.model ?? '—'}</td>
      <td className="px-4 py-3">
        {hw ? (
          <div className="space-y-1 min-w-[160px]">
            <div className="flex items-center gap-1 text-[10px] text-text-muted mb-0.5">
              <Cpu className="w-2.5 h-2.5" />
              <span className="truncate max-w-[140px]" title={hw.cpu_name}>{hw.cpu_name}</span>
              {hw.unified_memory && (
                <span className="px-1 rounded text-[9px] bg-accent/20 text-accent">unified</span>
              )}
            </div>
            <ResourceBar
              label={hw.unified_memory ? 'Mem' : 'RAM'}
              usedMb={hw.ram_total_mb - hw.ram_free_mb}
              totalMb={hw.ram_total_mb}
            />
            {!hw.unified_memory && hw.vram_total_mb > 0 && (
              <ResourceBar
                label="VRAM"
                usedMb={hw.vram_total_mb - hw.vram_free_mb}
                totalMb={hw.vram_total_mb}
              />
            )}
          </div>
        ) : (
          <span className="text-xs text-text-muted">{worker.gpu ?? '—'}</span>
        )}
      </td>
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
      {/* OTA / version */}
      <td className="px-4 py-3">
        <div className="flex flex-col gap-1 min-w-[130px]">
          {/* Commit hash */}
          <span className="flex items-center gap-1 text-[11px] font-mono text-text-muted">
            <GitCommit size={10} className="shrink-0" />
            {workerCommit || 'unknown'}
          </span>

          {/* Status line */}
          {(otaStatus === 'updating' || otaMut.isPending) && (
            <span className="flex items-center gap-1 text-[10px] text-accent">
              <Loader2 size={9} className="animate-spin" />
              updating…
            </span>
          )}
          {otaStatus === 'restarting' && (
            <span className="flex items-center gap-1 text-[10px] text-warning">
              <Loader2 size={9} className="animate-spin" />
              restarting…
            </span>
          )}
          {!isUpdating && otaStatus === 'success' && upToDate && (
            <span className="text-[10px] text-success">up to date</span>
          )}
          {!isUpdating && otaStatus === 'failed' && (
            <span
              className="text-[10px] text-danger cursor-help"
              title={(worker.ota_steps ?? []).join('\n') || 'OTA failed'}
            >
              update failed ✗
            </span>
          )}
          {!isUpdating && otaStatus === 'idle' && upToDate && (
            <span className="text-[10px] text-success">up to date</span>
          )}
          {!isUpdating && otaStatus === 'idle' && !upToDate && workerCommit && hostCommit && (
            <span className="text-[10px] text-warning">behind</span>
          )}

          {/* Steps log (after failed or success) */}
          {!isUpdating && otaStatus !== 'idle' && (worker.ota_steps ?? []).length > 0 && (
            <details className="text-[10px] text-text-muted/70">
              <summary className="cursor-pointer hover:text-text-muted">details</summary>
              <pre className="mt-1 whitespace-pre-wrap font-mono text-[9px] leading-tight max-h-20 overflow-auto">
                {(worker.ota_steps ?? []).join('\n')}
              </pre>
            </details>
          )}

          {/* Update button — hidden while updating */}
          {!isUpdating && (
            <button
              onClick={() => { if (!otaMut.isPending) otaMut.mutate() }}
              disabled={!worker.alive}
              className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-accent/15 text-accent border border-accent/20 hover:bg-accent/25 disabled:opacity-40 transition-colors"
              title={upToDate ? 'Force re-update' : 'Pull latest and restart worker'}
            >
              <ArrowDownCircle size={9} />
              {upToDate ? 'Re-update' : 'Update'}
            </button>
          )}
        </div>
      </td>
      <td className="px-4 py-3 text-xs text-text-muted">{timeAgo(worker.last_seen)}</td>
      <td
        className="px-4 py-3 text-xs text-text-muted max-w-[160px] truncate"
        title={worker.current_task ?? ''}
      >
        {worker.current_task ?? '—'}
      </td>
      <td className="px-4 py-3">
        <div className="flex flex-wrap gap-1.5">
          <button
            onClick={() => onLoad(worker)}
            className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 transition-colors"
          >
            <Upload className="w-3 h-3" />
            Load
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
          <button
            onClick={() => onBenchmark(worker)}
            disabled={!worker.model}
            title={worker.model ? 'Run benchmark' : 'Load a model first'}
            className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-bg-card2 text-text-muted border border-border-subtle hover:text-text-main disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <Play className="w-3 h-3" />
            Benchmark
          </button>
        </div>
      </td>
    </tr>
  )
}

// ── Host OTA Card ─────────────────────────────────────────────────────────────
function HostOtaCard() {
  const [log, setLog] = useState<string | null>(null)

  const statusQ = useQuery({
    queryKey: ['ota', 'status'],
    queryFn: otaApi.status,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  })

  const updateMut = useMutation({
    mutationFn: otaApi.update,
    onSuccess: (data) => {
      if (data.restarting) {
        setLog('Restarting… page will reload in 5 s')
        setTimeout(() => window.location.reload(), 5000)
      } else {
        setLog(data.steps.map((s) => `${s.ok ? '✓' : '✗'} ${s.step}`).join('\n'))
      }
    },
    onError: (e: Error) => setLog(`Error: ${e.message}`),
  })

  const s = statusQ.data
  const behind = s?.behind ?? 0

  return (
    <div className="bg-bg-card border border-border-subtle rounded-lg">
      <div className="flex items-center gap-2 px-5 py-4 border-b border-border-subtle">
        <Server className="w-4 h-4 text-accent" />
        <h2 className="font-semibold text-text-main">Host Server</h2>
        <span className="ml-1 text-xs text-text-muted/60 font-mono">
          {s ? `${s.branch} · ${s.commit}` : '…'}
        </span>
        {behind === 0 && s && (
          <span className="ml-1 text-[10px] text-success">up to date</span>
        )}
        {behind > 0 && (
          <span className="ml-1 px-1.5 py-0.5 rounded text-[10px] bg-warning/20 text-warning border border-warning/30">
            {behind} commit{behind !== 1 ? 's' : ''} behind
          </span>
        )}
        <button
          onClick={() => { setLog(null); statusQ.refetch() }}
          disabled={statusQ.isFetching}
          className="ml-auto text-text-muted hover:text-text-main transition-colors"
          title="Check for updates"
        >
          <RefreshCw size={14} className={statusQ.isFetching ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="px-5 py-4 space-y-3">
        {/* Always-visible status row */}
        <div className="flex items-center gap-3 text-xs">
          <span className="flex items-center gap-1.5 font-mono text-text-muted">
            <GitCommit size={12} className="shrink-0" />
            {s ? s.commit : '—'}
          </span>
          <span className="flex items-center gap-1.5 font-mono text-text-muted">
            <GitBranch size={12} className="shrink-0" />
            {s ? s.branch : '—'}
          </span>
          {s && behind === 0 && (
            <span className="text-success">up to date</span>
          )}
        </div>

        {/* Pending commits */}
        {s && s.pending_commits.length > 0 && (
          <div className="space-y-1">
            {s.pending_commits.map((c, i) => (
              <div key={i} className="flex items-start gap-1.5 text-xs text-text-muted font-mono">
                <GitBranch size={11} className="mt-0.5 shrink-0 text-warning" />
                {c}
              </div>
            ))}
          </div>
        )}

        {/* Update button */}
        {behind > 0 && (
          <button
            onClick={() => { setLog(null); updateMut.mutate() }}
            disabled={updateMut.isPending}
            className="flex items-center gap-2 px-4 py-2 rounded text-sm font-medium bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 disabled:opacity-50 transition-colors"
          >
            {updateMut.isPending
              ? <Loader2 size={14} className="animate-spin" />
              : <ArrowDownCircle size={14} />}
            {updateMut.isPending ? 'Updating…' : 'Update + Restart'}
          </button>
        )}

        {/* Output log */}
        {log && (
          <pre className="text-xs font-mono text-text-muted whitespace-pre-wrap bg-bg-base rounded p-3 border border-border-subtle max-h-48 overflow-auto leading-relaxed">
            {log}
          </pre>
        )}
      </div>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────
function ServersPage() {
  const qc = useQueryClient()
  const [loadModalWorker, setLoadModalWorker]       = useState<WorkerInfo | null>(null)
  const [benchmarkWorker, setBenchmarkWorker] = useState<WorkerInfo | null>(null)
  const [scanPoll, setScanPoll] = useState(false)
  const scanTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const workersQ = useQuery({
    queryKey: QK.workers(),
    queryFn: workersApi.list,
    refetchInterval: (query) => {
      const workers = query.state.data ?? []
      const hasActiveOta = workers.some(
        (w) => w.ota_status === 'updating' || w.ota_status === 'restarting',
      )
      return hasActiveOta ? 3_000 : 10_000
    },
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

  // Reuse the same status query that HostOtaCard uses — single source of truth for host commit
  const hostStatusQ = useQuery({
    queryKey: ['ota', 'status'],
    queryFn: otaApi.status,
    staleTime: 0,
    refetchInterval: 60_000,
  })

  const workers = workersQ.data ?? []
  const servers = serversQ.data ?? []
  const reports = reportsQ.data ?? []
  const hostCommit = hostStatusQ.data?.commit ?? ''

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-text-main">Servers</h1>
        <button
          onClick={() => {
            qc.invalidateQueries({ queryKey: QK.workers() })
            qc.invalidateQueries({ queryKey: ['ota'] })
          }}
          className="flex items-center gap-2 px-3 py-2 rounded text-sm bg-bg-card border border-border-subtle text-text-muted hover:text-text-main transition-colors"
        >
          <RefreshCw className={cn('w-4 h-4', workersQ.isFetching && 'animate-spin')} />
          Refresh
        </button>
      </div>

      {/* Host Server OTA */}
      <HostOtaCard />

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
                  {['Label', 'URL', 'Model', 'Resources', 'Backend', 'Version', 'Last Seen', 'Current Task', 'Actions'].map((h) => (
                    <th key={h} className="px-4 py-3 font-medium whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {workers.map((w) => (
                  <WorkerRow
                    key={w.label}
                    worker={w}
                    hostCommit={hostCommit}
                    onLoad={setLoadModalWorker}
                    onBenchmark={setBenchmarkWorker}
                  />
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

      {/* Benchmark Modal */}
      {benchmarkWorker && (
        <BenchmarkPanel
          worker={benchmarkWorker}
          onClose={() => setBenchmarkWorker(null)}
        />
      )}
    </div>
  )
}

export const Route = createFileRoute('/servers')({
  component: ServersPage,
})
