import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { DEFAULT_STRINGS_PER_PAGE } from '@/lib/constants'
import type { Scope } from '@/lib/constants'

interface UiState {
  stringsPerPage: number
  stringsScope: Scope
  sidebarCollapsed: boolean
  setStringsPerPage: (n: number) => void
  setStringsScope: (scope: Scope) => void
  setSidebarCollapsed: (collapsed: boolean) => void
  toggleSidebar: () => void
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      stringsPerPage: DEFAULT_STRINGS_PER_PAGE,
      stringsScope: 'all',
      sidebarCollapsed: false,

      setStringsPerPage: (n) => set({ stringsPerPage: n }),
      setStringsScope: (scope) => set({ stringsScope: scope }),
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      toggleSidebar: () =>
        set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
    }),
    {
      name: 'translator_ui',
    },
  ),
)
