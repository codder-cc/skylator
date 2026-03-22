import { createFileRoute, Link } from '@tanstack/react-router'

export const Route = createFileRoute('/$')({
  component: NotFound,
})

function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center h-64 gap-4 text-center">
      <p className="text-6xl font-bold text-text-muted/20">404</p>
      <p className="text-text-muted">Page not found</p>
      <Link to="/" className="text-accent hover:underline text-sm">
        Back to Dashboard
      </Link>
    </div>
  )
}
