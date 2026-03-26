import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { jobsApi } from '@/api/jobs'
import { apiPost, apiGet } from '@/api/client'
import { cn } from '@/lib/utils'
import {
  FileCode,
  Package,
  Film,
  BookOpen,
  Loader2,
  CheckCircle,
  AlertCircle,
  ExternalLink,
  Hash,
  Globe,
  Download,
  Type,
} from 'lucide-react'

// ── Shared result display ─────────────────────────────────────────────────────
interface ResultBannerProps {
  status: 'idle' | 'loading' | 'success' | 'error'
  message: string
  jobId?: string
}

function ResultBanner({ status, message, jobId }: ResultBannerProps) {
  const navigate = useNavigate()

  if (status === 'idle') return null

  if (status === 'loading') {
    return (
      <div className="flex items-center gap-2 text-sm text-accent">
        <Loader2 className="w-4 h-4 animate-spin" />
        {message}
      </div>
    )
  }

  if (status === 'error') {
    return (
      <div className="flex items-center gap-2 text-sm text-danger">
        <AlertCircle className="w-4 h-4 flex-shrink-0" />
        {message}
      </div>
    )
  }

  return (
    <div className="flex items-center gap-3 text-sm text-success">
      <CheckCircle className="w-4 h-4 flex-shrink-0" />
      <span>{message}</span>
      {jobId && (
        <button
          onClick={() => navigate({ to: `/jobs/${jobId}` })}
          className="flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 transition-colors ml-1"
        >
          <ExternalLink className="w-3 h-3" />
          View Job
        </button>
      )}
    </div>
  )
}

// ── Tool Card wrapper ─────────────────────────────────────────────────────────
interface ToolCardProps {
  title: string
  children: React.ReactNode
}

function ToolCard({ title, children }: ToolCardProps) {
  return (
    <div className="bg-bg-card border border-border-subtle rounded-lg p-5 space-y-4">
      <h3 className="text-sm font-semibold text-text-main">{title}</h3>
      {children}
    </div>
  )
}

function FieldRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-text-muted">{label}</label>
      {children}
    </div>
  )
}

const inputCls = 'w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main font-mono outline-none focus:border-accent/50 transition-colors'
const btnCls = 'flex items-center gap-2 px-4 py-2 rounded text-sm font-medium bg-accent text-bg-base hover:opacity-90 disabled:opacity-50 transition-opacity'

// ── ESP Tools ─────────────────────────────────────────────────────────────────
function EspParseTool() {
  const [filePath, setFilePath] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [result, setResult] = useState<{ count: number; strings: string[] } | null>(null)
  const [errorMsg, setErrorMsg] = useState('')

  const run = async () => {
    if (!filePath.trim()) return
    setStatus('loading')
    setResult(null)
    try {
      const res = await apiPost<{ count: number; strings: string[] }>('/tools/esp/parse', {
        file_path: filePath.trim(),
      })
      setResult(res)
      setStatus('success')
    } catch (e: unknown) {
      setStatus('error')
      setErrorMsg(e instanceof Error ? e.message : 'Unknown error')
    }
  }

  return (
    <ToolCard title="Parse ESP">
      <FieldRow label="ESP File Path">
        <input value={filePath} onChange={(e) => setFilePath(e.target.value)} placeholder="/path/to/mod.esp" className={inputCls} />
      </FieldRow>
      <button onClick={run} disabled={status === 'loading' || !filePath.trim()} className={btnCls}>
        {status === 'loading' ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
        Parse
      </button>
      {status === 'loading' && <p className="text-sm text-accent">Parsing…</p>}
      {status === 'error' && (
        <div className="flex items-center gap-2 text-sm text-danger">
          <AlertCircle className="w-4 h-4" />{errorMsg}
        </div>
      )}
      {status === 'success' && result && (
        <div className="space-y-2">
          <p className="text-sm text-success flex items-center gap-2">
            <CheckCircle className="w-4 h-4" />
            {result.count} strings found
          </p>
          {result.strings.length > 0 && (
            <div className="bg-bg-base rounded border border-border-subtle p-3 max-h-40 overflow-auto">
              {result.strings.slice(0, 20).map((s, i) => (
                <div key={i} className="text-xs font-mono text-text-muted py-0.5 border-b border-border-subtle/40 last:border-0">
                  {s}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </ToolCard>
  )
}

function EspJobTool({ title, jobType }: { title: string; jobType: string }) {
  const [filePath, setFilePath] = useState('')
  const [modName, setModName] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [jobId, setJobId] = useState('')
  const [errorMsg, setErrorMsg] = useState('')

  const run = async () => {
    if (!filePath.trim() || !modName.trim()) return
    setStatus('loading')
    try {
      const res = await jobsApi.create({
        job_type: jobType,
        file_path: filePath.trim(),
        mod_name: modName.trim(),
      })
      setJobId(res.job_id)
      setStatus('success')
    } catch (e: unknown) {
      setStatus('error')
      setErrorMsg(e instanceof Error ? e.message : 'Unknown error')
    }
  }

  return (
    <ToolCard title={title}>
      <FieldRow label="ESP File Path">
        <input value={filePath} onChange={(e) => setFilePath(e.target.value)} placeholder="/path/to/mod.esp" className={inputCls} />
      </FieldRow>
      <FieldRow label="Mod Name">
        <input value={modName} onChange={(e) => setModName(e.target.value)} placeholder="ModFolderName" className={inputCls} />
      </FieldRow>
      <button onClick={run} disabled={status === 'loading' || !filePath.trim() || !modName.trim()} className={btnCls}>
        {status === 'loading' ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
        Run
      </button>
      <ResultBanner
        status={status}
        message={status === 'loading' ? 'Creating job…' : status === 'success' ? 'Job created.' : errorMsg}
        jobId={jobId}
      />
    </ToolCard>
  )
}

// ── BSA Tools ─────────────────────────────────────────────────────────────────
function BsaUnpackTool() {
  const [bsaPath, setBsaPath] = useState('')
  const [outputDir, setOutputDir] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [jobId, setJobId] = useState('')
  const [errorMsg, setErrorMsg] = useState('')

  const run = async () => {
    if (!bsaPath.trim()) return
    setStatus('loading')
    try {
      const res = await jobsApi.create({
        job_type: 'bsa_unpack',
        bsa_path: bsaPath.trim(),
        output_dir: outputDir.trim() || undefined,
      })
      setJobId(res.job_id)
      setStatus('success')
    } catch (e: unknown) {
      setStatus('error')
      setErrorMsg(e instanceof Error ? e.message : 'Unknown error')
    }
  }

  return (
    <ToolCard title="Unpack BSA">
      <FieldRow label="BSA Path">
        <input value={bsaPath} onChange={(e) => setBsaPath(e.target.value)} placeholder="/path/to/archive.bsa" className={inputCls} />
      </FieldRow>
      <FieldRow label="Output Directory">
        <input value={outputDir} onChange={(e) => setOutputDir(e.target.value)} placeholder="/path/to/output (optional)" className={inputCls} />
      </FieldRow>
      <button onClick={run} disabled={status === 'loading' || !bsaPath.trim()} className={btnCls}>
        {status === 'loading' ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
        Unpack
      </button>
      <ResultBanner
        status={status}
        message={status === 'loading' ? 'Creating job…' : status === 'success' ? 'Unpack job created.' : errorMsg}
        jobId={jobId}
      />
    </ToolCard>
  )
}

function BsaPackTool() {
  const [inputDir, setInputDir] = useState('')
  const [bsaPath, setBsaPath] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [jobId, setJobId] = useState('')
  const [errorMsg, setErrorMsg] = useState('')

  const run = async () => {
    if (!inputDir.trim() || !bsaPath.trim()) return
    setStatus('loading')
    try {
      const res = await jobsApi.create({
        job_type: 'bsa_pack',
        input_dir: inputDir.trim(),
        bsa_path: bsaPath.trim(),
      })
      setJobId(res.job_id)
      setStatus('success')
    } catch (e: unknown) {
      setStatus('error')
      setErrorMsg(e instanceof Error ? e.message : 'Unknown error')
    }
  }

  return (
    <ToolCard title="Pack BSA">
      <FieldRow label="Input Directory">
        <input value={inputDir} onChange={(e) => setInputDir(e.target.value)} placeholder="/path/to/source_dir" className={inputCls} />
      </FieldRow>
      <FieldRow label="Output BSA Path">
        <input value={bsaPath} onChange={(e) => setBsaPath(e.target.value)} placeholder="/path/to/output.bsa" className={inputCls} />
      </FieldRow>
      <button onClick={run} disabled={status === 'loading' || !inputDir.trim() || !bsaPath.trim()} className={btnCls}>
        {status === 'loading' ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
        Pack
      </button>
      <ResultBanner
        status={status}
        message={status === 'loading' ? 'Creating job…' : status === 'success' ? 'Pack job created.' : errorMsg}
        jobId={jobId}
      />
    </ToolCard>
  )
}

// ── SWF Tools ─────────────────────────────────────────────────────────────────
function SwfTool({ title, jobType, pathLabel, pathPlaceholder }: {
  title: string
  jobType: string
  pathLabel: string
  pathPlaceholder: string
}) {
  const [path, setPath] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [jobId, setJobId] = useState('')
  const [errorMsg, setErrorMsg] = useState('')

  const run = async () => {
    if (!path.trim()) return
    setStatus('loading')
    try {
      const res = await jobsApi.create({ job_type: jobType, path: path.trim() })
      setJobId(res.job_id)
      setStatus('success')
    } catch (e: unknown) {
      setStatus('error')
      setErrorMsg(e instanceof Error ? e.message : 'Unknown error')
    }
  }

  return (
    <ToolCard title={title}>
      <FieldRow label={pathLabel}>
        <input value={path} onChange={(e) => setPath(e.target.value)} placeholder={pathPlaceholder} className={inputCls} />
      </FieldRow>
      <button onClick={run} disabled={status === 'loading' || !path.trim()} className={btnCls}>
        {status === 'loading' ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
        Run
      </button>
      <ResultBanner
        status={status}
        message={status === 'loading' ? 'Creating job…' : status === 'success' ? 'Job created.' : errorMsg}
        jobId={jobId}
      />
    </ToolCard>
  )
}

// ── xTranslate Import ─────────────────────────────────────────────────────────
function XTranslateImport() {
  const [filePath, setFilePath] = useState('')
  const [format, setFormat] = useState<'t3dict' | 'xml'>('t3dict')
  const [modName, setModName] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [importedCount, setImportedCount] = useState<number | null>(null)
  const [errorMsg, setErrorMsg] = useState('')

  const run = async () => {
    if (!filePath.trim()) return
    setStatus('loading')
    setImportedCount(null)
    try {
      const res = await apiPost<{ ok: boolean; imported: number }>('/tools/xtranslate/import', {
        file_path: filePath.trim(),
        format,
        mod_name: modName.trim() || undefined,
      })
      setImportedCount(res.imported)
      setStatus('success')
    } catch (e: unknown) {
      setStatus('error')
      setErrorMsg(e instanceof Error ? e.message : 'Unknown error')
    }
  }

  return (
    <ToolCard title="Import xTranslate Dictionary">
      <FieldRow label="Dictionary File Path">
        <input value={filePath} onChange={(e) => setFilePath(e.target.value)} placeholder="/path/to/dict.t3dict" className={inputCls} />
      </FieldRow>
      <div className="grid grid-cols-2 gap-3">
        <FieldRow label="Format">
          <select
            value={format}
            onChange={(e) => setFormat(e.target.value as 't3dict' | 'xml')}
            className={cn(inputCls, 'font-sans')}
          >
            <option value="t3dict">t3dict</option>
            <option value="xml">xml</option>
          </select>
        </FieldRow>
        <FieldRow label="Mod Name (optional)">
          <input value={modName} onChange={(e) => setModName(e.target.value)} placeholder="ModFolderName" className={inputCls} />
        </FieldRow>
      </div>
      <button onClick={run} disabled={status === 'loading' || !filePath.trim()} className={btnCls}>
        {status === 'loading' ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
        Import
      </button>
      {status === 'loading' && <p className="text-sm text-accent">Importing…</p>}
      {status === 'error' && (
        <div className="flex items-center gap-2 text-sm text-danger">
          <AlertCircle className="w-4 h-4" />{errorMsg}
        </div>
      )}
      {status === 'success' && importedCount !== null && (
        <div className="flex items-center gap-2 text-sm text-success">
          <CheckCircle className="w-4 h-4" />
          Imported {importedCount} string{importedCount !== 1 ? 's' : ''}.
        </div>
      )}
    </ToolCard>
  )
}

// ── File Hashes ───────────────────────────────────────────────────────────────
interface HashEntry {
  path: string
  sha256: string
  size: number
}

function FileHashesTool() {
  const [modName, setModName] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [hashes, setHashes] = useState<HashEntry[]>([])
  const [errorMsg, setErrorMsg] = useState('')

  const { data: existingData } = useQuery<{ hashes: HashEntry[] }>({
    queryKey: ['hashes'],
    queryFn: () => apiGet<{ hashes: HashEntry[] }>('/tools/hashes'),
  })

  const run = async () => {
    setStatus('loading')
    setHashes([])
    try {
      const res = await apiPost<{ hashes: HashEntry[] }>('/tools/hashes/compute', {
        mod_name: modName.trim() || undefined,
      })
      setHashes(res.hashes ?? [])
      setStatus('success')
    } catch (e: unknown) {
      setStatus('error')
      setErrorMsg(e instanceof Error ? e.message : 'Unknown error')
    }
  }

  const displayHashes = hashes.length > 0 ? hashes : (existingData?.hashes ?? [])

  return (
    <ToolCard title="File Hashes">
      <FieldRow label="Mod Name (optional)">
        <input
          value={modName}
          onChange={(e) => setModName(e.target.value)}
          placeholder="ModFolderName"
          className={inputCls}
        />
      </FieldRow>
      <button onClick={run} disabled={status === 'loading'} className={btnCls}>
        {status === 'loading' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Hash className="w-4 h-4" />}
        Compute Hashes
      </button>
      {status === 'loading' && <p className="text-sm text-accent">Computing hashes…</p>}
      {status === 'error' && (
        <div className="flex items-center gap-2 text-sm text-danger">
          <AlertCircle className="w-4 h-4" />{errorMsg}
        </div>
      )}
      {displayHashes.length > 0 && (
        <div className="overflow-auto max-h-64">
          <table className="w-full text-xs font-mono border-collapse">
            <thead>
              <tr className="border-b border-border-subtle">
                <th className="text-left py-1 pr-3 text-text-muted font-semibold">Path</th>
                <th className="text-left py-1 pr-3 text-text-muted font-semibold">SHA256</th>
                <th className="text-right py-1 text-text-muted font-semibold">Size</th>
              </tr>
            </thead>
            <tbody>
              {displayHashes.map((h, i) => (
                <tr key={i} className="border-b border-border-subtle/40 hover:bg-bg-card2/40">
                  <td className="py-1 pr-3 text-text-muted truncate max-w-[200px]" title={h.path}>{h.path}</td>
                  <td className="py-1 pr-3 text-text-muted truncate max-w-[180px]" title={h.sha256}>{h.sha256.slice(0, 16)}…</td>
                  <td className="py-1 text-right text-text-muted">{h.size.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </ToolCard>
  )
}

// ── Nexus Fetch ───────────────────────────────────────────────────────────────
interface NexusResult {
  ok: boolean
  mod_id: number
  name: string
  version: string
}

function NexusFetchTool() {
  const [modName, setModName] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [result, setResult] = useState<NexusResult | null>(null)
  const [errorMsg, setErrorMsg] = useState('')

  const run = async () => {
    if (!modName.trim()) return
    setStatus('loading')
    setResult(null)
    try {
      const res = await apiPost<NexusResult>('/tools/nexus/fetch', {
        mod_name: modName.trim(),
      })
      setResult(res)
      setStatus('success')
    } catch (e: unknown) {
      setStatus('error')
      setErrorMsg(e instanceof Error ? e.message : 'Unknown error')
    }
  }

  return (
    <ToolCard title="Nexus Mod Info">
      <FieldRow label="Mod Name">
        <input
          value={modName}
          onChange={(e) => setModName(e.target.value)}
          placeholder="ModFolderName"
          className={inputCls}
        />
      </FieldRow>
      <button onClick={run} disabled={status === 'loading' || !modName.trim()} className={btnCls}>
        {status === 'loading' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Globe className="w-4 h-4" />}
        Fetch from Nexus
      </button>
      {status === 'loading' && <p className="text-sm text-accent">Fetching…</p>}
      {status === 'error' && (
        <div className="flex items-center gap-2 text-sm text-danger">
          <AlertCircle className="w-4 h-4" />{errorMsg}
        </div>
      )}
      {status === 'success' && result && (
        <div className="space-y-1 text-sm">
          <div className="flex items-center gap-2 text-success">
            <CheckCircle className="w-4 h-4" />
            Fetched successfully
          </div>
          <div className="bg-bg-base rounded border border-border-subtle p-3 space-y-1 font-mono text-xs">
            <div className="flex gap-3">
              <span className="text-text-muted w-20 shrink-0">Mod ID</span>
              <span className="text-text-main">{result.mod_id}</span>
            </div>
            <div className="flex gap-3">
              <span className="text-text-muted w-20 shrink-0">Name</span>
              <span className="text-text-main">{result.name}</span>
            </div>
            <div className="flex gap-3">
              <span className="text-text-muted w-20 shrink-0">Version</span>
              <span className="text-text-main">{result.version}</span>
            </div>
          </div>
        </div>
      )}
    </ToolCard>
  )
}

// ── xTranslate Export ─────────────────────────────────────────────────────────
function XTranslateExport() {
  const [modName, setModName] = useState('')
  const [outputPath, setOutputPath] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [result, setResult] = useState<{ path: string; exported: number } | null>(null)
  const [errorMsg, setErrorMsg] = useState('')

  const run = async () => {
    setStatus('loading')
    setResult(null)
    try {
      const res = await apiPost<{ ok: boolean; path: string; exported: number }>(
        '/tools/xtranslate/export',
        {
          mod_name: modName.trim() || undefined,
          output_path: outputPath.trim() || undefined,
        },
      )
      setResult({ path: res.path, exported: res.exported })
      setStatus('success')
    } catch (e: unknown) {
      setStatus('error')
      setErrorMsg(e instanceof Error ? e.message : 'Unknown error')
    }
  }

  return (
    <ToolCard title="Export xTranslate Dictionary">
      <div className="grid grid-cols-2 gap-3">
        <FieldRow label="Mod Name (optional)">
          <input value={modName} onChange={(e) => setModName(e.target.value)} placeholder="ModFolderName" className={inputCls} />
        </FieldRow>
        <FieldRow label="Output Path (optional)">
          <input value={outputPath} onChange={(e) => setOutputPath(e.target.value)} placeholder="/path/to/output.t3dict" className={inputCls} />
        </FieldRow>
      </div>
      <button onClick={run} disabled={status === 'loading'} className={btnCls}>
        {status === 'loading' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
        Export
      </button>
      {status === 'loading' && <p className="text-sm text-accent">Exporting…</p>}
      {status === 'error' && (
        <div className="flex items-center gap-2 text-sm text-danger">
          <AlertCircle className="w-4 h-4" />{errorMsg}
        </div>
      )}
      {status === 'success' && result && (
        <div className="space-y-1 text-sm">
          <div className="flex items-center gap-2 text-success">
            <CheckCircle className="w-4 h-4" />
            Exported {result.exported} string{result.exported !== 1 ? 's' : ''}
          </div>
          <div className="text-xs font-mono text-text-muted bg-bg-base rounded border border-border-subtle p-2 truncate" title={result.path}>
            {result.path}
          </div>
        </div>
      )}
    </ToolCard>
  )
}

// ── SWF Font Fix ──────────────────────────────────────────────────────────────
interface FontEntry {
  id: string
  name: string
  style: string
}

function SwfFontFixTool() {
  const [swfPath, setSwfPath]   = useState('')
  const [ttfPath, setTtfPath]   = useState('')
  const [outPath, setOutPath]   = useState('')
  const [fonts, setFonts]       = useState<FontEntry[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [listStatus, setListStatus]   = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [fixStatus,  setFixStatus]    = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [listError, setListError]   = useState('')
  const [fixError,  setFixError]    = useState('')
  const [fixResult, setFixResult]   = useState<{ out_path: string; replaced: string[] } | null>(null)

  const listFonts = async () => {
    if (!swfPath.trim()) return
    setListStatus('loading')
    setFonts([])
    setSelected(new Set())
    setListError('')
    try {
      const res = await apiPost<{ fonts: FontEntry[] }>('/tools/swf/list-fonts', {
        swf_path: swfPath.trim(),
      })
      setFonts(res.fonts ?? [])
      // auto-select all fonts by default
      setSelected(new Set((res.fonts ?? []).map((f) => f.id)))
      setListStatus('success')
    } catch (e: unknown) {
      setListError(e instanceof Error ? e.message : 'Unknown error')
      setListStatus('error')
    }
  }

  const toggleFont = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const fixFonts = async () => {
    if (!swfPath.trim() || selected.size === 0) return
    setFixStatus('loading')
    setFixResult(null)
    setFixError('')
    try {
      const selectedFonts = fonts
        .filter((f) => selected.has(f.id))
        .map((f) => ({ id: f.id, name: f.name }))
      const res = await apiPost<{ ok: boolean; out_path: string; replaced: string[] }>(
        '/tools/swf/fix-fonts',
        {
          swf_path:  swfPath.trim(),
          ttf_path:  ttfPath.trim() || undefined,
          out_path:  outPath.trim() || undefined,
          fonts:     selectedFonts,
        },
      )
      setFixResult({ out_path: res.out_path, replaced: res.replaced })
      setFixStatus('success')
    } catch (e: unknown) {
      setFixError(e instanceof Error ? e.message : 'Unknown error')
      setFixStatus('error')
    }
  }

  return (
    <ToolCard title="SWF Font Fix (Cyrillic)">
      <FieldRow label="SWF File Path">
        <input
          value={swfPath}
          onChange={(e) => setSwfPath(e.target.value)}
          placeholder="/path/to/file.swf"
          className={inputCls}
        />
      </FieldRow>

      <button
        onClick={listFonts}
        disabled={listStatus === 'loading' || !swfPath.trim()}
        className={btnCls}
      >
        {listStatus === 'loading' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Type className="w-4 h-4" />}
        List Fonts
      </button>

      {listStatus === 'error' && (
        <div className="flex items-center gap-2 text-sm text-danger">
          <AlertCircle className="w-4 h-4" />{listError}
        </div>
      )}

      {fonts.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-text-muted">Select fonts to replace with Cyrillic TTF:</p>
          <div className="bg-bg-base rounded border border-border-subtle divide-y divide-border-subtle/40 max-h-40 overflow-auto">
            {fonts.map((f) => (
              <label
                key={f.id}
                className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-bg-card2/50"
              >
                <input
                  type="checkbox"
                  checked={selected.has(f.id)}
                  onChange={() => toggleFont(f.id)}
                  className="accent-accent"
                />
                <span className="text-xs font-mono text-text-muted w-8 shrink-0">#{f.id}</span>
                <span className="text-sm text-text-main">{f.name}</span>
                {f.style && (
                  <span className="text-xs text-text-muted ml-auto">{f.style}</span>
                )}
              </label>
            ))}
          </div>
        </div>
      )}

      {fonts.length > 0 && (
        <div className="space-y-3">
          <FieldRow label="Replacement TTF Path (leave blank to use config default)">
            <input
              value={ttfPath}
              onChange={(e) => setTtfPath(e.target.value)}
              placeholder="/path/to/font.ttf"
              className={inputCls}
            />
          </FieldRow>
          <FieldRow label="Output SWF Path (leave blank for auto _ru suffix)">
            <input
              value={outPath}
              onChange={(e) => setOutPath(e.target.value)}
              placeholder="/path/to/file_ru.swf"
              className={inputCls}
            />
          </FieldRow>
          <button
            onClick={fixFonts}
            disabled={fixStatus === 'loading' || selected.size === 0}
            className={btnCls}
          >
            {fixStatus === 'loading' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Type className="w-4 h-4" />}
            Fix Fonts ({selected.size})
          </button>
          {fixStatus === 'error' && (
            <div className="flex items-center gap-2 text-sm text-danger">
              <AlertCircle className="w-4 h-4" />{fixError}
            </div>
          )}
          {fixStatus === 'success' && fixResult && (
            <div className="space-y-1 text-sm">
              <div className="flex items-center gap-2 text-success">
                <CheckCircle className="w-4 h-4" />
                Replaced {fixResult.replaced.length} font{fixResult.replaced.length !== 1 ? 's' : ''}
              </div>
              <div className="text-xs font-mono text-text-muted bg-bg-base rounded border border-border-subtle p-2 truncate" title={fixResult.out_path}>
                {fixResult.out_path}
              </div>
            </div>
          )}
        </div>
      )}
    </ToolCard>
  )
}

// ── Section header ────────────────────────────────────────────────────────────
function SectionHeader({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <div className="text-accent">{icon}</div>
      <h2 className="text-base font-semibold text-text-main">{title}</h2>
      <div className="flex-1 h-px bg-border-subtle ml-2" />
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────
function ToolsPage() {
  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold text-text-main">Tools</h1>

      {/* ESP Tools */}
      <section>
        <SectionHeader icon={<FileCode className="w-5 h-5" />} title="ESP Tools" />
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <EspParseTool />
          <EspJobTool title="Validate ESP" jobType="esp_validate" />
          <EspJobTool title="Apply Translations" jobType="esp_apply" />
        </div>
      </section>

      {/* BSA Tools */}
      <section>
        <SectionHeader icon={<Package className="w-5 h-5" />} title="BSA Tools" />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <BsaUnpackTool />
          <BsaPackTool />
        </div>
      </section>

      {/* SWF Tools */}
      <section>
        <SectionHeader icon={<Film className="w-5 h-5" />} title="SWF Tools" />
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <SwfTool
            title="Decompile SWF"
            jobType="swf_decompile"
            pathLabel="SWF File Path"
            pathPlaceholder="/path/to/file.swf"
          />
          <SwfTool
            title="Compile SWF"
            jobType="swf_compile"
            pathLabel="Source Path"
            pathPlaceholder="/path/to/source_dir"
          />
          <SwfFontFixTool />
        </div>
      </section>

      {/* File Hashes + Nexus */}
      <section>
        <SectionHeader icon={<Hash className="w-5 h-5" />} title="File Hashes" />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <FileHashesTool />
          <NexusFetchTool />
        </div>
      </section>

      {/* xTranslate */}
      <section>
        <SectionHeader icon={<BookOpen className="w-5 h-5" />} title="xTranslate" />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 max-w-4xl">
          <XTranslateImport />
          <XTranslateExport />
        </div>
      </section>
    </div>
  )
}

export const Route = createFileRoute('/tools')({
  component: ToolsPage,
})
