import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { jobsApi } from '@/api/jobs'
import { apiPost } from '@/api/client'
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
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
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
        </div>
      </section>

      {/* xTranslate */}
      <section>
        <SectionHeader icon={<BookOpen className="w-5 h-5" />} title="xTranslate Import" />
        <div className="max-w-xl">
          <XTranslateImport />
        </div>
      </section>
    </div>
  )
}

export const Route = createFileRoute('/tools')({
  component: ToolsPage,
})
