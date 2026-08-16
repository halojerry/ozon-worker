import { useCallback, useEffect, useState } from 'react'
import {
  getAdminOverview,
  getAdminTasks,
  getAdminUserDetail,
  listAdminStores,
  listAdminUsers,
  type AdminOverview,
  type AdminStoreOut,
  type AdminUserDetail,
  type AdminUserOut,
} from '../api/client'
import { fmtTime } from '../lib/business/format'
import { extractError } from '../lib/business/errors'

/* ── 概览卡片 ── */

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="card stat-card" style={{ padding: '16px 20px', minWidth: '140px' }}>
      <div className="store-meta">{label}</div>
      <div className="store-name" style={{ fontSize: '24px', fontWeight: 700 }}>{value}</div>
    </div>
  )
}

export default function Admin() {
  const [overview, setOverview] = useState<AdminOverview | null>(null)
  const [users, setUsers] = useState<AdminUserOut[]>([])
  const [stores, setStores] = useState<AdminStoreOut[]>([])
  const [tab, setTab] = useState<'users' | 'stores' | 'tasks'>('users')
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [detail, setDetail] = useState<AdminUserDetail | null>(null)
  const [detailBusy, setDetailBusy] = useState<string | null>(null)
  const [tasks, setTasks] = useState<Record<string, unknown>>({})

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const [ov, us, st] = await Promise.all([getAdminOverview(), listAdminUsers(), listAdminStores()])
      setOverview(ov)
      setUsers(us)
      setStores(st)
      setLoadError('')
    } catch (err) {
      setLoadError(extractError(err, '加载管理后台失败（需要管理员权限）'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function loadTasks() {
    try {
      setTasks(await getAdminTasks())
    } catch {
      setTasks({ error: '任务统计加载失败' })
    }
  }

  async function openDetail(u: AdminUserOut) {
    setDetailBusy(u.id)
    try {
      setDetail(await getAdminUserDetail(u.id))
    } catch (err) {
      window.alert(extractError(err, '加载用户详情失败'))
    } finally {
      setDetailBusy(null)
    }
  }

  if (loading) {
    return (
      <div className="page">
        <div className="page-header"><h1 className="page-title">管理后台</h1></div>
        <div className="card">
          <div className="empty-state">
            <div className="spinner" style={{ borderColor: 'rgba(0, 91, 255, 0.2)', borderTopColor: 'var(--color-brand)' }} />
            <p className="empty-state-text">加载平台数据…</p>
          </div>
        </div>
      </div>
    )
  }

  if (loadError) {
    return (
      <div className="page">
        <div className="page-header"><h1 className="page-title">管理后台</h1></div>
        <div className="card">
          <div className="empty-state">
            <div className="form-error" role="alert"><span>{loadError}</span></div>
            <button className="btn" onClick={() => load()}>重试</button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="page">
      <header className="page-header">
        <h1 className="page-title">管理后台</h1>
        <span className="page-badge">v0.51</span>
      </header>

      {/* 概览卡片 */}
      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '16px' }}>
        <StatCard label="用户数" value={overview?.user_count ?? 0} />
        <StatCard label="活跃店铺" value={overview?.store_count ?? 0} />
        <StatCard label="任务总数" value={overview?.task_total ?? 0} />
        <StatCard label="今日任务" value={overview?.task_today ?? 0} />
        <StatCard label="成功率" value={`${overview?.success_rate ?? 0}%`} />
      </div>

      <div className="order-tabs">
        <button className={`order-tab${tab === 'users' ? ' active' : ''}`} onClick={() => setTab('users')}>
          用户（{users.length}）
        </button>
        <button className={`order-tab${tab === 'stores' ? ' active' : ''}`} onClick={() => setTab('stores')}>
          店铺（{stores.length}）
        </button>
        <button className={`order-tab${tab === 'tasks' ? ' active' : ''}`} onClick={() => { setTab('tasks'); loadTasks() }}>
          任务统计
        </button>
      </div>

      {tab === 'users' && (
        <div className="card stores-table-wrap">
          <table className="stores-table">
            <thead>
              <tr>
                <th>用户</th>
                <th>余额</th>
                <th>角色</th>
                <th>店铺数</th>
                <th>任务数</th>
                <th>注册时间</th>
                <th className="col-actions">操作</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>
                    <div className="store-name">{u.username || '（无用户名）'}</div>
                    <div className="store-meta mono">{u.id}</div>
                  </td>
                  <td>{u.quota != null ? `¥${Number(u.quota).toFixed(2)}` : '—'}</td>
                  <td>
                    <span className={`badge ${u.role === 'admin' ? 'badge-update' : 'badge-currency'}`}>
                      {u.role}
                    </span>
                  </td>
                  <td>{u.store_count}</td>
                  <td>{u.task_count}</td>
                  <td className="col-time">{fmtTime(u.created_at)}</td>
                  <td className="col-actions">
                    <button className="btn btn-small" disabled={detailBusy === u.id} onClick={() => openDetail(u)}>
                      {detailBusy === u.id ? '加载中…' : '详情'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === 'stores' && (
        <div className="card stores-table-wrap">
          <table className="stores-table">
            <thead>
              <tr>
                <th>店铺</th>
                <th>归属用户</th>
                <th>Client ID</th>
                <th>货币</th>
                <th>默认</th>
                <th>状态</th>
                <th>最近校验</th>
              </tr>
            </thead>
            <tbody>
              {stores.map((s) => (
                <tr key={s.id}>
                  <td>
                    <div className="store-name">{s.shop_name || '未命名店铺'}</div>
                    <div className="store-meta mono">{s.id}</div>
                  </td>
                  <td className="mono">{s.tenant_id}</td>
                  <td className="mono">{s.ozon_client_id}</td>
                  <td><span className="badge badge-currency">{s.currency}</span></td>
                  <td>{s.is_default ? <span className="badge badge-default">★</span> : '—'}</td>
                  <td>
                    <span className={`badge ${s.status === 'active' ? 'badge-ok' : 'badge-fail'}`}>
                      {s.status}
                    </span>
                  </td>
                  <td className="col-time">{fmtTime(s.last_validated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === 'tasks' && (
        <div className="card">
          <pre className="sub-history-error-text" style={{ margin: 0 }}>
            {JSON.stringify(tasks, null, 2)}
          </pre>
        </div>
      )}

      {detail && (
        <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && setDetail(null)}>
          <div className="modal" role="dialog" aria-modal="true" aria-label="用户详情">
            <div className="modal-header">
              <h2 className="modal-title">用户详情</h2>
              <button type="button" className="modal-close" aria-label="关闭" onClick={() => setDetail(null)}>
                ×
              </button>
            </div>
            <div className="modal-body">
              <div className="sub-history-title mono">{detail.id}</div>
              <div className="order-detail-grid">
                <div><span className="order-detail-label">任务总数</span>{detail.task_total}</div>
                <div><span className="order-detail-label">已完成</span>{detail.task_completed}</div>
                <div><span className="order-detail-label">失败</span>{detail.task_failed}</div>
              </div>
              <div className="order-detail-title">店铺（{detail.stores.length}）</div>
              {detail.stores.length === 0 ? (
                <p className="empty-state-text">该用户未绑定店铺</p>
              ) : (
                <table className="stores-table">
                  <thead>
                    <tr>
                      <th>店铺</th>
                      <th>Client ID</th>
                      <th>货币</th>
                      <th>默认</th>
                      <th>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.stores.map((s) => (
                      <tr key={s.id}>
                        <td>{s.shop_name || '未命名'}</td>
                        <td className="mono">{s.ozon_client_id}</td>
                        <td>{s.currency}</td>
                        <td>{s.is_default ? '★' : '—'}</td>
                        <td>{s.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <div className="modal-foot">
              <button type="button" className="btn" onClick={() => setDetail(null)}>关闭</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
