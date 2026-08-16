import { useCallback, useEffect, useState } from 'react'
import { listBestsellers, type BestsellerItem } from '../api/client'
import { extractError } from '../lib/business/errors'

/* P2b 榜单选品：浏览 skill 上报的 ozon-bestsellers（类目筛选 + 排序） */

const ORDER_OPTIONS = [
  { key: 'ordering_amount', label: '按订购金额' },
  { key: 'ordering_count', label: '按订购数量' },
  { key: 'avg_price_rub', label: '按均价' },
]

function fmtNum(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return Number(v).toLocaleString('zh-CN')
}

export default function Bestsellers() {
  const [items, setItems] = useState<BestsellerItem[]>([])
  const [total, setTotal] = useState(0)
  const [category, setCategory] = useState('')
  const [orderBy, setOrderBy] = useState('ordering_amount')
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await listBestsellers({ category: category || undefined, order_by: orderBy as never, limit: 100 })
      setItems(data.items)
      setTotal(data.total)
      setLoadError('')
    } catch (err) {
      setLoadError(extractError(err, '加载榜单失败'))
    } finally {
      setLoading(false)
    }
  }, [category, orderBy])

  useEffect(() => {
    load()
  }, [load])

  return (
    <div className="page">
      <header className="page-header">
        <h1 className="page-title">榜单选品</h1>
        <span className="page-badge">P2b</span>
      </header>

      <div className="toolbar">
        <input
          className="field-input"
          style={{ width: '200px' }}
          placeholder="类目筛选（如 宠物）"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
        />
        <select className="form-select" style={{ width: '160px' }} value={orderBy} onChange={(e) => setOrderBy(e.target.value)}>
          {ORDER_OPTIONS.map((o) => (
            <option key={o.key} value={o.key}>{o.label}</option>
          ))}
        </select>
        <button className="btn btn-primary" onClick={load} disabled={loading}>
          {loading ? '查询中…' : '查询'}
        </button>
        <span className="toolbar-spacer" />
        <button className="btn btn-ghost" onClick={load} disabled={loading}>刷新</button>
      </div>

      {loading ? (
        <div className="card"><div className="empty-state"><div className="spinner" /><p className="empty-state-text">加载榜单…</p></div></div>
      ) : loadError ? (
        <div className="card">
          <div className="empty-state">
            <div className="form-error" role="alert"><span>{loadError}</span></div>
            <button className="btn" onClick={load}>重试</button>
          </div>
        </div>
      ) : items.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <p className="empty-state-title">暂无榜单数据</p>
            <p className="empty-state-text">使用 Skill 的 queries / ozon-bestsellers 命令采集并上报后，数据会显示在这里</p>
          </div>
        </div>
      ) : (
        <div className="card stores-table-wrap">
          <table className="stores-table">
            <thead>
              <tr>
                <th>SKU / 商品 ID</th>
                <th>品牌</th>
                <th>类目</th>
                <th>订购金额</th>
                <th>订购数量</th>
                <th>均价（₽）</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={`${it.sku_or_id}-${it.category_path}`}>
                  <td className="mono">{it.sku_or_id}</td>
                  <td>{it.brand || '—'}</td>
                  <td>{it.category_path || '—'}</td>
                  <td>{fmtNum(it.ordering_amount)}</td>
                  <td>{fmtNum(it.ordering_count)}</td>
                  <td>{fmtNum(it.avg_price_rub)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="toolbar">
            <span className="toolbar-count">共 {total} 条</span>
          </div>
        </div>
      )}
    </div>
  )
}
