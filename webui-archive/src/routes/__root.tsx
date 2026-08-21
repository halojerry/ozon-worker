import { createRootRouteWithContext, Outlet } from '@tanstack/react-router'
import type { QueryClient } from '@tanstack/react-query'

export interface RouterContext {
  queryClient: QueryClient
}

/**
 * 根路由：仅挂载 Outlet。认证守卫在 `_authenticated/route.tsx`（登录后）与
 * `login.tsx`（未登录）两处分别实现。
 */
export const Route = createRootRouteWithContext<RouterContext>()({
  component: () => <Outlet />,
})
