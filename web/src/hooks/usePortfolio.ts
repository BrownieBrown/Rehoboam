import { useQuery } from '@tanstack/react-query'
import { portfolioApi } from '../api/client'

export function useSquad() {
  return useQuery({
    queryKey: ['portfolio', 'squad'],
    queryFn: () => portfolioApi.getSquad(),
    staleTime: 60000,
  })
}

export function useBalance() {
  return useQuery({
    queryKey: ['portfolio', 'balance'],
    queryFn: () => portfolioApi.getBalance(),
    staleTime: 60000,
  })
}

export function useValueHistory() {
  return useQuery({
    queryKey: ['portfolio', 'history'],
    queryFn: () => portfolioApi.getHistory(),
    staleTime: 300000,
  })
}
