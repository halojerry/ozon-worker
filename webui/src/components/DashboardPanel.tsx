import { useNavigate } from "react-router"
import { api } from "../api/client"
import type { DashboardOverview } from "../api/hooks"
import { formatDateTime, formatPrice, useApi } from "../api/hooks"
import { Metric, PageHeader, PanelEmpty, PanelError, PanelLoading } from "./ui"

export default function DashboardPanel() {
  const navigate = useNavigate()
  const { data, loading, error, reload } = useApi<DashboardOverview>(
    () => api.get("/dashboard/overview?days=14"),
    [],
  )
  const trend = data?.trend ?? []
  const maxGmv = Math.max(1, ...trend.map((t) => t.sales_amount ?? 0))
  const today = data?.today

  return (
    <>
      <PageHeader kicker="OVERVIEW" title="工作台" description="全店经营概览与待办(实时聚合自店铺同步缓存)" action="刷新" onAction={reload} />

      {loading ? <PanelLoading /> : error ? <PanelError message={error} onRetry={reload} /> : (
        <>
          <section className="metric-grid">
            <Metric label="今日销售额" value={today?.sales_amount != null ? `₽ ${today.sales_amount.toLocaleString()}` : "—"} note={`${data?.store_count ?? 0} 个店铺`} red />
            <Metric label="今日订单" value={String(today?.orders_count ?? 0)} note="全店累计" />
            <Metric label="在售商品" value={String(data?.active_products ?? 0)} note="未归档且无错误" />
            <Metric label="待处理任务" value={String(data?.pending_tasks ?? 0)} note="排队/执行/审核中" />
          </section>

          <section className="dashboard-grid">
            <article className="panel chart-panel dashboard-chart">
              <div className="panel-head"><h2>销售趋势 <small>近 {data?.trend_days ?? 14} 天</small></h2></div>
              {trend.length === 0 ? <PanelEmpty text="暂无日聚合数据(店铺同步 1-2 次后出现)" /> : (
                <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "12px 0" }}>
                  {trend.slice(-14).map((t) => (
                    <div key={t.date} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
                      <span style={{ width: 84, color: "#888" }}>{t.date}</span>
                      <div style={{ flex: 1, display: "flex", gap: 4, alignItems: "center" }}>
                        <div style={{ width: `${((t.sales_amount ?? 0) / maxGmv) * 100}%`, height: 12, background: "var(--accent, #e20e0e)", borderRadius: 3, opacity: 0.8 }} />
                        <span style={{ minWidth: 70, textAlign: "right" }}>₽ {(t.sales_amount ?? 0).toLocaleString()}</span>
                      </div>
                      <span style={{ minWidth: 44, textAlign: "right", color: "#666" }}>{t.orders} 单</span>
                    </div>
                  ))}
                </div>
              )}
            </article>

            <article className="panel rank-panel">
              <div className="panel-head">
                <div><span className="panel-kicker">TOP SELLING</span><h2>热销商品</h2></div>
                <button className="text-button" onClick={() => navigate("/products")}>查看全部 →</button>
              </div>
              {(data?.hot_products ?? []).length === 0 ? <PanelEmpty text="近 30 天暂无订单" /> : (
                (data?.hot_products ?? []).map((item, i) => (
                  <div className="rank" key={item.product_id || i}>
                    <b className={i === 0 ? "first" : ""}>0{i + 1}</b>
                    <div className={`product-thumb thumb-${i % 3}`} />
                    <div><strong>{item.name}</strong><span>{item.quantity} 件 · 近30天</span></div>
                    <em>{item.product_id?.slice(0, 8) || "—"}</em>
                  </div>
                ))
              )}
            </article>
          </section>

          <section className="wide-section">
            <article className="panel orders-panel">
              <div className="panel-head">
                <div><span className="panel-kicker">LATEST RECORDS</span><h2>最新订单</h2></div>
                <button className="text-button" onClick={() => navigate("/orders")}>全部订单 →</button>
              </div>
              {(data?.latest_orders ?? []).length === 0 ? <PanelEmpty text="暂无订单记录" /> : (
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>货件编号</th><th>商品</th><th>金额</th><th>状态</th><th>时间</th></tr></thead>
                    <tbody>
                      {(data?.latest_orders ?? []).map((o) => (
                        <tr key={o.posting_number}>
                          <td className="order-no">{o.posting_number}</td>
                          <td><b>{o.product_name}</b></td>
                          <td className="price">{o.total_amount != null ? formatPrice(o.total_amount, "₽") : "—"}</td>
                          <td><span className="status">{o.status}</span></td>
                          <td>{o.created_at ? formatDateTime(o.created_at) : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </article>
          </section>
        </>
      )}
    </>
  )
}
