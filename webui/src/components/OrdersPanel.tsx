import { useCallback, useEffect, useMemo, useState } from "react"
import { api } from "../api/client"
import type { CancelReason, Credential, OrderItem, OrderListResponse, OrderNote, ReturnsResponse } from "../api/hooks"
import { apiErrorMessage, formatDateTime, formatPrice, useApi } from "../api/hooks"
import { PageHeader, PanelEmpty, PanelError, PanelLoading } from "./ui"

const STATUS_MAP: Record<string, { label: string; cls: string }> = {
  pending: { label: "待发货", cls: "red" },
  awaiting: { label: "待发货", cls: "red" },
  waiting: { label: "待备货", cls: "dark" },
  delivering: { label: "运输中", cls: "line" },
  delivered: { label: "已完成", cls: "muted" },
  cancelled: { label: "已取消", cls: "line" },
}

function statusDisplay(s: string) {
  return STATUS_MAP[s] ?? { label: s, cls: "line" }
}

function OrderDetail({
  order,
  onClose,
  onRefresh,
}: {
  order: OrderItem
  onClose: () => void
  onRefresh: () => void
}) {
  const [tab, setTab] = useState<"info" | "notes" | "messages">("info")
  const [notes, setNotes] = useState<OrderNote | null>(null)
  const [cancelReasons, setCancelReasons] = useState<CancelReason[]>([])
  const [cancelReasonId, setCancelReasonId] = useState<number | null>(null)
  const [busy, setBusy] = useState("")
  const [msg, setMsg] = useState("")

  useEffect(() => {
    let live = true
    api.get<OrderNote>(`/orders/${order.posting_number}/notes`)
      .then((n) => { if (live) setNotes(n) })
      .catch(() => {})
    api.get<CancelReason[]>(`/orders/${order.posting_number}/cancel-reasons`)
      .then((r) => { if (live) setCancelReasons(Array.isArray(r) ? r : []) })
      .catch(() => {})
    return () => { live = false }
  }, [order.posting_number])

  const ship = async () => {
    setBusy("ship"); setMsg("")
    try {
      await api.post(`/orders/${order.posting_number}/ship`, {})
      setMsg("发货成功")
      onRefresh()
    } catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy("") }
  }

  const cancel = async () => {
    if (!cancelReasonId) { setMsg("请选择取消原因"); return }
    setBusy("cancel"); setMsg("")
    try {
      await api.post(`/orders/${order.posting_number}/cancel`, { cancel_reason_id: cancelReasonId })
      setMsg("取消成功")
      onRefresh()
    } catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy("") }
  }

  const saveNotes = async () => {
    if (!notes) return
    setBusy("notes"); setMsg("")
    try {
      await api.put(`/orders/${order.posting_number}/notes`, notes)
      setMsg("备注已保存")
    } catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy("") }
  }

  const st = statusDisplay(order.status)

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="product-drawer" role="dialog" aria-modal="true" aria-label="订单详情" onMouseDown={(e) => e.stopPropagation()}>
        <header>
          <div>
            <span className="panel-kicker">ORDER DETAIL</span>
            <h2>订单 {order.posting_number}</h2>
          </div>
          <button onClick={onClose} aria-label="关闭">×</button>
        </header>
        <nav className="drawer-tabs">
          {(["info", "notes", "messages"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)} className={tab === t ? "selected" : ""}>
              {t === "info" ? "订单信息" : t === "notes" ? "货源备注" : "买家消息"}
            </button>
          ))}
        </nav>
        {tab === "info" && (
          <div className="drawer-form">
            <div className="publish-row"><span>货件编号</span><b>{order.posting_number}</b></div>
            <div className="publish-row"><span>状态</span><b><span className={`status ${st.cls}`}>{st.label}</span></b></div>
            <div className="publish-row"><span>下单时间</span><b>{formatDateTime(order.created_at)}</b></div>
            <div className="publish-row"><span>商品数</span><b>{order.product_count}</b></div>
            <div className="publish-row"><span>订单金额</span><b>{formatPrice(order.total_amount, "₽")}</b></div>
            <div className="publish-row"><span>平台费用</span><b>{formatPrice(order.commission_amount, "₽")}</b></div>
            {order.profit != null && <div className="publish-row"><span>估算利润</span><b>{formatPrice(order.profit, "₽")}</b></div>}
            <div className="publish-row"><span>仓库</span><b>{order.warehouse || "—"}</b></div>
            <div className="publish-row"><span>配送方式</span><b>{order.delivery_method || "—"}</b></div>
            {order.products.map((p, i) => (
              <div key={i} className="publish-row"><span>商品 {i + 1}</span><b>{p.name} × {p.quantity}</b></div>
            ))}
            {order.status !== "cancelled" && (
              <>
                <div className="publish-row">
                  <span>取消原因</span>
                  <select value={cancelReasonId ?? ""} onChange={(e) => setCancelReasonId(Number(e.target.value) || null)}>
                    <option value="">选择原因…</option>
                    {cancelReasons.map((r) => <option key={r.id} value={r.id}>{r.title}</option>)}
                  </select>
                </div>
              </>
            )}
          </div>
        )}
        {tab === "notes" && notes && (
          <div className="drawer-form">
            <label>货源地址<input value={notes.source_url} onChange={(e) => setNotes({ ...notes, source_url: e.target.value })} /></label>
            <label>货源价格 (CNY)<input type="number" value={notes.source_cost ?? ""} onChange={(e) => setNotes({ ...notes, source_cost: e.target.value ? Number(e.target.value) : null })} /></label>
            <label>货源备注<input value={notes.source_remark} onChange={(e) => setNotes({ ...notes, source_remark: e.target.value })} /></label>
            <label>采购单号<input value={notes.purchase_no} onChange={(e) => setNotes({ ...notes, purchase_no: e.target.value })} /></label>
            <label>采购快递<input value={notes.purchase_carrier} onChange={(e) => setNotes({ ...notes, purchase_carrier: e.target.value })} /></label>
            <label>快递单号<input value={notes.purchase_tracking} onChange={(e) => setNotes({ ...notes, purchase_tracking: e.target.value })} /></label>
          </div>
        )}
        {tab === "messages" && (
          <div className="drawer-form">
            <p className="editor-tip"><b>买家消息</b><span>消息功能需通过 Ozon 卖家后台操作</span></p>
          </div>
        )}
        {msg && <div className={`inline-notice ${msg.includes("成功") ? "" : "error"}`}>{msg}</div>}
        <footer className="editor-footer">
          <button className="button ghost" onClick={onClose}>关闭</button>
          {tab === "notes" && <button className="button primary" onClick={saveNotes} disabled={busy === "notes"}>{busy === "notes" ? "保存中…" : "保存备注"}</button>}
          {tab === "info" && order.status !== "cancelled" && (
            <>
              <button className="button ghost" onClick={cancel} disabled={busy === "cancel"}>{busy === "cancel" ? "取消中…" : "取消订单"}</button>
              <button className="button primary" onClick={ship} disabled={busy === "ship"}>{busy === "ship" ? "发货中…" : "确认发货"}</button>
            </>
          )}
        </footer>
      </section>
    </div>
  )
}

export default function OrdersPanel() {
  const [detail, setDetail] = useState<OrderItem | null>(null)
  const [statusFilter, setStatusFilter] = useState("")
  const [page, setPage] = useState(0)
  const [view, setView] = useState<"orders" | "returns">("orders")
  const limit = 20

  const fetcher = useCallback(() =>
    api.get<OrderListResponse>(`/orders?limit=${limit}&offset=${page * limit}${statusFilter ? `&status=${statusFilter}` : ""}`),
    [page, statusFilter]
  )
  const { data, loading, error, reload } = useApi(fetcher, [page, statusFilter])
  const { data: creds } = useApi<Credential[]>(() => api.get("/credentials"), [])
  const defaultCred = useMemo(() => creds?.find((c) => c.is_default)?.id ?? creds?.[0]?.id ?? "", [creds])
  const { data: returns, loading: returnsLoading, reload: reloadReturns } = useApi<ReturnsResponse>(
    () => (defaultCred ? api.get(`/stores/${defaultCred}/returns?limit=50`) : Promise.resolve({ items: [], total: 0, limit: 50, offset: 0 })),
    [defaultCred],
  )

  const items = data?.items ?? []
  const total = data?.total ?? 0

  const batchShip = async () => {
    const selected = items.filter((o) => o.status === "pending" || o.status === "awaiting")
    if (!selected.length) return
    try {
      await api.post("/orders/batch/ship", { posting_numbers: selected.map((o) => o.posting_number) })
      reload()
    } catch { /* silent */ }
  }

  return (
    <>
      <PageHeader kicker="ORDER CENTER" title="订单中心" description={`共 ${total} 笔订单`} action="＋ 批量发货" onAction={batchShip} />
      <section className="filter-bar">
        <button className={view === "orders" ? "selected" : ""} onClick={() => setView("orders")}>订单</button>
        <button className={view === "returns" ? "selected" : ""} onClick={() => setView("returns")}>退货</button>
      </section>
      {view === "orders" ? (
        <>
      <section className="filter-bar">
        <select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setPage(0) }}>
          <option value="">全部状态</option>
          <option value="pending">待发货</option>
          <option value="delivering">运输中</option>
          <option value="delivered">已完成</option>
          <option value="cancelled">已取消</option>
        </select>
        <button className="button ghost" onClick={reload}>刷新</button>
      </section>
      {loading && <PanelLoading />}
      {error && <PanelError message={error} onRetry={reload} />}
      {!loading && !error && items.length === 0 && <PanelEmpty text="暂无订单数据" />}
      {!loading && !error && items.length > 0 && (
        <section className="wide-section">
          <article className="panel order-table">
            <div>
              <span>货件编号</span><span>商品</span><span>数量</span><span>金额</span><span>利润</span><span>下单时间</span><span>状态</span><span>操作</span>
            </div>
            {items.map((o) => {
              const st = statusDisplay(o.status)
              return (
                <div key={o.posting_number}>
                  <b>{o.posting_number}</b>
                  <span>{o.products[0]?.name ?? "—"}</span>
                  <span>{o.product_count}</span>
                  <span>{formatPrice(o.total_amount, "₽")}</span>
                  <span>{o.real_profit != null ? `${formatPrice(o.real_profit, "₽")}（净利）` : o.profit != null ? `${formatPrice(o.profit, "₽")}（毛利）` : "—"}</span>
                  <time>{formatDateTime(o.created_at)}</time>
                  <span className={`status ${st.cls}`}>{st.label}</span>
                  <span className="row-links">
                    <button onClick={() => setDetail(o)}>查看</button>
                  </span>
                </div>
              )
            })}
          </article>
          {total > limit && (
            <div className="filter-bar">
              <button className="button ghost" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>上一页</button>
              <span>第 {page + 1} / {Math.ceil(total / limit)} 页</span>
              <button className="button ghost" disabled={(page + 1) * limit >= total} onClick={() => setPage((p) => p + 1)}>下一页</button>
            </div>
          )}
        </section>
      )}
      {detail && <OrderDetail order={detail} onClose={() => setDetail(null)} onRefresh={reload} />}
        </>
      ) : (
        <>
          <section className="filter-bar">
            <button className="button ghost" onClick={reloadReturns}>刷新退货</button>
          </section>
          {returnsLoading && <PanelLoading />}
          {!returnsLoading && (returns?.items.length ?? 0) === 0 && <PanelEmpty text="暂无退货记录" />}
          {!returnsLoading && (returns?.items.length ?? 0) > 0 && (
            <section className="wide-section">
              <article className="panel order-table">
                <div><span>退货 ID</span><span>货件编号</span><span>原因</span><span>补偿状态</span><span>状态</span></div>
                {returns!.items.map((r) => (
                  <div key={r.return_id}>
                    <b>{r.return_id}</b>
                    <span>{r.posting_number || "—"}</span>
                    <span>{r.reason || "—"}</span>
                    <span>{r.compensation_status || "—"}</span>
                    <span><span className="status line">{r.status || "—"}</span></span>
                  </div>
                ))}
              </article>
            </section>
          )}
        </>
      )}
    </>
  )
}
