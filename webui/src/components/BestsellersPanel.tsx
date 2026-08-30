import { useEffect, useState } from "react"
import { ApiError, api } from "../api/client"
import { useApi, MarketOverview } from "../api/hooks"
import { PageHeader, Metric, PanelLoading, PanelError, PanelEmpty } from "./ui"

export default function BestsellersPanel() {
  const [mode, setMode] = useState("热销商品")
  const [category, setCategory] = useState("中国储热销")
  const [editor, setEditor] = useState<string | null>(null)
  const [modeDrawer, setModeDrawer] = useState(false)
  const [apiNotice, setApiNotice] = useState("正在读取榜单数据…")
  const [brand, setBrand] = useState("")
  const [minSales, setMinSales] = useState("")
  const [maxSales, setMaxSales] = useState("")
  const [minPrice, setMinPrice] = useState("")
  const [maxPrice, setMaxPrice] = useState("")
  const [filterVersion, setFilterVersion] = useState(0)
  const fallbackCandidates = [
    ["Bobber Thermal Mug, 0.77 L", "Bobber", "家居生活 · 保温杯", "5,168", "12.3%", "55.9%", "https://images.unsplash.com/photo-1514228742587-6b1558fcca3d?w=160&h=160&fit=crop&auto=format"],
    ["SoundPro X1 Wireless Headphones", "SoundPro", "电子产品 · 耳机", "4,782", "18.6%", "42.8%", "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=160&h=160&fit=crop&auto=format"],
    ["Smart Living Desk Lamp", "Lumen", "家居生活 · 照明", "3,945", "9.8%", "38.4%", "https://images.unsplash.com/photo-1507473885765-e6ed057f782c?w=160&h=160&fit=crop&auto=format"],
  ]
  const [candidates, setCandidates] = useState<string[][]>(fallbackCandidates)
  const categories = [
    ["大盘总览", "全类目市场趋势"],
    ["类目分析", "细分赛道洞察"],
    ["热销产品", "全站热度排行"],
    ["中国储热销", "中国仓现货机会"],
    ["热词精选", "高意图搜索词"],
    ["标签反查", "以品找词与标签"],
    ["WB热销", "Wildberries 热榜"],
  ]

  const overview = useApi(() => api.get<MarketOverview>("/analytics/market-overview"), [])

  useEffect(() => {
    let live = true
    const params = new URLSearchParams({ limit: "50" })
    if (category && category !== "中国储热销") params.set("category", category)
    if (brand.trim()) params.set("brand", brand.trim())
    if (minSales) params.set("min_sales", minSales)
    if (maxSales) params.set("max_sales", maxSales)
    if (minPrice) params.set("min_price", minPrice)
    if (maxPrice) params.set("max_price", maxPrice)
    api.get<unknown>(`/analytics/bestsellers?${params.toString()}`).then(payload => {
      const data = payload as { items?: unknown[]; data?: unknown[] }
      const rows = Array.isArray(payload) ? payload : (data.items || data.data || [])
      const parsed = rows.map((row, index) => {
        const item = row as Record<string, unknown>
        return [
          String(item.title || item.product_name || item.name || `市场商品 ${index + 1}`),
          String(item.brand || "—"),
          String(item.category_name || item.category || "Ozon 市场"),
          String(item.sales_amount || item.revenue || "—"),
          String(item.sales_growth || item.growth || "—"),
          String(item.click_rate || item.ctr || "—"),
          String(item.image_url || item.image || fallbackCandidates[index % fallbackCandidates.length][6]),
        ]
      }).filter(x => x[0])
      if (live && parsed.length) {
        setCandidates(parsed)
        setApiNotice(`已接入榜单 API · ${parsed.length} 条实时记录`)
      } else if (live) setApiNotice("榜单暂无可展示记录，当前显示示例数据")
    }).catch(error => {
      if (live) setApiNotice(
        error instanceof ApiError && error.status === 401
          ? "请登录后读取实时榜单，当前显示示例数据"
          : "榜单 API 暂不可用，当前显示示例数据"
      )
    })
    return () => { live = false }
  }, [category, brand, minSales, maxSales, minPrice, maxPrice, filterVersion])

  const addToCollection = async (item: string[]) => {
    try {
      const res = await api.post<{ id: string }>("/drafts", {
        source: "market",
        envelope: {
          draft: {
            title: item[0],
            item_id: `market_${Date.now()}`,
            images: item[6] ? [item[6]] : [],
          },
          source: {},
          extensions: {},
        },
      })
      setApiNotice(`✓ 已加入采集箱(draft ${res.id.slice(0, 8)})`)
    } catch (e) {
      setApiNotice(e instanceof Error ? `加入采集失败: ${e.message}` : "加入采集失败")
    }
  }

  return (
    <>
      <PageHeader
        kicker="PRODUCT DISCOVERY MARKETPLACE"
        title="选品广场"
        description="从 Ozon 市场趋势中筛选有潜力的商品，并直接进入你的商品编辑与上架流程。"
        action="⇧ 批量上架"
      />

      <section className="metric-grid">
        {overview.loading ? <PanelLoading /> : overview.error ? <PanelError message={overview.error} onRetry={overview.reload} /> : (
          <>
            <Metric label="市场 GMV" value={`₽ ${(overview.data?.total_gmv ?? 0).toLocaleString()}`} note={`${overview.data?.total_orders ?? 0} 笔订单`} red />
            <Metric label="总订单量" value={String(overview.data?.total_orders ?? 0)} note="累计" />
            <Metric label="在售商品" value={String(overview.data?.total_products ?? 0)} note="同步自 Ozon" />
            <Metric label="选品次数" value={String(overview.data?.total_discovery_runs ?? 0)} note={`${overview.data?.bestseller_count ?? 0} 热销品`} />
          </>
        )}
      </section>

      <section className="plaza-layout">
        <aside className="plaza-categories">
          <div className="plaza-category-title"><span>▧</span>选品广场 <b>⌃</b></div>
          {categories.map(([name, note]) => (
            <button key={name} onClick={() => setCategory(name)} className={category === name ? "selected" : ""}>
              <span className="category-orb">◉</span>
              <span>{name}<small>{note}</small></span>
            </button>
          ))}
          <div className="category-source-note">数据来源可在管理员后台配置</div>
        </aside>
        <div className="plaza-content">
          <div className="plaza-context">
            <div>
              <span className="eyebrow"><i /> {category.toUpperCase()}</span>
              <h2>{category}</h2>
              <p>{categories.find(x => x[0] === category)?.[1]} · 已接入 Ozon 市场、搜索与中国仓库存数据</p>
            </div>
            <span className="source-pill"><i /> {apiNotice}</span>
          </div>
          <div className="selection-modes">
            <span>推荐选品模式 ⓘ</span>
            {["热销商品", "热销新品", "轻仓爆品", "蓝海商品"].map(x => (
              <button key={x} className={mode === x ? "selected" : ""} onClick={() => setMode(x)}>{x}</button>
            ))}
            <button className="link-mode" onClick={() => setModeDrawer(true)}>✦ 自定义选品参数</button>
          </div>
          <div className="selection-note">ⓘ　{apiNotice}。已登录时"热销产品"会优先读取 `/analytics/bestsellers`。</div>
          <div className="selection-filter">
            <label>类目<input value={category} onChange={(e) => setCategory(e.target.value)} /></label>
            <label>品牌<input value={brand} onChange={(e) => setBrand(e.target.value)} placeholder="如 品牌A" /></label>
            <label>月销量下限<input type="number" value={minSales} onChange={(e) => setMinSales(e.target.value)} placeholder="最小值" /></label>
            <label>月销量上限<input type="number" value={maxSales} onChange={(e) => setMaxSales(e.target.value)} placeholder="最大值" /></label>
            <label>均价下限 ₽<input type="number" value={minPrice} onChange={(e) => setMinPrice(e.target.value)} placeholder="最小值" /></label>
            <label>均价上限 ₽<input type="number" value={maxPrice} onChange={(e) => setMaxPrice(e.target.value)} placeholder="最大值" /></label>
            <div>
              <button onClick={() => { setBrand(""); setMinSales(""); setMaxSales(""); setMinPrice(""); setMaxPrice(""); setFilterVersion((v) => v + 1) }}>重置</button>
              <button className="button primary" onClick={() => setFilterVersion((v) => v + 1)}>⌕ 查询</button>
            </div>
          </div>
          <article className="panel plaza-table">
            <div className="plaza-head">
              <span>主图</span><span>名称和品牌</span><span>类目</span><span>月销量</span>
              <span>月销售额(₽)</span><span>销售动态</span><span>浏览量</span><span>点击率</span><span>操作</span>
            </div>
            {candidates.map((item, i) => (
              <div className="plaza-row" key={`${item[0]}-${i}`}>
                <img src={item[6]} alt={item[0]} />
                <div><b>{item[0]}</b><small>{item[1]}</small></div>
                <span>{item[2]}</span>
                <span>{item[3]}</span>
                <span>—</span>
                <span>{item[4]}</span>
                <span>—</span>
                <span>{item[5]}</span>
                <span>
                  <button className="text-button" onClick={() => addToCollection(item)}>采集</button>
                  <button className="text-button" onClick={() => setEditor(item[0])}>编辑</button>
                </span>
              </div>
            ))}
            {!candidates.length && <PanelEmpty text="暂无热销数据" />}
          </article>
        </div>
      </section>
    </>
  )
}
