import { useState } from "react"
import { api } from "../api/client"
import type { EstimateResponse, LogisticsQuoteResponse } from "../api/hooks"
import { apiErrorMessage, formatPrice } from "../api/hooks"
import { PageHeader } from "./ui"

export default function PricingPanel() {
  const [purchaseCost, setPurchaseCost] = useState("")
  const [weightG, setWeightG] = useState("")
  const [depthCm, setDepthCm] = useState("")
  const [widthCm, setWidthCm] = useState("")
  const [heightCm, setHeightCm] = useState("")
  const [marginRate, setMarginRate] = useState("0.25")
  const [commissionRate, setCommissionRate] = useState("0.10")
  const [estResult, setEstResult] = useState<EstimateResponse | null>(null)
  const [logResult, setLogResult] = useState<LogisticsQuoteResponse | null>(null)
  const [busy, setBusy] = useState("")
  const [msg, setMsg] = useState("")

  const runEstimate = async () => {
    if (!purchaseCost) { setMsg("请输入采购价"); return }
    setBusy("est"); setMsg(""); setEstResult(null)
    try {
      const body = {
        envelope: {
          draft: {
            purchase_cost: Number(purchaseCost),
            weight: weightG ? Number(weightG) : undefined,
            dimensions: (depthCm || widthCm || heightCm) ? {
              length: Number(depthCm || 0),
              width: Number(widthCm || 0),
              height: Number(heightCm || 0),
            } : undefined,
          },
        },
        margin_rate: marginRate ? Number(marginRate) : undefined,
        commission_rate: commissionRate ? Number(commissionRate) : undefined,
      }
      const r = await api.post<EstimateResponse>("/estimate", body)
      setEstResult(r)
    } catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy("") }
  }

  const runLogistics = async () => {
    if (!weightG || !depthCm || !widthCm || !heightCm) { setMsg("物流报价需要重量和尺寸"); return }
    setBusy("log"); setMsg(""); setLogResult(null)
    try {
      const r = await api.post<LogisticsQuoteResponse>("/logistics/quote", {
        weight_g: Number(weightG),
        depth_cm: Number(depthCm),
        width_cm: Number(widthCm),
        height_cm: Number(heightCm),
      })
      setLogResult(r)
    } catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy("") }
  }

  return (
    <>
      <PageHeader kicker="AI PRICING ENGINE" title="智能定价" description="基于竞品、佣金和实时汇率，计算商品定价与利润。" />
      <section className="pricing-layout">
        <article className="panel pricing-hero">
          <span className="panel-kicker">PRICING CALCULATOR</span>
          <h2>定价计算器</h2>
          <div className="drawer-form">
            <label>采购价 (CNY)<input type="number" step="0.01" value={purchaseCost} onChange={(e) => setPurchaseCost(e.target.value)} placeholder="如 38.5" /></label>
            <label>重量 (g)<input type="number" value={weightG} onChange={(e) => setWeightG(e.target.value)} placeholder="如 300" /></label>
            <label>尺寸 (cm) — 长 × 宽 × 高</label>
            <div className="drawer-pair">
              <input type="number" value={depthCm} onChange={(e) => setDepthCm(e.target.value)} placeholder="长" />
              <input type="number" value={widthCm} onChange={(e) => setWidthCm(e.target.value)} placeholder="宽" />
              <input type="number" value={heightCm} onChange={(e) => setHeightCm(e.target.value)} placeholder="高" />
            </div>
            <label>利润率 (0-1)<input type="number" step="0.01" value={marginRate} onChange={(e) => setMarginRate(e.target.value)} /></label>
            <label>佣金率 (0-0.5)<input type="number" step="0.01" value={commissionRate} onChange={(e) => setCommissionRate(e.target.value)} /></label>
            <div className="editor-actions">
              <button className="button ghost" onClick={runLogistics} disabled={busy === "log"}>{busy === "log" ? "查询中…" : "查物流报价"}</button>
              <button className="button primary" onClick={runEstimate} disabled={busy === "est"}>{busy === "est" ? "计算中…" : "计算定价"}</button>
            </div>
          </div>
          {msg && <div className={`inline-notice ${msg.includes("需要") || msg.includes("请") ? "error" : ""}`}>{msg}</div>}
        </article>

        {estResult && (
          <article className="panel">
            <span className="panel-kicker">ESTIMATE RESULT</span>
            <h2>定价结果</h2>
            <div className="drawer-form">
              <div className="publish-row"><span>建议售价</span><b>{formatPrice(estResult.price, estResult.currency || "₽")}</b></div>
              <div className="publish-row"><span>划线价</span><b>{formatPrice(estResult.old_price, estResult.currency || "₽")}</b></div>
              {estResult.promo_price != null && <div className="publish-row"><span>促销底线</span><b>{formatPrice(estResult.promo_price, estResult.currency || "₽")}</b></div>}
              <div className="publish-row"><span>利润</span><b>{formatPrice(estResult.profit_cny, "CNY")}</b></div>
              <div className="publish-row"><span>利润率</span><b>{estResult.profit_rate != null ? `${(estResult.profit_rate * 100).toFixed(1)}%` : "—"}</b></div>
              <div className="publish-row"><span>物流费</span><b>{formatPrice(estResult.logistics_cost_cny, "CNY")}</b></div>
              <div className="publish-row"><span>佣金率</span><b>{estResult.commission_rate != null ? `${(estResult.commission_rate * 100).toFixed(1)}%` : "—"}</b></div>
              <div className="publish-row"><span>佣金来源</span><b>{estResult.commission_source || "—"}</b></div>
            </div>
          </article>
        )}

        {logResult && (
          <article className="panel">
            <span className="panel-kicker">LOGISTICS QUOTE</span>
            <h2>物流报价</h2>
            <div className="drawer-form">
              <div className="publish-row"><span>运费</span><b>{formatPrice(logResult.logistics_cost_cny, "CNY")}</b></div>
              <div className="publish-row"><span>渠道</span><b>{logResult.channel}</b></div>
              <div className="publish-row"><span>物流商</span><b>{logResult.tpl_provider_used}</b></div>
              <div className="publish-row"><span>服务等级</span><b>{logResult.service_level_used}</b></div>
              <div className="publish-row"><span>基础费</span><b>{formatPrice(logResult.base_cost, "CNY")}</b></div>
              <div className="publish-row"><span>每克费率</span><b>{formatPrice(logResult.per_gram_rate, "CNY")}</b></div>
              <div className="publish-row"><span>计费重量</span><b>{logResult.billable_weight}g</b></div>
            </div>
          </article>
        )}
      </section>
    </>
  )
}
