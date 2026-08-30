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

export default function SettingsPanel() {
  const isAdmin = getSession()?.role === "admin"
  const [tab, setTab] = useState<"业务参数" | "通知设置" | "物流费率">("业务参数")
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
          {isAdmin && <button className={tab === "物流费率" ? "selected" : ""} onClick={() => setTab("物流费率")}>物流费率(管理员)</button>}
        </aside>
        <article className="panel setting-detail">
          <span className="panel-kicker">{tab.toUpperCase()}</span>
          <h2>{tab}</h2>
          {notice && <div className={`inline-notice ${notice.startsWith("✓") ? "" : "error"}`}>{notice}</div>}
          {!loaded ? <PanelLoading /> : tab === "物流费率" ? <LogisticsAdmin /> : (
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
