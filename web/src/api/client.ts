import axios from 'axios'

const API_URL = import.meta.env.VITE_API_URL || ''

export const api = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Add auth token to requests
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('auth_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Handle auth errors
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Clear both the token and zustand persisted auth state
      // ProtectedRoute will handle redirect when isAuthenticated becomes false
      localStorage.removeItem('auth_token')
      localStorage.removeItem('auth-storage')
    }
    return Promise.reject(error)
  }
)

// API functions
export const authApi = {
  login: async (email: string, password: string) => {
    const res = await api.post('/api/auth/login', { email, password })
    return res.data
  },
  getMe: async () => {
    const res = await api.get('/api/auth/me')
    return res.data
  },
}

export const marketApi = {
  getPlayers: async (params?: { position?: string; min_score?: number; limit?: number }) => {
    const res = await api.get('/api/market/players', { params })
    return res.data
  },
  getPlayer: async (id: string) => {
    const res = await api.get(`/api/market/players/${id}`)
    return res.data
  },
  getPlayerFull: async (id: string, currentPrice?: number) => {
    const params = currentPrice ? { current_price: currentPrice } : {}
    const res = await api.get(`/api/market/players/${id}/full`, { params })
    return res.data
  },
  getTrends: async () => {
    const res = await api.get('/api/market/trends')
    return res.data
  },
}

export const portfolioApi = {
  getSquad: async () => {
    const res = await api.get('/api/portfolio/squad')
    return res.data
  },
  getBalance: async () => {
    const res = await api.get('/api/portfolio/balance')
    return res.data
  },
  getHistory: async () => {
    const res = await api.get('/api/portfolio/history')
    return res.data
  },
}

export const analyticsApi = {
  getRecommendations: async () => {
    const res = await api.get('/api/analytics/recommendations')
    return res.data
  },
  getRosterImpact: async () => {
    const res = await api.get('/api/analytics/roster-impact')
    return res.data
  },
}

export const tradingApi = {
  placeBid: async (playerId: string, amount: number, live: boolean = false) => {
    const res = await api.post('/api/trading/bid', { player_id: playerId, amount, live })
    return res.data
  },
  listForSale: async (playerId: string, price: number, live: boolean = false) => {
    const res = await api.post('/api/trading/sell', { player_id: playerId, price, live })
    return res.data
  },
  getAuctions: async () => {
    const res = await api.get('/api/trading/auctions')
    return res.data
  },
  getSuggestedBid: async (playerId: string, currentPrice?: number) => {
    const params = currentPrice ? { current_price: currentPrice } : {}
    const res = await api.get(`/api/trading/suggested-bid/${playerId}`, { params })
    return res.data
  },
}
