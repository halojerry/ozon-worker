import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { verifyToken, type AuthVerifyReason } from '../api/client'
import { setToken } from '../stores/auth'
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

export default function Login() {
  const navigate = useNavigate()
  const [token, setTokenValue] = useState('')
  const [showToken, setShowToken] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

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
      </div>
    </div>
  )
}
