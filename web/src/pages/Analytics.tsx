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

const COLORS = ['#0ea5e9', '#22c55e', '#f59e0b', '#ef4444']

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
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Analytics</h1>
        <p className="text-gray-500 mt-1">In-depth analysis and insights</p>
      </div>

      {isLoading ? (
        <div className="text-center py-8 text-gray-500">Loading analytics...</div>
      ) : (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div className="card">
              <div className="flex items-center">
                <div className="p-3 bg-success-50 rounded-xl">
                  <TrendingUp className="w-6 h-6 text-success-600" />
                </div>
                <div className="ml-4">
                  <p className="text-sm text-gray-500">Buy Opportunities</p>
                  <p className="text-2xl font-bold text-gray-900">
                    {recommendations?.buy_recommendations?.length || 0}
                  </p>
                </div>
              </div>
            </div>

            <div className="card">
              <div className="flex items-center">
                <div className="p-3 bg-danger-50 rounded-xl">
                  <TrendingDown className="w-6 h-6 text-danger-600" />
                </div>
                <div className="ml-4">
                  <p className="text-sm text-gray-500">Sell Alerts</p>
                  <p className="text-2xl font-bold text-gray-900">
                    {recommendations?.sell_recommendations?.length || 0}
                  </p>
                </div>
              </div>
            </div>

            <div className="card">
              <div className="flex items-center">
                <div className="p-3 bg-warning-50 rounded-xl">
                  <AlertTriangle className="w-6 h-6 text-warning-600" />
                </div>
                <div className="ml-4">
                  <p className="text-sm text-gray-500">Roster Gaps</p>
                  <p className="text-2xl font-bold text-gray-900">
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
              <h2 className="text-lg font-semibold text-gray-900 mb-4 flex items-center">
                <Users className="w-5 h-5 mr-2 text-gray-400" />
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
                    >
                      {positionData.map((_, index) => (
                        <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Roster gaps */}
            <div className="card">
              <h2 className="text-lg font-semibold text-gray-900 mb-4">
                Roster vs Minimum Requirements
              </h2>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={rosterData} layout="vertical">
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis type="number" />
                    <YAxis type="category" dataKey="position" width={80} />
                    <Tooltip />
                    <Bar dataKey="current" fill="#0ea5e9" name="Current" />
                    <Bar dataKey="minimum" fill="#e5e7eb" name="Minimum" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          {/* Recommendations tables */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Buy recommendations */}
            <div className="card">
              <h2 className="text-lg font-semibold text-gray-900 mb-4 flex items-center">
                <TrendingUp className="w-5 h-5 mr-2 text-success-600" />
                Top Buy Recommendations
              </h2>
              <div className="space-y-3">
                {recommendations?.buy_recommendations?.slice(0, 5).map((player: any) => (
                  <div
                    key={player.player_id}
                    className="flex items-center justify-between p-3 bg-gray-50 rounded-lg"
                  >
                    <div>
                      <p className="font-medium">{player.player_name}</p>
                      <p className="text-sm text-gray-500">
                        {player.position} - {player.team_name}
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="font-semibold text-success-600">
                        {formatCurrency(player.price)}
                      </p>
                      <p className="text-sm text-gray-500">
                        Score: {player.value_score.toFixed(0)}
                      </p>
                    </div>
                  </div>
                ))}
                {(!recommendations?.buy_recommendations ||
                  recommendations.buy_recommendations.length === 0) && (
                  <p className="text-gray-500 text-center py-4">No recommendations</p>
                )}
              </div>
            </div>

            {/* Sell recommendations */}
            <div className="card">
              <h2 className="text-lg font-semibold text-gray-900 mb-4 flex items-center">
                <TrendingDown className="w-5 h-5 mr-2 text-danger-600" />
                Sell Recommendations
              </h2>
              <div className="space-y-3">
                {recommendations?.sell_recommendations?.map((player: any) => (
                  <div
                    key={player.player_id}
                    className="flex items-center justify-between p-3 bg-gray-50 rounded-lg"
                  >
                    <div>
                      <p className="font-medium">{player.player_name}</p>
                      <p className="text-sm text-gray-500">
                        {player.position} - {player.team_name}
                      </p>
                    </div>
                    <div className="text-right">
                      <p
                        className={`font-semibold ${
                          (player.profit_loss_pct || 0) >= 0
                            ? 'text-success-600'
                            : 'text-danger-600'
                        }`}
                      >
                        {(player.profit_loss_pct || 0).toFixed(1)}%
                      </p>
                      <p className="text-sm text-gray-500">{player.reason}</p>
                    </div>
                  </div>
                ))}
                {(!recommendations?.sell_recommendations ||
                  recommendations.sell_recommendations.length === 0) && (
                  <p className="text-gray-500 text-center py-4">No sell alerts</p>
                )}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
