import type { WorkerHardware } from '@/types'

export interface ModelEntry {
  id: string
  label: string
  repoId: string
  ggufFilename: string
  backend: 'llamacpp' | 'mlx'
  /** Approximate model weight size in MB */
  sizeMb: number
  /** Number of transformer layers */
  layers: number
  kvHeads: number
  headDim: number
  /** Minimum free VRAM required (MB) — 0 for MLX models */
  minVramMb: number
  /** Minimum unified memory required (MB) — for MLX models */
  minRamMb: number
  draftRepoId?: string
  defaultParams: {
    n_ctx: number
    batch_size: number
    n_gpu_layers?: number
    n_batch?: number
    num_draft_tokens?: number
  }
}

export const MODEL_CATALOG: ModelEntry[] = [
  {
    id: 'huihui-27b-mlx',
    label: 'Qwen3.5-27B Huihui 4bit — MLX (Apple Silicon)',
    repoId: 'mlx-community/Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-4bit',
    ggufFilename: '',
    backend: 'mlx',
    sizeMb: 14336,
    layers: 64, kvHeads: 8, headDim: 128,
    minVramMb: 0,
    minRamMb: 20000,
    draftRepoId: 'mlx-community/Qwen2.5-1.5B-Instruct-4bit',
    defaultParams: { n_ctx: 4096, batch_size: 8, num_draft_tokens: 4 },
  },
  {
    id: 'instruct-27b-mlx',
    label: 'Qwen3.5-27B Instruct 4bit — MLX (Apple Silicon)',
    repoId: 'mlx-community/Qwen3.5-27B-Instruct-4bit',
    ggufFilename: '',
    backend: 'mlx',
    sizeMb: 14336,
    layers: 64, kvHeads: 8, headDim: 128,
    minVramMb: 0,
    minRamMb: 20000,
    draftRepoId: 'mlx-community/Qwen2.5-1.5B-Instruct-4bit',
    defaultParams: { n_ctx: 4096, batch_size: 8, num_draft_tokens: 3 },
  },
  {
    id: 'huihui-27b-gguf',
    label: 'Qwen3.5-27B Huihui Q4_K_M — CUDA/Metal',
    repoId: 'Sepolian/Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-Q4_K_M',
    ggufFilename: 'Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-Q4_K_M.gguf',
    backend: 'llamacpp',
    sizeMb: 16896,
    layers: 64, kvHeads: 8, headDim: 128,
    minVramMb: 14000,
    minRamMb: 0,
    defaultParams: { n_ctx: 4096, n_batch: 2048, batch_size: 12, n_gpu_layers: -1 },
  },
  {
    id: 'instruct-27b-gguf',
    label: 'Qwen3.5-27B Instruct Q4_K_M — CUDA/Metal',
    repoId: 'huihui-ai/Qwen3.5-27B-Instruct-GGUF',
    ggufFilename: 'Qwen3.5-27B-Instruct-Q4_K_M.gguf',
    backend: 'llamacpp',
    sizeMb: 16384,
    layers: 64, kvHeads: 8, headDim: 128,
    minVramMb: 14000,
    minRamMb: 0,
    defaultParams: { n_ctx: 4096, n_batch: 2048, batch_size: 12, n_gpu_layers: -1 },
  },
  {
    id: '14b-gguf',
    label: 'Qwen3-14B Q4_K_M — CUDA/Metal (8 GB+ VRAM)',
    repoId: 'bartowski/Qwen3-14B-GGUF',
    ggufFilename: 'Qwen3-14B-Q4_K_M.gguf',
    backend: 'llamacpp',
    sizeMb: 8704,
    layers: 40, kvHeads: 8, headDim: 128,
    minVramMb: 8000,
    minRamMb: 0,
    defaultParams: { n_ctx: 8192, n_batch: 1024, batch_size: 12, n_gpu_layers: -1 },
  },
]

/** KV-cache size estimate in MB (FP16, two tensors K+V). */
function kvCacheMb(m: ModelEntry, nCtx: number): number {
  // 2 × n_layers × n_kv_heads × head_dim × n_ctx × 2 bytes (FP16)
  return Math.round((2 * m.layers * m.kvHeads * m.headDim * nCtx * 2) / (1024 * 1024))
}

export interface VramEstimate {
  modelMb: number
  kvMb: number
  totalMb: number
  layersOnCpu: number
}

/**
 * Estimate VRAM (or unified RAM) consumption for a GGUF model.
 * n_gpu_layers = -1 means all layers on GPU.
 */
export function estimateVram(m: ModelEntry, nGpuLayers: number, nCtx: number): VramEstimate {
  const totalLayers    = m.layers
  const gpuLayers      = nGpuLayers < 0 ? totalLayers : Math.min(nGpuLayers, totalLayers)
  const layerFraction  = gpuLayers / totalLayers
  const modelMb        = Math.round(m.sizeMb * layerFraction)
  const kvMb           = kvCacheMb(m, nCtx)
  return {
    modelMb,
    kvMb,
    totalMb:    modelMb + kvMb,
    layersOnCpu: totalLayers - gpuLayers,
  }
}

/** Return how many layers fit in available VRAM given n_ctx. */
export function recommendedGpuLayers(m: ModelEntry, vramFreeMb: number, nCtx: number): number {
  const kv         = kvCacheMb(m, nCtx)
  const overhead   = 512
  const available  = vramFreeMb - kv - overhead
  if (available <= 0) return 0
  const perLayer   = (m.sizeMb / m.layers) * 1.05
  return Math.min(Math.floor(available / perLayer), m.layers)
}

/**
 * Return catalog entries ordered by recommendation for the given hardware,
 * annotated with a fit status.
 */
export function getRecommendedPresets(
  hw: WorkerHardware,
): Array<ModelEntry & { fit: 'full' | 'partial' | 'none'; recommended: boolean }> {
  return MODEL_CATALOG.map((m) => {
    let fit: 'full' | 'partial' | 'none' = 'none'
    let recommended = false

    if (m.backend === 'mlx') {
      if (!hw.unified_memory) {
        fit = 'none'
      } else if (hw.ram_free_mb >= m.minRamMb) {
        fit = 'full'
        recommended = true
      } else if (hw.ram_total_mb >= m.minRamMb) {
        fit = 'partial'
      }
    } else {
      const available = hw.unified_memory ? hw.ram_free_mb : hw.vram_free_mb
      const total     = hw.unified_memory ? hw.ram_total_mb : hw.vram_total_mb
      if (available >= m.minVramMb) {
        fit = 'full'
        recommended = !hw.unified_memory  // prefer MLX on Mac
      } else if (total >= m.minVramMb * 0.7) {
        fit = 'partial'
      }
    }

    return { ...m, fit, recommended }
  }).sort((a, b) => {
    // recommended first, then full fit, then partial, then none
    const score = (e: typeof a) =>
      e.recommended ? 3 : e.fit === 'full' ? 2 : e.fit === 'partial' ? 1 : 0
    return score(b) - score(a)
  })
}
