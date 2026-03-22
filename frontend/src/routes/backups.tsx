import { createFileRoute } from '@tanstack/react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { backupsApi } from '@/api/backups'
import { QK } from '@/lib/queryKeys'
import { timeAgo, humanSize, cn } from '@/lib/utils'
import type { BackupEntry } from '@/types'
import {
  HardDrive,
  Plus,
  RotateCcw,
  Trash2,
  ChevronDown,
  ChevronRight,
  Loader2,
  AlertCircle,
  CheckCircle,
  X,
} from 'lucide-react'

// ── Create Backup Dialog ──────────────────────────────────────────────────────
interface CreateDialogProps {
  onClose: () => void
  onCreated: () => void
}

function CreateBackupDialog({ onClose, onCreated }: CreateDialogProps) {
  const [modName, setModName] = useState('')
  const [label, setLabel] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [errorMsg, setErrorMsg] = useState('')

  const createMut = useMutation({
    mutationFn: () => backupsApi.create(modName.trim() || 'all', label.trim() || undefined),
    onSuccess: () => {
      setStatus('success')
      onCreated()
      setTimeout(onClose, 1200)
    },
    onError: (e: Error) => {
      setStatus('error')
      setErrorMsg(e.message)
    },
  })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-bg-card border border-border-subtle rounded-lg w-full max-w-sm shadow-2xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border-subtle">
          <h2 className="font-semibold text-text-main">Create Backup</h2>
          <button onClick={onClose} className="text-text-muted hover:text-text-main">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          <div>
            <label className="block text-xs text-text-muted mb-1">Mod Name</label>
            <input
              value={modName}
              onChange={(e) => setModName(e.target.value)}
              placeholder='Leave blank for "all"'
              className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main"
            />
          </div>
          <div>
            <label className="block text-xs text-text-muted mb-1">Label (optional)</label>
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. before-patch"
              className="w-full bg-bg-card2 border border-border-subtle rounded px-3 py-2 text-sm text-text-main"
            />
          </div>

          {status === 'loading' && (
            <div className="flex items-center gap-2 text-sm text-accent">
              <Loader2 className="w-4 h-4 animate-spin" />
              Creating backup…
            </div>
          )}
          {status === 'success' && (
            <div className="flex items-center gap-2 text-sm text-success">
              <CheckCircle className="w-4 h-4" />
              Backup created successfully.
            </div>
          )}
          {status === 'error' && (
            <div className="flex items-center gap-2 text-sm text-danger">
              <AlertCircle className="w-4 h-4" />
              {errorMsg}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 px-5 py-4 border-t border-border-subtle">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded text-sm text-text-muted bg-bg-card2 border border-border-subtle hover:text-text-main"
          >
            Cancel
          </button>
          <button
            onClick={() => { setStatus('loading'); createMut.mutate() }}
            disabled={createMut.isPending || status === 'success'}
            className="px-4 py-2 rounded text-sm font-medium bg-accent text-bg-base hover:opacity-90 disabled:opacity-50"
          >
            Create
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Backup Row ────────────────────────────────────────────────────────────────
interface BackupRowProps {
  entry: BackupEntry
  onRefresh: () => void
}

function BackupRow({ entry, onRefresh }: BackupRowProps) {
  const [restoreMsg, setRestoreMsg] = useState('')

  const restoreMut = useMutation({
    mutationFn: () => backupsApi.restore(entry.id),
    onSuccess: () => {
      setRestoreMsg('Restored successfully.')
      setTimeout(() => setRestoreMsg(''), 4000)
    },
    onError: (e: Error) => setRestoreMsg(`Error: ${e.message}`),
  })

  const deleteMut = useMutation({
    mutationFn: () => backupsApi.delete(entry.id),
    onSuccess: onRefresh,
  })

  const handleRestore = () => {
    if (!window.confirm(`Restore backup "${entry.label || entry.id}"? This will overwrite current translation files.`)) return
    restoreMut.mutate()
  }

  const handleDelete = () => {
    if (!window.confirm(`Delete backup "${entry.label || entry.id}"? This cannot be undone.`)) return
    deleteMut.mutate()
  }

  return (
    <>
      <tr className="border-t border-border-subtle hover:bg-bg-card2/40 transition-colors">
        <td className="px-4 py-3 text-sm text-text-main">{entry.mod_name}</td>
        <td className="px-4 py-3 text-sm text-text-muted font-mono">{entry.label || '—'}</td>
        <td className="px-4 py-3 text-xs text-text-muted whitespace-nowrap">{timeAgo(entry.created_at)}</td>
        <td className="px-4 py-3 text-xs text-text-muted">{humanSize(entry.size_bytes)}</td>
        <td className="px-4 py-3">
          <span className="px-1.5 py-0.5 rounded text-[10px] font-mono uppercase bg-bg-card2 text-text-muted border border-border-subtle">
            {entry.type}
          </span>
        </td>
        <td className="px-4 py-3">
          <div className="flex gap-2">
            <button
              onClick={handleRestore}
              disabled={restoreMut.isPending || deleteMut.isPending}
              className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 disabled:opacity-50 transition-colors"
            >
              {restoreMut.isPending
                ? <Loader2 className="w-3 h-3 animate-spin" />
                : <RotateCcw className="w-3 h-3" />}
              Restore
            </button>
            <button
              onClick={handleDelete}
              disabled={deleteMut.isPending || restoreMut.isPending}
              className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-danger/20 text-danger border border-danger/30 hover:bg-danger/30 disabled:opacity-50 transition-colors"
            >
              {deleteMut.isPending
                ? <Loader2 className="w-3 h-3 animate-spin" />
                : <Trash2 className="w-3 h-3" />}
              Delete
            </button>
          </div>
        </td>
      </tr>
      {restoreMsg && (
        <tr>
          <td colSpan={6} className="px-4 py-2">
            <span className={cn('text-xs', restoreMsg.startsWith('Error') ? 'text-danger' : 'text-success')}>
              {restoreMsg}
            </span>
          </td>
        </tr>
      )}
    </>
  )
}

// ── Group Section ─────────────────────────────────────────────────────────────
interface GroupProps {
  modName: string
  entries: BackupEntry[]
  onRefresh: () => void
}

function BackupGroup({ modName, entries, onRefresh }: GroupProps) {
  const [open, setOpen] = useState(true)

  return (
    <div className="bg-bg-card border border-border-subtle rounded-lg overflow-hidden mb-3">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-5 py-3 hover:bg-bg-card2 transition-colors text-left"
      >
        {open
          ? <ChevronDown className="w-4 h-4 text-text-muted flex-shrink-0" />
          : <ChevronRight className="w-4 h-4 text-text-muted flex-shrink-0" />}
        <HardDrive className="w-4 h-4 text-accent flex-shrink-0" />
        <span className="font-medium text-text-main text-sm">{modName}</span>
        <span className="ml-auto text-xs text-text-muted">
          {entries.length} backup{entries.length !== 1 ? 's' : ''}
        </span>
      </button>
      {open && (
        <div className="border-t border-border-subtle overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="text-xs text-text-muted">
                {['Mod', 'Label', 'Date', 'Size', 'Type', 'Actions'].map((h) => (
                  <th key={h} className="px-4 py-2 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <BackupRow key={e.id} entry={e} onRefresh={onRefresh} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────
function BackupsPage() {
  const qc = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)

  const backupsQ = useQuery({
    queryKey: QK.backups(),
    queryFn: backupsApi.list,
  })

  const refresh = () => qc.invalidateQueries({ queryKey: QK.backups() })

  const backups: BackupEntry[] = backupsQ.data ?? []

  // Group by mod_name
  const groups = backups.reduce<Record<string, BackupEntry[]>>((acc, b) => {
    const key = b.mod_name || 'unknown'
    if (!acc[key]) acc[key] = []
    acc[key].push(b)
    return acc
  }, {})

  const sortedKeys = Object.keys(groups).sort()

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-text-main">Backups</h1>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-2 px-4 py-2 rounded text-sm font-medium bg-accent text-bg-base hover:opacity-90 transition-opacity"
        >
          <Plus className="w-4 h-4" />
          Create Backup
        </button>
      </div>

      {/* Summary */}
      <div className="flex gap-4 text-sm text-text-muted">
        <span>{backups.length} total backup{backups.length !== 1 ? 's' : ''}</span>
        <span>·</span>
        <span>{sortedKeys.length} mod{sortedKeys.length !== 1 ? 's' : ''}</span>
      </div>

      {/* Content */}
      {backupsQ.isLoading ? (
        <div className="flex items-center justify-center py-16 text-text-muted text-sm">
          <Loader2 className="w-5 h-5 animate-spin mr-2" />
          Loading backups…
        </div>
      ) : backupsQ.isError ? (
        <div className="flex items-center gap-2 px-4 py-4 rounded border border-danger/30 bg-danger/10 text-sm text-danger">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          Failed to load backups.
        </div>
      ) : sortedKeys.length === 0 ? (
        <div className="bg-bg-card border border-border-subtle rounded-lg px-5 py-12 text-center text-text-muted text-sm">
          No backups yet. Click "Create Backup" to make your first one.
        </div>
      ) : (
        sortedKeys.map((modName) => (
          <BackupGroup
            key={modName}
            modName={modName}
            entries={groups[modName]}
            onRefresh={refresh}
          />
        ))
      )}

      {/* Create dialog */}
      {showCreate && (
        <CreateBackupDialog
          onClose={() => setShowCreate(false)}
          onCreated={refresh}
        />
      )}
    </div>
  )
}

export const Route = createFileRoute('/backups')({
  component: BackupsPage,
})
