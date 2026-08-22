import { useEffect, useMemo, useState } from "react"
import { api, ApiError } from "../api/client"
import type { Credential, OzonProductListResponse, ProductEditResponse, ProductListResponse } from "../api/hooks"
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
          const image = f.images?.[0]
          const sourceUrl = edit.payload?.source?.purchase_url || f.purchase_url
          return (
            <ProductEditor
              product={title}
              onClose={onClose}
              source={image && sourceUrl ? { image, sourceUrl } : undefined}
            />
          )
        })()
      )}
    </div>
  )
}

type Tab = "ozon" | "shelf"

export default function ProductsPanel() {
  const [tab, setTab] = useState<Tab>("ozon")
  const [query, setQuery] = useState("")
  const [editProduct, setEditProduct] = useState<string | null>(null)

  const { data: creds } = useApi<Credential[]>(() => api.get("/credentials"), [])
  const credentialId = useMemo(
    () => creds?.find((c) => c.is_default)?.id ?? creds?.[0]?.id ?? "",
    [creds],
  )
  const { data: ozon, loading: ozonLoading, error: ozonError, reload: reloadOzon } = useApi<OzonProductListResponse>(
    () => (credentialId
      ? api.get(`/products/ozon?credential_id=${credentialId}`)
      : Promise.resolve({ items: [], total: 0, limit: 50, offset: 0 })),
    [credentialId],
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
        <div className="product-table-head"><span>商品</span><span>Product ID</span><span>价格（₽）</span><span>库存</span><span>货币</span><span>操作</span></div>
        {filteredOzon.map((p) => (
          <div className="product-table-row" key={p.product_id}>
            <div>{p.image ? <img className="source-image" src={p.image} alt={p.name} onError={(e) => { e.currentTarget.style.visibility = "hidden" }}/> : <span className="product-thumb thumb-0"/>}<b>{p.name || p.offer_id}<small>SKU: {p.offer_id}</small></b></div>
            <span>{p.product_id}</span>
            <span className="price">{p.price != null ? formatPrice(p.price, p.currency === "CNY" ? "CNY" : "₽") : "—"}</span>
            <span>{p.stock ?? "—"}</span>
            <span>{p.currency || "—"}</span>
            <span className="row-links"><button onClick={() => setEditProduct(p.product_id)}>编辑</button></span>
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
      <section className="filter-bar">
        <label>⌕ <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索商品名称 / SKU / Product ID"/></label>
        <button>选择状态⌄</button>
        <button>选择类目⌄</button>
        {tab === "ozon" && <button className="button primary" onClick={reloadOzon}>同步商品</button>}
        {tab === "shelf" && <button className="button primary" onClick={reloadShelf}>刷新货架</button>}
      </section>
      <section className="wide-section">
        {tab === "ozon" ? renderOzon() : renderShelf()}
      </section>
      {editProduct && <ProductDetail productId={editProduct} onClose={() => setEditProduct(null)}/>}
    </>
  )
}
