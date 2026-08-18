import { createFileRoute, Outlet, redirect, useMatches } from '@tanstack/react-router'
import { Sidebar } from '@/components/layout/sidebar'
import { Header } from '@/components/layout/header'
import { useAuthStore } from '@/stores/auth'

/**
 * 认证布局壳 —— PRD §4.2
 * 认证守卫：无 token → redirect /login。
 * 桌面 236px 黑侧栏 + 顶栏 + 内容区；平板收为图标栏；移动隐藏侧栏。
 * 数据大屏（/_authenticated/data-screen）为独立全屏路由，隐藏侧栏与顶栏。
 */

const PAGE_TITLES: Record<string, string> = {
  '/_authenticated/index': '仪表盘',
  '/_authenticated/products': '商品管理',
  '/_authenticated/on-sale': '上架工作台',
  '/_authenticated/orders': '订单中心',
  '/_authenticated/tasks': '任务中心',
  '/_authenticated/pricing': '智能定价',
  '/_authenticated/image-studio': '图片工坊',
  '/_authenticated/collect-box': '采集箱',
  '/_authenticated/stores': '店铺管理',
  '/_authenticated/bestsellers': '热销榜',
  '/_authenticated/data-screen': '数据大屏',
  '/_authenticated/templates': '上架模板',
  '/_authenticated/settings': '系统设置',
  '/_authenticated/admin': '管理员后台',
}

export const Route = createFileRoute('/_authenticated')({
  beforeLoad: () => {
    const { token } = useAuthStore.getState()
    if (!token) {
      throw redirect({ to: '/login' })
    }
  },
  component: AuthenticatedLayout,
})

function AuthenticatedLayout() {
  const matches = useMatches()
  const currentMatch = [...matches].reverse().find((m) => PAGE_TITLES[m.routeId])
  const isDataScreen = matches.some((m) => m.routeId === '/_authenticated/data-screen')

  // 数据大屏：独立全屏（无侧栏/顶栏）
  if (isDataScreen) {
    return <Outlet />
  }

  return (
    <div className="min-h-screen bg-page">
      <div className="grid min-h-screen grid-cols-1 tablet:grid-cols-[64px_minmax(0,1fr)] desktop:grid-cols-[236px_minmax(0,1fr)]">
        <Sidebar />
        <div className="flex min-w-0 flex-col">
          <Header title={currentMatch ? PAGE_TITLES[currentMatch.routeId] : 'Ozon ERP'} />
          <main className="flex-1 px-6 py-8 desktop:px-page-pad">
            <div key={currentMatch?.routeId ?? 'page'} className="mx-auto w-full max-w-[1180px] animate-fade-in">
              <Outlet />
            </div>
          </main>
        </div>
      </div>
    </div>
  )
}
