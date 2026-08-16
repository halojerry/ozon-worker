/*
Copyright (C) 2023-2026 QuantumNous

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

For commercial licensing, please contact support@quantumnous.com
*/
import { createFileRoute, redirect } from '@tanstack/react-router'
import { useAuthStore } from '@/stores/auth-store'
import { getSelf } from '@/lib/api'
import { Outlet } from '@tanstack/react-router'
// 临时测试布局：确认路由匹配是否正常
function TestLayout() {
  return <Outlet />
}

// 内存中的验证标记，避免同一会话中重复验证
let sessionVerified = false

export const Route = createFileRoute('/_authenticated')({
  beforeLoad: async ({ location }) => {
    // ⚠️ 直接读 localStorage（生产构建 store 初始化时序不可靠，dev 正常 prod 偶发读不到）
    const { auth } = useAuthStore.getState()
    const hasUser = auth.user || (typeof window !== 'undefined' && !!window.localStorage.getItem('user'))

    // 如果本地没有用户信息，直接跳转登录页
    if (!hasUser) {
      throw redirect({
        to: '/sign-in',
        search: { redirect: location.href },
      })
    }

    // 本地有用户信息，但需要验证 session 是否有效（每个会话只验证一次）
    // ⚠️ getSelf 失败（New API 后端不可达）不重置登录态——保留 localStorage user
    //    继续渲染（业务页独立走 worker /api/v1，不依赖 mxou session 验证）
    if (!sessionVerified) {
      const res = await getSelf().catch(() => null)
      if (res?.success && res.data) {
        auth.setUser(res.data)
      }
      sessionVerified = true
    }
  },
  component: TestLayout,
})
