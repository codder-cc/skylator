import { createFileRoute } from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Check, ClipboardCheck, Layers, Pencil, RotateCcw, SkipForward, X,
} from 'lucide-react'
import { reviewApi, type ReviewIssue, type ReviewRow } from '@/api/review'
import { cn } from '@/lib/utils'

// ── Review queue ─────────────────────────────────────────────────────────────
//
// Built for walking a backlog, not for browsing one. The audit put five thousand
// strings in here in a single pass, so the cost that matters is seconds per decision:
// one string at a time, the reason it was flagged stated rather than inferred, the
// disputed words highlighted on both sides, and the three outcomes on the home row.

function qualityClass(q: number | null) {
  if (q == null) return 'text-text-muted'
  if (q >= 80) return 'text-success'
  if (q >= 50) return 'text-warning'
  return 'text-danger'
}

const ISSUE_STYLE: Record<ReviewIssue['kind'], { label: string; cls: string }> = {
  glossary:     { label: 'glossary',     cls: 'bg-warning/15 text-warning border-warning/30' },
  tokens:       { label: 'game token',   cls: 'bg-danger/15 text-danger border-danger/30' },
  untranslated: { label: 'untranslated', cls: 'bg-danger/15 text-danger border-danger/30' },
  low_score:    { label: 'low score',    cls: 'bg-text-muted/15 text-text-muted border-border-subtle' },
}

/** Split text so the parts worth looking at can be marked, without touching the rest. */
function highlight(text: string, needles: string[]) {
  if (!text) return [{ text: '', hit: false }]
  const found = needles.filter(Boolean)
  if (found.length === 0) return [{ text, hit: false }]
  const escaped = found.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const re = new RegExp(`(${escaped.join('|')})`, 'gi')
  return text.split(re).map((part) => ({
    text: part,
    hit: found.some((n) => part.toLowerCase() === n.toLowerCase()),
  }))
}

function Marked({ text, needles, tone }: { text: string; needles: string[]; tone: 'src' | 'dst' }) {
  const parts = useMemo(() => highlight(text, needles), [text, needles])
  return (
    <span className="whitespace-pre-wrap break-words">
      {parts.map((p, i) =>
        p.hit ? (
          <mark
            key={i}
            className={cn(
              'rounded px-0.5',
              tone === 'src' ? 'bg-warning/25 text-warning' : 'bg-danger/25 text-danger',
            )}
          >
            {p.text}
          </mark>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </span>
  )
}

function ReviewPage() {
  const qc = useQueryClient()
  const [term, setTerm] = useState<string>('')
  const [cursor, setCursor] = useState(0)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [done, setDone] = useState<{ approved: number; rejected: number; edited: number }>({
    approved: 0, rejected: 0, edited: 0,
  })
  const editRef = useRef<HTMLTextAreaElement>(null)

  const { data, isFetching } = useQuery({
    queryKey: ['review', term],
    queryFn: () => reviewApi.queue({ limit: 300, term: term || undefined }),
  })

  const rows: ReviewRow[] = data?.strings ?? []
  const row: ReviewRow | undefined = rows[cursor]

  const refresh = () => qc.invalidateQueries({ queryKey: ['review'] })
  const advance = useCallback(() => {
    setEditing(false)
    setCursor((c) => (c + 1 < rows.length ? c + 1 : 0))
  }, [rows.length])

  const approve = useMutation({
    mutationFn: (id: number) => reviewApi.approve([id]),
    onSuccess: () => { setDone((d) => ({ ...d, approved: d.approved + 1 })); advance() },
  })
  const reject = useMutation({
    mutationFn: (id: number) => reviewApi.reject([id]),
    onSuccess: () => { setDone((d) => ({ ...d, rejected: d.rejected + 1 })); advance() },
  })
  const edit = useMutation({
    mutationFn: ({ id, text }: { id: number; text: string }) => reviewApi.edit(id, text),
    onSuccess: () => { setDone((d) => ({ ...d, edited: d.edited + 1 })); advance() },
  })

  // Bulk path for a term that has one right answer across hundreds of strings.
  const approveAll = useMutation({
    mutationFn: (ids: number[]) => reviewApi.approve(ids),
    onSuccess: (_r, ids) => {
      setDone((d) => ({ ...d, approved: d.approved + ids.length })); setCursor(0); refresh()
    },
  })
  const rejectAll = useMutation({
    mutationFn: (ids: number[]) => reviewApi.reject(ids),
    onSuccess: (_r, ids) => {
      setDone((d) => ({ ...d, rejected: d.rejected + ids.length })); setCursor(0); refresh()
    },
  })

  const startEdit = useCallback(() => {
    if (!row) return
    setDraft(row.translation)
    setEditing(true)
    setTimeout(() => editRef.current?.focus(), 0)
  }, [row])

  // Home-row keys, because the whole point is speed. Suspended while editing so typing
  // a translation does not approve it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!row) return
      const typing = editing ||
        ['INPUT', 'TEXTAREA'].includes((e.target as HTMLElement)?.tagName)
      if (typing) {
        if (e.key === 'Escape') setEditing(false)
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && draft.trim()) {
          edit.mutate({ id: row.id, text: draft.trim() })
        }
        return
      }
      if (e.key === 'a' || e.key === 'ф') approve.mutate(row.id)
      else if (e.key === 'r' || e.key === 'к') reject.mutate(row.id)
      else if (e.key === 'e' || e.key === 'у') { e.preventDefault(); startEdit() }
      else if (e.key === 's' || e.key === 'ы' || e.key === 'ArrowRight') advance()
      else if (e.key === 'ArrowLeft') setCursor((c) => Math.max(0, c - 1))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [row, editing, draft, approve, reject, edit, advance, startEdit])

  // Words to mark: the glossary term on the source, what should have replaced it on the
  // translation. Nothing else is highlighted — everything marked is something to judge.
  const srcNeedles = (row?.issues ?? []).filter((i) => i.term).map((i) => i.term!) ?? []
  const dstNeedles = (row?.issues ?? []).filter((i) => i.expected).map((i) => i.expected!) ?? []

  const reviewed = done.approved + done.rejected + done.edited

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 flex-wrap">
        <ClipboardCheck className="w-5 h-5 text-accent" />
        <h1 className="text-2xl font-bold text-text-main">Review</h1>
        <span className="text-sm text-text-muted">
          {(data?.total ?? 0).toLocaleString()} awaiting review
        </span>
        {reviewed > 0 && (
          <span className="text-sm text-text-muted">
            · this session: <span className="text-success">{done.approved} kept</span>,{' '}
            <span className="text-danger">{done.rejected} sent back</span>,{' '}
            <span className="text-accent">{done.edited} fixed</span>
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-text-muted">
            {rows.length ? `${cursor + 1} / ${rows.length}` : '—'}
          </span>
          <button onClick={() => refresh()} disabled={isFetching}
            className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-bg-base border border-border-subtle hover:bg-bg-card2 disabled:opacity-50">
            <RotateCcw className="w-3 h-3" />reload
          </button>
        </div>
      </div>

      {/* Group by the term at fault: one term can account for hundreds of strings, and
          they usually share a single answer. */}
      {(data?.terms?.length ?? 0) > 0 && (
        <div className="card p-3">
          <div className="flex items-center gap-2 mb-2">
            <Layers className="w-4 h-4 text-text-muted" />
            <span className="text-xs text-text-muted">
              group by glossary term — settle one wording for all of them at once
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <button
              onClick={() => { setTerm(''); setCursor(0) }}
              className={cn('px-2 py-1 rounded text-xs border',
                term === '' ? 'bg-accent/20 text-accent border-accent/40'
                            : 'bg-bg-base text-text-muted border-border-subtle hover:bg-bg-card2')}
            >
              everything
            </button>
            {data!.terms.map((t) => (
              <button
                key={t.term}
                onClick={() => { setTerm(t.term); setCursor(0) }}
                title={`expected: ${t.expected}`}
                className={cn('px-2 py-1 rounded text-xs border font-mono',
                  term === t.term ? 'bg-accent/20 text-accent border-accent/40'
                                  : 'bg-bg-base text-text-muted border-border-subtle hover:bg-bg-card2')}
              >
                {t.term} <span className="opacity-60">{t.count}</span>
              </button>
            ))}
          </div>
          {term && rows.length > 0 && (
            <div className="flex items-center gap-2 mt-3 pt-3 border-t border-border-subtle">
              <span className="text-xs text-text-muted">
                all {rows.length} shown, for “{term}”:
              </span>
              <button
                onClick={() => approveAll.mutate(rows.map((r) => r.id))}
                disabled={approveAll.isPending}
                className="px-2 py-1 rounded text-xs bg-success/15 text-success border border-success/30 hover:bg-success/25 disabled:opacity-50"
              >
                keep all
              </button>
              <button
                onClick={() => rejectAll.mutate(rows.map((r) => r.id))}
                disabled={rejectAll.isPending}
                className="px-2 py-1 rounded text-xs bg-danger/15 text-danger border border-danger/30 hover:bg-danger/25 disabled:opacity-50"
              >
                send all back
              </button>
            </div>
          )}
        </div>
      )}

      {!row && (
        <div className="card p-10 text-center text-text-muted text-sm">
          {isFetching ? 'Loading…' : 'Nothing to review 🎉'}
        </div>
      )}

      {row && (
        <div className="card p-4 space-y-4">
          <div className="flex items-center gap-2 text-[11px] text-text-muted flex-wrap">
            <span className="font-mono truncate max-w-[22rem]" title={row.mod_name}>
              {row.mod_name}
            </span>
            <span className="font-mono opacity-70">{row.esp_name}</span>
            {row.source && <span className="font-mono opacity-70">via {row.source}</span>}
            <span className={cn('font-mono', qualityClass(row.quality_score))}>
              q{row.quality_score ?? '—'}
            </span>
          </div>

          {/* Why it is here, in words, so the reviewer is not re-deriving it by eye. */}
          <div className="flex flex-wrap gap-1.5">
            {row.issues.length === 0 && (
              <span className="text-xs text-text-muted">flagged, reason not recorded</span>
            )}
            {row.issues.map((i, n) => (
              <span key={n}
                className={cn('px-2 py-0.5 rounded text-[11px] border', ISSUE_STYLE[i.kind]?.cls)}>
                {ISSUE_STYLE[i.kind]?.label ?? i.kind}: {i.detail}
              </span>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <div className="text-[10px] uppercase tracking-wide text-text-muted mb-1">source</div>
              <div className="text-sm text-text-muted leading-relaxed">
                <Marked text={row.original} needles={srcNeedles} tone="src" />
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wide text-text-muted mb-1">
                translation
              </div>
              {editing ? (
                <textarea
                  ref={editRef}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  rows={Math.min(14, Math.max(3, draft.split('\n').length + 1))}
                  className="w-full px-2 py-1.5 rounded text-sm bg-bg-base border border-accent/50 text-text-main font-normal leading-relaxed"
                />
              ) : (
                <div className="text-sm text-text-main leading-relaxed">
                  <Marked text={row.translation} needles={dstNeedles} tone="dst" />
                </div>
              )}
            </div>
          </div>

          {/* What the glossary asked for, spelled out — otherwise the reviewer has to
              remember 204 terms. */}
          {row.issues.some((i) => i.expected) && !editing && (
            <div className="text-xs text-text-muted">
              expected:{' '}
              {row.issues.filter((i) => i.expected).map((i) => (
                <span key={i.term} className="font-mono text-warning mr-2">
                  {i.term} → {i.expected}
                </span>
              ))}
            </div>
          )}

          <div className="flex items-center gap-2 pt-1">
            {editing ? (
              <>
                <button
                  onClick={() => edit.mutate({ id: row.id, text: draft.trim() })}
                  disabled={!draft.trim() || edit.isPending}
                  className="flex items-center gap-1 px-3 py-1.5 rounded text-sm font-medium bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 disabled:opacity-50"
                >
                  <Check className="w-4 h-4" />save & keep
                  <kbd className="ml-1 opacity-60 text-[10px]">ctrl+enter</kbd>
                </button>
                <button onClick={() => setEditing(false)}
                  className="px-3 py-1.5 rounded text-sm bg-bg-base border border-border-subtle text-text-muted hover:bg-bg-card2">
                  cancel <kbd className="ml-1 opacity-60 text-[10px]">esc</kbd>
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={() => approve.mutate(row.id)} disabled={approve.isPending}
                  className="flex items-center gap-1 px-3 py-1.5 rounded text-sm font-medium bg-success/20 text-success border border-success/30 hover:bg-success/30 disabled:opacity-50"
                >
                  <Check className="w-4 h-4" />good
                  <kbd className="ml-1 opacity-60 text-[10px]">a</kbd>
                </button>
                <button
                  onClick={() => reject.mutate(row.id)} disabled={reject.isPending}
                  className="flex items-center gap-1 px-3 py-1.5 rounded text-sm font-medium bg-danger/20 text-danger border border-danger/30 hover:bg-danger/30 disabled:opacity-50"
                  title="clears the translation and puts the string back in the queue to be translated again"
                >
                  <X className="w-4 h-4" />translate again
                  <kbd className="ml-1 opacity-60 text-[10px]">r</kbd>
                </button>
                <button
                  onClick={startEdit}
                  className="flex items-center gap-1 px-3 py-1.5 rounded text-sm font-medium bg-accent/15 text-accent border border-accent/30 hover:bg-accent/25"
                >
                  <Pencil className="w-4 h-4" />fix it
                  <kbd className="ml-1 opacity-60 text-[10px]">e</kbd>
                </button>
                <button
                  onClick={advance}
                  className="flex items-center gap-1 px-3 py-1.5 rounded text-sm text-text-muted bg-bg-base border border-border-subtle hover:bg-bg-card2"
                >
                  <SkipForward className="w-4 h-4" />skip
                  <kbd className="ml-1 opacity-60 text-[10px]">s</kbd>
                </button>
              </>
            )}
          </div>
        </div>
      )}

      {/* What is coming, so the reviewer can see whether the next few are the same
          problem and reach for the bulk action instead. */}
      {rows.length > 1 && (
        <div className="card divide-y divide-border-subtle">
          {rows.slice(cursor + 1, cursor + 6).map((r) => (
            <button
              key={r.id}
              onClick={() => { setCursor(rows.indexOf(r)); setEditing(false) }}
              className="w-full px-4 py-2 text-left hover:bg-bg-card2/40"
            >
              <div className="flex items-center gap-2 text-[10px] text-text-muted">
                <span className="font-mono truncate max-w-[14rem]">{r.mod_name}</span>
                {r.issues.slice(0, 2).map((i, n) => (
                  <span key={n} className="opacity-70">{ISSUE_STYLE[i.kind]?.label ?? i.kind}</span>
                ))}
                <span className={cn('ml-auto font-mono', qualityClass(r.quality_score))}>
                  q{r.quality_score ?? '—'}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-3 text-xs mt-0.5">
                <div className="text-text-muted truncate">{r.original}</div>
                <div className="text-text-main truncate">{r.translation}</div>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export const Route = createFileRoute('/review')({ component: ReviewPage })
