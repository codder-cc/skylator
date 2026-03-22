import { createFileRoute } from '@tanstack/react-router'

function ModsPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-text-main">Mods</h1>
      <div className="card p-6 text-text-muted">
        Mods list page — coming soon.
      </div>
    </div>
  )
}

export const Route = createFileRoute('/mods/')({
  component: ModsPage,
})
