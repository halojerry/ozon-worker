import { useState, type FormEvent } from 'react'
import { useNavigate } from '@/lib/router-compat'
import { verifyToken, mxouLogin, type AuthVerifyReason } from '../api/client'
import { setToken } from '../stores/auth'
import { setSession, type SessionState } from '../stores/session'
import '../index.css'

const REASON_MESSAGES: Record<AuthVerifyReason, string> = {
  ok: '',
  token_invalid: 'Token 无效：请检查 MXOU API Key 是否正确',
  balance_insufficient: '账户余额不足：请到 MXOU 平台充值后重试',
  account_inactive: '账户未激活或已停用，请联系管理员',
  invalid_request: '请求格式错误，请刷新后重试',
}

const NETWORK_ERROR =
  '无法连接服务端：请确认 Worker 已启动（本地开发指向 localhost:8080，生产为站点域名）'

type ActiveTab = 'account' | 'key'

function formatBalance(balance: number | null | undefined): string {
  if (balance == null || !Number.isFinite(balance)) return '—'
  return balance.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function Login() {
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState<ActiveTab>('key')

  // ── API Key tab（T3 回归：逻辑与 v0.42 完全一致） ──
  const [token, setTokenValue] = useState('')
  const [showToken, setShowToken] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  // ── 账号密码 tab（T3：mxouLogin → 会话元数据 store；激活接线留待 T4） ──
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [accountError, setAccountError] = useState('')
  const [accountLoading, setAccountLoading] = useState(false)
  const [accountInfo, setAccountInfo] = useState<SessionState | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const value = token.trim()
    if (!value) {
      setError('请输入 MXOU Token')
      return
    }
    setLoading(true)
    setError('')
    try {
      const resp = await verifyToken({ token: value })
      if (resp.valid) {
        setToken(value)
        navigate('/', { replace: true })
      } else {
        setError(REASON_MESSAGES[resp.reason] ?? `验证失败：${resp.reason}`)
      }
    } catch (err) {
      setError(NETWORK_ERROR)
      console.error('[webui] auth/verify 调用失败:', err)
    } finally {
      setLoading(false)
    }
  }

  async function handleAccountSubmit(e: FormEvent) {
    e.preventDefault()
    const user = username.trim()
    if (!user || !password) {
      setAccountError('请输入 MXOU 账号和密码')
      return
    }
    setAccountLoading(true)
    setAccountError('')
    try {
      const resp = await mxouLogin(user, password)
      const session: SessionState = {
        username: resp.username,
        balance: resp.balance ?? null,
        keys: resp.keys ?? [],
        selected_key_id: resp.selected_key_id ?? null,
        session_expires_at: resp.session_expires_at ?? null,
      }
      setSession(session)
      // 账号登录即登录：登录响应直接返回选中 key 的完整值（key 字段，仅此一次），
      // 建立 token 登录态后直接进工作台——无需再手动切 API Key tab。
      if (resp.key) {
        setToken(resp.key)
        navigate('/', { replace: true })
        return
      }
      // 未选到 enabled key（账号无可用密钥）→ 展示账号信息 + 引导去 MXOU 平台创建
      setAccountInfo(session)
      setAccountError('账号下没有可用的 API Key，请到 MXOU 平台创建后重试')
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status
      setAccountError(status === 401 ? '账号或密码错误，请重试' : NETWORK_ERROR)
      console.error('[webui] mxou/login 调用失败:', err)
    } finally {
      setAccountLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <div className="login-logo" aria-hidden="true">
            <svg viewBox="0 0 32 32" width="40" height="40">
              <rect width="32" height="32" rx="7" fill="#005bff" />
              <path
                d="M9 10.5h9.2c2.6 0 4.3 1.5 4.3 3.7 0 1.6-1 2.9-2.5 3.4v.1c1.9.4 3.2 1.8 3.2 3.8 0 2.5-1.9 4.1-4.7 4.1H9V10.5zm3.7 6.4h4.9c1.3 0 2.1-.7 2.1-1.8s-.8-1.8-2.1-1.8h-4.9v3.6zm0 6.4h5.2c1.4 0 2.3-.7 2.3-1.9 0-1.2-.9-1.9-2.3-1.9h-5.2v3.8z"
                fill="#fff"
              />
            </svg>
          </div>
          <h1 className="login-title">Ozon 上架助手</h1>
          <p className="login-subtitle">登录以进入 WebUI 工作台</p>
        </div>

        <div className="login-tabs" role="tablist" aria-label="登录方式">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'account'}
            className={`login-tab${activeTab === 'account' ? ' active' : ''}`}
            onClick={() => setActiveTab('account')}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <circle cx="12" cy="8" r="3.5" />
              <path d="M4.5 19.5c.8-3.2 3.6-5 7.5-5s6.7 1.8 7.5 5" strokeLinecap="round" />
            </svg>
            账号密码
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'key'}
            className={`login-tab${activeTab === 'key' ? ' active' : ''}`}
            onClick={() => setActiveTab('key')}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <circle cx="8" cy="14" r="4" />
              <path d="M11 11L19.5 2.5M16 7l3 3M13.5 9.5l2.5 2.5" strokeLinecap="round" />
            </svg>
            API Key
          </button>
        </div>

        {activeTab === 'account' ? (
          <form className="login-form" onSubmit={handleAccountSubmit}>
            <label className="field" htmlFor="username">
              <span className="field-label">MXOU 账号</span>
              <input
                id="username"
                className="field-input"
                type="text"
                placeholder="api.mxou.cn 登录账号"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
                spellCheck={false}
              />
            </label>

            <label className="field" htmlFor="password">
              <span className="field-label">密码</span>
              <div className="token-input-wrap">
                <input
                  id="password"
                  className="field-input"
                  type={showPassword ? 'text' : 'password'}
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  spellCheck={false}
                />
                <button
                  type="button"
                  className="token-toggle"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? '隐藏密码' : '显示密码'}
                >
                  {showPassword ? (
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8">
                      <path d="M3 3l18 18M10.6 10.6a2 2 0 002.8 2.8M9.9 5.2A9.8 9.8 0 0112 5c5 0 9 4 9 7a8.6 8.6 0 01-2.2 3M6.6 6.6A8.8 8.8 0 003 12c0 3 4 7 9 7a9.3 9.3 0 003.6-.7" />
                    </svg>
                  ) : (
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8">
                      <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" />
                      <circle cx="12" cy="12" r="3" />
                    </svg>
                  )}
                </button>
              </div>
              <span className="field-hint">使用 MXOU 平台账号验证，成功后可查看余额与密钥列表</span>
            </label>

            {accountError && (
              <div className="login-error" role="alert">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="9" />
                  <path d="M12 8v5M12 16.5v.5" />
                </svg>
                {accountError}
              </div>
            )}

            {accountInfo && (
              <div className="account-panel" role="status">
                <div className="account-panel-head">
                  <span className="account-panel-user">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <circle cx="12" cy="12" r="9" />
                      <path d="M8.5 12.5l2.5 2.5 4.5-5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    {accountInfo.username} 验证成功
                  </span>
                  <span className="account-panel-balance">余额 ¥{formatBalance(accountInfo.balance)}</span>
                </div>
                <ul className="account-keys">
                  {accountInfo.keys.length === 0 && (
                    <li className="account-key">该账号暂无 API Key</li>
                  )}
                  {accountInfo.keys.map((k) => (
                    <li className="account-key" key={k.id}>
                      <span className="account-key-id">{k.id}</span>
                      {k.name ? <span className="account-key-name">{k.name}</span> : null}
                      {accountInfo.selected_key_id === k.id ? (
                        <span className="badge badge-ok">已选</span>
                      ) : null}
                      <span className={`badge ${k.status === 1 ? 'badge-ok' : 'badge-fail'}`}>
                        {k.status === 1 ? '启用' : '停用'}
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="account-panel-hint">
                  完整密钥将在「密钥管理」中选择激活（即将上线），激活后自动登录；当前请先使用 API Key 登录。
                </p>
                <button type="button" className="btn account-key-cta" onClick={() => setActiveTab('key')}>
                  使用 API Key 登录
                </button>
              </div>
            )}

            <button type="submit" className="btn btn-primary login-submit" disabled={accountLoading}>
              {accountLoading ? <span className="spinner" aria-hidden="true" /> : null}
              {accountLoading ? '验证中…' : '验证账号'}
            </button>
          </form>
        ) : (
          <form className="login-form" onSubmit={handleSubmit}>
            <label className="field" htmlFor="token">
              <span className="field-label">MXOU Token</span>
              <div className="token-input-wrap">
                <input
                  id="token"
                  className="field-input"
                  type={showToken ? 'text' : 'password'}
                  placeholder="sk-xxxxxxxx..."
                  value={token}
                  onChange={(e) => setTokenValue(e.target.value)}
                  autoComplete="off"
                  autoFocus
                  spellCheck={false}
                />
                <button
                  type="button"
                  className="token-toggle"
                  onClick={() => setShowToken((v) => !v)}
                  aria-label={showToken ? '隐藏 Token' : '显示 Token'}
                >
                  {showToken ? (
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8">
                      <path d="M3 3l18 18M10.6 10.6a2 2 0 002.8 2.8M9.9 5.2A9.8 9.8 0 0112 5c5 0 9 4 9 7a8.6 8.6 0 01-2.2 3M6.6 6.6A8.8 8.8 0 003 12c0 3 4 7 9 7a9.3 9.3 0 003.6-.7" />
                    </svg>
                  ) : (
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8">
                      <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" />
                      <circle cx="12" cy="12" r="3" />
                    </svg>
                  )}
                </button>
              </div>
              <span className="field-hint">Token 由 MXOU 平台签发，登录后保存于本地浏览器</span>
            </label>

            {error && (
              <div className="login-error" role="alert">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="9" />
                  <path d="M12 8v5M12 16.5v.5" />
                </svg>
                {error}
              </div>
            )}

            <button type="submit" className="btn btn-primary login-submit" disabled={loading}>
              {loading ? (
                <span className="spinner" aria-hidden="true" />
              ) : null}
              {loading ? '验证中…' : '登录'}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
