import { create } from 'zustand'

/**
 * 认证 token 状态。
 *
 * 存储约定（与 api-integration/API-INTEGRATION-GUIDE.md 一致）：
 * token 以裸字符串存于 localStorage key `ozon_webui_token`，
 * Axios 请求拦截器读这里注入 `Authorization: Bearer <token>`。
 */
const TOKEN_KEY = 'ozon_webui_token'
const USER_KEY = 'ozon_webui_user'

function readStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

function readStoredUser(): SessionUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as SessionUser) : null
  } catch {
    return null
  }
}

export interface SessionUser {
  /** MXOU 用户名（账号登录后已知；token 登录可为 null 直至 GET /mxou/my-key） */
  username?: string | null
  /** 平台真实余额（美元） */
  balance?: number | null
  /** 用户角色 admin / user（管理员路由守卫用） */
  role?: string | null
  /** token 对应的 key 展示名（脱敏） */
  keyName?: string | null
}

interface AuthState {
  token: string | null
  user: SessionUser | null
  setToken: (token: string) => void
  setUser: (user: SessionUser | null) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  token: readStoredToken(),
  user: readStoredUser(),
  setToken: (token) => {
    const clean = token.replace(/^sk-/, '')
    set({ token: clean })
    try {
      localStorage.setItem(TOKEN_KEY, clean)
    } catch {
      /* 隐私模式等场景静默降级（内存态仍有效） */
    }
  },
  setUser: (user) => {
    set({ user })
    try {
      if (user) localStorage.setItem(USER_KEY, JSON.stringify(user))
      else localStorage.removeItem(USER_KEY)
    } catch {
      /* noop */
    }
  },
  logout: () => {
    set({ token: null, user: null })
    try {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
    } catch {
      /* noop */
    }
  },
}))

/** 供非 React 上下文（拦截器）读取 */
export function getStoredToken(): string | null {
  return useAuthStore.getState().token
}
