import { createFileRoute } from '@tanstack/react-router'

function ToolsPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-text-main">Tools</h1>
      <div className="card p-6 text-text-muted">
        Utility tools and diagnostics — coming soon.
      </div>
    </div>
  )
}

export const Route = createFileRoute('/tools')({
  component: ToolsPage,
})
