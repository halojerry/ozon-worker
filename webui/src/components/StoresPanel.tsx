import { useEffect, useMemo, useState } from "react"
import { api } from "../api/client"
import type { AnalyticsDailyResponse, Credential, DailyMetricsResponse, StoreStats, StoreSyncStatus, SyncJob, SyncJobsResponse } from "../api/hooks"
import { apiErrorMessage, formatDateTime, formatPrice, useApi, usePolling } from "../api/hooks"
import { PageHeader, PanelEmpty, PanelError, PanelLoading } from "./ui"

function freshnessBadge(s: StoreSyncStatus | undefined, lastSync: string | null | undefined) {
  if (s?.is_stale) return <span className="status line">数据过期</span>
  if (s?.current_job) return <span className="status">同步中 {s.current_job.progress}%</span>
  if (!lastSync) return <span className="status line">从未同步</span>
  return <span className="status green">已同步</span>
}

function SyncConfigEditor({ store, onSaved }: { store: Credential; onSaved: () => void }) {
  const [open, setOpen] = useState(false)
  const [enabled, setEnabled] = useState(store.sync_enabled ?? true)
  const [orders, setOrders] = useState(String(store.sync_interval_minutes ?? 15))
  const [products, setProducts] = useState(String(store.sync_products_interval_minutes ?? 30))
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState("")

  const save = async () => {
    setBusy(true); setMsg("")
    try {
      await api.patch(`/stores/${store.id}/sync-config`, {
        sync_enabled: enabled,
        sync_interval_minutes: Math.max(5, Number(orders) || 15),
        sync_products_interval_minutes: Math.max(5, Number(products) || 30),
      })
      setMsg("✓ 已保存"); onSaved()
    } catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy(false) }
  }

  if (!open) return <button className="button ghost" onClick={() => setOpen(true)}>同步配置</button>
  return (
    <div className="store-config" style={{ borderTop: "1px solid var(--border, #E6E4DF)", paddingTop: 8, marginTop: 8 }}>
      <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12 }}>
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)}/>定时同步（手动不受影响）
      </label>
      <div style={{ display: "flex", gap: 8, fontSize: 12, marginTop: 6 }}>
        <label>订单间隔(min)<input type="number" min={5} value={orders} onChange={(e) => setOrders(e.target.value)} style={{ width: 64, marginLeft: 4 }}/></label>
        <label>商品间隔(min)<input type="number" min={5} value={products} onChange={(e) => setProducts(e.target.value)} style={{ width: 64, marginLeft: 4 }}/></label>
      </div>
      <div style={{ marginTop: 6 }}>
        <button className="button primary" disabled={busy} onClick={save}>{busy ? "保存中…" : "保存"}</button>
        <button className="button ghost" onClick={() => setOpen(false)}>收起</button>
        {msg && <span style={{ marginLeft: 8, fontSize: 11 }}>{msg}</span>}
      </div>
    </div>
  )
}

function StoreAnalysisDrawer({ store, onClose }: { store: Credential; onClose: () => void }) {
  const [daily, setDaily] = useState<DailyMetricsResponse | null>(null)
  const [analytics, setAnalytics] = useState<AnalyticsDailyResponse | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    let live = true
    Promise.all([
      api.get<DailyMetricsResponse>(`/stores/${store.id}/daily-metrics?days=30`),
      api.get<AnalyticsDailyResponse>(`/stores/${store.id}/analytics-daily?days=30`),
    ]).then(([d, a]) => {
      if (!live) return
      setDaily(d); setAnalytics(a)
    }).catch((e) => { if (live) setError(apiErrorMessage(e)) })
    return () => { live = false }
  }, [store.id])

  const rows = daily?.items ?? []
  const metricNames: Record<string, string> = {
    hits_view_search: "搜索展示", hits_view_pdp: "商品卡访问",
    orders_count: "订单数", revenue: "销售额",
  }
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="product-drawer" role="dialog" aria-modal="true" aria-label="店铺分析" onMouseDown={e => e.stopPropagation()}>
        <header><div><span className="panel-kicker">STORE ANALYTICS</span><h2>店铺分析</h2></div><button onClick={onClose} aria-label="关闭">×</button></header>
        <div className="drawer-form">
          {error && <div className="inline-notice error">{error}</div>}
          {rows.length === 0 && !error && <p style={{ fontSize: 12, opacity: 0.7 }}>暂无日聚合数据（同步 1-2 次后出现）</p>}
          {rows.length > 0 && (
            <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
              <thead><tr>
                <th style={{ textAlign: "left" }}>日期</th><th>订单</th><th>销售额(₽)</th><th>净利(₽)</th><th>促销</th><th>低库存</th>
              </tr></thead>
              <tbody>
                {rows.slice(-14).map((r) => (
                  <tr key={r.stat_date} style={{ borderTop: "1px solid rgba(0,0,0,0.06)" }}>
                    <td>{r.stat_date}</td>
                    <td style={{ textAlign: "center" }}>{r.order_count}</td>
                    <td style={{ textAlign: "right" }}>{r.sales_amount != null ? formatPrice(r.sales_amount, "₽") : "—"}</td>
                    <td style={{ textAlign: "right" }}>{r.profit_amount != null ? formatPrice(r.profit_amount, "₽") : "—"}</td>
                    <td style={{ textAlign: "center" }}>{r.active_discount_count}</td>
                    <td style={{ textAlign: "center" }}>{r.low_stock_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {analytics && analytics.items.length > 0 && (
            <div style={{ marginTop: 12, fontSize: 11 }}>
              <b>Ozon 流量指标</b>
              <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 4 }}>
                <tbody>
                  {[...new Set(analytics.items.map((i) => i.metric))].map((m) => (
                    <tr key={m} style={{ borderTop: "1px solid rgba(0,0,0,0.06)" }}>
                      <td>{metricNames[m] ?? m}</td>
                      <td style={{ textAlign: "right" }}>{analytics.items.filter((i) => i.metric === m).reduce((s, i) => s + i.value, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <footer className="editor-footer"><button className="button primary" onClick={onClose}>关闭</button></footer>
      </section>
    </div>
  )
}

function StoreCard({ store, onSyncAll }: { store: Credential; onSyncAll: () => void }) {
  const [stats, setStats] = useState<StoreStats | null>(null)
  const [syncStatus, setSyncStatus] = useState<StoreSyncStatus | null>(null)
  const [busy, setBusy] = useState("")
  const [validateMsg, setValidateMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [error, setError] = useState("")
  const [lastJob, setLastJob] = useState<SyncJob | null>(null)
  const [analysisOpen, setAnalysisOpen] = useState(false)

  const loadStatus = () => api.get<StoreSyncStatus>(`/stores/${store.id}/sync-status`)
  const { data: polled } = usePolling<StoreSyncStatus>(loadStatus, 3000, !!syncStatus?.current_job)

  useEffect(() => {
    let live = true
    api.get<StoreStats>(`/stores/${store.id}/stats`)
      .then((s) => { if (live) setStats(s) })
      .catch((e) => { if (live) setError(apiErrorMessage(e)) })
    api.get<StoreSyncStatus>(`/stores/${store.id}/sync-status`)
      .then((s) => { if (live) setSyncStatus(s) })
      .catch(() => { /* 状态读取失败不阻断卡片 */ })
    return () => { live = false }
  }, [store.id])

  useEffect(() => {
    if (polled) setSyncStatus(polled)
  }, [polled])

  const refreshAll = async () => {
    try {
      const [s, st] = await Promise.all([
        api.get<StoreStats>(`/stores/${store.id}/stats`),
        api.get<StoreSyncStatus>(`/stores/${store.id}/sync-status`),
      ])
      setStats(s); setSyncStatus(st)
    } catch (e) { setError(apiErrorMessage(e)) }
  }

  const validate = async () => {
    setBusy("validate"); setValidateMsg(null); setError("")
    try {
      const res = await api.post<{ valid: boolean; reason: string }>(`/credentials/${store.id}/validate`, {})
      setValidateMsg(res.valid ? { ok: true, text: "凭证有效" } : { ok: false, text: `凭证无效：${res.reason}` })
    } catch (e) { setValidateMsg({ ok: false, text: apiErrorMessage(e) }) }
    finally { setBusy("") }
  }

  const sync = async () => {
    setBusy("sync"); setError("")
    try {
      const res = await api.post<{ job_id: number }>(`/stores/${store.id}/sync`, {})
      const jobs = await api.get<SyncJobsResponse>(`/stores/${store.id}/sync-jobs?limit=5`)
      setLastJob(jobs.items.find((j) => j.id === res.job_id) ?? null)
      await refreshAll()
    } catch (e) { setError(apiErrorMessage(e)) }
    finally { setBusy("") }
  }

  const shopName = store.shop_name || `店铺 ${store.ozon_client_id}`
  const lastSync = syncStatus?.products_last_synced_at || syncStatus?.orders_last_synced_at
  const syncError = syncStatus?.orders_error || syncStatus?.products_error

  return (
    <article className="panel store-card">
      <div className="store-card-head">
        <span className="store-logo">O</span>
        <span className={`status ${store.is_default ? "red" : "dark"}`}>{store.is_default ? "默认店铺" : store.status === "active" ? "已授权" : "未校验"}</span>
        {freshnessBadge(syncStatus ?? undefined, lastSync)}
      </div>
      <h2>{shopName}</h2>
      <p>Ozon Russia · Client-Id {store.ozon_client_id} · {store.currency}
        {store.rating_total != null ? ` · 评分 ${store.rating_total}` : ""}</p>
      {error && <div className="inline-notice error" style={{ margin: "10px 0 0" }}>{error}</div>}
      <div className="store-stats">
        <span><b>{stats ? String(stats.today_orders) : "—"}</b>今日订单</span>
        <span><b>{stats ? formatPrice(stats.today_sales_amount) : "—"}</b>销售额</span>
        <span><b>{stats ? formatPrice(stats.today_profit) : "—"}</b>今日利润</span>
      </div>
      {validateMsg && <div className="inline-notice" style={{ margin: "0 0 10px" }}>{validateMsg.text}</div>}
      <div className="store-card-actions">
        <button className="button ghost" disabled={busy === "validate"} onClick={validate}>{busy === "validate" ? "校验中…" : "校验凭证"}</button>
        <button className="button primary" disabled={busy === "sync"} onClick={sync}>{busy === "sync" ? "同步中…" : "同步数据"}</button>
        <button className="button ghost" onClick={() => setAnalysisOpen(true)}>分析</button>
      </div>
      <SyncConfigEditor store={store} onSaved={refreshAll}/>
      <small className="store-card-meta">
        最近同步：{lastSync ? formatDateTime(lastSync) : "从未同步"}
        {syncStatus?.last_success_at ? ` · 最近成功 ${formatDateTime(syncStatus.last_success_at)}` : ""}
        {syncStatus?.consecutive_failures ? ` · 连续失败 ${syncStatus.consecutive_failures}` : ""}
        {syncError ? ` · 同步错误：${syncError}` : ""}
      </small>
      <button className="create-task" onClick={refreshAll}>刷新数据</button>
      {analysisOpen && <StoreAnalysisDrawer store={store} onClose={() => setAnalysisOpen(false)}/>}
    </article>
  )
}

function AddStoreModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("")
  const [clientId, setClientId] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [currency, setCurrency] = useState("CNY")
  const [defaultStore, setDefaultStore] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  const submit = async () => {
    setError("")
    if (!clientId.trim() || !apiKey.trim()) { setError("请填写 Ozon Client-Id 和 Api-Key"); return }
    setBusy(true)
    try {
      await api.post("/credentials", {
        ozon_client_id: clientId.trim(),
        api_key: apiKey.trim(),
        shop_name: name.trim() || undefined,
        currency,
        is_default: defaultStore,
        credential_type: "api_key",
      })
      onCreated()
      onClose()
    } catch (e) { setError(apiErrorMessage(e)) }
    finally { setBusy(false) }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="product-drawer" role="dialog" aria-modal="true" aria-label="添加店铺" onMouseDown={e => e.stopPropagation()}>
        <header>
          <div><span className="panel-kicker">CONNECT OZON STORE</span><h2>添加店铺</h2></div>
          <button onClick={onClose} aria-label="关闭">×</button>
        </header>
        <div className="drawer-form">
          <label>店铺名称<input value={name} onChange={e => setName(e.target.value)} placeholder="如：深圳跨境旗舰店"/></label>
          <label>Ozon Client-Id<input value={clientId} onChange={e => setClientId(e.target.value)} placeholder="Seller 后台「设置 → API 密钥」查看"/></label>
          <label>Ozon Api-Key<input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} placeholder="Api-Key（不会回显）"/></label>
          <div className="drawer-pair">
            <label>货币
              <select value={currency} onChange={e => setCurrency(e.target.value)}>
                <option value="CNY">CNY</option>
                <option value="RUB">RUB</option>
              </select>
            </label>
            <label>设为默认店铺<input type="checkbox" checked={defaultStore} onChange={e => setDefaultStore(e.target.checked)} style={{ width: "auto", marginTop: 10 }}/></label>
          </div>
          {error && <div className="inline-notice error">{error}</div>}
        </div>
        <footer className="editor-footer">
          <button className="button ghost" onClick={onClose}>取消</button>
          <button className="button primary" disabled={busy} onClick={submit}>{busy ? "添加中…" : "添加店铺"}</button>
        </footer>
      </section>
    </div>
  )
}

export default function StoresPanel() {
  const [addOpen, setAddOpen] = useState(false)
  const [notice, setNotice] = useState("")
  const [syncAllBusy, setSyncAllBusy] = useState(false)
  const { data: stores, loading, error, reload } = useApi<Credential[]>(() => api.get("/credentials"), [])

  const syncAll = async () => {
    setSyncAllBusy(true); setNotice("")
    try {
      const r = await api.post<{ enqueued: number }>("/stores/sync-all", {})
      setNotice(`已入队 ${r.enqueued} 个店铺的同步任务`)
      setTimeout(reload, 1500)
    } catch (e) { setNotice(apiErrorMessage(e)) }
    finally { setSyncAllBusy(false) }
  }

  return (
    <>
      <PageHeader kicker="MULTI-STORE MANAGEMENT" title="店铺管理" description="集中管理多个 Ozon 店铺的授权、同步与经营状态。" action="＋ 添加店铺" onAction={() => setAddOpen(true)}/>
      {notice && <div className="panel-notice inline-notice">{notice}</div>}
      <div className="filter-bar">
        <span style={{ fontSize: 12, opacity: 0.7 }}>绑定即自动首次同步；定时按店铺配置增量拉取（订单/商品/退货/分析/评分/促销/仓库）</span>
        <button className="button primary" disabled={syncAllBusy || (stores?.length ?? 0) === 0} onClick={syncAll}>
          {syncAllBusy ? "同步中…" : "一键全店同步"}
        </button>
      </div>
      {loading && <section className="store-grid"><div className="panel"><PanelLoading text="正在读取店铺列表…"/></div></section>}
      {!loading && error && <section className="store-grid"><div className="panel"><PanelError message={error} onRetry={reload}/></div></section>}
      {!loading && !error && (stores?.length ?? 0) === 0 && (
        <section className="store-grid">
          <div className="panel"><PanelEmpty text="暂无店铺，点击「＋ 添加店铺」接入第一个 Ozon 店铺"/></div>
        </section>
      )}
      {!loading && !error && (stores?.length ?? 0) > 0 && (
        <section className="store-grid">
          {stores!.map(store => <StoreCard key={store.id} store={store} onSyncAll={syncAll}/>)}
        </section>
      )}
      {addOpen && <AddStoreModal onClose={() => setAddOpen(false)} onCreated={reload}/>}
    </>
  )
}
