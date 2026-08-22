import { useEffect, useState } from "react"
import { api } from "../api/client"
import type { Credential, StoreStats, StoreSyncStatus, ValidateResponse } from "../api/hooks"
import { apiErrorMessage, formatDateTime, formatPrice, useApi } from "../api/hooks"
import { PageHeader, PanelEmpty, PanelError, PanelLoading } from "./ui"

function StoreCard({ store }: { store: Credential }) {
  const [stats, setStats] = useState<StoreStats | null>(null)
  const [syncStatus, setSyncStatus] = useState<StoreSyncStatus | null>(null)
  const [busy, setBusy] = useState("")
  const [validateMsg, setValidateMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [error, setError] = useState("")

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

  const refreshStats = async () => {
    try {
      const s = await api.get<StoreStats>(`/stores/${store.id}/stats`)
      setStats(s)
      const status = await api.get<StoreSyncStatus>(`/stores/${store.id}/sync-status`)
      setSyncStatus(status)
    } catch (e) { setError(apiErrorMessage(e)) }
  }

  const validate = async () => {
    setBusy("validate"); setValidateMsg(null); setError("")
    try {
      const res = await api.post<ValidateResponse>(`/credentials/${store.id}/validate`, {})
      setValidateMsg(res.valid ? { ok: true, text: "凭证有效" } : { ok: false, text: `凭证无效：${res.reason}` })
    } catch (e) { setValidateMsg({ ok: false, text: apiErrorMessage(e) }) }
    finally { setBusy("") }
  }

  const sync = async () => {
    setBusy("sync"); setError("")
    try {
      await api.post(`/stores/${store.id}/sync`, {})
      await refreshStats()
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
      </div>
      <h2>{shopName}</h2>
      <p>Ozon Russia · Client-Id {store.ozon_client_id} · {store.currency}</p>
      {error && <div className="inline-notice error" style={{ margin: "10px 0 0" }}>{error}</div>}
      <div className="store-stats">
        <span><b>{stats ? String(stats.today_orders) : "—"}</b>今日订单</span>
        <span><b>{stats ? formatPrice(stats.today_sales_amount) : "—"}</b>销售额</span>
        <span><b>{stats ? formatPrice(stats.today_profit) : "—"}</b>今日利润</span>
      </div>
      {validateMsg && <div className="inline-notice" style={{ margin: "0 0 10px" }}>{validateMsg.text}</div>}
      <div className="store-card-actions">
        <button className="button ghost" disabled={busy === "validate"} onClick={validate}>{busy === "validate" ? "校验中…" : "校验凭证"}</button>
        <button className="button ghost" disabled={busy === "sync"} onClick={sync}>{busy === "sync" ? "同步中…" : "同步数据"}</button>
      </div>
      <small className="store-card-meta">
        最近同步：{lastSync ? formatDateTime(lastSync) : "从未同步"}
        {syncError ? ` · 同步错误：${syncError}` : ""}
      </small>
      <button className="create-task" onClick={refreshStats}>刷新数据</button>
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
  const { data: stores, loading, error, reload } = useApi<Credential[]>(() => api.get("/credentials"), [])

  return (
    <>
      <PageHeader kicker="MULTI-STORE MANAGEMENT" title="店铺管理" description="集中管理多个 Ozon 店铺的授权、数据与经营状态。" action="＋ 添加店铺" onAction={() => setAddOpen(true)}/>
      {loading && <section className="store-grid"><div className="panel"><PanelLoading text="正在读取店铺列表…"/></div></section>}
      {!loading && error && <section className="store-grid"><div className="panel"><PanelError message={error} onRetry={reload}/></div></section>}
      {!loading && !error && (stores?.length ?? 0) === 0 && (
        <section className="store-grid">
          <div className="panel"><PanelEmpty text="暂无店铺，点击「＋ 添加店铺」接入第一个 Ozon 店铺"/></div>
        </section>
      )}
      {!loading && !error && (stores?.length ?? 0) > 0 && (
        <section className="store-grid">
          {stores!.map(store => <StoreCard key={store.id} store={store}/>)}
        </section>
      )}
      {addOpen && <AddStoreModal onClose={() => setAddOpen(false)} onCreated={reload}/>}
    </>
  )
}
