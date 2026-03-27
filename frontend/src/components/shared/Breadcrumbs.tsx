import { Link } from '@tanstack/react-router'
import { ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface BreadcrumbItem {
  label: string
  to?: string
  params?: Record<string, string>
}

interface BreadcrumbsProps {
  items: BreadcrumbItem[]
  className?: string
}

export function Breadcrumbs({ items, className }: BreadcrumbsProps) {
  return (
    <nav aria-label="Breadcrumb" className={cn('flex items-center gap-1 text-sm mb-4', className)}>
      {items.map((item, i) => {
        const isLast = i === items.length - 1
        return (
          <span key={i} className="flex items-center gap-1">
            {i > 0 && <ChevronRight className="w-3.5 h-3.5 text-text-muted/40 flex-shrink-0" />}
            {item.to && !isLast ? (
              <Link
                to={item.to}
                params={item.params as never}
                className="text-text-muted hover:text-text-main transition-colors no-underline"
              >
                {item.label}
              </Link>
            ) : (
              <span className={isLast ? 'text-text-main font-medium' : 'text-text-muted'}>
                {item.label}
              </span>
            )}
          </span>
        )
      })}
    </nav>
  )
}
