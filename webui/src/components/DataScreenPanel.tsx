import { useNavigate } from "react-router"
import { useState } from "react"
import { api, getSession } from "../api/client"
import { useApi, usePolling, MarketOverview, SalesTrendResponse, HotQueriesResponse } from "../api/hooks"
import { Metric, PanelLoading, PanelError, PanelEmpty } from "./ui"

function Sparkline({ compact = false }: { compact?: boolean }) {
  return (
    <svg viewBox="0 0 496 106" aria-hidden="true" className={compact ? "sparkline compact" : "sparkline"}>
      <defs>
        <linearGradient id="fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor="#e20e0e" stopOpacity=".18" />
          <stop offset="1" stopColor="#e20e0e" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d="M0 86 C30 78,42 80,66 67 S103 66,124 74 S162 50,188 57 S220 48,244 59 S279 36,304 41 S339 27,362 47 S395 40,418 25 S459 34,496 8 L496 106 L0 106Z" fill="url(#fill)" />
      <path d="M0 86 C30 78,42 80,66 67 S103 66,124 74 S162 50,188 57 S220 48,244 59 S279 36,304 41 S339 27,362 47 S395 40,418 25 S459 34,496 8" fill="none" stroke="#e20e0e" strokeWidth="2.5" />
    </svg>
  )
}

export default function DataScreenPanel() {
  const navigate = useNavigate()
  const [full, setFull] = useState(false)
  const [trendDays, setTrendDays] = useState(7)
  const isAdmin = getSession()?.role === "admin"

  const overview = useApi(() => api.get<MarketOverview>("/analytics/market-overview"), [])
  const trend = useApi(() => api.get<SalesTrendResponse>(`/analytics/sales-trend?days=${trendDays}`), [trendDays])
  // 热词榜/蓝海仅管理员：非管理员不请求（避免 403 console error），也不渲染
  const hotQueries = useApi<HotQueriesResponse>(() => isAdmin ? api.get<HotQueriesResponse>("/analytics/hot-queries?limit=20") : Promise.resolve({ items: [], scope: "tenant" }), [isAdmin])

  const toggleFull = async () => {
    if (!document.fullscreenElement) {
      await document.documentElement.requestFullscreen()
      setFull(true)
    } else {
      await document.exitFullscreen()
      setFull(false)
    }
  }

  const maxGmv = Math.max(1, ...((trend.data?.items ?? []).map(t => t.gmv)))
  const maxOrders = Math.max(1, ...((trend.data?.items ?? []).map(t => t.orders)))

  return (
    <div className="command-screen">
      <header>
        <button className="command-back" onClick={() => navigate("/")}>← 返回工作台</button>
        <b>▥ 数据大屏</b>
        <span>
          {new Date().toLocaleString("zh-CN")}　 ♧　
          <button className="command-full" onClick={toggleFull}>
            {full ? "⤡ 退出全屏" : "⛶ 全屏"}
          </button>　● Admin⌄
        </span>
      </header>
      {overview.data && (
        <div style={{ padding: "8px 20px 0", fontSize: 12, opacity: 0.75 }}>
          <span className={`status ${overview.data.scope === "global" ? "red" : "green"}`}>
            {overview.data.scope === "global" ? "全平台数据（管理员）" : "我的店铺数据"}
          </span>
        </div>
      )}

      <main>
        <section className="command-metrics">
          {overview.loading ? <PanelLoading /> : overview.error ? <PanelError message={overview.error} onRetry={overview.reload} /> : (
            <>
              <Metric label="AI上品个数" value={String(overview.data?.total_products ?? 0)} note={`${overview.data?.total_discovery_runs ?? 0} 次选品`} red />
              <Metric label="今日订单" value={String(overview.data?.total_orders ?? 0)} note="累计" red />
              <Metric label="总 GMV" value={`₽ ${(overview.data?.total_gmv ?? 0).toLocaleString()}`} note="累计" red />
              <Metric label="热销品数" value={String(overview.data?.bestseller_count ?? 0)} note="市场数据" red />
            </>
          )}
          <article className="panel">
            <h2>订单趋势（近{trendDays}天）</h2>
            <Sparkline compact />
          </article>
        </section>

        <section className="command-layout">
          <article className="panel live-feed">
            <h2>销售趋势</h2>
            {trend.loading ? <PanelLoading /> : trend.error ? <PanelError message={trend.error} onRetry={trend.reload} /> : (
              <>
                <div className="filter-bar compact" style={{ marginBottom: 12 }}>
                  {[7, 14, 30].map(d => (
                    <button key={d} className={trendDays === d ? "button primary" : ""} onClick={() => setTrendDays(d)}>
                      {d}天
                    </button>
                  ))}
                </div>
                {(trend.data?.items ?? []).length === 0 ? <PanelEmpty text="暂无销售趋势数据" /> : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {(trend.data?.items ?? []).map(item => (
                      <div key={item.date} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
                        <span style={{ width: 90, color: "#888" }}>{item.date}</span>
                        <div style={{ flex: 1, display: "flex", gap: 4, alignItems: "center" }}>
                          <div style={{ width: `${(item.gmv / maxGmv) * 100}%`, height: 14, background: "var(--accent, #e20e0e)", borderRadius: 3, opacity: 0.8 }} />
                          <span style={{ minWidth: 80, textAlign: "right" }}>₽ {item.gmv.toLocaleString()}</span>
                        </div>
                        <span style={{ minWidth: 50, textAlign: "right", color: "#666" }}>{item.orders} 单</span>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </article>

          <article className="panel world-map">
            <h2>全球订单销售热力图</h2>
            <div className="map-grid">
              <span className="map-label label-ru">俄罗斯</span>
              <span className="map-label label-moscow">莫斯科</span>
              <span className="map-label label-far-east">远东地区</span>
              <i className="heat h1" data-city="莫斯科" />
              <i className="heat h2" data-city="圣彼得堡" />
              <i className="heat h3" data-city="叶卡捷琳堡" />
              <i className="heat h4" data-city="新西伯利亚" />
              <i className="heat h5" data-city="符拉迪沃斯托克" />
              <i className="heat h6" data-city="喀山" />
              <i className="heat h7" data-city="克拉斯诺亚尔斯克" />
            </div>
          </article>

          {isAdmin && (
            <article className="panel command-rank">
              <h2>热词榜 Top 20</h2>
              {hotQueries.loading ? <PanelLoading /> : hotQueries.error ? <PanelEmpty text="蓝海热词仅管理员可见" /> : (
                (hotQueries.data?.items ?? []).length === 0 ? <PanelEmpty text="暂无热词数据" /> :
                (hotQueries.data?.items ?? []).map((q, i) => (
                  <p key={`${q.query}-${i}`}>
                    <b>{i + 1}</b>
                    {q.query}
                    <span>{q.uniq_queries_wca ?? q.count ?? 0}</span>
                  </p>
                ))
              )}
            </article>
          )}
        </section>
      </main>
    </div>
  )
}
