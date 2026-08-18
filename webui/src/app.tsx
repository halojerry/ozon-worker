import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createRouter } from '@tanstack/react-router'
import { routeTree } from './routeTree.gen'
import type { RouterContext } from './routes/__root'

/**
 * 根布局 —— PRD §4.2
 * QueryClient + RouterProvider（basepath `/app`：worker FastAPI 同域伺服）。
 */

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

const routerContext: RouterContext = { queryClient }

const router = createRouter({
  routeTree,
  basepath: '/app',
  context: routerContext,
  defaultPreload: 'intent',
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}
