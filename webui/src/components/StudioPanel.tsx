import { useCallback, useEffect, useState } from "react"
import { api } from "../api/client"
import { useApi, usePolling, ImageTaskItem, ImageTaskListResponse } from "../api/hooks"
import { PageHeader, PanelLoading, PanelError, PanelEmpty } from "./ui"

const TASK_TYPES = [
  { value: "remove_bg", label: "去背景", desc: "智能识别主体，去除多余背景" },
  { value: "upscale", label: "尺寸裁剪", desc: "批量裁剪为 Ozon 标准尺寸" },
  { value: "background_change", label: "背景替换", desc: "将商品背景替换为纯色或场景" },
]

const STATUS_LABELS: Record<string, string> = {
  pending: "待处理",
  processing: "进行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
}

export default function StudioPanel() {
  const [tab, setTab] = useState("全部任务")
  const [showForm, setShowForm] = useState(false)
  const [formType, setFormType] = useState("remove_bg")
  const [formUrl, setFormUrl] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState("")

  const fetchTasks = useCallback(() => api.get<ImageTaskListResponse>("/image-tasks?limit=50"), [])
  const { data: taskData, loading, error, reload } = useApi(fetchTasks, [])

  const hasActiveTasks = (taskData?.items ?? []).some(t => t.status === "pending" || t.status === "processing")
  const { data: pollData } = usePolling(fetchTasks, 3000, hasActiveTasks)

  const displayTasks = (pollData?.items ?? taskData?.items ?? [])

  useEffect(() => {
    if (pollData) reload()
  }, [pollData, reload])

  const filteredTasks = displayTasks.filter(t =>
    tab === "全部任务" || STATUS_LABELS[t.status] === tab
  )

  const handleSubmit = async () => {
    if (!formUrl.trim()) return
    setSubmitting(true)
    setFormError("")
    try {
      await api.post("/image-tasks", { type: formType, input_image_url: formUrl.trim() })
      setShowForm(false)
      setFormUrl("")
      reload()
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "创建失败")
    } finally {
      setSubmitting(false)
    }
  }

  const handleCancel = async (taskId: string) => {
    try {
      await api.post(`/image-tasks/${taskId}/cancel`, {})
      reload()
    } catch {
      /* ignore */
    }
  }

  const statusCounts = {
    total: displayTasks.length,
    active: displayTasks.filter(t => t.status === "pending" || t.status === "processing").length,
    completed: displayTasks.filter(t => t.status === "completed").length,
    failed: displayTasks.filter(t => t.status === "failed").length,
  }

  return (
    <>
      <PageHeader
        kicker="IMAGE PROCESSING CENTER"
        title="图片工坊"
        description="批量处理商品图，快速生成更适合 Ozon 的主图与详情素材。"
        action="▱ 新建任务"
        onAction={() => setShowForm(!showForm)}
      />

      <section className="studio-statbar">
        <div><span>今日处理任务</span><b>{statusCounts.total}</b></div>
        <div><span>处理中</span><b>{statusCounts.active}</b></div>
        <div><span>已完成</span><b>{statusCounts.completed}</b></div>
        <div><span>失败</span><b>{statusCounts.failed}</b></div>
        <button className="button primary" onClick={() => setShowForm(!showForm)}>▱ 新建任务</button>
      </section>

      {showForm && (
        <section className="panel" style={{ padding: 20, marginBottom: 16 }}>
          <div className="panel-head"><div><span className="panel-kicker">NEW IMAGE TASK</span><h2>新建图片任务</h2></div></div>
          <div className="drawer-form">
            <label>
              任务类型
              <select value={formType} onChange={e => setFormType(e.target.value)} style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border, #ddd)" }}>
                {TASK_TYPES.map(t => <option key={t.value} value={t.value}>{t.label} — {t.desc}</option>)}
              </select>
            </label>
            <label>
              输入图片 URL
              <input value={formUrl} onChange={e => setFormUrl(e.target.value)} placeholder="https://example.com/image.jpg" style={{ width: "100%", padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border, #ddd)" }} />
            </label>
            {formError && <p style={{ color: "#b30c0c", fontSize: 13 }}>⚠ {formError}</p>}
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="button ghost" onClick={() => setShowForm(false)}>取消</button>
              <button className="button primary" onClick={handleSubmit} disabled={!formUrl.trim() || submitting}>
                {submitting ? "提交中…" : "创建任务"}
              </button>
            </div>
          </div>
        </section>
      )}

      <section className="studio-tabs">
        {["全部任务", "处理中", "已完成"].map(t => (
          <button key={t} onClick={() => setTab(t)} className={tab === t ? "selected" : ""}>{t}</button>
        ))}
      </section>

      <section className="image-task-grid">
        {loading ? <PanelLoading /> : error ? <PanelError message={error} onRetry={reload} /> : (
          filteredTasks.length === 0 ? <PanelEmpty text="暂无图片任务" /> :
          filteredTasks.map(task => (
            <article className="panel image-task" key={task.id}>
              <div className="image-task-title">
                <span className="tool-icon">{TASK_TYPES.find(t => t.value === task.type)?.label?.[0] ?? "?"}</span>
                <div>
                  <h2>{TASK_TYPES.find(t => t.value === task.type)?.label ?? task.type}</h2>
                  <p>{TASK_TYPES.find(t => t.value === task.type)?.desc ?? ""}</p>
                </div>
              </div>
              <div className="before-after">
                <div className="scene scene-0">
                  <img src={task.input_image_url} alt="输入" style={{ width: "100%", height: "100%", objectFit: "cover", borderRadius: 6 }} />
                  <b>处理前</b>
                </div>
                <span>›</span>
                <div className="scene scene-0 after">
                  {task.result_image_url && task.status === "completed" ? (
                    <img src={task.result_image_url} alt="输出" style={{ width: "100%", height: "100%", objectFit: "cover", borderRadius: 6 }} />
                  ) : (
                    <b>{task.status === "completed" ? "完成" : "待处理"}</b>
                  )}
                </div>
              </div>
              <div className="task-meter">
                <div>
                  <span className={`status ${task.status === "completed" ? "dark" : task.status === "failed" ? "red" : "line"}`}>
                    {STATUS_LABELS[task.status] ?? task.status}
                  </span>
                </div>
              </div>
              <small>{task.created_at ? new Date(task.created_at).toLocaleString("zh-CN") : "—"}</small>
              {task.status === "pending" && (
                <button className="button ghost" style={{ marginTop: 8, width: "100%" }} onClick={() => handleCancel(task.id)}>
                  取消任务
                </button>
              )}
              {task.error_message && (
                <small style={{ color: "#b30c0c", marginTop: 4, display: "block" }}>⚠ {task.error_message}</small>
              )}
            </article>
          ))
        )}
      </section>

      <section className="wide-section">
        <article className="panel orders-panel">
          <div className="panel-head">
            <div><span className="panel-kicker">ALL TASKS</span><h2>全部任务记录</h2></div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>ID</th><th>类型</th><th>状态</th><th>创建时间</th><th>操作</th></tr>
              </thead>
              <tbody>
                {filteredTasks.map(task => (
                  <tr key={task.id}>
                    <td className="order-no">{task.id.slice(0, 8)}…</td>
                    <td><b>{TASK_TYPES.find(t => t.value === task.type)?.label ?? task.type}</b></td>
                    <td>
                      <span className={`status ${task.status === "completed" ? "dark" : task.status === "failed" ? "red" : "line"}`}>
                        {STATUS_LABELS[task.status] ?? task.status}
                      </span>
                    </td>
                    <td>{task.created_at ? new Date(task.created_at).toLocaleString("zh-CN") : "—"}</td>
                    <td>
                      {task.status === "pending" && (
                        <button className="text-button" onClick={() => handleCancel(task.id)}>取消</button>
                      )}
                      {task.result_image_url && (
                        <a href={task.result_image_url} target="_blank" rel="noreferrer" className="text-button">查看结果</a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {filteredTasks.length === 0 && <div className="empty-state">暂无任务记录</div>}
          </div>
        </article>
      </section>
    </>
  )
}
