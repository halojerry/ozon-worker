import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { deleteDraft, getDrafts, type Draft, type DraftSubmissionStatus } from '../api/client'

/* ── 上架状态映射（C1 状态机：无 submission 行 = 未上架） ── */
const STATUS_META: Record<string, { label: string; className: string }> = {
  pending: { label: '未上架', className: 'status-muted' },
  uploading: { label: '上架中', className: 'status-uploading' },
  published: { label: '已上架', className: 'status-published' },
  failed: { label: '失败', className: 'status-failed' },
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

export default function CollectBox() {
  const navigate = useNavigate()
  const [drafts, setDrafts] = useState<Draft[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [confirm, setConfirm] = useState<{ title: string; message: string; action: () => Promise<void> } | null>(null)

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
    </div>
  )
}
