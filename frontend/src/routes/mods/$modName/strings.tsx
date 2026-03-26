import { useState, useRef, useCallback, useEffect } from 'react'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ChevronLeft,
  ChevronRight,
  Search,
  Zap,
  RefreshCw,
  Save,
  AlertTriangle,
  Play,
  History,
  Lock,
  CheckCircle,
  CheckSquare,
  Copy,
  ChevronsUpDown,
  ChevronUp,
  ChevronDown,
  Replace,
  Users,
  X,
} from 'lucide-react'
import { modsApi } from '@/api/mods'
import { jobsApi } from '@/api/jobs'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/utils'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { SourceBadge } from '@/components/shared/SourceBadge'
import { StringHistoryModal } from '@/components/shared/StringHistoryModal'
import { useMachines } from '@/hooks/useMachines'
import { useReservations } from '@/hooks/useReservations'
import { SCOPES, type StringStatus } from '@/lib/constants'
import { useModLiveUpdates, useClearModLiveUpdates } from '@/hooks/useModLiveUpdates'
import type { StringEntry, StringUpdate } from '@/types'

const PER_PAGE = 100

export const Route = createFileRoute('/mods/$modName/strings')({
  validateSearch: (search: Record<string, unknown>) => ({
    scope: (search.scope as string) || 'all',
    status: (search.status as string) || 'all',
    q: (search.q as string) || '',
    page: Number(search.page) || 1,
    sort_by: (search.sort_by as string) || '',
    sort_dir: (search.sort_dir as string) || 'asc',
    rec_type: (search.rec_type as string) || '',
  }),
  component: ModStringsPage,
})

// ── Quality score badge ──────────────────────────────────────────────────────

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

// ── Sort icon ────────────────────────────────────────────────────────────────

function SortIcon({ col, current, dir }: { col: string; current: string; dir: string }) {
  if (current !== col) return <ChevronsUpDown size={11} className="text-text-muted/40" />
  return dir === 'asc'
    ? <ChevronUp size={11} className="text-accent" />
    : <ChevronDown size={11} className="text-accent" />
}

// ── Editable translation cell ────────────────────────────────────────────────

interface TranslationCellProps {
  entry: StringEntry
  modName: string
  onSaved: (key: string, esp: string, translation: string, quality_score: number | null, status: string | null) => void
}

function TranslationCell({ entry, modName, onSaved }: TranslationCellProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(entry.translation)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const saveMutation = useMutation({
    mutationFn: (translation: string) =>
      modsApi.updateString(modName, { key: entry.key, esp: entry.esp, translation }),
    onSuccess: (data, translation) => {
      const d = data as Record<string, unknown>
      const qs = d['quality_score'] as number | null ?? null
      const st = d['status'] as string | null ?? null
      onSaved(entry.key, entry.esp, translation, qs, st)
      setEditing(false)
    },
  })

  const handleBlur = useCallback(() => {
    if (draft !== entry.translation) {
      saveMutation.mutate(draft)
    } else {
      setEditing(false)
    }
  }, [draft, entry.translation, saveMutation])

  const handleFocus = useCallback(() => {
    setDraft(entry.translation)
    setEditing(true)
    // Auto-expand
    requestAnimationFrame(() => {
      const el = textareaRef.current
      if (el) {
        el.style.height = 'auto'
        el.style.height = `${el.scrollHeight}px`
      }
    })
  }, [entry.translation])

  if (editing || saveMutation.isPending) {
    return (
      <div className="relative">
        <textarea
          ref={textareaRef}
          className="w-full min-h-[3rem] max-h-48 overflow-y-auto bg-bg-card2 border border-accent/40 rounded px-2 py-1.5 text-xs text-text-main resize-none focus:outline-none focus:border-accent transition-colors leading-relaxed"
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value)
            // auto-expand up to max-h
            e.target.style.height = 'auto'
            e.target.style.height = `${Math.min(e.target.scrollHeight, 192)}px`
          }}
          onBlur={handleBlur}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              setDraft(entry.translation)
              setEditing(false)
            }
            if (e.key === 'Enter' && e.ctrlKey) {
              e.currentTarget.blur()
            }
          }}
          autoFocus
        />
        <div className="absolute top-1 right-1 flex items-center gap-1">
          {saveMutation.isPending ? (
            <RefreshCw size={10} className="text-text-muted animate-spin" />
          ) : (
            <Save size={10} className="text-accent" />
          )}
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

// ── String row ───────────────────────────────────────────────────────────────

interface StringRowProps {
  entry: StringEntry
  index: number
  modName: string
  isTranslating: boolean
  isReserved: boolean
  onTranslateOne: (entry: StringEntry) => void
  onSaved: (key: string, esp: string, translation: string, quality_score: number | null, status: string | null) => void
  onApproved: (key: string, esp: string) => void
  onCopyFromSource: (entry: StringEntry) => void
  onSyncDuplicates: (entry: StringEntry) => void
  error?: string
  flashed?: boolean
  isSelected?: boolean
  onToggleSelect?: (id: number) => void
}

function StringRow({
  entry,
  index,
  modName,
  isTranslating,
  isReserved,
  onTranslateOne,
  onSaved,
  onApproved,
  onCopyFromSource,
  onSyncDuplicates,
  error,
  flashed,
  isSelected,
  onToggleSelect,
}: StringRowProps) {
  const [showHistory, setShowHistory] = useState(false)
  const [approving, setApproving] = useState(false)
  const espShort = entry.esp.split(/[/\\]/).pop() ?? entry.esp

  const isNeedsReview = entry.status === 'needs_review'
  const isLowScore    = entry.quality_score !== null && entry.quality_score < 50
  const dupCount      = entry.dup_count ?? 0

  const handleApprove = async () => {
    if (!entry.id || approving) return
    setApproving(true)
    try {
      await modsApi.approveString(entry.id)
      onApproved(entry.key, entry.esp)
    } finally {
      setApproving(false)
    }
  }

  return (
    <>
      <tr
        className={cn(
          'border-t border-border-subtle hover:bg-bg-card2/30 transition-colors group',
          flashed       && 'ring-1 ring-accent/50 bg-accent/5 transition-all duration-500',
          isReserved    && 'bg-accent/3 border-l-2 border-l-accent/40',
          isNeedsReview && !isLowScore && !isReserved && 'border-l-2 border-l-warning/60 bg-warning/5',
          isLowScore    && !isReserved && 'border-l-2 border-l-danger/60 bg-danger/5',
        )}
      >
        {/* Checkbox */}
        <td className="px-2 py-2 w-8 align-top">
          <input
            type="checkbox"
            className="w-3.5 h-3.5 accent-accent cursor-pointer mt-0.5"
            checked={!!isSelected}
            onChange={() => entry.id > 0 && onToggleSelect?.(entry.id)}
          />
        </td>
        {/* # */}
        <td className="px-3 py-2 text-xs text-text-muted/60 tabular-nums w-10 shrink-0 align-top">
          {index}
        </td>
        {/* ESP */}
        <td
          className="px-2 py-2 align-top max-w-[120px]"
          title={entry.esp}
        >
          <span className="text-xs font-mono text-text-muted truncate block">{espShort}</span>
          {dupCount > 0 && (
            <span className="inline-flex items-center gap-0.5 mt-0.5 text-[10px] font-mono text-accent/70 bg-accent/10 rounded px-1">
              ×{dupCount}
            </span>
          )}
        </td>
        {/* Original */}
        <td className="px-2 py-2 align-top max-w-[260px]">
          <div className="text-xs text-text-muted max-h-40 overflow-y-auto leading-relaxed whitespace-pre-wrap">
            {entry.original || <span className="italic opacity-40">empty</span>}
          </div>
        </td>
        {/* Translation */}
        <td className="px-2 py-2 align-top min-w-[200px]">
          {isReserved ? (
            <div className="flex items-center gap-1.5 px-2 py-1.5 rounded bg-accent/5 border border-accent/20">
              <Lock size={11} className="text-accent/60 shrink-0" />
              <span className="text-xs text-text-muted/60 italic truncate">
                {entry.reserved_by ? `Reserved by ${entry.reserved_by}` : 'Being translated…'}
              </span>
            </div>
          ) : (
            <TranslationCell entry={entry} modName={modName} onSaved={onSaved} />
          )}
        </td>
        {/* Status + Source */}
        <td className="px-2 py-2 align-top">
          <div className="flex flex-col gap-1">
            <StatusBadge status={entry.status} />
            {entry.source && <SourceBadge source={entry.source} />}
            {isReserved && (
              <span className="text-[10px] font-mono text-accent/60 uppercase tracking-wide">reserved</span>
            )}
          </div>
        </td>
        {/* Score */}
        <td className="px-2 py-2 align-top text-center">
          <ScoreBadge score={entry.quality_score} />
        </td>
        {/* Actions */}
        <td className="px-2 py-2 align-top">
          <div className="flex items-center gap-1">
            {!isReserved && (
              <button
                onClick={() => onTranslateOne(entry)}
                disabled={isTranslating}
                title="Translate this string with AI"
                className={cn(
                  'flex items-center justify-center w-7 h-7 rounded-md transition-colors',
                  isTranslating
                    ? 'text-text-muted/40 cursor-not-allowed'
                    : 'text-warning/70 hover:text-warning hover:bg-warning/10',
                )}
              >
                {isTranslating ? (
                  <RefreshCw size={13} className="animate-spin" />
                ) : (
                  <Zap size={13} />
                )}
              </button>
            )}
            {!isReserved && entry.original && (
              <button
                onClick={() => onCopyFromSource(entry)}
                title="Copy original → translation"
                className="flex items-center justify-center w-7 h-7 rounded-md text-text-muted/50 hover:text-text-main hover:bg-bg-card2 transition-colors opacity-0 group-hover:opacity-100"
              >
                <Copy size={13} />
              </button>
            )}
            {dupCount > 0 && !isReserved && entry.translation && (
              <button
                onClick={() => onSyncDuplicates(entry)}
                title={`Apply to all ${dupCount} identical strings`}
                className="flex items-center justify-center w-7 h-7 rounded-md text-accent/60 hover:text-accent hover:bg-accent/10 transition-colors opacity-0 group-hover:opacity-100"
              >
                <Users size={13} />
              </button>
            )}
            {isNeedsReview && entry.id > 0 && !isReserved && (
              <button
                onClick={handleApprove}
                disabled={approving}
                title="Approve — mark as translated"
                className="flex items-center justify-center w-7 h-7 rounded-md text-success/70 hover:text-success hover:bg-success/10 transition-colors opacity-0 group-hover:opacity-100"
              >
                {approving ? <RefreshCw size={13} className="animate-spin" /> : <CheckCircle size={13} />}
              </button>
            )}
            {entry.id > 0 && (
              <button
                onClick={() => setShowHistory(true)}
                title="View translation history"
                className="flex items-center justify-center w-7 h-7 rounded-md text-text-muted/50 hover:text-accent hover:bg-accent/10 transition-colors opacity-0 group-hover:opacity-100"
              >
                <History size={13} />
              </button>
            )}
          </div>
        </td>
      </tr>
      {error && (
        <tr>
          <td colSpan={8} className="px-3 pb-2">
            <div className="flex items-center gap-1.5 text-xs text-danger bg-danger/10 border border-danger/20 rounded px-2 py-1">
              <AlertTriangle size={11} className="shrink-0" />
              {error}
            </div>
          </td>
        </tr>
      )}
      {showHistory && entry.id > 0 && (
        <StringHistoryModal
          stringId={entry.id}
          stringKey={entry.key}
          onClose={() => setShowHistory(false)}
        />
      )}
    </>
  )
}

// ── Find & Replace panel ──────────────────────────────────────────────────────

interface FindReplacePanelProps {
  scope: string
  modName: string
  onClose: () => void
  onDone: () => void
}

function FindReplacePanel({ scope, modName, onClose, onDone }: FindReplacePanelProps) {
  const [findText, setFindText] = useState('')
  const [replaceText, setReplaceText] = useState('')
  const [replaceResult, setReplaceResult] = useState<string | null>(null)
  const [replacing, setReplacing] = useState(false)

  const handleReplaceAll = async () => {
    if (!findText) return
    setReplacing(true)
    setReplaceResult(null)
    try {
      const r = await modsApi.replaceStrings(modName, {
        find: findText,
        replace: replaceText,
        scope: scope !== 'all' ? scope : undefined,
      })
      setReplaceResult(`Replaced ${r.count} translation${r.count !== 1 ? 's' : ''}`)
      onDone()
    } finally {
      setReplacing(false)
    }
  }

  return (
    <div className="flex items-center gap-2 px-5 py-2.5 bg-bg-card2 border-b border-border-subtle shrink-0">
      <Replace size={13} className="text-text-muted/60 shrink-0" />
      <input
        type="text"
        placeholder="Find in translations…"
        value={findText}
        onChange={(e) => setFindText(e.target.value)}
        className="px-2 py-1.5 rounded-lg bg-bg-card border border-border-subtle text-xs text-text-main placeholder-text-muted focus:outline-none focus:border-accent/60 w-48"
      />
      <span className="text-text-muted/40 text-xs">→</span>
      <input
        type="text"
        placeholder="Replace with…"
        value={replaceText}
        onChange={(e) => setReplaceText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') void handleReplaceAll() }}
        className="px-2 py-1.5 rounded-lg bg-bg-card border border-border-subtle text-xs text-text-main placeholder-text-muted focus:outline-none focus:border-accent/60 w-48"
      />
      <button
        onClick={() => void handleReplaceAll()}
        disabled={replacing || !findText}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 text-xs font-medium transition-colors disabled:opacity-50"
      >
        {replacing ? <RefreshCw size={12} className="animate-spin" /> : <Replace size={12} />}
        Replace All
      </button>
      {replaceResult && (
        <span className="text-xs text-success">{replaceResult}</span>
      )}
      <div className="flex-1" />
      <button
        onClick={onClose}
        className="flex items-center justify-center w-6 h-6 rounded text-text-muted/50 hover:text-text-main hover:bg-bg-card transition-colors"
      >
        <X size={13} />
      </button>
    </div>
  )
}

// ── Pagination ───────────────────────────────────────────────────────────────

interface PaginationProps {
  page: number
  pages: number
  total: number
  onPage: (p: number) => void
}

function Pagination({ page, pages, total, onPage }: PaginationProps) {
  const [jumpValue, setJumpValue] = useState('')

  const handleJump = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      const n = parseInt(jumpValue, 10)
      if (!isNaN(n) && n >= 1 && n <= pages) {
        onPage(n)
        setJumpValue('')
      }
    }
  }

  return (
    <div className="flex items-center gap-3 py-3 px-4 border-t border-border-subtle bg-bg-card">
      <span className="text-xs text-text-muted">
        {total} string{total !== 1 ? 's' : ''}
      </span>
      <div className="flex-1" />
      <button
        onClick={() => onPage(page - 1)}
        disabled={page <= 1}
        className="flex items-center gap-1 px-2 py-1 rounded text-xs text-text-muted hover:text-text-main hover:bg-bg-card2 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        <ChevronLeft size={14} />
        Prev
      </button>
      <span className="text-xs text-text-muted">
        Page{' '}
        <span className="text-text-main font-medium">{page}</span>
        {' '}of{' '}
        <span className="text-text-main font-medium">{pages}</span>
      </span>
      <button
        onClick={() => onPage(page + 1)}
        disabled={page >= pages}
        className="flex items-center gap-1 px-2 py-1 rounded text-xs text-text-muted hover:text-text-main hover:bg-bg-card2 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        Next
        <ChevronRight size={14} />
      </button>
      {pages > 2 && (
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-text-muted">Go:</span>
          <input
            type="number"
            min={1}
            max={pages}
            value={jumpValue}
            onChange={(e) => setJumpValue(e.target.value)}
            onKeyDown={handleJump}
            placeholder={String(page)}
            className="w-14 px-2 py-1 rounded bg-bg-card2 border border-border-subtle text-xs text-text-main text-center focus:outline-none focus:border-accent/60"
          />
        </div>
      )}
    </div>
  )
}

// ── Page-cache shape ──────────────────────────────────────────────────────────

interface StringsPage {
  strings: StringEntry[]
  total: number
  page: number
  per: number
  pages: number
  scope_counts?: Record<string, number>
}

/** Adjust the review bucket when a single string changes status. */
function patchReviewCount(
  counts: Record<string, number> | undefined,
  oldStatus: string | undefined,
  newStatus: string,
): Record<string, number> | undefined {
  if (!counts) return counts
  const wasReview = oldStatus === 'needs_review'
  const isReview  = newStatus === 'needs_review'
  if (wasReview === isReview) return counts
  return { ...counts, review: Math.max(0, (counts.review ?? 0) + (isReview ? 1 : -1)) }
}

// ── Main page ────────────────────────────────────────────────────────────────

function ModStringsPage() {
  const { modName } = Route.useParams()
  const decodedName = decodeURIComponent(modName)
  const navigate = useNavigate({ from: '/mods/$modName/strings' })
  const { scope, status, q, page, sort_by, sort_dir, rec_type } = Route.useSearch()
  const machines = useMachines()
  const queryClient = useQueryClient()

  // Per-key translate spinner tracking
  const [translatingKeys, setTranslatingKeys] = useState<Set<string>>(new Set())
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({})

  // Bulk selection for approve
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())

  // Find & Replace panel
  const [findReplaceOpen, setFindReplaceOpen] = useState(false)

  // Debounced search input
  const [searchInput, setSearchInput] = useState(q)
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleSearchChange = (value: string) => {
    setSearchInput(value)
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current)
    searchDebounceRef.current = setTimeout(() => {
      navigate({ search: (prev) => ({ ...prev, q: value, page: 1 }) })
    }, 300)
  }

  const queryKey = QK.modStrings(decodedName, { scope, status, q, page, per: PER_PAGE, sort_by, sort_dir, rec_type })

  const { data, isLoading, isError } = useQuery({
    queryKey,
    queryFn: () =>
      modsApi.getStrings(decodedName, {
        scope,
        status,
        q,
        page,
        per: PER_PAGE,
        sort_by: sort_by || undefined,
        sort_dir: sort_dir || undefined,
        rec_type: rec_type || undefined,
      }),
    staleTime: 60_000,
    retry: 1,
  })

  // ── Rec type dropdown ──────────────────────────────────────────────────────

  const { data: recTypesData } = useQuery({
    queryKey: ['recTypes', decodedName],
    queryFn: () => modsApi.getRecTypes(decodedName),
    staleTime: 5 * 60_000,
  })
  const recTypes = recTypesData?.rec_types ?? []

  // ── Live updates ───────────────────────────────────────────────────────────

  const liveUpdates = useModLiveUpdates(decodedName)
  const clearLiveUpdates = useClearModLiveUpdates(decodedName)
  const [flashedKeys, setFlashedKeys] = useState<Set<string>>(new Set())
  const processedCount = useRef(0)

  // Check if there's a running job for this mod
  const { data: allJobs = [] } = useQuery({ queryKey: QK.jobs(), queryFn: jobsApi.list })
  const activeJob = allJobs.find(
    (j) => j.mod_name === decodedName && ['running', 'pending'].includes(j.status),
  )

  // Reservation polling — only when a job is active for this mod
  const reservedKeys = useReservations(decodedName, allJobs)

  // Apply new live updates to the current page cache
  useEffect(() => {
    const newUpdates: StringUpdate[] = liveUpdates.slice(processedCount.current)
    if (newUpdates.length === 0) return
    processedCount.current = liveUpdates.length

    // Update the strings query cache for the current page
    queryClient.setQueryData<StringsPage>(
      queryKey,
      (old) => {
        if (!old) return old
        const updateMap = new Map(newUpdates.map((u) => [u.key, u]))
        let scope_counts = old.scope_counts
        const strings = old.strings.map((s) => {
          const u = updateMap.get(s.key)
          if (!u) return s
          scope_counts = patchReviewCount(scope_counts, s.status, u.status)
          return { ...s, translation: u.translation, status: u.status, quality_score: u.quality_score }
        })
        return { ...old, strings, scope_counts }
      },
    )

    // Flash updated rows
    const keys = new Set(newUpdates.map((u) => u.key))
    setFlashedKeys((prev) => new Set([...prev, ...keys]))
    setTimeout(() => {
      setFlashedKeys((prev) => {
        const next = new Set(prev)
        keys.forEach((k) => next.delete(k))
        return next
      })
    }, 2000)
  }, [liveUpdates]) // eslint-disable-line react-hooks/exhaustive-deps

  // Clear live updates on unmount
  useEffect(() => () => clearLiveUpdates(), []) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Translate-one ──────────────────────────────────────────────────────────

  const handleTranslateOne = useCallback(
    async (entry: StringEntry) => {
      const rowKey = `${entry.esp}::${entry.key}`
      setTranslatingKeys((prev) => new Set(prev).add(rowKey))
      setRowErrors((prev) => {
        const next = { ...prev }
        delete next[rowKey]
        return next
      })

      try {
        const result = await modsApi.translateOne(decodedName, {
          key: entry.key,
          esp: entry.esp,
          original: entry.original,
          force_ai: true,
          machines,
        })

        const r = result as Record<string, unknown>
        const translated = r['translation'] as string | undefined
        const serverStatus = r['status'] as string | undefined
        const serverScore = r['quality_score'] as number | null | undefined

        if (translated !== undefined) {
          queryClient.setQueryData<StringsPage>(queryKey, (old) => {
            if (!old) return old
            let scope_counts = old.scope_counts
            const strings = old.strings.map((s) => {
              if (s.key !== entry.key || s.esp !== entry.esp) return s
              const newStatus = serverStatus ?? 'translated'
              scope_counts = patchReviewCount(scope_counts, s.status, newStatus)
              return {
                ...s,
                translation: translated,
                status: newStatus,
                quality_score: serverScore !== undefined ? serverScore : s.quality_score,
              }
            })
            return { ...old, strings, scope_counts }
          })
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Translation failed'
        setRowErrors((prev) => ({ ...prev, [rowKey]: msg }))
      } finally {
        setTranslatingKeys((prev) => {
          const next = new Set(prev)
          next.delete(rowKey)
          return next
        })
      }
    },
    [decodedName, machines, queryClient, queryKey],
  )

  // ── Update string (on blur) ────────────────────────────────────────────────

  const handleStringSaved = useCallback(
    (key: string, esp: string, translation: string, quality_score: number | null, status: string | null) => {
      queryClient.setQueryData<StringsPage>(queryKey, (old) => {
        if (!old) return old
        let scope_counts = old.scope_counts
        const strings = old.strings.map((s) => {
          if (s.key !== key || s.esp !== esp) return s
          const newStatus = status ?? (translation ? 'translated' : s.status)
          scope_counts = patchReviewCount(scope_counts, s.status, newStatus)
          return {
            ...s,
            translation,
            status: newStatus,
            quality_score: quality_score !== null ? quality_score : s.quality_score,
          }
        })
        return { ...old, strings, scope_counts }
      })
    },
    [queryClient, queryKey],
  )

  // ── Approve string ────────────────────────────────────────────────────────

  const handleApproved = useCallback(
    (key: string, esp: string) => {
      queryClient.setQueryData<StringsPage>(queryKey, (old) => {
        if (!old) return old
        let scope_counts = old.scope_counts
        const strings = old.strings.map((s) => {
          if (s.key !== key || s.esp !== esp) return s
          scope_counts = patchReviewCount(scope_counts, s.status, 'translated')
          return { ...s, status: 'translated' }
        })
        return { ...old, strings, scope_counts }
      })
      // Sync mod detail stat counts immediately
      void queryClient.invalidateQueries({ queryKey: QK.mod(decodedName) })
    },
    [queryClient, queryKey, decodedName],
  )

  // ── Copy from source ─────────────────────────────────────────────────────

  const handleCopyFromSource = useCallback(async (entry: StringEntry) => {
    await modsApi.updateString(decodedName, {
      key: entry.key, esp: entry.esp, translation: entry.original,
    })
    handleStringSaved(entry.key, entry.esp, entry.original, 100, 'translated')
  }, [decodedName, handleStringSaved])

  // ── Sync duplicates ──────────────────────────────────────────────────────

  const handleSyncDuplicates = useCallback(async (entry: StringEntry) => {
    const result = await modsApi.syncDuplicates(decodedName, {
      original: entry.original,
      translation: entry.translation,
      status: entry.status,
      quality_score: entry.quality_score,
    })
    if (result.ok && result.count > 0) {
      void queryClient.invalidateQueries({ queryKey: ['mods', decodedName, 'strings'] })
    }
  }, [decodedName, queryClient])

  // ── Bulk approve mutation ─────────────────────────────────────────────────

  const bulkApproveMut = useMutation({
    mutationFn: (ids: number[]) => modsApi.approveBulk(decodedName, ids),
    onSuccess: (result) => {
      if (!result.ok) return
      // Optimistically mark approved strings as translated in cache
      queryClient.setQueryData<{ strings: typeof strings; scope_counts?: Record<string, number>; [k: string]: unknown }>(queryKey, (old) => {
        if (!old) return old
        let scope_counts = old.scope_counts
        const updated = old.strings.map((s) => {
          if (!selectedIds.has(s.id)) return s
          scope_counts = patchReviewCount(scope_counts, s.status, 'translated')
          return { ...s, status: 'translated' }
        })
        return { ...old, strings: updated, scope_counts }
      })
      setSelectedIds(new Set())
      void queryClient.invalidateQueries({ queryKey: QK.mod(decodedName) })
    },
  })

  // ── Bulk translate mutation ────────────────────────────────────────────────

  const bulkTranslateMut = useMutation({
    mutationFn: () =>
      jobsApi.create({
        type: 'translate_strings',
        mods: [decodedName],
        scope,
        options: { machines },
      }),
    onSuccess: (result) => {
      if (result.ok && result.job_id) {
        queryClient.invalidateQueries({ queryKey: QK.jobs() })
        navigate({ to: '/jobs/$jobId', params: { jobId: result.job_id } })
      }
    },
  })

  // ── Sort helper ───────────────────────────────────────────────────────────

  const setSort = (col: string) => {
    navigate({
      search: (prev) => {
        if (prev.sort_by === col) {
          if (prev.sort_dir === 'asc') return { ...prev, sort_dir: 'desc' }
          return { ...prev, sort_by: '', sort_dir: 'asc' }
        }
        return { ...prev, sort_by: col, sort_dir: 'asc', page: 1 }
      },
    })
  }

  // ── Navigation helpers ────────────────────────────────────────────────────

  const setScope = (s: string) => navigate({ search: (prev) => ({ ...prev, scope: s, page: 1 }) })
  const setStatus = (s: string) => navigate({ search: (prev) => ({ ...prev, status: s, page: 1 }) })
  const setPage = (p: number) => navigate({ search: (prev) => ({ ...prev, page: p }) })

  // ── Render ────────────────────────────────────────────────────────────────

  const strings = data?.strings ?? []
  const scopeCounts = data?.scope_counts ?? {}

  return (
    <div className="flex flex-col h-full gap-0 -m-6">
      {/* Top bar */}
      <div className="flex flex-wrap items-center gap-2 px-5 py-3 bg-bg-card border-b border-border-subtle shrink-0">
        {/* Back */}
        <Link
          to="/mods/$modName"
          params={{ modName }}
          className="flex items-center gap-1 text-sm text-text-muted hover:text-text-main transition-colors shrink-0"
        >
          <ChevronLeft size={15} />
          <span className="font-medium truncate max-w-[140px]" title={decodedName}>
            {decodedName}
          </span>
        </Link>

        <span className="text-border-subtle">|</span>

        {/* Live badge */}
        {activeJob && (
          <span className="inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full font-medium bg-success/20 text-success border border-success/30">
            <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse" />
            Live
          </span>
        )}

        {/* Scope tabs */}
        <div className="flex items-center gap-0.5 bg-bg-card2 border border-border-subtle rounded-lg p-0.5 overflow-x-auto">
          {SCOPES.map((s) => {
            const count = scopeCounts[s] ?? 0
            const isActive = scope === s
            return (
              <button
                key={s}
                onClick={() => setScope(s)}
                className={cn(
                  'px-2.5 py-1 rounded-md text-xs font-medium transition-colors whitespace-nowrap flex items-center gap-1',
                  isActive
                    ? 'bg-accent text-bg-base'
                    : 'text-text-muted hover:text-text-main',
                )}
              >
                {s}
                {count > 0 && s !== 'all' && (
                  <span
                    className={cn(
                      'text-[10px] rounded-full px-1 tabular-nums',
                      isActive ? 'bg-bg-base/20' : 'bg-bg-card text-text-muted/60',
                    )}
                  >
                    {count}
                  </span>
                )}
              </button>
            )
          })}
        </div>

        {/* Status filter */}
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="px-2 py-1.5 rounded-lg bg-bg-card2 border border-border-subtle text-xs text-text-muted focus:outline-none focus:border-accent/60 transition-colors"
        >
          <option value="all">All statuses</option>
          <option value="pending">Pending</option>
          <option value="translated">Translated</option>
          <option value="needs_review">Needs Review {scopeCounts.review ? `(${scopeCounts.review})` : ''}</option>
          <option value="untranslatable">Untranslatable {scopeCounts.untranslatable ? `(${scopeCounts.untranslatable})` : ''}</option>
          <option value="reserved">Reserved {scopeCounts.reserved ? `(${scopeCounts.reserved})` : ''}</option>
        </select>

        {/* Record type filter */}
        {recTypes.length > 0 && (
          <select
            value={rec_type}
            onChange={(e) => navigate({ search: (prev) => ({ ...prev, rec_type: e.target.value, page: 1 }) })}
            className="px-2 py-1.5 rounded-lg bg-bg-card2 border border-border-subtle text-xs text-text-muted focus:outline-none focus:border-accent/60"
          >
            <option value="">All types</option>
            {recTypes.map((rt) => <option key={rt} value={rt}>{rt}</option>)}
          </select>
        )}

        {/* Search */}
        <div className="relative flex-1 min-w-[160px] max-w-xs">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
          <input
            type="text"
            placeholder="Search strings…"
            value={searchInput}
            onChange={(e) => handleSearchChange(e.target.value)}
            className="w-full pl-7 pr-3 py-1.5 bg-bg-card2 border border-border-subtle rounded-lg text-xs text-text-main placeholder-text-muted focus:outline-none focus:border-accent/60 transition-colors"
          />
        </div>

        <div className="flex-1" />

        {/* Find & Replace toggle */}
        <button
          onClick={() => setFindReplaceOpen((v) => !v)}
          title="Find & Replace"
          className={cn(
            'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs font-medium transition-colors',
            findReplaceOpen
              ? 'bg-accent/20 text-accent border-accent/40'
              : 'bg-bg-card2 text-text-muted border-border-subtle hover:text-text-main',
          )}
        >
          <Replace size={13} />
        </button>

        {/* Bulk approve — shown when strings are selected */}
        {selectedIds.size > 0 && (
          <button
            onClick={() => bulkApproveMut.mutate([...selectedIds])}
            disabled={bulkApproveMut.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-success/20 text-success border border-success/30 hover:bg-success/30 text-xs font-medium transition-colors disabled:opacity-50"
          >
            {bulkApproveMut.isPending ? (
              <RefreshCw size={13} className="animate-spin" />
            ) : (
              <CheckSquare size={13} />
            )}
            Approve {selectedIds.size}
          </button>
        )}

        {/* Bulk translate */}
        <button
          onClick={() => bulkTranslateMut.mutate()}
          disabled={bulkTranslateMut.isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 text-xs font-medium transition-colors disabled:opacity-50"
        >
          {bulkTranslateMut.isPending ? (
            <RefreshCw size={13} className="animate-spin" />
          ) : (
            <Play size={13} />
          )}
          Translate All
        </button>
      </div>

      {/* Find & Replace panel */}
      {findReplaceOpen && (
        <FindReplacePanel
          scope={scope}
          modName={decodedName}
          onClose={() => setFindReplaceOpen(false)}
          onDone={() => void queryClient.invalidateQueries({ queryKey: ['mods', decodedName, 'strings'] })}
        />
      )}

      {/* Table area */}
      <div className="flex-1 overflow-auto">
        {isError ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-center p-8">
            <AlertTriangle size={32} className="text-warning" />
            <p className="text-text-muted text-sm">
              Failed to load strings. The server may have returned an unexpected response.
            </p>
            <button
              onClick={() => queryClient.invalidateQueries({ queryKey })}
              className="text-accent text-sm hover:underline"
            >
              Retry
            </button>
          </div>
        ) : isLoading ? (
          <div className="p-6 space-y-2 animate-pulse">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="h-12 bg-bg-card rounded-lg" />
            ))}
          </div>
        ) : strings.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-2 text-text-muted p-8 text-center">
            <Search size={28} className="opacity-30" />
            <p className="text-sm">No strings found</p>
            {(q || status !== 'all' || scope !== 'all' || rec_type) && (
              <button
                onClick={() =>
                  navigate({ search: () => ({ scope: 'all', status: 'all', q: '', page: 1, sort_by: '', sort_dir: 'asc', rec_type: '' }) })
                }
                className="text-accent text-xs hover:underline mt-1"
              >
                Clear filters
              </button>
            )}
          </div>
        ) : (
          <table className="w-full text-left border-collapse">
            <thead className="sticky top-0 z-10 bg-bg-card border-b border-border-subtle">
              <tr>
                <th className="px-2 py-2 w-8">
                  <input
                    type="checkbox"
                    className="w-3.5 h-3.5 accent-accent cursor-pointer"
                    checked={strings.length > 0 && strings.every((s) => selectedIds.has(s.id))}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setSelectedIds(new Set(strings.map((s) => s.id)))
                      } else {
                        setSelectedIds(new Set())
                      }
                    }}
                  />
                </th>
                <th className="px-3 py-2 text-xs text-text-muted font-medium w-10">#</th>
                <th
                  className="px-2 py-2 text-xs text-text-muted font-medium w-[120px] cursor-pointer select-none hover:text-text-main transition-colors"
                  onClick={() => setSort('esp_name')}
                >
                  <span className="inline-flex items-center gap-1">
                    ESP <SortIcon col="esp_name" current={sort_by} dir={sort_dir} />
                  </span>
                </th>
                <th
                  className="px-2 py-2 text-xs text-text-muted font-medium cursor-pointer select-none hover:text-text-main transition-colors"
                  onClick={() => setSort('original')}
                >
                  <span className="inline-flex items-center gap-1">
                    Original <SortIcon col="original" current={sort_by} dir={sort_dir} />
                  </span>
                </th>
                <th
                  className="px-2 py-2 text-xs text-text-muted font-medium cursor-pointer select-none hover:text-text-main transition-colors"
                  onClick={() => setSort('translation')}
                >
                  <span className="inline-flex items-center gap-1">
                    Translation <SortIcon col="translation" current={sort_by} dir={sort_dir} />
                  </span>
                </th>
                <th
                  className="px-2 py-2 text-xs text-text-muted font-medium w-[90px] cursor-pointer select-none hover:text-text-main transition-colors"
                  onClick={() => setSort('status')}
                >
                  <span className="inline-flex items-center gap-1">
                    Status <SortIcon col="status" current={sort_by} dir={sort_dir} />
                  </span>
                </th>
                <th
                  className="px-2 py-2 text-xs text-text-muted font-medium w-12 text-center cursor-pointer select-none hover:text-text-main transition-colors"
                  onClick={() => setSort('quality_score')}
                >
                  <span className="inline-flex items-center gap-1 justify-center">
                    Score <SortIcon col="quality_score" current={sort_by} dir={sort_dir} />
                  </span>
                </th>
                <th className="px-2 py-2 text-xs text-text-muted font-medium w-10"></th>
              </tr>
            </thead>
            <tbody>
              {strings.map((entry, i) => {
                const rowKey = `${entry.esp}::${entry.key}`
                const offset = (page - 1) * PER_PAGE
                return (
                  <StringRow
                    key={rowKey}
                    entry={entry}
                    index={offset + i + 1}
                    modName={decodedName}
                    isTranslating={translatingKeys.has(rowKey)}
                    isReserved={reservedKeys.has(entry.key)}
                    onTranslateOne={handleTranslateOne}
                    onSaved={handleStringSaved}
                    onApproved={handleApproved}
                    onCopyFromSource={handleCopyFromSource}
                    onSyncDuplicates={handleSyncDuplicates}
                    error={rowErrors[rowKey]}
                    flashed={flashedKeys.has(entry.key)}
                    isSelected={selectedIds.has(entry.id)}
                    onToggleSelect={(id) =>
                      setSelectedIds((prev) => {
                        const next = new Set(prev)
                        next.has(id) ? next.delete(id) : next.add(id)
                        return next
                      })
                    }
                  />
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {data && data.pages > 1 && (
        <Pagination
          page={data.page}
          pages={data.pages}
          total={data.total}
          onPage={setPage}
        />
      )}
      {data && data.pages <= 1 && data.total > 0 && (
        <div className="py-2 px-4 border-t border-border-subtle text-xs text-text-muted/60 shrink-0">
          {data.total} string{data.total !== 1 ? 's' : ''}
        </div>
      )}
    </div>
  )
}
