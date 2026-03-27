import { useState, useEffect, useMemo } from 'react'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Search, RefreshCw, Play, FileText, Archive,
  ArrowUpDown, CheckSquare, Square, X, ChevronDown,
  AlertTriangle, Clock,
} from 'lucide-react'
import { modsApi } from '@/api/mods'
import { jobsApi } from '@/api/jobs'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/utils'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { ProgressBar } from '@/components/shared/ProgressBar'
import { useMachines } from '@/hooks/useMachines'
import { useJobsStore } from '@/stores/jobsStore'
import type { ModInfo } from '@/types'

type SortBy = 'status' | 'name' | 'pct' | 'pending'

export const Route = createFileRoute('/mods/')({
  validateSearch: (search: Record<string, unknown>) => ({
    status:  (search.status  as string)  || 'all',
    q:       (search.q       as string)  || '',
    sort_by: (search.sort_by as SortBy)  || 'status',
    page:    Number(search.page)         || 0,
  }),
  component: ModsPage,
})

const STATUS_TABS = [
  { value: 'all',          label: 'All' },
  { value: 'pending',      label: 'Pending' },
  { value: 'partial',      label: 'Partial' },
  { value: 'done',         label: 'Done' },
  { value: 'error',        label: 'Error' },
  { value: 'needs_review', label: 'Needs Review' },
  { value: 'validation',   label: 'Validation Issues' },
] as const

const SORT_OPTIONS: { value: SortBy; label: string }[] = [
  { value: 'status',  label: 'Status' },
  { value: 'name',    label: 'Name' },
  { value: 'pct',     label: '% Done' },
  { value: 'pending', label: 'Pending strings' },
]

const STATUS_SORT_ORDER: Record<string, number> = {
  error:      0,
  pending:    1,
  partial:    2,
  unknown:    3,
  no_strings: 4,
  done:       5,
}

function sortMods(mods: ModInfo[], sortBy: SortBy): ModInfo[] {
  return [...mods].sort((a, b) => {
    switch (sortBy) {
      case 'name':
        return a.folder_name.localeCompare(b.folder_name)
      case 'pct':
        return (b.pct ?? 0) - (a.pct ?? 0)
      case 'pending': {
        const aPending = (a.total_strings ?? 0) - (a.translated_strings ?? 0)
        const bPending = (b.total_strings ?? 0) - (b.translated_strings ?? 0)
        if (bPending !== aPending) return bPending - aPending
        return a.folder_name.localeCompare(b.folder_name)
      }
      default: { // status
        const ao = STATUS_SORT_ORDER[a.status] ?? 2
        const bo = STATUS_SORT_ORDER[b.status] ?? 2
        if (ao !== bo) return ao - bo
        return a.folder_name.localeCompare(b.folder_name)
      }
    }
  })
}

function SkeletonCard() {
  return (
    <div className="card p-5 animate-pulse space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div className="h-4 bg-bg-card2 rounded w-2/3" />
        <div className="h-5 bg-bg-card2 rounded-full w-16" />
      </div>
      <div className="h-2 bg-bg-card2 rounded-full" />
      <div className="flex items-center justify-between">
        <div className="h-3 bg-bg-card2 rounded w-24" />
        <div className="h-7 bg-bg-card2 rounded w-20" />
      </div>
    </div>
  )
}

interface ModCardProps {
  mod: ModInfo
  selected: boolean
  onToggleSelect: (name: string) => void
}

function ModCard({ mod, selected, onToggleSelect }: ModCardProps) {
  const navigate = useNavigate()
  const machines = useMachines()
  const queryClient = useQueryClient()
  const pendingCount = (mod.total_strings ?? 0) - (mod.translated_strings ?? 0)

  // Live job state for this mod from SSE-backed store
  const activeJob = useJobsStore((s) => {
    const all = Object.values(s.jobs)
    return all.find((j) =>
      (j.status === 'running' || j.status === 'pending') &&
      (j.mod_name === mod.folder_name ||
        (j.params?.mod_name as string | undefined) === mod.folder_name ||
        (j.params?.mods as string[] | undefined)?.includes(mod.folder_name))
    )
  })

  const translateMutation = useMutation({
    mutationFn: () =>
      jobsApi.create({
        type: 'translate_mod',
        mods: [mod.folder_name],
        options: { machines },
      }),
    onSuccess: (data) => {
      if (data.ok && data.job_id) {
        navigate({ to: '/jobs/$jobId', params: { jobId: data.job_id } })
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: QK.jobs() })
    },
  })

  const isActionable = mod.status !== 'no_strings' && mod.status !== 'done'

  return (
    <div
      className={cn(
        'card p-5 flex flex-col gap-3 hover:bg-bg-card2/50 transition-colors relative',
        selected && 'ring-2 ring-accent/60',
      )}
    >
      {/* Selection checkbox */}
      <button
        onClick={() => onToggleSelect(mod.folder_name)}
        className={cn(
          'absolute top-3 right-3 transition-opacity',
          selected ? 'opacity-100' : 'opacity-0 group-hover:opacity-100',
        )}
        aria-label={selected ? 'Deselect' : 'Select'}
      >
        {selected
          ? <CheckSquare size={16} className="text-accent" />
          : <Square size={16} className="text-text-muted/40 hover:text-text-muted" />}
      </button>

      {/* Header */}
      <div className="flex items-start gap-2 min-w-0 pr-5">
        <Link
          to="/mods/$modName"
          params={{ modName: encodeURIComponent(mod.folder_name) }}
          className="text-sm font-semibold text-text-main hover:text-accent transition-colors truncate leading-snug flex-1"
          title={mod.folder_name}
        >
          {mod.folder_name}
        </Link>
        <div className="flex items-center gap-1 shrink-0">
          {mod.needs_review_strings > 0 && (
            <span
              className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium bg-warning/20 text-warning"
              title={`${mod.needs_review_strings} string${mod.needs_review_strings !== 1 ? 's' : ''} need review`}
            >
              <Clock size={9} />
              {mod.needs_review_strings}
            </span>
          )}
          {mod.has_validation_issues && (
            <span
              className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium bg-danger/20 text-danger"
              title={`${mod.validation_issues_count} validation issue${mod.validation_issues_count !== 1 ? 's' : ''}`}
            >
              <AlertTriangle size={9} />
              {mod.validation_issues_count}
            </span>
          )}
          <StatusBadge status={mod.status} className="shrink-0" />
        </div>
      </div>

      {/* Progress bar */}
      {mod.total_strings > 0 && (
        <ProgressBar pct={mod.pct} />
      )}

      {/* Counts + file icons */}
      <div className="flex items-center gap-3 text-xs text-text-muted">
        {mod.total_strings > 0 ? (
          <span>
            <span className="text-success font-medium">{mod.translated_strings}</span>
            {' / '}
            <span className="text-text-main font-medium">{mod.total_strings}</span>
            {' strings'}
            {pendingCount > 0 && (
              <span className="ml-1.5 text-warning/80">({pendingCount} pending)</span>
            )}
          </span>
        ) : (
          <span className="italic">No strings</span>
        )}
        <span className="flex-1" />
        {mod.esp_files.length > 0 && (
          <span className="flex items-center gap-1" title={`${mod.esp_files.length} ESP file(s)`}>
            <FileText size={12} />
            {mod.esp_files.length}
          </span>
        )}
        {mod.bsa_files.length > 0 && (
          <span className="flex items-center gap-1" title={`${mod.bsa_files.length} BSA file(s)`}>
            <Archive size={12} />
            {mod.bsa_files.length}
          </span>
        )}
      </div>

      {/* Live job status */}
      {activeJob && (
        <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-accent/8 border border-accent/20 text-[10px]">
          <RefreshCw size={9} className={cn('shrink-0', activeJob.status === 'running' ? 'animate-spin text-accent' : 'text-text-muted/60')} />
          <span className="text-accent/80 truncate flex-1 min-w-0">
            {activeJob.status === 'pending'
              ? 'Queued…'
              : activeJob.progress?.message || 'Translating…'}
          </span>
          {activeJob.status === 'running' && activeJob.pct > 0 && (
            <span className="font-mono text-accent/60 shrink-0">{activeJob.pct.toFixed(0)}%</span>
          )}
        </div>
      )}

      {/* Action row */}
      <div className="flex items-center gap-2 pt-1 border-t border-border-subtle">
        <Link
          to="/mods/$modName"
          params={{ modName: encodeURIComponent(mod.folder_name) }}
          className="flex-1 text-center text-xs px-2 py-1.5 rounded bg-bg-card2 hover:bg-bg-card2/80 text-text-muted hover:text-text-main transition-colors"
        >
          Details
        </Link>
        <button
          onClick={(e) => {
            e.preventDefault()
            translateMutation.mutate()
          }}
          disabled={translateMutation.isPending || mod.status === 'no_strings'}
          className={cn(
            'flex items-center gap-1.5 text-xs px-3 py-1.5 rounded font-medium transition-colors',
            isActionable
              ? 'bg-accent/20 text-accent hover:bg-accent/30 border border-accent/30'
              : 'bg-bg-card2 text-text-muted cursor-not-allowed opacity-50',
          )}
          title={mod.status === 'no_strings' ? 'No strings to translate' : 'Translate this mod'}
        >
          {translateMutation.isPending ? (
            <RefreshCw size={12} className="animate-spin" />
          ) : (
            <Play size={12} />
          )}
          Translate
        </button>
      </div>
    </div>
  )
}

function BatchToolbar({
  selected,
  onClear,
  onTranslate,
  onApply,
  isBusy,
}: {
  selected: string[]
  onClear: () => void
  onTranslate: () => void
  onApply: () => void
  isBusy: boolean
}) {
  return (
    <div className="flex items-center gap-3 px-4 py-2.5 rounded-lg bg-accent/10 border border-accent/30 text-sm">
      <span className="text-accent font-medium">{selected.length} selected</span>
      <div className="flex-1" />
      <button
        onClick={onTranslate}
        disabled={isBusy}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-accent text-bg-base font-medium hover:bg-accent/90 disabled:opacity-50 transition-colors"
      >
        {isBusy ? <RefreshCw size={13} className="animate-spin" /> : <Play size={13} />}
        Translate selected
      </button>
      <button
        onClick={onApply}
        disabled={isBusy}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-bg-card border border-border-default text-text-main hover:bg-bg-card2 disabled:opacity-50 transition-colors"
      >
        Apply selected
      </button>
      <button
        onClick={onClear}
        className="text-text-muted hover:text-text-main transition-colors p-1"
        title="Clear selection"
      >
        <X size={15} />
      </button>
    </div>
  )
}

const PER_PAGE = 100

function ModsPage() {
  const navigate = useNavigate({ from: '/mods/' })
  const { status, q, sort_by, page } = Route.useSearch()
  const queryClient = useQueryClient()
  const machines = useMachines()

  const [inputValue, setInputValue] = useState(q)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [batchBusy, setBatchBusy] = useState(false)
  const [showSortMenu, setShowSortMenu] = useState(false)

  useEffect(() => { setInputValue(q) }, [q])
  useEffect(() => {
    const timer = setTimeout(() => {
      navigate({ search: (prev) => ({ ...prev, q: inputValue, page: 0 }) })
    }, 300)
    return () => clearTimeout(timer)
  }, [inputValue]) // eslint-disable-line react-hooks/exhaustive-deps

  // Reset page & selection when filters change
  useEffect(() => {
    navigate({ search: (prev) => ({ ...prev, page: 0 }) })
    setSelected(new Set())
  }, [q, status, sort_by]) // eslint-disable-line react-hooks/exhaustive-deps

  const { data: allMods, isLoading, isFetching, refetch } = useQuery({
    queryKey: QK.mods(),
    queryFn: () => modsApi.list(),
    staleTime: 60_000,
  })

  const sorted = useMemo(() => {
    if (!allMods) return []
    const needle = q.toLowerCase()
    const filtered = allMods.filter((m) => {
      if (status === 'needs_review') {
        if (m.needs_review_strings <= 0) return false
      } else if (status === 'validation') {
        if (!m.has_validation_issues) return false
      } else if (status !== 'all' && m.status !== status) {
        return false
      }
      if (needle && !m.folder_name.toLowerCase().includes(needle)) return false
      return true
    })
    return sortMods(filtered, sort_by)
  }, [allMods, q, status, sort_by])

  const totalPages = Math.max(1, Math.ceil(sorted.length / PER_PAGE))
  const safePage   = Math.min(page, totalPages - 1)
  const visible    = sorted.slice(safePage * PER_PAGE, (safePage + 1) * PER_PAGE)

  function toggleSelect(name: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  function toggleSelectAll() {
    const visibleNames = visible.map((m) => m.folder_name)
    const allSelected = visibleNames.every((n) => selected.has(n))
    if (allSelected) {
      setSelected((prev) => {
        const next = new Set(prev)
        visibleNames.forEach((n) => next.delete(n))
        return next
      })
    } else {
      setSelected((prev) => {
        const next = new Set(prev)
        visibleNames.forEach((n) => next.add(n))
        return next
      })
    }
  }

  async function handleBatchTranslate() {
    const mods = Array.from(selected)
    if (!mods.length) return
    setBatchBusy(true)
    try {
      await jobsApi.create({ type: 'translate_mod', mods, options: { machines } })
      queryClient.invalidateQueries({ queryKey: QK.jobs() })
      setSelected(new Set())
      navigate({ to: '/jobs' })
    } finally {
      setBatchBusy(false)
    }
  }

  async function handleBatchApply() {
    const mods = Array.from(selected)
    if (!mods.length) return
    setBatchBusy(true)
    try {
      await jobsApi.create({ type: 'apply_mod', mods })
      queryClient.invalidateQueries({ queryKey: QK.jobs() })
      setSelected(new Set())
      navigate({ to: '/jobs' })
    } finally {
      setBatchBusy(false)
    }
  }

  const selectedArr = Array.from(selected)
  const visibleNames = visible.map((m) => m.folder_name)
  const allVisibleSelected = visibleNames.length > 0 && visibleNames.every((n) => selected.has(n))
  const someVisibleSelected = visibleNames.some((n) => selected.has(n))

  const sortLabel = SORT_OPTIONS.find((o) => o.value === sort_by)?.label ?? 'Sort'

  return (
    <div className="space-y-5">
      {/* Page header */}
      <div className="flex items-center gap-4">
        <div>
          <h1 className="text-2xl font-bold text-text-main">Mods</h1>
          {allMods && (
            <p className="text-sm text-text-muted mt-0.5">
              {sorted.length !== allMods.length
                ? `${sorted.length} of ${allMods.length} mods`
                : `${allMods.length} mod${allMods.length !== 1 ? 's' : ''}`}
              {totalPages > 1 && ` · page ${safePage + 1}/${totalPages}`}
            </p>
          )}
        </div>
        <div className="flex-1" />
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="flex items-center gap-2 text-sm px-3 py-2 rounded-lg bg-bg-card border border-border-subtle text-text-muted hover:text-text-main hover:bg-bg-card2 transition-colors"
          title="Refresh mods list"
        >
          <RefreshCw size={14} className={cn(isFetching && 'animate-spin')} />
          Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Status tabs */}
        <div className="flex items-center bg-bg-card border border-border-subtle rounded-lg p-1 gap-0.5">
          {STATUS_TABS.map((tab) => (
            <button
              key={tab.value}
              onClick={() => navigate({ search: (prev) => ({ ...prev, status: tab.value, page: 0 }) })}
              className={cn(
                'px-3 py-1 text-sm rounded-md font-medium transition-colors',
                status === tab.value
                  ? 'bg-accent text-bg-base'
                  : 'text-text-muted hover:text-text-main',
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Search */}
        <div className="relative flex-1 min-w-48 max-w-sm">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
          <input
            id="mods-search"
            type="text"
            placeholder="Search mods…"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            className="w-full pl-8 pr-3 py-2 bg-bg-card border border-border-subtle rounded-lg text-sm text-text-main placeholder-text-muted focus:outline-none focus:border-accent/60 transition-colors"
          />
          {inputValue && (
            <button
              onClick={() => setInputValue('')}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-main"
              aria-label="Clear search"
            >
              <X size={14} />
            </button>
          )}
        </div>

        {/* Sort dropdown */}
        <div className="relative">
          <button
            onClick={() => setShowSortMenu((v) => !v)}
            className="flex items-center gap-1.5 text-sm px-3 py-2 rounded-lg bg-bg-card border border-border-subtle text-text-muted hover:text-text-main hover:bg-bg-card2 transition-colors"
          >
            <ArrowUpDown size={13} />
            {sortLabel}
            <ChevronDown size={13} />
          </button>
          {showSortMenu && (
            <>
              <div
                className="fixed inset-0 z-40"
                onClick={() => setShowSortMenu(false)}
              />
              <div className="absolute right-0 mt-1 w-44 bg-bg-card border border-border-default rounded-lg shadow-lg z-50 py-1 overflow-hidden">
                {SORT_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => {
                      navigate({ search: (prev) => ({ ...prev, sort_by: opt.value, page: 0 }) })
                      setShowSortMenu(false)
                    }}
                    className={cn(
                      'w-full text-left px-3 py-2 text-sm transition-colors',
                      sort_by === opt.value
                        ? 'text-accent bg-accent/10'
                        : 'text-text-muted hover:text-text-main hover:bg-bg-card2',
                    )}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>

        {/* Select all toggle */}
        {!isLoading && visible.length > 0 && (
          <button
            onClick={toggleSelectAll}
            className="flex items-center gap-1.5 text-sm px-3 py-2 rounded-lg bg-bg-card border border-border-subtle text-text-muted hover:text-text-main hover:bg-bg-card2 transition-colors"
            title={allVisibleSelected ? 'Deselect all visible' : 'Select all visible'}
          >
            {allVisibleSelected
              ? <CheckSquare size={13} className="text-accent" />
              : someVisibleSelected
                ? <CheckSquare size={13} className="text-text-muted/60" />
                : <Square size={13} />}
            Select
          </button>
        )}
      </div>

      {/* Batch toolbar */}
      {selectedArr.length > 0 && (
        <BatchToolbar
          selected={selectedArr}
          onClear={() => setSelected(new Set())}
          onTranslate={handleBatchTranslate}
          onApply={handleBatchApply}
          isBusy={batchBusy}
        />
      )}

      {/* Grid */}
      {isLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : sorted.length === 0 ? (
        <div className="card p-12 text-center text-text-muted">
          <Search size={32} className="mx-auto mb-3 opacity-30" />
          <p className="text-base">No mods found</p>
          {(q || status !== 'all') && (
            <button
              onClick={() => navigate({ search: () => ({ status: 'all', q: '', sort_by: 'status', page: 0 }) })}
              className="mt-3 text-sm text-accent hover:underline"
            >
              Clear filters
            </button>
          )}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {visible.map((mod) => (
              <ModCard
                key={mod.folder_name}
                mod={mod}
                selected={selected.has(mod.folder_name)}
                onToggleSelect={toggleSelect}
              />
            ))}
          </div>

          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-2 pt-2">
              <button
                onClick={() => navigate({ search: (prev) => ({ ...prev, page: Math.max(0, safePage - 1) }) })}
                disabled={safePage === 0}
                className="px-3 py-1.5 rounded text-sm bg-bg-card border border-border-subtle text-text-muted hover:text-text-main disabled:opacity-40 transition-colors"
              >
                ← Prev
              </button>
              <span className="text-sm text-text-muted">
                {safePage + 1} / {totalPages}
              </span>
              <button
                onClick={() => navigate({ search: (prev) => ({ ...prev, page: Math.min(totalPages - 1, safePage + 1) }) })}
                disabled={safePage === totalPages - 1}
                className="px-3 py-1.5 rounded text-sm bg-bg-card border border-border-subtle text-text-muted hover:text-text-main disabled:opacity-40 transition-colors"
              >
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
