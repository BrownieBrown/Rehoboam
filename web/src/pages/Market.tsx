import { useState } from 'react'
import { Search, Filter, TrendingUp, TrendingDown, Minus } from 'lucide-react'
import { useMarketPlayers } from '../hooks/useMarket'
import { useUIStore } from '../stores/uiStore'

const positions = ['all', 'Goalkeeper', 'Defender', 'Midfielder', 'Forward']

function formatCurrency(value: number): string {
  if (value >= 1000000) {
    return `${(value / 1000000).toFixed(1)}M`
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(0)}K`
  }
  return value.toString()
}

function TrendIndicator({ direction }: { direction: string | null }) {
  if (direction === 'rising') {
    return <TrendingUp className="w-4 h-4 text-success-600" />
  }
  if (direction === 'falling') {
    return <TrendingDown className="w-4 h-4 text-danger-600" />
  }
  return <Minus className="w-4 h-4 text-gray-400" />
}

function PlayerRow({ player }: { player: any }) {
  const [showBidForm, setShowBidForm] = useState(false)

  return (
    <>
      <tr
        className="hover:bg-gray-50 cursor-pointer"
        onClick={() => setShowBidForm(!showBidForm)}
      >
        <td className="px-4 py-3">
          <div>
            <p className="font-medium text-gray-900">{player.first_name} {player.last_name}</p>
            <p className="text-sm text-gray-500">{player.team_name}</p>
          </div>
        </td>
        <td className="px-4 py-3">
          <span className="badge badge-primary">{player.position}</span>
        </td>
        <td className="px-4 py-3 text-right font-medium">
          {formatCurrency(player.price)}
        </td>
        <td className="px-4 py-3 text-right">
          {formatCurrency(player.market_value)}
        </td>
        <td className="px-4 py-3 text-center">
          <TrendIndicator direction={player.trend_direction} />
        </td>
        <td className="px-4 py-3 text-right">
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
        </td>
        <td className="px-4 py-3">
          <span
            className={`badge ${
              player.recommendation === 'BUY'
                ? 'badge-success'
                : player.recommendation === 'WATCH'
                ? 'badge-warning'
                : 'badge-danger'
            }`}
          >
            {player.recommendation}
          </span>
        </td>
      </tr>
      {showBidForm && (
        <tr>
          <td colSpan={7} className="px-4 py-3 bg-gray-50">
            <div className="flex items-center justify-between">
              <div className="text-sm text-gray-600">
                <strong>Factors:</strong>{' '}
                {Object.entries(player.factors || {})
                  .map(([k, v]) => `${k}: ${(v as number).toFixed(0)}`)
                  .join(', ')}
              </div>
              <div className="flex items-center space-x-3">
                <input
                  type="number"
                  className="input w-32"
                  placeholder="Bid amount"
                  defaultValue={Math.round(player.price * 1.15)}
                />
                <button className="btn-primary">Place Bid</button>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

export default function Market() {
  const { marketFilters, setMarketFilters } = useUIStore()
  const [search, setSearch] = useState('')

  const { data: players, isLoading, error } = useMarketPlayers({
    position: marketFilters.position === 'all' ? undefined : marketFilters.position,
    min_score: marketFilters.minScore,
    limit: 100,
  })

  const filteredPlayers = players?.filter((p: any) =>
    `${p.first_name} ${p.last_name}`.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Market</h1>
        <p className="text-gray-500 mt-1">Browse and bid on players</p>
      </div>

      {/* Filters */}
      <div className="card">
        <div className="flex flex-wrap items-center gap-4">
          {/* Search */}
          <div className="relative flex-1 min-w-[200px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
            <input
              type="text"
              placeholder="Search players..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="input pl-10"
            />
          </div>

          {/* Position filter */}
          <div className="flex items-center space-x-2">
            <Filter className="w-5 h-5 text-gray-400" />
            <select
              value={marketFilters.position}
              onChange={(e) => setMarketFilters({ position: e.target.value })}
              className="input w-auto"
            >
              {positions.map((pos) => (
                <option key={pos} value={pos}>
                  {pos === 'all' ? 'All Positions' : pos}
                </option>
              ))}
            </select>
          </div>

          {/* Min score filter */}
          <div className="flex items-center space-x-2">
            <span className="text-sm text-gray-500">Min Score:</span>
            <input
              type="number"
              value={marketFilters.minScore}
              onChange={(e) => setMarketFilters({ minScore: Number(e.target.value) })}
              className="input w-20"
              min={0}
              max={100}
            />
          </div>
        </div>
      </div>

      {/* Players table */}
      <div className="card overflow-hidden p-0">
        {isLoading ? (
          <div className="p-8 text-center text-gray-500">Loading players...</div>
        ) : error ? (
          <div className="p-8 text-center text-danger-600">Failed to load players</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">
                    Player
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">
                    Position
                  </th>
                  <th className="px-4 py-3 text-right text-sm font-medium text-gray-500">
                    Price
                  </th>
                  <th className="px-4 py-3 text-right text-sm font-medium text-gray-500">
                    Market Value
                  </th>
                  <th className="px-4 py-3 text-center text-sm font-medium text-gray-500">
                    Trend
                  </th>
                  <th className="px-4 py-3 text-right text-sm font-medium text-gray-500">
                    Score
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-500">
                    Action
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {filteredPlayers?.map((player: any) => (
                  <PlayerRow key={player.id} player={player} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
