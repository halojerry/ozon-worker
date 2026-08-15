import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth, clearToken, getToken } from '../stores/auth'

interface NavItem {
  to: string
  label: string
  /** 计划 §1.4 页面清单：T10-T13 逐页填充 */
  icon: React.ReactNode
}

const NAV_ITEMS: NavItem[] = [
  {
    to: '/collect-box',
    label: '采集箱',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M4 7h16M4 7l1.5 13h13L20 7M9 7V5a2 2 0 012-2h2a2 2 0 012 2v2" />
      </svg>
    ),
  },
  {
    to: '/products',
    label: '商品编辑',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M6 3h12a1 1 0 011 1v17l-7-4-7 4V4a1 1 0 011-1z" />
      </svg>
    ),
  },
  {
    to: '/stores',
    label: '店铺管理',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M4 9.5V20h16V9.5M3 5h18l-1.5 4.5a3.2 3.2 0 01-6.3 0 3.2 3.2 0 01-6.4 0L3 5z" />
      </svg>
    ),
  },
  {
    to: '/tasks',
    label: '任务进度',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <rect x="3.5" y="4" width="17" height="16" rx="2.5" />
        <path d="M8 9h8M8 13h5M8 17h3" />
      </svg>
    ),
  },
  {
    to: '/on-sale',
    label: '在售货架',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M4 5.5h16M4 5.5L2.8 9.2a2.3 2.3 0 004.5.6 2.3 2.3 0 004.5 0 2.3 2.3 0 004.5-.6L20 5.5M5 10.5V19h14v-8.5M9 19v-5h6v5" />
      </svg>
    ),
  },
  {
    to: '/image-studio',
    label: '生图工作台',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <rect x="3.5" y="3.5" width="17" height="17" rx="2.5" />
        <circle cx="9" cy="9" r="1.8" />
        <path d="M4.5 18.5l5-5 3.5 3.5 3-3 3.5 3.5" />
      </svg>
    ),
  },
]

export default function Layout() {
  const authed = useAuth()
  const navigate = useNavigate()

  function handleLogout() {
    clearToken()
    navigate('/login', { replace: true })
  }

  const maskedToken = authed && getToken() ? `${getToken()!.slice(0, 6)}…${getToken()!.slice(-4)}` : ''

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="sidebar-logo" aria-hidden="true">
            <svg viewBox="0 0 32 32" width="26" height="26">
              <rect width="32" height="32" rx="7" fill="#005bff" />
              <path
                d="M9 10.5h9.2c2.6 0 4.3 1.5 4.3 3.7 0 1.6-1 2.9-2.5 3.4v.1c1.9.4 3.2 1.8 3.2 3.8 0 2.5-1.9 4.1-4.7 4.1H9V10.5zm3.7 6.4h4.9c1.3 0 2.1-.7 2.1-1.8s-.8-1.8-2.1-1.8h-4.9v3.6zm0 6.4h5.2c1.4 0 2.3-.7 2.3-1.9 0-1.2-.9-1.9-2.3-1.9h-5.2v3.8z"
                fill="#fff"
              />
            </svg>
          </div>
          <div className="sidebar-title">
            <span className="sidebar-name">Ozon 上架助手</span>
            <span className="sidebar-sub">WebUI</span>
          </div>
        </div>

        <nav className="sidebar-nav" aria-label="主导航">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
            >
              <span className="nav-icon">{item.icon}</span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-user">
            <div className="sidebar-avatar" aria-hidden="true">
              {maskedToken ? maskedToken[0] : '?'}
            </div>
            <span className="sidebar-token" title={maskedToken}>
              {maskedToken || '未登录'}
            </span>
          </div>
          <button className="sidebar-logout" onClick={handleLogout}>
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M15 4h4a1 1 0 011 1v14a1 1 0 01-1 1h-4M10 8l-4 4 4 4M6 12h10" />
            </svg>
            退出登录
          </button>
        </div>
      </aside>

      <main className="app-main">
        <Outlet />
      </main>
    </div>
  )
}
