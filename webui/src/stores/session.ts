/**
 * 前端状态：MXOU 会话元数据（src/stores/ —— 与 auth.ts 同模式：localStorage 持久化 + useSyncExternalStore）
 *
 * ⚠️ 本 store 只存元数据（username/balance/脱敏 keys/selected_key_id/session_expires_at），
 *    绝不存完整 API Key —— 完整 key 永远只在 stores/auth.ts 的 token store 里。
 */

import { useSyncExternalStore } from 'react'
import type { MxouKeyItem } from '../api/client'

export const SESSION_STORAGE_KEY = 'ozon_webui_session'

/** 会话元数据（与 worker MxouLoginResponse 同构；balance 为 number|null） */
export interface SessionState {
  username: string
  balance: number | null
  keys: MxouKeyItem[]
  selected_key_id?: string | null
  session_expires_at?: string | null
}

type Listener = () => void

function normalize(raw: SessionState): SessionState {
  return {
    username: raw.username,
    balance: typeof raw.balance === 'number' && Number.isFinite(raw.balance) ? raw.balance : null,
    keys: Array.isArray(raw.keys) ? raw.keys : [],
    selected_key_id: raw.selected_key_id ?? null,
    session_expires_at: raw.session_expires_at ?? null,
  }
}

function readStored(): SessionState | null {
  try {
    const raw = localStorage.getItem(SESSION_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as SessionState
    if (!parsed || typeof parsed.username !== 'string' || !parsed.username) return null
    return normalize(parsed)
  } catch {
    return null
  }
}

let listeners: Listener[] = []
let cachedSession: SessionState | null = readStored()

function emit(): void {
  listeners.forEach((l) => l())
}

export function getSession(): SessionState | null {
  return cachedSession
}

export function setSession(session: SessionState): void {
  cachedSession = normalize(session)
  localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(cachedSession))
  emit()
}

export function clearSession(): void {
  cachedSession = null
  localStorage.removeItem(SESSION_STORAGE_KEY)
  emit()
}

/** React hook：会话元数据变化时触发重渲染（登录成功卡片 / 布局余额徽章 T4 用） */
export function useSession(): SessionState | null {
  return useSyncExternalStore(
    (cb) => {
      listeners.push(cb)
      return () => {
        listeners = listeners.filter((l) => l !== cb)
      }
    },
    getSession,
  )
}
