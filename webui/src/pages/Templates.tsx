import { useCallback, useEffect, useState } from 'react'
import {
  createTemplate,
  deleteTemplate,
  listCredentials,
  listTemplates,
  patchTemplate,
  setTemplateDefault,
  type CredentialOut,
  type ListingTemplateConfig,
  type ListingTemplateOut,
} from '../api/client'

function extractError(e: unknown, fallback: string): string {
  const err = e as { response?: { data?: { detail?: string } }; message?: string }
  return err?.response?.data?.detail ?? err?.message ?? fallback
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const dt = new Date(iso)
  if (Number.isNaN(dt.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())} ${pad(dt.getHours())}:${pad(dt.getMinutes())}`
}

function pct(v: number | null | undefined): string {
  return v == null ? '自动' : `${Math.round(v * 100)}%`
}

interface TemplateFormState {
  name: string
  description: string
  isDefault: boolean
  marginRate: string
  commissionRate: string
  fxBuffer: string
  offerIdPrefix: string
  followType: string
  stock: string
  warehouseId: string
  /** P1b 店铺差异化覆盖：每行 {credential_id, margin_rate%, stock} */
  storeOverrides: { credentialId: string; marginRate: string; stock: string }[]
}

const EMPTY_FORM: TemplateFormState = {
  name: '',
  description: '',
  isDefault: false,
  marginRate: '',
  commissionRate: '',
  fxBuffer: '',
  offerIdPrefix: '',
  followType: 'hand',
  stock: '',
  warehouseId: '',
  storeOverrides: [],
}

function formToPayload(f: TemplateFormState) {
  const config: ListingTemplateConfig = {}
  if (f.marginRate.trim() !== '') config.margin_rate = Number(f.marginRate) / 100
  if (f.commissionRate.trim() !== '') config.commission_rate = Number(f.commissionRate) / 100
  if (f.fxBuffer.trim() !== '') config.fx_buffer = Number(f.fxBuffer) / 100
  if (f.offerIdPrefix.trim() !== '') config.offer_id_prefix = f.offerIdPrefix.trim()
  if (f.followType === 'api') config.follow_type = 'api'
  if (f.stock.trim() !== '') config.stock = Number(f.stock)
  if (f.warehouseId.trim() !== '') config.warehouse_id = f.warehouseId.trim()
  const storeOverrides: Record<string, Record<string, unknown>> = {}
  for (const row of f.storeOverrides) {
    if (!row.credentialId) continue
    const cfg: Record<string, unknown> = {}
    if (row.marginRate.trim() !== '') cfg.margin_rate = Number(row.marginRate) / 100
    if (row.stock.trim() !== '') cfg.stock = Number(row.stock)
    if (Object.keys(cfg).length > 0) storeOverrides[row.credentialId] = cfg
  }
  return {
    name: f.name.trim(),
    description: f.description.trim(),
    is_default: f.isDefault,
    config,
    ...(Object.keys(storeOverrides).length > 0 ? { store_overrides: storeOverrides } : {}),
  }
}

function templateToForm(t: ListingTemplateOut): TemplateFormState {
  const c = t.config ?? {}
  const overrides = t.store_overrides ?? {}
  return {
    name: t.name,
    description: t.description ?? '',
    isDefault: t.is_default,
    marginRate: c.margin_rate != null ? String(Math.round(c.margin_rate * 100)) : '',
    commissionRate: c.commission_rate != null ? String(Math.round(c.commission_rate * 100)) : '',
    fxBuffer: c.fx_buffer != null ? String(Math.round(c.fx_buffer * 100)) : '',
    offerIdPrefix: c.offer_id_prefix ?? '',
    followType: c.follow_type ?? 'hand',
    stock: c.stock != null ? String(c.stock) : '',
    warehouseId: c.warehouse_id ?? '',
    storeOverrides: Object.entries(overrides).map(([credentialId, cfg]) => ({
      credentialId,
      marginRate: cfg.margin_rate != null ? String(Math.round(cfg.margin_rate * 100)) : '',
      stock: cfg.stock != null ? String(cfg.stock) : '',
    })),
  }
}

interface TemplateModalProps {
  target: ListingTemplateOut | null
  defaultDefault: boolean
  onClose: () => void
  onSave: (form: TemplateFormState, targetId?: string) => Promise<string | null>
}

function TemplateModal({ target, defaultDefault, onClose, onSave }: TemplateModalProps) {
  const [form, setForm] = useState<TemplateFormState>(() =>
    target ? templateToForm(target) : { ...EMPTY_FORM, isDefault: defaultDefault },
  )
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  /** P1b 店铺差异化：店铺列表（选择覆盖目标） */
  const [credentials, setCredentials] = useState<CredentialOut[]>([])

  useEffect(() => {
    listCredentials().then(setCredentials).catch(() => {})
  }, [])

  function set<K extends keyof TemplateFormState>(key: K, value: TemplateFormState[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  /** P1b 店铺覆盖行操作 */
  function setOverrideRow(idx: number, patch: Partial<{ credentialId: string; marginRate: string; stock: string }>) {
    setForm((f) => ({
      ...f,
      storeOverrides: f.storeOverrides.map((r, i) => (i === idx ? { ...r, ...patch } : r)),
    }))
  }

  function addOverrideRow() {
    setForm((f) => ({
      ...f,
      storeOverrides: [...f.storeOverrides, { credentialId: '', marginRate: '', stock: '' }],
    }))
  }

  function removeOverrideRow(idx: number) {
    setForm((f) => ({ ...f, storeOverrides: f.storeOverrides.filter((_, i) => i !== idx) }))
  }

  const canSubmit = form.name.trim() !== '' && !submitting

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit) return
    setSubmitting(true)
    setError('')
    const err = await onSave(form, target?.id)
    if (err) {
      setError(err)
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="上架配置模板">
        <div className="modal-header">
          <h2 className="modal-title">{target ? '编辑上架配置' : '新建上架配置'}</h2>
          <button type="button" className="modal-close" aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </div>
        <form className="modal-form" onSubmit={handleSubmit}>
          <div className="field">
            <label className="field-label" htmlFor="tpl-name">配置名称</label>
            <input
              id="tpl-name"
              className="field-input"
              type="text"
              value={form.name}
              placeholder="例如：高利润模板"
              onChange={(e) => set('name', e.target.value)}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="tpl-desc">备注</label>
            <input
              id="tpl-desc"
              className="field-input"
              type="text"
              value={form.description}
              placeholder="选填"
              onChange={(e) => set('description', e.target.value)}
            />
          </div>
          <div className="field">
            <span className="field-label">默认配置</span>
            <div className="radio-group" role="radiogroup" aria-label="默认配置">
              <label className="radio-option">
                <input
                  type="radio"
                  name="tpl-default"
                  checked={form.isDefault}
                  onChange={() => set('isDefault', true)}
                />
                设为默认（提交未指定模板时自动使用；原默认自动取消）
              </label>
              <label className="radio-option">
                <input
                  type="radio"
                  name="tpl-default"
                  checked={!form.isDefault}
                  onChange={() => set('isDefault', false)}
                />
                不作为默认
              </label>
            </div>
          </div>

          <div className="field">
            <span className="field-label">定价参数（不填则用 worker 默认）</span>
            <div className="form-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
              <div className="field">
                <label className="field-label" htmlFor="tpl-margin">利润率（%）</label>
                <input
                  id="tpl-margin"
                  className="field-input"
                  type="number"
                  min="0"
                  max="100"
                  value={form.marginRate}
                  placeholder="默认 25"
                  onChange={(e) => set('marginRate', e.target.value)}
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="tpl-commission">佣金率（%）</label>
                <input
                  id="tpl-commission"
                  className="field-input"
                  type="number"
                  min="0"
                  max="50"
                  value={form.commissionRate}
                  placeholder="0=自动查真实佣金"
                  onChange={(e) => set('commissionRate', e.target.value)}
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="tpl-fx">汇率缓冲（%）</label>
                <input
                  id="tpl-fx"
                  className="field-input"
                  type="number"
                  min="0"
                  max="50"
                  value={form.fxBuffer}
                  placeholder="默认 5"
                  onChange={(e) => set('fxBuffer', e.target.value)}
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="tpl-prefix">货号前缀</label>
                <input
                  id="tpl-prefix"
                  className="field-input"
                  type="text"
                  value={form.offerIdPrefix}
                  placeholder="如 W1（同店铺多批次防重）"
                  onChange={(e) => set('offerIdPrefix', e.target.value)}
                />
              </div>
            </div>
          </div>

          <div className="field">
            <label className="field-label" htmlFor="tpl-follow">跟卖方式</label>
            <select
              id="tpl-follow"
              className="field-select"
              value={form.followType}
              onChange={(e) => set('followType', e.target.value)}
            >
              <option value="hand">hand（防侵权，默认）</option>
              <option value="api">api（强制 1:1 复制，有下架风险）</option>
            </select>
          </div>
          <div className="form-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
            <div className="field">
              <label className="field-label" htmlFor="tpl-stock">库存</label>
              <input
                id="tpl-stock"
                className="field-input"
                type="number"
                min="0"
                value={form.stock}
                placeholder="选填"
                onChange={(e) => set('stock', e.target.value)}
              />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="tpl-warehouse">仓库</label>
              <input
                id="tpl-warehouse"
                className="field-input"
                type="text"
                value={form.warehouseId}
                placeholder="选填"
                onChange={(e) => set('warehouseId', e.target.value)}
              />
            </div>
          </div>

          <div className="field">
            <span className="field-label">店铺差异化覆盖（选填）</span>
            <p className="field-hint">指定店铺用覆盖参数（利润率/库存），未覆盖店铺用上方全局参数</p>
            {form.storeOverrides.map((row, idx) => (
              <div key={idx} className="form-grid" style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr auto', gap: '8px', marginBottom: '8px' }}>
                <select
                  className="field-select"
                  value={row.credentialId}
                  onChange={(e) => setOverrideRow(idx, { credentialId: e.target.value })}
                >
                  <option value="">选择店铺</option>
                  {credentials.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.shop_name || c.ozon_client_id}（{c.ozon_client_id}）
                    </option>
                  ))}
                </select>
                <input
                  className="field-input"
                  type="number"
                  min="0"
                  max="100"
                  placeholder="利润率%"
                  value={row.marginRate}
                  onChange={(e) => setOverrideRow(idx, { marginRate: e.target.value })}
                />
                <input
                  className="field-input"
                  type="number"
                  min="0"
                  placeholder="库存"
                  value={row.stock}
                  onChange={(e) => setOverrideRow(idx, { stock: e.target.value })}
                />
                <button type="button" className="btn btn-danger-text" onClick={() => removeOverrideRow(idx)}>
                  删除
                </button>
              </div>
            ))}
            <button type="button" className="btn" onClick={addOverrideRow}>
              + 添加店铺覆盖
            </button>
          </div>

          {error && (
            <div className="form-error" role="alert">
              <span>{error}</span>
            </div>
          )}
          <div className="modal-foot">
            <button type="button" className="btn" onClick={onClose}>取消</button>
            <button type="submit" className="btn btn-primary" disabled={!canSubmit}>
              {submitting ? <span className="spinner" aria-hidden="true" /> : null}
              {submitting ? '保存中…' : '保存'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function Templates() {
  const [templates, setTemplates] = useState<ListingTemplateOut[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [modalOpen, setModalOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<ListingTemplateOut | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const items = await listTemplates()
      setTemplates(items)
      setLoadError('')
    } catch (err) {
      setLoadError(extractError(err, '加载上架配置失败'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function handleSave(form: TemplateFormState, targetId?: string): Promise<string | null> {
    try {
      const payload = formToPayload(form)
      if (targetId) {
        await patchTemplate(targetId, payload)
      } else {
        await createTemplate(payload)
      }
      setModalOpen(false)
      setEditTarget(null)
      await load()
      return null
    } catch (err) {
      return extractError(err, '保存上架配置失败')
    }
  }

  async function handleSetDefault(t: ListingTemplateOut) {
    setBusyId(t.id)
    try {
      await setTemplateDefault(t.id)
      await load()
    } catch (err) {
      window.alert(extractError(err, '设默认失败'))
    } finally {
      setBusyId(null)
    }
  }

  async function handleDelete(t: ListingTemplateOut) {
    const ok = window.confirm(`删除上架配置「${t.name}」？删除后不可恢复。`)
    if (!ok) return
    setBusyId(t.id)
    try {
      await deleteTemplate(t.id)
      await load()
    } catch (err) {
      window.alert(extractError(err, '删除失败'))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">上架配置</h1>
        <span className="page-badge">P0-1</span>
      </div>

      <div className="toolbar">
        <span className="toolbar-count">共 {templates.length} 个配置</span>
        <button className="btn btn-primary" onClick={() => { setEditTarget(null); setModalOpen(true) }}>
          新建配置
        </button>
      </div>

      {loading ? (
        <div className="card">
          <div className="empty-state">
            <div className="spinner" style={{ borderColor: 'rgba(0, 91, 255, 0.2)', borderTopColor: 'var(--color-brand)' }} />
            <p className="empty-state-text">加载上架配置…</p>
          </div>
        </div>
      ) : loadError ? (
        <div className="card">
          <div className="empty-state">
            <div className="form-error" role="alert"><span>{loadError}</span></div>
            <button className="btn" onClick={() => { setLoading(true); load() }}>重试</button>
          </div>
        </div>
      ) : templates.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <p className="empty-state-title">暂无上架配置</p>
            <p className="empty-state-text">创建配置模板后，提交上架时可一键套用定价/货号前缀等参数</p>
            <button className="btn btn-primary" onClick={() => { setEditTarget(null); setModalOpen(true) }}>
              新建配置
            </button>
          </div>
        </div>
      ) : (
        <div className="card stores-table-wrap">
          <table className="stores-table">
            <thead>
              <tr>
                <th>配置名称</th>
                <th>默认</th>
                <th>利润率</th>
                <th>佣金率</th>
                <th>汇率缓冲</th>
                <th>货号前缀</th>
                <th>库存/仓库</th>
                <th>备注</th>
                <th>更新时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {templates.map((t) => {
                const busy = busyId === t.id
                const c = t.config ?? {}
                return (
                  <tr key={t.id}>
                    <td>
                      <div className="store-name">{t.name}</div>
                      <div className="store-meta">跟卖：{c.follow_type ?? 'hand'}</div>
                    </td>
                    <td>
                      {t.is_default ? (
                        <span className="badge badge-default">★ 默认</span>
                      ) : (
                        <span className="badge badge-currency">—</span>
                      )}
                    </td>
                    <td>{pct(c.margin_rate)}</td>
                    <td>{c.commission_rate == null || c.commission_rate === 0 ? '自动' : pct(c.commission_rate)}</td>
                    <td>{pct(c.fx_buffer)}</td>
                    <td><span className="mono">{c.offer_id_prefix ?? '—'}</span></td>
                    <td>
                      {c.stock != null ? `${c.stock}` : '—'}
                      {c.warehouse_id ? ` / ${c.warehouse_id}` : ''}
                    </td>
                    <td>{t.description || '—'}</td>
                    <td className="mono">{formatTime(t.updated_at)}</td>
                    <td>
                      <div className="row-actions">
                        {!t.is_default && (
                          <button className="row-action" disabled={busy} onClick={() => handleSetDefault(t)}>
                            设默认
                          </button>
                        )}
                        <button className="row-action" disabled={busy} onClick={() => { setEditTarget(t); setModalOpen(true) }}>
                          编辑
                        </button>
                        <button className="row-action danger" disabled={busy} onClick={() => handleDelete(t)}>
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {modalOpen && (
        <TemplateModal
          target={editTarget}
          defaultDefault={templates.length === 0}
          onClose={() => { setModalOpen(false); setEditTarget(null) }}
          onSave={handleSave}
        />
      )}
    </div>
  )
}
