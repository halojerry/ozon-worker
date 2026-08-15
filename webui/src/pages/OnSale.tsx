import { useCallback, useEffect, useState } from 'react'
import {
  listProducts,
  updateProductImages,
  type ProductItem,
  type ProductModerationStatus,
} from '../api/client'

const PAGE_SIZE = 20

/* ── 审核状态映射（approved=已上架 / pending_moderation=重新审核中 / 其他=未知） ── */
const STATUS_META: Record<string, { label: string; className: string }> = {
  approved: { label: '已上架', className: 'status-published' },
  pending_moderation: { label: '重新审核中', className: 'status-uploading' },
  pending: { label: '审核中', className: 'status-uploading' },
  failed: { label: '失败', className: 'status-failed' },
  declined: { label: '审核被拒', className: 'status-failed' },
  rejected: { label: '审核被拒', className: 'status-failed' },
}

function statusMeta(status: ProductModerationStatus | null | undefined) {
  return STATUS_META[status ?? ''] ?? { label: '未知', className: 'status-muted' }
}

function fmtTime(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function extractError(err: unknown, fallback: string): string {
  const resp = (err as { response?: { data?: { detail?: string } } } | null)?.response
  return resp?.data?.detail || fallback
}

function ExternalIcon() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M14 4h6v6M20 4L10 14M19 13v6a1 1 0 01-1 1H5a1 1 0 01-1-1V6a1 1 0 011-1h6" />
    </svg>
  )
}

function WarningIcon() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M12 3.5L22 20H2L12 3.5z" />
      <path d="M12 9.5v5M12 17.2v.1" />
    </svg>
  )
}

/* ── 改图弹窗（T14：输入新图片 URL 列表 → update_images → 刷新 + 重新审核中） ── */

function UpdateImagesModal({
  product,
  onClose,
  onUpdated,
}: {
  product: ProductItem
  onClose: () => void
  onUpdated: () => void
}) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    const urls = text
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.length > 0)
    if (urls.length === 0) {
      setError('请至少输入一个图片 URL')
      return
    }
    setBusy(true)
    setError('')
    try {
      await updateProductImages(product.product_id, urls)
      onUpdated()
      onClose()
    } catch (err) {
      setError(extractError(err, '改图失败，请稍后重试'))
      setBusy(false)
    }
  }

  return (
    <div className="modal-mask" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="改图">
        <h3 className="modal-title">改图</h3>
        <p className="modal-text">
          商品 <span className="mono">{product.product_id}</span>（offer_id{' '}
          <span className="mono">{product.offer_id || '—'}</span>）将使用新图片全量重传，触发重新审核。
          每行一个图片 URL：
        </p>
        <textarea
          className="field-input images-textarea"
          rows={6}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={'https://…/image1.jpg\nhttps://…/image2.jpg'}
          disabled={busy}
        />
        {error && (
          <div className="alert alert-error" role="alert">
            <WarningIcon />
            <span>{error}</span>
          </div>
        )}
        <div className="modal-actions">
          <button className="btn" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button className="btn btn-primary" disabled={busy} onClick={submit}>
            {busy ? '提交中…' : '确认改图'}
          </button>
        </div>
      </div>
    </div>
  )
}

/* ── 在售货架页 ── */

export default function OnSale() {
  const [items, setItems] = useState<ProductItem[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [notice, setNotice] = useState('')
  const [editing, setEditing] = useState<ProductItem | null>(null)

  const load = useCallback(async (targetOffset = offset, silent = false) => {
    if (!silent) setLoading(true)
    try {
      const data = await listProducts({ limit: PAGE_SIZE, offset: targetOffset })
      setItems(data.items)
      setTotal(data.total)
      setOffset(targetOffset)
      setLoadError('')
    } catch (err) {
      setLoadError(extractError(err, '加载在售商品失败'))
    } finally {
      setLoading(false)
    }
  }, [offset])

  useEffect(() => {
    load(0, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const pageIndex = Math.floor(offset / PAGE_SIZE) + 1
  const hasPrev = offset > 0
  const hasNext = offset + items.length < total

  return (
    <div className="page">
      <header className="page-header">
        <h1 className="page-title">在售货架</h1>
        <span className="page-badge">M2.1</span>
      </header>

      <div className="toolbar">
        <span className="toolbar-count">共 {total} 个商品</span>
        <button className="btn" onClick={() => load(offset)} disabled={loading}>
          刷新
        </button>
      </div>

      {notice && (
        <div className="alert alert-success" role="status">
          <span>{notice}</span>
          <button className="btn btn-small btn-ghost" onClick={() => setNotice('')}>
            知道了
          </button>
        </div>
      )}

      <div className="card">
        {loading ? (
          <div className="empty-state">
            <div className="spinner" style={{ borderColor: 'rgba(0, 91, 255, 0.2)', borderTopColor: 'var(--color-brand)' }} />
            <p className="empty-state-text">加载在售商品…</p>
          </div>
        ) : loadError ? (
          <div className="empty-state">
            <div className="form-error" role="alert">
              <WarningIcon />
              <span>{loadError}</span>
            </div>
            <button className="btn" onClick={() => load(offset)}>
              重试
            </button>
          </div>
        ) : items.length === 0 ? (
          <div className="empty-state">
            <div className="placeholder-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.6">
                <path d="M6 3h12a1 1 0 011 1v17l-7-4-7 4V4a1 1 0 011-1z" />
              </svg>
            </div>
            <p className="empty-state-title">还没有上架商品</p>
            <p className="empty-state-text">使用 Skill 的 graph / follow 提交上架任务，审核通过后商品会显示在这里</p>
          </div>
        ) : (
          <table className="draft-table">
            <thead>
              <tr>
                <th>Ozon product_id</th>
                <th>offer_id</th>
                <th>审核状态</th>
                <th>草稿来源</th>
                <th className="col-time">上架时间</th>
                <th className="col-actions">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const meta = statusMeta(item.moderation_status)
                return (
                  <tr key={item.product_id}>
                    <td className="mono">{item.product_id}</td>
                    <td className="mono">{item.offer_id || '—'}</td>
                    <td>
                      <span className={`status-badge ${meta.className}`}>{meta.label}</span>
                    </td>
                    <td>
                      <span className={`source-tag ${item.draft_id ? 'source-skill' : 'source-webui'}`}>
                        {item.draft_id ? '来自采集箱' : '直连'}
                      </span>
                    </td>
                    <td className="col-time">{fmtTime(item.created_at)}</td>
                    <td className="col-actions">
                      <div className="row-actions">
                        <button className="row-action" disabled={!!editing} onClick={() => setEditing(item)}>
                          改图
                        </button>
                        <a
                          className="row-action"
                          href={`https://www.ozon.ru/product/${item.product_id}/`}
                          target="_blank"
                          rel="noreferrer"
                          title="打开 Ozon 商品页（新窗口）"
                        >
                          打开商品页
                          <ExternalIcon />
                        </a>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {items.length > 0 && (
        <div className="pager">
          <span className="toolbar-count">
            第 {pageIndex} / {pageCount} 页
          </span>
          <div className="pager-buttons">
            <button className="btn btn-small" disabled={!hasPrev || loading} onClick={() => load(offset - PAGE_SIZE)}>
              上一页
            </button>
            <button className="btn btn-small" disabled={!hasNext || loading} onClick={() => load(offset + PAGE_SIZE)}>
              下一页
            </button>
          </div>
        </div>
      )}

      {editing && (
        <UpdateImagesModal
          product={editing}
          onClose={() => setEditing(null)}
          onUpdated={() => {
            setNotice(`商品 ${editing.product_id} 改图已提交，进入重新审核`)
            load(offset, true)
          }}
        />
      )}
    </div>
  )
}
