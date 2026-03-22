import { createFileRoute } from '@tanstack/react-router'

function ServersPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-text-main">Servers</h1>
      <div className="card p-6 text-text-muted">
        Worker server management — coming soon.
      </div>
    </div>
  )
}

export const Route = createFileRoute('/servers')({
  component: ServersPage,
})
