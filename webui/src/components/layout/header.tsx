import { LogOut } from 'lucide-react'
import { useNavigate } from '@tanstack/react-router'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/stores/auth'
import { useSessionStore } from '@/stores/session'

/**
 * Header —— PRD §4.2 布局壳顶栏
 * 左：当前路由面包屑/页名；右：用户名 + 退出登录。
 */
export interface HeaderProps {
  title: string
  /** 二级标题（可选，如店铺名） */
  subtitle?: string
}

export function Header({ title, subtitle }: HeaderProps) {
  const navigate = useNavigate()
  const { token, user, logout } = useAuthStore()
  const { reset } = useSessionStore()

  function handleLogout() {
    logout()
    reset()
    void navigate({ to: '/login' })
  }

  const displayName = user?.username ?? token?.slice(0, 8).toUpperCase() ?? '未登录'

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center justify-between gap-4 border-b border-line bg-surface/90 px-page-pad backdrop-blur-sm">
      <div className="flex min-w-0 items-baseline gap-2">
        <h2 className="truncate text-h3 text-ink">{title}</h2>
        {subtitle && <span className="hidden truncate text-[12px] text-ink-aux md:inline">{subtitle}</span>}
      </div>

      <div className="flex shrink-0 items-center gap-3">
        <span className="hidden text-[12px] text-ink-aux sm:inline">{displayName}</span>
        <Button
          variant="ghost"
          className="h-8 gap-1.5 px-2.5 text-[12px]"
          onClick={handleLogout}
          aria-label="退出登录"
        >
          <LogOut className="size-3.5" />
          <span className="hidden sm:inline">退出</span>
        </Button>
      </div>
    </header>
  )
}
