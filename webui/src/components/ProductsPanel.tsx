import { useEffect, useMemo, useState } from "react"
import { api, ApiError } from "../api/client"
import type { Credential, OzonProduct, OzonProductListResponse, ProductCost, ProductEditResponse, ProductListResponse } from "../api/hooks"
import { apiErrorMessage, draftFieldsFromPayload, formatDateTime, formatPrice, useApi } from "../api/hooks"
import { PageHeader, PanelEmpty, PanelError, PanelLoading } from "./ui"
import ProductEditor from "./ProductEditor"

function ProductDetail({ productId, onClose }: { productId: string; onClose: () => void }) {
  const [edit, setEdit] = useState<ProductEditResponse | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    let live = true
    setError(""); setEdit(null)
    api.get<ProductEditResponse>(`/products/${productId}/edit`)
      .then((d) => { if (live) setEdit(d) })
      .catch((e) => { if (live) setError(e instanceof ApiError && e.status === 404 ? "商品不存在" : apiErrorMessage(e)) })
    return () => { live = false }
  }, [productId])

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      {error ? (
        <section className="product-drawer" role="dialog" aria-modal="true" aria-label="商品未找到" onMouseDown={e => e.stopPropagation()}>
          <header><div><span className="panel-kicker">PRODUCT EDIT</span><h2>商品未找到</h2></div><button onClick={onClose} aria-label="关闭">×</button></header>
          <div className="drawer-form"><div className="inline-notice error">{error}（product not found）</div></div>
          <footer className="editor-footer"><button className="button ghost" onClick={onClose}>关闭</button></footer>
        </section>
      ) : !edit ? (
        <section className="product-drawer" role="dialog" aria-modal="true" aria-label="加载中" onMouseDown={e => e.stopPropagation()}>
          <header><div><span className="panel-kicker">PRODUCT EDIT</span><h2>编辑商品</h2></div><button onClick={onClose} aria-label="关闭">×</button></header>
          <div className="drawer-form"><PanelLoading text="读取商品编辑初值…"/></div>
        </section>
      ) : (
        (() => {
          const f = draftFieldsFromPayload(edit.payload)
          const title = f.title || edit.offer_id || edit.product_id
          return (
            <ProductEditor
              product={title}
              productId={edit.product_id}
              draftId={edit.draft_id}
              draftVersion={edit.draft_version ?? 1}
              credentialId={edit.credential_id ?? ""}
              payload={edit.payload}
              onClose={onClose}
            />
          )
        })()
      )}
    </div>
  )
}

type Tab = "ozon" | "shelf"

function CostEditor({ product, credentialId, onClose }: { product: OzonProduct; credentialId: string; onClose: () => void }) {
  const [cost, setCost] = useState<ProductCost | null>(null)
  const [candidates, setCandidates] = useState<Array<{ source_offer_id: string; source_url: string; price_cny: number | null; match_score: number | null; match_method: string; status: string }>>([])
  const [url, setUrl] = useState("")
  const [price, setPrice] = useState("")
  const [supplier, setSupplier] = useState("")
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState("")

  useEffect(() => {
    api.get<ProductCost>(`/products/${product.product_id}/cost?credential_id=${credentialId}`)
      .then((c) => { setCost(c); setUrl(c.purchase_url ?? ""); setPrice(c.purchase_cost != null ? String(c.purchase_cost) : ""); setSupplier(c.supplier ?? "") })
      .catch(() => { /* 首次无成本记录 */ })
    api.get<Array<{ source_offer_id: string; source_url: string; price_cny: number | null; match_score: number | null; match_method: string; status: string }>>(`/products/${product.product_id}/source-candidates?credential_id=${credentialId}`)
      .then(setCandidates)
      .catch(() => { /* 无候选 */ })
  }, [product.product_id, credentialId])

  const save = async () => {
    setBusy(true); setMsg("")
    try {
      const c = await api.patch<ProductCost>(`/products/${product.product_id}/source`, {
        credential_id: credentialId,
        purchase_url: url,
        purchase_cost: Number(price),
        supplier,
      })
      setCost(c); setMsg("✓ 已保存并重算相关订单利润")
    } catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy(false) }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="product-drawer" role="dialog" aria-modal="true" aria-label="成本货源" onMouseDown={e => e.stopPropagation()}>
        <header><div><span className="panel-kicker">SOURCE & COST</span><h2>货源 / 成本</h2></div><button onClick={onClose} aria-label="关闭">×</button></header>
        <div className="drawer-form">
          <label>商品<b>{product.name || product.offer_id}</b><small>Product ID: {product.product_id} · 来源：{cost?.cost_source ?? "未设置"}</small></label>
          <label>1688 货源链接<input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://detail.1688.com/offer/..."/></label>
          <label>到仓成本 CNY（含国内运费）<input type="number" min="0" step="0.01" value={price} onChange={e => setPrice(e.target.value)}/></label>
          <label>供应商<input value={supplier} onChange={e => setSupplier(e.target.value)} placeholder="1688 店铺名"/></label>
          {cost && cost.history.length > 0 && (
            <div style={{ fontSize: 11, opacity: 0.8 }}>
              <b>成本历史</b>
              {cost.history.slice(0, 5).map((h, i) => (
                <div key={i}>{h.old_cost ?? "—"} → {h.new_cost ?? "—"} · {h.changed_by} · {h.changed_at ?? ""}</div>
              ))}
            </div>
          )}
          {candidates.length > 0 && (
            <div style={{ fontSize: 11, opacity: 0.85 }}>
              <b>匹配候选(skill/discover 上报)</b>
              {candidates.slice(0, 8).map((c, i) => (
                <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 4 }}>
                  <a href={c.source_url} target="_blank" rel="noreferrer">1688 货源 ↗</a>
                  <span>¥{c.price_cny ?? "—"}</span>
                  <span>匹配度 {c.match_score ?? "—"}</span>
                  <span className="status">{c.match_method}</span>
                </div>
              ))}
            </div>
          )}
          {msg && <div className="inline-notice">{msg}</div>}
        </div>
        <footer className="editor-footer">
          <button className="button ghost" onClick={onClose}>关闭</button>
          <button className="button primary" disabled={busy} onClick={save}>{busy ? "保存中…" : "保存成本"}</button>
        </footer>
      </section>
    </div>
  )
}

export default function ProductsPanel() {
  const [tab, setTab] = useState<Tab>("ozon")
  const [statusFilter, setStatusFilter] = useState("all")
  const [sourceFilter, setSourceFilter] = useState("all")
  const [query, setQuery] = useState("")
  const [editProduct, setEditProduct] = useState<string | null>(null)
  const [costProduct, setCostProduct] = useState<OzonProduct | null>(null)

  const { data: creds } = useApi<Credential[]>(() => api.get("/credentials"), [])
  const credentialId = useMemo(
    () => creds?.find((c) => c.is_default)?.id ?? creds?.[0]?.id ?? "",
    [creds],
  )
  const { data: ozon, loading: ozonLoading, error: ozonError, reload: reloadOzon } = useApi<OzonProductListResponse>(
    () => (credentialId
      ? api.get(`/products/ozon?credential_id=${credentialId}&status=${statusFilter}&source=${sourceFilter}`)
      : Promise.resolve({ items: [], total: 0, limit: 50, offset: 0 })),
    [credentialId, statusFilter, sourceFilter],
  )
  const { data: shelf, loading: shelfLoading, error: shelfError, reload: reloadShelf } = useApi<ProductListResponse>(
    () => api.get("/products"),
    [],
  )

  const filteredOzon = useMemo(() => {
    const list = ozon?.items ?? []
    if (!query.trim()) return list
    const needle = query.toLowerCase()
    return list.filter((p) => `${p.name} ${p.offer_id} ${p.product_id}`.toLowerCase().includes(needle))
  }, [ozon, query])

  const filteredShelf = useMemo(() => {
    const list = shelf?.items ?? []
    if (!query.trim()) return list
    const needle = query.toLowerCase()
    return list.filter((p) => `${p.offer_id} ${p.product_id}`.toLowerCase().includes(needle))
  }, [shelf, query])

  const renderOzon = () => {
    if (!credentialId) return <div className="panel"><PanelEmpty text="暂无店铺凭证，请先在「店铺管理」添加 Ozon 店铺"/></div>
    if (ozonLoading) return <div className="panel"><PanelLoading text="正在读取 Ozon 在线商品…"/></div>
    if (ozonError) return <div className="panel"><PanelError message={ozonError} onRetry={reloadOzon}/></div>
    if (filteredOzon.length === 0) return <div className="panel"><PanelEmpty text={query ? "没有匹配的商品" : "该店铺暂无在线商品，可点击「同步」刷新"}/></div>
    return (
      <article className="panel product-table">
        <div className="product-table-head"><span>商品</span><span>Product ID</span><span>状态</span><span>售价（₽）</span><span>划线价</span><span>最低价</span><span>库存</span><span>操作</span></div>
        {filteredOzon.map((p) => (
          <div className="product-table-row" key={p.product_id}>
            <div>{p.image ? <img className="source-image" src={p.image} alt={p.name} onError={(e) => { e.currentTarget.style.visibility = "hidden" }}/> : <span className="product-thumb thumb-0"/>}<b>{p.name || p.offer_id}<small>SKU: {p.offer_id}</small></b></div>
            <span>{p.product_id}</span>
            <span><span className={`status ${p.status === "archived" ? "line" : p.status === "error" ? "red" : "green"}`}>{p.status || "visible"}</span></span>
            <span className="price">{p.price != null ? formatPrice(p.price, p.currency === "CNY" ? "CNY" : "₽") : "—"}</span>
            <span>{p.old_price != null ? formatPrice(p.old_price, "₽") : "—"}</span>
            <span>{p.min_price != null ? formatPrice(p.min_price, "₽") : "—"}</span>
            <span>{p.stock ?? "—"}</span>
            <span className="row-links">
              <button onClick={() => setEditProduct(p.product_id)}>编辑</button>
              {credentialId && <button onClick={() => setCostProduct(p)}>成本/货源</button>}
            </span>
          </div>
        ))}
        {ozon?.last_synced_at && <div className="empty-state" style={{ textAlign: "left", fontSize: 10 }}>最近同步：{formatDateTime(ozon.last_synced_at)}{ozon.sync_error ? ` · 同步错误：${ozon.sync_error}` : ""}</div>}
      </article>
    )
  }

  const renderShelf = () => {
    if (shelfLoading) return <div className="panel"><PanelLoading text="正在读取在售货架…"/></div>
    if (shelfError) return <div className="panel"><PanelError message={shelfError} onRetry={reloadShelf}/></div>
    if (filteredShelf.length === 0) return <div className="panel"><PanelEmpty text={query ? "没有匹配的商品" : "在售货架暂无记录"}/></div>
    return (
      <article className="panel product-table">
        <div className="product-table-head"><span>货号 / Product ID</span><span>审核状态</span><span>关联草稿</span><span>创建时间</span><span>任务 ID</span><span>操作</span></div>
        {filteredShelf.map((p) => (
          <div className="product-table-row" key={p.product_id}>
            <div><span className="product-thumb thumb-0"/><b>{p.offer_id}<small>Product ID: {p.product_id}</small></b></div>
            <span><span className={`status ${p.moderation_status === "approved" ? "red" : "line"}`}>{p.moderation_status || "未知"}</span></span>
            <span>{p.draft_id ? p.draft_id.slice(0, 8) : "—"}</span>
            <span>{formatDateTime(p.created_at)}</span>
            <span>{p.task_id.slice(0, 8)}</span>
            <span className="row-links"><button onClick={() => setEditProduct(p.product_id)}>编辑</button></span>
          </div>
        ))}
      </article>
    )
  }

  return (
    <>
      <PageHeader kicker="PRODUCT CATALOG" title="商品管理" description="查看 Ozon 在线商品与本地在售货架；点击「编辑」读取商品详情初值。" action="＋ 新建商品"/>
      <div className="tab-bar">
        <button className={tab === "ozon" ? "selected" : ""} onClick={() => setTab("ozon")}>Ozon 在线商品</button>
        <button className={tab === "shelf" ? "selected" : ""} onClick={() => setTab("shelf")}>在售货架</button>
      </div>
      {tab === "ozon" && (
        <div className="tab-bar" style={{ marginTop: 4, flexWrap: "wrap", gap: 4 }}>
          {(["all", "visible", "archived", "error"] as const).map((s) => (
            <button key={s} className={statusFilter === s ? "selected" : ""} onClick={() => setStatusFilter(s)}>
              {s === "all" ? "全部" : s === "visible" ? "在售" : s === "archived" ? "归档" : "错误"}
            </button>
          ))}
          <span style={{ opacity: 0.5, margin: "0 4px" }}>|</span>
          {(["all", "matched", "unmatched"] as const).map((s) => (
            <button key={s} className={sourceFilter === s ? "selected" : ""} onClick={() => setSourceFilter(s)}>
              {s === "all" ? "货源全部" : s === "matched" ? "已匹配货源" : "未匹配货源"}
            </button>
          ))}
        </div>
      )}
      <section className="filter-bar">
        <label>⌕ <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索商品名称 / SKU / Product ID"/></label>
        {tab === "ozon" && <button className="button primary" onClick={reloadOzon}>同步商品</button>}
        {tab === "shelf" && <button className="button primary" onClick={reloadShelf}>刷新货架</button>}
      </section>
      <section className="wide-section">
        {tab === "ozon" ? renderOzon() : renderShelf()}
      </section>
      {editProduct && <ProductDetail productId={editProduct} onClose={() => setEditProduct(null)}/>}
      {costProduct && credentialId && <CostEditor product={costProduct} credentialId={credentialId} onClose={() => setCostProduct(null)}/>}
    </>
  )
}
