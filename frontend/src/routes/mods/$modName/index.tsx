import { createFileRoute } from '@tanstack/react-router'

function ModDetailPage() {
  const { modName } = Route.useParams()

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-text-main">
        Mod: <span className="text-accent">{decodeURIComponent(modName)}</span>
      </h1>
      <div className="card p-6 text-text-muted">
        Mod detail page — coming soon.
      </div>
    </div>
  )
}

export const Route = createFileRoute('/mods/$modName/')({
  component: ModDetailPage,
})
