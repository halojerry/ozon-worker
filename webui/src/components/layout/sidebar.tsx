import { Link } from '@tanstack/react-router'
import { NAV_GROUPS } from '@/lib/constants'

/**
 * Sidebar —— spec §06 侧边栏实样 / PRD §4.2 布局壳
 * - 黑色底 #111 + 白字导航（bg.sidebar / text.onSidebar）
 * - 当前页：深色 hover 底 #1D1D1D + 左侧 2px 品牌红竖线
 * - 桌面 236px 常驻；平板 768-1199 收成 64px 图标栏；移动 <768 隐藏
 */
export function Sidebar() {
  return (
    <aside className="sticky top-0 hidden h-screen w-full flex-col overflow-y-auto bg-sidebar tablet:flex">
      {/* 品牌区 */}
      <div className="flex items-center gap-2.5 border-b border-border-sidebar px-5 py-[18px]">
        <span
          className="size-[26px] shrink-0 rounded-[6px] bg-accent shadow-glow"
          aria-hidden
        />
        <div className="min-w-0">
          <b className="block truncate text-[15px] font-bold tracking-[0.5px] text-on-dark">
            Ozon ERP
          </b>
          <p className="hidden truncate text-[11px] text-sidebar-muted desktop:block">
            AI 自动化运营系统
          </p>
        </div>
      </div>

      {/* 导航组 */}
      <nav className="flex-1 pb-6" aria-label="主导航">
        {NAV_GROUPS.map((group) => (
          <div key={group.label}>
            <div className="hidden px-5 pb-1.5 pt-[18px] text-[11px] font-semibold uppercase tracking-[1px] text-sidebar-group desktop:block">
              {group.label}
            </div>
            <ul>
              {group.items.map((item) => {
                const Icon = item.icon
                return (
                  <li key={item.to}>
                    <Link
                      to={item.to}
                      activeOptions={item.to === '/' ? { exact: true } : undefined}
                      className="flex items-center gap-2.5 border-l-2 px-5 py-[7px] text-[13px] border-transparent text-on-sidebar transition-colors duration-fast ease-standard hover:bg-sidebar-hover hover:text-on-dark focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-[-2px]"
                      activeProps={{
                        className: 'border-accent bg-sidebar-hover font-medium text-on-dark',
                      }}
                    >
                      <Icon className="size-[16px] shrink-0 text-accent" strokeWidth={1.75} />
                      <span className="hidden truncate desktop:block">{item.label}</span>
                    </Link>
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </nav>

      {/* 底部版权 */}
      <div className="hidden border-t border-border-sidebar px-5 py-3 text-[11px] text-sidebar-muted desktop:block">
        © {new Date().getFullYear()} Ozon AI ERP
      </div>
    </aside>
  )
}
