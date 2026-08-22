import { useCallback, useState } from "react"
import { api } from "../api/client"
import type { DiscoveryRun, DiscoveryRunsResponse, MappingLookupResult, SeoKeywordsResponse } from "../api/hooks"
import { apiErrorMessage, formatDateTime, useApi } from "../api/hooks"
import { PageHeader, PanelEmpty, PanelError, PanelLoading } from "./ui"

export default function DiscoveryPanel() {
  const [tab, setTab] = useState<"runs" | "mappings" | "seo">("runs")
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

  return (
    <>
      <PageHeader kicker="PRODUCT DISCOVERY" title="选品归档" description={`共 ${total} 条选品记录`} />
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
    </>
  )
}
