import { create } from 'zustand'
import type { SessionUser } from './auth'

/**
 * 会话状态：登录后的非认证类 UI 状态。
 * 当前无后端会话 API，先承载登录来源/余额等展示位，W1 起接入
 * GET /api/v1/mxou/my-key 与 GET /api/v1/task_statistics 聚合。
 */
interface SessionState {
  /** 登录方式：api-key | account */
  loginMethod: 'api-key' | 'account' | null
  /** 登录时间戳 */
  loggedInAt: number | null
  /** 会话用户信息（与 auth store 冗余，用于只读组件） */
  user: SessionUser | null
  setSession: (method: 'api-key' | 'account', user?: SessionUser | null) => void
  reset: () => void
}

export const useSessionStore = create<SessionState>((set) => ({
  loginMethod: null,
  loggedInAt: null,
  user: null,
  setSession: (method, user = null) =>
    set({ loginMethod: method, loggedInAt: Date.now(), user }),
  reset: () => set({ loginMethod: null, loggedInAt: null, user: null }),
}))
