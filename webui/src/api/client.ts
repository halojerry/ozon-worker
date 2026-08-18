import axios, { type AxiosError, type AxiosRequestConfig, type AxiosResponse } from 'axios'
import { useAuthStore } from '@/stores/auth'

/**
 * Axios 实例：baseURL `/api/v1`（worker FastAPI，同域托管于 /app，零 CORS）。
 *
 * 请求拦截器：注入 `Authorization: Bearer <token>`（token 存 localStorage
 * `ozon_webui_token`，见 stores/auth.ts）。
 *
 * 响应拦截器（PRD §8.1）：
 * - 401 → 清除 token → 重定向 /app/login（已在登录页则不再跳）
 * - 503 → 服务不可用，派发全局事件供 UI 提示（不清 token）
 */
export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30_000,
})

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

/** 401 拦截事件：UI 层可监听展示「登录已过期」 */
export const AUTH_EXPIRED_EVENT = 'oz:auth-expired'

function handleUnauthorized(): void {
  const { token, logout } = useAuthStore.getState()
  if (!token) return
  logout()
  window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT))
  const path = window.location.pathname
  if (!path.startsWith('/app/login')) {
    window.location.replace('/app/login')
  }
}

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const status = error.response?.status
    if (status === 401) {
      handleUnauthorized()
    }
    if (status === 503) {
      window.dispatchEvent(new CustomEvent('oz:service-unavailable'))
    }
    return Promise.reject(error)
  },
)

/* ── 类型化请求辅助 ─────────────────────────────────────────────── */

export async function apiGet<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const res = await api.get<T>(url, config)
  return res.data
}

export async function apiPost<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const res = await api.post<T>(url, data, config)
  return res.data
}

export async function apiPatch<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const res = await api.patch<T>(url, data, config)
  return res.data
}

export async function apiDelete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const res = await api.delete<T>(url, config)
  return res.data
}

export async function apiPut<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  const res = await api.put<T>(url, data, config)
  return res.data
}

export type { AxiosError, AxiosResponse }

/* ── 鉴权端点（PRD §5.1） ───────────────────────────────────────── */

import type { components } from './generated'

export type AuthVerifyResponse = components['schemas']['AuthVerifyResponse']
export type MxouLoginResponse = components['schemas']['MxouLoginResponse']
export type MxouKeyItem = components['schemas']['MxouKeyItem']

/** POST /auth/verify —— token 验证（成功 → 存 token → 路由守卫放行） */
export function authVerify(token: string): Promise<AuthVerifyResponse> {
  return apiPost<AuthVerifyResponse>('/auth/verify', { token })
}

/** POST /mxou/login —— 账号密码登录（免鉴权），返回可用 key + 余额 + 角色 */
export function mxouLogin(username: string, password: string): Promise<MxouLoginResponse> {
  return apiPost<MxouLoginResponse>('/mxou/login', { username, password })
}

/** GET /mxou/my-key —— 当前已选 key（token 登录后补全会话信息用） */
export function fetchMyKey(): Promise<{ key?: string }> {
  return apiGet<{ key?: string }>('/mxou/my-key')
}
