import { useCallback, useEffect, useState } from 'react'
import {
  createCredential,
  listCredentials,
  revokeCredential,
  rotateCredential,
  validateCredential,
  type CredentialOut,
} from '../api/client'

const ROTATION_WARN_DAYS = 30
const DAY_MS = 24 * 60 * 60 * 1000

const VALIDATE_REASON: Record<string, string> = {
  ok: '凭证有效',
  invalid_key: '密钥无效（401/403）',
  ozon_api_error: 'Ozon API 调用失败',
  decrypt_failed: '解密失败，请重新配置',
}

function formatTime(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function effectiveRotationAt(c: CredentialOut): number | null {
  const t = c.last_rotated_at ?? c.created_at
  if (!t) return null
  const ms = new Date(t).getTime()
  return Number.isNaN(ms) ? null : ms
}

function reasonText(reason: string): string {
  return VALIDATE_REASON[reason] ?? reason
}

function extractError(err: unknown, fallback: string): string {
  const resp = (err as { response?: { data?: { detail?: string } } } | null)?.response
  return resp?.data?.detail || fallback
}

interface RowValidate {
  status: 'pending' | 'success' | 'fail'
  text: string
}

function EyeIcon() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" />
      <circle cx="12" cy="12" r="2.8" />
    </svg>
  )
}

function EyeOffIcon() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M4 4l16 16M9.9 5.9A9.8 9.8 0 0112 5.5c6 0 9.5 6.5 9.5 6.5a17.6 17.6 0 01-2.6 3.1M6.4 6.9A17 17 0 002.5 12s3.5 6.5 9.5 6.5a9.7 9.7 0 005.6-1.8" />
    </svg>
  )
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  )
}

function WarningIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M12 3.5L22 20H2L12 3.5z" />
      <path d="M12 9.5v5M12 17.2v.1" />
    </svg>
  )
}

function ErrorIcon() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.5v6M12 16.8v.1" />
    </svg>
  )
}

interface ApiKeyInputProps {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  autoFocus?: boolean
}

function ApiKeyInput({ value, onChange, placeholder, autoFocus }: ApiKeyInputProps) {
  const [show, setShow] = useState(false)
  return (
    <div className="token-input-wrap">
      <input
        className="field-input"
        type={show ? 'text' : 'password'}
        value={value}
        placeholder={placeholder}
        autoFocus={autoFocus}
        autoComplete="new-password"
        spellCheck={false}
        onChange={(e) => onChange(e.target.value)}
      />
      <button
        type="button"
        className="token-toggle"
        aria-label={show ? '隐藏密钥' : '显示密钥'}
        onClick={() => setShow((v) => !v)}
      >
        {show ? <EyeOffIcon /> : <EyeIcon />}
      </button>
    </div>
  )
}

/* ── 添加店铺弹窗（毛子绑定式：名称/Client ID/密钥/货币/默认 radio） ── */

interface AddStoreModalProps {
  defaultDefault: boolean
  onClose: () => void
  onCreate: (payload: {
    shop_name: string
    ozon_client_id: string
    api_key: string
    currency: string
    is_default: boolean
  }) => Promise<string | null>
}

function AddStoreModal({ defaultDefault, onClose, onCreate }: AddStoreModalProps) {
  const [shopName, setShopName] = useState('')
  const [clientId, setClientId] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [currency, setCurrency] = useState('CNY')
  const [isDefault, setIsDefault] = useState(defaultDefault)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const canSubmit = clientId.trim() !== '' && apiKey.trim() !== '' && !submitting

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit) return
    setSubmitting(true)
    setError('')
    const err = await onCreate({
      shop_name: shopName.trim(),
      ozon_client_id: clientId.trim(),
      api_key: apiKey.trim(),
      currency,
      is_default: isDefault,
    })
    if (err) {
      setError(err)
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="添加店铺">
        <div className="modal-header">
          <h2 className="modal-title">添加店铺</h2>
          <button type="button" className="modal-close" aria-label="关闭" onClick={onClose}>
            <CloseIcon />
          </button>
        </div>
        <form className="modal-form" onSubmit={handleSubmit}>
          <div className="field">
            <label className="field-label" htmlFor="add-shop-name">店铺名称</label>
            <input
              id="add-shop-name"
              className="field-input"
              type="text"
              value={shopName}
              placeholder="例如：我的主店铺"
              onChange={(e) => setShopName(e.target.value)}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="add-client-id">Client ID</label>
            <input
              id="add-client-id"
              className="field-input"
              type="text"
              value={clientId}
              placeholder="Ozon 卖家 Client-Id"
              onChange={(e) => setClientId(e.target.value)}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="add-api-key">API 密钥</label>
            <ApiKeyInput
              value={apiKey}
              onChange={setApiKey}
              placeholder="Ozon 卖家 Api-Key"
              autoFocus
            />
            <p className="field-hint">密钥提交后加密存储，列表仅显示掩码（****XXXX）</p>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="add-currency">货币类型</label>
            <select
              id="add-currency"
              className="field-select"
              value={currency}
              onChange={(e) => setCurrency(e.target.value)}
            >
              <option value="CNY">CNY（人民币）</option>
              <option value="RUB">RUB（卢布）</option>
            </select>
          </div>
          <div className="field">
            <span className="field-label">默认店铺</span>
            <div className="radio-group" role="radiogroup" aria-label="默认店铺">
              <label className="radio-option">
                <input
                  type="radio"
                  name="is-default"
                  checked={isDefault}
                  onChange={() => setIsDefault(true)}
                />
                设为默认店铺（上架默认使用；原默认自动取消）
              </label>
              <label className="radio-option">
                <input
                  type="radio"
                  name="is-default"
                  checked={!isDefault}
                  onChange={() => setIsDefault(false)}
                />
                不作为默认
              </label>
            </div>
          </div>
          {error && (
            <div className="form-error" role="alert">
              <ErrorIcon />
              <span>{error}</span>
            </div>
          )}
          <div className="modal-foot">
            <button type="button" className="btn" onClick={onClose}>取消</button>
            <button type="submit" className="btn btn-primary" disabled={!canSubmit}>
              {submitting ? <span className="spinner" aria-hidden="true" /> : null}
              {submitting ? '添加中…' : '添加店铺'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

/* ── 轮换密钥弹窗 ── */

interface RotateModalProps {
  target: CredentialOut
  onClose: () => void
  onRotate: (apiKey: string) => Promise<string | null>
}

function RotateModal({ target, onClose, onRotate }: RotateModalProps) {
  const [apiKey, setApiKey] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const storeName = target.shop_name || target.ozon_client_id

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (apiKey.trim() === '' || submitting) return
    setSubmitting(true)
    setError('')
    const err = await onRotate(apiKey.trim())
    if (err) {
      setError(err)
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="轮换 API 密钥">
        <div className="modal-header">
          <h2 className="modal-title">轮换 API 密钥</h2>
          <button type="button" className="modal-close" aria-label="关闭" onClick={onClose}>
            <CloseIcon />
          </button>
        </div>
        <form className="modal-form" onSubmit={handleSubmit}>
          <div className="field">
            <label className="field-label">店铺</label>
            <div className="field-input" style={{ display: 'flex', alignItems: 'center' }}>
              <span>{storeName}</span>
              <span className="key-masked" style={{ marginLeft: 'auto' }}>{target.api_key_masked}</span>
            </div>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="rotate-api-key">新 API 密钥</label>
            <ApiKeyInput
              value={apiKey}
              onChange={setApiKey}
              placeholder="输入新密钥"
              autoFocus
            />
            <p className="field-hint">旧密钥将立即吊销，默认店铺标记保持不变</p>
          </div>
          {error && (
            <div className="form-error" role="alert">
              <ErrorIcon />
              <span>{error}</span>
            </div>
          )}
          <div className="modal-foot">
            <button type="button" className="btn" onClick={onClose}>取消</button>
            <button type="submit" className="btn btn-primary" disabled={apiKey.trim() === '' || submitting}>
              {submitting ? <span className="spinner" aria-hidden="true" /> : null}
              {submitting ? '轮换中…' : '确认轮换'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

/* ── 店铺管理页 ── */

export default function Stores() {
  const [stores, setStores] = useState<CredentialOut[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [addOpen, setAddOpen] = useState(false)
  const [rotateTarget, setRotateTarget] = useState<CredentialOut | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [validateMap, setValidateMap] = useState<Record<string, RowValidate>>({})

  const load = useCallback(async () => {
    try {
      const items = await listCredentials()
      setStores(items)
      setLoadError('')
    } catch (err) {
      setLoadError(extractError(err, '加载店铺列表失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const staleStores = stores.filter((c) => {
    const at = effectiveRotationAt(c)
    return at !== null && Date.now() - at > ROTATION_WARN_DAYS * DAY_MS
  })

  async function handleCreate(payload: {
    shop_name: string
    ozon_client_id: string
    api_key: string
    currency: string
    is_default: boolean
  }): Promise<string | null> {
    try {
      await createCredential(payload)
      setAddOpen(false)
      await load()
      return null
    } catch (err) {
      return extractError(err, '添加店铺失败')
    }
  }

  async function handleRotate(apiKey: string): Promise<string | null> {
    if (!rotateTarget) return '店铺不存在'
    try {
      await rotateCredential(rotateTarget.id, { api_key: apiKey })
      setRotateTarget(null)
      await load()
      return null
    } catch (err) {
      return extractError(err, '轮换失败')
    }
  }

  async function handleRevoke(c: CredentialOut) {
    const name = c.shop_name || c.ozon_client_id
    const ok = window.confirm(`吊销「${name}」后该店铺将无法用于上架，且不可恢复。确认吊销？`)
    if (!ok) return
    setBusyId(c.id)
    try {
      await revokeCredential(c.id)
      setStores((list) => list.filter((x) => x.id !== c.id))
      setValidateMap((m) => {
        const next = { ...m }
        delete next[c.id]
        return next
      })
    } catch (err) {
      window.alert(extractError(err, '吊销失败'))
    } finally {
      setBusyId(null)
    }
  }

  async function handleValidate(id: string) {
    setValidateMap((m) => ({ ...m, [id]: { status: 'pending', text: '校验中…' } }))
    try {
      const res = await validateCredential(id)
      setValidateMap((m) => ({
        ...m,
        [id]: {
          status: res.valid ? 'success' : 'fail',
          text: res.valid ? `有效（${res.reason}）` : reasonText(res.reason),
        },
      }))
      if (res.last_validated_at) {
        setStores((list) =>
          list.map((c) => (c.id === id ? { ...c, last_validated_at: res.last_validated_at } : c)),
        )
      }
    } catch {
      setValidateMap((m) => ({ ...m, [id]: { status: 'fail', text: '校验失败：网络或服务错误' } }))
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">店铺管理</h1>
        <span className="page-badge">T11</span>
      </div>

      {staleStores.length > 0 && (
        <div className="rotate-banner" role="alert">
          <WarningIcon />
          <div>
            <strong>建议轮换 API 密钥：</strong>
            {staleStores.map((s) => s.shop_name || s.ozon_client_id).join('、')}
            （超过 {ROTATION_WARN_DAYS} 天未轮换，存在密钥泄露风险）
          </div>
        </div>
      )}

      <div className="toolbar">
        <span className="toolbar-count">共 {stores.length} 个店铺</span>
        <button className="btn btn-primary" onClick={() => setAddOpen(true)}>
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M12 5v14M5 12h14" />
          </svg>
          添加店铺
        </button>
      </div>

      {loading ? (
        <div className="card">
          <div className="empty-state">
            <div className="spinner" style={{ borderColor: 'rgba(0, 91, 255, 0.2)', borderTopColor: 'var(--color-brand)' }} />
            <p className="empty-state-text">加载店铺列表…</p>
          </div>
        </div>
      ) : loadError ? (
        <div className="card">
          <div className="empty-state">
            <div className="form-error" role="alert">
              <ErrorIcon />
              <span>{loadError}</span>
            </div>
            <button className="btn" onClick={() => { setLoading(true); load() }}>
              重试
            </button>
          </div>
        </div>
      ) : stores.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <p className="empty-state-title">暂无店铺</p>
            <p className="empty-state-text">绑定第一个 Ozon 店铺后即可在上架时选择使用</p>
            <button className="btn btn-primary" onClick={() => setAddOpen(true)}>
              添加店铺
            </button>
          </div>
        </div>
      ) : (
        <div className="card stores-table-wrap">
          <table className="stores-table">
            <thead>
              <tr>
                <th>店铺</th>
                <th>Client ID</th>
                <th>API 密钥</th>
                <th>货币</th>
                <th>默认</th>
                <th>状态</th>
                <th>最近轮换</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {stores.map((c) => {
                const validating = validateMap[c.id]?.status === 'pending'
                const busy = busyId === c.id
                const vResult = validateMap[c.id]
                return (
                  <tr key={c.id}>
                    <td>
                      <div className="store-name">{c.shop_name || '未命名店铺'}</div>
                      <div className="store-meta">校验：{formatTime(c.last_validated_at)}</div>
                    </td>
                    <td className="mono">{c.ozon_client_id}</td>
                    <td>
                      <span className="key-masked" title={c.api_key_masked}>
                        {c.api_key_masked}
                      </span>
                    </td>
                    <td>
                      <span className="badge badge-currency">{c.currency}</span>
                    </td>
                    <td>
                      {c.is_default ? (
                        <span className="badge badge-default">★ 默认</span>
                      ) : (
                        <span className="badge badge-currency">—</span>
                      )}
                    </td>
                    <td>
                      <span className="badge badge-ok">正常</span>
                    </td>
                    <td className="mono">{formatTime(c.last_rotated_at)}</td>
                    <td>
                      <div className="row-actions">
                        <button
                          className="row-action"
                          disabled={busy || validating}
                          onClick={() => handleValidate(c.id)}
                        >
                          {validating ? '校验中…' : '校验'}
                        </button>
                        <button
                          className="row-action"
                          disabled={busy || validating}
                          onClick={() => setRotateTarget(c)}
                        >
                          轮换
                        </button>
                        <button
                          className="row-action danger"
                          disabled={busy || validating}
                          onClick={() => handleRevoke(c)}
                        >
                          吊销
                        </button>
                      </div>
                      {vResult && vResult.status !== 'pending' && (
                        <div className="validate-result">
                          <span className={`badge ${vResult.status === 'success' ? 'badge-ok' : 'badge-fail'}`}>
                            {vResult.status === 'success' ? '有效' : '无效'}
                          </span>
                          <span>{vResult.text}</span>
                        </div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {addOpen && (
        <AddStoreModal
          defaultDefault={stores.length === 0}
          onClose={() => setAddOpen(false)}
          onCreate={handleCreate}
        />
      )}
      {rotateTarget && (
        <RotateModal
          target={rotateTarget}
          onClose={() => setRotateTarget(null)}
          onRotate={handleRotate}
        />
      )}
    </div>
  )
}
