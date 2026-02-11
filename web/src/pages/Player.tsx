import { useState } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  ArrowLeft,
  TrendingUp,
  TrendingDown,
  AlertTriangle,
  Shield,
  Calendar,
  Activity,
  DollarSign,
  Target,
  Loader2,
  Home,
  Plane,
} from 'lucide-react'
import {
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  ComposedChart,
} from 'recharts'
import { usePlayerDetail } from '../hooks/useMarket'
import { tradingApi } from '../api/client'

function formatCurrency(value: number): string {
  if (value >= 1000000) {
    return `${(value / 1000000).toFixed(2)}M`
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

function getPositionShort(position: string) {
  const shorts: Record<string, string> = {
    Goalkeeper: 'GK',
    Defender: 'DEF',
    Midfielder: 'MID',
    Forward: 'FWD',
  }
  return shorts[position] || position
}

function StatCard({
  label,
  value,
  subValue,
  icon: Icon,
  valueColor,
}: {
  label: string
  value: string
  subValue?: string
  icon: React.ElementType
  valueColor?: string
}) {
  return (
    <div className="card">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm text-kb-grey">{label}</p>
          <p className={`text-xl font-bold mt-1 ${valueColor || 'text-kb-white'}`}>{value}</p>
          {subValue && <p className="text-xs text-kb-grey mt-1">{subValue}</p>}
        </div>
        <div className="p-2 rounded-lg bg-kb-border/50">
          <Icon className="w-5 h-5 text-kb-grey-light" />
        </div>
      </div>
    </div>
  )
}

function FactorBar({ name, score, reason }: { name: string; score: number; reason: string }) {
  const isPositive = score > 0
  const barWidth = Math.min(Math.abs(score) * 2, 100)

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-sm">
        <span className="text-kb-grey-light">{name}</span>
        <span className={isPositive ? 'text-success-400' : score < 0 ? 'text-danger-400' : 'text-kb-grey'}>
          {score > 0 ? '+' : ''}{score.toFixed(0)}
        </span>
      </div>
      <div className="h-2 bg-kb-dark rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${
            isPositive ? 'bg-success-400' : score < 0 ? 'bg-danger-400' : 'bg-kb-grey'
          }`}
          style={{ width: `${barWidth}%` }}
        />
      </div>
      <p className="text-xs text-kb-grey">{reason}</p>
    </div>
  )
}

function DifficultyDots({ difficulty }: { difficulty: string }) {
  const filled = difficulty === 'Hard' ? 4 : difficulty === 'Medium' ? 2 : 1
  return (
    <div className="flex items-center space-x-1">
      {[1, 2, 3, 4].map((i) => (
        <div
          key={i}
          className={`w-2 h-2 rounded-full ${
            i <= filled ? (filled >= 4 ? 'bg-danger-400' : filled >= 2 ? 'bg-warning-400' : 'bg-success-400') : 'bg-kb-border'
          }`}
        />
      ))}
    </div>
  )
}

export default function Player() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const currentPrice = searchParams.get('price') ? parseInt(searchParams.get('price')!) : undefined

  const { data: player, isLoading, error } = usePlayerDetail(id, currentPrice)
  const [bidAmount, setBidAmount] = useState<number | null>(null)

  // Fetch suggested bid when on market
  const { data: suggestedBid, isLoading: bidLoading } = useQuery({
    queryKey: ['suggested-bid', id, player?.price],
    queryFn: () => tradingApi.getSuggestedBid(id!, player?.price),
    enabled: !!id && player?.is_on_market,
    staleTime: 60000,
  })

  if (isLoading) {
    return (
      <div className="space-y-6 animate-fade-in">
        <div className="flex items-center space-x-4">
          <button onClick={() => navigate(-1)} className="btn-ghost">
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div className="text-kb-grey">Loading player details...</div>
        </div>
      </div>
    )
  }

  if (error || !player) {
    return (
      <div className="space-y-6 animate-fade-in">
        <div className="flex items-center space-x-4">
          <button onClick={() => navigate(-1)} className="btn-ghost">
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div className="text-danger-400">Failed to load player details</div>
        </div>
      </div>
    )
  }

  // Prepare chart data
  const chartData = player.trend_history?.map((point: any) => ({
    date: point.date?.split('T')[0] || '',
    value: point.value,
  })) || []

  // Add prediction data if available
  if (player.predictions && chartData.length > 0) {
    const lastDate = new Date(chartData[chartData.length - 1].date)
    const pred7d = new Date(lastDate)
    pred7d.setDate(pred7d.getDate() + 7)
    const pred14d = new Date(lastDate)
    pred14d.setDate(pred14d.getDate() + 14)
    const pred30d = new Date(lastDate)
    pred30d.setDate(pred30d.getDate() + 30)

    chartData.push({
      date: pred7d.toISOString().split('T')[0],
      predicted: player.predictions.predicted_value_7d,
    })
    chartData.push({
      date: pred14d.toISOString().split('T')[0],
      predicted: player.predictions.predicted_value_14d,
    })
    chartData.push({
      date: pred30d.toISOString().split('T')[0],
      predicted: player.predictions.predicted_value_30d,
    })
  }

  const scoreColor =
    player.value_score >= 70
      ? 'text-success-400'
      : player.value_score >= 50
      ? 'text-warning-400'
      : 'text-danger-400'

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-center space-x-4">
          <button onClick={() => navigate(-1)} className="btn-ghost p-2">
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <div className="flex items-center space-x-3">
              <h1 className="text-2xl font-bold text-kb-white">
                {player.first_name} {player.last_name}
              </h1>
              <span className={getPositionBadge(player.position)}>
                {getPositionShort(player.position)}
              </span>
              {player.is_on_market && (
                <span className="badge-primary">On Market</span>
              )}
              {player.is_in_squad && (
                <span className="badge-purple">In Squad</span>
              )}
            </div>
            <p className="text-kb-grey mt-1">{player.team_name}</p>
          </div>
        </div>
        <div className="text-right">
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
        </div>
      </div>

      {/* Roster Context Notice - Show when roster-adjusted recommendation differs */}
      {player.roster_recommendation && player.roster_recommendation !== player.recommendation && (
        <div className="bg-warning-500/10 border border-warning-500/20 rounded-lg p-4">
          <div className="flex items-start space-x-3">
            <AlertTriangle className="w-5 h-5 text-warning-400 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-medium text-warning-400 mb-1">
                Roster Context Affects Recommendation
              </p>
              <p className="text-sm text-kb-grey-light mb-2">
                This page shows the <span className="text-kb-white font-medium">pure fundamental analysis</span> (
                <span className={player.recommendation === 'BUY' ? 'text-success-400' : player.recommendation === 'SKIP' ? 'text-danger-400' : 'text-warning-400'}>
                  {player.recommendation}
                </span>
                ), which evaluates the player based on value trends and metrics alone.
              </p>
              <p className="text-sm text-kb-grey-light">
                Market & Dashboard show{' '}
                <span className={player.roster_recommendation === 'BUY' ? 'text-success-400' : player.roster_recommendation === 'SKIP' ? 'text-danger-400' : 'text-warning-400'}>
                  {player.roster_recommendation}
                </span>{' '}
                because{' '}
                <span className="text-kb-white">{player.roster_impact}</span>
                {player.roster_value_score && (
                  <span className="text-kb-grey"> (roster-adjusted score: {player.roster_value_score.toFixed(0)})</span>
                )}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Stat Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="Price"
          value={player.price ? formatCurrency(player.price) : '-'}
          subValue={`MV: ${formatCurrency(player.market_value)}`}
          icon={DollarSign}
        />
        <StatCard
          label="Value Score"
          value={`${player.value_score.toFixed(0)}/100`}
          subValue={`${(player.confidence * 100).toFixed(0)}% confidence`}
          icon={Target}
          valueColor={scoreColor}
        />
        <StatCard
          label="Avg Points"
          value={player.average_points.toFixed(1)}
          subValue={player.games_played ? `${player.games_played} games` : undefined}
          icon={Activity}
        />
        <StatCard
          label="Status"
          value={player.status || 'Unknown'}
          subValue={player.lineup_probability ? `Prob: ${player.lineup_probability}/5` : undefined}
          icon={Shield}
        />
      </div>

      {/* Market Value Chart */}
      {chartData.length > 0 && (
        <div className="card">
          <h2 className="text-lg font-semibold text-kb-white mb-4">Market Value History</h2>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartData} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
                <XAxis
                  dataKey="date"
                  stroke="#6b7280"
                  fontSize={12}
                  tickFormatter={(value) => {
                    const date = new Date(value)
                    return `${date.getDate()}/${date.getMonth() + 1}`
                  }}
                />
                <YAxis
                  stroke="#6b7280"
                  fontSize={12}
                  tickFormatter={(value) => formatCurrency(value)}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#1a1a1a',
                    border: '1px solid #2a2a2a',
                    borderRadius: '8px',
                  }}
                  labelStyle={{ color: '#a8adb4' }}
                  formatter={(value: number, name: string) => [
                    formatCurrency(value),
                    name === 'predicted' ? 'Predicted' : 'Value',
                  ]}
                />
                <ReferenceLine y={player.market_value} stroke="#4a4a4a" strokeDasharray="3 3" />
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke="#e11d48"
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  type="monotone"
                  dataKey="predicted"
                  stroke="#9747FF"
                  strokeWidth={2}
                  strokeDasharray="5 5"
                  dot={{ fill: '#9747FF', r: 4 }}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          {player.trend_pct !== null && (
            <div className="flex items-center mt-2 text-sm">
              {player.trend_direction === 'rising' ? (
                <TrendingUp className="w-4 h-4 text-success-400 mr-1" />
              ) : player.trend_direction === 'falling' ? (
                <TrendingDown className="w-4 h-4 text-danger-400 mr-1" />
              ) : null}
              <span
                className={
                  player.trend_pct > 0
                    ? 'text-success-400'
                    : player.trend_pct < 0
                    ? 'text-danger-400'
                    : 'text-kb-grey'
                }
              >
                {player.trend_pct > 0 ? '+' : ''}{player.trend_pct?.toFixed(1)}% over period
              </span>
            </div>
          )}
        </div>
      )}

      {/* Two Column Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Analysis Factors */}
        <div className="card">
          <h2 className="text-lg font-semibold text-kb-white mb-4">Analysis Factors</h2>
          <div className="space-y-4">
            {player.factor_details?.map((factor: any, index: number) => (
              <FactorBar
                key={index}
                name={factor.name}
                score={factor.score}
                reason={factor.reason}
              />
            ))}
          </div>
        </div>

        {/* Price Projections */}
        <div className="card">
          <h2 className="text-lg font-semibold text-kb-white mb-4">Price Projections</h2>
          {player.predictions ? (
            <div className="space-y-4">
              <div className="flex items-center justify-between p-3 bg-kb-dark rounded-lg">
                <span className="text-kb-grey-light">7 Days</span>
                <div className="text-right">
                  <p className="font-medium text-kb-white">
                    {formatCurrency(player.predictions.predicted_value_7d)}
                  </p>
                  <p
                    className={`text-sm ${
                      player.predictions.change_7d_pct > 0
                        ? 'text-success-400'
                        : player.predictions.change_7d_pct < 0
                        ? 'text-danger-400'
                        : 'text-kb-grey'
                    }`}
                  >
                    {player.predictions.change_7d_pct > 0 ? '+' : ''}
                    {player.predictions.change_7d_pct.toFixed(1)}%
                  </p>
                </div>
              </div>
              <div className="flex items-center justify-between p-3 bg-kb-dark rounded-lg">
                <span className="text-kb-grey-light">14 Days</span>
                <div className="text-right">
                  <p className="font-medium text-kb-white">
                    {formatCurrency(player.predictions.predicted_value_14d)}
                  </p>
                  <p
                    className={`text-sm ${
                      player.predictions.change_14d_pct > 0
                        ? 'text-success-400'
                        : player.predictions.change_14d_pct < 0
                        ? 'text-danger-400'
                        : 'text-kb-grey'
                    }`}
                  >
                    {player.predictions.change_14d_pct > 0 ? '+' : ''}
                    {player.predictions.change_14d_pct.toFixed(1)}%
                  </p>
                </div>
              </div>
              <div className="flex items-center justify-between p-3 bg-kb-dark rounded-lg">
                <span className="text-kb-grey-light">30 Days</span>
                <div className="text-right">
                  <p className="font-medium text-kb-white">
                    {formatCurrency(player.predictions.predicted_value_30d)}
                  </p>
                  <p
                    className={`text-sm ${
                      player.predictions.change_30d_pct > 0
                        ? 'text-success-400'
                        : player.predictions.change_30d_pct < 0
                        ? 'text-danger-400'
                        : 'text-kb-grey'
                    }`}
                  >
                    {player.predictions.change_30d_pct > 0 ? '+' : ''}
                    {player.predictions.change_30d_pct.toFixed(1)}%
                  </p>
                </div>
              </div>
              <div className="pt-2 border-t border-kb-border">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-kb-grey">Form Trajectory</span>
                  <span
                    className={
                      player.predictions.form_trajectory === 'improving'
                        ? 'text-success-400'
                        : player.predictions.form_trajectory === 'declining'
                        ? 'text-danger-400'
                        : 'text-warning-400'
                    }
                  >
                    {player.predictions.form_trajectory}
                  </span>
                </div>
                <div className="flex items-center justify-between text-sm mt-2">
                  <span className="text-kb-grey">Confidence</span>
                  <span className="text-kb-grey-light">
                    {(player.predictions.confidence * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
            </div>
          ) : (
            <p className="text-kb-grey text-center py-4">No prediction data available</p>
          )}
        </div>

        {/* Schedule Summary */}
        <div className="card">
          <h2 className="text-lg font-semibold text-kb-white mb-4 flex items-center">
            <Calendar className="w-5 h-5 mr-2" />
            Schedule Summary
          </h2>
          {player.schedule?.upcoming?.length > 0 ? (
            <div className="space-y-3">
              {player.schedule.upcoming.slice(0, 5).map((match: any, index: number) => (
                <div
                  key={index}
                  className="flex items-center justify-between p-3 bg-kb-dark rounded-lg"
                >
                  <div className="flex items-center space-x-3">
                    {match.is_home ? (
                      <Home className="w-4 h-4 text-success-400" />
                    ) : (
                      <Plane className="w-4 h-4 text-kb-grey" />
                    )}
                    <div>
                      <p className="text-kb-white font-medium">
                        {match.matchday ? `MD${match.matchday}: ` : ''}
                        {match.is_home ? 'vs' : '@'} {match.opponent}
                      </p>
                      <p className="text-xs text-kb-grey">
                        {match.opponent_rank ? `#${match.opponent_rank}` : ''}
                        {match.opponent_points !== null ? ` (${match.opponent_points} pts)` : ''}
                        {match.expected_points ? ` - Exp: ${match.expected_points} pts` : ''}
                      </p>
                    </div>
                  </div>
                  <DifficultyDots difficulty={match.difficulty} />
                </div>
              ))}
              <div className="pt-2 border-t border-kb-border">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-kb-grey">Overall Difficulty</span>
                  <span
                    className={
                      player.schedule.difficulty_rating === 'Easy'
                        ? 'text-success-400'
                        : player.schedule.difficulty_rating === 'Difficult'
                        ? 'text-danger-400'
                        : 'text-warning-400'
                    }
                  >
                    {player.schedule.difficulty_rating}
                  </span>
                </div>
                <div className="flex items-center justify-between text-sm mt-2">
                  <span className="text-kb-grey">Avg Opponent Rank</span>
                  <span className="text-kb-grey-light">
                    #{player.schedule.avg_opponent_rank.toFixed(1)}
                  </span>
                </div>
                <div className="flex items-center justify-between text-sm mt-2">
                  <span className="text-kb-grey">Remaining Matches</span>
                  <span className="text-kb-grey-light">
                    {player.schedule.upcoming.length}
                  </span>
                </div>
              </div>
            </div>
          ) : (
            <p className="text-kb-grey text-center py-4">No schedule data available</p>
          )}
        </div>

        {/* Risk Analysis */}
        <div className="card">
          <h2 className="text-lg font-semibold text-kb-white mb-4 flex items-center">
            <AlertTriangle className="w-5 h-5 mr-2" />
            Risk Analysis
          </h2>
          {player.risk_metrics ? (
            <div className="space-y-4">
              <div className="flex items-center justify-between p-3 bg-kb-dark rounded-lg">
                <span className="text-kb-grey-light">Risk Category</span>
                <span
                  className={`font-medium ${
                    player.risk_metrics.risk_category === 'Low Risk'
                      ? 'text-success-400'
                      : player.risk_metrics.risk_category === 'Medium Risk'
                      ? 'text-warning-400'
                      : 'text-danger-400'
                  }`}
                >
                  {player.risk_metrics.risk_category}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="p-3 bg-kb-dark rounded-lg">
                  <p className="text-xs text-kb-grey">Price Volatility</p>
                  <p className="text-kb-white font-medium">
                    {player.risk_metrics.price_volatility.toFixed(1)}%
                  </p>
                </div>
                <div className="p-3 bg-kb-dark rounded-lg">
                  <p className="text-xs text-kb-grey">Sharpe Ratio</p>
                  <p
                    className={`font-medium ${
                      player.risk_metrics.sharpe_ratio >= 1
                        ? 'text-success-400'
                        : player.risk_metrics.sharpe_ratio >= 0.5
                        ? 'text-warning-400'
                        : 'text-danger-400'
                    }`}
                  >
                    {player.risk_metrics.sharpe_ratio.toFixed(2)}
                  </p>
                </div>
                <div className="p-3 bg-kb-dark rounded-lg">
                  <p className="text-xs text-kb-grey">7d VaR (95%)</p>
                  <p className="text-danger-400 font-medium">
                    {player.risk_metrics.var_7d_pct.toFixed(1)}%
                  </p>
                </div>
                <div className="p-3 bg-kb-dark rounded-lg">
                  <p className="text-xs text-kb-grey">30d VaR (95%)</p>
                  <p className="text-danger-400 font-medium">
                    {player.risk_metrics.var_30d_pct.toFixed(1)}%
                  </p>
                </div>
              </div>
              <div className="pt-2 border-t border-kb-border">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-kb-grey">Expected Return (30d)</span>
                  <span
                    className={
                      player.risk_metrics.expected_return_30d > 0
                        ? 'text-success-400'
                        : player.risk_metrics.expected_return_30d < 0
                        ? 'text-danger-400'
                        : 'text-kb-grey'
                    }
                  >
                    {player.risk_metrics.expected_return_30d > 0 ? '+' : ''}
                    {player.risk_metrics.expected_return_30d.toFixed(1)}%
                  </span>
                </div>
                <div className="flex items-center justify-between text-sm mt-2">
                  <span className="text-kb-grey">Data Confidence</span>
                  <span className="text-kb-grey-light">
                    {(player.risk_metrics.confidence * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
            </div>
          ) : (
            <p className="text-kb-grey text-center py-4">No risk data available</p>
          )}
        </div>
      </div>

      {/* Full Schedule with Match Analysis */}
      {player.schedule?.upcoming?.length > 0 && (
        <div className="card">
          <h2 className="text-lg font-semibold text-kb-white mb-4 flex items-center">
            <Calendar className="w-5 h-5 mr-2" />
            Upcoming Matches ({player.schedule.upcoming.length})
          </h2>
          {player.schedule.upcoming.length <= 3 && (
            <p className="text-xs text-kb-grey mb-3">
              Note: Kickbase API only provides a limited match summary
            </p>
          )}
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="table-header">
                <tr>
                  <th className="px-4 py-3 text-left text-sm font-medium text-kb-grey-light">MD</th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-kb-grey-light">Opponent</th>
                  <th className="px-4 py-3 text-center text-sm font-medium text-kb-grey-light">H/A</th>
                  <th className="px-4 py-3 text-center text-sm font-medium text-kb-grey-light">Rank</th>
                  <th className="px-4 py-3 text-center text-sm font-medium text-kb-grey-light">Record</th>
                  <th className="px-4 py-3 text-center text-sm font-medium text-kb-grey-light">Diff</th>
                  <th className="px-4 py-3 text-right text-sm font-medium text-kb-grey-light">Exp. Pts</th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-kb-grey-light">Analysis</th>
                </tr>
              </thead>
              <tbody>
                {player.schedule.upcoming.map((match: any, index: number) => (
                  <tr key={index} className="table-row">
                    <td className="px-4 py-3 text-kb-grey-light">
                      {match.matchday || '-'}
                    </td>
                    <td className="px-4 py-3">
                      <span className="font-medium text-kb-white">{match.opponent}</span>
                    </td>
                    <td className="px-4 py-3 text-center">
                      {match.is_home ? (
                        <span className="text-success-400 font-medium">H</span>
                      ) : (
                        <span className="text-kb-grey">A</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-center">
                      {match.opponent_rank ? (
                        <span
                          className={
                            match.opponent_rank <= 5
                              ? 'text-danger-400 font-medium'
                              : match.opponent_rank >= 14
                              ? 'text-success-400'
                              : 'text-kb-grey-light'
                          }
                        >
                          #{match.opponent_rank}
                        </span>
                      ) : (
                        '-'
                      )}
                    </td>
                    <td className="px-4 py-3 text-center text-sm text-kb-grey">
                      {match.opponent_wins !== null
                        ? `${match.opponent_wins}W-${match.opponent_draws}D-${match.opponent_losses}L`
                        : '-'}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <DifficultyDots difficulty={match.difficulty} />
                    </td>
                    <td className="px-4 py-3 text-right">
                      {match.expected_points ? (
                        <span
                          className={
                            match.expected_points >= player.average_points * 1.1
                              ? 'text-success-400 font-medium'
                              : match.expected_points <= player.average_points * 0.9
                              ? 'text-danger-400'
                              : 'text-kb-grey-light'
                          }
                        >
                          {match.expected_points}
                        </span>
                      ) : (
                        '-'
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm text-kb-grey max-w-xs truncate" title={match.analysis}>
                      {match.analysis || '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Smart Bid Section - Only show if on market */}
      {player.is_on_market && (
        <div className="card">
          <h2 className="text-lg font-semibold text-kb-white mb-4">Smart Bid Recommendation</h2>
          {bidLoading ? (
            <div className="flex items-center space-x-2 text-kb-grey py-4">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>Calculating optimal bid...</span>
            </div>
          ) : suggestedBid ? (
            <div className="space-y-4">
              {suggestedBid.suggested_bid === 0 ? (
                <div className="bg-danger-400/10 border border-danger-400/20 rounded-lg p-4">
                  <p className="text-danger-400 font-medium mb-2">Not Recommended</p>
                  <p className="text-sm text-kb-grey-light">
                    Current price ({formatCurrency(suggestedBid.current_price)}) exceeds the maximum
                    profitable bid ({formatCurrency(suggestedBid.max_bid)}). You would likely overpay
                    for this player.
                  </p>
                  {suggestedBid.reasoning && (
                    <p className="text-xs text-kb-grey mt-2">{suggestedBid.reasoning}</p>
                  )}
                </div>
              ) : (
                <>
                  <div className="grid grid-cols-3 gap-4">
                    <div className="p-4 bg-kb-dark rounded-lg text-center">
                      <p className="text-sm text-kb-grey mb-1">Min Bid</p>
                      <p className="text-lg font-medium text-kb-white">
                        {formatCurrency(suggestedBid.min_bid)}
                      </p>
                    </div>
                    <div className="p-4 bg-success-500/10 border border-success-500/20 rounded-lg text-center">
                      <p className="text-sm text-success-400 mb-1">Suggested</p>
                      <p className="text-lg font-semibold text-success-400">
                        {formatCurrency(suggestedBid.suggested_bid)}
                      </p>
                    </div>
                    <div className="p-4 bg-kb-dark rounded-lg text-center">
                      <p className="text-sm text-kb-grey mb-1">Max Profitable</p>
                      <p className="text-lg font-medium text-kb-white">
                        {formatCurrency(suggestedBid.max_bid)}
                      </p>
                    </div>
                  </div>
                  {suggestedBid.reasoning && (
                    <p className="text-sm text-kb-grey">{suggestedBid.reasoning}</p>
                  )}
                  <div className="flex items-center space-x-3 pt-2">
                    <input
                      type="number"
                      className="input w-40"
                      placeholder="Bid amount"
                      value={bidAmount ?? suggestedBid.suggested_bid}
                      onChange={(e) => setBidAmount(Number(e.target.value))}
                      min={suggestedBid.min_bid}
                    />
                    <button
                      className="btn-primary"
                      onClick={() => {
                        alert(
                          `Bid of ${formatCurrency(bidAmount ?? suggestedBid.suggested_bid)} would be placed (not implemented)`
                        )
                      }}
                    >
                      Place Bid
                    </button>
                    <button
                      className="btn-secondary"
                      onClick={() => setBidAmount(suggestedBid.suggested_bid)}
                    >
                      Use Suggested
                    </button>
                  </div>
                </>
              )}
            </div>
          ) : (
            <p className="text-kb-grey">Unable to calculate bid recommendation</p>
          )}
        </div>
      )}
    </div>
  )
}
