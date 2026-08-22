import { useCallback, useState } from "react"
import { api } from "../api/client"
import type { SiteBanner, SiteAnnouncement } from "../api/hooks"
import { apiErrorMessage, formatDateTime, useApi } from "../api/hooks"
import { PageHeader, PanelEmpty, PanelError, PanelLoading } from "./ui"

export default function SitePanel() {
  const [tab, setTab] = useState<"banners" | "announcements">("banners")
  const [msg, setMsg] = useState("")

  const fetchBanners = useCallback(() => api.get<SiteBanner[]>("/site/banners"), [])
  const fetchAnnouncements = useCallback(() => api.get<SiteAnnouncement[]>("/site/announcements"), [])

  const banners = useApi(fetchBanners, [tab === "banners"])
  const announcements = useApi(fetchAnnouncements, [tab === "announcements"])

  const active = tab === "banners" ? banners : announcements
  const bannerList = (banners.data ?? []) as SiteBanner[]
  const announcementList = (announcements.data ?? []) as SiteAnnouncement[]

  return (
    <>
      <PageHeader kicker="SITE MANAGEMENT" title="站点管理" description="管理首页横幅与公告通知。" />
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
            <div><span>标题</span><span>链接</span><span>排序</span><span>状态</span><span>创建时间</span></div>
            {bannerList.map((b) => (
              <div key={b.id}>
                <b>{b.title}</b>
                <span>{b.link_url || "—"}</span>
                <span>{b.sort_order}</span>
                <span className={`status ${b.is_active ? "" : "line"}`}>{b.is_active ? "启用" : "停用"}</span>
                <time>{formatDateTime(b.created_at)}</time>
              </div>
            ))}
          </article>
        </section>
      )}

      {!active.loading && !active.error && tab === "announcements" && announcementList.length > 0 && (
        <section className="wide-section">
          <article className="panel order-table">
            <div><span>标题</span><span>优先级</span><span>状态</span><span>创建时间</span><span>过期时间</span></div>
            {announcementList.map((a) => (
              <div key={a.id}>
                <b>{a.title}</b>
                <span className={`status ${a.priority === "high" ? "red" : "line"}`}>{a.priority}</span>
                <span className={`status ${a.is_active ? "" : "line"}`}>{a.is_active ? "启用" : "停用"}</span>
                <time>{formatDateTime(a.created_at)}</time>
                <time>{formatDateTime(a.expires_at)}</time>
              </div>
            ))}
          </article>
        </section>
      )}
    </>
  )
}
