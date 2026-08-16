import { useCallback, useEffect, useState } from 'react'
import {
  createMxouKey,
  getStoredToken,
  listMxouKeys,
  revokeMxouKey,
  selectMxouKey,
  type MxouKeyItem,
} from '../api/client'
import { setToken } from '../stores/auth'

/**
 * T4 密钥管理弹窗 —— 列表（脱敏）/ 复制激活 / 新建（完整 key 仅展示一次）/ 吊销。
 * 入口：Layout 侧边栏余额 badge 点击。全部走 Bearer 鉴权；
 * token 未激活（会话有但无 token）→ 提示先通过 API Key 登录或激活密钥。
 */

function extractError(err: unknown, fallback: string): string {
  const resp = (err as { response?: { data?: { detail?: string } } } | null)?.response
  return resp?.data?.detail || fallback
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  )
}

function CopyIcon() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8">
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15V5a2 2 0 012-2h10" />
    </svg>
  )
}

export default function KeyManager({ onClose }: { onClose: () => void }) {
  const token = getStoredToken()
  const [keys, setKeys] = useState<MxouKeyItem[] | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [newKey, setNewKey] = useState<{ id: string; name: string; key: string } | null>(null)
  const [confirmRevoke, setConfirmRevoke] = useState<MxouKeyItem | null>(null)

  const flash = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice(''), 2500)
  }

  const load = useCallback(async () => {
    setError('')
    try {
      const data = await listMxouKeys()
      setKeys(data)
    } catch (err) {
      setError(extractError(err, '加载密钥列表失败'))
    }
  }, [])

  useEffect(() => {
    if (token) {
      load()
    }
  }, [token, load])

  async function handleCopy(keyId: string) {
    if (!token) return
    setBusyId(keyId)
    setError('')
    try {
      const { key } = await selectMxouKey(keyId)
      await navigator.clipboard.writeText(key)
      setToken(key)
      flash('已复制到剪贴板')
    } catch (err) {
      setError(extractError(err, '获取密钥失败'))
    } finally {
      setBusyId(null)
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    if (!token) return
    setCreating(true)
    setError('')
    try {
      const created = await createMxouKey(name.trim() || 'default')
      setNewKey(created)
      setName('')
      flash('密钥已创建')
      await load()
    } catch (err) {
      setError(extractError(err, '创建密钥失败'))
    } finally {
      setCreating(false)
    }
  }

  async function handleRevoke() {
    if (!confirmRevoke || !token) return
    setBusyId(confirmRevoke.id)
    setError('')
    try {
      await revokeMxouKey(confirmRevoke.id)
      setConfirmRevoke(null)
      flash('密钥已吊销')
      setKeys((prev) => (prev ?? []).filter((k) => k.id !== confirmRevoke.id))
    } catch (err) {
      setError(extractError(err, '吊销密钥失败'))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal key-manager-modal" role="dialog" aria-modal="true" aria-label="密钥管理">
        <div className="modal-header">
          <h2 className="modal-title">密钥管理</h2>
          <button type="button" className="modal-close" aria-label="关闭" onClick={onClose}>
            <CloseIcon />
          </button>
        </div>

        {!token ? (
          <div className="empty-state">
            <div className="placeholder-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.6">
                <circle cx="8" cy="14" r="4" />
                <path d="M11 11L19.5 2.5M16 7l3 3" strokeLinecap="round" />
              </svg>
            </div>
            <p className="empty-state-title">尚未激活 API Key</p>
            <p className="empty-state-text">请先通过 API Key 登录或激活密钥，才能管理密钥。</p>
          </div>
        ) : (
          <>
            {error && (
              <div className="form-error" role="alert">
                {error}
              </div>
            )}
            {notice && (
              <div className="key-manager-notice" role="status">
                {notice}
              </div>
            )}

            {keys === null ? (
              <div className="empty-state">
                <div className="spinner-inline" aria-hidden="true" />
                <p className="empty-state-text">加载密钥…</p>
              </div>
            ) : keys.length === 0 ? (
              <div className="empty-state">
                <p className="empty-state-text">该账号暂无 API Key，可在下方新建。</p>
              </div>
            ) : (
              <ul className="key-manager-list">
                {keys.map((k) => (
                  <li className="key-manager-item" key={k.id}>
                    <div className="key-manager-item-body">
                      <div className="key-manager-item-head">
                        <span className="key-manager-name" title={k.name}>
                          {k.name || '（未命名）'}
                        </span>
                        <span className={`badge ${k.status === 1 ? 'badge-ok' : 'badge-fail'}`}>
                          {k.status === 1 ? '启用' : '停用'}
                        </span>
                      </div>
                      <span className="key-manager-id mono" title={k.id}>
                        {k.id}
                      </span>
                    </div>
                    <button
                      type="button"
                      className="btn btn-small"
                      disabled={busyId === k.id}
                      onClick={() => handleCopy(k.id)}
                    >
                      <CopyIcon />
                      {busyId === k.id ? '复制中…' : '复制'}
                    </button>
                    <button
                      type="button"
                      className="btn btn-small btn-danger-text"
                      disabled={busyId === k.id}
                      onClick={() => setConfirmRevoke(k)}
                    >
                      吊销
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <form className="modal-form key-manager-create" onSubmit={handleCreate}>
              <label className="field" htmlFor="key-name">
                <span className="field-label">新建密钥名称</span>
                <input
                  id="key-name"
                  className="field-input"
                  type="text"
                  placeholder="如：prod / dev"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  spellCheck={false}
                />
              </label>
              <button type="submit" className="btn btn-primary" disabled={creating}>
                {creating ? '创建中…' : '新建密钥'}
              </button>
            </form>

            {newKey && (
              <div className="key-manager-new">
                <div className="key-manager-new-head">
                  <span className="key-manager-new-title">密钥已创建（仅此一次展示，请立即复制保存）</span>
                  <button type="button" className="modal-close" aria-label="关闭" onClick={() => setNewKey(null)}>
                    <CloseIcon />
                  </button>
                </div>
                <pre className="key-manager-new-key mono">{newKey.key}</pre>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={async () => {
                    await navigator.clipboard.writeText(newKey.key)
                    setToken(newKey.key)
                    flash('已复制到剪贴板')
                  }}
                >
                  <CopyIcon />
                  复制完整密钥
                </button>
              </div>
            )}

            {confirmRevoke && (
              <div className="modal-mask" role="dialog" aria-modal="true" aria-label="确认吊销">
                <div className="modal">
                  <h3 className="modal-title">吊销密钥</h3>
                  <p className="modal-text">
                    将吊销密钥「{confirmRevoke.name || confirmRevoke.id}」，吊销后不可恢复。确认继续？
                  </p>
                  <div className="modal-actions">
                    <button type="button" className="btn" onClick={() => setConfirmRevoke(null)}>
                      取消
                    </button>
                    <button type="button" className="btn btn-danger" onClick={handleRevoke} disabled={busyId !== null}>
                      确认吊销
                    </button>
                  </div>
                </div>
              </div>
            )}
          </>
        )}

        <div className="modal-foot">
          <button type="button" className="btn" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}
