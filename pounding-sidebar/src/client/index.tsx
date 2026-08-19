/**
 * pounding-sidebar client half —— 经 dsh-better-sidebar 服务注册电商业务板块。
 *
 * 侧边栏 8 板块中，Agent（对话）为 dsh 原生，这里注册其余 7 个业务板块：
 *   采集箱 / 任务中心 / 专家 / 知识库 / 爆品新闻 / 计算器 / 用量
 *
 * 契约要点（v0.4.0+，当前验证版本 0.13.1）：
 * - `import type {} from 'dsh-better-sidebar'` 触发 Context 类型合并（编译期擦除）；
 * - `inject = ['betterSidebar']` 保证服务就绪后才激活本插件；
 * - 注册必须包在 `ctx.effect(...)` 里，fiber 卸载时自动撤销（HMR 安全）；
 * - 服务只在 client half；host 半读 better-sidebar 数据走 /sidebar/api/*。
 * - 内置 tab id 不可重复（editor/explorer/git/subagent/terminal/browser/diff），
 *   我们统一用 `pounding:*` 前缀。
 *
 * 数据源约定（对 PRD §2.3 能力归属）：
 * - 采集箱/任务中心 → worker REST（/api/v1，本地网关注入 Bearer）
 * - 专家/计算器 → skill 能力 / 纯脚本（本地面板直接算）
 * - 知识库 → vault 落盘（经 better-sidebar /sidebar/api/fs.* 读）
 * - 爆品新闻/用量 → skill 能力 + 外部源 / dsh 原生 token-meter
 */
import { createElement } from 'react'
import type { ReactNode } from 'react'
import type {} from 'dsh-better-sidebar'
import type { Context } from 'cordis'

export const inject = ['betterSidebar']

export function apply(ctx: Context): void {
  const { betterSidebar } = ctx

  ctx.effect(() =>
    betterSidebar.registerTab({
      id: 'pounding:collect',
      title: () => '采集箱',
      icon: (size) => Icon(size, 'M3 12h3l2-5 3 10 2-5h3'),
      order: 10,
      single: true,
      component: (props) => createElement(CollectPanel, props),
    }),
  )

  ctx.effect(() =>
    betterSidebar.registerTab({
      id: 'pounding:tasks',
      title: () => '任务中心',
      icon: (size) => Icon(size, 'M4 4h16v16H4z M8 9h8 M8 13h8 M8 17h5'),
      order: 20,
      single: true,
      component: (props) => createElement(TasksPanel, props),
    }),
  )

  ctx.effect(() =>
    betterSidebar.registerTab({
      id: 'pounding:experts',
      title: () => '专家',
      icon: (size) => Icon(size, 'M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6l7-3z'),
      order: 30,
      single: true,
      component: (props) => createElement(ExpertsPanel, props),
    }),
  )

  ctx.effect(() =>
    betterSidebar.registerTab({
      id: 'pounding:vault',
      title: () => '知识库',
      icon: (size) => Icon(size, 'M4 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5z M8 3v18'),
      order: 40,
      single: true,
      component: (props) => createElement(VaultPanel, props),
    }),
  )

  ctx.effect(() =>
    betterSidebar.registerTab({
      id: 'pounding:buzz',
      title: () => '爆品新闻',
      icon: (size) => Icon(size, 'M13 2L4 14h6l-1 8 9-12h-6l1-8z'),
      order: 50,
      single: true,
      component: (props) => createElement(BuzzPanel, props),
    }),
  )

  ctx.effect(() =>
    betterSidebar.registerTab({
      id: 'pounding:calculator',
      title: () => '计算器',
      icon: (size) => Icon(size, 'M7 3h10a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z M8 7h8 M8 11h2 M8 15h2 M12 11h3 M12 15h3'),
      order: 60,
      single: true,
      component: (props) => createElement(CalculatorPanel, props),
    }),
  )

  ctx.effect(() =>
    betterSidebar.registerTab({
      id: 'pounding:usage',
      title: () => '用量',
      icon: (size) => Icon(size, 'M12 20V10 M12 10V4 M5 20h14'),
      order: 70,
      single: true,
      component: (props) => createElement(UsagePanel, props),
    }),
  )

  // 文件预览器：vault 产出的 csv（选品/采集导出）在侧栏直接预览。
  // 演示 registerFileViewer 机制；fetchStrategy 'fsRead' 走 better-sidebar
  // /sidebar/api/fs.read，component 收到 content 文本字段。
  ctx.effect(() =>
    betterSidebar.registerFileViewer({
      id: 'pounding:csv',
      title: () => 'CSV 表格',
      exts: ['csv'],
      fetchStrategy: 'fsRead',
      component: (props) => createElement(CsvViewer, props),
    }),
  )
}

/* ------------------------------------------------------------------ */
/* 品牌常量（对齐 design-deliverables/design-tokens.json）              */
/* ------------------------------------------------------------------ */

const B = {
  bg: '#F7F6F2', // 暖白底
  ink: '#111111', // 黑
  inkMuted: '#6A6A6A', // 辅助文字（AA 达标 5.0:1）
  line: '#E8E5DF',
  red: '#E20E0E', // 强调红
  redDark: '#B30C0C', // 深红（小字用）
  card: '#FFFFFF',
}

const ROW: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 8 }
const SHELL: React.CSSProperties = { height: '100%', display: 'flex', flexDirection: 'column', background: B.bg, color: B.ink, fontSize: 13, fontFamily: '-apple-system,"PingFang SC","Microsoft YaHei",sans-serif' }
const HEAD: React.CSSProperties = { padding: '10px 12px', borderBottom: `1px solid ${B.line}`, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 8 }
const BODY: React.CSSProperties = { flex: 1, overflow: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }
const CARD: React.CSSProperties = { background: B.card, border: `1px solid ${B.line}`, borderRadius: 8, padding: 10, display: 'flex', flexDirection: 'column', gap: 4 }
const MUTED: React.CSSProperties = { color: B.inkMuted, fontSize: 12 }
const HINT: React.CSSProperties = { color: B.inkMuted, fontSize: 11, border: `1px dashed ${B.line}`, borderRadius: 6, padding: '6px 8px', background: B.card }
const PILL: React.CSSProperties = { background: B.red, color: '#fff', fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 10 }

/* ------------------------------------------------------------------ */
/* 通用小件                                                             */
/* ------------------------------------------------------------------ */

function Icon(size: number, d: string): ReactNode {
  return createElement('svg', {
    width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
    children: createElement('path', { d }),
  })
}

function PanelShell(props: { icon?: ReactNode; title: string; children: ReactNode }): ReactNode {
  return createElement(
    'div', { style: SHELL },
    createElement('div', { style: HEAD },
      props.icon && createElement('span', { style: { color: B.red, display: 'inline-flex' } }, props.icon),
      createElement('span', null, props.title),
    ),
    createElement('div', { style: BODY }, props.children),
  )
}

function Placeholder(props: { note: string }): ReactNode {
  return createElement('div', { style: HINT }, `骨架占位 — ${props.note}`)
}

function PanelTitle(props: { text: string; muted?: string }): ReactNode {
  return createElement('div', { style: { fontSize: 12, color: B.inkMuted, fontWeight: 600 } },
    createElement('span', null, props.text),
    props.muted ? createElement('span', { style: { fontWeight: 400 } }, ` — ${props.muted}`) : null,
  )
}

/* ------------------------------------------------------------------ */
/* 7 个业务板块                                                          */
/* ------------------------------------------------------------------ */

function CollectPanel(props: { scope: { sessionId: string; cwd?: string } }): ReactNode {
  return PanelShell({
    icon: Icon(14, 'M3 12h3l2-5 3 10 2-5h3'),
    title: '采集箱',
    children: [
      createElement(PanelTitle, { text: '1688 货源采集结果', muted: `session ${props.scope.sessionId.slice(0, 8)}` }),
      createElement(Placeholder, {
        note: '商品卡片列表（图片 + 采购价 + 运费 + 利润率）→ worker /api/v1/drafts，本地网关注入 Bearer',
      }),
      createElement('div', { style: CARD },
        createElement('div', { style: ROW }, createElement('span', { style: { flex: 1 } }, '示例：抽取式收纳盒'),
          createElement('span', { style: PILL }, '利润 39%')),
        createElement('div', { style: MUTED }, '采购 ¥33.5 · 运费 ¥14.04 · 售价 ₽69'),
      ),
      createElement('div', { style: { marginTop: 'auto' } },
        createElement(Placeholder, { note: 'TODO：drafts 列表 / 转上架 / 生成 Excel' }),
      ),
    ],
  })
}

function TasksPanel(props: { scope: { sessionId: string } }): ReactNode {
  return PanelShell({
    icon: Icon(14, 'M4 4h16v16H4z M8 9h8 M8 13h8 M8 17h5'),
    title: '任务中心',
    children: [
      createElement(PanelTitle, { text: '采集任务 + 上架任务', muted: `session ${props.scope.sessionId.slice(0, 8)}` }),
      createElement(Placeholder, {
        note: '任务列表与进度 → worker /task_status、/tasks，agent 也可经 pounding-mcp 查询',
      }),
      createElement('div', { style: ROW },
        createElement('span', { style: { ...PILL, background: B.ink } }, '采集 3'),
        createElement('span', { style: { ...PILL, background: B.ink } }, '上架 2'),
        createElement('span', { style: PILL }, '进行中 1'),
      ),
      createElement('div', { style: { marginTop: 'auto' } },
        createElement(Placeholder, { note: 'TODO：任务状态机 + 进度条 + 失败重试' }),
      ),
    ],
  })
}

function ExpertsPanel(_props: { scope: { sessionId: string } }): ReactNode {
  const tools = ['采集（CDP 爬 1688）', '图搜（以图找款）', '选品（discover）', '上架组装（graph）', '类目查询', '店铺/凭证配置']
  return PanelShell({
    icon: Icon(14, 'M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6l7-3z'),
    title: '专家',
    children: [
      createElement(PanelTitle, { text: 'skill 手动能力入口', muted: '点卡片直接执行，不走对话' }),
      ...tools.map((t) => createElement('button', {
        key: t, style: { ...CARD, textAlign: 'left', cursor: 'pointer', color: B.ink, background: B.card },
      }, createElement('span', { style: ROW },
        createElement('span', { style: { flex: 1 } }, t),
        createElement('span', { style: { color: B.red } }, '→'),
      ))),
      createElement('div', { style: { marginTop: 'auto' } },
        createElement(Placeholder, { note: 'TODO：按钮 → 本地 pounding-mcp HTTP 网关（8901）触发 skill' }),
      ),
    ],
  })
}

function VaultPanel(props: { scope: { sessionId: string; cwd?: string } }): ReactNode {
  return PanelShell({
    icon: Icon(14, 'M4 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5z M8 3v18'),
    title: '知识库',
    children: [
      createElement(PanelTitle, { text: 'vault 工作区', muted: props.scope.cwd ?? '经 better-sidebar 文件面板读' }),
      createElement(Placeholder, {
        note: '店铺配置 / 选品规则 / 类目映射 / 踩坑记录 → /sidebar/api/fs.tree + fs.read',
      }),
      createElement('div', { style: ROW },
        createElement('span', { style: { ...PILL, background: B.ink } }, '01-Stores'),
        createElement('span', { style: { ...PILL, background: B.ink } }, '03-Products'),
      ),
      createElement('div', { style: { marginTop: 'auto' } },
        createElement(Placeholder, { note: 'TODO：vault 目录树 + markdown 预览（内置 viewer）' }),
      ),
    ],
  })
}

function BuzzPanel(_props: { scope: { sessionId: string } }): ReactNode {
  return PanelShell({
    icon: Icon(14, 'M13 2L4 14h6l-1 8 9-12h-6l1-8z'),
    title: '爆品新闻',
    children: [
      createElement(PanelTitle, { text: '电商行业情报', muted: '热销 / 热搜 / 类目 / 汇率 / 政策' }),
      createElement(Placeholder, {
        note: '热销榜 + 热搜词 → skill queries/bestsellers；汇率 → check；平台政策 → 外部源',
      }),
      createElement('div', { style: ROW },
        createElement('span', { style: { ...PILL, background: B.ink } }, 'Ozon 热搜 TOP10'),
        createElement('span', { style: { ...PILL, background: B.ink } }, '￥1≈₽12.47'),
      ),
      createElement('div', { style: { marginTop: 'auto' } },
        createElement(Placeholder, { note: 'TODO：榜单卡片 + 汇率实时 + 政策订阅' }),
      ),
    ],
  })
}

function CalculatorPanel(_props: { scope: { sessionId: string } }): ReactNode {
  // 跨境定价器（纯脚本直算，无需 agent）：worker compute_price 公式的前端形态
  const row = (label: string, value: string, unit = '') =>
    createElement('div', { style: { ...ROW, justifyContent: 'space-between', padding: '4px 0' } },
      createElement('span', { style: MUTED }, label),
      createElement('span', { style: { fontWeight: 600 } }, value + (unit ? ` ${unit}` : '')),
    )
  return PanelShell({
    icon: Icon(14, 'M7 3h10a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z M8 7h8 M8 11h2 M8 15h2 M12 11h3 M12 15h3'),
    title: '计算器',
    children: [
      createElement(PanelTitle, { text: 'OZON 跨境定价器', muted: '纯前端直算' }),
      row('商品类目', '家居收纳', ''),
      row('采购成本', '33.5', '￥'),
      row('包裹重量', '250', 'g'),
      row('期望毛利', '40', '%'),
      row('跨境物流商', '未选择', ''),
      createElement('div', { style: { borderTop: `1px solid ${B.line}`, margin: '4px 0' } }),
      row('建议售价', '₽69（利润率 39%）'),
      row('划线价', '₽89'),
      createElement('div', { style: { marginTop: 'auto' } },
        createElement(Placeholder, { note: 'TODO：完整表单（类目/成本/重量体积/毛利/折扣/物流商 → 明细）' }),
      ),
    ],
  })
}

function UsagePanel(_props: { scope: { sessionId: string } }): ReactNode {
  return PanelShell({
    icon: Icon(14, 'M12 20V10 M12 10V4 M5 20h14'),
    title: '用量',
    children: [
      createElement(PanelTitle, { text: '成本 / 额度监控' }),
      createElement(Placeholder, {
        note: '模型用量（dsh token-meter）+ worker 配额 / 余额 + 手机远程通道状态',
      }),
      createElement('div', { style: ROW },
        createElement('span', { style: { ...PILL, background: B.ink } }, '本月 68%'),
        createElement('span', { style: { ...PILL, background: B.ink } }, '远程：在线'),
      ),
      createElement('div', { style: { marginTop: 'auto' } },
        createElement(Placeholder, { note: 'TODO：用量仪表 + 余额 + 订阅到期提醒' }),
      ),
    ],
  })
}

/* ------------------------------------------------------------------ */
/* 文件预览器：CSV                                                      */
/* ------------------------------------------------------------------ */

function CsvViewer(props: { path: string; content?: string; truncated?: boolean }): ReactNode {
  const rows = (props.content ?? '').split('\n').filter(Boolean).slice(0, 50)
  const cells = (line: string) => line.split(',').map((c) => c.trim())
  return createElement('div', { style: { height: '100%', overflow: 'auto', background: B.bg, color: B.ink, fontSize: 12, fontFamily: 'ui-monospace,Menlo,monospace' } },
    createElement('div', { style: { padding: 8, borderBottom: `1px solid ${B.line}`, display: 'flex', gap: 8, alignItems: 'center' } },
      createElement('span', { style: { color: B.red, fontWeight: 600 } }, 'CSV'),
      createElement('span', { style: MUTED }, props.path),
      props.truncated ? createElement('span', { style: MUTED }, '（已截断）') : null,
    ),
    createElement('table', { style: { borderCollapse: 'collapse', width: '100%' } },
      createElement('tbody', null,
        rows.map((line, i) => createElement('tr', { key: i },
          cells(line).map((c, j) => createElement('td', {
            key: j,
            style: { borderBottom: `1px solid ${B.line}`, padding: '4px 8px', whiteSpace: 'nowrap', color: i === 0 ? B.ink : B.inkMuted, fontWeight: i === 0 ? 700 : 400 },
          }, c)),
        )),
      ),
    ),
  )
}
