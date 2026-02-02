import { TrendingUp, TrendingDown, AlertTriangle } from 'lucide-react'
import { useSquad } from '../hooks/usePortfolio'

function formatCurrency(value: number): string {
  if (value >= 1000000) {
    return `${(value / 1000000).toFixed(1)}M`
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(0)}K`
  }
  return value.toString()
}

const positionOrder = ['Goalkeeper', 'Defender', 'Midfielder', 'Forward']

function PlayerCard({ player }: { player: any }) {
  const profitLoss = player.profit_loss
  const profitLossPct = player.profit_loss_pct
  const isProfit = profitLoss >= 0
  const hasSellRec = player.sell_recommendation === 'SELL'

  return (
    <div className={`card ${hasSellRec ? 'border-danger-200 bg-danger-50/30' : ''}`}>
      <div className="flex items-start justify-between">
        <div>
          <p className="font-medium text-gray-900">
            {player.first_name} {player.last_name}
          </p>
          <p className="text-sm text-gray-500">{player.team_name}</p>
          <span className="badge badge-primary mt-2">{player.position}</span>
        </div>
        <div className="text-right">
          <p className="font-semibold text-lg">{formatCurrency(player.market_value)}</p>
          <p className="text-sm text-gray-500">
            Bought: {formatCurrency(player.purchase_price)}
          </p>
        </div>
      </div>

      <div className="mt-4 pt-4 border-t border-gray-100">
        <div className="flex items-center justify-between">
          <div className="flex items-center">
            {isProfit ? (
              <TrendingUp className="w-5 h-5 text-success-600 mr-2" />
            ) : (
              <TrendingDown className="w-5 h-5 text-danger-600 mr-2" />
            )}
            <span className={isProfit ? 'text-success-600' : 'text-danger-600'}>
              {isProfit ? '+' : ''}{formatCurrency(profitLoss)} ({profitLossPct.toFixed(1)}%)
            </span>
          </div>
          <div className="text-right">
            <span className="text-sm text-gray-500">Score: </span>
            <span
              className={`font-semibold ${
                player.value_score >= 70
                  ? 'text-success-600'
                  : player.value_score >= 50
                  ? 'text-warning-600'
                  : 'text-danger-600'
              }`}
            >
              {player.value_score.toFixed(0)}
            </span>
          </div>
        </div>

        {hasSellRec && (
          <div className="mt-3 flex items-center text-danger-600 text-sm">
            <AlertTriangle className="w-4 h-4 mr-1" />
            {player.sell_reason}
          </div>
        )}
      </div>
    </div>
  )
}

export default function Portfolio() {
  const { data: portfolio, isLoading, error } = useSquad()

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Portfolio</h1>
          <p className="text-gray-500 mt-1">Your squad and holdings</p>
        </div>
        <div className="text-center py-8 text-gray-500">Loading portfolio...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Portfolio</h1>
        </div>
        <div className="text-center py-8 text-danger-600">Failed to load portfolio</div>
      </div>
    )
  }

  // Group players by position
  const playersByPosition: Record<string, any[]> = {}
  portfolio?.squad?.forEach((player: any) => {
    if (!playersByPosition[player.position]) {
      playersByPosition[player.position] = []
    }
    playersByPosition[player.position].push(player)
  })

  // Sort each position group by value
  Object.values(playersByPosition).forEach((players) => {
    players.sort((a, b) => b.market_value - a.market_value)
  })

  const sellRecommendations = portfolio?.squad?.filter((p: any) => p.sell_recommendation === 'SELL') || []

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Portfolio</h1>
          <p className="text-gray-500 mt-1">Your squad and holdings</p>
        </div>
        <div className="text-right">
          <p className="text-sm text-gray-500">Total Value</p>
          <p className="text-2xl font-bold text-gray-900">
            {formatCurrency(portfolio?.team_value || 0)}
          </p>
          <p
            className={`text-sm ${
              portfolio?.total_profit_loss >= 0 ? 'text-success-600' : 'text-danger-600'
            }`}
          >
            {portfolio?.total_profit_loss >= 0 ? '+' : ''}
            {formatCurrency(portfolio?.total_profit_loss || 0)} P&L
          </p>
        </div>
      </div>

      {/* Sell alerts */}
      {sellRecommendations.length > 0 && (
        <div className="card bg-danger-50 border-danger-200">
          <h2 className="text-lg font-semibold text-danger-700 flex items-center mb-3">
            <AlertTriangle className="w-5 h-5 mr-2" />
            Sell Alerts ({sellRecommendations.length})
          </h2>
          <div className="flex flex-wrap gap-2">
            {sellRecommendations.map((p: any) => (
              <span key={p.id} className="px-3 py-1 bg-danger-100 text-danger-700 rounded-full text-sm">
                {p.first_name} {p.last_name} - {p.sell_reason}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Players by position */}
      {positionOrder.map((position) => {
        const players = playersByPosition[position]
        if (!players || players.length === 0) return null

        return (
          <div key={position}>
            <h2 className="text-lg font-semibold text-gray-900 mb-4">
              {position}s ({players.length})
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {players.map((player: any) => (
                <PlayerCard key={player.id} player={player} />
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}
