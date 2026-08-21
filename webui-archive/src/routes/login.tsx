import { createFileRoute, redirect, useRouter } from '@tanstack/react-router'
import { useState } from 'react'
import { KeyRound, UserRound } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs } from '@/components/ui/tabs'
import { authVerify, mxouLogin } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useSessionStore } from '@/stores/session'
import { toErrorMessage } from '@/lib/errors'
import { formatCurrency } from '@/lib/format'

/**
 * 登录页 —— PRD §5.1（ozon-login-proto）
 * 左黑品牌面板（40%）+ 右表单区（60%）：
 * - API Key 登录：POST /auth/verify → 存 token → 进仪表盘
 * - 账号密码登录：POST /mxou/login → 取 key → 同上（返回余额/角色）
 * 认证流程（PRD §5.1）：验证成功 → 存 localStorage → 路由守卫放行。
 */

interface LoginSearch {
  redirect?: string
}

export const Route = createFileRoute('/login')({
  validateSearch: (search: Record<string, unknown>): LoginSearch => ({
    redirect: typeof search.redirect === 'string' ? search.redirect : undefined,
  }),
  beforeLoad: () => {
    const { token } = useAuthStore.getState()
    if (token) {
      throw redirect({ to: '/' })
    }
  },
  component: LoginRoute,
})

const TOKEN_ERROR_TEXT: Record<string, string> = {
  token_invalid: 'Token 无效，请检查后重试',
  balance_insufficient: '余额不足，请先充值',
  account_inactive: '账号未激活，请联系管理员',
}

function LoginRoute() {
  const router = useRouter()
  const { setToken, setUser } = useAuthStore()
  const { setSession } = useSessionStore()
  const { redirect: redirectTarget } = Route.useSearch()

  const [tab, setTab] = useState<'api-key' | 'account'>('api-key')
  const [tokenInput, setTokenInput] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [balance, setBalance] = useState<number | null | undefined>(null)

  function gotoApp() {
    router.history.push(redirectTarget ?? '/')
  }

  async function handleTokenLogin(e: React.FormEvent) {
    e.preventDefault()
    const value = tokenInput.trim()
    if (!value) {
      setError('请输入 API Key')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await authVerify(value)
      if (!res.valid) {
        setError(TOKEN_ERROR_TEXT[res.reason ?? ''] ?? '验证失败，请稍后重试')
        return
      }
      setToken(value)
      setUser({ keyName: 'API Key' })
      setSession('api-key')
      gotoApp()
    } catch (err) {
      setError(toErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  async function handleAccountLogin(e: React.FormEvent) {
    e.preventDefault()
    if (!username.trim() || !password) {
      setError('请输入账号和密码')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await mxouLogin(username.trim(), password)
      const key = res.key
      if (!key) {
        setError('登录成功但未获取到可用密钥，请先在 MXOU 平台创建密钥')
        setBalance(res.balance ?? null)
        return
      }
      setToken(key)
      setUser({ username: res.username, balance: res.balance, role: res.role })
      setBalance(res.balance ?? null)
      setSession('account', { username: res.username, balance: res.balance, role: res.role })
      gotoApp()
    } catch (err) {
      setError(toErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="grid min-h-screen grid-cols-1 lg:grid-cols-[40%_60%]">
      {/* 左：黑色品牌面板（40%） */}
      <div className="hidden flex-col justify-between bg-sidebar p-12 text-on-dark lg:flex">
        <div className="flex items-center gap-3">
          <div className="flex size-[32px] items-center justify-center rounded-[6px] bg-accent">
            <span className="text-[16px] font-bold text-white">A</span>
          </div>
          <b className="text-[18px] font-bold tracking-[0.5px]">OzonAI</b>
        </div>
        <div>
          <h1 className="text-display text-on-dark">
            Ozon AI
            <br />
            自动化运营 ERP
          </h1>
          <p className="mt-4 text-body text-sidebar-muted">
            智能运营 · 高效管理 · 数据驱动增长
          </p>
        </div>
        <p className="text-caption text-sidebar-muted">© {new Date().getFullYear()} OzonAI ERP</p>
      </div>

      {/* 右：表单区（60%） */}
      <div className="flex items-center justify-center bg-page px-6 py-12">
        <div className="w-full max-w-[400px]">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <span className="size-[26px] rounded-[6px] bg-accent shadow-glow" aria-hidden />
            <b className="text-[16px] font-bold tracking-[0.5px] text-ink">Ozon ERP</b>
          </div>

          <h2 className="mb-1 text-h2 text-ink">登录系统</h2>
          <p className="mb-6 text-body-sm text-ink-aux">使用 MXOU API Key 或账号密码进入工作台</p>

          <Tabs
            className="mb-6"
            value={tab}
            onChange={(k) => {
              setTab(k as 'api-key' | 'account')
              setError(null)
            }}
            items={[
              { key: 'api-key', label: 'API Key' },
              { key: 'account', label: '账号密码' },
            ]}
          />

          {tab === 'api-key' ? (
            <form onSubmit={handleTokenLogin} className="space-y-4" noValidate>
              <div>
                <label className="mb-1 block text-body-sm font-medium text-ink">API Key</label>
                <Input
                  leading={<KeyRound className="size-4" />}
                  placeholder="请输入 API Key"
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  error={error ?? undefined}
                  hint={error ? undefined : '输入 MXOU API Key（sk- 前缀可选）'}
                  autoFocus
                  autoComplete="off"
                />
              </div>
              <Button type="submit" loading={loading} className="w-full">
                登录
              </Button>
            </form>
          ) : (
            <form onSubmit={handleAccountLogin} className="space-y-4" noValidate>
              <div>
                <label className="mb-1 block text-body-sm font-medium text-ink">用户名</label>
                <Input
                  leading={<UserRound className="size-4" />}
                  placeholder="MXOU 用户名"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  error={error ? ' ' : undefined}
                  autoComplete="username"
                />
              </div>
              <div>
                <label className="mb-1 block text-body-sm font-medium text-ink">密码</label>
                <Input
                  leading={<KeyRound className="size-4" />}
                  placeholder="请输入密码"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                />
              </div>
              {error && <p className="text-[12px] text-accent-dark">{error}</p>}
              <Button type="submit" loading={loading} className="w-full">
                登录
              </Button>
            </form>
          )}

          {/* 底部余额展示（PRD §5.1） */}
          <div className="mt-8 flex items-center justify-between rounded-card bg-badge-neutral px-4 py-3">
            <div className="flex items-center gap-3">
              <div className="flex size-[28px] items-center justify-center rounded-full bg-surface">
                <span className="text-[12px]">💰</span>
              </div>
              <div>
                <p className="text-caption text-ink-aux">账户余额</p>
                <p className="font-mono text-data-md text-ink">
                  {balance == null ? '登录后展示' : `¥ ${formatCurrency(balance, 'CNY')}`}
                </p>
              </div>
            </div>
            <span className="text-caption text-ink-aux">余额明细 &gt;</span>
          </div>
        </div>
      </div>
    </div>
  )
}
