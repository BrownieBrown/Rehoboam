import { create } from 'zustand'

interface UIState {
  sidebarOpen: boolean
  selectedPlayerId: string | null
  marketFilters: {
    position: string
    minScore: number
    sortBy: string
  }
  toggleSidebar: () => void
  setSelectedPlayer: (id: string | null) => void
  setMarketFilters: (filters: Partial<UIState['marketFilters']>) => void
}

export const useUIStore = create<UIState>((set) => ({
  sidebarOpen: true,
  selectedPlayerId: null,
  marketFilters: {
    position: 'all',
    minScore: 0,
    sortBy: 'value_score',
  },
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSelectedPlayer: (id) => set({ selectedPlayerId: id }),
  setMarketFilters: (filters) =>
    set((state) => ({
      marketFilters: { ...state.marketFilters, ...filters },
    })),
}))
