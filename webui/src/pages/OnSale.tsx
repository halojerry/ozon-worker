import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  bulkArchive,
  bulkUpdatePrices,
  bulkUpdateStocks,
  getProductEdit,
  listCredentials,
  listOzonProducts,
  listProducts,
  updateProductImages,
  type CredentialOut,
  type OzonProductOut,
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

/* ── P1a 批量操作弹窗：改价 / 改库存 ── */

interface BulkActionModalProps {
  action: 'price' | 'stock'
  count: number
  onClose: () => void
  onApplyPrice: (price: string, oldPrice: string, minPrice: string) => void
  onApplyStock: (stock: number) => void
}

function BulkActionModal({ action, count, onClose, onApplyPrice, onApplyStock }: BulkActionModalProps) {
  const [price, setPrice] = useState('')
  const [oldPrice, setOldPrice] = useState('')
  const [minPrice, setMinPrice] = useState('')
  const [stock, setStock] = useState('')

  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="批量操作">
        <div className="modal-header">
          <h2 className="modal-title">{action === 'price' ? '批量改价' : '批量改库存'}</h2>
          <button type="button" className="modal-close" aria-label="关闭" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          <p className="modal-text">将应用到 {count} 个商品（真实生效）</p>
          {action === 'price' ? (
            <>
              <div className="field">
                <label className="field-label" htmlFor="bulk-price">新售价</label>
                <input id="bulk-price" className="field-input" type="number" min="0" placeholder="如 99" value={price} onChange={(e) => setPrice(e.target.value)} />
              </div>
              <div className="form-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                <div className="field">
                  <label className="field-label" htmlFor="bulk-old-price">划线价（选填）</label>
                  <input id="bulk-old-price" className="field-input" type="number" min="0" placeholder="如 129" value={oldPrice} onChange={(e) => setOldPrice(e.target.value)} />
                </div>
                <div className="field">
                  <label className="field-label" htmlFor="bulk-min-price">最低价（选填）</label>
                  <input id="bulk-min-price" className="field-input" type="number" min="0" placeholder="如 89" value={minPrice} onChange={(e) => setMinPrice(e.target.value)} />
                </div>
              </div>
            </>
          ) : (
            <div className="field">
              <label className="field-label" htmlFor="bulk-stock">库存数量</label>
              <input id="bulk-stock" className="field-input" type="number" min="0" placeholder="如 100" value={stock} onChange={(e) => setStock(e.target.value)} />
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button type="button" className="btn" onClick={onClose}>取消</button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={action === 'price' ? price.trim() === '' : stock.trim() === ''}
            onClick={() => {
              if (action === 'price') onApplyPrice(price.trim(), oldPrice.trim(), minPrice.trim())
              else onApplyStock(Number(stock))
            }}
          >
            确认应用
          </button>
        </div>
      </div>
    </div>
  )
}

function ExternalIcon() {  return (
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
  const [editError, setEditError] = useState('')
  const [editing, setEditing] = useState<ProductItem | null>(null)
  const [editBusyId, setEditBusyId] = useState<string | null>(null)
  /** v0.50 视图切换：system=本系统上架 / ozon=店铺商品（实时拉取） */
  const [view, setView] = useState<'system' | 'ozon'>('system')
  const [ozonItems, setOzonItems] = useState<OzonProductOut[]>([])
  const [ozonTotal, setOzonTotal] = useState(0)
  const [ozonCredentials, setOzonCredentials] = useState<CredentialOut[]>([])
  const [ozonCredentialId, setOzonCredentialId] = useState('')
  const [ozonLoading, setOzonLoading] = useState(false)
  const [ozonError, setOzonError] = useState('')
  /** P1a 批量操作：多选 + 弹窗类型 */
  const [ozonSelected, setOzonSelected] = useState<Set<string>>(new Set())
  const [bulkAction, setBulkAction] = useState<'price' | 'stock' | 'archive' | 'unarchive' | null>(null)
  const [bulkNotice, setBulkNotice] = useState('')
  const navigate = useNavigate()

  async function handleEdit(item: ProductItem) {
    setEditBusyId(item.product_id)
    setEditError('')
    setNotice('')
    try {
      const data = await getProductEdit(item.product_id)
      navigate(`/products/${data.draft_id}?mode=online&product_id=${item.product_id}`)
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status
      if (status === 409) setEditError(`商品 ${item.product_id} 无草稿来源，仅支持改图`)
      else if (status === 404) setEditError(`商品 ${item.product_id} 未找到，可能已归档`)
      else setEditError(`获取编辑数据失败：${extractError(err, '未知错误')}`)
    } finally {
      setEditBusyId(null)
    }
  }

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

  /** v0.50 店铺商品视图：加载店铺 + 拉取 Ozon 商品 */
  const loadOzon = useCallback(
    async (silent = false) => {
      if (!ozonCredentialId) return
      if (!silent) setOzonLoading(true)
      try {
        const data = await listOzonProducts({ credential_id: ozonCredentialId, limit: 100 })
        setOzonItems(data.items)
        setOzonTotal(data.total)
        setOzonError('')
      } catch (err) {
        setOzonError(extractError(err, '拉取店铺商品失败'))
      } finally {
        setOzonLoading(false)
      }
    },
    [ozonCredentialId],
  )

  useEffect(() => {
    if (view !== 'ozon') return
    if (ozonCredentials.length === 0) {
      listCredentials()
        .then((creds) => {
          setOzonCredentials(creds)
          const def = creds.find((c) => c.is_default)
          setOzonCredentialId((prev) => prev || def?.id || '')
        })
        .catch(() => setOzonError('加载店铺列表失败'))
    }
  }, [view, ozonCredentials.length])

  useEffect(() => {
    if (view === 'ozon' && ozonCredentialId) loadOzon()
  }, [view, ozonCredentialId, loadOzon])

  /** P1a 多选切换 */
  const toggleOzonSelect = (id: string) => {
    setOzonSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleOzonAll = () => {
    const all = ozonItems.map((p) => p.product_id)
    const allSelected = all.every((id) => ozonSelected.has(id))
    setOzonSelected(allSelected ? new Set() : new Set(all))
  }

  const selectedOzon = ozonItems.filter((p) => ozonSelected.has(p.product_id))

  /** P1a 批量改价 */
  const applyBulkPrice = async (price: string, oldPrice: string, minPrice: string) => {
    try {
      await bulkUpdatePrices(
        selectedOzon.map((p) => ({
          offer_id: p.offer_id,
          price,
          ...(oldPrice ? { old_price: oldPrice } : {}),
          ...(minPrice ? { min_price: minPrice } : {}),
        })),
      )
      setBulkNotice(`已提交批量改价：${selectedOzon.length} 个商品`)
      setBulkAction(null)
      setOzonSelected(new Set())
      loadOzon()
    } catch (e) {
      setBulkNotice(`批量改价失败：${extractError(e, '批量改价失败')}`)
    }
  }

  /** P1a 批量改库存 */
  const applyBulkStock = async (stock: number) => {
    try {
      await bulkUpdateStocks(
        selectedOzon.map((p) => ({ offer_id: p.offer_id, product_id: p.product_id, stock })),
      )
      setBulkNotice(`已提交批量改库存：${selectedOzon.length} 个商品`)
      setBulkAction(null)
      setOzonSelected(new Set())
      loadOzon()
    } catch (e) {
      setBulkNotice(`批量改库存失败：${extractError(e, '批量改库存失败')}`)
    }
  }

  /** P1a 批量归档/恢复 */
  const applyBulkArchive = async (archive: boolean) => {
    const ok = window.confirm(
      `确认${archive ? '归档' : '恢复'} ${selectedOzon.length} 个商品？${archive ? '归档后商品从 Ozon 前台下架（真实生效）' : ''}`,
    )
    if (!ok) return
    try {
      await bulkArchive(selectedOzon.map((p) => p.product_id), archive)
      setBulkNotice(`已${archive ? '归档' : '恢复'} ${selectedOzon.length} 个商品`)
      setBulkAction(null)
      setOzonSelected(new Set())
      loadOzon()
    } catch (e) {
      setBulkNotice(`${archive ? '归档' : '恢复'}失败：${extractError(e, '批量归档失败')}`)
    }
  }

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

      {/* v0.50 视图切换：本系统上架（索引）/ 店铺商品（实时拉取） */}
      <div className="order-tabs">
        <button className={`order-tab${view === 'system' ? ' active' : ''}`} onClick={() => setView('system')}>
          本系统上架
        </button>
        <button className={`order-tab${view === 'ozon' ? ' active' : ''}`} onClick={() => setView('ozon')}>
          店铺商品
        </button>
      </div>

      {view === 'ozon' ? (
        <div className="card stores-table-wrap">
          <div className="toolbar">
            <select
              className="form-select"
              style={{ width: '220px' }}
              value={ozonCredentialId}
              onChange={(e) => setOzonCredentialId(e.target.value)}
            >
              <option value="">请选择店铺</option>
              {ozonCredentials.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.shop_name || c.ozon_client_id}（{c.ozon_client_id}
                  {c.is_default ? ' · 默认' : ''}）
                </option>
              ))}
            </select>
            <button className="btn" disabled={ozonLoading || !ozonCredentialId} onClick={() => loadOzon()}>
              {ozonLoading ? '拉取中…' : '刷新'}
            </button>
            {ozonSelected.size > 0 && (
              <>
                <button className="btn" onClick={() => setBulkAction('price')}>
                  批量改价 ({ozonSelected.size})
                </button>
                <button className="btn" onClick={() => setBulkAction('stock')}>
                  批量改库存 ({ozonSelected.size})
                </button>
                <button className="btn" onClick={() => applyBulkArchive(true)}>
                  批量归档 ({ozonSelected.size})
                </button>
                <button className="btn" onClick={() => applyBulkArchive(false)}>
                  批量恢复 ({ozonSelected.size})
                </button>
              </>
            )}
            <span className="toolbar-hint">店铺商品 = Ozon 店铺全部在线商品（含手动上架/其他工具）</span>
          </div>
          {ozonLoading ? (
            <div className="empty-state">
              <div className="spinner" style={{ borderColor: 'rgba(0, 91, 255, 0.2)', borderTopColor: 'var(--color-brand)' }} />
              <p className="empty-state-text">拉取店铺商品…</p>
            </div>
          ) : ozonError ? (
            <div className="empty-state">
              <div className="form-error" role="alert"><span>{ozonError}</span></div>
              <button className="btn" onClick={() => loadOzon()}>重试</button>
            </div>
          ) : ozonItems.length === 0 ? (
            <div className="empty-state">
              <p className="empty-state-title">暂无店铺商品</p>
              <p className="empty-state-text">请先选择上方店铺（未配置店铺可到店铺管理添加）</p>
            </div>
          ) : (
            <table className="stores-table">
              <thead>
                <tr>
                  <th className="col-check">
                    <input
                      type="checkbox"
                      checked={ozonItems.length > 0 && ozonSelected.size === ozonItems.length}
                      onChange={toggleOzonAll}
                      aria-label="全选店铺商品"
                    />
                  </th>
                  <th>商品</th>
                  <th>货号</th>
                  <th className="col-price">售价</th>
                  <th>库存</th>
                  <th>货币</th>
                </tr>
              </thead>
              <tbody>
                {ozonItems.map((p) => (
                  <tr key={p.product_id} className={ozonSelected.has(p.product_id) ? 'row-selected' : undefined}>
                    <td className="col-check">
                      <input
                        type="checkbox"
                        checked={ozonSelected.has(p.product_id)}
                        onChange={() => toggleOzonSelect(p.product_id)}
                        aria-label={`选择 ${p.name}`}
                      />
                    </td>
                    <td className="col-title">
                      <div className="task-product">
                        {p.image ? (
                          <img
                            className="task-thumb"
                            src={p.image}
                            alt={p.name}
                            style={{ width: '40px', height: '40px', objectFit: 'cover', borderRadius: '6px', flexShrink: 0 }}
                            onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none' }}
                          />
                        ) : (
                          <div className="img-placeholder" role="img" aria-label="无图片">无图</div>
                        )}
                        <div className="task-product-info">
                          <span className="draft-title" title={p.name}>{p.name || '（无名称）'}</span>
                          <span className="task-product-meta mono">{p.product_id}</span>
                        </div>
                      </div>
                    </td>
                    <td className="mono">{p.offer_id || '—'}</td>
                    <td className="col-price">{p.price != null ? `${p.currency} ${p.price}` : '—'}</td>
                    <td>{p.stock != null ? p.stock : '—'}</td>
                    <td>{p.currency || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {ozonTotal > 0 && (
            <div className="toolbar">
              <span className="toolbar-count">共 {ozonTotal} 个商品（显示前 {ozonItems.length}）</span>
            </div>
          )}
        </div>
      ) : (
        <>
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

      {editError && (
        <div className="alert alert-error" role="alert">
          <WarningIcon />
          <span>{editError}</span>
          <button className="btn btn-small btn-ghost" onClick={() => setEditError('')}>
            关闭
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
                        <button
                          className="row-action"
                          disabled={!!editing || editBusyId !== null}
                          onClick={() => handleEdit(item)}
                          title="加载该商品的草稿来源，进入编辑页全量更新"
                        >
                          {editBusyId === item.product_id ? '加载中…' : '编辑商品'}
                        </button>
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

      {bulkNotice && (
        <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && setBulkNotice('')}>
          <div className="modal" role="dialog" aria-modal="true" aria-label="批量操作结果">
            <div className="modal-header">
              <h2 className="modal-title">批量操作</h2>
              <button type="button" className="modal-close" aria-label="关闭" onClick={() => setBulkNotice('')}>×</button>
            </div>
            <div className="modal-body">
              <p className="modal-text">{bulkNotice}</p>
            </div>
            <div className="modal-foot">
              <button type="button" className="btn btn-primary" onClick={() => setBulkNotice('')}>知道了</button>
            </div>
          </div>
        </div>
      )}

      {bulkAction && (bulkAction === 'price' || bulkAction === 'stock') && (
        <BulkActionModal
          action={bulkAction}
          count={ozonSelected.size}
          onClose={() => setBulkAction(null)}
          onApplyPrice={applyBulkPrice}
          onApplyStock={applyBulkStock}
        />
      )}
        </>
      )}
    </div>
  )
}
