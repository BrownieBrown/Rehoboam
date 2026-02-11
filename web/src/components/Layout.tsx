import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard,
  ShoppingCart,
  Briefcase,
  BarChart3,
  User,
  LogOut,
  Menu,
  X,
  Zap,
} from 'lucide-react'
import { useAuthStore } from '../stores/authStore'
import { useUIStore } from '../stores/uiStore'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/market', icon: ShoppingCart, label: 'Market' },
  { to: '/portfolio', icon: Briefcase, label: 'Portfolio' },
  { to: '/analytics', icon: BarChart3, label: 'Analytics' },
  { to: '/account', icon: User, label: 'Account' },
]

export default function Layout() {
  const navigate = useNavigate()
  const { user, logout } = useAuthStore()
  const { sidebarOpen, toggleSidebar } = useUIStore()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  return (
    <div className="min-h-screen bg-kb-black">
      {/* Mobile header */}
      <div className="lg:hidden fixed top-0 left-0 right-0 h-16 bg-kb-dark border-b border-kb-border flex items-center justify-between px-4 z-50">
        <button onClick={toggleSidebar} className="p-2 hover:bg-kb-card rounded-lg text-kb-grey-light">
          {sidebarOpen ? <X size={24} /> : <Menu size={24} />}
        </button>
        <div className="flex items-center space-x-2">
          <Zap className="w-5 h-5 text-kb-red" />
          <span className="font-semibold text-kb-white">Rehoboam</span>
        </div>
        <div className="w-10" />
      </div>

      {/* Sidebar */}
      <aside
        className={`fixed top-0 left-0 h-full w-64 bg-kb-dark border-r border-kb-border transform transition-transform duration-200 ease-in-out z-40 ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        } lg:translate-x-0`}
      >
        <div className="flex flex-col h-full">
          {/* Logo */}
          <div className="h-16 flex items-center px-6 border-b border-kb-border">
            <div className="w-9 h-9 bg-kb-red rounded-lg flex items-center justify-center shadow-glow-sm">
              <Zap className="w-5 h-5 text-white" />
            </div>
            <span className="ml-3 font-bold text-xl text-kb-white">Rehoboam</span>
          </div>

          {/* Navigation */}
          <nav className="flex-1 px-3 py-6 space-y-1">
            {navItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                className={({ isActive }) =>
                  `flex items-center px-4 py-3 rounded-lg transition-all duration-200 ${
                    isActive
                      ? 'bg-kb-red/10 text-kb-red border border-kb-red/20'
                      : 'text-kb-grey-light hover:bg-kb-card hover:text-kb-white'
                  }`
                }
              >
                <item.icon size={20} />
                <span className="ml-3 font-medium">{item.label}</span>
              </NavLink>
            ))}
          </nav>

          {/* User info */}
          <div className="border-t border-kb-border p-4">
            <div className="flex items-center">
              <div className="w-10 h-10 bg-kb-card border border-kb-border rounded-full flex items-center justify-center">
                <span className="text-kb-red font-semibold">
                  {user?.team_name?.charAt(0) || 'U'}
                </span>
              </div>
              <div className="ml-3 flex-1 min-w-0">
                <p className="text-sm font-medium text-kb-white truncate">
                  {user?.team_name || 'My Team'}
                </p>
                <p className="text-xs text-kb-grey truncate">{user?.league_name}</p>
              </div>
              <button
                onClick={handleLogout}
                className="p-2 text-kb-grey hover:text-kb-red hover:bg-kb-card rounded-lg transition-colors"
                title="Logout"
              >
                <LogOut size={18} />
              </button>
            </div>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className={`transition-all duration-200 ${sidebarOpen ? 'lg:ml-64' : ''}`}>
        <div className="pt-16 lg:pt-0 min-h-screen">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
            <Outlet />
          </div>
        </div>
      </main>

      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/60 z-30 lg:hidden backdrop-blur-sm"
          onClick={toggleSidebar}
        />
      )}
    </div>
  )
}
