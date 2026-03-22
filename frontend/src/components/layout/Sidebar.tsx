import { Link, useRouterState } from '@tanstack/react-router'
import {
  LayoutDashboard,
  Layers,
  Briefcase,
  Server,
  Settings,
  BookOpen,
  Archive,
  Wrench,
  ScrollText,
  Cpu,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useMachinesStore } from '@/stores/machinesStore'

interface NavItem {
  to: string
  label: string
  icon: React.ReactNode
  exact?: boolean
}

const NAV_ITEMS: NavItem[] = [
  { to: '/',           label: 'Dashboard',   icon: <LayoutDashboard className="w-4 h-4" />, exact: true },
  { to: '/mods',       label: 'Mods',        icon: <Layers className="w-4 h-4" /> },
  { to: '/jobs',       label: 'Jobs',        icon: <Briefcase className="w-4 h-4" /> },
  { to: '/servers',    label: 'Servers',     icon: <Server className="w-4 h-4" /> },
  { to: '/config',     label: 'Config',      icon: <Settings className="w-4 h-4" /> },
  { to: '/terminology',label: 'Terminology', icon: <BookOpen className="w-4 h-4" /> },
  { to: '/backups',    label: 'Backups',     icon: <Archive className="w-4 h-4" /> },
  { to: '/tools',      label: 'Tools',       icon: <Wrench className="w-4 h-4" /> },
  { to: '/logs',       label: 'Logs',        icon: <ScrollText className="w-4 h-4" /> },
]

export function Sidebar() {
  const routerState = useRouterState()
  const pathname = routerState.location.pathname
  const mode = useMachinesStore((s) => s.mode)

  function isActive(item: NavItem): boolean {
    if (item.exact) return pathname === item.to
    return pathname.startsWith(item.to)
  }

  return (
    <aside
      className="flex flex-col bg-bg-sidebar border-r border-border-default"
      style={{ width: 240, minWidth: 240, flexShrink: 0 }}
    >
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-4 py-4 border-b border-border-default">
        <div className="w-8 h-8 rounded-md bg-accent/20 flex items-center justify-center">
          <Cpu className="w-4 h-4 text-accent" />
        </div>
        <span className="text-base font-bold text-accent tracking-widest">SKYLATOR</span>
      </div>

      {/* Nav links */}
      <nav className="flex-1 overflow-y-auto py-3 px-2 space-y-0.5">
        {NAV_ITEMS.map((item) => {
          const active = isActive(item)
          return (
            <Link
              key={item.to}
              to={item.to}
              className={cn(
                'flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors no-underline',
                active
                  ? 'bg-accent/15 text-accent'
                  : 'text-text-muted hover:text-text-main hover:bg-bg-card',
              )}
            >
              <span className={active ? 'text-accent' : 'text-text-muted'}>
                {item.icon}
              </span>
              {item.label}
            </Link>
          )
        })}
      </nav>

      {/* Machines badge */}
      <div className="px-4 py-3 border-t border-border-default">
        <div className="flex items-center gap-2">
          <span className="text-xs text-text-muted">Machines:</span>
          <span
            className={cn(
              'text-xs px-2 py-0.5 rounded-full font-medium',
              mode === 'smart'
                ? 'bg-accent/20 text-accent'
                : 'bg-accent2/20 text-accent2',
            )}
          >
            {mode}
          </span>
        </div>
      </div>
    </aside>
  )
}
