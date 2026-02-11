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
import { Link, useNavigate } from 'react-router-dom'

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
  color: 'red' | 'green' | 'purple' | 'default'
}) {
  const iconColors = {
    red: 'text-kb-red bg-kb-red/10',
    green: 'text-success-400 bg-success-500/10',
    purple: 'text-kb-purple bg-kb-purple/10',
    default: 'text-kb-grey-light bg-kb-border',
  }

  return (
    <div className="card-hover">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-kb-grey">{title}</p>
          <p className="text-2xl font-bold text-kb-white mt-1">{value}</p>
          {change !== undefined && (
            <p
              className={`text-sm mt-1 flex items-center ${
                change >= 0 ? 'text-success-400' : 'text-danger-400'
              }`}
            >
              {change >= 0 ? <ArrowUpRight size={16} /> : <ArrowDownRight size={16} />}
              {Math.abs(change).toFixed(1)}%
            </p>
          )}
        </div>
        <div className={`p-3 rounded-xl ${iconColors[color]}`}>
          <Icon size={24} />
        </div>
      </div>
    </div>
  )
}

function RecommendationCard({
  type,
  player,
  onClick,
}: {
  type: 'buy' | 'sell'
  player: {
    player_id: string
    player_name: string
    position: string
    team_name: string
    reason: string
    value_score: number
    price?: number
    profit_loss_pct?: number
  }
  onClick: () => void
}) {
  const isBuy = type === 'buy'

  return (
    <div
      className="flex items-center justify-between p-4 bg-kb-dark rounded-lg border border-kb-border hover:border-kb-grey-dark transition-colors cursor-pointer"
      onClick={onClick}
    >
      <div className="flex items-center space-x-3">
        <div
          className={`w-10 h-10 rounded-full flex items-center justify-center ${
            isBuy ? 'bg-success-500/10 text-success-400' : 'bg-danger-500/10 text-danger-400'
          }`}
        >
          {isBuy ? <TrendingUp size={20} /> : <TrendingDown size={20} />}
        </div>
        <div>
          <p className="font-medium text-kb-white">{player.player_name}</p>
          <p className="text-sm text-kb-grey">
            {player.position} · {player.team_name}
          </p>
        </div>
      </div>
      <div className="text-right">
        <p className={`font-semibold ${isBuy ? 'text-success-400' : 'text-danger-400'}`}>
          {isBuy ? `${formatCurrency(player.price || 0)}` : `${player.profit_loss_pct?.toFixed(1)}%`}
        </p>
        <p className="text-xs text-kb-grey">{player.reason}</p>
      </div>
    </div>
  )
}

export default function Dashboard() {
  const navigate = useNavigate()
  const { data: balance, isLoading: balanceLoading } = useBalance()
  const { data: squad, isLoading: squadLoading } = useSquad()
  const { data: recommendations, isLoading: recsLoading } = useRecommendations()

  const isLoading = balanceLoading || squadLoading || recsLoading

  return (
    <div className="space-y-8 animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold text-kb-white">Dashboard</h1>
        <p className="text-kb-grey mt-1">Overview of your trading performance</p>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard
          title="Budget"
          value={isLoading ? '...' : formatCurrency(balance?.budget || 0)}
          icon={Wallet}
          color="purple"
        />
        <StatCard
          title="Team Value"
          value={isLoading ? '...' : formatCurrency(squad?.team_value || 0)}
          icon={TrendingUp}
          color="default"
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
          color={squad?.total_profit_loss >= 0 ? 'green' : 'red'}
        />
        <StatCard
          title="Squad Size"
          value={isLoading ? '...' : `${squad?.squad_size || 0} players`}
          icon={Users}
          color="default"
        />
      </div>

      {/* Recommendations */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Buy Recommendations */}
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-kb-white flex items-center">
              <CheckCircle className="w-5 h-5 text-success-400 mr-2" />
              Buy Recommendations
            </h2>
            <Link to="/market" className="text-sm text-kb-red hover:text-kb-red-dark transition-colors">
              View all
            </Link>
          </div>
          <div className="space-y-3">
            {isLoading ? (
              <p className="text-kb-grey text-center py-4">Loading...</p>
            ) : recommendations?.buy_recommendations?.length > 0 ? (
              recommendations.buy_recommendations.slice(0, 3).map((player: any) => (
                <RecommendationCard
                  key={player.player_id}
                  type="buy"
                  player={player}
                  onClick={() => navigate(`/player/${player.player_id}?price=${player.price}`)}
                />
              ))
            ) : (
              <p className="text-kb-grey text-center py-4">No buy recommendations</p>
            )}
          </div>
        </div>

        {/* Sell Recommendations */}
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-kb-white flex items-center">
              <AlertTriangle className="w-5 h-5 text-danger-400 mr-2" />
              Sell Recommendations
            </h2>
            <Link to="/portfolio" className="text-sm text-kb-red hover:text-kb-red-dark transition-colors">
              View all
            </Link>
          </div>
          <div className="space-y-3">
            {isLoading ? (
              <p className="text-kb-grey text-center py-4">Loading...</p>
            ) : recommendations?.sell_recommendations?.length > 0 ? (
              recommendations.sell_recommendations.slice(0, 3).map((player: any) => (
                <RecommendationCard
                  key={player.player_id}
                  type="sell"
                  player={player}
                  onClick={() => navigate(`/player/${player.player_id}`)}
                />
              ))
            ) : (
              <p className="text-kb-grey text-center py-4">No sell recommendations</p>
            )}
          </div>
        </div>
      </div>

      {/* Roster Gaps */}
      {recommendations?.roster_gaps?.length > 0 && (
        <div className="card bg-warning-500/5 border-warning-500/20">
          <h2 className="text-lg font-semibold text-warning-400 flex items-center mb-3">
            <AlertTriangle className="w-5 h-5 mr-2" />
            Roster Gaps
          </h2>
          <div className="flex flex-wrap gap-2">
            {recommendations.roster_gaps.map((gap: string, index: number) => (
              <span
                key={index}
                className="badge-warning"
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
