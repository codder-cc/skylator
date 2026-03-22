import { createFileRoute } from '@tanstack/react-router'

function BackupsPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-text-main">Backups</h1>
      <div className="card p-6 text-text-muted">
        Translation backup management — coming soon.
      </div>
    </div>
  )
}

export const Route = createFileRoute('/backups')({
  component: BackupsPage,
})
