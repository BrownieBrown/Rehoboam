import { useQuery } from '@tanstack/react-query'
import { marketApi } from '../api/client'

export function useMarketPlayers(params?: {
  position?: string
  min_score?: number
  limit?: number
}) {
  return useQuery({
    queryKey: ['market', 'players', params],
    queryFn: () => marketApi.getPlayers(params),
    staleTime: 60000,
  })
}

export function useMarketPlayer(id: string | null) {
  return useQuery({
    queryKey: ['market', 'player', id],
    queryFn: () => (id ? marketApi.getPlayer(id) : null),
    enabled: !!id,
    staleTime: 60000,
  })
}

export function useMarketTrends() {
  return useQuery({
    queryKey: ['market', 'trends'],
    queryFn: () => marketApi.getTrends(),
    staleTime: 120000,
  })
}
