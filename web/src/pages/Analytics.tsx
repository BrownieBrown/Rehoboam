import { useRecommendations, useRosterImpact } from '../hooks/useAnalytics'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from 'recharts'
import { TrendingUp, TrendingDown, Users, AlertTriangle } from 'lucide-react'

const COLORS = ['#e11d48', '#9747FF', '#22c55e', '#f59e0b']

function formatCurrency(value: number): string {
  if (value >= 1000000) {
    return `${(value / 1000000).toFixed(1)}M`
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(0)}K`
  }
  return value.toString()
}

export default function Analytics() {
  const { data: recommendations, isLoading: recsLoading } = useRecommendations()
  const { data: rosterImpact, isLoading: rosterLoading } = useRosterImpact()

  const isLoading = recsLoading || rosterLoading

  // Prepare position distribution data
  const positionData = recommendations?.position_counts
    ? Object.entries(recommendations.position_counts).map(([name, value]) => ({
        name,
        value: value as number,
      }))
    : []

  // Prepare roster impact data
  const rosterData = rosterImpact
    ? Object.entries(rosterImpact).map(([position, data]: [string, any]) => ({
        position,
        current: data.current_count,
        minimum: data.minimum_count,
        gap: data.minimum_count - data.current_count,
      }))
    : []

  return (
    <div className="space-y-8 animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold text-kb-white">Analytics</h1>
        <p className="text-kb-grey mt-1">In-depth analysis and insights</p>
      </div>

      {isLoading ? (
        <div className="text-center py-8 text-kb-grey">Loading analytics...</div>
      ) : (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div className="card-hover">
              <div className="flex items-center">
                <div className="p-3 bg-success-500/10 rounded-xl">
                  <TrendingUp className="w-6 h-6 text-success-400" />
                </div>
                <div className="ml-4">
                  <p className="text-sm text-kb-grey">Buy Opportunities</p>
                  <p className="text-2xl font-bold text-kb-white">
                    {recommendations?.buy_recommendations?.length || 0}
                  </p>
                </div>
              </div>
            </div>

            <div className="card-hover">
              <div className="flex items-center">
                <div className="p-3 bg-danger-500/10 rounded-xl">
                  <TrendingDown className="w-6 h-6 text-danger-400" />
                </div>
                <div className="ml-4">
                  <p className="text-sm text-kb-grey">Sell Alerts</p>
                  <p className="text-2xl font-bold text-kb-white">
                    {recommendations?.sell_recommendations?.length || 0}
                  </p>
                </div>
              </div>
            </div>

            <div className="card-hover">
              <div className="flex items-center">
                <div className="p-3 bg-warning-500/10 rounded-xl">
                  <AlertTriangle className="w-6 h-6 text-warning-400" />
                </div>
                <div className="ml-4">
                  <p className="text-sm text-kb-grey">Roster Gaps</p>
                  <p className="text-2xl font-bold text-kb-white">
                    {recommendations?.roster_gaps?.length || 0}
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Charts row */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Position distribution */}
            <div className="card">
              <h2 className="text-lg font-semibold text-kb-white mb-4 flex items-center">
                <Users className="w-5 h-5 mr-2 text-kb-grey" />
                Squad Distribution
              </h2>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={positionData}
                      cx="50%"
                      cy="50%"
                      innerRadius={60}
                      outerRadius={80}
                      paddingAngle={5}
                      dataKey="value"
                      label={({ name, value }) => `${name}: ${value}`}
                      labelLine={{ stroke: '#6b7280' }}
                    >
                      {positionData.map((_, index) => (
                        <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{
                        backgroundColor: '#1a1a1a',
                        border: '1px solid #2a2a2a',
                        borderRadius: '8px',
                        color: '#f5f5f5',
                      }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Roster gaps */}
            <div className="card">
              <h2 className="text-lg font-semibold text-kb-white mb-4">
                Roster vs Minimum Requirements
              </h2>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={rosterData} layout="vertical">
                    <CartesianGrid strokeDasharray="3 3" stroke="#2a2a2a" />
                    <XAxis type="number" stroke="#6b7280" />
                    <YAxis type="category" dataKey="position" width={80} stroke="#6b7280" />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: '#1a1a1a',
                        border: '1px solid #2a2a2a',
                        borderRadius: '8px',
                        color: '#f5f5f5',
                      }}
                    />
                    <Bar dataKey="current" fill="#e11d48" name="Current" />
                    <Bar dataKey="minimum" fill="#3a3a3a" name="Minimum" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          {/* Recommendations tables */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Buy recommendations */}
            <div className="card">
              <h2 className="text-lg font-semibold text-kb-white mb-4 flex items-center">
                <TrendingUp className="w-5 h-5 mr-2 text-success-400" />
                Top Buy Recommendations
              </h2>
              <div className="space-y-3">
                {recommendations?.buy_recommendations?.slice(0, 5).map((player: any) => (
                  <div
                    key={player.player_id}
                    className="flex items-center justify-between p-3 bg-kb-dark rounded-lg border border-kb-border"
                  >
                    <div>
                      <p className="font-medium text-kb-white">{player.player_name}</p>
                      <p className="text-sm text-kb-grey">
                        {player.position} · {player.team_name}
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="font-semibold text-success-400">
                        {formatCurrency(player.price)}
                      </p>
                      <p className="text-sm text-kb-grey">
                        Score: {player.value_score.toFixed(0)}
                      </p>
                    </div>
                  </div>
                ))}
                {(!recommendations?.buy_recommendations ||
                  recommendations.buy_recommendations.length === 0) && (
                  <p className="text-kb-grey text-center py-4">No recommendations</p>
                )}
              </div>
            </div>

            {/* Sell recommendations */}
            <div className="card">
              <h2 className="text-lg font-semibold text-kb-white mb-4 flex items-center">
                <TrendingDown className="w-5 h-5 mr-2 text-danger-400" />
                Sell Recommendations
              </h2>
              <div className="space-y-3">
                {recommendations?.sell_recommendations?.map((player: any) => (
                  <div
                    key={player.player_id}
                    className="flex items-center justify-between p-3 bg-kb-dark rounded-lg border border-kb-border"
                  >
                    <div>
                      <p className="font-medium text-kb-white">{player.player_name}</p>
                      <p className="text-sm text-kb-grey">
                        {player.position} · {player.team_name}
                      </p>
                    </div>
                    <div className="text-right">
                      <p
                        className={`font-semibold ${
                          (player.profit_loss_pct || 0) >= 0
                            ? 'text-success-400'
                            : 'text-danger-400'
                        }`}
                      >
                        {(player.profit_loss_pct || 0).toFixed(1)}%
                      </p>
                      <p className="text-sm text-kb-grey">{player.reason}</p>
                    </div>
                  </div>
                ))}
                {(!recommendations?.sell_recommendations ||
                  recommendations.sell_recommendations.length === 0) && (
                  <p className="text-kb-grey text-center py-4">No sell alerts</p>
                )}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
