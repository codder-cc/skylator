import { useState, useEffect } from 'react'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, Save, RefreshCw, BookOpen, AlertTriangle } from 'lucide-react'
import { apiGet, apiPost } from '@/api/client'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/mods/$modName/context')({
  component: ModContextPage,
})

interface ContextData {
  context: string
  auto_context?: string
}

function ModContextPage() {
  const { modName } = Route.useParams()
  const decodedName = decodeURIComponent(modName)
  const queryClient = useQueryClient()

  const { data, isLoading, isError } = useQuery<ContextData>({
    queryKey: ['modContext', decodedName],
    queryFn: () => apiGet(`/api/mods/${encodeURIComponent(decodedName)}/context`),
  })

  const [draft, setDraft] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (data?.context !== undefined) {
      setDraft(data.context)
    }
  }, [data?.context])

  const saveMutation = useMutation({
    mutationFn: (context: string) =>
      apiPost(`/api/mods/${encodeURIComponent(decodedName)}/context`, { context }),
    onSuccess: () => {
      setSaved(true)
      void queryClient.invalidateQueries({ queryKey: ['modContext', decodedName] })
      setTimeout(() => setSaved(false), 2000)
    },
  })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-40 text-text-muted">
        <RefreshCw className="animate-spin mr-2" size={16} />
        Loading context...
      </div>
    )
  }

  return (
    <div className="space-y-5 max-w-4xl">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Link
          to="/mods/$modName"
          params={{ modName }}
          className="flex items-center gap-1 text-sm text-text-muted hover:text-text-main transition-colors"
        >
          <ChevronLeft size={16} />
          {decodedName}
        </Link>
        <span className="text-border-subtle">/</span>
        <span className="text-text-main font-semibold">Context</span>
      </div>

      {isError && (
        <div className="card p-4 border border-danger/30 bg-danger/5 flex items-center gap-2 text-sm text-danger">
          <AlertTriangle size={16} />
          Failed to load context
        </div>
      )}

      {/* Auto-generated context preview */}
      {data?.auto_context && (
        <div className="card p-4 space-y-2">
          <div className="flex items-center gap-2 text-sm font-semibold text-text-muted">
            <BookOpen size={14} />
            Auto-Generated Context (read-only)
          </div>
          <pre className="text-xs text-text-muted font-mono whitespace-pre-wrap bg-bg-base p-3 rounded-md max-h-40 overflow-auto">
            {data.auto_context}
          </pre>
        </div>
      )}

      {/* Editable custom context */}
      <div className="card p-5 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-text-main">Custom Context</h2>
            <p className="text-xs text-text-muted mt-0.5">
              Instructions passed to the AI before translating this mod. Leave empty to use auto-generated context only.
            </p>
          </div>
          <button
            onClick={() => saveMutation.mutate(draft)}
            disabled={saveMutation.isPending}
            className={cn(
              'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors',
              saved
                ? 'bg-success/20 text-success border border-success/30'
                : 'bg-accent text-white hover:bg-accent/80',
            )}
          >
            {saveMutation.isPending ? (
              <RefreshCw size={14} className="animate-spin" />
            ) : (
              <Save size={14} />
            )}
            {saved ? 'Saved!' : 'Save'}
          </button>
        </div>

        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={16}
          placeholder="e.g. This is a fantasy RPG mod adding new quests to Skyrim. Use formal archaic English. Keep character names untranslated."
          className={cn(
            'w-full bg-bg-base border border-border-subtle rounded-lg p-3',
            'text-sm text-text-main font-mono resize-y',
            'focus:outline-none focus:ring-1 focus:ring-accent/50',
            'placeholder:text-text-muted/40',
          )}
        />

        {saveMutation.isError && (
          <p className="text-xs text-danger">
            Save failed: {String((saveMutation.error as Error)?.message ?? 'Unknown error')}
          </p>
        )}
      </div>
    </div>
  )
}
