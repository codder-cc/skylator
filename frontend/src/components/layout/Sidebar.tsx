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
  PackageOpen,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react'
import * as Tooltip from '@radix-ui/react-tooltip'
import { cn } from '@/lib/utils'
import { useQuery } from '@tanstack/react-query'
import { jobsApi } from '@/api/jobs'
import { QK } from '@/lib/queryKeys'
import { useMachinesStore } from '@/stores/machinesStore'
import { useUiStore } from '@/stores/uiStore'

interface NavItem {
  to: string
  label: string
  icon: React.ReactNode
  exact?: boolean
}

const NAV_ITEMS: NavItem[] = [
  { to: '/',            label: 'Dashboard',   icon: <LayoutDashboard className="w-4 h-4" />, exact: true },
  { to: '/mods',        label: 'Mods',        icon: <Layers className="w-4 h-4" /> },
  { to: '/single',      label: 'Single Mod',  icon: <PackageOpen className="w-4 h-4" /> },
  { to: '/jobs',        label: 'Jobs',        icon: <Briefcase className="w-4 h-4" /> },
  { to: '/servers',     label: 'Servers',     icon: <Server className="w-4 h-4" /> },
  { to: '/config',      label: 'Config',      icon: <Settings className="w-4 h-4" /> },
  { to: '/terminology', label: 'Terminology', icon: <BookOpen className="w-4 h-4" /> },
  { to: '/backups',     label: 'Backups',     icon: <Archive className="w-4 h-4" /> },
  { to: '/tools',       label: 'Tools',       icon: <Wrench className="w-4 h-4" /> },
  { to: '/logs',        label: 'Logs',        icon: <ScrollText className="w-4 h-4" /> },
]


function NavTooltip({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Tooltip.Root delayDuration={0}>
      <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          side="right"
          sideOffset={8}
          className="z-[100] px-2.5 py-1.5 rounded-md text-xs font-medium bg-bg-card border border-border-default text-text-main shadow-lg"
        >
          {label}
          <Tooltip.Arrow className="fill-bg-card" />
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  )
}

export function Sidebar() {
  const routerState = useRouterState()
  const pathname = routerState.location.pathname
  const mode = useMachinesStore((s) => s.mode)
  const collapsed = useUiStore((s) => s.sidebarCollapsed)
  const toggleSidebar = useUiStore((s) => s.toggleSidebar)
  const { data: jobs = [] } = useQuery({
    queryKey: QK.jobs(),
    queryFn: jobsApi.list,
    staleTime: 5_000,
  })
  const runningCount = jobs.filter(
    (j) => j.status === 'running' || j.status === 'pending',
  ).length

  function isActive(item: NavItem): boolean {
    if (item.exact) return pathname === item.to
    return pathname.startsWith(item.to)
  }

  const w = collapsed ? 52 : 240

  return (
    <Tooltip.Provider>
      <aside
        className="flex flex-col bg-bg-sidebar border-r border-border-default transition-[width] duration-200 overflow-hidden"
        style={{ width: w, minWidth: w, flexShrink: 0 }}
      >
        {/* Brand + collapse toggle */}
        <div className="flex items-center gap-2.5 px-3 py-4 border-b border-border-default min-h-[57px]">
          <div className="w-7 h-7 rounded-md bg-accent/20 flex items-center justify-center flex-shrink-0">
            <Cpu className="w-3.5 h-3.5 text-accent" />
          </div>
          {!collapsed && (
            <span className="text-base font-bold text-accent tracking-widest flex-1 min-w-0 truncate">
              SKYLATOR
            </span>
          )}
          <button
            onClick={toggleSidebar}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className={cn(
              'text-text-muted/40 hover:text-text-muted transition-colors p-0.5 rounded flex-shrink-0',
              collapsed ? 'mx-auto' : 'ml-auto',
            )}
          >
            {collapsed
              ? <PanelLeftOpen className="w-3.5 h-3.5" />
              : <PanelLeftClose className="w-3.5 h-3.5" />}
          </button>
        </div>

        {/* Nav links */}
        <nav className="flex-1 overflow-y-auto py-3 px-1.5 space-y-0.5">
          {NAV_ITEMS.map((item) => {
            const active = isActive(item)
            const link = (
              <Link
                to={item.to}
                className={cn(
                  'relative flex items-center gap-3 px-2.5 py-2 rounded-md text-sm font-medium transition-colors no-underline',
                  collapsed ? 'justify-center' : '',
                  active
                    ? 'bg-accent/15 text-accent'
                    : 'text-text-muted hover:text-text-main hover:bg-bg-card',
                )}
              >
                <span className={cn('flex-shrink-0', active ? 'text-accent' : 'text-text-muted')}>
                  {item.icon}
                </span>
                {!collapsed && (
                  <>
                    <span className="truncate">{item.label}</span>
                    {item.to === '/jobs' && runningCount > 0 && (
                      <span className="ml-auto flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full bg-success text-bg-base text-[10px] font-bold leading-none">
                        {runningCount}
                      </span>
                    )}
                  </>
                )}
                {collapsed && item.to === '/jobs' && runningCount > 0 && (
                  <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 rounded-full bg-success" />
                )}
              </Link>
            )
            return (
              <div key={item.to}>
                {collapsed ? <NavTooltip label={item.label}>{link}</NavTooltip> : link}
              </div>
            )
          })}
        </nav>

        {/* Machines badge (hide when collapsed) */}
        {!collapsed && (
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
        )}
      </aside>
    </Tooltip.Provider>
  )
}
