import { useState, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Save, RotateCcw, Loader2, Check } from 'lucide-react'
import { settingsApi } from '../api/client'

export default function Settings() {
  const queryClient = useQueryClient()
  const [saved, setSaved] = useState(false)

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: () => settingsApi.get(),
  })

  const [formData, setFormData] = useState({
    min_sell_profit_pct: 5.0,
    max_loss_pct: -2.0,
    min_value_score_to_buy: 50.0,
    max_player_cost: 5000000,
    reserve_budget: 1000000,
    dry_run: true,
  })

  useEffect(() => {
    if (settings) {
      setFormData(settings)
    }
  }, [settings])

  const updateMutation = useMutation({
    mutationFn: (data: typeof formData) => settingsApi.update(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    },
  })

  const resetMutation = useMutation({
    mutationFn: () => settingsApi.reset(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    updateMutation.mutate(formData)
  }

  const handleReset = () => {
    if (confirm('Reset all settings to defaults?')) {
      resetMutation.mutate()
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        </div>
        <div className="text-center py-8 text-gray-500">Loading settings...</div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="text-gray-500 mt-1">Configure trading parameters</p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Trading thresholds */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Trading Thresholds</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Min Sell Profit (%)
              </label>
              <input
                type="number"
                step="0.1"
                value={formData.min_sell_profit_pct}
                onChange={(e) =>
                  setFormData({ ...formData, min_sell_profit_pct: parseFloat(e.target.value) })
                }
                className="input"
              />
              <p className="text-xs text-gray-500 mt-1">
                Sell when profit exceeds this percentage
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Max Loss (%)
              </label>
              <input
                type="number"
                step="0.1"
                value={formData.max_loss_pct}
                onChange={(e) =>
                  setFormData({ ...formData, max_loss_pct: parseFloat(e.target.value) })
                }
                className="input"
              />
              <p className="text-xs text-gray-500 mt-1">
                Stop-loss trigger (negative value)
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Min Value Score to Buy
              </label>
              <input
                type="number"
                step="1"
                min="50"
                max="100"
                value={formData.min_value_score_to_buy}
                onChange={(e) =>
                  setFormData({
                    ...formData,
                    min_value_score_to_buy: Math.max(50, parseFloat(e.target.value)),
                  })
                }
                className="input"
              />
              <p className="text-xs text-gray-500 mt-1">Minimum score for buy recommendations (50-100)</p>
            </div>
          </div>
        </div>

        {/* Budget settings */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Budget Settings</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Max Player Cost
              </label>
              <input
                type="number"
                step="100000"
                value={formData.max_player_cost}
                onChange={(e) =>
                  setFormData({ ...formData, max_player_cost: parseInt(e.target.value) })
                }
                className="input"
              />
              <p className="text-xs text-gray-500 mt-1">Maximum spend on a single player</p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Reserve Budget
              </label>
              <input
                type="number"
                step="100000"
                value={formData.reserve_budget}
                onChange={(e) =>
                  setFormData({ ...formData, reserve_budget: parseInt(e.target.value) })
                }
                className="input"
              />
              <p className="text-xs text-gray-500 mt-1">Always keep this amount in reserve</p>
            </div>
          </div>
        </div>

        {/* Safety settings */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Safety</h2>
          <div className="flex items-center">
            <input
              type="checkbox"
              id="dry_run"
              checked={formData.dry_run}
              onChange={(e) => setFormData({ ...formData, dry_run: e.target.checked })}
              className="w-4 h-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
            />
            <label htmlFor="dry_run" className="ml-2 text-sm text-gray-700">
              Dry Run Mode
            </label>
          </div>
          <p className="text-xs text-gray-500 mt-1">
            When enabled, trades are simulated but not executed
          </p>
        </div>

        {/* Actions */}
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={handleReset}
            disabled={resetMutation.isPending}
            className="btn-secondary flex items-center"
          >
            {resetMutation.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin mr-2" />
            ) : (
              <RotateCcw className="w-4 h-4 mr-2" />
            )}
            Reset to Defaults
          </button>

          <button
            type="submit"
            disabled={updateMutation.isPending}
            className="btn-primary flex items-center"
          >
            {updateMutation.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin mr-2" />
            ) : saved ? (
              <Check className="w-4 h-4 mr-2" />
            ) : (
              <Save className="w-4 h-4 mr-2" />
            )}
            {saved ? 'Saved!' : 'Save Settings'}
          </button>
        </div>
      </form>
    </div>
  )
}
