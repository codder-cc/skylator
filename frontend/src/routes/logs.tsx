import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useLogStream } from '@/hooks/useLogStream'
import { LogViewer } from '@/components/shared/LogViewer'
import { apiGet } from '@/api/client'

const MAX_COMBINED = 2000

function LogsPage() {
  const { lines: streamLines, connected } = useLogStream('/logs/stream')

  const { data: initialData } = useQuery<{ lines: string[] }>({
    queryKey: ['logs', 'tail'],
    queryFn: () => apiGet('/logs/tail?n=300'),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })

  const combined = [...(initialData?.lines ?? []), ...streamLines]
  const allLines = combined.length > MAX_COMBINED
    ? combined.slice(combined.length - MAX_COMBINED)
    : combined

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold text-text-main">Logs</h1>
        <span
          className={`inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full font-medium ${
            connected
              ? 'bg-success/20 text-success'
              : 'bg-text-muted/20 text-text-muted'
          }`}
        >
          <span
            className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-success animate-pulse' : 'bg-text-muted'}`}
          />
          {connected ? 'Live' : 'Disconnected'}
        </span>
      </div>

      <div className="card">
        <LogViewer lines={allLines} maxHeight="calc(100vh - 12rem)" autoScroll />
      </div>
    </div>
  )
}

export const Route = createFileRoute('/logs')({
  component: LogsPage,
})
