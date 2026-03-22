import { createFileRoute } from '@tanstack/react-router'

function ConfigPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-text-main">Config</h1>
      <div className="card p-6 text-text-muted">
        YAML configuration editor — coming soon.
      </div>
    </div>
  )
}

export const Route = createFileRoute('/config')({
  component: ConfigPage,
})
