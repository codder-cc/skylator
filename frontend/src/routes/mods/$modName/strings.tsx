import { createFileRoute } from '@tanstack/react-router'

function ModStringsPage() {
  const { modName } = Route.useParams()

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-text-main">
        Strings — <span className="text-accent">{decodeURIComponent(modName)}</span>
      </h1>
      <div className="card p-6 text-text-muted">
        Strings editor page — coming soon.
      </div>
    </div>
  )
}

export const Route = createFileRoute('/mods/$modName/strings')({
  component: ModStringsPage,
})
