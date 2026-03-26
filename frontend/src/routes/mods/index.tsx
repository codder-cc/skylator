import { useState, useEffect, useMemo } from 'react'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Search, RefreshCw, Play, FileText, Archive } from 'lucide-react'
import { modsApi } from '@/api/mods'
import { jobsApi } from '@/api/jobs'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/utils'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { ProgressBar } from '@/components/shared/ProgressBar'
import { useMachines } from '@/hooks/useMachines'
import type { ModInfo } from '@/types'

export const Route = createFileRoute('/mods/')({
  validateSearch: (search: Record<string, unknown>) => ({
    status: (search.status as string) || 'all',
    q: (search.q as string) || '',
  }),
  component: ModsPage,
})

const STATUS_TABS = [
  { value: 'all',     label: 'All' },
  { value: 'pending', label: 'Pending' },
  { value: 'partial', label: 'Partial' },
  { value: 'done',    label: 'Done' },
  { value: 'error',   label: 'Error' },
] as const

const STATUS_SORT_ORDER: Record<string, number> = {
  error:      0,
  pending:    1,
  partial:    2,
  unknown:    3,
  no_strings: 4,
  done:       5,
}

function sortMods(mods: ModInfo[]): ModInfo[] {
  return [...mods].sort((a, b) => {
    const ao = STATUS_SORT_ORDER[a.status] ?? 2
    const bo = STATUS_SORT_ORDER[b.status] ?? 2
    if (ao !== bo) return ao - bo
    return a.folder_name.localeCompare(b.folder_name)
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

function ModCard({ mod }: { mod: ModInfo }) {
  const navigate = useNavigate()
  const machines = useMachines()
  const queryClient = useQueryClient()

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
    <div className="card p-5 flex flex-col gap-3 hover:bg-bg-card2/50 transition-colors">
      {/* Header */}
      <div className="flex items-start justify-between gap-2 min-w-0">
        <Link
          to="/mods/$modName"
          params={{ modName: encodeURIComponent(mod.folder_name) }}
          className="text-sm font-semibold text-text-main hover:text-accent transition-colors truncate leading-snug"
          title={mod.folder_name}
        >
          {mod.folder_name}
        </Link>
        <StatusBadge status={mod.status} className="shrink-0" />
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

const PER_PAGE = 100

function ModsPage() {
  const navigate = useNavigate({ from: '/mods/' })
  const { status, q } = Route.useSearch()
  const queryClient = useQueryClient()

  // Local debounced search input
  const [inputValue, setInputValue] = useState(q)
  const [page, setPage] = useState(0)

  useEffect(() => {
    setInputValue(q)
  }, [q])
  useEffect(() => {
    const timer = setTimeout(() => {
      navigate({ search: (prev) => ({ ...prev, q: inputValue, page: undefined as never }) })
    }, 300)
    return () => clearTimeout(timer)
  }, [inputValue]) // eslint-disable-line react-hooks/exhaustive-deps

  // Reset page when filters change
  useEffect(() => { setPage(0) }, [q, status])

  // Load all mods once — filter/search client-side (backend ignores q/status params anyway)
  const { data: allMods, isLoading, isFetching, refetch } = useQuery({
    queryKey: QK.mods(),
    queryFn: () => modsApi.list(),
    staleTime: 60_000,
  })

  const sorted = useMemo(() => {
    if (!allMods) return []
    const needle = q.toLowerCase()
    const filtered = allMods.filter((m) => {
      if (status !== 'all' && m.status !== status) return false
      if (needle && !m.folder_name.toLowerCase().includes(needle)) return false
      return true
    })
    return sortMods(filtered)
  }, [allMods, q, status])

  const totalPages = Math.max(1, Math.ceil(sorted.length / PER_PAGE))
  const safePage   = Math.min(page, totalPages - 1)
  const visible    = sorted.slice(safePage * PER_PAGE, (safePage + 1) * PER_PAGE)

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
              onClick={() => navigate({ search: (prev) => ({ ...prev, status: tab.value }) })}
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
            >
              ×
            </button>
          )}
        </div>
      </div>

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
              onClick={() => navigate({ search: () => ({ status: 'all', q: '' }) })}
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
              <ModCard key={mod.folder_name} mod={mod} />
            ))}
          </div>

          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-2 pt-2">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={safePage === 0}
                className="px-3 py-1.5 rounded text-sm bg-bg-card border border-border-subtle text-text-muted hover:text-text-main disabled:opacity-40 transition-colors"
              >
                ← Prev
              </button>
              <span className="text-sm text-text-muted">
                {safePage + 1} / {totalPages}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
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
