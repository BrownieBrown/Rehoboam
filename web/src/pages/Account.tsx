import { useQuery } from '@tanstack/react-query'
import { User, Trophy, Wallet, Users, CheckCircle, XCircle, RefreshCw } from 'lucide-react'
import { useAuthStore } from '../stores/authStore'
import { api } from '../api/client'

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('de-DE', {
    style: 'currency',
    currency: 'EUR',
    maximumFractionDigits: 0,
  }).format(value)
}

export default function Account() {
  const { user } = useAuthStore()

  const { data: health, isLoading: healthLoading, refetch: refetchHealth } = useQuery({
    queryKey: ['health'],
    queryFn: async () => {
      const res = await api.get('/api/health')
      return res.data
    },
    refetchInterval: 30000, // Check every 30 seconds
  })

  const { data: balance, isLoading: balanceLoading } = useQuery({
    queryKey: ['balance'],
    queryFn: async () => {
      const res = await api.get('/api/portfolio/balance')
      return res.data
    },
  })

  const isConnected = health?.status === 'healthy'

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold text-kb-white">Account</h1>
        <p className="text-kb-grey mt-1">Your account and league information</p>
      </div>

      {/* Connection Status */}
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-kb-white">Connection Status</h2>
          <button
            onClick={() => refetchHealth()}
            className="p-2 text-kb-grey hover:text-kb-white hover:bg-kb-card rounded-lg transition-colors"
            title="Refresh"
          >
            <RefreshCw size={16} className={healthLoading ? 'animate-spin' : ''} />
          </button>
        </div>
        <div className="flex items-center space-x-3">
          {healthLoading ? (
            <div className="w-5 h-5 border-2 border-kb-grey border-t-transparent rounded-full animate-spin" />
          ) : isConnected ? (
            <CheckCircle className="w-5 h-5 text-green-500" />
          ) : (
            <XCircle className="w-5 h-5 text-kb-red" />
          )}
          <div>
            <p className="text-kb-white font-medium">
              {healthLoading ? 'Checking...' : isConnected ? 'Connected' : 'Disconnected'}
            </p>
            <p className="text-sm text-kb-grey">
              {isConnected ? 'API connection is healthy' : 'Unable to reach API server'}
            </p>
          </div>
        </div>
      </div>

      {/* Account Info */}
      <div className="card">
        <h2 className="text-lg font-semibold text-kb-white mb-4">Account Info</h2>
        <div className="space-y-4">
          <div className="flex items-center space-x-4">
            <div className="w-12 h-12 bg-kb-card border border-kb-border rounded-full flex items-center justify-center">
              <User className="w-6 h-6 text-kb-red" />
            </div>
            <div>
              <p className="text-sm text-kb-grey">Email</p>
              <p className="text-kb-white font-medium">{user?.email || '-'}</p>
            </div>
          </div>
          <div className="flex items-center space-x-4">
            <div className="w-12 h-12 bg-kb-card border border-kb-border rounded-full flex items-center justify-center">
              <Users className="w-6 h-6 text-kb-red" />
            </div>
            <div>
              <p className="text-sm text-kb-grey">Team Name</p>
              <p className="text-kb-white font-medium">{user?.team_name || '-'}</p>
            </div>
          </div>
        </div>
      </div>

      {/* League Info */}
      <div className="card">
        <h2 className="text-lg font-semibold text-kb-white mb-4">League Info</h2>
        <div className="space-y-4">
          <div className="flex items-center space-x-4">
            <div className="w-12 h-12 bg-kb-card border border-kb-border rounded-full flex items-center justify-center">
              <Trophy className="w-6 h-6 text-kb-red" />
            </div>
            <div>
              <p className="text-sm text-kb-grey">League</p>
              <p className="text-kb-white font-medium">{user?.league_name || '-'}</p>
            </div>
          </div>
          <div className="flex items-center space-x-4">
            <div className="w-12 h-12 bg-kb-card border border-kb-border rounded-full flex items-center justify-center">
              <span className="text-kb-red font-mono text-xs">ID</span>
            </div>
            <div>
              <p className="text-sm text-kb-grey">League ID</p>
              <p className="text-kb-white font-mono text-sm">{user?.league_id || '-'}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Team Finances */}
      <div className="card">
        <h2 className="text-lg font-semibold text-kb-white mb-4">Team Finances</h2>
        {balanceLoading ? (
          <div className="text-kb-grey">Loading...</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-kb-card border border-kb-border rounded-lg p-4">
              <div className="flex items-center space-x-3 mb-2">
                <Wallet className="w-5 h-5 text-green-500" />
                <span className="text-sm text-kb-grey">Budget</span>
              </div>
              <p className="text-xl font-bold text-kb-white">
                {formatCurrency(balance?.budget || user?.budget || 0)}
              </p>
            </div>
            <div className="bg-kb-card border border-kb-border rounded-lg p-4">
              <div className="flex items-center space-x-3 mb-2">
                <Users className="w-5 h-5 text-blue-500" />
                <span className="text-sm text-kb-grey">Team Value</span>
              </div>
              <p className="text-xl font-bold text-kb-white">
                {formatCurrency(balance?.team_value || user?.team_value || 0)}
              </p>
            </div>
            <div className="bg-kb-card border border-kb-border rounded-lg p-4">
              <div className="flex items-center space-x-3 mb-2">
                <Trophy className="w-5 h-5 text-kb-red" />
                <span className="text-sm text-kb-grey">Total Assets</span>
              </div>
              <p className="text-xl font-bold text-kb-white">
                {formatCurrency(balance?.total_assets || (user?.budget || 0) + (user?.team_value || 0))}
              </p>
            </div>
          </div>
        )}
        {balance?.squad_size !== undefined && (
          <p className="text-sm text-kb-grey mt-4">
            Squad size: {balance.squad_size} players
          </p>
        )}
      </div>
    </div>
  )
}
