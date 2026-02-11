import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Search, Filter, ChevronDown, ChevronUp, Loader2, Info, ExternalLink } from 'lucide-react'
import { useMarketPlayers } from '../hooks/useMarket'
import { useUIStore } from '../stores/uiStore'
import { tradingApi } from '../api/client'

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

function getPositionBadge(position: string) {
  const badges: Record<string, string> = {
    Goalkeeper: 'badge-gk',
    Defender: 'badge-def',
    Midfielder: 'badge-mid',
    Forward: 'badge-fwd',
  }
  return badges[position] || 'badge-neutral'
}

function PlayerRow({ player, onViewDetail }: { player: any; onViewDetail: (id: string, price: number) => void }) {
  const [expanded, setExpanded] = useState(false)
  const [bidAmount, setBidAmount] = useState<number | null>(null)

  // Fetch suggested bid when expanded
  const { data: suggestedBid, isLoading: bidLoading, error: bidError } = useQuery({
    queryKey: ['suggested-bid', player.id, player.price],
    queryFn: () => tradingApi.getSuggestedBid(player.id, player.price),
    enabled: expanded,
    staleTime: 60000, // Cache for 1 minute
    retry: false,
  })

  // Set bid amount to suggested when loaded
  if (suggestedBid && bidAmount === null) {
    setBidAmount(suggestedBid.suggested_bid)
  }

  return (
    <>
      <tr
        className="table-row cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <td className="px-4 py-3">
          <div className="flex items-center">
            <div>
              <p className="font-medium text-kb-white">{player.first_name} {player.last_name}</p>
              <p className="text-sm text-kb-grey">{player.team_name}</p>
            </div>
          </div>
        </td>
        <td className="px-4 py-3">
          <span className={getPositionBadge(player.position)}>{player.position}</span>
        </td>
        <td className="px-4 py-3 text-right font-medium text-kb-white">
          {formatCurrency(player.price)}
        </td>
        <td className="px-4 py-3 text-right text-kb-grey-light">
          {formatCurrency(player.market_value)}
        </td>
        <td className="px-4 py-3 text-right">
          <span className="text-kb-grey-light">{player.average_points.toFixed(1)}</span>
        </td>
        <td className="px-4 py-3 text-right">
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
        </td>
        <td className="px-4 py-3">
          <span
            className={
              player.recommendation === 'BUY'
                ? 'badge-success'
                : player.recommendation === 'WATCH'
                ? 'badge-warning'
                : 'badge-danger'
            }
          >
            {player.recommendation}
          </span>
        </td>
        <td className="px-4 py-3 text-center">
          <div className="flex items-center justify-center space-x-2">
            <button
              onClick={(e) => {
                e.stopPropagation()
                onViewDetail(player.id, player.price)
              }}
              className="p-1 hover:bg-kb-card rounded"
              title="View full details"
            >
              <ExternalLink className="w-4 h-4 text-kb-grey hover:text-kb-white" />
            </button>
            {expanded ? (
              <ChevronUp className="w-4 h-4 text-kb-grey" />
            ) : (
              <ChevronDown className="w-4 h-4 text-kb-grey" />
            )}
          </div>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={8} className="px-4 py-4 bg-kb-dark border-b border-kb-border">
            <div className="space-y-4">
              {/* Roster Impact */}
              {player.roster_impact && (
                <div className="flex items-start space-x-2 text-sm">
                  <Info className="w-4 h-4 text-kb-red mt-0.5 flex-shrink-0" />
                  <span className="text-kb-grey-light">{player.roster_impact}</span>
                </div>
              )}

              {/* Factors */}
              <div className="text-sm">
                <span className="text-kb-grey">Scoring factors: </span>
                <span className="text-kb-grey-light">
                  {Object.entries(player.factors || {})
                    .map(([k, v]) => `${k}: ${(v as number).toFixed(0)}`)
                    .join(' · ')}
                </span>
              </div>

              {/* Smart Bid Section */}
              <div className="bg-kb-card border border-kb-border rounded-lg p-4">
                <h4 className="text-sm font-medium text-kb-white mb-3">Smart Bid Recommendation</h4>

                {bidLoading ? (
                  <div className="flex items-center space-x-2 text-kb-grey">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>Calculating optimal bid...</span>
                  </div>
                ) : suggestedBid ? (
                  <div className="space-y-3">
                    {suggestedBid.suggested_bid === 0 ? (
                      // Not recommended to buy
                      <div className="bg-danger-400/10 border border-danger-400/20 rounded-lg p-4">
                        <p className="text-danger-400 font-medium mb-2">Not Recommended</p>
                        <p className="text-sm text-kb-grey-light">
                          Current price ({formatCurrency(suggestedBid.current_price)}) exceeds the maximum profitable bid ({formatCurrency(suggestedBid.max_bid)}).
                          You would likely overpay for this player.
                        </p>
                        {suggestedBid.reasoning && (
                          <p className="text-xs text-kb-grey mt-2">{suggestedBid.reasoning}</p>
                        )}
                      </div>
                    ) : (
                      // Good to buy
                      <>
                        <div className="grid grid-cols-3 gap-4 text-sm">
                          <div>
                            <p className="text-kb-grey">Min Bid</p>
                            <p className="text-kb-white font-medium">{formatCurrency(suggestedBid.min_bid)}</p>
                          </div>
                          <div>
                            <p className="text-kb-grey">Suggested</p>
                            <p className="text-success-400 font-semibold">{formatCurrency(suggestedBid.suggested_bid)}</p>
                          </div>
                          <div>
                            <p className="text-kb-grey">Max Profitable</p>
                            <p className="text-kb-white font-medium">{formatCurrency(suggestedBid.max_bid)}</p>
                          </div>
                        </div>

                        {suggestedBid.reasoning && (
                          <p className="text-xs text-kb-grey">{suggestedBid.reasoning}</p>
                        )}

                        <div className="flex items-center space-x-3 pt-2">
                          <input
                            type="number"
                            className="input w-40"
                            placeholder="Bid amount"
                            value={bidAmount || ''}
                            onChange={(e) => setBidAmount(Number(e.target.value))}
                            onClick={(e) => e.stopPropagation()}
                            min={suggestedBid.min_bid}
                          />
                          <button
                            className="btn-primary"
                            onClick={(e) => {
                              e.stopPropagation()
                              // TODO: Implement actual bid placement
                              alert(`Bid of ${formatCurrency(bidAmount || 0)} would be placed (not implemented)`)
                            }}
                          >
                            Place Bid
                          </button>
                          <button
                            className="btn-secondary"
                            onClick={(e) => {
                              e.stopPropagation()
                              setBidAmount(suggestedBid.suggested_bid)
                            }}
                          >
                            Use Suggested
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                ) : bidError ? (
                  <p className="text-danger-400 text-sm">
                    Error: {(bidError as any)?.response?.data?.detail || (bidError as Error).message || 'Unable to calculate bid'}
                  </p>
                ) : (
                  <p className="text-kb-grey text-sm">Unable to calculate bid</p>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

export default function Market() {
  const navigate = useNavigate()
  const { marketFilters, setMarketFilters } = useUIStore()
  const [search, setSearch] = useState('')

  const handleViewDetail = (id: string, price: number) => {
    navigate(`/player/${id}?price=${price}`)
  }

  const { data: players, isLoading, error } = useMarketPlayers({
    position: marketFilters.position === 'all' ? undefined : marketFilters.position,
    min_score: marketFilters.minScore,
    limit: 100,
  })

  const filteredPlayers = players?.filter((p: any) =>
    `${p.first_name} ${p.last_name}`.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold text-kb-white">Market</h1>
        <p className="text-kb-grey mt-1">Browse and bid on players</p>
      </div>

      {/* Filters */}
      <div className="card">
        <div className="flex flex-wrap items-center gap-4">
          {/* Search */}
          <div className="relative flex-1 min-w-[200px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-kb-grey pointer-events-none" />
            <input
              type="text"
              placeholder="Search players..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="input"
              style={{ paddingLeft: '2.5rem' }}
            />
          </div>

          {/* Position filter */}
          <div className="flex items-center space-x-2">
            <Filter className="w-5 h-5 text-kb-grey" />
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
            <span className="text-sm text-kb-grey">Min Score:</span>
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
          <div className="p-8 text-center text-kb-grey">Loading players...</div>
        ) : error ? (
          <div className="p-8 text-center text-danger-400">Failed to load players</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="table-header">
                <tr>
                  <th className="px-4 py-3 text-left text-sm font-medium text-kb-grey-light">
                    Player
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-kb-grey-light">
                    Position
                  </th>
                  <th className="px-4 py-3 text-right text-sm font-medium text-kb-grey-light">
                    Price
                  </th>
                  <th className="px-4 py-3 text-right text-sm font-medium text-kb-grey-light">
                    Market Value
                  </th>
                  <th className="px-4 py-3 text-right text-sm font-medium text-kb-grey-light">
                    Avg Pts
                  </th>
                  <th className="px-4 py-3 text-right text-sm font-medium text-kb-grey-light">
                    Score
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-kb-grey-light">
                    Action
                  </th>
                  <th className="px-4 py-3 w-10"></th>
                </tr>
              </thead>
              <tbody>
                {filteredPlayers?.map((player: any) => (
                  <PlayerRow key={player.id} player={player} onViewDetail={handleViewDetail} />
                ))}
              </tbody>
            </table>
            {filteredPlayers?.length === 0 && (
              <div className="p-8 text-center text-kb-grey">No players found</div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
