const API_PREFIX = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") || "/api/v1"
const TOKEN_KEY = "ozon_webui_token"
const ROLE_KEY = "ozon_webui_role"

export type Session = { token: string; role: "admin" | "user"; username?: string }

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export function getSession(): Session | null {
  const token = localStorage.getItem(TOKEN_KEY)
  if (!token) return null
  return { token, role: localStorage.getItem(ROLE_KEY) === "admin" ? "admin" : "user", username: localStorage.getItem("ozon_webui_username") ?? undefined }
}

export function saveSession(session: Session) {
  localStorage.setItem(TOKEN_KEY, session.token)
  localStorage.setItem(ROLE_KEY, session.role)
  if (session.username) localStorage.setItem("ozon_webui_username", session.username)
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(ROLE_KEY)
  localStorage.removeItem("ozon_webui_username")
}

async function request<T>(path: string, init: RequestInit = {}, includeToken = true): Promise<T> {
  const token = getSession()?.token
  const headers = new Headers(init.headers)
  if (includeToken && token) headers.set("Authorization", `Bearer ${token}`)
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json")
  const response = await fetch(`${API_PREFIX}${path}`, { ...init, headers })
  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try { message = (await response.json() as { detail?: string }).detail || message } catch { /* no JSON body */ }
    // 鉴权失效：清会话并通知应用跳转到登录页，避免各面板误显示"服务异常/加载中"
    if (response.status === 401 && includeToken) {
      clearSession()
      window.dispatchEvent(new Event("auth:expired"))
    }
    throw new ApiError(response.status, message)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown, includeToken = true) => request<T>(path, { method: "POST", body: JSON.stringify(body) }, includeToken),
  put: <T>(path: string, body: unknown) => request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) => request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  verify: (token: string) => request<{ valid: boolean; reason?: string }>("/auth/verify", { method: "POST", body: JSON.stringify({ token }) }, false),
  login: (username: string, password: string) => request<{ key?: string | null; role?: string; username?: string }>("/mxou/login", { method: "POST", body: JSON.stringify({ username, password }) }, false),
  me: () => request<{ user_id: string; email: string; role: string }>("/mxou/me"),
}

/** 下载 CSV 类附件(带 Bearer 鉴权,触发浏览器下载)。 */
export async function downloadCsv(path: string, filename: string): Promise<void> {
  const token = getSession()?.token
  const headers = new Headers()
  if (token) headers.set("Authorization", `Bearer ${token}`)
  const response = await fetch(`${API_PREFIX}${path}`, { headers })
  if (!response.ok) {
    let message = `导出失败（${response.status}）`
    try { message = (await response.json() as { detail?: string }).detail || message } catch { /* no JSON */ }
    throw new ApiError(response.status, message)
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
