/**
 * BenchmarkPanel — run a 3-sample TPS/quality benchmark on a worker and optionally apply the
 * recommended params. Extracted from routes/servers.tsx (#8 decompose): self-contained modal
 * keyed only by {worker, onClose}.
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { X, Loader2, AlertCircle, CheckCircle } from 'lucide-react'
import { workersApi } from '@/api/workers'
import { QK } from '@/lib/queryKeys'
import type { WorkerInfo, BenchmarkResult } from '@/types'

export function BenchmarkPanel({ worker, onClose }: { worker: WorkerInfo; onClose: () => void }) {
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
