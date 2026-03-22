import { create } from 'zustand'
import { persist } from 'zustand/middleware'

type MachinesMode = 'smart' | 'custom'

interface MachinesState {
  mode: MachinesMode
  custom: string[]
  setMode: (mode: MachinesMode) => void
  toggleCustom: (label: string) => void
  setCustom: (labels: string[]) => void
}

export const useMachinesStore = create<MachinesState>()(
  persist(
    (set) => ({
      mode: 'smart',
      custom: [],

      setMode: (mode) => set({ mode }),

      toggleCustom: (label) =>
        set((state) => ({
          custom: state.custom.includes(label)
            ? state.custom.filter((l) => l !== label)
            : [...state.custom, label],
        })),

      setCustom: (labels) => set({ custom: labels }),
    }),
    {
      name: 'translator_machines',
    },
  ),
)
