import {
  TrendingUp,
  TrendingDown,
  Wallet,
  Users,
  AlertTriangle,
  CheckCircle,
  ArrowUpRight,
  ArrowDownRight,
} from 'lucide-react'
import { useBalance, useSquad } from '../hooks/usePortfolio'
import { useRecommendations } from '../hooks/useAnalytics'
import { Link } from 'react-router-dom'

function formatCurrency(value: number): string {
  if (value >= 1000000) {
    return `${(value / 1000000).toFixed(1)}M`
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(0)}K`
  }
  return value.toString()
}

function StatCard({
  title,
  value,
  change,
  icon: Icon,
  color,
}: {
  title: string
  value: string
  change?: number
  icon: React.ElementType
  color: 'primary' | 'success' | 'danger' | 'warning'
}) {
  const colorClasses = {
    primary: 'bg-primary-50 text-primary-600',
    success: 'bg-success-50 text-success-600',
    danger: 'bg-danger-50 text-danger-600',
    warning: 'bg-warning-50 text-warning-600',
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-gray-500">{title}</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">{value}</p>
          {change !== undefined && (
            <p
              className={`text-sm mt-1 flex items-center ${
                change >= 0 ? 'text-success-600' : 'text-danger-600'
              }`}
            >
              {change >= 0 ? <ArrowUpRight size={16} /> : <ArrowDownRight size={16} />}
              {Math.abs(change).toFixed(1)}%
            </p>
          )}
        </div>
        <div className={`p-3 rounded-xl ${colorClasses[color]}`}>
          <Icon size={24} />
        </div>
      </div>
    </div>
  )
}

function RecommendationCard({
  type,
  player,
}: {
  type: 'buy' | 'sell'
  player: {
    player_name: string
    position: string
    team_name: string
    reason: string
    value_score: number
    price?: number
    profit_loss_pct?: number
  }
}) {
  const isBuy = type === 'buy'

  return (
    <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
      <div className="flex items-center space-x-3">
        <div
          className={`w-10 h-10 rounded-full flex items-center justify-center ${
            isBuy ? 'bg-success-50 text-success-600' : 'bg-danger-50 text-danger-600'
          }`}
        >
          {isBuy ? <TrendingUp size={20} /> : <TrendingDown size={20} />}
        </div>
        <div>
          <p className="font-medium text-gray-900">{player.player_name}</p>
          <p className="text-sm text-gray-500">
            {player.position} - {player.team_name}
          </p>
        </div>
      </div>
      <div className="text-right">
        <p className={`font-medium ${isBuy ? 'text-success-600' : 'text-danger-600'}`}>
          {isBuy ? `${formatCurrency(player.price || 0)}` : `${player.profit_loss_pct?.toFixed(1)}%`}
        </p>
        <p className="text-xs text-gray-500">{player.reason}</p>
      </div>
    </div>
  )
}

export default function Dashboard() {
  const { data: balance, isLoading: balanceLoading } = useBalance()
  const { data: squad, isLoading: squadLoading } = useSquad()
  const { data: recommendations, isLoading: recsLoading } = useRecommendations()

  const isLoading = balanceLoading || squadLoading || recsLoading

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
        <p className="text-gray-500 mt-1">Overview of your trading performance</p>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard
          title="Budget"
          value={isLoading ? '...' : formatCurrency(balance?.budget || 0)}
          icon={Wallet}
          color="primary"
        />
        <StatCard
          title="Team Value"
          value={isLoading ? '...' : formatCurrency(squad?.team_value || 0)}
          icon={TrendingUp}
          color="success"
        />
        <StatCard
          title="P&L"
          value={isLoading ? '...' : formatCurrency(squad?.total_profit_loss || 0)}
          change={
            squad?.team_value
              ? (squad.total_profit_loss / (squad.team_value - squad.total_profit_loss)) * 100
              : 0
          }
          icon={squad?.total_profit_loss >= 0 ? TrendingUp : TrendingDown}
          color={squad?.total_profit_loss >= 0 ? 'success' : 'danger'}
        />
        <StatCard
          title="Squad Size"
          value={isLoading ? '...' : `${squad?.squad_size || 0} players`}
          icon={Users}
          color="primary"
        />
      </div>

      {/* Recommendations */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Buy Recommendations */}
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-gray-900 flex items-center">
              <CheckCircle className="w-5 h-5 text-success-600 mr-2" />
              Buy Recommendations
            </h2>
            <Link to="/market" className="text-sm text-primary-600 hover:text-primary-700">
              View all
            </Link>
          </div>
          <div className="space-y-3">
            {isLoading ? (
              <p className="text-gray-500 text-center py-4">Loading...</p>
            ) : recommendations?.buy_recommendations?.length > 0 ? (
              recommendations.buy_recommendations.slice(0, 3).map((player: any) => (
                <RecommendationCard key={player.player_id} type="buy" player={player} />
              ))
            ) : (
              <p className="text-gray-500 text-center py-4">No buy recommendations</p>
            )}
          </div>
        </div>

        {/* Sell Recommendations */}
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-gray-900 flex items-center">
              <AlertTriangle className="w-5 h-5 text-danger-600 mr-2" />
              Sell Recommendations
            </h2>
            <Link to="/portfolio" className="text-sm text-primary-600 hover:text-primary-700">
              View all
            </Link>
          </div>
          <div className="space-y-3">
            {isLoading ? (
              <p className="text-gray-500 text-center py-4">Loading...</p>
            ) : recommendations?.sell_recommendations?.length > 0 ? (
              recommendations.sell_recommendations.slice(0, 3).map((player: any) => (
                <RecommendationCard key={player.player_id} type="sell" player={player} />
              ))
            ) : (
              <p className="text-gray-500 text-center py-4">No sell recommendations</p>
            )}
          </div>
        </div>
      </div>

      {/* Roster Gaps */}
      {recommendations?.roster_gaps?.length > 0 && (
        <div className="card bg-warning-50 border-warning-200">
          <h2 className="text-lg font-semibold text-warning-700 flex items-center mb-3">
            <AlertTriangle className="w-5 h-5 mr-2" />
            Roster Gaps
          </h2>
          <div className="flex flex-wrap gap-2">
            {recommendations.roster_gaps.map((gap: string, index: number) => (
              <span
                key={index}
                className="px-3 py-1 bg-warning-100 text-warning-700 rounded-full text-sm"
              >
                {gap}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
