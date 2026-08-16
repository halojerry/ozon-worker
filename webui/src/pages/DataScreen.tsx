import { useCallback, useEffect, useState } from 'react'
import { getAdminOverview, listOrders, type AdminOverview } from '../api/client'

/* P3 数据大屏：实时订单 + 平台概览（复用现有端点，纯前端聚合） */

function fmtTime(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function extractError(err: unknown, fallback: string): string {
  const resp = (err as { response?: { data?: { detail?: string } } } | null)?.response
  return resp?.data?.detail || fallback
}

export default function DataScreen() {
  const [overview, setOverview] = useState<AdminOverview | null>(null)
  const [recentOrders, setRecentOrders] = useState<{ posting_number: string; status: string; total_amount: number; created_at?: string | null }[]>([])
  const [clock, setClock] = useState(new Date())
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const [ov, orders] = await Promise.all([
        getAdminOverview(),
        listOrders({ limit: 50, since_days: 30 }).catch(() => ({ items: [], total: 0 } as never)),
      ])
      setOverview(ov)
      const items = (orders as { items?: { posting_number: string; status: string; total_amount: number; created_at?: string | null }[] })?.items ?? []
      setRecentOrders(items)
      setError('')
    } catch (err) {
      setError(extractError(err, '加载大屏数据失败'))
    }
  }, [])

  useEffect(() => {
    load()
    const t1 = setInterval(load, 15000) // 15s 刷新
    const t2 = setInterval(() => setClock(new Date()), 1000)
    return () => {
      clearInterval(t1)
      clearInterval(t2)
    }
  }, [load])

  const statusLabel: Record<string, string> = {
    pending: '待处理', awaiting: '待备货', waiting: '待发运',
    delivering: '运输中', delivered: '已签收', cancelled: '已取消',
  }

  return (
    <div className="data-screen">
      <header className="data-screen-header">
        <div>
          <h1 className="data-screen-title">实时数据大屏</h1>
          <div className="data-screen-clock">{clock.toLocaleTimeString('zh-CN')}</div>
        </div>
        {error && <span className="data-screen-error">{error}</span>}
        <button className="btn btn-ghost" onClick={load}>刷新</button>
      </header>

      <div className="data-screen-cards">
        <div className="data-card">
          <div className="data-card-label">总订单数</div>
          <div className="data-card-value">{overview?.task_total ?? '—'}</div>
        </div>
        <div className="data-card">
          <div className="data-card-label">今日任务</div>
          <div className="data-card-value">{overview?.task_today ?? '—'}</div>
        </div>
        <div className="data-card">
          <div className="data-card-label">任务成功率</div>
          <div className="data-card-value">{overview?.success_rate ?? '—'}%</div>
        </div>
        <div className="data-card">
          <div className="data-card-label">活跃店铺</div>
          <div className="data-card-value">{overview?.store_count ?? '—'}</div>
        </div>
        <div className="data-card">
          <div className="data-card-label">用户数</div>
          <div className="data-card-value">{overview?.user_count ?? '—'}</div>
        </div>
      </div>

      <div className="data-screen-body">
        <div className="data-panel">
          <h2 className="data-panel-title">实时订单</h2>
          <div className="data-order-list">
            {recentOrders.length === 0 ? (
              <p className="empty-state-text">暂无订单数据</p>
            ) : (
              recentOrders.map((o) => (
                <div key={o.posting_number} className="data-order-row">
                  <span className="mono">{o.posting_number}</span>
                  <span className={`status-badge ${o.status === 'delivered' ? 'status-published' : o.status === 'cancelled' ? 'status-failed' : 'status-uploading'}`}>
                    {statusLabel[o.status] ?? o.status}
                  </span>
                  <span className="data-order-amount">₽{Number(o.total_amount).toFixed(2)}</span>
                  <span className="data-order-time">{fmtTime(o.created_at)}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
