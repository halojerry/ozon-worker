import { useState } from "react"
import { api, getSession } from "../api/client"
import { apiErrorMessage, draftFieldsFromPayload } from "../api/hooks"
import type { DraftEnvelopeDraft, DraftPayload } from "../api/hooks"

export default function ProductEditor({
  product,
  productId,
  draftId,
  draftVersion,
  credentialId,
  payload,
  onClose,
  onSaved,
}: {
  product: string
  productId: string
  draftId: string
  draftVersion: number
  credentialId: string
  payload: DraftPayload
  onClose: () => void
  onSaved?: () => void
}) {
  const f = draftFieldsFromPayload(payload)
  const [tab, setTab] = useState("基础信息")
  const [title, setTitle] = useState(f.title || product)
  const [sourceUrl, setSourceUrl] = useState(payload.source?.purchase_url ?? f.purchase_url ?? "")
  const [imagesText, setImagesText] = useState((f.images ?? []).join("\n"))
  const [sku, setSku] = useState(f.sku_id ?? f.item_id ?? "")
  const [description, setDescription] = useState(f.description ?? "")
  const [busy, setBusy] = useState("")
  const [msg, setMsg] = useState("")

  const buildPayload = (): DraftPayload => {
    const next: DraftPayload = JSON.parse(JSON.stringify(payload))
    const draft: DraftEnvelopeDraft = next.draft ?? (next.draft = {})
    draft.title = title.trim() || draft.title
    draft.images = imagesText.split("\n").map((s) => s.trim()).filter(Boolean)
    if (sku.trim()) draft.sku_id = sku.trim()
    if (description.trim()) draft.description = description.trim()
    if (sourceUrl.trim()) {
      next.source = { ...(next.source ?? {}), purchase_url: sourceUrl.trim() }
    }
    return next
  }

  const save = async () => {
    setBusy("save"); setMsg("")
    try {
      await api.patch(`/drafts/${draftId}`, { version: draftVersion, payload: buildPayload() })
      setMsg("✓ 草稿已保存")
      onSaved?.()
    } catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy("") }
  }

  const publish = async () => {
    setBusy("publish"); setMsg("")
    try {
      await save()
      const res = await api.post<{ task_id?: string; status?: string; ok?: boolean }>(`/drafts/${draftId}/submit`, {
        token: getSession()?.token ?? "",
        credential_id: credentialId,
        update_product_id: productId,
      })
      setMsg(`✓ 已提交更新上架(task: ${res.task_id ?? res.status ?? ""})`)
      onSaved?.()
    } catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy("") }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="product-drawer listing-editor" role="dialog" aria-modal="true" aria-label="编辑商品" onMouseDown={(e) => e.stopPropagation()}>
        <header>
          <div><span className="panel-kicker">PRODUCT WORKSPACE</span><h2>编辑商品</h2></div>
          <button onClick={onClose} aria-label="关闭">×</button>
        </header>
        <div className="drawer-product">
          {f.images?.[0] ? <img className="product-thumb" src={f.images[0]} alt="" style={{ objectFit: "cover" }} /> : <div className="product-thumb thumb-0" />}
          <div><b>{title}</b><small>Product ID: {productId} · 关联草稿: {draftId.slice(0, 8)}</small></div>
          <span className="status red">待发布</span>
        </div>
        <nav className="drawer-tabs">
          {["基础信息", "图文素材"].map((x) => (
            <button key={x} onClick={() => setTab(x)} className={tab === x ? "selected" : ""}>{x}</button>
          ))}
        </nav>
        {tab === "基础信息" && (
          <div className="drawer-form">
            <label>商品标题<input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
            <label>货源地址<input value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} placeholder="https://detail.1688.com/offer/..." /></label>
            <label>SKU<input value={sku} onChange={(e) => setSku(e.target.value)} /></label>
            <label>图片地址(每行一个)<textarea value={imagesText} onChange={(e) => setImagesText(e.target.value)} rows={4} /></label>
            <label>商品卖点<textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} /></label>
          </div>
        )}
        {tab === "图文素材" && (
          <div className="media-workspace">
            <div className="media-head"><b>商品素材</b></div>
            <div className="media-grid">
              {(f.images ?? []).map((img, i) => (
                <div className="media-item main" key={i}>
                  <img className="editor-source-image" src={img} alt={`图${i + 1}`} style={{ objectFit: "cover", width: "100%", height: "100%" }} />
                  <b>{i === 0 ? "主图" : `图 ${i + 1}`}</b>
                </div>
              ))}
              {(f.images ?? []).length === 0 && <div className="media-item"><span>＋</span><b>无图片</b></div>}
            </div>
            <p style={{ fontSize: 11, opacity: 0.7 }}>图片列表在「基础信息」中编辑;在线改图重传请用商品列表的更新图片能力。</p>
          </div>
        )}
        <footer className="editor-footer">
          <span className="save-state">{msg || (busy ? "处理中…" : "修改后先保存草稿,再提交更新上架")}</span>
          <button className="button ghost" onClick={onClose}>关闭</button>
          <button className="button ghost" disabled={!!busy} onClick={save}>{busy === "save" ? "保存中…" : "保存草稿"}</button>
          <button className="button primary" disabled={!!busy} onClick={publish}>{busy === "publish" ? "提交中…" : "保存并更新上架"}</button>
        </footer>
      </section>
    </div>
  )
}
