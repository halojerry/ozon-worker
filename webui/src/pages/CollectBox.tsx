import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from '@/lib/router-compat'
import {
  deleteDraft,
  estimateDraft,
  getDrafts,
  listCredentials,
  listTemplates,
  submitDraft,
  type CredentialOut,
  type Draft,
  type DraftEstimate,
  type DraftSubmissionStatus,
  type ListingTemplateOut,
  type SubmitResponse,
} from '../api/client'
import SubmissionHistory from '../components/SubmissionHistory'

/* ── 上架状态映射（C1 状态机：无 submission 行 = 未上架） ── */
const STATUS_META: Record<string, { label: string; className: string }> = {
  pending: { label: '未上架', className: 'status-muted' },
  uploading: { label: '上架中', className: 'status-uploading' },
  published: { label: '已上架', className: 'status-published' },
  failed: { label: '失败', className: 'status-failed' },
  rejected: { label: '审核被拒', className: 'status-failed' },
}

function statusMeta(status: DraftSubmissionStatus | null | undefined) {
  return STATUS_META[status ?? ''] ?? STATUS_META.pending
}

/** 采集价格：variants 存在 → 区间 ¥min-¥max；否则单值 ¥cost */
function priceLabel(draft: Draft): string {
  const variants = draft.payload?.draft?.variants
  const prices = (variants ?? []).map((v) => v.price).filter((p): p is number => typeof p === 'number')
  if (prices.length > 1) {
    const min = Math.min(...prices)
    const max = Math.max(...prices)
    return min === max ? `¥${min}` : `¥${min}-¥${max}`
  }
  const cost = draft.payload?.draft?.purchase_cost
  return typeof cost === 'number' ? `¥${cost}` : '—'
}

function skuCount(draft: Draft): number {
  const variants = draft.payload?.draft?.variants
  return Array.isArray(variants) && variants.length > 0 ? variants.length : 1
}

/* ── M1.2 预估懒加载：模块级 Promise 缓存去重（同 draft 只请求一次）+ 并发节流 ── */
const estimateCache = new Map<string, Promise<DraftEstimate | null>>()
const ESTIMATE_MAX_IN_FLIGHT = 4
let estimateInFlight = 0
const estimateWaiters: Array<() => void> = []

function acquireEstimateSlot(): Promise<void> {
  if (estimateInFlight < ESTIMATE_MAX_IN_FLIGHT) {
    estimateInFlight++
    return Promise.resolve()
  }
  return new Promise((resolve) => estimateWaiters.push(resolve))
}

function releaseEstimateSlot(): void {
  estimateInFlight--
  estimateWaiters.shift()?.()
}

function loadEstimate(draftId: string): Promise<DraftEstimate | null> {
  const hit = estimateCache.get(draftId)
  if (hit) return hit
  const pending = (async () => {
    await acquireEstimateSlot()
    try {
      return await estimateDraft(draftId)
    } catch {
      return null
    } finally {
      releaseEstimateSlot()
    }
  })()
  estimateCache.set(draftId, pending)
  return pending
}

const CURRENCY_SYMBOL: Record<string, string> = { CNY: '¥', RUB: '₽', USD: '$' }

function fmtMoney(v: number | undefined, currency?: string): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—'
  const sym = (currency && CURRENCY_SYMBOL[currency]) || '¥'
  return `${sym}${v.toFixed(2)}`
}

function fmtRate(v: number | undefined): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—'
  return `${(v * 100).toFixed(1)}%`
}

function EstimateCells({ draftId }: { draftId: string }) {
  const [est, setEst] = useState<DraftEstimate | null>(null)
  useEffect(() => {
    let alive = true
    loadEstimate(draftId).then((r) => {
      if (alive) setEst(r)
    })
    return () => {
      alive = false
    }
  }, [draftId])
  return (
    <>
      <td className="col-price">{est ? fmtMoney(est.price, est.currency) : '—'}</td>
      <td className="col-price">{est ? fmtMoney(est.profit_cny, est.currency) : '—'}</td>
      <td className="col-price">{est ? fmtRate(est.profit_rate) : '—'}</td>
    </>
  )
}

function fmtTime(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function remarkLabel(draft: Draft): string {
  const supplier = draft.payload?.draft?.supplier
  if (supplier) return supplier
  const url = draft.payload?.draft?.purchase_url ?? draft.payload?.source?.purchase_url
  if (url) {
    try {
      return new URL(url).hostname
    } catch {
      return url
    }
  }
  return '—'
}

function ImageCell({ src, alt }: { src?: string; alt: string }) {
  const [broken, setBroken] = useState(false)
  if (!src || broken) {
    return (
      <div className="img-placeholder" role="img" aria-label={`图片加载失败：${alt}`}>
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6">
          <rect x="3.5" y="3.5" width="17" height="17" rx="2.5" />
          <circle cx="9" cy="9" r="1.8" />
          <path d="M4.5 18.5l5-5 3.5 3.5 3-3 3.5 3.5" />
        </svg>
      </div>
    )
  }
  return <img className="draft-thumb" src={src} alt={alt} loading="lazy" onError={() => setBroken(true)} />
}

function ConfirmDialog({
  title,
  message,
  confirmText,
  danger,
  onConfirm,
  onCancel,
}: {
  title: string
  message: string
  confirmText: string
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <div className="modal-mask" role="dialog" aria-modal="true" aria-label={title}>
      <div className="modal">
        <h3 className="modal-title">{title}</h3>
        <p className="modal-text">{message}</p>
        <div className="modal-actions">
          <button className="btn" onClick={onCancel}>
            取消
          </button>
          <button className={danger ? 'btn btn-danger' : 'btn btn-primary'} onClick={onConfirm}>
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  )
}

/* ── P0-3 批量上架：选店铺/模板 → 逐个提交（失败隔离）→ 结果视图 ── */

interface BatchOk {
  draftId: string
  title: string
  taskId: string
}

interface BatchFail {
  draftId: string
  title: string
  reason: string
}

interface BatchSubmitModalProps {
  drafts: Draft[]
  credentials: CredentialOut[]
  templates: ListingTemplateOut[]
  onClose: () => void
  onDone: (okCount: number) => void
}

function BatchSubmitModal({ drafts, credentials, templates, onClose, onDone }: BatchSubmitModalProps) {
  const [credentialId, setCredentialId] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [phase, setPhase] = useState<'form' | 'running' | 'result'>('form')
  const [progress, setProgress] = useState(0)
  const [okList, setOkList] = useState<BatchOk[]>([])
  const [failList, setFailList] = useState<BatchFail[]>([])

  const totalCost = useMemo(
    () =>
      drafts.reduce((sum, d) => {
        const cost = d.payload?.draft?.purchase_cost
        return sum + (typeof cost === 'number' ? cost : 0)
      }, 0),
    [drafts],
  )

  const hasDefaultTpl = templates.some((t) => t.is_default)
  useEffect(() => {
    if (!credentialId) {
      const def = credentials.find((c) => c.is_default)
      setCredentialId(def?.id ?? '')
    }
    if (!templateId && hasDefaultTpl) {
      const def = templates.find((t) => t.is_default)
      setTemplateId(def?.id ?? '')
    }
  }, [credentials, templates, credentialId, templateId, hasDefaultTpl])

  async function run() {
    setPhase('running')
    setOkList([])
    setFailList([])
    let okCount = 0
    for (let i = 0; i < drafts.length; i++) {
      const d = drafts[i]
      setProgress(i + 1)
      try {
        const res: SubmitResponse = await submitDraft(d.id, credentialId || undefined, templateId || undefined)
        okCount += 1
        setOkList((prev) => [...prev, { draftId: d.id, title: d.payload?.draft?.title ?? d.id, taskId: res.task_id }])
      } catch (e: unknown) {
        const err = e as { response?: { data?: { detail?: string } }; message?: string }
        const detail = err?.response?.data?.detail
        let reason = detail ?? err?.message ?? '提交失败'
        if ((e as { response?: { status?: number } })?.response?.status === 409) {
          reason = detail ?? '目标店铺已存在相同商品'
        }
        setFailList((prev) => [...prev, { draftId: d.id, title: d.payload?.draft?.title ?? d.id, reason }])
      }
    }
    setPhase('result')
    onDone(okCount)
  }

  const submitDisabled = phase !== 'form' || !credentialId || drafts.length === 0

  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && phase !== 'running' && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="批量上架">
        <div className="modal-header">
          <h2 className="modal-title">{phase === 'result' ? '批量上架结果' : '批量上架'}</h2>
          <button type="button" className="modal-close" aria-label="关闭" onClick={onClose} disabled={phase === 'running'}>
            ×
          </button>
        </div>

        {phase === 'form' && (
          <>
            <p className="modal-text">
              共 <strong>{drafts.length}</strong> 个草稿 · 采购总成本 ¥{totalCost.toFixed(2)}
            </p>
            <div className="modal-form">
              <div className="field">
                <label className="field-label" htmlFor="batch-store">目标店铺</label>
                <select
                  id="batch-store"
                  className="field-select"
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
              </div>
              <div className="field">
                <label className="field-label" htmlFor="batch-template">上架配置</label>
                <select
                  id="batch-template"
                  className="field-select"
                  value={templateId}
                  onChange={(e) => setTemplateId(e.target.value)}
                >
                  <option value="">不使用（worker 默认参数）</option>
                  {templates.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                      {t.is_default ? '（默认）' : ''}
                    </option>
                  ))}
                </select>
              </div>
              <p className="field-hint">将逐条提交上架；某条失败不中断其余，完成后显示结果。</p>
            </div>
            <div className="modal-foot">
              <button type="button" className="btn" onClick={onClose}>取消</button>
              <button type="button" className="btn btn-primary" disabled={submitDisabled} onClick={run}>
                批量上架
              </button>
            </div>
          </>
        )}

        {phase === 'running' && (
          <div className="modal-body">
            <div className="empty-state">
              <div className="spinner" style={{ borderColor: 'rgba(0, 91, 255, 0.2)', borderTopColor: 'var(--color-brand)' }} />
              <p className="empty-state-text">正在提交 {progress}/{drafts.length}…</p>
            </div>
          </div>
        )}

        {phase === 'result' && (
          <div className="modal-body">
            <div className="empty-state">
              <p className="empty-state-title">
                成功 {okList.length} · 失败 {failList.length}
              </p>
            </div>
            {okList.length > 0 && (
              <div className="batch-result-block">
                <div className="batch-result-title">成功</div>
                <ul className="batch-result-list">
                  {okList.map((r) => (
                    <li key={r.draftId}>
                      <span className="batch-result-item" title={r.title}>{r.title}</span>
                      <Link className="btn btn-small" to={`/tasks?task_id=${r.taskId}`}>查看进度</Link>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {failList.length > 0 && (
              <div className="batch-result-block">
                <div className="batch-result-title">失败</div>
                <ul className="batch-result-list">
                  {failList.map((r) => (
                    <li key={r.draftId}>
                      <span className="batch-result-item" title={r.reason}>
                        {r.title} — {r.reason}
                      </span>
                      <Link className="btn btn-small" to={`/products/${r.draftId}`}>去编辑</Link>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="modal-foot">
              <button type="button" className="btn btn-primary" onClick={onClose}>关闭</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default function CollectBox() {
  const navigate = useNavigate()
  const [drafts, setDrafts] = useState<Draft[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [confirm, setConfirm] = useState<{ title: string; message: string; action: () => Promise<void> } | null>(null)
  /** M2.2 提交历史弹窗目标草稿（行按钮触发） */
  const [historyDraft, setHistoryDraft] = useState<Draft | null>(null)
  /** P0-3 批量上架：弹窗开关 + 店铺/模板缓存 */
  const [batchOpen, setBatchOpen] = useState(false)
  const [credentials, setCredentials] = useState<CredentialOut[]>([])
  const [templates, setTemplates] = useState<ListingTemplateOut[]>([])

  /** P0-3 批量上架：打开弹窗时懒加载店铺 + 模板（缓存避免每次弹窗请求） */
  const openBatch = useCallback(() => {
    if (selected.size === 0) return
    setBatchOpen(true)
    Promise.all([listCredentials(), listTemplates()])
      .then(([creds, tpls]) => {
        setCredentials(creds)
        setTemplates(tpls)
      })
      .catch(() => {
        /* 店铺/模板加载失败：弹窗内下拉为空，用户仍可提交（用默认参数） */
      })
  }, [selected])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getDrafts()
      setDrafts(data)
      setSelected(new Set())
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败，请稍后重试')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const allChecked = drafts.length > 0 && selected.size === drafts.length
  const someChecked = selected.size > 0 && !allChecked

  const toggleAll = () => {
    setSelected(allChecked ? new Set() : new Set(drafts.map((d) => d.id)))
  }

  const toggleOne = (id: string) => {
    const next = new Set(selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSelected(next)
  }

  const removeMany = useCallback(
    async (ids: string[]) => {
      await Promise.all(ids.map((id) => deleteDraft(id)))
      await load()
    },
    [load],
  )

  const askDelete = (ids: string[], what: string) => {
    setConfirm({
      title: what,
      message: `将删除 ${ids.length} 个草稿（其提交记录同步级联删除，不可恢复）。确认继续？`,
      action: () => removeMany(ids),
    })
  }

  const askClear = () => {
    if (drafts.length === 0) return
    setConfirm({
      title: '清空采集箱',
      message: `将删除全部 ${drafts.length} 个草稿（提交记录同步级联删除，不可恢复）。确认继续？`,
      action: () => removeMany(drafts.map((d) => d.id)),
    })
  }

  const selectedCountLabel = useMemo(() => (selected.size > 0 ? `已选 ${selected.size} 项` : null), [selected])

  return (
    <div className="page">
      <header className="page-header">
        <h1 className="page-title">采集箱</h1>
        <span className="page-badge">{drafts.length > 0 ? `${drafts.length} 个草稿` : '空'}</span>
      </header>

      <div className="card">
        <div className="collect-toolbar">
          <button
            className="btn btn-primary"
            disabled={selected.size === 0}
            onClick={openBatch}
          >
            批量上架
          </button>
          <button
            className="btn btn-danger"
            disabled={selected.size === 0}
            onClick={() => askDelete([...selected], '批量删除')}
          >
            批量删除
          </button>
          <button className="btn" disabled={drafts.length === 0} onClick={askClear}>
            清空采集箱
          </button>
          <span className="toolbar-hint">{selectedCountLabel}</span>
          <span className="toolbar-spacer" />
          <button className="btn btn-ghost" onClick={load} disabled={loading}>
            {loading ? '加载中…' : '刷新'}
          </button>
        </div>

        {loading ? (
          <div className="collect-loading">加载中…</div>
        ) : error ? (
          <div className="collect-error">
            <p>{error}</p>
            <button className="btn btn-primary" onClick={load}>
              重试
            </button>
          </div>
        ) : drafts.length === 0 ? (
          <div className="collect-empty">
            <div className="placeholder-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.6">
                <path d="M4 7h16M4 7l1.5 13h13L20 7M9 7V5a2 2 0 012-2h2a2 2 0 012 2v2" />
              </svg>
            </div>
            <h2 className="placeholder-title">暂无数据</h2>
            <p className="placeholder-text">使用 Skill 的 graph / follow --to-box 采集商品后，会出现在这里</p>
          </div>
        ) : (
          <table className="draft-table">
            <thead>
              <tr>
                <th className="col-check">
                  <input
                    type="checkbox"
                    checked={allChecked}
                    ref={(el) => {
                      if (el) el.indeterminate = someChecked
                    }}
                    onChange={toggleAll}
                    aria-label="全选"
                  />
                </th>
                <th>图片</th>
                <th>产品名称</th>
                <th>采集价格</th>
                <th>预估售价</th>
                <th>预估利润</th>
                <th>利润率</th>
                <th>sku数量</th>
                <th>采集来源</th>
                <th>备注</th>
                <th>上架状态</th>
                <th>创建时间</th>
                <th>更新时间</th>
                <th className="col-actions">操作</th>
              </tr>
            </thead>
            <tbody>
              {drafts.map((draft) => {
                const meta = statusMeta(draft.submission_status)
                return (
                  <tr key={draft.id} className={selected.has(draft.id) ? 'row-selected' : undefined}>
                    <td className="col-check">
                      <input
                        type="checkbox"
                        checked={selected.has(draft.id)}
                        onChange={() => toggleOne(draft.id)}
                        aria-label={`选择 ${draft.payload?.draft?.title ?? draft.id}`}
                      />
                    </td>
                    <td>
                      <ImageCell
                        src={draft.payload?.draft?.images?.[0]}
                        alt={draft.payload?.draft?.title ?? draft.id}
                      />
                    </td>
                    <td className="col-title">
                      <span className="draft-title" title={draft.payload?.draft?.title}>
                        {draft.payload?.draft?.title ?? '（无标题）'}
                      </span>
                    </td>
                    <td className="col-price">{priceLabel(draft)}</td>
                    <EstimateCells draftId={draft.id} />
                    <td className="col-num">{skuCount(draft)}</td>
                    <td>
                      <span className={`source-tag source-${draft.source}`}>
                        {draft.source === 'skill' ? 'Skill 采集' : 'WebUI'}
                      </span>
                    </td>
                    <td className="col-remark" title={remarkLabel(draft)}>
                      {remarkLabel(draft)}
                    </td>
                    <td>
                      <span className={`status-badge ${meta.className}`}>{meta.label}</span>
                    </td>
                    <td className="col-time">{fmtTime(draft.created_at)}</td>
                    <td className="col-time">{fmtTime(draft.updated_at)}</td>
                    <td className="col-actions">
                      <button className="btn btn-small btn-primary" onClick={() => navigate(`/products/${draft.id}`)}>
                        编辑上架
                      </button>
                      <button className="btn btn-small" onClick={() => setHistoryDraft(draft)}>
                        提交历史
                      </button>
                      <button className="btn btn-small btn-danger-text" onClick={() => askDelete([draft.id], '删除草稿')}>
                        删除
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {confirm && (
        <ConfirmDialog
          title={confirm.title}
          message={confirm.message}
          confirmText="确认删除"
          danger
          onConfirm={() => {
            const action = confirm.action
            setConfirm(null)
            action().catch((err) => setError(err instanceof Error ? err.message : '删除失败'))
          }}
          onCancel={() => setConfirm(null)}
        />
      )}

      {historyDraft && (
        <SubmissionHistory
          draftId={historyDraft.id}
          draftTitle={historyDraft.payload?.draft?.title}
          onClose={() => setHistoryDraft(null)}
        />
      )}

      {batchOpen && (
        <BatchSubmitModal
          drafts={drafts.filter((d) => selected.has(d.id))}
          credentials={credentials}
          templates={templates}
          onClose={() => setBatchOpen(false)}
          onDone={(okCount) => {
            if (okCount > 0) {
              setSelected(new Set())
              load()
            }
          }}
        />
      )}
    </div>
  )
}
