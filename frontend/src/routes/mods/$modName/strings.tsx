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
} from 'lucide-react'
import { modsApi } from '@/api/mods'
import { jobsApi } from '@/api/jobs'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/utils'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { useMachines } from '@/hooks/useMachines'
import { SCOPES } from '@/lib/constants'
import { useModLiveUpdates, useClearModLiveUpdates } from '@/hooks/useModLiveUpdates'
import type { StringEntry, StringUpdate } from '@/types'

const PER_PAGE = 100

export const Route = createFileRoute('/mods/$modName/strings')({
  validateSearch: (search: Record<string, unknown>) => ({
    scope: (search.scope as string) || 'all',
    status: (search.status as string) || 'all',
    q: (search.q as string) || '',
    page: Number(search.page) || 1,
  }),
  component: ModStringsPage,
})

// ── Quality score badge ──────────────────────────────────────────────────────

function ScoreBadge({ score }: { score: number | null }) {
  if (score === null) return <span className="text-text-muted/40 text-xs">—</span>
  const colorClass =
    score >= 80 ? 'text-success' : score >= 50 ? 'text-warning' : 'text-danger'
  return (
    <span className={cn('font-mono text-xs font-semibold tabular-nums', colorClass)}>
      {score}
    </span>
  )
}

// ── Editable translation cell ────────────────────────────────────────────────

interface TranslationCellProps {
  entry: StringEntry
  modName: string
  onSaved: (key: string, esp: string, translation: string) => void
}

function TranslationCell({ entry, modName, onSaved }: TranslationCellProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(entry.translation)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const saveMutation = useMutation({
    mutationFn: (translation: string) =>
      modsApi.updateString(modName, { key: entry.key, esp: entry.esp, translation }),
    onSuccess: (_data, translation) => {
      onSaved(entry.key, entry.esp, translation)
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
          className="w-full min-h-[3rem] bg-bg-card2 border border-accent/40 rounded px-2 py-1.5 text-xs text-text-main resize-none focus:outline-none focus:border-accent transition-colors leading-relaxed"
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value)
            // auto-expand
            e.target.style.height = 'auto'
            e.target.style.height = `${e.target.scrollHeight}px`
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
        'min-h-[2rem] px-2 py-1.5 rounded cursor-text text-xs leading-relaxed hover:bg-bg-card2 transition-colors',
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
  onTranslateOne: (entry: StringEntry) => void
  onSaved: (key: string, esp: string, translation: string) => void
  error?: string
  flashed?: boolean
}

function StringRow({
  entry,
  index,
  modName,
  isTranslating,
  onTranslateOne,
  onSaved,
  error,
  flashed,
}: StringRowProps) {
  const espShort = entry.esp.split(/[/\\]/).pop() ?? entry.esp

  return (
    <>
      <tr
        className={cn(
          'border-t border-border-subtle hover:bg-bg-card2/30 transition-colors group',
          flashed && 'ring-1 ring-accent/50 bg-accent/5 transition-all duration-500',
        )}
      >
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
        </td>
        {/* Original */}
        <td className="px-2 py-2 align-top max-w-[260px]">
          <div className="text-xs text-text-muted line-clamp-2 leading-relaxed">
            {entry.original || <span className="italic opacity-40">empty</span>}
          </div>
        </td>
        {/* Translation */}
        <td className="px-2 py-2 align-top min-w-[200px]">
          <TranslationCell entry={entry} modName={modName} onSaved={onSaved} />
        </td>
        {/* Status */}
        <td className="px-2 py-2 align-top">
          <StatusBadge status={entry.status} />
        </td>
        {/* Score */}
        <td className="px-2 py-2 align-top text-center">
          <ScoreBadge score={entry.quality_score} />
        </td>
        {/* Actions */}
        <td className="px-2 py-2 align-top">
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
        </td>
      </tr>
      {error && (
        <tr>
          <td colSpan={7} className="px-3 pb-2">
            <div className="flex items-center gap-1.5 text-xs text-danger bg-danger/10 border border-danger/20 rounded px-2 py-1">
              <AlertTriangle size={11} className="shrink-0" />
              {error}
            </div>
          </td>
        </tr>
      )}
    </>
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

// ── Main page ────────────────────────────────────────────────────────────────

function ModStringsPage() {
  const { modName } = Route.useParams()
  const decodedName = decodeURIComponent(modName)
  const navigate = useNavigate({ from: '/mods/$modName/strings' })
  const { scope, status, q, page } = Route.useSearch()
  const machines = useMachines()
  const queryClient = useQueryClient()

  // Per-key translate spinner tracking
  const [translatingKeys, setTranslatingKeys] = useState<Set<string>>(new Set())
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({})

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

  const queryKey = QK.modStrings(decodedName, { scope, status, q, page, per: PER_PAGE })

  const { data, isLoading, isError } = useQuery({
    queryKey,
    queryFn: () =>
      modsApi.getStrings(decodedName, { scope, status, q, page, per: PER_PAGE }),
    staleTime: 60_000,
    retry: 1,
  })

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
        const strings = old.strings.map((s) =>
          updateMap.has(s.key)
            ? {
                ...s,
                translation: updateMap.get(s.key)!.translation,
                status: updateMap.get(s.key)!.status,
                quality_score: updateMap.get(s.key)!.quality_score,
              }
            : s,
        )
        return { ...old, strings }
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

        const translated = (result as Record<string, unknown>)['translation'] as string | undefined

        if (translated !== undefined) {
          // Optimistic update in query cache
          queryClient.setQueryData<StringsPage>(queryKey, (old) => {
            if (!old) return old
            return {
              ...old,
              strings: old.strings.map((s) =>
                s.key === entry.key && s.esp === entry.esp
                  ? { ...s, translation: translated, status: 'translated' }
                  : s,
              ),
            }
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
    (key: string, esp: string, translation: string) => {
      queryClient.setQueryData<StringsPage>(queryKey, (old) => {
        if (!old) return old
        return {
          ...old,
          strings: old.strings.map((s) =>
            s.key === key && s.esp === esp
              ? { ...s, translation, status: translation ? 'translated' : s.status }
              : s,
          ),
        }
      })
    },
    [queryClient, queryKey],
  )

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
          <option value="needs_review">Needs Review</option>
        </select>

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
            {(q || status !== 'all' || scope !== 'all') && (
              <button
                onClick={() =>
                  navigate({ search: () => ({ scope: 'all', status: 'all', q: '', page: 1 }) })
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
                <th className="px-3 py-2 text-xs text-text-muted font-medium w-10">#</th>
                <th className="px-2 py-2 text-xs text-text-muted font-medium w-[120px]">ESP</th>
                <th className="px-2 py-2 text-xs text-text-muted font-medium">Original</th>
                <th className="px-2 py-2 text-xs text-text-muted font-medium">Translation</th>
                <th className="px-2 py-2 text-xs text-text-muted font-medium w-[90px]">Status</th>
                <th className="px-2 py-2 text-xs text-text-muted font-medium w-12 text-center">Score</th>
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
                    onTranslateOne={handleTranslateOne}
                    onSaved={handleStringSaved}
                    error={rowErrors[rowKey]}
                    flashed={flashedKeys.has(entry.key)}
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
