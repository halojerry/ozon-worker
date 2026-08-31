import { useCallback, useEffect, useState } from "react"
import { api, ApiError, getSession } from "../api/client"
import { apiErrorMessage, useApi } from "../api/hooks"
import { PageHeader, PanelEmpty, PanelError, PanelLoading } from "./ui"

type Settings = Record<string, boolean | number>

const LABELS: Record<string, string> = {
  fx_buffer_percent: "默认汇率缓冲(%)",
  low_stock_threshold: "低库存预警值",
  auto_review_enabled: "自动上架审核(评分达标自动提交)",
  auto_review_score: "自动提交评分阈值",
  order_status_notify: "订单状态提醒",
  task_fail_notify: "任务失败通知",
  daily_report_enabled: "每日经营日报",
}

const GROUP: Record<string, string[]> = {
  "业务参数": ["fx_buffer_percent", "low_stock_threshold", "auto_review_enabled", "auto_review_score"],
  "通知设置": ["order_status_notify", "task_fail_notify", "daily_report_enabled"],
}

interface LogisticsRate {
  id: number
  scoring_group: string
  service_level: string
  tpl_provider: string
  delivery_method?: string | null
  base_cost: number
  per_gram_rate: number
  weight_min: number
  weight_max: number
}

interface AdminUser {
  id: string
  username: string
  quota?: number | null
  role: "admin" | "user"
  created_at?: string | null
  store_count?: number
  task_count?: number
}

export default function SettingsPanel() {
  const isAdmin = getSession()?.role === "admin"
  const [tab, setTab] = useState<"业务参数" | "通知设置" | "物流费率" | "工人变量" | "用户管理">("业务参数")
  const [settings, setSettings] = useState<Settings>({})
  const [loaded, setLoaded] = useState(false)
  const [notice, setNotice] = useState("")
  const [saving, setSaving] = useState(false)

  const loadSettings = useCallback(async () => {
    try {
      const s = await api.get<Settings>("/settings")
      setSettings(s)
      setLoaded(true)
    } catch (e) {
      setNotice(`设置读取失败: ${apiErrorMessage(e)}`)
      setLoaded(true)
    }
  }, [])

  useEffect(() => { loadSettings() }, [loadSettings])

  const save = async () => {
    setSaving(true); setNotice("")
    try {
      const updated = await api.put<Settings>("/settings", settings)
      setSettings(updated)
      setNotice("✓ 已保存")
    } catch (e) { setNotice(apiErrorMessage(e)) }
    finally { setSaving(false) }
  }

  const set = (key: string, value: boolean | number) =>
    setSettings((prev) => ({ ...prev, [key]: value }))

  const activeKeys = tab === "物流费率" ? [] : GROUP[tab as "业务参数" | "通知设置"]

  return (
    <>
      <PageHeader kicker="SYSTEM PREFERENCES" title="系统设置" description="管理全局业务规则与通知偏好；店铺授权与凭证请在店铺管理中维护。" action="保存修改" onAction={save} />
      <section className="settings-layout">
        <aside className="panel setting-tabs">
          {(["业务参数", "通知设置"] as const).map((x) => (
            <button key={x} className={tab === x ? "selected" : ""} onClick={() => setTab(x)}>{x}</button>
          ))}
          {isAdmin && <>
            <button className={tab === "物流费率" ? "selected" : ""} onClick={() => setTab("物流费率")}>物流费率(管理员)</button>
            <button className={tab === "工人变量" ? "selected" : ""} onClick={() => setTab("工人变量")}>工人变量(管理员)</button>
            <button className={tab === "用户管理" ? "selected" : ""} onClick={() => setTab("用户管理")}>用户管理(管理员)</button>
          </>}
        </aside>
        <article className="panel setting-detail">
          <span className="panel-kicker">{tab.toUpperCase()}</span>
          <h2>{tab}</h2>
          {notice && <div className={`inline-notice ${notice.startsWith("✓") ? "" : "error"}`}>{notice}</div>}
          {!loaded ? <PanelLoading /> : tab === "物流费率" ? <LogisticsAdmin /> : tab === "工人变量" ? <WorkerConfigAdmin /> : tab === "用户管理" ? <UserAdmin /> : (
            <div className="drawer-form">
              {activeKeys.map((key) => (
                <div className="setting-row" key={key}>
                  <div><b>{LABELS[key] ?? key}</b><span>保存后对新任务/新上架生效</span></div>
                  {typeof settings[key] === "boolean" ? (
                    <button className={`switch ${settings[key] ? "on" : ""}`} aria-label={LABELS[key]} onClick={() => set(key, !settings[key])}><i /></button>
                  ) : (
                    <input
                      type="number"
                      value={String(settings[key] ?? "")}
                      onChange={(e) => set(key, Number(e.target.value))}
                    />
                  )}
                </div>
              ))}
              <footer className="settings-save">
                <span>{saving ? "保存中…" : "修改后点击右上角「保存修改」"}</span>
                <button className="button primary" disabled={saving} onClick={save}>保存{tab}</button>
              </footer>
            </div>
          )}
        </article>
      </section>
    </>
  )
}

function LogisticsAdmin() {
  const [csv, setCsv] = useState("")
  const [msg, setMsg] = useState("")
  const fetcher = useCallback(() => api.get<{ items: LogisticsRate[]; total: number }>("/admin/logistics/rates?limit=200"), [])
  const { data, loading, error, reload } = useApi(fetcher, [])

  const updateRate = async (r: LogisticsRate, patch: { base_cost: number; per_gram_rate: number }) => {
    setMsg("")
    try {
      await api.put(`/admin/logistics/rates/${r.id}`, {
        scoring_group: r.scoring_group,
        service_level: r.service_level,
        tpl_provider: r.tpl_provider,
        delivery_method: r.delivery_method,
        base_cost: patch.base_cost,
        per_gram_rate: patch.per_gram_rate,
        weight_min: r.weight_min,
        weight_max: r.weight_max,
        sum_limit_cm: 0,
        longest_limit_cm: 0,
        charge_type: "weight",
      })
      setMsg("✓ 已更新"); reload()
    } catch (e) { setMsg(apiErrorMessage(e)) }
  }

  const importCsv = async () => {
    setMsg("")
    if (!csv.trim()) { setMsg("请先粘贴 CSV 内容"); return }
    try {
      const res = await api.post<{ imported: number; updated: number; errors?: unknown[] }>("/admin/logistics/rates/import", { csv })
      setMsg(`✓ 导入完成: 新增 ${res.imported ?? 0} / 更新 ${res.updated ?? 0}`)
      reload()
    } catch (e) { setMsg(e instanceof ApiError ? e.message : "导入失败") }
  }

  return (
    <div className="drawer-form">
      {msg && <div className={`inline-notice ${msg.startsWith("✓") ? "" : "error"}`}>{msg}</div>}
      {loading ? <PanelLoading /> : error ? <PanelError message={error} onRetry={reload} /> : (
        (data?.items ?? []).length === 0 ? <PanelEmpty text="暂无物流费率" /> : (
          <div className="table-wrap">
            <table style={{ fontSize: 11 }}>
              <thead><tr><th>分组</th><th>服务</th><th>承运商</th><th>重量区间</th><th>基础费</th><th>每克费率</th><th>操作</th></tr></thead>
              <tbody>
                {(data?.items ?? []).map((r) => (
                  <RateRow key={r.id} rate={r} onSave={(p) => updateRate(r, p)} />
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
      <div style={{ marginTop: 14 }}>
        <label>CSV 批量导入(表头: scoring_group,service_level,tpl_provider,base_cost,per_gram_rate,weight_min,weight_max)</label>
        <textarea value={csv} onChange={(e) => setCsv(e.target.value)} rows={4} style={{ width: "100%", fontSize: 11 }} placeholder={"scoring_group,service_level,tpl_provider,base_cost,per_gram_rate,weight_min,weight_max\nFBS,Standard,RETS,5,0.02,0,500"} />
        <button className="button primary" onClick={importCsv} style={{ marginTop: 6 }}>导入费率</button>
      </div>
    </div>
  )
}

function RateRow({ rate, onSave }: { rate: LogisticsRate; onSave: (p: { base_cost: number; per_gram_rate: number }) => void }) {
  const [base, setBase] = useState(String(rate.base_cost))
  const [gram, setGram] = useState(String(rate.per_gram_rate))
  return (
    <tr>
      <td>{rate.scoring_group}</td>
      <td>{rate.service_level}</td>
      <td>{rate.tpl_provider}</td>
      <td>{rate.weight_min}–{rate.weight_max}g</td>
      <td><input type="number" value={base} onChange={(e) => setBase(e.target.value)} style={{ width: 60 }} /></td>
      <td><input type="number" step="0.001" value={gram} onChange={(e) => setGram(e.target.value)} style={{ width: 70 }} /></td>
      <td><button className="text-button" onClick={() => onSave({ base_cost: Number(base), per_gram_rate: Number(gram) })}>保存</button></td>
    </tr>
  )
}

function WorkerConfigAdmin() {
  const [selected, setSelected] = useState("")
  const [content, setContent] = useState("")
  const [msg, setMsg] = useState("")
  const [busy, setBusy] = useState(false)
  const list = useApi<{ name: string }[]>(() => api.get("/admin/config"), [])
  const configs = list.data ?? []

  const load = async (name: string) => {
    setSelected(name); setMsg("")
    try {
      const r = await api.get<Record<string, unknown>>(`/admin/config/${name}`)
      setContent(JSON.stringify(r, null, 2))
    } catch (e) { setMsg(apiErrorMessage(e)) }
  }

  const save = async () => {
    if (!selected) return
    setBusy(true); setMsg("")
    try {
      const r = await api.put<{ updated: boolean; backup_path?: string }>(`/admin/config/${selected}`, { content })
      setMsg(`✓ 已保存${r.backup_path ? "（已自动备份）" : ""}`)
    } catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy(false) }
  }

  return (
    <div className="drawer-form">
      {msg && <div className={`inline-notice ${msg.startsWith("✓") ? "" : "error"}`}>{msg}</div>}
      <p style={{ fontSize: 11, opacity: 0.75 }}>修改 worker/config/*.json，保存前校验 JSON，自动备份；即时生效（config 目录热加载）。</p>
      {list.loading ? <PanelLoading /> : list.error ? <PanelError message={list.error} onRetry={list.reload} /> : (
        configs.length === 0 ? <PanelEmpty text="暂无配置" /> : (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
            {configs.map((c) => (
              <button key={c.name} className={selected === c.name ? "button primary" : "button ghost"} onClick={() => load(c.name)}>{c.name}</button>
            ))}
          </div>
        )
      )}
      {selected && (
        <>
          <label>编辑 {selected}（JSON）</label>
          <textarea value={content} onChange={(e) => setContent(e.target.value)} rows={14} style={{ width: "100%", fontFamily: "var(--font-mono, monospace)", fontSize: 11 }} />
          <button className="button primary" disabled={busy || !selected} onClick={save} style={{ marginTop: 6 }}>{busy ? "保存中…" : "保存配置"}</button>
        </>
      )}
    </div>
  )
}

function UserAdmin() {
  const [msg, setMsg] = useState("")
  const [busy, setBusy] = useState("")
  const [create, setCreate] = useState({ email: "", role: "user", quota: "0" })
  const users = useApi<AdminUser[]>(() => api.get("/admin/users"), [])
  const rows = users.data ?? []

  const createUser = async () => {
    if (!create.email.trim()) return
    setBusy("create"); setMsg("")
    try {
      await api.post("/admin/users", { email: create.email.trim(), role: create.role, quota: Number(create.quota) })
      setMsg("✓ 已创建"); setCreate({ email: "", role: "user", quota: "0" }); users.reload()
    } catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy("") }
  }

  const patchUser = async (u: AdminUser, data: Record<string, unknown>) => {
    setBusy("patch"); setMsg("")
    try { await api.patch(`/admin/users/${u.id}`, data); setMsg("✓ 已更新"); users.reload() }
    catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy("") }
  }

  return (
    <div className="drawer-form">
      {msg && <div className={`inline-notice ${msg.startsWith("✓") ? "" : "error"}`}>{msg}</div>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        <input placeholder="用户名/邮箱" value={create.email} onChange={(e) => setCreate({ ...create, email: e.target.value })} style={{ flex: 2, minWidth: 160 }} />
        <select value={create.role} onChange={(e) => setCreate({ ...create, role: e.target.value })}><option value="user">普通</option><option value="admin">管理员</option></select>
        <input placeholder="额度" type="number" value={create.quota} onChange={(e) => setCreate({ ...create, quota: e.target.value })} style={{ width: 110 }} />
        <button className="button primary" disabled={busy === "create" || !create.email.trim()} onClick={createUser}>创建用户</button>
      </div>
      {users.loading ? <PanelLoading /> : users.error ? <PanelError message={users.error} onRetry={users.reload} /> : (
        rows.length === 0 ? <PanelEmpty text="暂无用户" /> : (
          <div className="table-wrap">
            <table style={{ fontSize: 11 }}>
              <thead><tr><th>用户名</th><th>角色</th><th>额度</th><th>店铺</th><th>任务</th><th>创建时间</th><th>操作</th></tr></thead>
              <tbody>
                {rows.map((u) => (
                  <tr key={u.id}>
                    <td>{u.username}</td>
                    <td>
                      <select value={u.role} onChange={(e) => patchUser(u, { role: e.target.value })}>
                        <option value="user">普通</option><option value="admin">管理员</option>
                      </select>
                    </td>
                    <td><input type="number" defaultValue={u.quota ?? ""} onBlur={(e) => patchUser(u, { quota: Number(e.target.value) })} style={{ width: 80 }} /></td>
                    <td>{u.store_count}</td><td>{u.task_count}</td>
                    <td>{u.created_at ? new Date(u.created_at).toLocaleString("zh-CN", { hour12: false }) : "—"}</td>
                    <td><button className="text-button" onClick={() => patchUser(u, { status: "2" })}>禁用</button> <button className="text-button" onClick={() => patchUser(u, { status: "1" })}>启用</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  )
}
