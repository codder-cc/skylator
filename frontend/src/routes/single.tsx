import { useState, useRef, useCallback, useEffect } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ChevronLeft,
  ChevronRight,
  Upload,
  Download,
  RefreshCw,
  Save,
  Zap,
  X,
  AlertTriangle,
  PackageOpen,
} from 'lucide-react'
import { singleModApi, type SingleModSession } from '@/api/singleMod'
import { jobsApi } from '@/api/jobs'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/utils'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { useModLiveUpdates, useClearModLiveUpdates } from '@/hooks/useModLiveUpdates'
import { useJobStream } from '@/hooks/useJobStream'
import { useMachines } from '@/hooks/useMachines'
import type { StringEntry, StringUpdate } from '@/types'

const PER_PAGE = 100

export const Route = createFileRoute('/single')({
  component: SingleModPage,
})

// ── Score badge ──────────────────────────────────────────────────────────────

function ScoreBadge({ score }: { score: number | null }) {
  if (score === null) return <span className="text-text-muted/40 text-xs">—</span>
  const cls =
    score >= 80
      ? 'text-success'
      : score >= 50
      ? 'text-warning bg-warning/10 px-1 rounded'
      : 'text-danger bg-danger/15 px-1 rounded font-bold'
  return (
    <span className={cn('font-mono text-xs tabular-nums', cls)}>
      {score}
    </span>
  )
}

// ── Editable translation cell ─────────────────────────────────────────────────

interface TranslationCellProps {
  entry: StringEntry
  sessionId: string
  onSaved: (key: string, esp: string, translation: string, qs: number | null, st: string | null) => void
}

function TranslationCell({ entry, sessionId, onSaved }: TranslationCellProps) {
  const [editing, setEditing]   = useState(false)
  const [saving,  setSaving]    = useState(false)
  const [draft,   setDraft]     = useState(entry.translation)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleFocus = useCallback(() => {
    setDraft(entry.translation)
    setEditing(true)
    requestAnimationFrame(() => {
      const el = textareaRef.current
      if (el) { el.style.height = 'auto'; el.style.height = `${el.scrollHeight}px` }
    })
  }, [entry.translation])

  const handleBlur = useCallback(async () => {
    if (draft === entry.translation) { setEditing(false); return }
    setSaving(true)
    try {
      const r = await singleModApi.updateString(sessionId, {
        key: entry.key, esp: entry.esp, translation: draft,
      })
      onSaved(entry.key, entry.esp, draft, r.quality_score ?? null, r.status ?? null)
      setEditing(false)
    } catch {
      setEditing(false)
    } finally {
      setSaving(false)
    }
  }, [draft, entry, sessionId, onSaved])

  if (editing || saving) {
    return (
      <div className="relative">
        <textarea
          ref={textareaRef}
          className="w-full min-h-[3rem] max-h-48 overflow-y-auto bg-bg-card2 border border-accent/40 rounded px-2 py-1.5 text-xs text-text-main resize-none focus:outline-none focus:border-accent transition-colors leading-relaxed"
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value)
            e.target.style.height = 'auto'
            e.target.style.height = `${Math.min(e.target.scrollHeight, 192)}px`
          }}
          onBlur={handleBlur}
          onKeyDown={(e) => {
            if (e.key === 'Escape') { setDraft(entry.translation); setEditing(false) }
            if (e.key === 'Enter' && e.ctrlKey) e.currentTarget.blur()
          }}
          autoFocus
        />
        <div className="absolute top-1 right-1">
          {saving
            ? <RefreshCw size={10} className="text-text-muted animate-spin" />
            : <Save size={10} className="text-accent" />}
        </div>
      </div>
    )
  }

  return (
    <div
      onClick={handleFocus}
      className={cn(
        'min-h-[2rem] max-h-40 overflow-y-auto px-2 py-1.5 rounded cursor-text text-xs leading-relaxed whitespace-pre-wrap hover:bg-bg-card2 transition-colors',
        entry.translation ? 'text-text-main' : 'text-text-muted/40 italic',
      )}
    >
      {entry.translation || 'Click to edit…'}
    </div>
  )
}

// ── String row ────────────────────────────────────────────────────────────────

interface StringRowProps {
  entry: StringEntry
  index: number
  sessionId: string
  isTranslating: boolean
  onTranslateOne: (entry: StringEntry) => void
  onSaved: (key: string, esp: string, translation: string, qs: number | null, st: string | null) => void
  error?: string
  flashed?: boolean
}

function StringRow({ entry, index, sessionId, isTranslating, onTranslateOne, onSaved, error, flashed }: StringRowProps) {
  const espShort = entry.esp.split(/[/\\]/).pop() ?? entry.esp
  return (
    <>
      <tr
        className={cn(
          'border-t border-border-subtle hover:bg-bg-card2/30 transition-colors group',
          flashed && 'ring-1 ring-accent/50 bg-accent/5 transition-all duration-500',
          entry.status === 'needs_review' && 'border-l-2 border-l-warning/60 bg-warning/5',
          entry.quality_score !== null && entry.quality_score < 50 && 'border-l-2 border-l-danger/60 bg-danger/5',
        )}
      >
        <td className="px-3 py-2 text-xs text-text-muted/60 tabular-nums w-10 align-top">{index}</td>
        <td className="px-2 py-2 align-top max-w-[120px]" title={entry.esp}>
          <span className="text-xs font-mono text-text-muted truncate block">{espShort}</span>
        </td>
        <td className="px-2 py-2 align-top max-w-[260px]">
          <div className="text-xs text-text-muted max-h-40 overflow-y-auto leading-relaxed whitespace-pre-wrap">
            {entry.original || <span className="italic opacity-40">empty</span>}
          </div>
        </td>
        <td className="px-2 py-2 align-top min-w-[200px]">
          <TranslationCell entry={entry} sessionId={sessionId} onSaved={onSaved} />
        </td>
        <td className="px-2 py-2 align-top">
          <StatusBadge status={entry.status} />
        </td>
        <td className="px-2 py-2 align-top text-center">
          <ScoreBadge score={entry.quality_score} />
        </td>
        <td className="px-2 py-2 align-top">
          <button
            onClick={() => onTranslateOne(entry)}
            disabled={isTranslating}
            title="Translate with AI"
            className={cn(
              'flex items-center justify-center w-7 h-7 rounded-md transition-colors',
              isTranslating
                ? 'text-text-muted/40 cursor-not-allowed'
                : 'text-warning/70 hover:text-warning hover:bg-warning/10',
            )}
          >
            {isTranslating
              ? <RefreshCw size={13} className="animate-spin" />
              : <Zap size={13} />}
          </button>
        </td>
      </tr>
      {error && (
        <tr>
          <td colSpan={7} className="px-3 pb-2">
            <div className="flex items-center gap-1.5 text-xs text-danger bg-danger/10 border border-danger/20 rounded px-2 py-1">
              <AlertTriangle size={11} className="shrink-0" />{error}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

// ── Pagination ────────────────────────────────────────────────────────────────

function Pagination({ page, pages, total, onPage }: { page: number; pages: number; total: number; onPage: (p: number) => void }) {
  const [jump, setJump] = useState('')
  return (
    <div className="flex items-center gap-3 py-3 px-4 border-t border-border-subtle bg-bg-card">
      <span className="text-xs text-text-muted">{total} string{total !== 1 ? 's' : ''}</span>
      <div className="flex-1" />
      <button
        onClick={() => onPage(page - 1)} disabled={page <= 1}
        className="flex items-center gap-1 px-2 py-1 rounded text-xs text-text-muted hover:text-text-main hover:bg-bg-card2 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        <ChevronLeft size={14} />Prev
      </button>
      <span className="text-xs text-text-muted">
        Page <span className="text-text-main font-medium">{page}</span> of <span className="text-text-main font-medium">{pages}</span>
      </span>
      <button
        onClick={() => onPage(page + 1)} disabled={page >= pages}
        className="flex items-center gap-1 px-2 py-1 rounded text-xs text-text-muted hover:text-text-main hover:bg-bg-card2 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        Next<ChevronRight size={14} />
      </button>
      {pages > 2 && (
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-text-muted">Go:</span>
          <input
            type="number" min={1} max={pages} value={jump}
            onChange={(e) => setJump(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { const n = parseInt(jump, 10); if (!isNaN(n) && n >= 1 && n <= pages) { onPage(n); setJump('') } } }}
            placeholder={String(page)}
            className="w-14 px-2 py-1 rounded bg-bg-card2 border border-border-subtle text-xs text-text-main text-center focus:outline-none focus:border-accent/60"
          />
        </div>
      )}
    </div>
  )
}

// ── Drop zone ─────────────────────────────────────────────────────────────────

function DropZone({ onFile, loading }: { onFile: (f: File) => void; loading: boolean }) {
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file?.name.toLowerCase().endsWith('.zip')) onFile(file)
  }

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      onClick={() => !loading && inputRef.current?.click()}
      className={cn(
        'flex flex-col items-center justify-center gap-4 rounded-xl border-2 border-dashed transition-colors cursor-pointer',
        'py-20 px-8 text-center select-none',
        dragging ? 'border-accent bg-accent/10' : 'border-border-subtle hover:border-accent/50 hover:bg-bg-card2',
        loading && 'cursor-wait opacity-60',
      )}
    >
      <input
        ref={inputRef} type="file" accept=".zip" className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f) }}
      />
      {loading ? (
        <RefreshCw size={40} className="text-accent animate-spin" />
      ) : (
        <PackageOpen size={40} className="text-text-muted/40" />
      )}
      <div>
        <p className="text-text-main font-medium mb-1">
          {loading ? 'Uploading & parsing ESP…' : 'Drop your mod ZIP here'}
        </p>
        <p className="text-text-muted text-sm">
          {loading ? 'This may take a moment for large mods' : 'or click to browse — ZIP with ESP/ESM/ESL inside'}
        </p>
      </div>
    </div>
  )
}

// ── Scope filter tabs ─────────────────────────────────────────────────────────

const SCOPES = ['all', 'esp', 'mcm', 'bsa', 'swf'] as const

// ── Main page ─────────────────────────────────────────────────────────────────

interface StringsPageCache {
  strings: StringEntry[]
  total: number
}

function SingleModPage() {
  const queryClient = useQueryClient()
  const machines    = useMachines()

  const [phase,     setPhase]     = useState<'idle' | 'uploading' | 'loaded'>('idle')
  const [session,   setSession]   = useState<SingleModSession | null>(null)
  const [uploadErr, setUploadErr] = useState<string | null>(null)

  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [scope,       setScope]       = useState('all')
  const [status,      setStatus]      = useState('all')
  const [q,           setQ]           = useState('')
  const [page,        setPage]        = useState(1)

  const [translatingKeys, setTranslatingKeys] = useState<Set<string>>(new Set())
  const [rowErrors,       setRowErrors]       = useState<Record<string, string>>({})
  const [flashedKeys,     setFlashedKeys]     = useState<Set<string>>(new Set())
  const processedCount = useRef(0)

  const sessionId = session?.session_id ?? ''
  const modName   = session?.mod_name   ?? ''

  // ── Strings query ─────────────────────────────────────────────────────────

  const offset  = (page - 1) * PER_PAGE
  const queryKey = ['single', sessionId, scope, status, q, page]

  const { data, isLoading: strLoading } = useQuery({
    queryKey,
    queryFn: () =>
      singleModApi.getStrings(sessionId, {
        scope: scope !== 'all' ? scope : undefined,
        status: status !== 'all' ? status : undefined,
        q: q || undefined,
        limit: PER_PAGE,
        offset,
      }),
    enabled: phase === 'loaded' && !!sessionId,
    staleTime: 30_000,
  })

  const strings  = data?.strings ?? []
  const total    = data?.total ?? 0
  const pages    = Math.max(1, Math.ceil(total / PER_PAGE))

  // Reset page when filters change
  useEffect(() => { setPage(1) }, [scope, status, q])

  // ── Live updates ──────────────────────────────────────────────────────────

  const liveUpdates  = useModLiveUpdates(modName)
  const clearLiveUpdates = useClearModLiveUpdates(modName)

  useJobStream(activeJobId ?? '', !!activeJobId)

  useEffect(() => {
    const newUpdates: StringUpdate[] = liveUpdates.slice(processedCount.current)
    if (newUpdates.length === 0) return
    processedCount.current = liveUpdates.length

    queryClient.setQueryData<StringsPageCache>(queryKey, (old) => {
      if (!old) return old
      const updateMap = new Map(newUpdates.map((u) => [u.key, u]))
      const strings = old.strings.map((s) => {
        const u = updateMap.get(s.key)
        if (!u) return s
        return { ...s, translation: u.translation, status: u.status, quality_score: u.quality_score }
      })
      return { ...old, strings }
    })

    const flashKeys = new Set(newUpdates.map((u) => u.key))
    setFlashedKeys((prev) => new Set([...prev, ...flashKeys]))
    setTimeout(() => {
      setFlashedKeys((prev) => {
        const next = new Set(prev)
        flashKeys.forEach((k) => next.delete(k))
        return next
      })
    }, 2000)
  }, [liveUpdates])

  // Detect job completion → clear live updates + refetch
  const { data: allJobs = [] } = useQuery({ queryKey: QK.jobs(), queryFn: jobsApi.list })
  const activeJob = activeJobId ? allJobs.find((j) => j.id === activeJobId) : null
  useEffect(() => {
    if (!activeJob) return
    if (['done', 'failed', 'cancelled'].includes(activeJob.status)) {
      setActiveJobId(null)
      clearLiveUpdates()
      processedCount.current = 0
      void queryClient.invalidateQueries({ queryKey: ['single', sessionId] })
    }
  }, [activeJob?.status])

  // ── Upload ────────────────────────────────────────────────────────────────

  const handleFile = async (file: File) => {
    setUploadErr(null)
    setPhase('uploading')
    try {
      const res = await singleModApi.upload(file)
      setSession(res)
      setPhase('loaded')
    } catch (e: unknown) {
      setUploadErr(e instanceof Error ? e.message : 'Upload failed')
      setPhase('idle')
    }
  }

  // ── Translate-one ─────────────────────────────────────────────────────────

  const handleTranslateOne = async (entry: StringEntry) => {
    const key = entry.key
    setTranslatingKeys((prev) => new Set([...prev, key]))
    setRowErrors((prev) => { const n = { ...prev }; delete n[key]; return n })
    try {
      const res = await singleModApi.translateOne(sessionId, {
        key, esp: entry.esp, original: entry.original, machines,
      })
      if (!res.ok) {
        setRowErrors((prev) => ({ ...prev, [key]: res.error ?? 'Translation failed' }))
        return
      }
      queryClient.setQueryData<StringsPageCache>(queryKey, (old) => {
        if (!old) return old
        return {
          ...old,
          strings: old.strings.map((s) =>
            s.key === key
              ? { ...s, translation: res.translation, status: res.status, quality_score: res.quality_score }
              : s,
          ),
        }
      })
    } catch (e: unknown) {
      setRowErrors((prev) => ({ ...prev, [key]: e instanceof Error ? e.message : 'Error' }))
    } finally {
      setTranslatingKeys((prev) => { const n = new Set(prev); n.delete(key); return n })
    }
  }

  // ── Manual save callback ──────────────────────────────────────────────────

  const handleSaved = (key: string, _esp: string, translation: string, qs: number | null, st: string | null) => {
    queryClient.setQueryData<StringsPageCache>(queryKey, (old) => {
      if (!old) return old
      return {
        ...old,
        strings: old.strings.map((s) =>
          s.key === key
            ? { ...s, translation, quality_score: qs ?? s.quality_score, status: st ?? s.status }
            : s,
        ),
      }
    })
  }

  // ── Bulk translate ────────────────────────────────────────────────────────

  const handleTranslateAll = async () => {
    try {
      const res = await singleModApi.translateBulk(sessionId, { machines })
      setActiveJobId(res.job_id)
      processedCount.current = 0
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : 'Failed to start translation job')
    }
  }

  // ── Download ──────────────────────────────────────────────────────────────

  const handleDownload = async () => {
    const url  = singleModApi.downloadUrl(sessionId)
    const r    = await fetch(url)
    const blob = await r.blob()
    const stem = (session?.zip_name ?? 'mod').replace(/\.zip$/i, '')
    const dl   = Object.assign(document.createElement('a'), {
      href: URL.createObjectURL(blob),
      download: `${stem}_ru.zip`,
    })
    dl.click()
    URL.revokeObjectURL(dl.href)
  }

  // ── Reset ─────────────────────────────────────────────────────────────────

  const handleReset = async () => {
    if (sessionId) {
      await singleModApi.deleteSession(sessionId).catch(() => {})
      queryClient.removeQueries({ queryKey: ['single', sessionId] })
    }
    setSession(null)
    setPhase('idle')
    setActiveJobId(null)
    setScope('all')
    setStatus('all')
    setQ('')
    setPage(1)
    clearLiveUpdates()
    processedCount.current = 0
  }

  // ── Job progress bar ──────────────────────────────────────────────────────

  const jobRunning = activeJob && ['running', 'pending'].includes(activeJob.status)
  const jobPct     = activeJob?.pct ?? 0
  const jobMsg     = activeJob?.progress?.message ?? ''

  // ── Render ────────────────────────────────────────────────────────────────

  if (phase === 'idle' || phase === 'uploading') {
    return (
      <div className="flex flex-col flex-1 overflow-auto p-8">
        <div className="max-w-2xl mx-auto w-full">
          <div className="flex items-center gap-3 mb-8">
            <PackageOpen className="w-6 h-6 text-accent" />
            <h1 className="text-xl font-bold text-text-main">Single Mod Translate</h1>
          </div>
          <DropZone onFile={handleFile} loading={phase === 'uploading'} />
          {uploadErr && (
            <div className="mt-4 flex items-center gap-2 text-sm text-danger bg-danger/10 border border-danger/20 rounded-lg px-4 py-3">
              <AlertTriangle size={16} className="shrink-0" />{uploadErr}
            </div>
          )}
          <p className="mt-6 text-xs text-text-muted text-center">
            Upload any Skyrim mod as a ZIP. ESP strings will be extracted, and you can translate them with AI or manually, then download the translated mod.
          </p>
        </div>
      </div>
    )
  }

  // Loaded phase
  const translatedCount = session?.esp_files.reduce((acc, e) => acc + e.count, 0) ?? 0

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border-default bg-bg-card shrink-0">
        <PackageOpen className="w-4 h-4 text-accent shrink-0" />
        <span className="font-semibold text-text-main text-sm truncate max-w-xs" title={session?.zip_name}>
          {session?.zip_name}
        </span>
        <span className="text-xs text-text-muted">·</span>
        <span className="text-xs text-text-muted">{session?.total ?? 0} strings</span>
        <span className="text-xs text-text-muted">·</span>
        <span className="text-xs text-text-muted">{session?.esp_files.length ?? 0} ESP file{(session?.esp_files.length ?? 0) !== 1 ? 's' : ''}</span>
        <div className="flex-1" />
        <button
          onClick={handleTranslateAll}
          disabled={!!jobRunning}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-accent/15 text-accent hover:bg-accent/25 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {jobRunning
            ? <><RefreshCw size={12} className="animate-spin" />Translating…</>
            : <><Zap size={12} />Translate All</>}
        </button>
        <button
          onClick={handleDownload}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-success/15 text-success hover:bg-success/25 transition-colors"
        >
          <Download size={12} />Download ZIP
        </button>
        <button
          onClick={handleReset}
          title="Close and start over"
          className="flex items-center justify-center w-7 h-7 rounded-md text-text-muted hover:text-text-main hover:bg-bg-card2 transition-colors"
        >
          <X size={15} />
        </button>
      </div>

      {/* Job progress */}
      {jobRunning && (
        <div className="shrink-0 px-4 py-2 bg-bg-card border-b border-border-subtle">
          <div className="flex items-center gap-3 mb-1">
            <RefreshCw size={11} className="text-accent animate-spin" />
            <span className="text-xs text-text-muted truncate">{jobMsg || 'Translating…'}</span>
            <span className="ml-auto text-xs text-accent font-medium tabular-nums">{Math.round(jobPct)}%</span>
          </div>
          <div className="h-1 rounded-full bg-bg-card2 overflow-hidden">
            <div
              className="h-full bg-accent transition-all duration-500 rounded-full"
              style={{ width: `${jobPct}%` }}
            />
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-border-subtle bg-bg-card shrink-0">
        {SCOPES.map((sc) => (
          <button
            key={sc}
            onClick={() => setScope(sc)}
            className={cn(
              'px-3 py-1 rounded text-xs font-medium transition-colors capitalize',
              scope === sc
                ? 'bg-accent/15 text-accent'
                : 'text-text-muted hover:text-text-main hover:bg-bg-card2',
            )}
          >
            {sc}
          </button>
        ))}
        <div className="w-px h-4 bg-border-subtle mx-1" />
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="px-2 py-1 rounded bg-bg-card2 border border-border-subtle text-xs text-text-main focus:outline-none focus:border-accent/60"
        >
          <option value="all">All statuses</option>
          <option value="pending">Pending</option>
          <option value="translated">Translated</option>
          <option value="needs_review">Needs review</option>
        </select>
        <div className="flex-1" />
        <div className="relative">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search…"
            className="pl-7 pr-3 py-1 rounded bg-bg-card2 border border-border-subtle text-xs text-text-main w-48 focus:outline-none focus:border-accent/60"
          />
          <span className="absolute left-2 top-1/2 -translate-y-1/2 text-text-muted/50 pointer-events-none">
            <Upload size={11} />
          </span>
        </div>
      </div>

      {/* String table */}
      <div className="flex-1 overflow-auto">
        {strLoading ? (
          <div className="flex items-center justify-center py-20 text-text-muted">
            <RefreshCw size={20} className="animate-spin mr-2" />Loading…
          </div>
        ) : strings.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-text-muted text-sm gap-2">
            <PackageOpen size={32} className="opacity-30" />
            <p>No strings match current filters</p>
          </div>
        ) : (
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-border-subtle bg-bg-card sticky top-0 z-10">
                <th className="px-3 py-2 text-xs font-medium text-text-muted w-10">#</th>
                <th className="px-2 py-2 text-xs font-medium text-text-muted">ESP</th>
                <th className="px-2 py-2 text-xs font-medium text-text-muted">Original</th>
                <th className="px-2 py-2 text-xs font-medium text-text-muted">Translation</th>
                <th className="px-2 py-2 text-xs font-medium text-text-muted">Status</th>
                <th className="px-2 py-2 text-xs font-medium text-text-muted text-center">Score</th>
                <th className="px-2 py-2 text-xs font-medium text-text-muted">AI</th>
              </tr>
            </thead>
            <tbody>
              {strings.map((entry, idx) => (
                <StringRow
                  key={entry.key}
                  entry={entry}
                  index={offset + idx + 1}
                  sessionId={sessionId}
                  isTranslating={translatingKeys.has(entry.key)}
                  onTranslateOne={handleTranslateOne}
                  onSaved={handleSaved}
                  error={rowErrors[entry.key]}
                  flashed={flashedKeys.has(entry.key)}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {total > PER_PAGE && (
        <Pagination page={page} pages={pages} total={total} onPage={setPage} />
      )}
    </div>
  )
}
