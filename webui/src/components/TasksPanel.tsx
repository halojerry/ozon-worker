import { useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router"
import { api } from "../api/client"
import type { ImageRegenResponse, TaskDraftResponse, TaskImageItem, TaskImagesResponse, TaskListItem, TaskListResponse, TaskProgressEvent, TaskProgressResponse, TaskStatusResponse } from "../api/hooks"
import { getSession } from "../api/client"
import { apiErrorMessage, formatDateTime, taskStatusClass, taskStatusText, useApi, usePolling } from "../api/hooks"
import { Metric, PageHeader, PanelEmpty, PanelError, PanelLoading } from "./ui"

function ImageDrawer({ task, onClose }: { task: TaskListItem; onClose: () => void }) {
  const [images, setImages] = useState<TaskImageItem[] | null>(null)
  const [error, setError] = useState("")
  const [regenSlot, setRegenSlot] = useState("")

  const load = () => {
    setError("")
    api.get<TaskImagesResponse>(`/tasks/${task.id}/images`)
      .then((res) => setImages(res.images))
      .catch((e) => setError(apiErrorMessage(e)))
  }

  useEffect(() => { load() }, [task.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const regen = async (slot: string) => {
    setRegenSlot(slot); setError("")
    try {
      await api.post<ImageRegenResponse>(`/tasks/${task.id}/images/${slot}/regen`, {})
      load()
    } catch (e) { setError(apiErrorMessage(e)) }
    finally { setRegenSlot("") }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="product-drawer listing-editor" role="dialog" aria-modal="true" aria-label="任务图片" onMouseDown={e => e.stopPropagation()}>
        <header>
          <div><span className="panel-kicker">TASK IMAGES</span><h2>任务图片</h2></div>
          <button onClick={onClose} aria-label="关闭">×</button>
        </header>
        <div className="drawer-product">
          <div className="product-thumb thumb-0"/>
          <div><b>{task.title || `任务 ${task.id.slice(0, 8)}`}</b><small>图片按槽位版本缓存，可单槽位重生成</small></div>
          <span className={`status ${taskStatusClass(task.status)}`}>{taskStatusText(task.status)}</span>
        </div>
        {error && <div className="drawer-form"><div className="inline-notice error">{error}</div></div>}
        {!images ? <div className="drawer-form"><PanelLoading text="读取图片列表…"/></div>
          : images.length === 0 ? <div className="drawer-form"><PanelEmpty text="该任务暂无生成图片"/></div>
          : <div className="drawer-form">
              <div className="task-image-grid">
                {images.map((img) => (
                  <div className="task-image-cell" key={`${img.slot}-${img.version}`}>
                    <img src={img.url} alt={img.slot} onError={(e) => { e.currentTarget.style.opacity = "0.25" }}/>
                    <small>{img.slot} · v{img.version}{img.created_at ? ` · ${formatDateTime(img.created_at)}` : ""}</small>
                    <button className="button ghost" disabled={regenSlot === img.slot} onClick={() => regen(img.slot)}>{regenSlot === img.slot ? "生成中…" : "重生成"}</button>
                  </div>
                ))}
              </div>
            </div>}
      </section>
    </div>
  )
}

function TaskDetailDrawer({ task, onClose, onOpenImages }: { task: TaskListItem; onClose: () => void; onOpenImages: () => void }) {
  const active = task.status === "running" || task.status === "pending" || task.status === "uploading"
  const { data: status, error: pollError } = usePolling<TaskStatusResponse>(
    () => api.get(`/task_status/${task.id}`),
    3000,
    active,
  )
  const [draftState, setDraftState] = useState<{ queried: boolean; id: string | null; error: string }>({ queried: false, id: null, error: "" })
  const [events, setEvents] = useState<TaskProgressEvent[]>([])
  const [draftBusy, setDraftBusy] = useState(false)
  const navigate = useNavigate()

  const progress = status?.progress ?? task.progress ?? null
  const percent = progress?.percent
  const hasProgress = progress != null && percent != null && Number.isFinite(percent)
  const pct = Math.max(0, Math.min(100, Number(percent ?? 0)))

  const showDraft = async () => {
    setDraftBusy(true)
    try {
      const res = await api.get<TaskDraftResponse>(`/tasks/${task.id}/draft`)
      setDraftState({ queried: true, id: res.draft_id ?? null, error: "" })
    } catch (e) { setDraftState({ queried: true, id: null, error: apiErrorMessage(e) }) }
    finally { setDraftBusy(false) }
  }

  const summary = status?.result?.product_summary as Record<string, unknown>[] | undefined

  useEffect(() => {
    let cancelled = false
    if (!active) {
      api.get<TaskProgressResponse>(`/tasks/${task.id}/progress`)
        .then((r) => { if (!cancelled) setEvents(r.events) })
        .catch(() => { /* 事件表未迁移/无事件 → 静默 */ })
      return
    }
    const token = getSession()?.token ?? ""
    let es: EventSource | null = null
    try {
      es = new EventSource(`/api/v1/progress/${task.id}/stream?token=${encodeURIComponent(token)}`)
      es.addEventListener("progress", (e) => {
        if (cancelled) return
        try {
          const ev = JSON.parse((e as MessageEvent).data) as TaskProgressEvent
          setEvents((prev) => [...prev.filter((x) => x.seq !== ev.seq), ev])
        } catch { /* ignore */ }
      })
    } catch { es = null }
    const iv = window.setInterval(() => {
      api.get<TaskProgressResponse>(`/tasks/${task.id}/progress`)
        .then((r) => { if (!cancelled) setEvents(r.events) })
        .catch(() => { /* 静默 */ })
    }, 5000)
    return () => { cancelled = true; es?.close(); window.clearInterval(iv) }
  }, [task.id, active])

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="product-drawer listing-editor" role="dialog" aria-modal="true" aria-label="任务详情" onMouseDown={e => e.stopPropagation()}>
        <header>
          <div><span className="panel-kicker">TASK DETAIL</span><h2>任务详情</h2></div>
          <button onClick={onClose} aria-label="关闭">×</button>
        </header>
        <div className="drawer-product">
          <div className="product-thumb thumb-0"/>
          <div><b>{task.title || `任务 ${task.id.slice(0, 8)}`}</b><small>{task.shop_name || `Client-Id ${task.ozon_client_id ?? "—"}`} · {formatDateTime(task.created_at)}</small></div>
          <span className={`status task-row-status ${taskStatusClass(task.status)}`}>{taskStatusText(task.status)}</span>
        </div>
        <div className="drawer-form">
          <div className="publish-row"><span>任务状态</span><b><span className={`status ${taskStatusClass(task.status)}`}>{taskStatusText(task.status)}</span></b></div>
          <div className="publish-row"><span>进度</span><b>{hasProgress ? `${Math.round(pct)}%` : "进度不可用"}</b></div>
          {hasProgress && <div className="task-progress" style={{ maxWidth: "none", margin: "8px 0 4px" }}><i style={{ width: `${pct}%` }}/></div>}
          {progress?.stage && <p style={{ fontSize: 10, color: "#89847f", margin: "4px 0 0" }}>阶段：{progress.stage}{progress.message ? ` · ${progress.message}` : ""}</p>}
          {events.length > 0 && (
            <div style={{ marginTop: 10, fontSize: 11, maxHeight: 160, overflowY: "auto" }}>
              <b>执行时间线</b>
              {events.slice(-20).map((ev) => (
                <div key={ev.seq} style={{ display: "flex", gap: 8, padding: "2px 0", borderBottom: "1px solid rgba(0,0,0,0.05)" }}>
                  <span style={{ opacity: 0.6, whiteSpace: "nowrap" }}>{ev.started_at ? ev.started_at.slice(11, 19) : ""}</span>
                  <span style={{ opacity: 0.85 }}>{ev.node}{ev.step ? `/${ev.step}` : ""}</span>
                  <span style={{ marginLeft: "auto", color: ev.status === "failed" ? "#e20e0e" : ev.status === "finished" ? "#1a7f37" : "inherit" }}>{ev.message || ev.status}</span>
                </div>
              ))}
            </div>
          )}
          {(status?.error_message || task.status === "failed") && <div className="inline-notice error" style={{ marginTop: 12 }}>{status?.error_message || "任务执行失败"}</div>}
          {pollError && !status && <div className="inline-notice error" style={{ marginTop: 12 }}>进度读取失败：{pollError}</div>}
          {active && <div className="inline-notice" style={{ marginTop: 12 }}>任务运行中，进度每 3 秒自动刷新…</div>}
          {summary && summary.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <div className="publish-row"><span>商品摘要</span><b>{String(summary[0]?.title ?? summary[0]?.name ?? task.title ?? "—")}</b></div>
              {Object.entries(summary[0] ?? {}).filter(([k]) => !["title", "name"].includes(k)).slice(0, 6).map(([k, v]) => (
                <div className="publish-row" key={k}><span>{k}</span><b>{String(v ?? "—")}</b></div>
              ))}
            </div>
          )}
        </div>
        <div className="drawer-form">
          <div className="publish-row"><span>关联草稿</span><b>{!draftState.queried ? "未查询" : draftState.error ? "读取失败" : draftState.id ? `草稿 ID：${draftState.id}` : "无关联草稿"}</b></div>
          {draftState.id && (
            <div className="inline-notice" style={{ marginTop: 8 }}>
              <button className="text-button" onClick={() => { onClose(); navigate("/collection") }}>前往采集箱编辑 →</button>
            </div>
          )}
          {draftState.queried && !draftState.id && !draftState.error && <p style={{ fontSize: 10, color: "#89847f", margin: "6px 0 0" }}>该任务无关联采集箱草稿（直连任务）。</p>}
        </div>
        <footer className="editor-footer">
          <span className="save-state">{active ? "● 实时进度" : "终态"}</span>
          <button className="button ghost" onClick={showDraft} disabled={draftBusy}>{draftBusy ? "查询中…" : "查看草稿"}</button>
          <button className="button ghost" onClick={onOpenImages}>查看图片</button>
          <button className="button primary" onClick={onClose}>关闭</button>
        </footer>
      </section>
    </div>
  )
}

function TaskRow({ task, onDetail, onImages }: { task: TaskListItem; onDetail: () => void; onImages: () => void }) {
  const percent = task.progress?.percent
  const hasProgress = task.progress != null && percent != null && Number.isFinite(percent)
  const pct = Math.max(0, Math.min(100, Number(percent ?? 0)))
  return (
    <div className="task">
      <div className="task-icon">↥</div>
      <div>
        <strong>{task.title || task.item_id || `任务 ${task.id.slice(0, 8)}`}</strong>
        <span>{task.shop_name || `Client-Id ${task.ozon_client_id ?? "—"}`} · {formatDateTime(task.created_at)}</span>
        {hasProgress
          ? <div className="task-progress"><i style={{ width: `${pct}%` }}/></div>
          : <span className="no-progress">进度不可用</span>}
        <span className="task-row-actions">
          <button onClick={onDetail}>详情</button>
          <button onClick={onImages}>图片</button>
        </span>
      </div>
      <b><span className={`status task-row-status ${taskStatusClass(task.status)}`}>{taskStatusText(task.status)}</span></b>
    </div>
  )
}

export default function TasksPanel({ onCreateAutomation }: { onCreateAutomation: () => void }) {
  const { data, loading, error, reload } = useApi<TaskListResponse>(() => api.get("/tasks"), [])
  const [detail, setDetail] = useState<TaskListItem | null>(null)
  const [imagesTask, setImagesTask] = useState<TaskListItem | null>(null)

  const items = data?.items ?? []
  const metrics = useMemo(() => {
    const running = items.filter((t) => t.status === "running" || t.status === "pending" || t.status === "uploading").length
    const completed = items.filter((t) => t.status === "completed").length
    const failed = items.filter((t) => t.status === "failed" || t.status === "rejected" || t.status === "cancelled").length
    return { total: data?.total ?? items.length, running, completed, failed }
  }, [items, data])

  return (
    <>
      <PageHeader kicker="AUTOMATION CENTER" title="任务中心" description="查看所有上架任务与实时进度，支持查看任务图片与关联草稿。" action="＋ 创建自动化" onAction={onCreateAutomation}/>
      <section className="metric-grid">
        <Metric label="任务总数" value={String(metrics.total)} note="当前账号全部任务" red/>
        <Metric label="运行中" value={String(metrics.running)} note="需要关注"/>
        <Metric label="已完成" value={String(metrics.completed)} note="上架成功"/>
        <Metric label="失败" value={String(metrics.failed)} note="可重新提交"/>
      </section>
      <section className="wide-section">
        {loading ? <div className="panel"><PanelLoading text="正在读取任务…"/></div>
          : error ? <div className="panel"><PanelError message={error} onRetry={reload}/></div>
          : items.length === 0 ? <div className="panel"><PanelEmpty text="暂无任务，从采集箱提交上架后这里会出现任务"/></div>
          : <article className="panel tasks-panel">
              <div className="panel-head"><div><span className="panel-kicker">TASKS · {items.length}</span><h2>上架任务</h2></div><button className="text-button" onClick={reload}>刷新</button></div>
              {items.map((t) => <TaskRow key={t.id} task={t} onDetail={() => setDetail(t)} onImages={() => setImagesTask(t)}/>)}
            </article>}
      </section>
      {detail && <TaskDetailDrawer task={detail} onClose={() => setDetail(null)} onOpenImages={() => { setImagesTask(detail); setDetail(null) }}/>}
      {imagesTask && <ImageDrawer task={imagesTask} onClose={() => setImagesTask(null)}/>}
    </>
  )
}
