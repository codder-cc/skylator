import { createFileRoute } from '@tanstack/react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import { termsApi } from '@/api/terminology'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/utils'
import {
  Search,
  Plus,
  Trash2,
  Save,
  CheckCircle,
  AlertCircle,
  Loader2,
  Info,
} from 'lucide-react'

// ── Editable Cell ─────────────────────────────────────────────────────────────
interface EditableCellProps {
  value: string
  onCommit: (v: string) => void
  placeholder?: string
  className?: string
}

function EditableCell({ value, onCommit, placeholder, className }: EditableCellProps) {
  const [editing, setEditing] = useState(false)
  const [local, setLocal] = useState(value)

  useEffect(() => { setLocal(value) }, [value])

  const commit = () => {
    setEditing(false)
    if (local !== value) onCommit(local)
  }

  if (!editing) {
    return (
      <span
        onClick={() => setEditing(true)}
        className={cn(
          'cursor-text block w-full px-2 py-1 rounded hover:bg-bg-card2 transition-colors min-h-[1.75rem]',
          !value && 'text-text-muted italic',
          className,
        )}
      >
        {value || placeholder || '—'}
      </span>
    )
  }

  return (
    <input
      autoFocus
      value={local}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { setLocal(value); setEditing(false) } }}
      className={cn(
        'w-full px-2 py-1 rounded bg-bg-card2 border border-accent/50 outline-none text-sm text-text-main font-mono',
        className,
      )}
    />
  )
}

// ── Add Term Row ──────────────────────────────────────────────────────────────
interface AddTermRowProps {
  onAdd: (en: string, ru: string) => void
  onCancel: () => void
}

function AddTermRow({ onAdd, onCancel }: AddTermRowProps) {
  const [en, setEn] = useState('')
  const [ru, setRu] = useState('')

  const submit = () => {
    const trimEn = en.trim()
    const trimRu = ru.trim()
    if (!trimEn || !trimRu) return
    onAdd(trimEn, trimRu)
  }

  return (
    <tr className="border-t border-border-subtle bg-accent/5">
      <td className="px-4 py-2">
        <input
          autoFocus
          value={en}
          onChange={(e) => setEn(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') onCancel() }}
          placeholder="English term"
          className="w-full px-2 py-1 rounded bg-bg-card2 border border-accent/50 outline-none text-sm text-text-main font-mono"
        />
      </td>
      <td className="px-4 py-2">
        <input
          value={ru}
          onChange={(e) => setRu(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') onCancel() }}
          placeholder="Russian translation"
          className="w-full px-2 py-1 rounded bg-bg-card2 border border-accent/50 outline-none text-sm text-text-main font-mono"
        />
      </td>
      <td className="px-4 py-2">
        <div className="flex gap-2">
          <button
            onClick={submit}
            disabled={!en.trim() || !ru.trim()}
            className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-success/20 text-success border border-success/30 hover:bg-success/30 disabled:opacity-40 transition-colors"
          >
            <CheckCircle className="w-3 h-3" />
            Add
          </button>
          <button
            onClick={onCancel}
            className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-bg-card2 text-text-muted border border-border-subtle hover:text-text-main transition-colors"
          >
            Cancel
          </button>
        </div>
      </td>
    </tr>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────
function TerminologyPage() {
  const qc = useQueryClient()
  const [terms, setTerms] = useState<Record<string, string>>({})
  const [filter, setFilter] = useState('')
  const [showAdd, setShowAdd] = useState(false)
  const [banner, setBanner] = useState<'success' | 'error' | null>(null)
  const [bannerMsg, setBannerMsg] = useState('')

  const termsQ = useQuery({
    queryKey: QK.terms(),
    queryFn: termsApi.get,
  })

  useEffect(() => {
    if (termsQ.data) setTerms(termsQ.data)
  }, [termsQ.data])

  const addMut = useMutation({
    mutationFn: ({ en, ru }: { en: string; ru: string }) => termsApi.add(en, ru),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.terms() }),
  })

  const deleteMut = useMutation({
    mutationFn: (en: string) => termsApi.delete(en),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.terms() }),
  })

  const saveMut = useMutation({
    mutationFn: () => termsApi.save(terms),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.terms() })
      setBanner('success')
      setBannerMsg('Terminology saved successfully.')
      setTimeout(() => setBanner(null), 4000)
    },
    onError: (e: Error) => {
      setBanner('error')
      setBannerMsg(e.message)
    },
  })

  const handleAdd = (en: string, ru: string) => {
    setTerms((prev) => ({ ...prev, [en]: ru }))
    setShowAdd(false)
    addMut.mutate({ en, ru })
  }

  const handleDelete = (en: string) => {
    setTerms((prev) => {
      const next = { ...prev }
      delete next[en]
      return next
    })
    deleteMut.mutate(en)
  }

  const handleEditEn = (oldEn: string, newEn: string, ru: string) => {
    if (oldEn === newEn) return
    setTerms((prev) => {
      const next = { ...prev }
      delete next[oldEn]
      next[newEn] = ru
      return next
    })
    deleteMut.mutate(oldEn)
    addMut.mutate({ en: newEn, ru })
  }

  const handleEditRu = (en: string, newRu: string) => {
    setTerms((prev) => ({ ...prev, [en]: newRu }))
    addMut.mutate({ en, ru: newRu })
  }

  const filteredEntries = Object.entries(terms).filter(([en, ru]) => {
    const q = filter.toLowerCase()
    return en.toLowerCase().includes(q) || ru.toLowerCase().includes(q)
  })

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-text-main">Terminology</h1>
        <div className="flex gap-2">
          <button
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-2 px-3 py-2 rounded text-sm bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 transition-colors"
          >
            <Plus className="w-4 h-4" />
            Add Term
          </button>
          <button
            onClick={() => saveMut.mutate()}
            disabled={saveMut.isPending}
            className="flex items-center gap-2 px-4 py-2 rounded text-sm font-medium bg-accent text-bg-base hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {saveMut.isPending
              ? <Loader2 className="w-4 h-4 animate-spin" />
              : <Save className="w-4 h-4" />}
            Save All
          </button>
        </div>
      </div>

      {/* Info banner */}
      <div className="flex items-center gap-2 px-4 py-3 rounded border border-border-subtle bg-bg-card text-xs text-text-muted">
        <Info className="w-4 h-4 text-accent flex-shrink-0" />
        Terms are injected into AI prompts to improve translation consistency.
        Click any cell to edit inline. Changes are saved locally; press <strong className="text-text-main mx-1">Save All</strong> to persist.
      </div>

      {/* Save banner */}
      {banner && (
        <div
          className={cn(
            'flex items-center gap-2 px-4 py-3 rounded border text-sm',
            banner === 'success'
              ? 'bg-success/10 border-success/30 text-success'
              : 'bg-danger/10 border-danger/30 text-danger',
          )}
        >
          {banner === 'success'
            ? <CheckCircle className="w-4 h-4 flex-shrink-0" />
            : <AlertCircle className="w-4 h-4 flex-shrink-0" />}
          {bannerMsg}
        </div>
      )}

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted pointer-events-none" />
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter terms…"
          className="w-full pl-9 pr-4 py-2 rounded bg-bg-card border border-border-subtle text-sm text-text-main outline-none focus:border-accent/50 transition-colors"
        />
      </div>

      {/* Table */}
      <div className="bg-bg-card border border-border-subtle rounded-lg overflow-hidden">
        <div className="flex items-center justify-between px-5 py-3 border-b border-border-subtle">
          <span className="text-sm font-medium text-text-muted">
            {filteredEntries.length} term{filteredEntries.length !== 1 ? 's' : ''}
            {filter && ` matching "${filter}"`}
          </span>
        </div>

        {termsQ.isLoading ? (
          <div className="flex items-center justify-center py-12 text-text-muted text-sm">
            <Loader2 className="w-5 h-5 animate-spin mr-2" />
            Loading…
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="text-xs text-text-muted border-b border-border-subtle">
                  <th className="px-4 py-3 font-medium w-1/2">English</th>
                  <th className="px-4 py-3 font-medium w-1/2">Russian</th>
                  <th className="px-4 py-3 font-medium w-24">Actions</th>
                </tr>
              </thead>
              <tbody>
                {showAdd && (
                  <AddTermRow onAdd={handleAdd} onCancel={() => setShowAdd(false)} />
                )}
                {filteredEntries.length === 0 && !showAdd ? (
                  <tr>
                    <td colSpan={3} className="px-4 py-10 text-center text-text-muted text-sm">
                      {filter ? 'No terms match your filter.' : 'No terms defined yet. Click "Add Term" to start.'}
                    </td>
                  </tr>
                ) : (
                  filteredEntries.map(([en, ru]) => (
                    <tr key={en} className="border-t border-border-subtle hover:bg-bg-card2/40 transition-colors">
                      <td className="px-4 py-2">
                        <EditableCell
                          value={en}
                          onCommit={(newEn) => handleEditEn(en, newEn, ru)}
                          className="font-mono text-sm"
                        />
                      </td>
                      <td className="px-4 py-2">
                        <EditableCell
                          value={ru}
                          onCommit={(newRu) => handleEditRu(en, newRu)}
                          className="font-mono text-sm"
                        />
                      </td>
                      <td className="px-4 py-2">
                        <button
                          onClick={() => handleDelete(en)}
                          disabled={deleteMut.isPending}
                          className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-danger/20 text-danger border border-danger/30 hover:bg-danger/30 disabled:opacity-50 transition-colors"
                        >
                          <Trash2 className="w-3 h-3" />
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

export const Route = createFileRoute('/terminology')({
  component: TerminologyPage,
})
