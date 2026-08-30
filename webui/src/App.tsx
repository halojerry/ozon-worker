import { useEffect, useMemo, useState } from "react"
import worldMap from "./assets/world-map.svg"
import { ApiError, api, getSession, saveSession } from "./api/client"
import { PageHeader, Metric } from "./components/ui"
import DashboardPanel from "./components/DashboardPanel"
import SettingsPanel from "./components/SettingsPanel"
import StoresPanel from "./components/StoresPanel"
import CollectionPanel from "./components/CollectionPanel"
import TasksPanel from "./components/TasksPanel"
import ProductsPanel from "./components/ProductsPanel"
import OrdersPanel from "./components/OrdersPanel"
import TemplatesPanel from "./components/TemplatesPanel"
import PricingPanel from "./components/PricingPanel"
import DiscoveryPanel from "./components/DiscoveryPanel"
import KeysPanel from "./components/KeysPanel"
import AdminPanel from "./components/AdminPanel"
import SitePanel from "./components/SitePanel"
import BestsellersPanel from "./components/BestsellersPanel"
import DataScreenPanel from "./components/DataScreenPanel"
import StudioPanel from "./components/StudioPanel"
import { NavLink, Outlet, RouterProvider, createBrowserRouter, useLocation, useNavigate } from "react-router"

const navGroups: { group: string; items: string[][] }[] = [
  { group: "概览", items: [["◉", "工作台", "/"], ["⌁", "数据大屏", "/data"]] },
  { group: "运营中心", items: [["▦", "商品管理", "/products", "24"], ["□", "订单中心", "/orders", "8"], ["◷", "任务中心", "/tasks", "3"]] },
  { group: "AI 工具", items: [["✦", "智能定价", "/pricing"], ["⌘", "采集箱", "/collection"], ["♜", "选品广场", "/bestsellers"]] },
  { group: "管理", items: [["◌", "店铺管理", "/stores"], ["≡", "上架模板", "/templates"], ["⚙", "系统设置", "/settings"], ["♙", "平台后台", "/admin"], ["🔑", "API Key", "/keys"], ["🔍", "选品归档", "/discovery"], ["🌐", "站点管理", "/site"]] },
]

const getUserRole = () => getSession()?.role === "admin" ? "管理员" : "普通成员"




function Sparkline({ compact = false }: { compact?: boolean }) { return <svg viewBox="0 0 496 106" aria-hidden="true" className={compact ? "sparkline compact" : "sparkline"}><defs><linearGradient id="fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#e20e0e" stopOpacity=".18"/><stop offset="1" stopColor="#e20e0e" stopOpacity="0"/></linearGradient></defs><path d="M0 86 C30 78,42 80,66 67 S103 66,124 74 S162 50,188 57 S220 48,244 59 S279 36,304 41 S339 27,362 47 S395 40,418 25 S459 34,496 8 L496 106 L0 106Z" fill="url(#fill)"/><path d="M0 86 C30 78,42 80,66 67 S103 66,124 74 S162 50,188 57 S220 48,244 59 S279 36,304 41 S339 27,362 47 S395 40,418 25 S459 34,496 8" fill="none" stroke="#e20e0e" strokeWidth="2.5"/></svg> }

function Shell() {
  const [notice, setNotice] = useState(false)
  const location = useLocation()
  const currentUserRole = getUserRole()
  const isAdmin = currentUserRole === "管理员"
  const activeLabel = navGroups.flatMap(g => g.items).find(item => item[2] === location.pathname)?.[1] ?? "工作台"
  return <div className={`erp-shell ${location.pathname === "/data" ? "data-shell" : ""}`}><aside className="sidebar"><div className="brand"><div className="brand-mark">O</div><div><b>Ozon ERP</b><span>AI AUTOMATION</span></div></div><div className="workspace"><span className="store-dot"/> 深圳跨境旗舰店 <span className="chevron">⌄</span></div><nav aria-label="主导航">{navGroups.map(section => <div className="nav-group" key={section.group}><div className="nav-label">{section.group}</div>{section.items.filter(([, , path]) => path !== "/admin" || isAdmin).map(([icon,label,path,badge]) => <NavLink end={path === "/"} className="nav-item" to={path} key={path}><span className="nav-icon">{icon}</span><span>{label}</span>{badge && <small>{badge}</small>}</NavLink>)}</div>)}</nav><div className="sidebar-bottom"><div className="help-card"><span>✦</span><div><b>AI 运营助手</b><p>今日已生成 36 条建议</p></div></div><button className="account"><span className="avatar">K</span><span><b>Kate Lin</b><small>{currentUserRole}</small></span><span>···</span></button></div></aside><main className="main-content"><header className="topbar"><div className="crumb"><span>Ozon ERP</span><i>/</i><b>{activeLabel}</b></div><div className="top-actions"><button className="icon-button" aria-label="帮助">?</button><button onClick={() => setNotice(!notice)} className={`icon-button notification ${notice ? "has-notice" : ""}`} aria-label="通知">♢</button><button className="top-store"><span className="store-dot"/> Ozon Russia <span>⌄</span></button></div></header>{notice && <div className="notice-popover"><b>3 条待处理提醒</b><p>2 个商品待补充俄语属性</p><p>1 个自动化任务运行失败</p></div>}<Outlet/><footer>数据更新时间：2024-06-17 12:48:06 <span>OZON AI ERP · INTERNAL CONSOLE</span></footer></main></div>
}




function Dashboard(){return <DashboardPanel/>}
function Bestsellers(){return <BestsellersPanel/>}
function Studio(){return <StudioPanel/>}
function AutomationCreator({onClose}:{onClose:()=>void}){
  const navigate=useNavigate()
  const [selected,setSelected]=useState("自动上架")
  const automation=[
    {name:"自动上架",icon:"↑",status:"已支持",kind:"ready",summary:"将校验完成的商品草稿提交到队列，并持续追踪执行状态。",path:"选择店铺凭证 → 创建草稿 → submit_task → 轮询 task_status",support:"Worker 已提供草稿创建、提交上架、取消/重试和任务状态接口；创建前需选择凭证并满足商品必填字段。",action:"前往采集箱",to:"/collection"}
  ]
  const item=automation.find(x=>x.name===selected) ?? automation[0]
  return <div className="automation-overlay" role="presentation" aria-label="创建自动化"><section className="automation-drawer"><header><div><span className="panel-kicker">WORKER CAPABILITY CHECK</span><h2>创建自动化</h2><p>只展示当前 worker 已支持或可安全组合的执行路径。</p></div><button className="drawer-close" onClick={onClose} aria-label="关闭">×</button></header><div className="automation-body"><aside className="automation-types">{automation.map(type=><button key={type.name} onClick={()=>setSelected(type.name)} className={selected===type.name?"selected":""}><span className="automation-icon">{type.icon}</span><span><b>{type.name}</b><small className={type.kind}>{type.status}</small></span></button>)}</aside><article className="automation-detail"><div className={`capability-badge ${item.kind}`}>{item.status}</div><h3>{item.name}</h3><p className="automation-summary">{item.summary}</p><div className="execution-route"><span>执行方式</span><b>{item.path}</b></div><div className="worker-note"><span>▣</span><p>{item.support}</p></div></article></div><footer><button className="button ghost" onClick={onClose}>稍后配置</button><button className="button primary" onClick={()=>{onClose();navigate(item.to)}}>{item.action} →</button></footer></section></div>
}
function Settings(){return <SettingsPanel/>}
function Login(){
  const navigate=useNavigate()
  const [mode,setMode]=useState("api")
  const [show,setShow]=useState(false)
  const [apiKey,setApiKey]=useState("")
  const [username,setUsername]=useState("")
  const [password,setPassword]=useState("")
  const [error,setError]=useState("")
  const [loading,setLoading]=useState(false)
  const [balance,setBalance]=useState<number|null>(null)
  const [balanceLoaded,setBalanceLoaded]=useState(false)
  const submit=async()=>{
    setError(""); setLoading(true)
    try{
      let token=""
      if(mode==="api"){
        const verified=await api.verify(apiKey.trim())
        if(!verified.valid) throw new Error(verified.reason||"Token 无效")
        token=apiKey.trim()
        saveSession({token:apiKey.trim(),role:"user"})
      }else{
        const result=await api.login(username.trim(),password)
        if(!result.key) throw new Error("登录成功但未返回可用 API Key")
        token=result.key
        saveSession({token:result.key,role:result.role==="admin"?"admin":"user",username:result.username})
      }
      try{
        const b=await api.get<{balance:number|null;currency:string}>(`/mxou/balance`)
        setBalance(b.balance)
      }catch{setBalance(null)}
      setBalanceLoaded(true)
      navigate("/")
    }catch(e){setError(e instanceof Error?e.message:"登录失败，请稍后重试")}finally{setLoading(false)}
  }
  return <div className="login-page"><section className="login-hero"><div className="ozon-ai-mark">⌂</div><b>OzonAI</b><h1>Ozon AI自动化运营 ERP</h1><p>智能运营 · 高效管理 · 数据驱动增长</p><small>© 2024 OzonAI ERP: All rights reserved.</small></section><section className="login-panel"><div className="login-tabs"><button className={mode==="api"?"selected":""} onClick={()=>setMode("api")}>API Key登录</button><button className={mode==="account"?"selected":""} onClick={()=>setMode("account")}>账号密码登录</button></div>{mode==="api"?<div className="login-form"><label>API Key</label><input value={apiKey} onChange={e=>setApiKey(e.target.value)} placeholder="请输入 MXOU API Key"/><p className="login-help">验证后将以普通成员权限进入；管理员角色请使用账号密码登录。</p><button disabled={!apiKey.trim()||loading} onClick={submit} className="button primary login-button">{loading?"验证中…":"登录"}</button></div>:<div className="login-form"><label>账号</label><input value={username} onChange={e=>setUsername(e.target.value)} placeholder="请输入账号"/><label>密码</label><div className="password-field"><input value={password} onChange={e=>setPassword(e.target.value)} type={show?"text":"password"} placeholder="请输入密码"/><button onClick={()=>setShow(!show)}>{show?"◉":"◌"}</button></div><button disabled={!username.trim()||!password||loading} onClick={submit} className="button primary login-button">{loading?"登录中…":"登录"}</button></div>}{error&&<p className="login-error">ⓘ {error}</p>}<div className="balance-card"><span>▣</span><div><small>账户余额</small><b>{balanceLoaded ? (balance!=null ? `¥ ${balance.toLocaleString()}` : "查询失败(—)") : "登录后读取"}</b></div><span>受保护会话 ›</span></div></section></div>}

function DataScreen(){return <DataScreenPanel/>}
function TasksRoute(){ const [creatorOpen,setCreatorOpen]=useState(false); return <><TasksPanel onCreateAutomation={()=>setCreatorOpen(true)}/>{creatorOpen&&<AutomationCreator onClose={()=>setCreatorOpen(false)}/>}</> }
function NotFound(){return <><PageHeader kicker="404" title="页面未找到" description="你访问的地址不存在。" action="返回工作台"/></>}
const router = createBrowserRouter([{path:"/",Component:Shell,children:[{index:true,Component:Dashboard},{path:"data",Component:DataScreen},{path:"products",Component:ProductsPanel},{path:"orders",Component:OrdersPanel},{path:"collection",Component:CollectionPanel},{path:"pricing",Component:PricingPanel},{path:"studio",Component:Studio},{path:"tasks",Component:TasksRoute},{path:"bestsellers",Component:Bestsellers},{path:"stores",Component:StoresPanel},{path:"templates",Component:TemplatesPanel},{path:"settings",Component:Settings},{path:"admin",Component:AdminPanel},{path:"keys",Component:KeysPanel},{path:"discovery",Component:DiscoveryPanel},{path:"site",Component:SitePanel},{path:"*",Component:NotFound}]},{path:"/login",Component:Login}],{basename:"/app"})
export default function App(){return <RouterProvider router={router}/>}
