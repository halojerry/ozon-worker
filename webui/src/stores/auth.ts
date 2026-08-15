/**
 * 前端状态：token 鉴权（src/stores/ —— token/店铺/当前草稿 等前端状态统一放这里）
 *
 * 持久化：localStorage（键 TOKEN_STORAGE_KEY），刷新后仍登录。
 * 订阅：useAuth() 基于 useSyncExternalStore，token 变化驱动受保护路由重渲染。
 */

import { useSyncExternalStore } from 'react'
import { TOKEN_STORAGE_KEY } from '../api/client'

type Listener = () => void

let listeners: Listener[] = []
let cachedToken: string | null = localStorage.getItem(TOKEN_STORAGE_KEY)

function emit(): void {
  listeners.forEach((l) => l())
}

export function getToken(): string | null {
  return cachedToken
}

export function isAuthenticated(): boolean {
  return Boolean(cachedToken)
}

export function setToken(value: string): void {
  const trimmed = value.trim()
  cachedToken = trimmed || null
  if (trimmed) {
    localStorage.setItem(TOKEN_STORAGE_KEY, trimmed)
  } else {
    localStorage.removeItem(TOKEN_STORAGE_KEY)
  }
  emit()
}

export function clearToken(): void {
  setToken('')
}

/** React hook：token 状态变化时触发重渲染（受保护路由 / 布局登出入口用） */
export function useAuth(): boolean {
  return useSyncExternalStore(
    (cb) => {
      listeners.push(cb)
      return () => {
        listeners = listeners.filter((l) => l !== cb)
      }
    },
    isAuthenticated,
  )
}
