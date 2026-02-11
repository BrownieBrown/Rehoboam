import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from './stores/authStore'
import Layout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Market from './pages/Market'
import Portfolio from './pages/Portfolio'
import Analytics from './pages/Analytics'
import Account from './pages/Account'
import Player from './pages/Player'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, hasHydrated } = useAuthStore()

  // Wait for zustand to hydrate from localStorage
  if (!hasHydrated) {
    return (
      <div className="min-h-screen bg-kb-black flex items-center justify-center">
        <div className="text-kb-grey">Loading...</div>
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="market" element={<Market />} />
        <Route path="portfolio" element={<Portfolio />} />
        <Route path="analytics" element={<Analytics />} />
        <Route path="account" element={<Account />} />
        <Route path="player/:id" element={<Player />} />
      </Route>
    </Routes>
  )
}

export default App
