import { useCallback, useEffect, useState } from "react"
import { api } from "../api/client"
import type { AdminOverview, AdminUser, AdminStore, AdminUserDetail } from "../api/hooks"
import { apiErrorMessage, formatDateTime, useApi } from "../api/hooks"
import { PageHeader, PanelEmpty, PanelError, PanelLoading } from "./ui"

function UserDetail({
  user,
  onClose,
}: {
  user: AdminUser
  onClose: () => void
}) {
  const [detail, setDetail] = useState<AdminUserDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  useEffect(() => {
    let live = true
    api.get<AdminUserDetail>(`/admin/users/${user.id}`)
      .then((d) => { if (live) setDetail(d) })
      .catch((e) => { if (live) setError(apiErrorMessage(e)) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [user.id])

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="product-drawer" role="dialog" aria-modal="true" onMouseDown={(e) => e.stopPropagation()}>
        <header>
          <div>
            <span className="panel-kicker">USER DETAIL</span>
            <h2>{user.username}</h2>
          </div>
          <button onClick={onClose} aria-label="关闭">×</button>
        </header>
        {loading && <PanelLoading />}
        {error && <PanelError message={error} />}
        {!loading && !error && detail && (
          <div className="drawer-form">
            <div className="publish-row"><span>用户 ID</span><b>{detail.id}</b></div>
            <div className="publish-row"><span>店铺数</span><b>{detail.stores.length}</b></div>
            <div className="publish-row"><span>任务总数</span><b>{detail.task_total}</b></div>
            <div className="publish-row"><span>成功任务</span><b>{detail.task_completed}</b></div>
            <div className="publish-row"><span>失败任务</span><b>{detail.task_failed}</b></div>
            {detail.stores.length > 0 && (
              <>
                <span className="panel-kicker" style={{ marginTop: 16 }}>STORES</span>
                <div className="admin-table">
                  <div><span>店铺名</span><span>Client ID</span><span>状态</span></div>
                  {detail.stores.map((s) => (
                    <div key={s.id}>
                      <b>{s.shop_name || "—"}</b>
                      <span>{s.ozon_client_id}</span>
                      <span className={`status ${s.status === "active" ? "" : "line"}`}>{s.status}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        )}
        <footer className="editor-footer">
          <button className="button ghost" onClick={onClose}>关闭</button>
        </footer>
      </section>
    </div>
  )
}

export default function AdminPanel() {
  const [tab, setTab] = useState<"overview" | "users" | "stores" | "config">("overview")
  const [detailUser, setDetailUser] = useState<AdminUser | null>(null)
  const [cfgList, setCfgList] = useState<string[]>([])
  const [cfgSelected, setCfgSelected] = useState<string | null>(null)
  const [cfgContent, setCfgContent] = useState("")
  const [cfgBackups, setCfgBackups] = useState<Array<{ name: string; size: number; mtime: number }>>([])
  const [cfgNotice, setCfgNotice] = useState("")
  const [cfgSaving, setCfgSaving] = useState(false)

  const fetchOverview = useCallback(() => api.get<AdminOverview>("/admin/overview"), [])
  const fetchUsers = useCallback(() => api.get<AdminUser[]>("/admin/users"), [])
  const fetchStores = useCallback(() => api.get<AdminStore[]>("/admin/stores"), [])

  const overview = useApi(fetchOverview, [tab === "overview"])
  const users = useApi(fetchUsers, [tab === "users"])
  const stores = useApi(fetchStores, [tab === "stores"])
  const loadConfig = useCallback(async () => {
    setCfgNotice("正在读取配置列表…")
    try {
      const list = await api.get<Array<{ name: string }>>("/admin/config")
      const names = list.map((x) => x.name)
      setCfgList(names)
      setCfgNotice(`已加载 ${names.length} 个配置项`)
      if (names.length && !cfgSelected) setCfgSelected(names[0])
    } catch (e) { setCfgNotice(apiErrorMessage(e)) }
  }, [cfgSelected])

  useEffect(() => {
    if (tab === "config") loadConfig()
  }, [tab, loadConfig])

  const loadCfgContent = useCallback(async (name: string) => {
    setCfgNotice("")
    try {
      const [content, backups] = await Promise.all([
        api.get<unknown>(`/admin/config/${name}`),
        api.get<Array<{ name: string; size: number; mtime: number }>>(`/admin/config/${name}/backups`),
      ])
      setCfgContent(JSON.stringify(content, null, 2))
      setCfgBackups(backups ?? [])
    } catch (e) { setCfgNotice(apiErrorMessage(e)) }
  }, [])

  useEffect(() => {
    if (tab === "config" && cfgSelected) loadCfgContent(cfgSelected)
  }, [tab, cfgSelected, loadCfgContent])

  const saveCfg = async () => {
    if (!cfgSelected) return
    setCfgSaving(true)
    try {
      await api.put(`/admin/config/${cfgSelected}`, JSON.parse(cfgContent))
      setCfgNotice("✓ 已保存并备份")
    } catch (e) { setCfgNotice(`保存失败: ${apiErrorMessage(e)}`) }
    finally { setCfgSaving(false) }
  }

  const rollbackCfg = async (name: string) => {
    if (!cfgSelected) return
    try {
      const res = await api.post<{ content?: unknown }>(`/admin/config/${cfgSelected}/rollback`, { backup: name })
      setCfgContent(JSON.stringify(res.content ?? {}, null, 2))
      setCfgNotice("✓ 已回滚")
    } catch (e) { setCfgNotice(`回滚失败: ${apiErrorMessage(e)}`) }
  }

  const active = tab === "overview" ? overview : tab === "users" ? users : stores

  return (
    <>
      <PageHeader kicker="ADMIN DASHBOARD" title="管理后台" description="平台级数据概览与管理。" />
      <section className="filter-bar">
        {(["overview", "users", "stores"] as const).map((t) => (
          <button key={t} className={tab === t ? "button primary" : "button ghost"} onClick={() => setTab(t)}>
            {t === "overview" ? "概览" : t === "users" ? "用户" : "店铺"}
          </button>
        ))}
        <button className={tab === "config" ? "button primary" : "button ghost"} onClick={() => setTab("config")}>生图配置</button>
      </section>

      {active.loading && <PanelLoading />}
      {active.error && <PanelError message={active.error} onRetry={active.reload} />}
      {!active.loading && !active.error && !active.data && <PanelEmpty text="暂无数据" />}

      {tab === "overview" && overview.data && (
        <section className="wide-section">
          <article className="panel">
            <span className="panel-kicker">PLATFORM OVERVIEW</span>
            <h2>平台概览</h2>
            <div className="drawer-form">
              <div className="publish-row"><span>用户总数</span><b>{(overview.data as AdminOverview).user_count}</b></div>
              <div className="publish-row"><span>店铺总数</span><b>{(overview.data as AdminOverview).store_count}</b></div>
              <div className="publish-row"><span>任务总数</span><b>{(overview.data as AdminOverview).task_total}</b></div>
              <div className="publish-row"><span>今日任务</span><b>{(overview.data as AdminOverview).task_today}</b></div>
              <div className="publish-row"><span>成功率</span><b>{((overview.data as AdminOverview).success_rate * 100).toFixed(1)}%</b></div>
            </div>
          </article>
        </section>
      )}

      {tab === "users" && users.data && (
        <section className="wide-section">
          <article className="panel order-table">
            <div><span>用户名</span><span>角色</span><span>配额</span><span>店铺</span><span>任务</span><span>操作</span></div>
            {(users.data as AdminUser[]).map((u) => (
              <div key={u.id}>
                <b>{u.username}</b>
                <span>{u.role}</span>
                <span>{u.quota != null ? u.quota : "∞"}</span>
                <span>{u.store_count}</span>
                <span>{u.task_count}</span>
                <span className="row-links">
                  <button onClick={() => setDetailUser(u)}>详情</button>
                </span>
              </div>
            ))}
          </article>
        </section>
      )}

      {tab === "config" && (
        <section className="wide-section">
          <article className="panel">
            <span className="panel-kicker">ENGINE CONFIG</span>
            <h2>生图/提示词配置</h2>
            {cfgNotice && <div className={`inline-notice ${cfgNotice.startsWith("✓") || cfgNotice.startsWith("已加载") ? "" : "error"}`}>{cfgNotice}</div>}
            <div style={{ display: "flex", gap: 12 }}>
              <aside style={{ minWidth: 180 }}>
                {cfgList.map((name) => (
                  <button key={name} className={cfgSelected === name ? "selected" : ""} onClick={() => setCfgSelected(name)} style={{ display: "block", width: "100%", marginBottom: 4 }}>
                    {name}
                  </button>
                ))}
                {cfgList.length === 0 && <PanelEmpty text="加载中…" />}
              </aside>
              <div style={{ flex: 1 }}>
                {cfgSelected ? (
                  <>
                    <div style={{ display: "flex", gap: 8, marginBottom: 6, alignItems: "center" }}>
                      <b>{cfgSelected}</b>
                      <span style={{ fontSize: 11, opacity: 0.7 }}>{cfgBackups.length} 个历史备份</span>
                      <button className="button ghost" onClick={() => { const b = cfgBackups[0]; if (b) rollbackCfg(b.name) }}>回滚最新备份</button>
                      <button className="button primary" disabled={cfgSaving} onClick={saveCfg}>{cfgSaving ? "保存中…" : "保存配置"}</button>
                    </div>
                    <textarea
                      value={cfgContent}
                      onChange={(e) => setCfgContent(e.target.value)}
                      spellCheck={false}
                      style={{ width: "100%", minHeight: 320, fontFamily: "var(--font-mono, monospace)", fontSize: 11 }}
                    />
                    <p style={{ fontSize: 11, opacity: 0.7 }}>⚠ 此处修改直接影响引擎提示词/变量;保存前请确保 JSON 合法。</p>
                  </>
                ) : <PanelEmpty text="← 从左侧选择配置文件" />}
              </div>
            </div>
          </article>
        </section>
      )}

      {tab === "stores" && stores.data && (
        <section className="wide-section">
          <article className="panel order-table">
            <div><span>店铺名</span><span>Client ID</span><span>租户</span><span>状态</span><span>验证时间</span></div>
            {(stores.data as AdminStore[]).map((s) => (
              <div key={s.id}>
                <b>{s.shop_name || "—"}</b>
                <span>{s.ozon_client_id}</span>
                <span>{s.tenant_id.slice(0, 8)}…</span>
                <span className={`status ${s.status === "active" ? "" : "line"}`}>{s.status}</span>
                <time>{formatDateTime(s.last_validated_at)}</time>
              </div>
            ))}
          </article>
        </section>
      )}

      {detailUser && <UserDetail user={detailUser} onClose={() => setDetailUser(null)} />}
    </>
  )
}
