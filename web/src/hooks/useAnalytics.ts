import { useQuery } from '@tanstack/react-query'
import { analyticsApi } from '../api/client'

export function useRecommendations() {
  return useQuery({
    queryKey: ['analytics', 'recommendations'],
    queryFn: () => analyticsApi.getRecommendations(),
    staleTime: 120000,
  })
}

export function useRosterImpact() {
  return useQuery({
    queryKey: ['analytics', 'roster-impact'],
    queryFn: () => analyticsApi.getRosterImpact(),
    staleTime: 120000,
  })
}
