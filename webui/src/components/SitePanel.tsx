import { useCallback, useState } from "react"
import { api } from "../api/client"
import type { SiteBanner, SiteAnnouncement } from "../api/hooks"
import { apiErrorMessage, formatDateTime, useApi } from "../api/hooks"
import { PageHeader, PanelEmpty, PanelError, PanelLoading } from "./ui"

export default function SitePanel() {
  const [tab, setTab] = useState<"banners" | "announcements">("banners")
  const [msg, setMsg] = useState("")
  const [busy, setBusy] = useState(false)
  const [bannerUrl, setBannerUrl] = useState("")
  const [bannerLink, setBannerLink] = useState("")
  const [annTitle, setAnnTitle] = useState("")
  const [annContent, setAnnContent] = useState("")

  const fetchBanners = useCallback(() => api.get<SiteBanner[]>("/site/banners"), [])
  const fetchAnnouncements = useCallback(() => api.get<SiteAnnouncement[]>("/site/announcements"), [])

  const banners = useApi(fetchBanners, [tab === "banners"])
  const announcements = useApi(fetchAnnouncements, [tab === "announcements"])
  const reload = tab === "banners" ? banners.reload : announcements.reload

  const active = tab === "banners" ? banners : announcements
  const bannerList = (banners.data ?? []) as SiteBanner[]
  const announcementList = (announcements.data ?? []) as SiteAnnouncement[]

  return (
    <>
      <PageHeader kicker="SITE MANAGEMENT" title="站点管理" description="管理首页横幅与公告通知。" />
      {msg && <div className={`inline-notice ${msg.startsWith("✓") ? "" : "error"}`}>{msg}</div>}
      <section className="filter-bar">
        {(["banners", "announcements"] as const).map((t) => (
          <button key={t} className={tab === t ? "button primary" : "button ghost"} onClick={() => setTab(t)}>
            {t === "banners" ? "首页横幅" : "公告通知"}
          </button>
        ))}
      </section>

      {active.loading && <PanelLoading />}
      {active.error && <PanelError message={active.error} onRetry={active.reload} />}
      {!active.loading && !active.error && tab === "banners" && bannerList.length === 0 && <PanelEmpty text="暂无横幅" />}
      {!active.loading && !active.error && tab === "announcements" && announcementList.length === 0 && <PanelEmpty text="暂无公告" />}

      {!active.loading && !active.error && tab === "banners" && bannerList.length > 0 && (
        <section className="wide-section">
          <article className="panel order-table">
            <div><span>标题</span><span>链接</span><span>排序</span><span>状态</span><span>创建时间</span><span>操作</span></div>
            {bannerList.map((b) => (
              <div key={b.id}>
                <b>{b.title}</b>
                <span>{b.link_url || "—"}</span>
                <span>{b.sort_order}</span>
                <span className={`status ${b.enabled ? "" : "line"}`}>{b.enabled ? "启用" : "停用"}</span>
                <time>{formatDateTime(b.created_at)}</time>
                <span className="row-links">
                  <button disabled={busy} onClick={async () => {
                    setBusy(true)
                    try { await api.delete(`/admin/site/banners/${b.id}`); setMsg("✓ 已删除"); reload() }
                    catch (e) { setMsg(apiErrorMessage(e)) }
                    finally { setBusy(false) }
                  }}>删除</button>
                </span>
              </div>
            ))}
          </article>
        </section>
      )}
      {!active.loading && !active.error && tab === "banners" && (
        <section className="wide-section">
          <article className="panel">
            <span className="panel-kicker">CREATE BANNER</span>
            <div className="drawer-form" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <input style={{ flex: 1, minWidth: 180 }} placeholder="图片 URL(必填)" value={bannerUrl} onChange={(e) => setBannerUrl(e.target.value)} />
              <input style={{ flex: 1, minWidth: 180 }} placeholder="跳转链接(可选)" value={bannerLink} onChange={(e) => setBannerLink(e.target.value)} />
              <button className="button primary" disabled={busy || !bannerUrl.trim()} onClick={async () => {
                setBusy(true)
                try {
                  await api.post("/admin/site/banners", { image_url: bannerUrl.trim(), link_url: bannerLink.trim() || null, title: "", sort_order: 0, enabled: true })
                  setBannerUrl(""); setBannerLink(""); setMsg("✓ 已创建"); reload()
                } catch (e) { setMsg(apiErrorMessage(e)) }
                finally { setBusy(false) }
              }}>创建横幅</button>
            </div>
          </article>
        </section>
      )}

      {!active.loading && !active.error && tab === "announcements" && announcementList.length > 0 && (
        <section className="wide-section">
          <article className="panel order-table">
            <div><span>标题</span><span>优先级</span><span>状态</span><span>创建时间</span><span>过期时间</span><span>操作</span></div>
            {announcementList.map((a) => (
              <div key={a.id}>
                <b>{a.title}</b>
                <span className="status">{a.announcement_type || "notice"}</span>
                <span className={`status ${a.enabled ? "" : "line"}`}>{a.enabled ? "启用" : "停用"}</span>
                <time>{formatDateTime(a.created_at)}</time>
                <span className="row-links">
                  <button disabled={busy} onClick={async () => {
                    setBusy(true)
                    try { await api.delete(`/admin/site/announcements/${a.id}`); setMsg("✓ 已删除"); reload() }
                    catch (e) { setMsg(apiErrorMessage(e)) }
                    finally { setBusy(false) }
                  }}>删除</button>
                </span>
              </div>
            ))}
          </article>
        </section>
      )}
      {!active.loading && !active.error && tab === "announcements" && (
        <section className="wide-section">
          <article className="panel">
            <span className="panel-kicker">CREATE ANNOUNCEMENT</span>
            <div className="drawer-form" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <input style={{ flex: 1, minWidth: 160 }} placeholder="标题" value={annTitle} onChange={(e) => setAnnTitle(e.target.value)} />
              <input style={{ flex: 2, minWidth: 240 }} placeholder="公告内容(必填)" value={annContent} onChange={(e) => setAnnContent(e.target.value)} />
              <button className="button primary" disabled={busy || !annContent.trim()} onClick={async () => {
                setBusy(true)
                try {
                  await api.post("/admin/site/announcements", { title: annTitle.trim(), content: annContent.trim(), announcement_type: "notice", enabled: true })
                  setAnnTitle(""); setAnnContent(""); setMsg("✓ 已创建"); reload()
                } catch (e) { setMsg(apiErrorMessage(e)) }
                finally { setBusy(false) }
              }}>创建公告</button>
            </div>
          </article>
        </section>
      )}
    </>
  )
}
