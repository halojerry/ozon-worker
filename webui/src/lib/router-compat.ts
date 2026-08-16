import { useNavigate as useTSNavigate, useParams as useTSParams, useSearch as useTSSearch, Link as TSLink } from '@tanstack/react-router'
import { createElement, type ComponentProps } from 'react'

/**
 * react-router-dom 兼容层：业务页面（pages/*）从 react-router-dom 迁移到
 * TanStack Router 时，只改 import 来源即可，调用签名保持兼容。
 * 用法：`import { useNavigate, useParams, useSearchParams, Link } from '@/lib/router-compat'`
 */

/** navigate('/path') / navigate('/path', { replace: true, state }) —— react-router 签名兼容 */
export function useNavigate() {
  const navigate = useTSNavigate()
  return (to: string, opts?: { replace?: boolean; state?: Record<string, unknown> }) => {
    navigate({ to, replace: opts?.replace, state: opts?.state as never })
  }
}

/** useParams<{ draftId: string }>() —— 宽松模式（无 from） */
export function useParams<T extends Record<string, string>>(): T {
  // TanStack Router 宽松模式：无 from 时返回当前路由全部 params
  return useTSParams({ strict: false } as never) as T
}

/** useSearchParams() → [URLSearchParams, setter] —— react-router 兼容（只读常用） */
export function useSearchParams(): [URLSearchParams, (next: URLSearchParams) => void] {
  const search = useTSSearch({ strict: false } as never) as Record<string, unknown>
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(search)) {
    if (v !== undefined && v !== null) params.set(k, String(v))
  }
  const setParams = (next: URLSearchParams) => {
    const obj: Record<string, string> = {}
    next.forEach((v, k) => {
      obj[k] = v
    })
    // 页面只读 searchParams，setter 兼容占位（路由参数变化由导航驱动）
    void obj
  }
  return [params, setParams]
}

/** <Link to="/x"> —— react-router 兼容（to 为字符串路径） */
export function Link(props: ComponentProps<typeof TSLink> & { to: string; className?: string; title?: string }) {
  const { to, ...rest } = props
  return createElement(TSLink, { to: to as never, ...(rest as object) })
}
