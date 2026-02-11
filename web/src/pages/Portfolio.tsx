import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { TrendingUp, TrendingDown, Users } from 'lucide-react'
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

const positionConfig: Record<string, { label: string; shortLabel: string; color: string; activeColor: string }> = {
  Goalkeeper: { label: 'Goalkeepers', shortLabel: 'GK', color: 'text-amber-400', activeColor: 'bg-amber-400' },
  Defender: { label: 'Defenders', shortLabel: 'DEF', color: 'text-blue-400', activeColor: 'bg-blue-400' },
  Midfielder: { label: 'Midfielders', shortLabel: 'MID', color: 'text-emerald-400', activeColor: 'bg-emerald-400' },
  Forward: { label: 'Forwards', shortLabel: 'FW', color: 'text-rose-400', activeColor: 'bg-rose-400' },
}

const positionOrder = ['Goalkeeper', 'Defender', 'Midfielder', 'Forward']

function PlayerCard({ player, onClick }: { player: any; onClick: () => void }) {
  const profitLoss = player.profit_loss
  const profitLossPct = player.profit_loss_pct
  const isProfit = profitLoss >= 0
  const hasSellRec = player.sell_recommendation === 'SELL'

  return (
    <div className="card-hover cursor-pointer" onClick={onClick}>
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center space-x-2">
            <p className="font-medium text-kb-white">
              {player.first_name} {player.last_name}
            </p>
            {hasSellRec && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-danger-500/20 text-danger-400">
                Sell
              </span>
            )}
          </div>
          <p className="text-sm text-kb-grey mt-1">{player.team_name}</p>
        </div>
        <div className="text-right">
          <p className="font-semibold text-lg text-kb-white">{formatCurrency(player.market_value)}</p>
          <p className="text-sm text-kb-grey">
            Bought: {formatCurrency(player.purchase_price)}
          </p>
        </div>
      </div>

      <div className="mt-4 pt-4 border-t border-kb-border flex items-center justify-between">
        <div className="flex items-center">
          {isProfit ? (
            <TrendingUp className="w-5 h-5 text-success-400 mr-2" />
          ) : (
            <TrendingDown className="w-5 h-5 text-danger-400 mr-2" />
          )}
          <span className={isProfit ? 'text-success-400' : 'text-danger-400'}>
            {isProfit ? '+' : ''}{formatCurrency(profitLoss)} ({profitLossPct.toFixed(1)}%)
          </span>
        </div>
        <div>
          <span className="text-sm text-kb-grey">Score: </span>
          <span
            className={`font-semibold ${
              player.value_score >= 70
                ? 'text-success-400'
                : player.value_score >= 50
                ? 'text-warning-400'
                : 'text-danger-400'
            }`}
          >
            {player.value_score.toFixed(0)}
          </span>
        </div>
      </div>
    </div>
  )
}

function PositionTab({
  position,
  count,
  isActive,
  onClick,
}: {
  position: string
  count: number
  isActive: boolean
  onClick: () => void
}) {
  const config = positionConfig[position]

  return (
    <button
      onClick={onClick}
      className={`relative flex flex-col items-center px-4 py-3 rounded-lg transition-all ${
        isActive
          ? 'bg-kb-card border border-kb-border'
          : 'hover:bg-kb-card/50'
      }`}
    >
      <div className="flex items-center space-x-2">
        <span className={`font-medium ${isActive ? config.color : 'text-kb-grey-light'}`}>
          {config.shortLabel}
        </span>
        <span className={`text-sm ${isActive ? 'text-kb-white' : 'text-kb-grey'}`}>
          {count}
        </span>
      </div>
      {isActive && (
        <div className={`absolute bottom-0 left-1/2 -translate-x-1/2 w-8 h-1 ${config.activeColor} rounded-full`} />
      )}
    </button>
  )
}

export default function Portfolio() {
  const navigate = useNavigate()
  const [activePosition, setActivePosition] = useState('Goalkeeper')
  const { data: portfolio, isLoading, error } = useSquad()

  if (isLoading) {
    return (
      <div className="space-y-6 animate-fade-in">
        <div>
          <h1 className="text-2xl font-bold text-kb-white">Portfolio</h1>
          <p className="text-kb-grey mt-1">Your squad and holdings</p>
        </div>
        <div className="text-center py-8 text-kb-grey">Loading portfolio...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="space-y-6 animate-fade-in">
        <div>
          <h1 className="text-2xl font-bold text-kb-white">Portfolio</h1>
        </div>
        <div className="text-center py-8 text-danger-400">Failed to load portfolio</div>
      </div>
    )
  }

  // Group players by position
  const playersByPosition: Record<string, any[]> = {}
  positionOrder.forEach((pos) => {
    playersByPosition[pos] = []
  })
  portfolio?.squad?.forEach((player: any) => {
    if (playersByPosition[player.position]) {
      playersByPosition[player.position].push(player)
    }
  })

  // Sort each position group by value
  Object.values(playersByPosition).forEach((players) => {
    players.sort((a, b) => b.market_value - a.market_value)
  })

  const totalPlayers = portfolio?.squad?.length || 0
  const activePlayers = playersByPosition[activePosition] || []
  const config = positionConfig[activePosition]

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-kb-white">Portfolio</h1>
          <p className="text-kb-grey mt-1">Your squad and holdings</p>
        </div>
        <div className="flex items-center space-x-6">
          <div className="flex items-center space-x-2">
            <Users className="w-5 h-5 text-kb-grey" />
            <span className="text-kb-grey-light">{totalPlayers} players</span>
          </div>
          <div className="text-right">
            <p className="text-sm text-kb-grey">Total Value</p>
            <p className="text-xl font-bold text-kb-white">
              {formatCurrency(portfolio?.team_value || 0)}
            </p>
          </div>
          <div className="text-right">
            <p className="text-sm text-kb-grey">Total P&L</p>
            <p
              className={`text-xl font-bold ${
                portfolio?.total_profit_loss >= 0 ? 'text-success-400' : 'text-danger-400'
              }`}
            >
              {portfolio?.total_profit_loss >= 0 ? '+' : ''}
              {formatCurrency(portfolio?.total_profit_loss || 0)}
            </p>
          </div>
        </div>
      </div>

      {/* Position tabs */}
      <div className="flex items-center space-x-2 bg-kb-dark rounded-xl p-2">
        {positionOrder.map((position) => (
          <PositionTab
            key={position}
            position={position}
            count={playersByPosition[position].length}
            isActive={activePosition === position}
            onClick={() => setActivePosition(position)}
          />
        ))}
      </div>

      {/* Active position content */}
      <div>
        <div className="mb-4">
          <h2 className={`text-lg font-semibold ${config.color}`}>
            {config.label}
          </h2>
        </div>

        {activePlayers.length === 0 ? (
          <div className="card text-center py-8 text-kb-grey">
            No {config.label.toLowerCase()} in your squad
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {activePlayers.map((player: any) => (
              <PlayerCard
                key={player.id}
                player={player}
                onClick={() => navigate(`/player/${player.id}`)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
