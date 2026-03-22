import { createFileRoute } from '@tanstack/react-router'

function TerminologyPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-text-main">Terminology</h1>
      <div className="card p-6 text-text-muted">
        Translation terminology glossary — coming soon.
      </div>
    </div>
  )
}

export const Route = createFileRoute('/terminology')({
  component: TerminologyPage,
})
