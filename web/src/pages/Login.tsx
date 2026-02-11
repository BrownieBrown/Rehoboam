import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { Loader2, Zap } from 'lucide-react'
import { authApi } from '../api/client'
import { useAuthStore } from '../stores/authStore'

export default function Login() {
  const navigate = useNavigate()
  const setAuth = useAuthStore((state) => state.setAuth)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  const loginMutation = useMutation({
    mutationFn: () => authApi.login(email, password),
    onSuccess: (data) => {
      setAuth(data.access_token, data.user)
      navigate('/', { replace: true })
    },
    onError: (err: any) => {
      console.error('Login error:', err)
      const message = err?.response?.data?.detail || err?.message || 'Login failed'
      setError(message)
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    loginMutation.mutate()
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-kb-black px-4 relative overflow-hidden">
      {/* Background glow effects */}
      <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-kb-red/10 rounded-full blur-3xl" />
      <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-kb-purple/10 rounded-full blur-3xl" />

      <div className="w-full max-w-md relative z-10">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="w-20 h-20 bg-kb-card border border-kb-border rounded-2xl flex items-center justify-center mx-auto mb-4 shadow-glow">
            <Zap className="w-10 h-10 text-kb-red" />
          </div>
          <h1 className="text-4xl font-bold text-kb-white">Rehoboam</h1>
          <p className="text-kb-grey mt-2">KICKBASE Trading Intelligence</p>
        </div>

        {/* Login form */}
        <div className="card border-kb-border">
          <h2 className="text-xl font-semibold text-kb-white mb-6">Sign in to continue</h2>

          {error && (
            <div className="mb-4 p-3 bg-danger-500/10 border border-danger-500/20 text-danger-400 rounded-lg text-sm">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label htmlFor="email" className="block text-sm font-medium text-kb-grey-light mb-2">
                Email
              </label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="input"
                placeholder="your@email.com"
                required
              />
            </div>

            <div>
              <label htmlFor="password" className="block text-sm font-medium text-kb-grey-light mb-2">
                Password
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="input"
                placeholder="Your KICKBASE password"
                required
              />
            </div>

            <button
              type="submit"
              disabled={loginMutation.isPending}
              className="w-full btn-primary py-3 flex items-center justify-center text-base"
            >
              {loginMutation.isPending ? (
                <>
                  <Loader2 className="w-5 h-5 animate-spin mr-2" />
                  Connecting...
                </>
              ) : (
                'Sign in with KICKBASE'
              )}
            </button>
          </form>

          <p className="mt-6 text-center text-sm text-kb-grey">
            Use your KICKBASE credentials
          </p>
        </div>
      </div>
    </div>
  )
}
