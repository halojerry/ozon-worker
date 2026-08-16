import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  cancelOrder,
  getMessageTemplates,
  getOrderLabel,
  getOrderNotes,
  listCancelReasons,
  listOrderMessages,
  listCredentials,
  listOrders,
  sendOrderMessage,
  shipOrder,
  upsertOrderNotes,
  type CredentialOut,
  type MessageTemplateOut,
  type OrderMessageRecord,
  type OrderNoteOut,
  type OrderOut,
  type OrderStatus,
} from '../api/client'

const STATUS_TABS: { key: string; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'pending', label: '待处理' },
  { key: 'awaiting', label: '待备货' },
  { key: 'waiting', label: '待发运' },
  { key: 'delivering', label: '运输中' },
  { key: 'delivered', label: '已签收' },
  { key: 'cancelled', label: '已取消' },
  { key: 'other', label: '其他' },
]

const STATUS_META: Record<OrderStatus, { label: string; className: string }> = {
  pending: { label: '待处理', className: 'status-muted' },
  awaiting: { label: '待备货', className: 'status-uploading' },
  waiting: { label: '待发运', className: 'status-uploading' },
  delivering: { label: '运输中', className: 'status-uploading' },
  delivered: { label: '已签收', className: 'status-published' },
  cancelled: { label: '已取消', className: 'status-failed' },
  other: { label: '其他', className: 'status-muted' },
}

function statusMeta(status: OrderStatus) {
  return STATUS_META[status] ?? STATUS_META.other
}

function fmtTime(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return `₽${v.toFixed(2)}`
}

function extractError(err: unknown, fallback: string): string {
  const resp = (err as { response?: { data?: { detail?: string } } } | null)?.response
  return resp?.data?.detail || fallback
}

/* ── CSV 导出（当前筛选结果，UTF-8 BOM） ── */

function exportCsv(orders: OrderOut[]): void {
  const esc = (v: unknown): string => {
    const s = v === null || v === undefined ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const header = ['货件编号', '状态', '商品', '件数', '金额(₽)', '费用(₽)', '利润(₽)', '仓库', '配送方式', '取消原因', '下单时间']
  const rows = orders.map((o) => [
    o.posting_number,
    statusMeta(o.status).label,
    o.products[0]?.name ?? '',
    o.product_count,
    o.total_amount,
    o.commission_amount,
    o.profit ?? '',
    o.warehouse,
    o.delivery_method,
    o.cancel_reason,
    fmtTime(o.created_at),
  ])
  const csv = '\uFEFF' + [header, ...rows].map((r) => r.map(esc).join(',')).join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  const stamp = new Date().toISOString().slice(0, 10)
  a.href = url
  a.download = `订单-${stamp}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

/* ── P1-1 订单备注弹窗：货源 + 采购信息（本地元数据） ── */

interface NotesModalProps {
  postingNumber: string
  credentialId: string
  onClose: () => void
  onSaved: (notes: OrderNoteOut) => void
}

function NotesModal({ postingNumber, credentialId, onClose, onSaved }: NotesModalProps) {
  const [notes, setNotes] = useState<OrderNoteOut | null>(null)
  const [form, setForm] = useState({ source_url: '', source_cost: '', source_remark: '', purchase_no: '', purchase_carrier: '', purchase_tracking: '' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    getOrderNotes(postingNumber)
      .then((n) => {
        setNotes(n)
        setForm({
          source_url: n.source_url ?? '',
          source_cost: n.source_cost != null ? String(n.source_cost) : '',
          source_remark: n.source_remark ?? '',
          purchase_no: n.purchase_no ?? '',
          purchase_carrier: n.purchase_carrier ?? '',
          purchase_tracking: n.purchase_tracking ?? '',
        })
      })
      .catch((e) => setError(extractError(e, '加载订单备注失败')))
  }, [postingNumber])

  function set<K extends keyof typeof form>(key: K, value: string) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function handleSave() {
    setSaving(true)
    setError('')
    try {
      const saved = await upsertOrderNotes(postingNumber, {
        source_url: form.source_url.trim(),
        source_cost: form.source_cost.trim() !== '' ? Number(form.source_cost) : null,
        source_remark: form.source_remark.trim(),
        purchase_no: form.purchase_no.trim(),
        purchase_carrier: form.purchase_carrier.trim(),
        purchase_tracking: form.purchase_tracking.trim(),
      })
      onSaved(saved)
      onClose()
    } catch (e) {
      setError(extractError(e, '保存失败'))
      setSaving(false)
    }
  }

  async function handleLabel() {
    setError('')
    try {
      const label = await getOrderLabel(postingNumber, credentialId)
      const bytes = atob(label.label_base64)
      const arr = new Uint8Array(bytes.length)
      for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i)
      const blob = new Blob([arr], { type: label.content_type || 'application/pdf' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${postingNumber}.pdf`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(extractError(e, '面单下载失败'))
    }
  }

  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="订单备注">
        <div className="modal-header">
          <h2 className="modal-title">订单备注</h2>
          <button type="button" className="modal-close" aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="modal-body">
          <div className="sub-history-title mono">{postingNumber}</div>
          {!notes && !error && (
            <div className="empty-state"><div className="spinner-inline" /><p>加载备注…</p></div>
          )}
          {error && <div className="form-error" role="alert"><span>{error}</span></div>}
          <div className="field">
            <span className="field-label">货源信息</span>
            <input className="field-input" placeholder="货源地址（1688 等链接）" value={form.source_url} onChange={(e) => set('source_url', e.target.value)} />
            <div className="form-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginTop: '8px' }}>
              <input className="field-input" type="number" min="0" placeholder="货源价格（CNY）" value={form.source_cost} onChange={(e) => set('source_cost', e.target.value)} />
              <input className="field-input" placeholder="货源备注" value={form.source_remark} onChange={(e) => set('source_remark', e.target.value)} />
            </div>
          </div>
          <div className="field">
            <span className="field-label">采购信息</span>
            <input className="field-input" placeholder="采购单号" value={form.purchase_no} onChange={(e) => set('purchase_no', e.target.value)} />
            <div className="form-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginTop: '8px' }}>
              <input className="field-input" placeholder="采购快递" value={form.purchase_carrier} onChange={(e) => set('purchase_carrier', e.target.value)} />
              <input className="field-input" placeholder="采购快递单号" value={form.purchase_tracking} onChange={(e) => set('purchase_tracking', e.target.value)} />
            </div>
          </div>
        </div>
        <div className="modal-foot">
          <button type="button" className="btn" onClick={handleLabel} disabled={saving}>
            下载面单
          </button>
          <button type="button" className="btn" onClick={onClose} disabled={saving}>取消</button>
          <button type="button" className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}

/* ── P1-2 取消订单弹窗：拉取原因 → 下拉选择 → 确认取消 ── */

interface CancelModalProps {
  order: OrderOut
  onClose: () => void
  onCancelled: () => void
}

function CancelModal({ order, onClose, onCancelled }: CancelModalProps) {
  const [reasons, setReasons] = useState<{ id: number; title: string }[] | null>(null)
  const [reasonId, setReasonId] = useState('')
  const [error, setError] = useState('')
  const [cancelling, setCancelling] = useState(false)

  useEffect(() => {
    listCancelReasons(order.posting_number)
      .then((rs) => {
        setReasons(rs)
        if (rs.length > 0) setReasonId(String(rs[0].id))
      })
      .catch((e) => setError(extractError(e, '加载取消原因失败')))
  }, [order.posting_number])

  async function handleCancel() {
    if (!reasonId) {
      setError('请选择取消原因')
      return
    }
    setCancelling(true)
    setError('')
    try {
      await cancelOrder(order.posting_number, Number(reasonId))
      onCancelled()
      onClose()
    } catch (e) {
      setError(extractError(e, '取消订单失败'))
      setCancelling(false)
    }
  }

  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="取消订单">
        <div className="modal-header">
          <h2 className="modal-title">取消订单</h2>
          <button type="button" className="modal-close" aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="modal-body">
          <div className="sub-history-title mono">{order.posting_number}</div>
          <p className="modal-text">取消订单将对 Ozon 实际生效，请确认。</p>
          {!reasons && !error && (
            <div className="empty-state"><div className="spinner-inline" /><p>加载取消原因…</p></div>
          )}
          {error && <div className="form-error" role="alert"><span>{error}</span></div>}
          {reasons && reasons.length === 0 && !error && (
            <div className="form-error" role="alert"><span>该订单没有可用的取消原因</span></div>
          )}
          {reasons && reasons.length > 0 && (
            <div className="field">
              <label className="field-label" htmlFor="cancel-reason">取消原因</label>
              <select
                id="cancel-reason"
                className="field-select"
                value={reasonId}
                onChange={(e) => setReasonId(e.target.value)}
              >
                {reasons.map((r) => (
                  <option key={r.id} value={r.id}>{r.title}</option>
                ))}
              </select>
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button type="button" className="btn" onClick={onClose} disabled={cancelling}>取消</button>
          <button
            type="button"
            className="btn btn-danger"
            onClick={handleCancel}
            disabled={cancelling || !reasonId}
          >
            {cancelling ? '取消中…' : '确认取消订单'}
          </button>
        </div>
      </div>
    </div>
  )
}


/* ── P2c 发消息弹窗：模板选择 + 预览编辑 + 发送 ── */

interface MessageModalProps {
  order: OrderOut
  onClose: () => void
  onSent: () => void
}

function MessageModal({ order, onClose, onSent }: MessageModalProps) {
  const [templates, setTemplates] = useState<MessageTemplateOut[] | null>(null)
  const [templateKey, setTemplateKey] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    getMessageTemplates()
      .then((tpls) => {
        setTemplates(tpls)
        if (tpls.length > 0) {
          setTemplateKey(tpls[0].key)
          setMessage(fillTemplate(tpls[0].text, order))
        }
      })
      .catch((e) => setError(extractError(e, '加载模板失败')))
  }, [order])

  function fillTemplate(text: string, o: OrderOut): string {
    const product = o.products[0]?.name || o.posting_number
    return text.replace(/\[货件编号\]/g, o.posting_number).replace(/\[商品名称\]/g, product.slice(0, 60))
  }

  function selectTemplate(key: string) {
    setTemplateKey(key)
    const tpl = templates?.find((t) => t.key === key)
    if (tpl) setMessage(fillTemplate(tpl.text, order))
  }

  async function handleSend() {
    if (!message.trim()) {
      setError('消息内容不能为空')
      return
    }
    setBusy(true)
    setError('')
    try {
      await sendOrderMessage(order.posting_number, message, templateKey)
      onSent()
      onClose()
    } catch (e) {
      setError(extractError(e, '发送失败'))
      setBusy(false)
    }
  }

  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="发送消息">
        <div className="modal-header">
          <h2 className="modal-title">发送消息</h2>
          <button type="button" className="modal-close" aria-label="关闭" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          <div className="sub-history-title mono">{order.posting_number}</div>
          {error && <div className="form-error" role="alert"><span>{error}</span></div>}
          {templates && templates.length > 0 && (
            <div className="field">
              <label className="field-label" htmlFor="msg-template">消息模板</label>
              <select id="msg-template" className="field-select" value={templateKey} onChange={(e) => selectTemplate(e.target.value)}>
                {templates.map((t) => (
                  <option key={t.key} value={t.key}>{t.name}</option>
                ))}
              </select>
            </div>
          )}
          <div className="field">
            <label className="field-label" htmlFor="msg-text">消息内容（俄语）</label>
            <textarea id="msg-text" className="field-input images-textarea" rows={5} value={message} onChange={(e) => setMessage(e.target.value)} />
          </div>
          <p className="field-hint">将发送 Ozon 站内信给买家（真实生效）</p>
        </div>
        <div className="modal-foot">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>取消</button>
          <button type="button" className="btn btn-primary" onClick={handleSend} disabled={busy}>
            {busy ? '发送中…' : '发送'}
          </button>
        </div>
      </div>
    </div>
  )
}

/* ── P2c 消息记录弹窗 ── */

function MessageLogModal({ onClose }: { onClose: () => void }) {
  const [records, setRecords] = useState<OrderMessageRecord[] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    listOrderMessages()
      .then((r) => setRecords(r.items))
      .catch((e) => setError(extractError(e, '加载消息记录失败')))
  }, [])

  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal sub-history-modal" role="dialog" aria-modal="true" aria-label="消息记录">
        <div className="modal-header">
          <h2 className="modal-title">消息记录</h2>
          <button type="button" className="modal-close" aria-label="关闭" onClick={onClose}>×</button>
        </div>
        {records === null ? (
          <div className="empty-state"><div className="spinner-inline" /><p>加载记录…</p></div>
        ) : error ? (
          <div className="empty-state"><div className="form-error" role="alert"><span>{error}</span></div></div>
        ) : records.length === 0 ? (
          <div className="empty-state"><p className="empty-state-title">暂无消息记录</p></div>
        ) : (
          <div className="modal-body">
            <table className="stores-table">
              <thead>
                <tr>
                  <th>货件编号</th>
                  <th>模板</th>
                  <th>状态</th>
                  <th>时间</th>
                </tr>
              </thead>
              <tbody>
                {records.map((r, i) => (
                  <tr key={i}>
                    <td className="mono">{r.posting_number}</td>
                    <td>{r.template_key}</td>
                    <td>
                      <span className={`badge ${r.status === 'sent' ? 'badge-ok' : 'badge-fail'}`}>
                        {r.status === 'sent' ? '已发送' : '失败'}
                      </span>
                    </td>
                    <td className="col-time">{fmtTime(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="modal-foot">
          <button type="button" className="btn" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}

export default function Orders() {
  const [orders, setOrders] = useState<OrderOut[]>([])
  const [credentials, setCredentials] = useState<CredentialOut[]>([])
  const [credentialId, setCredentialId] = useState('')
  const [statusTab, setStatusTab] = useState('all')
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [detail, setDetail] = useState<OrderOut | null>(null)
  /** P1-1 备注弹窗目标订单 + 备注更新回调 */
  const [notesTarget, setNotesTarget] = useState<OrderOut | null>(null)
  const [notesMap, setNotesMap] = useState<Record<string, OrderNoteOut>>({})
  /** P1-2 取消弹窗目标 + 操作提示 */
  const [cancelTarget, setCancelTarget] = useState<OrderOut | null>(null)
  const [notice, setNotice] = useState('')
  /** P2c 发消息目标 + 消息记录开关 */
  const [messageTarget, setMessageTarget] = useState<OrderOut | null>(null)
  const [logOpen, setLogOpen] = useState(false)

  /* 店铺列表（默认店铺默认选中） */
  useEffect(() => {
    listCredentials()
      .then((creds) => {
        setCredentials(creds)
        const def = creds.find((c) => c.is_default)
        setCredentialId((prev) => prev || def?.id || '')
      })
      .catch(() => {
        setLoadError('加载店铺列表失败')
        setLoading(false)
      })
  }, [])

  const load = useCallback(
    async (silent = false) => {
      if (!credentialId) return
      if (!silent) setLoading(true)
      try {
        const data = await listOrders({
          credential_id: credentialId,
          status: statusTab === 'all' ? undefined : statusTab,
          limit: 200,
          since_days: 30,
        })
        setOrders(data.items)
        setLoadError('')
      } catch (err) {
        setLoadError(extractError(err, '加载订单失败'))
      } finally {
        setLoading(false)
      }
    },
    [credentialId, statusTab],
  )

  useEffect(() => {
    if (credentialId) load()
  }, [credentialId, statusTab, load])

  const counts = useMemo(() => {
    const m = new Map<string, number>()
    for (const o of orders) m.set(o.status, (m.get(o.status) ?? 0) + 1)
    return m
  }, [orders])

  /** P1-2 备货发货：确认后调 ship → 提示 + 刷新 */
  const handleShip = async (o: OrderOut) => {
    const ok = window.confirm(`确认备货发货 ${o.posting_number}？\n备货后订单进入待发运（真实生效）。`)
    if (!ok) return
    try {
      await shipOrder(o.posting_number)
      setNotice(`备货成功：${o.posting_number}`)
      load()
    } catch (e) {
      setNotice(`备货失败：${extractError(e, '备货失败')}`)
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <h1 className="page-title">订单管理</h1>
        <span className="page-badge">{orders.length > 0 ? `${orders.length} 条` : 'P0-4'}</span>
      </header>

      {notice && (
        <div className="alert alert-info">
          <span>{notice}</span>
          <span className="alert-actions">
            <button className="btn btn-sm" onClick={() => setNotice('')}>关闭</button>
          </span>
        </div>
      )}

      <div className="toolbar">
        <select
          className="form-select"
          style={{ width: '220px' }}
          value={credentialId}
          onChange={(e) => setCredentialId(e.target.value)}
        >
          <option value="">请选择店铺</option>
          {credentials.map((c) => (
            <option key={c.id} value={c.id}>
              {c.shop_name || c.ozon_client_id}（{c.ozon_client_id}
              {c.is_default ? ' · 默认' : ''}）
            </option>
          ))}
        </select>
        <button className="btn" disabled={orders.length === 0} onClick={() => exportCsv(orders)}>
          导出 CSV
        </button>
        <button className="btn" onClick={() => setLogOpen(true)}>
          消息记录
        </button>
        <span className="toolbar-spacer" />
        <button className="btn btn-ghost" onClick={() => load()} disabled={loading}>
          {loading ? '加载中…' : '刷新'}
        </button>
      </div>

      {/* 状态 tab */}
      <div className="order-tabs">
        {STATUS_TABS.map((t) => {
          const count = t.key === 'all' ? orders.length : counts.get(t.key) ?? 0
          return (
            <button
              key={t.key}
              className={`order-tab${statusTab === t.key ? ' active' : ''}`}
              onClick={() => setStatusTab(t.key)}
            >
              {t.label}
              {count > 0 ? ` (${count})` : ''}
            </button>
          )
        })}
      </div>

      {!credentialId && !loading ? (
        <div className="card">
          <div className="empty-state">
            <p className="empty-state-title">未配置店铺</p>
            <p className="empty-state-text">请先在店铺管理绑定 Ozon 店铺，或选择上方店铺下拉</p>
            <Link className="btn btn-primary" to="/stores">去店铺管理</Link>
          </div>
        </div>
      ) : loading ? (
        <div className="card">
          <div className="empty-state">
            <div className="spinner" style={{ borderColor: 'rgba(0, 91, 255, 0.2)', borderTopColor: 'var(--color-brand)' }} />
            <p className="empty-state-text">加载订单…</p>
          </div>
        </div>
      ) : loadError ? (
        <div className="card">
          <div className="empty-state">
            <div className="form-error" role="alert"><span>{loadError}</span></div>
            <button className="btn" onClick={() => load()}>重试</button>
          </div>
        </div>
      ) : orders.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <p className="empty-state-title">暂无订单</p>
            <p className="empty-state-text">该店铺最近 30 天没有匹配的 FBS 订单</p>
          </div>
        </div>
      ) : (
        <div className="card stores-table-wrap">
          <table className="stores-table">
            <thead>
              <tr>
                <th>货件编号</th>
                <th>状态</th>
                <th>商品信息</th>
                <th className="col-price">金额(₽)</th>
                <th className="col-price">费用(₽)</th>
                <th className="col-price">利润(₽)</th>
                <th>仓库 / 配送</th>
                <th>备注</th>
                <th className="col-time">下单时间</th>
                <th className="col-actions">操作</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => {
                const meta = statusMeta(o.status)
                return (
                  <tr key={o.posting_number}>
                    <td><span className="mono">{o.posting_number}</span></td>
                    <td>
                      <span className={`status-badge ${meta.className}`}>{meta.label}</span>
                      {o.cancel_reason && (
                        <div className="task-error-hint" title={o.cancel_reason}>
                          {o.cancel_reason.slice(0, 30)}
                        </div>
                      )}
                    </td>
                    <td className="col-title">
                      <span className="draft-title" title={o.products[0]?.name}>
                        {o.products[0]?.name || '（无商品信息）'}
                      </span>
                      <span className="task-product-meta mono">
                        {o.product_count > 0 ? `共 ${o.product_count} 件 · ${o.products.length} 行` : ''}
                        {o.products[0]?.offer_id ? ` · ${o.products[0].offer_id}` : ''}
                      </span>
                    </td>
                    <td className="col-price">{fmtMoney(o.total_amount)}</td>
                    <td className="col-price">{fmtMoney(o.commission_amount)}</td>
                    <td className="col-price">{fmtMoney(o.profit)}</td>
                    <td>
                      <div className="task-account">
                        <span className="task-shop">{o.delivery_method || '—'}</span>
                        <span className="task-client mono">{o.warehouse || '—'}</span>
                      </div>
                    </td>
                    <td>
                      {notesMap[o.posting_number]?.source_url ? (
                        <span className="badge badge-update" title={`货源：${notesMap[o.posting_number].source_url}`}>
                          已备注
                        </span>
                      ) : (
                        <span className="task-source-empty">—</span>
                      )}
                    </td>
                    <td className="col-time">{fmtTime(o.created_at)}</td>
                    <td className="col-actions">
                      <button className="btn btn-small" onClick={() => setDetail(o)}>
                        详情
                      </button>
                      <button className="btn btn-small" onClick={() => setNotesTarget(o)}>
                        备注
                      </button>
                      {(o.status === 'awaiting' || o.status === 'waiting') && (
                        <button className="btn btn-small btn-primary" onClick={() => handleShip(o)}>
                          备货发货
                        </button>
                      )}
                      <button className="btn btn-small" onClick={() => setMessageTarget(o)}>
                        发消息
                      </button>
                      <button className="btn btn-small btn-danger-text" onClick={() => setCancelTarget(o)}>
                        取消
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {detail && (
        <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && setDetail(null)}>
          <div className="modal" role="dialog" aria-modal="true" aria-label="订单详情">
            <div className="modal-header">
              <h2 className="modal-title">订单详情</h2>
              <button type="button" className="modal-close" aria-label="关闭" onClick={() => setDetail(null)}>
                ×
              </button>
            </div>
            <div className="modal-body">
              <div className="sub-history-title mono">{detail.posting_number}</div>
              <div className="order-detail-grid">
                <div><span className="order-detail-label">状态</span>{statusMeta(detail.status).label}（{detail.raw_status}）</div>
                <div><span className="order-detail-label">下单时间</span>{fmtTime(detail.created_at)}</div>
                <div><span className="order-detail-label">金额</span>{fmtMoney(detail.total_amount)}</div>
                <div><span className="order-detail-label">费用</span>{fmtMoney(detail.commission_amount)}</div>
                <div><span className="order-detail-label">利润</span>{fmtMoney(detail.profit)}</div>
                <div><span className="order-detail-label">仓库</span>{detail.warehouse || '—'}</div>
                <div><span className="order-detail-label">配送方式</span>{detail.delivery_method || '—'}</div>
                {detail.cancel_reason && <div><span className="order-detail-label">取消原因</span>{detail.cancel_reason}</div>}
                {detail.cancellation && <div><span className="order-detail-label">取消方</span>{detail.cancellation}</div>}
              </div>
              <div className="order-detail-title">商品明细</div>
              <table className="stores-table">
                <thead>
                  <tr>
                    <th>商品</th>
                    <th>货号</th>
                    <th>数量</th>
                    <th className="col-price">单价(₽)</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.products.map((p, i) => (
                    <tr key={i}>
                      <td className="col-title">{p.name}</td>
                      <td className="mono">{p.offer_id || '—'}</td>
                      <td>{p.quantity}</td>
                      <td className="col-price">{p.price != null ? `₽${p.price}` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="modal-foot">
              <button type="button" className="btn" onClick={() => setDetail(null)}>关闭</button>
            </div>
          </div>
        </div>
      )}

      {notesTarget && (
        <NotesModal
          postingNumber={notesTarget.posting_number}
          credentialId={credentialId}
          onClose={() => setNotesTarget(null)}
          onSaved={(notes) => {
            setNotesMap((m) => ({ ...m, [notes.posting_number]: notes }))
          }}
        />
      )}

      {messageTarget && (
        <MessageModal
          order={messageTarget}
          onClose={() => setMessageTarget(null)}
          onSent={() => {
            setNotice(`消息已发送：${messageTarget.posting_number}`)
          }}
        />
      )}

      {logOpen && <MessageLogModal onClose={() => setLogOpen(false)} />}

      {cancelTarget && (
        <CancelModal
          order={cancelTarget}
          onClose={() => setCancelTarget(null)}
          onCancelled={() => {
            setNotice(`已提交取消：${cancelTarget.posting_number}`)
            load()
          }}
        />
      )}
    </div>
  )
}
