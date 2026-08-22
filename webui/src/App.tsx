import { useEffect, useMemo, useState } from "react"
import worldMap from "./assets/world-map.svg"
import { ApiError, api, getSession, saveSession } from "./api/client"
import { PageHeader, Metric } from "./components/ui"
import StoresPanel from "./components/StoresPanel"
import CollectionPanel from "./components/CollectionPanel"
import TasksPanel from "./components/TasksPanel"
import ProductsPanel from "./components/ProductsPanel"
import ProductEditor from "./components/ProductEditor"
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




function DataTable({ rows = [] }: { rows?: string[][] }) {
  const [query, setQuery] = useState("")
  const display = useMemo(() => rows.filter(row => row.join(" ").toLowerCase().includes(query.toLowerCase())), [rows, query])
  return <article className="panel orders-panel"><div className="panel-head"><div><span className="panel-kicker">LATEST RECORDS</span><h2>最新记录</h2></div><div className="orders-tools"><label><span>⌕</span><input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索" /></label><button className="text-button">全部记录 →</button></div></div><div className="table-wrap"><table><thead><tr><th>编号</th><th>客户 / 商品信息</th><th>金额</th><th>状态</th></tr></thead><tbody>{display.map(row => <tr key={row[0]}><td className="order-no">{row[0]}</td><td><b>{row[1]}</b><span>{row[2]}</span></td><td className="price">{row[3]}</td><td><span className={`status ${row[5]}`}>{row[4]}</span></td></tr>)}</tbody></table>{!display.length && <div className="empty-state">没有匹配的记录</div>}</div></article>
}

function Dashboard(){
  const [period,setPeriod]=useState("近7天")
  return <><PageHeader kicker="OVERVIEW" title="工作台" description="深圳跨境旗舰店的实时经营概览与待办事项。" action="＋ 创建自动化"/><section className="metric-grid"><Metric label="今日销售额" value="₽ 286,420" note="+18.4%" red/><Metric label="今日订单" value="128" note="+12.5%"/><Metric label="在售商品" value="1,284" note="+8.1%"/><Metric label="待处理任务" value="24" note="需要关注"/></section><section className="dashboard-grid"><article className="panel chart-panel dashboard-chart"><div className="panel-head"><h2>订单趋势 <small>ⓘ</small></h2><button className="button ghost">订单数量⌄</button></div><div className="plain-tabs">{["近7天","近30天","近90天"].map(item=><button key={item} className={period===item?"selected":""} onClick={()=>setPeriod(item)}>{item}</button>)}</div><Sparkline/><div className="axis"><span>05-27</span><span>05-28</span><span>05-29</span><span>05-30</span><span>05-31</span><span>06-01</span><span>06-02</span></div></article><article className="panel rank-panel"><div className="panel-head"><div><span className="panel-kicker">TOP SELLING</span><h2>热销商品</h2></div><button className="text-button">查看全部 →</button></div>{[["MagSafe 磁吸支架","436","78,340"],["不锈钢保温杯","289","52,610"],["蓝牙降噪耳机","214","48,920"]].map((item,index)=><div className="rank" key={item[0]}><b className={index===0?"first":""}>0{index+1}</b><div className={`product-thumb thumb-${index}`}/><div><strong>{item[0]}</strong><span>{item[1]} 件 · 今日</span></div><em>₽ {item[2]}</em></div>)}</article></section><section className="wide-section"><DataTable/></section></>}


function Listing(){const [selected,setSelected]=useState(0); return <><PageHeader kicker="AI LISTING WORKSPACE" title="上架工作台" description="AI 正在为 18 个商品补全俄语标题、卖点与搜索关键词。" action="＋ 导入商品"/><section className="listing-layout"><article className="panel task-board"><div className="panel-head"><div><span className="panel-kicker">PENDING QUEUE · 18</span><h2>待上架商品</h2></div><button className="text-button">筛选 ⌄</button></div>{["蓝牙运动耳机 S10","北欧风床头小夜灯","304 保温便携水杯","磁吸车载手机支架"].map((name,i)=><button className={`listing-row ${selected===i?"chosen":""}`} onClick={()=>setSelected(i)} key={name}><div className={`product-thumb thumb-${i%3}`}/><span><b>{name}</b><small>SKU-2406{i+1} · 未发布</small></span><em>{selected===i?"›":""}</em></button>)}</article><article className="panel ai-editor"><div className="panel-head"><div><span className="panel-kicker">AI COPY GENERATION</span><h2>蓝牙运动耳机 S10</h2></div><span className="status red">AI 已生成</span></div><div className="editor-field"><label>俄语商品标题</label><p>Беспроводные спортивные наушники S10 с шумоподавлением</p><button>✦ 重新生成</button></div><div className="editor-field"><label>核心卖点</label><p>• 24 小时长续航，满足全天运动需求<br/>• ENC 通话降噪，嘈杂环境清晰沟通<br/>• IPX5 防水，适合跑步与健身</p><button>✦ 优化文案</button></div><div className="keyword-row"><label>SEO 关键词</label>{["беспроводные наушники","спорт","bluetooth"].map(x=><span key={x}>{x} ×</span>)}</div><div className="editor-actions"><button className="button ghost">保存草稿</button><button className="button primary">确认并上架</button></div></article><article className="panel ai-sidebar"><span className="panel-kicker">AI INSIGHT</span><h2>发布建议</h2><div className="insight-score"><b>92</b><span>综合评分 / 100</span></div><ul><li><b>标题长度优秀</b><span>符合 Ozon 搜索建议</span></li><li><b>建议补充 3 张场景图</b><span>预计转化率提升 14%</span></li><li><b>竞品均价 ₽ 3,790</b><span>当前定价具备竞争力</span></li></ul><button className="create-task">查看完整建议</button></article></section></>}

function Pricing(){const pricingProducts=[["SKU-04201","MagSafe 磁吸手机支架"],["SKU-04192","不锈钢保温杯 500ml"],["SKU-04177","无线人体感应夜灯"],["SKU-04162","蓝牙降噪耳机 Pro"]];return <><PageHeader kicker="AI PRICING ENGINE" title="智能定价" description="基于竞品、佣金和实时汇率，让每一件商品保持合理利润。" action="＋ 创建策略"/><section className="pricing-layout"><article className="panel pricing-hero"><span className="panel-kicker">AI RECOMMENDATION</span><h2>本周建议调整 16 个商品</h2><p>预计在不降低转化率的前提下，提高毛利 ₽ 18,460。</p><div className="price-number"><span>预估额外收益</span><strong>₽ 18,460</strong><b>+8.4%</b></div><button className="button primary">应用全部建议</button></article><article className="panel"><div className="panel-head"><div><span className="panel-kicker">COMPETITOR INDEX</span><h2>竞品价格走势</h2></div><span className="status red">实时</span></div><Sparkline compact/><div className="legend"><span><i/> 我的价格</span><span><i/> 市场中位价</span></div></article></section><section className="wide-section"><DataTable rows={pricingProducts.map((p,i)=>[p[0],p[1],"当前 ₽ "+["1,650","1,290","890","5,990"][i],"₽ "+["1,590","1,340","920","5,790"][i],i===0?"建议降价":"价格健康",i===0?"red":"dark"])}/></section></>}

function Studio(){return <StudioPanel/>}
function AutomationCreator({onClose}:{onClose:()=>void}){
  const navigate=useNavigate()
  const [selected,setSelected]=useState("产品翻新")
  const automation=[
    {name:"产品翻新",icon:"✦",status:"可编排",kind:"partial",summary:"针对已有商品，依次完成内容优化与图片更新，再提交商品更新任务。",path:"商品详情 → 改图重传 → 优化标题 → 优化标签 → 更新商品",support:"Worker 已支持商品详情读取、任务图片重生成、图片更新，以及草稿 AI 字段生成。在线商品的标题/标签更新将通过 update_mode 更新任务提交；需在联调时确认字段映射。",action:"前往商品管理",to:"/products"},
    {name:"自动选品",icon:"⌘",status:"Skill 驱动",kind:"skill",summary:"由选品 Skill 读取市场信息、应用类目与利润约束、给候选商品评分，再把结果交给采集箱。",path:"选择选品 Skill → 输入选品约束 → Skill 评分/筛选 → 归档候选 → 创建草稿",support:"现有 worker 可提供榜单读取和选品结果归档，但 OpenAPI 尚未暴露“执行选品 Skill”的任务入口。该自动化的执行按钮将在 Skill 路由和输入 schema 确认后启用。",action:"配置选品约束",to:"/bestsellers"},
    {name:"自动上架",icon:"↑",status:"已支持",kind:"ready",summary:"将校验完成的商品草稿提交到队列，并持续追踪执行状态。",path:"选择店铺凭证 → 创建草稿 → submit_task → 轮询 task_status",support:"Worker 已提供草稿创建、提交上架、取消/重试和任务状态接口；创建前需选择凭证并满足商品必填字段。",action:"前往采集箱",to:"/collection"}
  ]
  // Hot reload can retain a previously selected automation that no longer exists.
  const item=automation.find(x=>x.name===selected) ?? automation[0]
  return <div className="automation-overlay" role="dialog" aria-modal="true" aria-label="创建自动化"><section className="automation-drawer"><header><div><span className="panel-kicker">WORKER CAPABILITY CHECK</span><h2>创建自动化</h2><p>只展示当前 worker 已支持或可安全组合的执行路径。</p></div><button className="drawer-close" onClick={onClose} aria-label="关闭">×</button></header><div className="automation-body"><aside className="automation-types">{automation.map(type=><button key={type.name} onClick={()=>setSelected(type.name)} className={selected===type.name?"selected":""}><span className="automation-icon">{type.icon}</span><span><b>{type.name}</b><small className={type.kind}>{type.status}</small></span></button>)}</aside><article className="automation-detail"><div className={`capability-badge ${item.kind}`}>{item.status}</div><h3>{item.name}</h3><p className="automation-summary">{item.summary}</p><div className="execution-route"><span>执行方式</span><b>{item.path}</b></div><div className="worker-note"><span>▣</span><p>{item.support}</p></div><div className="automation-guard"><b>执行前检查</b><ul>{item.name==="自动上架"?<><li>已选择有效 Ozon 店铺凭证</li><li>草稿包含标题、图片、重量、尺寸、成本和货源链接</li><li>任务状态会通过 worker 队列轮询</li></>:item.name==="产品翻新"?<><li>先选择需要更新的在线商品</li><li>确认图片、标题和标签的优化范围</li><li>标题/标签字段映射联调完成后才允许批量提交</li></>:<><li>选择并配置可用的选品 Skill</li><li>输入类目、价格、利润、仓储和竞争度约束</li><li>Skill 的执行路由与输入 schema 确认前，仅可查看榜单和保存筛选条件</li></>}</ul></div></article></div><footer><span>接口依据：worker OpenAPI v0.56.6 / v0.57 增量</span><button className="button ghost" onClick={onClose}>稍后配置</button><button className="button primary" onClick={()=>{onClose();navigate(item.to)}}>{item.action} →</button></footer></section></div>}
function Bestsellers(){return <BestsellersPanel/>}
function TemplateEditor({tmpl,onClose}:{tmpl:{name:string;desc:string;meta:string};onClose:()=>void}){const [tab,setTab]=useState("基础设置");const [saved,setSaved]=useState(false);const [name,setName]=useState(tmpl.name);const TABS=["基础设置","定价规则","库存策略","内容策略","图片规则","发布设置"];return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}><section className="product-drawer listing-editor" role="dialog" aria-modal="true" aria-label="编辑上架模板" onMouseDown={e=>e.stopPropagation()}><header><div><span className="panel-kicker">TEMPLATE EDITOR</span><h2>编辑上架模板</h2></div><button onClick={onClose} aria-label="关闭">×</button></header><div className="drawer-product"><div><b>{name}</b><small>{tmpl.desc} · {tmpl.meta}</small></div><span className="status red">启用中</span></div><nav className="drawer-tabs">{TABS.map(x=><button key={x} onClick={()=>setTab(x)} className={tab===x?"selected":""}>{x}</button>)}</nav>{tab==="基础设置"&&<div className="drawer-form"><label>模板名称<input value={name} onChange={e=>setName(e.target.value)}/></label><label>模板描述<textarea defaultValue="适用于通用商品上架流程，兼顾利润与效率。"/></label><label>适用类目<button className="field-button">全部类目　⌄</button></label><label>适用店铺<button className="field-button">全部店铺　⌄</button></label></div>}{tab==="定价规则"&&<div className="drawer-form"><div className="editor-tip"><b>✦ 价格公式</b><span>售价 = 采购价 × 汇率 × 加价率 + 运费 + 佣金</span></div><label>加价率（倍率）<input type="number" defaultValue="1.42" step="0.01"/><small>建议范围 1.2 ~ 2.5</small></label><label>汇率缓冲<input type="number" defaultValue="3.5" step="0.1"/><small>% · 对冲汇率波动风险</small></label><label>平台佣金率<input type="number" defaultValue="15" step="0.5"/><small>% · 自动从 Ozon 规则读取</small></label><label>固定运费（₽）<input type="number" defaultValue="0"/><small>填 0 则按 Ozon 物流规则自动计算</small></label><div className="publish-row"><span>智能定价建议</span><button className="switch on" aria-label="已启用"><i/></button></div></div>}{tab==="库存策略"&&<div className="drawer-form"><label>库存缓冲比例<input type="number" defaultValue="15" step="1"/><small>% · 预留比例防止超卖</small></label><label>低库存预警值<input type="number" defaultValue="10"/><small>低于此数值时推送提醒</small></label><div className="publish-row"><span>超卖自动下架</span><button className="switch on" aria-label="已启用"><i/></button></div><div className="publish-row"><span>库存为 0 时暂停上架</span><button className="switch on" aria-label="已启用"><i/></button></div></div>}{tab==="内容策略"&&<div className="drawer-form"><label>标题模板<input defaultValue="{{品牌}} {{型号}} {{颜色}} — {{卖点关键词}}"/><small>{`可用变量：{{品牌}} {{型号}} {{颜色}} {{卖点关键词}} {{类目}}`}</small></label><div className="keyword-row"><label>插入变量</label>{["{{品牌}}","{{型号}}","{{颜色}}","{{卖点关键词}}","{{类目}}"].map(v=><span key={v}>{v}</span>)}</div><label>俄语翻译策略<button className="field-button">AI 直译后人工审核　⌄</button><small>可选：AI 直译 / 人工审核 / 跳过翻译</small></label><div className="publish-row"><span>AI 卖点自动生成</span><button className="switch on" aria-label="已启用"><i/></button></div><div className="publish-row"><span>属性 AI 补全</span><button className="switch on" aria-label="已启用"><i/></button></div><div className="publish-row"><span>SEO 关键词自动补全</span><button className="switch on" aria-label="已启用"><i/></button></div></div>}{tab==="图片规则"&&<div className="drawer-form"><div className="publish-row"><span>自动去背景</span><button className="switch on" aria-label="已启用"><i/></button></div><div className="publish-row"><span>生成白底主图</span><button className="switch on" aria-label="已启用"><i/></button></div><div className="publish-row"><span>裁剪为 Ozon 标准尺寸（1000×1000）</span><button className="switch on" aria-label="已启用"><i/></button></div><div className="publish-row"><span>AI 场景图生成</span><button className="switch" aria-label="未启用"><i/></button></div><label>主图数量要求（最少）<input type="number" defaultValue="3"/></label><label>图片最小分辨率<button className="field-button">800 × 800 px　⌄</button></label></div>}{tab==="发布设置"&&<div className="publish-workspace"><div className="publish-row"><span>发布方式</span><button className="field-button">AI 评分通过后自动发布　⌄</button></div><div className="publish-row"><span>AI 自动发布评分阈值</span><b>85 分 / 100</b><button>调整</button></div><div className="publish-row"><span>定时发布默认时间</span><b>每日 10:00</b><button>编辑</button></div><div className="publish-row"><span>发布失败自动重试</span><button className="switch on" aria-label="已启用"><i/></button></div><div className="publish-row"><span>上架后自动开启广告</span><button className="switch" aria-label="未启用"><i/></button></div></div>}<footer className="editor-footer"><span className="save-state">{saved?"✓ 模板已保存":"修改尚未保存"}</span><button className="button ghost" onClick={onClose}>关闭</button><button className="button ghost">另存为新模板</button><button className="button primary" onClick={()=>setSaved(true)}>保存模板</button></footer></section></div>}
function Templates(){const [editing,setEditing]=useState<{name:string;desc:string;meta:string}|null>(null);const list=[{name:"默认通用模板",desc:"适用 842 个商品",meta:"加价率 1.42 · 库存缓冲 15%"},{name:"数码配件高利润",desc:"适用 156 个商品",meta:"加价率 1.58 · 佣金率 18%"},{name:"家居生活快速铺货",desc:"适用 214 个商品",meta:"加价率 1.35 · 库存缓冲 20%"}];return <><PageHeader kicker="LISTING CONFIGURATION" title="上架模板" description="通过模板统一商品的利润、库存、物流与内容策略。" action="＋ 新建模板"/><section className="template-grid">{list.map((x,i)=><article className="panel template-card" key={x.name}><span className="template-index">0{i+1}</span><span className="status red">启用中</span><h2>{x.name}</h2><p>{x.desc}</p><div className="template-meta">{x.meta}</div><div className="template-actions"><button className="text-button" onClick={()=>setEditing(x)}>编辑模板 →</button><button className="text-button">复制</button></div></article>)}</section>{editing&&<TemplateEditor tmpl={editing} onClose={()=>setEditing(null)}/>}</>;}
function Settings(){
  const [tab,setTab]=useState("业务参数")
  const [saved,setSaved]=useState(false)
  const content={
    "业务参数":<><div className="setting-row"><div><b>默认汇率缓冲</b><span>应用于所有新的上架任务</span></div><input defaultValue="3.5%"/></div><div className="setting-row"><div><b>低库存预警值</b><span>当商品库存低于此数值时通知</span></div><input defaultValue="10"/></div><div className="setting-row"><div><b>自动上架审核</b><span>AI 评分高于 85 时自动提交</span></div><button className="switch on" aria-label="已启用"><i/></button></div></>,
    "通知设置":<><div className="setting-row"><div><b>订单状态提醒</b><span>待发货、取消和异常订单实时提醒</span></div><button className="switch on" aria-label="已启用"><i/></button></div><div className="setting-row"><div><b>任务失败通知</b><span>自动化任务失败时发送站内消息</span></div><button className="switch on" aria-label="已启用"><i/></button></div><div className="setting-row"><div><b>每日经营日报</b><span>每日 09:00 汇总昨日店铺经营数据</span></div><button className="switch" aria-label="未启用"><i/></button></div></>
  }
  return <><PageHeader kicker="SYSTEM PREFERENCES" title="系统设置" description="管理全局业务规则与通知偏好；店铺授权与凭证请在店铺管理中维护。" action="保存修改"/><section className="settings-layout"><aside className="panel setting-tabs">{["业务参数","通知设置"].map(x=><button onClick={()=>{setTab(x);setSaved(false)}} className={tab===x?"selected":""} key={x}>{x}</button>)}</aside><article className="panel setting-detail"><span className="panel-kicker">{tab.toUpperCase()}</span><h2>{tab}</h2>{content[tab as keyof typeof content]}<footer className="settings-save"><span>{saved?"✓ 修改已保存":"修改尚未保存"}</span><button className="button primary" onClick={()=>setSaved(true)}>保存{tab}</button></footer></article></section></>}

function Admin(){
  const isAdmin=getUserRole()==="管理员"
  const [tab,setTab]=useState("用户管理")
  const [categoryRows,setCategoryRows]=useState(["电子产品","家居生活","美妆个护","母婴用品"])
  const [sourceRows,setSourceRows]=useState([{name:"Ozon 市场数据",type:"官方 API",status:"在线",sync:"12:48"},{name:"中国仓库存",type:"ERP 库存",status:"在线",sync:"12:45"},{name:"Wildberries 趋势",type:"第三方采集",status:"待校验",sync:"11:30"}])
  const [overview,setOverview]=useState({user_count:2543,store_count:1287,task_today:3421,success_rate:96.7})
  const [users,setUsers]=useState<Array<{name:string;role:string;status:string;created:string}>>([])
  const [apiNotice,setApiNotice]=useState("正在读取管理员数据…")
  // ── 生图配置 state ──
  const [cfgList,setCfgList]=useState<string[]>([])
  const [cfgSelected,setCfgSelected]=useState<string|null>(null)
  const [cfgContent,setCfgContent]=useState("")
  const [cfgBackups,setCfgBackups]=useState<Array<{name:string;size:number;mtime:number}>>([])
  const [cfgSaving,setCfgSaving]=useState(false)
  const [cfgNotice,setCfgNotice]=useState("")
  const [cfgLoading,setCfgLoading]=useState(false)
  const [showBackups,setShowBackups]=useState(false)

  useEffect(()=>{
    if(!isAdmin)return
    let live=true
    Promise.all([api.get<Record<string,unknown>>("/admin/overview"),api.get<unknown>("/admin/users")]).then(([data,userData])=>{if(!live)return;setOverview({user_count:Number(data.user_count||0)||2543,store_count:Number(data.store_count||0)||1287,task_today:Number(data.task_today||0)||3421,success_rate:Number(data.success_rate||0)||96.7});const payload=userData as {items?:unknown[];users?:unknown[]};const list=Array.isArray(userData)?userData:(payload.items||payload.users||[]);setUsers(list.map((item,index)=>{const user=item as Record<string,unknown>;return {name:String(user.username||user.name||user.email||`成员 ${index+1}`),role:String(user.role||"普通成员"),status:String(user.status||"正常"),created:String(user.created_at||user.created||"—")}}));setApiNotice("管理员数据已同步")}).catch(error=>{if(live)setApiNotice(error instanceof ApiError&&error.status===401?"请以管理员身份登录后同步真实数据":"管理员 API 暂不可用，当前显示演示数据")})
    return()=>{live=false}
  },[isAdmin])

  useEffect(()=>{
    if(!isAdmin||tab!=="生图配置")return
    setCfgNotice("正在读取配置列表…")
    api.get<Array<{name:string}>>("/admin/config").then(list=>{
      const names=list.map(x=>x.name)
      setCfgList(names)
      setCfgNotice(`已加载 ${names.length} 个配置项`)
      if(names.length&&!cfgSelected)setCfgSelected(names[0])
    }).catch(()=>setCfgNotice("配置列表读取失败，请确认管理员权限"))
  },[tab,isAdmin])

  useEffect(()=>{
    if(!cfgSelected)return
    setCfgLoading(true);setCfgContent("");setShowBackups(false)
    Promise.all([
      api.get<unknown>(`/admin/config/${cfgSelected}`),
      api.get<Array<{name:string;size:number;mtime:number}>>(`/admin/config/${cfgSelected}/backups`)
    ]).then(([content,backups])=>{
      setCfgContent(JSON.stringify(content,null,2))
      setCfgBackups(backups)
    }).catch(()=>setCfgContent("// 配置读取失败"))
    .finally(()=>setCfgLoading(false))
  },[cfgSelected])

  const saveCfg=async()=>{
    if(!cfgSelected)return
    let parsed:unknown
    try{parsed=JSON.parse(cfgContent)}catch{setCfgNotice("⚠ JSON 格式错误，请修正后再保存");return}
    setCfgSaving(true);setCfgNotice("")
    try{
      await api.put(`/admin/config/${cfgSelected}`,parsed)
      setCfgNotice(`✓ "${cfgSelected}" 已保存`)
    }catch(e){setCfgNotice(e instanceof ApiError?`保存失败：${e.message}`:"保存失败")}
    finally{setCfgSaving(false)}
  }

  const rollbackCfg=async(backupName:string)=>{
    if(!cfgSelected)return
    try{
      await api.post(`/admin/config/${cfgSelected}/rollback`,{backup:backupName})
      setCfgNotice(`✓ 已回滚至备份 ${backupName}`)
      const content=await api.get<unknown>(`/admin/config/${cfgSelected}`)
      setCfgContent(JSON.stringify(content,null,2))
      setShowBackups(false)
    }catch(e){setCfgNotice(e instanceof ApiError?`回滚失败：${e.message}`:"回滚失败")}
  }

  if(!isAdmin)return <><PageHeader kicker="ACCESS CONTROL" title="无访问权限" description="管理员后台仅对管理员角色开放。" action="返回工作台"/></>
  const tabs=["用户管理","类目配置","数据源管理","选品模式配置","生图配置","权限与审计"]
  return <><PageHeader kicker="PLATFORM CONTROL · ADMIN ONLY" title="管理员后台" description="管理选品类目、外部数据源和成员权限；此区域仅管理员可见。" action={tab==="类目配置"?"＋ 新增类目":"＋ 新增成员"}/><section className="metric-grid"><Metric label="平台成员" value={overview.user_count.toLocaleString()} note={apiNotice} red/><Metric label="活跃店铺" value={overview.store_count.toLocaleString()} note="管理员概览" red/><Metric label="今日任务" value={overview.task_today.toLocaleString()} note={`成功率 ${overview.success_rate}%`} red/><Metric label="已配置类目" value={`${categoryRows.length+14}`} note="待后端配置契约"/></section><section className="wide-section"><article className="panel admin-panel"><div className="admin-tabs">{tabs.map(x=><button className={tab===x?"selected":""} onClick={()=>setTab(x)} key={x}>{x}</button>)}</div>{tab==="类目配置"&&<div className="admin-config"><div className="admin-section-head"><div><h2>选品广场分类</h2><p>等待 `admin/config` 配置契约确认后发布到真实数据源。</p></div><button className="button primary" onClick={()=>setCategoryRows(rows=>[...rows,`新建类目 ${rows.length+1}`])}>＋ 新增一级类目</button></div><div className="category-admin-list">{categoryRows.map((name,i)=><div key={name}><span className="drag">⠿</span><b>{name}</b><small>{["12 个二级类目","8 个二级类目","6 个二级类目","9 个二级类目","待配置"][i]||"待配置"}</small><span className="status green">草稿</span><button>编辑</button><button className="text-danger" onClick={()=>setCategoryRows(rows=>rows.filter(x=>x!==name))}>移除</button></div>)}</div></div>}{tab==="数据源管理"&&<div className="admin-config"><div className="admin-section-head"><div><h2>选品数据源</h2><p>数据源 CRUD 接口尚未开放；当前用于确认配置结构。</p></div><button className="button primary" onClick={()=>setSourceRows(rows=>[...rows,{name:"新数据源",type:"待配置",status:"草稿",sync:"—"}])}>＋ 接入数据源</button></div><div className="source-admin-table"><div className="source-admin-head"><span>数据源</span><span>接入方式</span><span>状态</span><span>最近同步</span><span>填充范围</span><span>操作</span></div>{sourceRows.map(row=><div key={row.name}><b>{row.name}</b><span>{row.type}</span><span className={`status ${row.status==="在线"?"green":"red"}`}>{row.status}</span><time>今日 {row.sync}</time><span>选品广场 · {row.name.includes("仓")?"中国储热销":"全类目"}</span><button>配置</button></div>)}</div></div>}{tab==="选品模式配置"&&<div className="admin-config"><div className="admin-section-head"><div><h2>选品模式全局配置</h2><p>控制各模式的启用状态与全局默认参数；用户可在选品广场中基于此进行个性化调整。</p></div></div><div className="selection-mode-admin">{[{name:"热销商品",icon:"♜",desc:"跟随 Ozon/WB 热销榜",params:[{label:"默认排名上限",val:"100"},{label:"默认月销量下限",val:"500 件"},{label:"默认最低利润率",val:"25%"}]},{name:"热销新品",icon:"✦",desc:"新上架 + 快速增长",params:[{label:"默认时间范围",val:"近 30 天"},{label:"默认增速下限",val:"50%/月"},{label:"默认最低评分",val:"4.0"}]},{name:"轻仓爆品",icon:"◉",desc:"小体积高利润快周转",params:[{label:"默认重量上限",val:"1 kg"},{label:"默认周转天数",val:"14 天"},{label:"默认最低利润率",val:"35%"}]},{name:"蓝海商品",icon:"⌁",desc:"低竞争高潜力品类",params:[{label:"默认竞争度上限",val:"30%"},{label:"默认搜索增速",val:"20%/月"},{label:"默认卖家数上限",val:"50 家"}]}].map((m,i)=><article className="panel mode-admin-card" key={m.name}><div className="mode-admin-head"><div><span className="mode-icon">{m.icon}</span><div><b>{m.name}</b><small>{m.desc}</small></div></div><button className={`switch ${i<3?"on":""}`} aria-label={i<3?"已启用":"未启用"}><i/></button></div><div className="mode-admin-params">{m.params.map(p=><div key={p.label}><span>{p.label}</span><b>{p.val}</b><button>编辑</button></div>)}</div><div className="mode-admin-foot"><label><input type="checkbox" defaultChecked={i<2}/> 允许普通成员自定义参数</label><label><input type="checkbox" defaultChecked/> 显示在选品广场</label></div></article>)}</div></div>}
{tab==="生图配置"&&<div className="admin-config cfg-editor-layout"><aside className="cfg-sidebar"><div className="cfg-sidebar-head"><b>配置文件列表</b><small>引擎级提示词与变量</small></div>{cfgList.length===0&&<div className="cfg-empty">{cfgNotice||"加载中…"}</div>}{cfgList.map(name=><button key={name} className={`cfg-item ${cfgSelected===name?"selected":""}`} onClick={()=>setCfgSelected(name)}><span className="cfg-icon">⚙</span><span>{name}</span></button>)}</aside><div className="cfg-main">{cfgNotice&&<div className={`inline-notice ${cfgNotice.startsWith("⚠")||cfgNotice.startsWith("保存失败")||cfgNotice.startsWith("回滚失败")?"error":""}`}>{cfgNotice}</div>}{cfgSelected?<><div className="cfg-toolbar"><div><b className="cfg-name">{cfgSelected}</b><small>{cfgBackups.length} 个历史备份</small></div><div className="cfg-toolbar-actions"><button className="button ghost" onClick={()=>setShowBackups(!showBackups)}>◷ 备份历史</button><button className="button primary" onClick={saveCfg} disabled={cfgSaving}>{cfgSaving?"保存中…":"保存配置"}</button></div></div>{showBackups&&<div className="cfg-backups"><div className="cfg-backups-head"><b>备份历史</b><button onClick={()=>setShowBackups(false)}>×</button></div>{cfgBackups.length===0&&<p className="cfg-empty">暂无备份</p>}{cfgBackups.map(bk=><div key={bk.name} className="cfg-backup-row"><div><b>{bk.name}</b><small>{(bk.size/1024).toFixed(1)} KB · {new Date(bk.mtime*1000).toLocaleString("zh-CN")}</small></div><button className="button ghost" onClick={()=>rollbackCfg(bk.name)}>回滚至此版本</button></div>)}</div>}<div className="cfg-editor-wrap">{cfgLoading?<div className="cfg-empty cfg-loading">◌ 加载配置内容…</div>:<textarea className="cfg-editor" value={cfgContent} onChange={e=>setCfgContent(e.target.value)} spellCheck={false} placeholder="配置 JSON 内容"/>}</div><div className="cfg-editor-tip"><span>⚠ 此处修改直接影响生图引擎的提示词和变量——保存前请仔细核对 JSON 格式，错误配置可能导致任务失败。</span><button className="text-button" onClick={()=>{try{setCfgContent(JSON.stringify(JSON.parse(cfgContent),null,2))}catch{setCfgNotice("⚠ JSON 解析失败，无法格式化")}}}>格式化 JSON</button></div></>:<div className="cfg-empty">← 从左侧选择一个配置文件开始编辑</div>}</div></div>}
{(tab==="用户管理"||tab==="权限与审计")&&<div className="admin-config"><div className="filter-bar compact"><label>⌕ <input placeholder={tab==="用户管理"?"搜索成员":"搜索操作记录"}/></label><button>角色筛选⌄</button><button>时间筛选⌄</button><button className="button primary">导出记录</button></div><div className="admin-table"><div><span>{tab==="用户管理"?"用户名":"操作人"}</span><span>{tab==="用户管理"?"角色":"操作内容"}</span><span>状态</span><span>最近时间</span><span>操作</span></div>{(users.length?users:[{name:"Kate Lin",role:"管理员",status:"演示数据",created:"—"},{name:"Mia Chen",role:"运营主管",status:"演示数据",created:"—"}]).map(user=><div key={user.name}><b>{user.name}</b><span>{tab==="用户管理"?user.role:"查看了选品榜单配置"}</span><span className="status green">{user.status}</span><time>{user.created}</time><span className="row-links">查看　编辑　停用</span></div>)}</div></div>}</article></section></>}

function Login(){
  const navigate=useNavigate()
  const [mode,setMode]=useState("api")
  const [show,setShow]=useState(false)
  const [apiKey,setApiKey]=useState("")
  const [username,setUsername]=useState("")
  const [password,setPassword]=useState("")
  const [error,setError]=useState("")
  const [loading,setLoading]=useState(false)
  const submit=async()=>{
    setError(""); setLoading(true)
    try{
      if(mode==="api"){
        const verified=await api.verify(apiKey.trim())
        if(!verified.valid) throw new Error(verified.reason||"Token 无效")
        saveSession({token:apiKey.trim(),role:"user"})
      }else{
        const result=await api.login(username.trim(),password)
        if(!result.key) throw new Error("登录成功但未返回可用 API Key")
        saveSession({token:result.key,role:result.role==="admin"?"admin":"user",username:result.username})
      }
      navigate("/")
    }catch(e){setError(e instanceof Error?e.message:"登录失败，请稍后重试")}finally{setLoading(false)}
  }
  return <div className="login-page"><section className="login-hero"><div className="ozon-ai-mark">⌂</div><b>OzonAI</b><h1>Ozon AI自动化运营 ERP</h1><p>智能运营 · 高效管理 · 数据驱动增长</p><small>© 2024 OzonAI ERP: All rights reserved.</small></section><section className="login-panel"><div className="login-tabs"><button className={mode==="api"?"selected":""} onClick={()=>setMode("api")}>API Key登录</button><button className={mode==="account"?"selected":""} onClick={()=>setMode("account")}>账号密码登录</button></div>{mode==="api"?<div className="login-form"><label>API Key</label><input value={apiKey} onChange={e=>setApiKey(e.target.value)} placeholder="请输入 MXOU API Key"/><p className="login-help">验证后将以普通成员权限进入；管理员角色请使用账号密码登录。</p><button disabled={!apiKey.trim()||loading} onClick={submit} className="button primary login-button">{loading?"验证中…":"登录"}</button></div>:<div className="login-form"><label>账号</label><input value={username} onChange={e=>setUsername(e.target.value)} placeholder="请输入账号"/><label>密码</label><div className="password-field"><input value={password} onChange={e=>setPassword(e.target.value)} type={show?"text":"password"} placeholder="请输入密码"/><button onClick={()=>setShow(!show)}>{show?"◉":"◌"}</button></div><button disabled={!username.trim()||!password||loading} onClick={submit} className="button primary login-button">{loading?"登录中…":"登录"}</button></div>}{error&&<p className="login-error">ⓘ {error}</p>}<div className="balance-card"><span>▣</span><div><small>账户余额</small><b>登录后读取</b></div><span>受保护会话 ›</span></div></section></div>}

function DataScreen(){return <DataScreenPanel/>}
function TasksRoute(){ const [creatorOpen,setCreatorOpen]=useState(false); return <><TasksPanel onCreateAutomation={()=>setCreatorOpen(true)}/>{creatorOpen&&<AutomationCreator onClose={()=>setCreatorOpen(false)}/>}</> }
function NotFound(){return <><PageHeader kicker="404" title="页面未找到" description="你访问的地址不存在。" action="返回工作台"/></>}
const router = createBrowserRouter([{path:"/",Component:Shell,children:[{index:true,Component:Dashboard},{path:"data",Component:DataScreen},{path:"products",Component:ProductsPanel},{path:"orders",Component:OrdersPanel},{path:"collection",Component:CollectionPanel},{path:"listing",Component:Listing},{path:"pricing",Component:PricingPanel},{path:"studio",Component:Studio},{path:"tasks",Component:TasksRoute},{path:"bestsellers",Component:Bestsellers},{path:"stores",Component:StoresPanel},{path:"templates",Component:TemplatesPanel},{path:"settings",Component:Settings},{path:"admin",Component:AdminPanel},{path:"keys",Component:KeysPanel},{path:"discovery",Component:DiscoveryPanel},{path:"site",Component:SitePanel},{path:"*",Component:NotFound}]},{path:"/login",Component:Login}],{basename:"/app"})
export default function App(){return <RouterProvider router={router}/>}
