import { useCallback, useState } from "react"
import { api } from "../api/client"
import type { Template } from "../api/hooks"
import { apiErrorMessage, useApi } from "../api/hooks"
import { PageHeader, PanelEmpty, PanelError, PanelLoading } from "./ui"

function TemplateCard({
  tmpl,
  onEdit,
  onSetDefault,
  onDelete,
}: {
  tmpl: Template
  onEdit: (t: Template) => void
  onSetDefault: (id: string) => void
  onDelete: (id: string) => void
}) {
  const cfg = tmpl.config ?? {}
  const threeTier = cfg.margin_anchor != null || cfg.margin_floor != null ? "三档定价" : null
  const meta = [
    threeTier,
    cfg.margin_rate != null ? `利润率 ${(cfg.margin_rate * 100).toFixed(0)}%` : null,
    cfg.commission_rate != null ? `佣金 ${(cfg.commission_rate * 100).toFixed(0)}%` : null,
    cfg.fx_buffer != null ? `汇率缓冲 ${(cfg.fx_buffer * 100).toFixed(1)}%` : null,
  ].filter(Boolean).join(" · ")

  return (
    <article className="panel template-card">
      <span className="template-index">{tmpl.is_default ? "★" : "○"}</span>
      {tmpl.is_default && <span className="status red">默认</span>}
      <h2>{tmpl.name}</h2>
      <p>{tmpl.description || "无描述"}</p>
      {meta && <div className="template-meta">{meta}</div>}
      <div className="template-actions">
        <button className="text-button" onClick={() => onEdit(tmpl)}>编辑模板 →</button>
        {!tmpl.is_default && <button className="text-button" onClick={() => onSetDefault(tmpl.id)}>设为默认</button>}
        <button className="text-button text-danger" onClick={() => onDelete(tmpl.id)}>删除</button>
      </div>
    </article>
  )
}

function TemplateEditor({
  tmpl,
  onClose,
  onSaved,
}: {
  tmpl: Template | null
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(tmpl?.name ?? "")
  const [desc, setDesc] = useState(tmpl?.description ?? "")
  const [marginRate, setMarginRate] = useState(String(tmpl?.config?.margin_rate ?? ""))
  const [commissionRate, setCommissionRate] = useState(String(tmpl?.config?.commission_rate ?? ""))
  const [fxBuffer, setFxBuffer] = useState(String(tmpl?.config?.fx_buffer ?? ""))
  const [marginAnchor, setMarginAnchor] = useState(String(tmpl?.config?.margin_anchor ?? ""))
  const [marginFloor, setMarginFloor] = useState(String(tmpl?.config?.margin_floor ?? ""))
  const [variableCostRate, setVariableCostRate] = useState(String(tmpl?.config?.variable_cost_rate ?? ""))
  const [promoVariableCostRate, setPromoVariableCostRate] = useState(String(tmpl?.config?.promo_variable_cost_rate ?? ""))
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState("")

  const save = async () => {
    setBusy(true); setMsg("")
    const body: Record<string, unknown> = {
      name: name.trim(),
      description: desc,
      config: {},
    }
    if (marginRate) (body.config as Record<string, unknown>).margin_rate = Number(marginRate)
    if (commissionRate) (body.config as Record<string, unknown>).commission_rate = Number(commissionRate)
    if (fxBuffer) (body.config as Record<string, unknown>).fx_buffer = Number(fxBuffer)
    if (marginAnchor) (body.config as Record<string, unknown>).margin_anchor = Number(marginAnchor)
    if (marginFloor) (body.config as Record<string, unknown>).margin_floor = Number(marginFloor)
    if (variableCostRate) (body.config as Record<string, unknown>).variable_cost_rate = Number(variableCostRate)
    if (promoVariableCostRate) (body.config as Record<string, unknown>).promo_variable_cost_rate = Number(promoVariableCostRate)
    try {
      if (tmpl) {
        await api.patch(`/templates/${tmpl.id}`, body)
      } else {
        await api.post("/templates", body)
      }
      onSaved()
      onClose()
    } catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy(false) }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="product-drawer" role="dialog" aria-modal="true" aria-label="编辑模板" onMouseDown={(e) => e.stopPropagation()}>
        <header>
          <div>
            <span className="panel-kicker">TEMPLATE EDITOR</span>
            <h2>{tmpl ? "编辑模板" : "新建模板"}</h2>
          </div>
          <button onClick={onClose} aria-label="关闭">×</button>
        </header>
        <div className="drawer-form">
          <label>模板名称<input value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label>模板描述<textarea value={desc} onChange={(e) => setDesc(e.target.value)} /></label>
          <label>利润率 (0-1)<input type="number" step="0.01" value={marginRate} onChange={(e) => setMarginRate(e.target.value)} placeholder="如 0.25" /></label>
          <label>佣金率 (0-0.5)<input type="number" step="0.01" value={commissionRate} onChange={(e) => setCommissionRate(e.target.value)} placeholder="如 0.15" /></label>
          <label>汇率缓冲 (0-0.5)<input type="number" step="0.01" value={fxBuffer} onChange={(e) => setFxBuffer(e.target.value)} placeholder="如 0.05" /></label>
          <label>划线锚点 margin_anchor (0-5)<input type="number" min="0" max="5" step="0.1" value={marginAnchor} onChange={(e) => setMarginAnchor(e.target.value)} placeholder="如 2.0（留空走 worker 默认）" /></label>
          <label>促销底线 margin_floor (0-2)<input type="number" min="0" max="2" step="0.05" value={marginFloor} onChange={(e) => setMarginFloor(e.target.value)} placeholder="如 0.6（留空走 worker 默认）" /></label>
          <label>日常变动成本率 variable_cost_rate (0-0.5)<input type="number" min="0" max="0.5" step="0.005" value={variableCostRate} onChange={(e) => setVariableCostRate(e.target.value)} placeholder="如 0.155（留空走 worker 默认）" /></label>
          <label>促销变动成本率 promo_variable_cost_rate (0-0.5)<input type="number" min="0" max="0.5" step="0.005" value={promoVariableCostRate} onChange={(e) => setPromoVariableCostRate(e.target.value)} placeholder="如 0.245（留空走 worker 默认）" /></label>
        </div>
        {msg && <div className="inline-notice error">{msg}</div>}
        <footer className="editor-footer">
          <button className="button ghost" onClick={onClose}>取消</button>
          <button className="button primary" onClick={save} disabled={busy || !name.trim()}>{busy ? "保存中…" : "保存"}</button>
        </footer>
      </section>
    </div>
  )
}

export default function TemplatesPanel() {
  const [editing, setEditing] = useState<Template | null>(null)
  const [creating, setCreating] = useState(false)

  const fetcher = useCallback(() => api.get<Template[]>("/templates"), [])
  const { data, loading, error, reload } = useApi(fetcher)

  const list = data ?? []

  const setDefault = async (id: string) => {
    try { await api.post(`/templates/${id}/default`, {}); reload() } catch { /* silent */ }
  }

  const deleteTmpl = async (id: string) => {
    try { await api.delete(`/templates/${id}`); reload() } catch { /* silent */ }
  }

  return (
    <>
      <PageHeader kicker="LISTING CONFIGURATION" title="上架模板" description="通过模板统一商品的利润、库存、物流与内容策略。" action="＋ 新建模板" onAction={() => setCreating(true)} />
      {loading && <PanelLoading />}
      {error && <PanelError message={error} onRetry={reload} />}
      {!loading && !error && list.length === 0 && <PanelEmpty text="暂无模板，点击上方按钮创建" />}
      {!loading && !error && list.length > 0 && (
        <section className="template-grid">
          {list.map((t) => (
            <TemplateCard key={t.id} tmpl={t} onEdit={setEditing} onSetDefault={setDefault} onDelete={deleteTmpl} />
          ))}
        </section>
      )}
      {(editing || creating) && (
        <TemplateEditor
          tmpl={editing}
          onClose={() => { setEditing(null); setCreating(false) }}
          onSaved={reload}
        />
      )}
    </>
  )
}
