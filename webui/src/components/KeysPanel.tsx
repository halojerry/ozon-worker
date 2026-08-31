import { useCallback, useState } from "react"
import { api } from "../api/client"
import type { MxouKey, MxouKeyCreateResponse } from "../api/hooks"
import { apiErrorMessage, useApi } from "../api/hooks"
import { PageHeader, PanelEmpty, PanelError, PanelLoading } from "./ui"

export default function KeysPanel() {
  const [creating, setCreating] = useState(false)
  const [newKeyName, setNewKeyName] = useState("")
  const [newKeyResult, setNewKeyResult] = useState<MxouKeyCreateResponse | null>(null)
  const [busy, setBusy] = useState("")
  const [msg, setMsg] = useState("")

  const fetcher = useCallback(() => api.get<MxouKey[]>("/mxou/keys"), [])
  const { data, loading, error, reload } = useApi(fetcher)

  const keys = data ?? []

  const createKey = async () => {
    if (!newKeyName.trim()) return
    setBusy("create"); setMsg("")
    try {
      const r = await api.post<MxouKeyCreateResponse>("/mxou/keys", { name: newKeyName.trim() })
      setNewKeyResult(r)
      setNewKeyName("")
      setCreating(false)
      reload()
    } catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy("") }
  }

  const deleteKey = async (id: string) => {
    setBusy("delete"); setMsg("")
    try {
      await api.delete(`/mxou/keys/${id}`)
      reload()
    } catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy("") }
  }

  const selectKey = async (id: string) => {
    setBusy("select"); setMsg("")
    try {
      await api.post(`/mxou/keys/${id}/select`, {})
      setMsg("已切换活跃 Key")
      reload()
    } catch (e) { setMsg(apiErrorMessage(e)) }
    finally { setBusy("") }
  }

  return (
    <>
      <PageHeader kicker="API KEY MANAGEMENT" title="API Key 管理" description="管理平台访问凭证，控制 API 调用权限。" action="＋ 创建 Key" onAction={() => setCreating(true)} />
      {loading && <PanelLoading />}
      {error && (
        <div className="panel">
          <PanelEmpty text="未登录 MXOU / 无法读取密钥；请确认已登录后重试" />
          <div style={{ marginTop: 8, textAlign: "center" }}><button className="button ghost" onClick={reload}>重试</button></div>
        </div>
      )}
      {!loading && !error && keys.length === 0 && <PanelEmpty text="暂无 API Key" />}
      {!loading && !error && keys.length > 0 && (
        <section className="wide-section">
          <article className="panel order-table">
            <div><span>名称</span><span>状态</span><span>操作</span></div>
            {keys.map((k) => (
              <div key={k.id}>
                <b>{k.name}</b>
                <span className={`status ${k.status === 1 ? "" : "line"}`}>{k.status === 1 ? "活跃" : "停用"}</span>
                <span className="row-links">
                  <button onClick={() => selectKey(k.id)}>切换</button>
                  <button onClick={() => deleteKey(k.id)}>删除</button>
                </span>
              </div>
            ))}
          </article>
        </section>
      )}
      {creating && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setCreating(false)}>
          <section className="product-drawer" role="dialog" aria-modal="true" onMouseDown={(e) => e.stopPropagation()}>
            <header>
              <div>
                <span className="panel-kicker">CREATE KEY</span>
                <h2>创建 API Key</h2>
              </div>
              <button onClick={() => setCreating(false)} aria-label="关闭">×</button>
            </header>
            <div className="drawer-form">
              <label>Key 名称<input value={newKeyName} onChange={(e) => setNewKeyName(e.target.value)} placeholder="如 测试环境" /></label>
            </div>
            {msg && <div className="inline-notice error">{msg}</div>}
            <footer className="editor-footer">
              <button className="button ghost" onClick={() => setCreating(false)}>取消</button>
              <button className="button primary" onClick={createKey} disabled={busy === "create" || !newKeyName.trim()}>{busy === "create" ? "创建中…" : "创建"}</button>
            </footer>
          </section>
        </div>
      )}
      {newKeyResult && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setNewKeyResult(null)}>
          <section className="product-drawer" role="dialog" aria-modal="true" onMouseDown={(e) => e.stopPropagation()}>
            <header>
              <div>
                <span className="panel-kicker">KEY CREATED</span>
                <h2>Key 已创建</h2>
              </div>
              <button onClick={() => setNewKeyResult(null)} aria-label="关闭">×</button>
            </header>
            <div className="drawer-form">
              <div className="inline-notice">请立即复制此 Key，关闭后将无法再查看完整内容。</div>
              <div className="publish-row"><span>Key</span><b style={{ wordBreak: "break-all" }}>{newKeyResult.key}</b></div>
            </div>
            <footer className="editor-footer">
              <button className="button primary" onClick={() => { navigator.clipboard.writeText(newKeyResult.key); setMsg("已复制") }}>复制 Key</button>
            </footer>
          </section>
        </div>
      )}
    </>
  )
}
