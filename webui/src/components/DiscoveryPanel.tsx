import { useCallback, useState } from "react"
import { api } from "../api/client"
import type { DiscoveryRun, DiscoveryRunsResponse, MappingLookupResult, SeoKeywordsResponse } from "../api/hooks"
import { apiErrorMessage, formatDateTime, useApi } from "../api/hooks"
import { PageHeader, PanelEmpty, PanelError, PanelLoading } from "./ui"

export default function DiscoveryPanel() {
  const [tab, setTab] = useState<"runs" | "mappings" | "seo">("runs")
  const [detail, setDetail] = useState<DiscoveryRun | null>(null)
  const [keyword, setKeyword] = useState("")
  const [mappingResult, setMappingResult] = useState<MappingLookupResult | null>(null)
  const [seoResult, setSeoResult] = useState<SeoKeywordsResponse | null>(null)
  const [busy, setBusy] = useState("")
  const [msg, setMsg] = useState("")

  const fetcher = useCallback(() => api.get<DiscoveryRunsResponse>("/discovery/runs?limit=50"), [])
  const { data, loading, error, reload } = useApi(fetcher, [tab])

  const runs = data?.items ?? []
  const total = data?.total ?? 0

  const lookupMapping = async () => {
    if (!keyword.trim()) return
    setBusy("map"); setMsg(""); setMappingResult(null)
    try {
      const r = await api.get<MappingLookupResult>(`/mappings/lookup?keyword=${encodeURIComponent(keyword.trim())}`)
      setMappingResult(r)
      setMsg(r.found ? "找到匹配类目" : "未找到匹配类目")
    } catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy("") }
  }

  const searchSeo = async () => {
    setBusy("seo"); setMsg(""); setSeoResult(null)
    try {
      const q = keyword.trim() ? `?q=${encodeURIComponent(keyword.trim())}&limit=20` : "?limit=20"
      const r = await api.get<SeoKeywordsResponse>(`/seo/keywords${q}`)
      setSeoResult(r)
      setMsg(`找到 ${r.total} 个关键词`)
    } catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy("") }
  }

  const exportCsv = () => {
    const rows = (runs ?? []).map((r) => ({
      keyword: r.keyword,
      candidates: Array.isArray(r.candidates) ? r.candidates.length : 0,
      created_at: r.created_at ?? "",
      contributor: r.contributed_by_token_id ?? "",
    }))
    if (!rows.length) return
    const header = Object.keys(rows[0]).join(",")
    const body = rows.map((r) => Object.values(r).map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n")
    const blob = new Blob([`${header}\n${body}`], { type: "text/csv;charset=utf-8" })
    const a = document.createElement("a")
    a.href = URL.createObjectURL(blob)
    a.download = `discovery-runs-${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  const exportCandidates = (r: DiscoveryRun) => {
    const cands = Array.isArray(r.candidates) ? r.candidates : []
    if (!cands.length) return
    const pick = (o: Record<string, unknown>, keys: string[]) => {
      for (const k of keys) if (o[k] != null) return o[k]
      return ""
    }
    const rows = cands.map((c) => {
      const o = (c ?? {}) as Record<string, unknown>
      return {
        title: String(pick(o, ["title", "name", "product_name"]) ?? ""),
        price_rub: String(pick(o, ["price_rub", "price", "avg_price_rub"]) ?? ""),
        source: String(pick(o, ["purchase_url", "source_url", "url"]) ?? ""),
        score: String(pick(o, ["match_score", "score", "confidence"]) ?? ""),
      }
    })
    const header = Object.keys(rows[0]).join(",")
    const body = rows.map((r) => Object.values(r).map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n")
    const blob = new Blob([`${header}\n${body}`], { type: "text/csv;charset=utf-8" })
    const a = document.createElement("a")
    a.href = URL.createObjectURL(blob)
    a.download = `discovery-${r.keyword}-${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  const candidateRows = (r: DiscoveryRun) => {
    const cands = Array.isArray(r.candidates) ? r.candidates : []
    return cands.slice(0, 50).map((c, i) => {
      const o = (c ?? {}) as Record<string, unknown>
      const title = String(o.title ?? o.name ?? o.product_name ?? "—")
      const price = String(o.price_rub ?? o.price ?? o.avg_price_rub ?? "—")
      const src = String(o.purchase_url ?? o.source_url ?? o.url ?? "")
      const score = String(o.match_score ?? o.score ?? "")
      return (
        <div key={i} style={{ display: "flex", gap: 8, fontSize: 12, padding: "4px 0", borderBottom: "1px solid rgba(0,0,0,0.06)" }}>
          <span style={{ width: 40 }}>{i + 1}</span>
          <b style={{ flex: 1 }}>{title}</b>
          <span>{price}</span>
          {score && <span style={{ opacity: 0.7 }}>{score}</span>}
          {src && <a href={src} target="_blank" rel="noreferrer" style={{ fontSize: 11 }}>货源 ↗</a>}
        </div>
      )
    })
  }

  return (
    <>
      <PageHeader kicker="PRODUCT DISCOVERY" title="选品归档" description={`共 ${total} 条选品记录`} />
      <div className="filter-bar">
        <button className="button ghost" onClick={exportCsv}>导出 CSV</button>
      </div>
      <section className="filter-bar">
        {(["runs", "mappings", "seo"] as const).map((t) => (
          <button key={t} className={tab === t ? "button primary" : "button ghost"} onClick={() => setTab(t)}>
            {t === "runs" ? "选品记录" : t === "mappings" ? "类目映射" : "SEO 关键词"}
          </button>
        ))}
      </section>

      {tab === "runs" && (
        <>
          {loading && <PanelLoading />}
          {error && <PanelError message={error} onRetry={reload} />}
          {!loading && !error && runs.length === 0 && <PanelEmpty text="暂无选品记录" />}
          {!loading && !error && runs.length > 0 && (
            <section className="wide-section">
              <article className="panel order-table">
                <div><span>关键词</span><span>候选数</span><span>归档时间</span><span>贡献者</span></div>
                {runs.map((r) => (
                  <div key={r.id}>
                    <b>{r.keyword}</b>
                    <span>{Array.isArray(r.candidates) ? r.candidates.length : 0}</span>
                    <time>{formatDateTime(r.created_at)}</time>
                    <span>{r.contributed_by_token_id ? r.contributed_by_token_id.slice(0, 8) + "…" : "—"}</span>
                    <span className="row-links">
                      <button onClick={() => setDetail(r)}>查看候选</button>
                      <button onClick={() => exportCandidates(r)}>导出</button>
                    </span>
                  </div>
                ))}
              </article>
            </section>
          )}
        </>
      )}

      {tab === "mappings" && (
        <section className="wide-section">
          <article className="panel">
            <span className="panel-kicker">CATEGORY MAPPING LOOKUP</span>
            <h2>类目映射查询</h2>
            <div className="drawer-form">
              <label>关键词 (中文类目名)<input value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="如 宠物用品" /></label>
              <button className="button primary" onClick={lookupMapping} disabled={busy === "map"}>{busy === "map" ? "查询中…" : "查询"}</button>
            </div>
            {msg && <div className="inline-notice">{msg}</div>}
            {mappingResult && mappingResult.mappings.length > 0 && (
              <div className="admin-table">
                <div><span>类目 ID</span><span>类型 ID</span><span>置信度</span></div>
                {mappingResult.mappings.map((m, i) => (
                  <div key={i}><b>{m.dc}</b><span>{m.tp}</span><span>{(m.confidence * 100).toFixed(0)}%</span></div>
                ))}
              </div>
            )}
          </article>
        </section>
      )}

      {tab === "seo" && (
        <section className="wide-section">
          <article className="panel">
            <span className="panel-kicker">SEO KEYWORDS</span>
            <h2>SEO 流量关键词</h2>
            <div className="drawer-form">
              <label>搜索<input value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="留空查看热门关键词" /></label>
              <button className="button primary" onClick={searchSeo} disabled={busy === "seo"}>{busy === "seo" ? "搜索中…" : "搜索"}</button>
            </div>
            {msg && <div className="inline-notice">{msg}</div>}
            {seoResult && seoResult.keywords.length > 0 && (
              <div className="admin-table">
                <div><span>关键词</span><span>数据</span></div>
                {seoResult.keywords.map((k, i) => (
                  <div key={i}>
                    <b>{k.query || k.keyword || "—"}</b>
                    <span>{k.count ?? k.uniq_queries_wca ?? "—"}</span>
                  </div>
                ))}
              </div>
            )}
          </article>
        </section>
      )}
      {detail && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setDetail(null)}>
          <section className="product-drawer" role="dialog" aria-modal="true" aria-label="选品候选" onMouseDown={e => e.stopPropagation()}>
            <header>
              <div><span className="panel-kicker">DISCOVERY CANDIDATES</span><h2>{detail.keyword}</h2></div>
              <button onClick={() => setDetail(null)} aria-label="关闭">×</button>
            </header>
            <div className="drawer-form">
              <p style={{ fontSize: 12, opacity: 0.7 }}>共 {Array.isArray(detail.candidates) ? detail.candidates.length : 0} 个候选 · 归档 {formatDateTime(detail.created_at)}</p>
              {candidateRows(detail)}
            </div>
            <footer className="editor-footer">
              <button className="button ghost" onClick={() => setDetail(null)}>关闭</button>
              <button className="button primary" onClick={() => exportCandidates(detail)}>导出 CSV</button>
            </footer>
          </section>
        </div>
      )}
    </>
  )
}
