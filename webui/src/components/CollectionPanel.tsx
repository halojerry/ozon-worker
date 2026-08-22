import { useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router"
import { api, getSession, ApiError } from "../api/client"
import type { Credential, Draft, DraftAiResponse, DraftEnvelopeDraft, DraftPayload, EstimateResponse, SubmitResponse } from "../api/hooks"
import { apiErrorMessage, draftFields, formatDateTime, formatPrice, submissionStatusClass, submissionStatusText, useApi } from "../api/hooks"
import { Metric, PageHeader, PanelEmpty, PanelError, PanelLoading } from "./ui"

function AddDraftModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [title, setTitle] = useState("")
  const [purchaseCost, setPurchaseCost] = useState("")
  const [purchaseUrl, setPurchaseUrl] = useState("")
  const [weight, setWeight] = useState("")
  const [images, setImages] = useState("")
  const [clientId, setClientId] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  const submit = async () => {
    setError("")
    if (!title.trim()) { setError("请填写商品标题"); return }
    const imagesList = images.split("\n").map((s) => s.trim()).filter(Boolean)
    const draft: DraftEnvelopeDraft = { title: title.trim() }
    if (purchaseCost.trim()) draft.purchase_cost = Number(purchaseCost)
    if (purchaseUrl.trim()) draft.purchase_url = purchaseUrl.trim()
    if (weight.trim()) draft.weight = Number(weight)
    if (imagesList.length) draft.images = imagesList
    setBusy(true)
    try {
      await api.post("/drafts", {
        token: getSession()?.token ?? "",
        ozon_client_id: clientId.trim(),
        ozon_api_key: apiKey.trim(),
        source: "webui",
        envelope: {
          draft,
          source: {
            purchase_url: purchaseUrl.trim(),
            purchase_cost: purchaseCost.trim() ? Number(purchaseCost) : undefined,
          },
          extensions: {},
        },
      })
      onCreated()
      onClose()
    } catch (e) { setError(apiErrorMessage(e)) }
    finally { setBusy(false) }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="product-drawer" role="dialog" aria-modal="true" aria-label="添加商品" onMouseDown={e => e.stopPropagation()}>
        <header>
          <div><span className="panel-kicker">ADD TO COLLECTION</span><h2>添加商品草稿</h2></div>
          <button onClick={onClose} aria-label="关闭">×</button>
        </header>
        <div className="drawer-form">
          <label>商品标题<input value={title} onChange={e => setTitle(e.target.value)} placeholder="必填 · 采集的商品名称"/></label>
          <label>采购价（CNY）<input type="number" min="0" step="0.01" value={purchaseCost} onChange={e => setPurchaseCost(e.target.value)} placeholder="采购成本 + 国内运费"/></label>
          <label>货源地址<input value={purchaseUrl} onChange={e => setPurchaseUrl(e.target.value)} placeholder="https://detail.1688.com/offer/..."/></label>
          <label>重量（克）<input type="number" min="0" value={weight} onChange={e => setWeight(e.target.value)} placeholder="可选"/></label>
          <label>图片地址（每行一个）<textarea value={images} onChange={e => setImages(e.target.value)} placeholder="https://..."/></label>
          <div className="drawer-pair">
            <label>Ozon Client-Id（可选）<input value={clientId} onChange={e => setClientId(e.target.value)} placeholder="填则同时保存为店铺凭证"/></label>
            <label>Ozon Api-Key（可选）<input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} placeholder="不会回显"/></label>
          </div>
          {error && <div className="inline-notice error">{error}</div>}
        </div>
        <footer className="editor-footer">
          <span className="save-state">草稿仅保存信封，不含凭证明文</span>
          <button className="button ghost" onClick={onClose}>取消</button>
          <button className="button primary" disabled={busy} onClick={submit}>{busy ? "创建中…" : "创建草稿"}</button>
        </footer>
      </section>
    </div>
  )
}

function EditDraftDrawer({ draft, credentials, onClose, onSaved }: {
  draft: Draft
  credentials: Credential[]
  onClose: () => void
  onSaved: () => void
}) {
  const [detail, setDetail] = useState<Draft | null>(null)
  const [loadError, setLoadError] = useState("")
  const [title, setTitle] = useState("")
  const [description, setDescription] = useState("")
  const [purchaseCost, setPurchaseCost] = useState("")
  const [purchaseUrl, setPurchaseUrl] = useState("")
  const [weight, setWeight] = useState("")
  const [images, setImages] = useState("")
  const [aiBusy, setAiBusy] = useState("")
  const [aiNotice, setAiNotice] = useState("")
  const [aiError, setAiError] = useState("")
  const [estimate, setEstimate] = useState<EstimateResponse | null>(null)
  const [estimateBusy, setEstimateBusy] = useState(false)
  const [estimateError, setEstimateError] = useState("")
  const [credentialId, setCredentialId] = useState("")
  const [submitBusy, setSubmitBusy] = useState(false)
  const [submitResult, setSubmitResult] = useState<SubmitResponse | null>(null)
  const [submitError, setSubmitError] = useState("")
  const [saving, setSaving] = useState(false)
  const [saveNotice, setSaveNotice] = useState("")

  const loadDetail = () => {
    setLoadError("")
    api.get<Draft>(`/drafts/${draft.id}`)
      .then((d) => {
        setDetail(d)
        const f = draftFields(d)
        setTitle(f.title ?? "")
        setDescription(f.description ?? "")
        setPurchaseCost(f.purchase_cost != null ? String(f.purchase_cost) : "")
        setPurchaseUrl(f.purchase_url ?? "")
        setWeight(f.weight != null ? String(f.weight) : "")
        setImages((f.images ?? []).join("\n"))
        const defaultCred = credentials.find(c => c.is_default)?.id ?? credentials[0]?.id ?? ""
        setCredentialId((cur) => cur || defaultCred)
      })
      .catch((e) => setLoadError(apiErrorMessage(e)))
  }

  useEffect(() => { loadDetail() }, [draft.id]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!credentialId && credentials.length) {
      setCredentialId(credentials.find(c => c.is_default)?.id ?? credentials[0].id ?? "")
    }
  }, [credentials, credentialId])

  const buildEnvelope = (): DraftPayload => {
    const base = detail?.payload ?? {}
    const current = base.draft ?? {}
    const parsedCost = purchaseCost.trim() === "" ? current.purchase_cost : Number(purchaseCost)
    const parsedWeight = weight.trim() === "" ? current.weight : Number(weight)
    const imagesList = images.split("\n").map((s) => s.trim()).filter(Boolean)
    return {
      ...base,
      draft: {
        ...current,
        title: title.trim() || current.title,
        description: description.trim() || current.description,
        purchase_cost: Number.isFinite(parsedCost) ? parsedCost : current.purchase_cost,
        purchase_url: purchaseUrl.trim() || current.purchase_url,
        weight: Number.isFinite(parsedWeight) ? parsedWeight : current.weight,
        images: imagesList.length ? imagesList : current.images ?? [],
      },
    }
  }

  const save = async () => {
    if (!detail) return
    setSaving(true); setSaveNotice("")
    try {
      await api.patch<Draft>(`/drafts/${detail.id}`, { version: detail.version, payload: buildEnvelope() })
      setSaveNotice("✓ 草稿已保存")
      onSaved()
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setSaveNotice("版本冲突：草稿已被其他会话修改，已重新加载最新版本")
        loadDetail()
      } else {
        setSaveNotice(`保存失败：${apiErrorMessage(e)}`)
      }
    } finally { setSaving(false) }
  }

  const runAi = async (field: "title" | "description") => {
    setAiBusy(field); setAiError(""); setAiNotice("")
    try {
      const res = await api.post<DraftAiResponse>(`/drafts/${draft.id}/ai/${field}`, { token: getSession()?.token ?? "" })
      if (field === "title") setTitle(res.value)
      else setDescription(res.value)
      setAiNotice(`已生成${field === "title" ? "标题" : "描述"}（俄语），核对后请点击保存`)
    } catch (e) {
      if (e instanceof ApiError && e.status === 504) setAiError("AI 生成超时，请稍后重试")
      else if (e instanceof ApiError && (e.status === 422 || e.status === 400)) setAiError("当前字段为空或生成失败，请先填写内容后再试")
      else setAiError(apiErrorMessage(e))
    } finally { setAiBusy("") }
  }

  const runEstimate = async () => {
    setEstimateBusy(true); setEstimateError(""); setEstimate(null)
    try {
      const res = await api.post<EstimateResponse>(`/drafts/${draft.id}/estimate`, { token: getSession()?.token ?? "" })
      setEstimate(res)
    } catch (e) { setEstimateError(apiErrorMessage(e)) }
    finally { setEstimateBusy(false) }
  }

  const runSubmit = async () => {
    setSubmitBusy(true); setSubmitError(""); setSubmitResult(null)
    if (!credentialId) {
      setSubmitError("提交失败：请先选择店铺凭证")
      setSubmitBusy(false)
      return
    }
    try {
      const res = await api.post<SubmitResponse>(`/drafts/${draft.id}/submit`, { token: getSession()?.token ?? "", credential_id: credentialId || undefined })
      setSubmitResult(res)
      onSaved()
    } catch (e) {
      if (e instanceof ApiError && (e.status === 400 || e.status === 422)) {
        setSubmitError("提交失败：请先选择有效的店铺凭证")
      } else {
        setSubmitError(apiErrorMessage(e))
      }
    } finally { setSubmitBusy(false) }
  }

  const navigate = useNavigate()

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="product-drawer listing-editor" role="dialog" aria-modal="true" aria-label="编辑草稿" onMouseDown={e => e.stopPropagation()}>
        <header>
          <div><span className="panel-kicker">DRAFT WORKSPACE</span><h2>编辑草稿</h2></div>
          <button onClick={onClose} aria-label="关闭">×</button>
        </header>
        {loadError ? (
          <div className="drawer-form"><div className="inline-notice error">{loadError}</div>
            <footer className="editor-footer"><button className="button ghost" onClick={onClose}>关闭</button></footer>
          </div>
        ) : (
          <>
            <div className="drawer-form">
              <div className="editor-tip"><b>✦ AI 内容助手</b><span>可为空字段生成俄语标题 / 描述，生成结果需手动保存。</span></div>
              <label>商品标题<input value={title} onChange={e => setTitle(e.target.value)}/><small>保存时同步 PATCH 草稿信封</small></label>
              <div className="draft-drawer-field"><label>AI 生成标题</label><button disabled={aiBusy === "title"} onClick={() => runAi("title")}>{aiBusy === "title" ? "生成中…" : "✦ 生成"}</button></div>
              <label>商品描述<textarea value={description} onChange={e => setDescription(e.target.value)}/></label>
              <div className="draft-drawer-field"><label>AI 生成描述</label><button disabled={aiBusy === "description"} onClick={() => runAi("description")}>{aiBusy === "description" ? "生成中…" : "✦ 生成"}</button></div>
              {aiNotice && <div className="inline-notice">{aiNotice}</div>}
              {aiError && <div className="inline-notice error">{aiError}</div>}
              <div className="drawer-pair">
                <label>采购价（CNY）<input type="number" min="0" step="0.01" value={purchaseCost} onChange={e => setPurchaseCost(e.target.value)}/></label>
                <label>重量（克）<input type="number" min="0" value={weight} onChange={e => setWeight(e.target.value)}/></label>
              </div>
              <label>货源地址<input value={purchaseUrl} onChange={e => setPurchaseUrl(e.target.value)} placeholder="https://..."/></label>
              <label>图片地址（每行一个）<textarea value={images} onChange={e => setImages(e.target.value)}/></label>
              {saveNotice && <div className={`inline-notice ${saveNotice.startsWith("保存失败") || saveNotice.startsWith("版本冲突") ? "error" : ""}`}>{saveNotice}</div>}
            </div>
            <div className="drawer-form">
              <div className="draft-drawer-field">
                <label>预估售价（与 worker 定价公式同源）</label>
                {estimate ? (
                  <div className="estimate-grid">
                    <div className="estimate-cell"><span>日常价</span><b>{formatPrice(estimate.price, estimate.currency)}</b></div>
                    <div className="estimate-cell"><span>划线价</span><b>{formatPrice(estimate.old_price, estimate.currency)}</b></div>
                    <div className="estimate-cell"><span>促销底线</span><b className={estimate.promo_price ? "promo" : ""}>{estimate.promo_price != null ? formatPrice(estimate.promo_price, estimate.currency) : "—"}</b></div>
                  </div>
                ) : (
                  <button className="button ghost" style={{ position: "static", marginTop: 8 }} disabled={estimateBusy} onClick={runEstimate}>{estimateBusy ? "计算中…" : "✦ 预估售价"}</button>
                )}
                {estimate && <p>预计净利 {formatPrice(estimate.profit_cny)} CNY（{Math.round(estimate.profit_rate * 100)}%）· 佣金 {Math.round(estimate.commission_rate * 100)}% · 物流 {formatPrice(estimate.logistics_cost_cny)} CNY</p>}
                {estimateError && <div className="inline-notice error">{estimateError}</div>}
              </div>
            </div>
            <div className="drawer-form">
              <div className="publish-row"><span>提交到店铺</span><b>{credentials.length ? "选择目标店铺" : "暂无店铺凭证"}</b></div>
              {credentials.length > 0 ? (
                <select value={credentialId} onChange={e => setCredentialId(e.target.value)}>
                  <option value="">请选择店铺…</option>
                  {credentials.map(c => <option key={c.id} value={c.id}>{c.shop_name || `店铺 ${c.ozon_client_id}`}{c.is_default ? "（默认）" : ""}</option>)}
                </select>
              ) : (
                <p style={{ fontSize: 10, color: "#89847f" }}>请先在「店铺管理」添加 Ozon 店铺凭证后再提交上架。</p>
              )}
              {submitError && <div className="inline-notice error">{submitError}</div>}
              {submitResult && (
                <div className="inline-notice">
                  已提交上架，任务 ID：{submitResult.task_id || "—"}
                  {submitResult.task_id && <button className="text-button" style={{ marginLeft: 10 }} onClick={() => { onClose(); navigate("/tasks") }}>前往任务中心 →</button>}
                </div>
              )}
            </div>
            <footer className="editor-footer">
              <span className="save-state">{detail ? `version ${detail.version}` : ""}</span>
              <button className="button ghost" onClick={onClose}>关闭</button>
              <button className="button ghost" disabled={saving} onClick={save}>{saving ? "保存中…" : "保存草稿"}</button>
              <button className="button primary" disabled={submitBusy || credentials.length === 0} onClick={runSubmit}>{submitBusy ? "提交中…" : "提交上架"}</button>
            </footer>
          </>
        )}
      </section>
    </div>
  )
}

export default function CollectionPanel() {
  const { data: drafts, loading, error, reload } = useApi<Draft[]>(() => api.get("/drafts"), [])
  const { data: credentials } = useApi<Credential[]>(() => api.get("/credentials"), [])
  const [q, setQ] = useState("")
  const [addOpen, setAddOpen] = useState(false)
  const [editing, setEditing] = useState<Draft | null>(null)
  const [notice, setNotice] = useState("")
  const [noticeError, setNoticeError] = useState(false)

  const flashNotice = (text: string, isError: boolean) => { setNotice(text); setNoticeError(isError) }

  const filtered = useMemo(() => {
    const list = drafts ?? []
    if (!q.trim()) return list
    const needle = q.toLowerCase()
    return list.filter((d) => {
      const f = draftFields(d)
      return `${d.id} ${f.title ?? ""} ${f.item_id ?? ""} ${f.purchase_url ?? ""}`.toLowerCase().includes(needle)
    })
  }, [drafts, q])

  const metrics = useMemo(() => {
    const list = drafts ?? []
    return {
      total: list.length,
      pending: list.filter((d) => !d.submission_status).length,
      submitted: list.filter((d) => d.submission_status && d.submission_status !== "published").length,
      published: list.filter((d) => d.submission_status === "published").length,
    }
  }, [drafts])

  const remove = async (d: Draft) => {
    const f = draftFields(d)
    if (!window.confirm(`确认删除草稿「${f.title || d.id}」？`)) return
    try {
      await api.delete(`/drafts/${d.id}`)
      flashNotice("草稿已删除", false)
      reload()
    } catch (e) { flashNotice(apiErrorMessage(e), true) }
  }

  return (
    <>
      <PageHeader kicker="PRODUCT SOURCING CENTER" title="采集箱" description="商品采集中心 · 编辑货源、AI 生成俄语内容、预估售价并提交上架。" action="＋ 添加商品" onAction={() => setAddOpen(true)}/>
      {notice && <div className={`panel-notice inline-notice ${noticeError ? "error" : ""}`}>{notice}</div>}
      <section className="metric-grid">
        <Metric label="已采集" value={String(metrics.total)} note="草稿总数" red/>
        <Metric label="待导入" value={String(metrics.pending)} note="尚未提交上架" red/>
        <Metric label="已提交" value={String(metrics.submitted)} note="排队 / 上传 / 失败"/>
        <Metric label="已上架" value={String(metrics.published)} note="发布成功"/>
      </section>
      <section className="filter-bar">
        <label>⌕ <input value={q} onChange={e => setQ(e.target.value)} placeholder="搜索标题 / ID / 货源地址"/></label>
        <button>全部平台⌄</button>
        <button>全部状态⌄</button>
        <button className="button primary" onClick={() => setAddOpen(true)}>＋ 添加商品</button>
      </section>
      <section className="wide-section">
        {loading ? <div className="panel"><PanelLoading text="正在读取采集箱…"/></div>
          : error ? <div className="panel"><PanelError message={error} onRetry={reload}/></div>
          : filtered.length === 0 ? <div className="panel"><PanelEmpty text={q ? "没有匹配的草稿" : "采集箱为空，点击「＋ 添加商品」创建草稿"}/></div>
          : <article className="panel source-table">
              <div className="source-head"><span>商品信息</span><span>来源平台 / 货源地址</span><span>价格</span><span>采集状态</span><span>导入状态</span><span>采集时间</span><span>操作</span></div>
              {filtered.map((d) => {
                const f = draftFields(d)
                const image = f.images?.[0]
                const cost = f.purchase_cost
                return (
                  <div className="source-row" key={d.id}>
                    <div>{image ? <img className="source-image" src={image} alt={f.title || ""}/> : <span className="product-thumb thumb-0"/>}<b>{f.title || "未命名草稿"}<small>ID：{d.id.slice(0, 8)}</small></b></div>
                    <div className="platform-cell"><em>{d.source === "webui" ? "WebUI" : d.source || "ERP"}</em>{f.purchase_url ? <a href={f.purchase_url} target="_blank" rel="noreferrer">货源地址 ↗</a> : <span>—</span>}</div>
                    <span>{cost ? `¥ ${formatPrice(cost)}` : "—"}</span>
                    <span className="status green">已采集</span>
                    <span className={`status ${submissionStatusClass(d.submission_status)}`}>{submissionStatusText(d.submission_status)}</span>
                    <time>{formatDateTime(d.created_at)}</time>
                    <span className="row-links">
                      <button onClick={() => setEditing(d)}>编辑</button>
                      <button onClick={() => remove(d)}>删除</button>
                    </span>
                  </div>
                )
              })}
            </article>}
      </section>
      {addOpen && <AddDraftModal onClose={() => setAddOpen(false)} onCreated={() => { reload(); flashNotice("草稿已创建", false) }}/>}
      {editing && <EditDraftDrawer draft={editing} credentials={credentials ?? []} onClose={() => setEditing(null)} onSaved={reload}/>}
    </>
  )
}
