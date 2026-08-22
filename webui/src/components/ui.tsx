export function PageHeader({
  kicker,
  title,
  description,
  action = "＋ 新建任务",
  onAction,
}: { kicker: string; title: string; description: string; action?: string; onAction?: () => void }) {
  return (
    <div className="page-head">
      <div>
        <div className="eyebrow"><span/> {kicker}</div>
        <h1>{title}<em>·</em></h1>
        <p>{description}</p>
      </div>
      <div className="head-actions">
        <button className="button ghost">导出数据</button>
        <button className="button primary" onClick={onAction}>{action}</button>
      </div>
    </div>
  )
}

export function Metric({ label, value, note, red = false }: { label: string; value: string; note: string; red?: boolean }) {
  return (
    <article className={`metric-card ${red ? "accent-card" : ""}`}>
      <div className="metric-top"><span>{label}</span><b className="metric-symbol">{red ? "✦" : "◌"}</b></div>
      <strong>{value}</strong>
      <div className="metric-foot"><b>{note}</b><span>较上周期</span><div className="mini-bars"><i/><i/><i/><i/><i/><i/><i/></div></div>
    </article>
  )
}

export function PanelLoading({ text = "加载中…" }: { text?: string }) {
  return <div className="empty-state">◌ {text}</div>
}

export function PanelError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="empty-state">
      <span style={{ color: "#b30c0c" }}>⚠ {message}</span>
      {onRetry && <span style={{ display: "block", marginTop: 10 }}><button className="button ghost" onClick={onRetry}>重试</button></span>}
    </div>
  )
}

export function PanelEmpty({ text = "暂无数据" }: { text?: string }) {
  return <div className="empty-state">{text}</div>
}
