import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useAuth, clearToken, getToken } from '../stores/auth'
import { useSession } from '../stores/session'
import { getDrafts, listProducts, listTasks } from '../api/client'
import KeyManager from './KeyManager'

interface NavItem {
  to: string
  label: string
  icon: React.ReactNode
  /** always=始终显示；products=在售货架有数据（listProducts total>0）才显示 */
  show: 'always' | 'products'
}

/** M2.3 条件侧边栏：首页置顶 + 按工作流排序（采集箱 → 任务进度 → 在售货架 → 生图 → 店铺） */
const NAV_ITEMS: NavItem[] = [
  {
    to: '/',
    label: '工作台',
    show: 'always',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M4 10.5L12 4l8 6.5V20h-6v-6h-4v6H4v-9.5z" />
      </svg>
    ),
  },
  {
    to: '/collect-box',
    label: '采集箱',
    show: 'always',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M4 7h16M4 7l1.5 13h13L20 7M9 7V5a2 2 0 012-2h2a2 2 0 012 2v2" />
      </svg>
    ),
  },
  {
    to: '/tasks',
    label: '任务进度',
    show: 'always',
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
    show: 'products',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M4 5.5h16M4 5.5L2.8 9.2a2.3 2.3 0 004.5.6 2.3 2.3 0 004.5 0 2.3 2.3 0 004.5-.6L20 5.5M5 10.5V19h14v-8.5M9 19v-5h6v5" />
      </svg>
    ),
  },
  {
    to: '/orders',
    label: '订单管理',
    show: 'always',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M4 4h16v4H4zM4 11h16v9H4zM9 4v4M15 4v4" />
      </svg>
    ),
  },
  {
    to: '/image-studio',
    label: '生图工作台',
    show: 'always',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <rect x="3.5" y="3.5" width="17" height="17" rx="2.5" />
        <circle cx="9" cy="9" r="1.8" />
        <path d="M4.5 18.5l5-5 3.5 3.5 3-3 3.5 3.5" />
      </svg>
    ),
  },
  {
    to: '/stores',
    label: '店铺管理',
    show: 'always',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M4 9.5V20h16V9.5M3 5h18l-1.5 4.5a3.2 3.2 0 01-6.3 0 3.2 3.2 0 01-6.4 0L3 5z" />
      </svg>
    ),
  },
  {
    to: '/templates',
    label: '上架配置',
    show: 'always',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M9 3h6v6l5 5-6 6-5-5H3V9l6-6zM14.5 6.5h.01M10 17l-4 4" />
      </svg>
    ),
  },
]

interface NavCounts {
  /** null = 加载中/未知（未知时导航项全部显示，避免闪烁） */
  drafts: number | null
  tasks: number | null
  products: number | null
}

function formatBalance(balance: number | null | undefined): string {
  if (balance == null || !Number.isFinite(balance)) return '—'
  return balance.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function Layout() {
  const authed = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const session = useSession()
  const [counts, setCounts] = useState<NavCounts>({ drafts: null, tasks: null, products: null })
  const [showKeys, setShowKeys] = useState(false)

  /* M2.3：挂载时并行拉取 drafts/tasks/products 计数（limit=1 只取 total），
     路由变化时静默重拉（上架/采集后导航项自动出现/消失）；失败保持未知 → 显示 */
  useEffect(() => {
    let alive = true
    Promise.allSettled([getDrafts(), listTasks({ limit: 1 }), listProducts({ limit: 1 })]).then(
      ([draftRes, taskRes, productRes]) => {
        if (!alive) return
        setCounts({
          drafts: draftRes.status === 'fulfilled' ? draftRes.value.length : null,
          tasks: taskRes.status === 'fulfilled' ? taskRes.value.total : null,
          products: productRes.status === 'fulfilled' ? productRes.value.total : null,
        })
      },
    )
    return () => {
      alive = false
    }
  }, [location.pathname])

  /** 在售货架：加载中/失败（null）时显示全部；确认 total=0 才隐藏 */
  const visibleItems = NAV_ITEMS.filter(
    (item) => item.show === 'always' || counts.products === null || counts.products > 0,
  )

  function handleLogout() {
    clearToken()
    navigate('/login', { replace: true })
  }

  const maskedToken = authed && getToken() ? `${getToken()!.slice(0, 6)}…${getToken()!.slice(-4)}` : ''
  const displayName = session?.username || maskedToken || '未登录'

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
          {visibleItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
            >
              <span className="nav-icon">{item.icon}</span>
              <span>{item.label}</span>
              {item.to === '/collect-box' && counts.drafts !== null && counts.drafts > 0 && (
                <span className="nav-count" title={`${counts.drafts} 个草稿`}>
                  {counts.drafts}
                </span>
              )}
              {item.to === '/tasks' && counts.tasks !== null && counts.tasks > 0 && (
                <span className="nav-count" title={`${counts.tasks} 个任务`}>
                  {counts.tasks}
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-user">
            <div className="sidebar-avatar" aria-hidden="true">
              {displayName ? displayName[0].toUpperCase() : '?'}
            </div>
            <div className="sidebar-user-info">
              <span className="sidebar-username" title={displayName}>
                {displayName}
              </span>
              <button type="button" className="sidebar-balance" onClick={() => setShowKeys(true)} title="密钥管理">
                <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <circle cx="8" cy="14" r="4" />
                  <path d="M11 11L19.5 2.5M16 7l3 3" strokeLinecap="round" />
                </svg>
                余额 ¥{formatBalance(session?.balance)}
              </button>
            </div>
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

      {showKeys && <KeyManager onClose={() => setShowKeys(false)} />}
    </div>
  )
}
