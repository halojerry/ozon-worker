/**
 * pounding-sidebar client half —— Pounding 电商客户端侧边栏（8 板块设计界面）。
 *
 * 板块：Agent（dsh 原生对话）+ 7 业务板块（本插件注册）：
 *   采集箱 / 任务中心 / 专家 / 知识库 / 爆品新闻 / 计算器 / 用量
 *
 * 设计语言：design-deliverables/design-tokens.json（暖白底 #F7F6F2 / 黑 #111 / 红 #E20E0E）。
 *
 * 接入契约（dsh-better-sidebar v0.4.0+，实测 0.13.1）：
 * - `import type {} from 'dsh-better-sidebar'` 触发 Context 类型合并（编译期擦除）
 * - `inject = ['betterSidebar']` 服务就绪后激活
 * - 注册必须包在 `ctx.effect(...)` 里（fiber 卸载自动撤销，HMR 安全）
 * - 内置 tab id 不可占用，我们用 `pounding:*` 前缀
 * - 数据源归属（PRD §2.3）：worker REST / skill 本地能力 / 纯脚本 / agent 驱动
 */
import { createElement, useState } from 'react'
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

  // 文件预览器：vault 产出的 csv（选品/采集导出）在侧栏直接预览
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

/* ================================================================== */
/* 设计 token（design-tokens.json 落地）                               */
/* ================================================================== */

const T = {
  bg: '#F7F6F2', // color.primitive.base
  surface: '#FFFFFF',
  headerBg: '#FAF9F6',
  ink: '#111111', // ink-900
  ink700: '#2A2A2A',
  ink600: '#4A4A4A',
  ink500: '#6F6F6F',
  ink400: '#8A8A8A',
  aux: '#6A6A6A', // text.aux（AA 达标）
  ink300: '#B4B0A9',
  line: '#E6E4DF',
  whiteBorder: '#D9D6D0',
  neutralBg: '#F1EFEA',
  accent: '#E20E0E',
  accentDark: '#B30C0C',
  accentSoft: '#FDEBEB',
}

const FX = {
  rub: 12.4688, // ￥1 ≈ ₽
  usd: 0.1481, // ￥1 ≈ $
}

const MONO = 'ui-monospace,SFMono-Regular,Menlo,Consolas,monospace'
const SANS = '-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif'

const SHELL: React.CSSProperties = { height: '100%', display: 'flex', flexDirection: 'column', background: T.bg, color: T.ink, fontSize: 13, fontFamily: SANS, overflow: 'hidden' }
const BODY: React.CSSProperties = { flex: 1, overflowY: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }
const CARD: React.CSSProperties = { background: T.surface, border: `1px solid ${T.line}`, borderRadius: 8, padding: 10, display: 'flex', flexDirection: 'column', gap: 8 }
const MUTED: React.CSSProperties = { color: T.aux, fontSize: 12 }
const LABEL: React.CSSProperties = { fontSize: 11, color: T.aux, fontWeight: 600, letterSpacing: '0.06em' }
const FIELD: React.CSSProperties = { background: T.surface, border: `1px solid ${T.whiteBorder}`, borderRadius: 6, padding: '6px 8px', fontSize: 13, color: T.ink, outline: 'none', width: '100%', boxSizing: 'border-box', fontFamily: SANS }
const ROW: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 8 }
const MONONUM: React.CSSProperties = { fontFamily: MONO, fontWeight: 700 }

/* ================================================================== */
/* 通用小件                                                             */
/* ================================================================== */

function Icon(size: number, d: string): ReactNode {
  return createElement('svg', {
    width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
    children: createElement('path', { d }),
  })
}

function Head(props: { icon?: ReactNode; title: string; right?: ReactNode }): ReactNode {
  return createElement('div', { style: { padding: '11px 12px', borderBottom: `1px solid ${T.line}`, background: T.headerBg, display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 } },
    props.icon && createElement('span', { style: { color: T.accent, display: 'inline-flex' } }, props.icon),
    createElement('span', { style: { fontWeight: 700, fontSize: 13.5 } }, props.title),
    props.right ? createElement('span', { style: { marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 } }, props.right) : null,
  )
}

function Section(props: { text: string; right?: ReactNode }): ReactNode {
  return createElement('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 2 } },
    createElement('span', { style: LABEL }, props.text),
    props.right ? createElement('span', { style: MUTED }, props.right) : null,
  )
}

function Btn(props: { label: string; kind?: 'primary' | 'accent' | 'outline' | 'ghost'; on?: boolean; onClick?: () => void; style?: React.CSSProperties }): ReactNode {
  const base: React.CSSProperties = { border: 'none', borderRadius: 6, padding: '6px 10px', fontSize: 12.5, fontWeight: 600, cursor: 'pointer', fontFamily: SANS, display: 'inline-flex', alignItems: 'center', gap: 6 }
  const kinds: Record<string, React.CSSProperties> = {
    primary: { background: T.ink, color: '#fff' },
    accent: { background: T.accent, color: '#fff' },
    outline: { background: T.surface, color: T.ink, border: `1px solid ${T.whiteBorder}` },
    ghost: { background: 'transparent', color: T.ink600 },
  }
  const on: React.CSSProperties = { background: T.accentSoft, color: T.accentDark, border: `1px solid ${T.accent}` }
  return createElement('button', { style: { ...base, ...(props.on ? on : kinds[props.kind ?? 'outline']), ...props.style }, onClick: props.onClick }, props.label)
}

function Pill(props: { text: string; tone?: 'neutral' | 'accent' | 'ink'; dot?: boolean }): ReactNode {
  const tones: Record<string, React.CSSProperties> = {
    neutral: { background: T.neutralBg, color: T.ink600 },
    accent: { background: T.accentSoft, color: T.accentDark },
    ink: { background: T.ink, color: '#fff' },
  }
  return createElement('span', { style: { ...tones[props.tone ?? 'neutral'], fontSize: 10.5, fontWeight: 600, padding: '2px 6px', borderRadius: 10, display: 'inline-flex', alignItems: 'center', gap: 4 } },
    props.dot ? createElement('span', { style: { width: 4, height: 4, borderRadius: 2, background: 'currentColor', display: 'inline-block' } }) : null,
    props.text,
  )
}

function Num(props: { children: string; accent?: boolean; size?: number }): ReactNode {
  return createElement('span', { style: { ...MONONUM, fontSize: props.size ?? 13, color: props.accent ? T.accent : T.ink } }, props.children)
}

function Stat(props: { label: string; value: string; accent?: boolean }): ReactNode {
  return createElement('div', { style: { flex: 1, background: T.surface, border: `1px solid ${T.line}`, borderRadius: 8, padding: '8px 10px' } },
    createElement('div', { style: { fontSize: 11, color: T.aux } }, props.label),
    createElement('div', { style: { fontSize: 17, ...MONONUM, color: props.accent ? T.accent : T.ink, marginTop: 2 } }, props.value),
  )
}

function Bar(props: { pct: number; accent?: boolean }): ReactNode {
  return createElement('div', { style: { height: 3, background: T.neutralBg, borderRadius: 2, overflow: 'hidden' } },
    createElement('div', { style: { height: '100%', width: `${Math.min(100, Math.max(0, props.pct))}%`, background: props.accent ? T.accent : T.ink } }),
  )
}

function ImgBlock(props: { text: string; hue?: 'warm' | 'neutral' | 'accent' }): ReactNode {
  const hues: Record<string, React.CSSProperties> = {
    warm: { background: '#F3EFE6', color: T.ink600 },
    neutral: { background: T.neutralBg, color: T.ink400 },
    accent: { background: T.accentSoft, color: T.accentDark },
  }
  return createElement('div', { style: { width: 52, height: 52, borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 700, flexShrink: 0, ...hues[props.hue ?? 'neutral'] } }, props.text)
}

/* ================================================================== */
/* 采集箱 —— worker /api/v1/drafts 商品卡片（图片+采购价+运费+利润）      */
/* ================================================================== */

function CollectPanel(props: { scope: { sessionId: string } }): ReactNode {
  const [f, setF] = useState<'all' | 'sourcing' | 'image'>('all')
  const items = [
    { img: '收纳', title: '抽取式桌面收纳盒 3 层', cost: '33.5', ship: '14.04', profit: '39', price: '₽69', tag: '1688', hue: 'warm' },
    { img: '保温', title: '便携保温杯 500ml 316 钢', cost: '28.0', ship: '12.60', profit: '35', price: '₽58', tag: '1688', hue: 'warm' },
    { img: '围巾', title: '秋冬针织围巾 莫兰迪色', cost: '19.8', ship: '9.30', profit: '41', price: '₽42', tag: '图搜', hue: 'accent' },
    { img: '猫窝', title: '猫抓板猫窝 可拆洗', cost: '45.0', ship: '16.80', profit: '28', price: '₽89', tag: '1688', hue: 'warm' },
  ]
  const list = items.filter((i) => f === 'all' || (f === 'image' ? i.tag === '图搜' : i.tag === '1688'))
  return createElement('div', { style: SHELL },
    Head({ icon: Icon(14, 'M3 12h3l2-5 3 10 2-5h3'), title: '采集箱', right: createElement(Pill, { text: '今日 +12', tone: 'accent' }) }),
    createElement('div', { style: BODY },
      createElement('div', { style: ROW },
        createElement(Stat, { label: '今日采集', value: '12' }),
        createElement(Stat, { label: '待上架', value: '5', accent: true }),
      ),
      createElement('input', { style: FIELD, placeholder: '搜索商品 / 1688 链接…' }),
      createElement('div', { style: ROW },
        Btn({ label: '全部', on: f === 'all', style: { padding: '3px 8px', fontSize: 11.5 } }),
        Btn({ label: '1688', on: f === 'sourcing', style: { padding: '3px 8px', fontSize: 11.5 } }),
        Btn({ label: '图搜', on: f === 'image', style: { padding: '3px 8px', fontSize: 11.5 } }),
        createElement('span', { style: { marginLeft: 'auto' } }, Btn({ label: '+ 采集', kind: 'primary', style: { padding: '3px 8px', fontSize: 11.5 } })),
      ),
      ...list.map((i) =>
        createElement('div', { key: i.title, style: { ...CARD, flexDirection: 'row', alignItems: 'center' } },
          ImgBlock({ text: i.img, hue: (i.tag === '图搜' ? 'accent' : 'warm') as 'warm' | 'accent' }),
          createElement('div', { style: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 } },
            createElement('div', { style: { fontSize: 12.5, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' } }, i.title),
            createElement('div', { style: ROW, flexWrap: 'wrap' as const },
              createElement(Pill, { text: i.tag, tone: i.tag === '图搜' ? 'accent' : 'neutral' }),
              createElement('span', { style: MUTED }, `采购 ¥${i.cost} · 运费 ¥${i.ship}`),
            ),
          ),
          createElement('div', { style: { display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3 } },
            createElement(Num, { children: `+${i.profit}%`, accent: true }),
            createElement('span', { style: { ...MUTED, fontSize: 11.5 } }, `售价 ${i.price}`),
          ),
        ),
      ),
      createElement('div', { style: { marginTop: 'auto', display: 'flex', gap: 8 } },
        Btn({ label: '批量导出 Excel', kind: 'outline', style: { flex: 1, justifyContent: 'center' } }),
        Btn({ label: '转上架', kind: 'accent', style: { flex: 1, justifyContent: 'center' } }),
      ),
      createElement('div', { style: { fontSize: 11, color: T.ink300, textAlign: 'center' } }, `session ${props.scope.sessionId.slice(0, 8)} · 数据源 worker /api/v1/drafts`),
    ),
  )
}

/* ================================================================== */
/* 任务中心 —— worker /task_status：采集任务 + 上架任务                   */
/* ================================================================== */

function TasksPanel(props: { scope: { sessionId: string } }): ReactNode {
  const [tab, setTab] = useState<'collect' | 'listing'>('collect')
  const tasks = tab === 'collect'
    ? [
        { t: '采集「收纳盒」关键词 · 第 2 页', s: '运行中', pct: 66, tone: 'accent' as const, time: '刚刚' },
        { t: '图搜「莫兰迪围巾」同款', s: '排队中', pct: 0, tone: 'neutral' as const, time: '2 分钟前' },
        { t: '批量采集 1688 货源 12 条', s: '成功', pct: 100, tone: 'neutral' as const, time: '1 小时前' },
      ]
    : [
        { t: '上架「收纳盒」到 主店铺', s: '运行中', pct: 40, tone: 'accent' as const, time: '刚刚' },
        { t: '批量上架 5 个 SKU · 定价审核中', s: '等待审批', pct: 0, tone: 'accent' as const, time: '3 分钟前' },
        { t: '上架「猫窝」到 测试店铺5381204', s: '成功', pct: 100, tone: 'neutral' as const, time: '2 小时前' },
      ]
  return createElement('div', { style: SHELL },
    Head({ icon: Icon(14, 'M4 4h16v16H4z M8 9h8 M8 13h8 M8 17h5'), title: '任务中心', right: createElement(Pill, { text: '1 进行中', tone: 'accent' }) }),
    createElement('div', { style: BODY },
      createElement('div', { style: ROW },
        Btn({ label: `采集任务 ${tab === 'collect' ? '' : '3'}`, on: tab === 'collect', style: { flex: 1, justifyContent: 'center' } }),
        Btn({ label: `上架任务 ${tab === 'listing' ? '' : '3'}`, on: tab === 'listing', style: { flex: 1, justifyContent: 'center' } }),
      ),
      ...tasks.map((i) =>
        createElement('div', { key: i.t, style: CARD },
          createElement('div', { style: ROW },
            createElement('span', { style: { flex: 1, fontSize: 12.5, fontWeight: 600 } }, i.t),
            createElement(Pill, { text: i.s, tone: i.tone, dot: i.s === '运行中' }),
          ),
          createElement('div', { style: ROW, justifyContent: 'space-between' } as React.CSSProperties,
            createElement(Bar, { pct: i.pct, accent: i.tone === 'accent' }),
            createElement('span', { style: { ...MUTED, fontSize: 11 } }, i.time),
          ),
        ),
      ),
      createElement('div', { style: { marginTop: 'auto' } },
        createElement('div', { style: { ...CARD, background: T.accentSoft, border: `1px solid ${T.accent}`, flexDirection: 'row', justifyContent: 'space-between' } },
          createElement('span', { style: { color: T.accentDark, fontSize: 12.5, fontWeight: 600 } }, '上架任务需老板审批'),
          createElement('span', { style: { color: T.accentDark, fontSize: 12 } }, '→'),
        ),
      ),
      createElement('div', { style: { fontSize: 11, color: T.ink300, textAlign: 'center' } }, `session ${props.scope.sessionId.slice(0, 8)} · 数据源 worker /task_status`),
    ),
  )
}

/* ================================================================== */
/* 专家 —— skill 能力手动入口（点卡片触发本地 pounding-mcp HTTP 网关）     */
/* ================================================================== */

function ExpertsPanel(props: { ctx: Context; scope: { sessionId: string } }): ReactNode {
  const tools = [
    { n: '采集', d: 'CDP 爬取 1688 货源', hue: 'warm' as const, p: 'probe / search', cmd: 'probe --url <1688链接>' },
    { n: '图搜', d: '以图找款 · 同款推荐', hue: 'accent' as const, p: 'image_search', cmd: 'image_search --image <图片URL>' },
    { n: '选品', d: '蓝海选品 discover', hue: 'warm' as const, p: 'discover / seller', cmd: 'discover --keyword "<品类>"' },
    { n: '上架', d: '组装 + 提交 worker', hue: 'neutral' as const, p: 'graph / follow', cmd: 'graph --url <1688链接>' },
    { n: '类目', d: '关键词 → Ozon 类目', hue: 'neutral' as const, p: 'category', cmd: 'category "<关键词>"' },
    { n: '店铺', d: '凭证 / AK 配置', hue: 'neutral' as const, p: 'set_store / check', cmd: 'check' },
  ]
  const [copied, setCopied] = useState('')
  const gotoAgent = () => {
    try {
      props.ctx.betterSidebar.openTab({ type: 'agent' })
    } catch {
      // 内置 agent tab 不可用时静默降级（提示文案已引导）
    }
  }
  const copyCmd = (cmd: string) => {
    try {
      void navigator.clipboard.writeText(`python3.12 scripts/cli.py ${cmd}`)
      setCopied(cmd)
      window.setTimeout(() => setCopied(''), 1600)
    } catch {
      /* 剪贴板不可用时静默 */
    }
  }
  return createElement('div', { style: SHELL },
    Head({ icon: Icon(14, 'M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6l7-3z'), title: '专家', right: createElement(Pill, { text: 'skill 本地能力', tone: 'neutral' }) }),
    createElement('div', { style: BODY },
      createElement('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 } },
        tools.map((x) =>
          createElement('button', { key: x.n, style: { ...CARD, cursor: 'pointer', textAlign: 'left', gap: 6 }, onClick: () => copyCmd(x.cmd), title: `点击复制: python3.12 scripts/cli.py ${x.cmd}` },
            createElement('div', { style: ROW },
              ImgBlock({ text: x.n.slice(0, 2), hue: x.hue }),
              createElement('div', { style: { flex: 1 } },
                createElement('div', { style: { fontWeight: 700, fontSize: 13 } }, x.n),
                createElement('div', { style: { ...MUTED, fontSize: 11 } }, x.d),
              ),
            ),
            createElement('div', { style: { ...MUTED, fontSize: 10.5, fontFamily: MONO } }, copied === x.cmd ? '✓ 已复制' : x.p),
          ),
        ),
      ),
      createElement('button', { style: { ...CARD, background: T.neutralBg, border: 'none', cursor: 'pointer', textAlign: 'left' }, onClick: gotoAgent },
        createElement('span', { style: { fontSize: 12, color: T.ink600 } }, '对话驱动：去 Agent 标签页，直接说'),
        createElement('span', { style: { fontSize: 11, color: T.aux, fontFamily: MONO } }, '“帮我把这个 1688 链接采集下来”'),
      ),
      createElement('div', { style: { fontSize: 11, color: T.ink300, textAlign: 'center', marginTop: 'auto' } }, '点卡片复制命令 · 本地 pounding-mcp HTTP 网关（8901）'),
    ),
  )
}

/* ================================================================== */
/* 知识库 —— vault 落盘（better-sidebar /sidebar/api/fs.* 读）          */
/* ================================================================== */

function VaultPanel(props: { scope: { sessionId: string } }): ReactNode {
  const dirs = [
    { n: '00-System', c: '3', sub: '索引 / Boot / Active-Context' },
    { n: '01-Stores', c: '1', sub: '5 店铺 · 脱敏' },
    { n: '02-Sourcing', c: '4', sub: '1688 货源采集' },
    { n: '03-Selection', c: '6', sub: '选品结果' },
    { n: '04-Listing', c: '2', sub: '上架记录' },
    { n: '05-Ozon', c: '3', sub: '类目 + 定价' },
  ]
  return createElement('div', { style: SHELL },
    Head({ icon: Icon(14, 'M4 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5z M8 3v18'), title: '知识库', right: createElement(Pill, { text: 'vault', tone: 'ink' }) }),
    createElement('div', { style: BODY },
      createElement(Section, { text: '目录结构', right: `${props.scope.sessionId.slice(0, 8)}` }),
      ...dirs.map((d) =>
        createElement('div', { key: d.n, style: { ...CARD, flexDirection: 'row', padding: '8px 10px' } },
          createElement('span', { style: { fontFamily: MONO, fontSize: 12, fontWeight: 600, flex: 1 } }, d.n),
          createElement('span', { style: { ...MUTED, fontSize: 11, flex: 1.4 } }, d.sub),
          createElement(Pill, { text: d.c, tone: 'neutral' }),
        ),
      ),
      createElement(Section, { text: '最近知识卡' }),
      createElement('div', { style: CARD },
        createElement('div', { style: { fontWeight: 600, fontSize: 12.5 } }, 'Ozon 家居类目佣金基准'),
        createElement('div', { style: ROW }, createElement(Pill, { text: '类目', tone: 'neutral' }), createElement(Pill, { text: '定价', tone: 'neutral' }), createElement('span', { style: { ...MUTED, fontSize: 11, marginLeft: 'auto' } }, '今天')),
      ),
      createElement('div', { style: CARD },
        createElement('div', { style: { fontWeight: 600, fontSize: 12.5 } }, '1688 采集反爬注意（验证码人工过）'),
        createElement('div', { style: ROW }, createElement(Pill, { text: '采集', tone: 'neutral' }), createElement(Pill, { text: '避坑', tone: 'accent' }), createElement('span', { style: { ...MUTED, fontSize: 11, marginLeft: 'auto' } }, '昨天')),
      ),
      createElement('div', { style: { marginTop: 'auto' } }, Btn({ label: '打开 vault 工作区', kind: 'outline', style: { width: '100%', justifyContent: 'center' } })),
    ),
  )
}

/* ================================================================== */
/* 爆品新闻 —— 热销/热搜/汇率/政策（skill queries/bestsellers + 外部源）  */
/* ================================================================== */

function BuzzPanel(_props: { scope: { sessionId: string } }): ReactNode {
  const hot = [
    { n: '桌面收纳', up: '↑32%', rank: 1 },
    { n: '保温杯', up: '↑21%', rank: 2 },
    { n: '针织围巾', up: '↑17%', rank: 3 },
  ]
  const words = ['收纳盒', '保温杯', '围巾', '猫窝', '手机支架', '加湿器']
  return createElement('div', { style: SHELL },
    Head({ icon: Icon(14, 'M13 2L4 14h6l-1 8 9-12h-6l1-8z'), title: '爆品新闻', right: createElement(Pill, { text: 'Ozon 俄罗斯', tone: 'ink' }) }),
    createElement('div', { style: BODY },
      createElement('div', { style: { ...CARD, background: T.ink, border: 'none', gap: 6 } },
        createElement('span', { style: { fontSize: 11, color: '#9A9A9A' } }, '汇率实时'),
        createElement('div', { style: ROW },
          createElement(Num, { children: '￥1', size: 15 }), createElement('span', { style: { color: '#9A9A9A' } }), '≈',
          createElement(Num, { children: `₽${FX.rub}`, size: 15, accent: true }),
          createElement('span', { style: { color: '#9A9A9A' } }), '·',
          createElement(Num, { children: `$${FX.usd}`, size: 15 }),
        ),
      ),
      createElement(Section, { text: '热销榜 TOP', right: '更新于 10 分钟前' }),
      ...hot.map((h) =>
        createElement('div', { key: h.n, style: { ...CARD, flexDirection: 'row', padding: '8px 10px' } },
          createElement('span', { style: { ...MONONUM, fontSize: 14, color: h.rank === 1 ? T.accent : T.ink, width: 18 } }, h.rank),
          createElement('span', { style: { flex: 1, fontWeight: 600, fontSize: 12.5 } }, h.n),
          createElement('span', { style: { ...MONONUM, fontSize: 12, color: T.accentDark } }, h.up),
        ),
      ),
      createElement(Section, { text: '热搜词' }),
      createElement('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 6 } },
        words.map((w) => createElement(Pill, { key: w, text: `# ${w}`, tone: 'neutral' })),
      ),
      createElement(Section, { text: '平台公告' }),
      createElement('div', { style: { ...CARD, background: T.accentSoft, border: `1px solid ${T.accent}` } },
        createElement('div', { style: { fontWeight: 600, fontSize: 12.5, color: T.accentDark } }, 'Ozon：2026 Q3 佣金费率调整（家居-1pp）'),
        createElement('div', { style: { fontSize: 11.5, color: T.accentDark } }, '生效 9/1 · 建议同步更新定价模板'),
      ),
    ),
  )
}

/* ================================================================== */
/* 计算器 —— OZON 跨境定价器（纯脚本直算，公式与 worker compute_price 一致）*/
/* ================================================================== */

const CATEGORIES: Record<string, { rate: number; label: string }> = {
  '家居收纳': { rate: 0.12, label: '家居收纳' },
  '服饰配件': { rate: 0.15, label: '服饰配件' },
  '数码电子': { rate: 0.08, label: '数码电子' },
  '美妆个护': { rate: 0.18, label: '美妆个护' },
  '母婴用品': { rate: 0.15, label: '母婴用品' },
  '宠物用品': { rate: 0.12, label: '宠物用品' },
}

const LOGISTICS: Record<string, number> = {
  '兔邮国际（跨境）': 12.5,
  'CDEK（跨境+尾程）': 15.8,
  '众禄（经济线）': 9.9,
}

interface CalcParams {
  purchase: number
  domestic: number
  otherRate: number
  margin: number
  commission: number
  logistics: number
  currency: 'CNY' | 'RUB'
  fxBuffer: number
  rate: number
  // v0.60 三档双价格（可选，对齐 worker compute_price 关键字参数；全部缺省 → 单档旧行为）：
  margin_anchor?: number // 划线原价利润率（pricing_node 传 2.0；缺省 = margin×1.2 保底）
  margin_floor?: number // 促销底线利润率（pricing_node 传 0.6）；缺省 → 不产生 promo_price
  variable_cost_rate?: number // 日常变动成本率（推广/退货/提现/汇损/附加），缺省 0.155（v0.60 默认）
  promo_variable_cost_rate?: number // 促销变动成本率，缺省 0.245（v0.60 默认）
}

/** 与 worker/src/utils/pricing_estimate.py#compute_price 逐字一致（含 Ozon ≥20% 划线规则 + v0.60 三档） */
function computePrice(p: CalcParams): {
  price: number
  oldPrice: number
  promoPrice: number | null
  profitCny: number
  profitRate: number
} {
  const totalCost = p.purchase + p.domestic + p.otherRate * p.purchase
  const legacy = p.margin_anchor === undefined && p.margin_floor === undefined
  const vcr = p.variable_cost_rate ?? 0.155
  const pvcr = p.promo_variable_cost_rate ?? 0.245

  // pricing_node Step 5：防止除零（分母 = 1 - 佣金 - 变动成本率；单档不含变动成本率）
  let commissionDivisor = 1 - p.commission - (legacy ? 0 : vcr)
  if (commissionDivisor <= 0) commissionDivisor = 0.9

  let base = (totalCost * (1 + p.margin)) / commissionDivisor
  if (p.currency === 'RUB') base *= (1 + p.fxBuffer) * p.rate
  const price = Math.ceil(base)

  if (legacy) {
    // ── 单档（旧行为，逐字保持）──
    // Ozon 规则：折扣至少 20%（price≤25 时 old_price-price≥5；否则 20% 加价）
    const oldPrice = price <= 25 ? Math.max(price + 5, Math.ceil(price * 1.2)) : Math.ceil(price * 1.2)
    const profitCny = p.currency === 'CNY' ? price - totalCost : price / p.rate - totalCost
    const profitRate = totalCost > 0 ? profitCny / totalCost : 0
    return { price, oldPrice, promoPrice: null, profitCny, profitRate }
  }

  // ── 三档（v0.60 双价格体系）──
  // 划线原价：用日常变动成本率（与日常价同分母），anchor 缺省 = margin×1.2 保底
  const anchorEff = p.margin_anchor ?? p.margin * 1.2
  let oldBase = (totalCost * (1 + anchorEff)) / commissionDivisor
  if (p.currency === 'RUB') oldBase *= (1 + p.fxBuffer) * p.rate
  // Ozon 规则：划线价 ≥ 日常价×1.2（anchor 偏低时强制）
  const oldPrice = Math.max(Math.ceil(oldBase), price <= 25 ? price + 5 : Math.ceil(price * 1.2))

  // 促销底线价：用促销变动成本率（大促推广/退货更高）
  let promoDivisor = 1 - p.commission - pvcr
  if (promoDivisor <= 0) promoDivisor = 0.9
  let promoPrice: number | null = null
  if (p.margin_floor !== undefined) {
    let promoBase = (totalCost * (1 + p.margin_floor)) / promoDivisor
    if (p.currency === 'RUB') promoBase *= (1 + p.fxBuffer) * p.rate
    promoPrice = Math.ceil(promoBase)
  }

  // 销售净利率口径：净利 = 售价×(1-佣金-变动成本率) - 总成本（不再是成本利润率）
  const profitCny = p.currency === 'CNY'
    ? price * (1 - p.commission - vcr) - totalCost
    : (price / p.rate) * (1 - p.commission - vcr) - totalCost
  const profitRate = price > 0 ? profitCny / price : 0

  return { price, oldPrice, promoPrice, profitCny, profitRate }
}

function Field(props: { label: string; value: string | number; onChange: (v: string) => void; unit?: string; type?: string }): ReactNode {
  return createElement('div', { style: { display: 'flex', flexDirection: 'column', gap: 4 } },
    createElement('span', { style: MUTED }, props.label),
    createElement('div', { style: { display: 'flex', alignItems: 'center', gap: 6 } },
      createElement('input', {
        style: FIELD, type: props.type ?? 'number', value: props.value,
        onChange: (e: { target: { value: string } }) => props.onChange(e.target.value),
      }),
      props.unit ? createElement('span', { style: { fontSize: 12, color: T.aux, width: 18 } }, props.unit) : null,
    ),
  )
}

function Select(props: { label: string; value: string; options: string[]; onChange: (v: string) => void }): ReactNode {
  return createElement('div', { style: { display: 'flex', flexDirection: 'column', gap: 4 } },
    createElement('span', { style: MUTED }, props.label),
    createElement('select', {
      style: FIELD, value: props.value,
      onChange: (e: { target: { value: string } }) => props.onChange(e.target.value),
      children: props.options.map((o) => createElement('option', { key: o, value: o }, o)),
    }),
  )
}

function CalculatorPanel(_props: { scope: { sessionId: string } }): ReactNode {
  const [cat, setCat] = useState('家居收纳')
  const [purchase, setPurchase] = useState('33.5')
  const [weight, setWeight] = useState('250')
  const [margin, setMargin] = useState('40')
  const [discount, setDiscount] = useState('20')
  const [logi, setLogi] = useState('兔邮国际（跨境）')
  const [domestic, setDomestic] = useState('0')
  const [ad, setAd] = useState('0')
  const [other, setOther] = useState('2')
  const [res, setRes] = useState<ReturnType<typeof computePrice> | null>(null)

  const calc = () => {
    const r = computePrice({
      purchase: parseFloat(purchase) || 0,
      domestic: parseFloat(domestic) || 0,
      otherRate: (parseFloat(other) || 0) / 100,
      margin: (parseFloat(margin) || 0) / 100,
      commission: CATEGORIES[cat].rate,
      logistics: LOGISTICS[logi] ?? 0,
      currency: 'RUB',
      fxBuffer: 0.05,
      rate: FX.rub,
    })
    setRes(r)
  }

  return createElement('div', { style: SHELL },
    Head({ icon: Icon(14, 'M7 3h10a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z M8 7h8 M8 11h2 M8 15h2 M12 11h3 M12 15h3'), title: '计算器', right: createElement(Pill, { text: '跨境定价', tone: 'accent' }) }),
    createElement('div', { style: BODY },
      createElement(Section, { text: '基本参数' }),
      Select({ label: '商品类目', value: cat, options: Object.keys(CATEGORIES), onChange: setCat }),
      Field({ label: '采购成本', value: purchase, onChange: setPurchase, unit: '￥' }),
      Field({ label: '包裹重量', value: weight, onChange: setWeight, unit: 'g' }),
      createElement('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 } },
        Field({ label: '期望毛利', value: margin, onChange: setMargin, unit: '%' }),
        Field({ label: '前台折扣', value: discount, onChange: setDiscount, unit: '%' }),
      ),
      Select({ label: '跨境物流商', value: logi, options: Object.keys(LOGISTICS), onChange: setLogi }),
      createElement(Section, { text: '其他参数' }),
      createElement('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 } },
        Field({ label: '国内运费+代贴单', value: domestic, onChange: setDomestic, unit: '￥' }),
        Field({ label: '广告费比例', value: ad, onChange: setAd, unit: '%' }),
      ),
      Field({ label: '其他（提现、货损等）', value: other, onChange: setOther, unit: '%' }),
      createElement(Btn, { label: '计算定价', kind: 'accent', style: { width: '100%', justifyContent: 'center', padding: '9px' }, onClick: calc }),
      ...(res
        ? [
            createElement('div', { key: 'r1', style: { ...CARD, background: T.ink, border: 'none', gap: 6 } },
              createElement('span', { style: { fontSize: 11, color: '#9A9A9A' } }, '计算结果 · 建议售价'),
              createElement('div', { style: ROW, gap: 14 },
                createElement('div', null,
                  createElement('div', { style: { fontSize: 11, color: '#9A9A9A' } }, '售价'),
                  createElement('div', { style: { fontSize: 20, ...MONONUM, color: '#fff' } }, `₽${res.price}`),
                ),
                createElement('div', null,
                  createElement('div', { style: { fontSize: 11, color: '#9A9A9A' } }, '划线价'),
                  createElement('div', { style: { fontSize: 16, ...MONONUM, color: '#E8E8E8', textDecoration: 'line-through' } }, `₽${res.oldPrice}`),
                ),
                createElement('div', { style: { marginLeft: 'auto', textAlign: 'right' } },
                  createElement('div', { style: { fontSize: 11, color: '#9A9A9A' } }, '利润率'),
                  createElement('div', { style: { fontSize: 16, ...MONONUM, color: T.accent } }, `${(res.profitRate * 100).toFixed(0)}%`),
                ),
              ),
              createElement('div', { style: { fontSize: 11, color: '#9A9A9A', borderTop: '1px solid #333', paddingTop: 6 } },
                `￥${(res.price / FX.rub).toFixed(1)} ≈ ₽${res.price} · 利润 ￥${res.profitCny.toFixed(2)} · 佣金 ${(CATEGORIES[cat].rate * 100).toFixed(0)}% · 汇率 ￥1≈₽${FX.rub}`,
              ),
            ),
            createElement('div', { key: 'r2', style: CARD },
              createElement(Section, { text: '计算明细' }),
              createElement('div', { style: { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 } },
                createElement('div', { style: ROW, justifyContent: 'space-between' } , createElement('span', { style: MUTED }, '商品成本（采购+运费+其他）'), createElement('span', { ...MONONUM }, `￥${((parseFloat(purchase) || 0) + (parseFloat(domestic) || 0) + ((parseFloat(other) || 0) / 100) * (parseFloat(purchase) || 0)).toFixed(2)}`)),
                createElement('div', { style: ROW, justifyContent: 'space-between' } , createElement('span', { style: MUTED }, '跨境运费 + 尾程'), createElement('span', { ...MONONUM }, `￥${LOGISTICS[logi]}`)),
                createElement('div', { style: ROW, justifyContent: 'space-between' } , createElement('span', { style: MUTED }, '平台佣金'), createElement('span', { ...MONONUM }, `${(CATEGORIES[cat].rate * 100).toFixed(0)}%`)),
              ),
            ),
          ]
        : [createElement('div', { key: 'h', style: { fontSize: 11, color: T.ink300, textAlign: 'center' } }, '填写参数后点击「计算定价」')]),
      createElement('div', { style: { fontSize: 11, color: T.ink300, textAlign: 'center' } }, '公式与 worker compute_price 一致 · RUB 店铺含 5% 汇率缓冲'),
    ),
  )
}

/* ================================================================== */
/* 用量 —— dsh token-meter + worker 配额 + 远程通道                      */
/* ================================================================== */

function UsagePanel(_props: { scope: { sessionId: string } }): ReactNode {
  return createElement('div', { style: SHELL },
    Head({ icon: Icon(14, 'M12 20V10 M12 10V4 M5 20h14'), title: '用量', right: createElement(Pill, { text: '本月 68%', tone: 'accent' }) }),
    createElement('div', { style: BODY },
      createElement('div', { style: { ...CARD, gap: 6 } },
        createElement('div', { style: ROW, justifyContent: 'space-between' } as React.CSSProperties,
          createElement('span', { style: { fontSize: 12.5, fontWeight: 600 } }, '本月模型用量'),
          createElement(Num, { children: '68%', accent: true }),
        ),
        createElement(Bar, { pct: 68, accent: true }),
        createElement('div', { style: { ...MUTED, fontSize: 11 } }, '已用 1.36M tokens / 2M 额度'),
      ),
      createElement('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 } },
        createElement(Stat, { label: 'Worker API 配额', value: '82%' }),
        createElement(Stat, { label: '图片生成', value: '45/月' }),
        createElement(Stat, { label: 'AI 上架成功率', value: '96%', accent: true }),
        createElement(Stat, { label: '余额', value: '¥88.6' }),
      ),
      createElement(Section, { text: '服务状态' }),
      createElement('div', { style: { ...CARD, flexDirection: 'row', justifyContent: 'space-between' } },
        createElement('span', { style: { fontSize: 12.5 } }, '远程通道（手机扫码）'),
        createElement(Pill, { text: '在线', tone: 'accent', dot: true }),
      ),
      createElement('div', { style: { ...CARD, flexDirection: 'row', justifyContent: 'space-between' } },
        createElement('span', { style: { fontSize: 12.5 } }, '本地 skill 采集服务'),
        createElement(Pill, { text: '运行中', tone: 'neutral', dot: true }),
      ),
      createElement('div', { style: { ...CARD, flexDirection: 'row', justifyContent: 'space-between' } },
        createElement('span', { style: { fontSize: 12.5 } }, '订阅到期'),
        createElement('span', { style: { ...MUTED, fontFamily: MONO } }, '2026-09-30'),
      ),
      createElement('div', { style: { marginTop: 'auto' } }, Btn({ label: '管理订阅 / 充值', kind: 'outline', style: { width: '100%', justifyContent: 'center' } })),
    ),
  )
}

/* ================================================================== */
/* 文件预览器：CSV                                                      */
/* ================================================================== */

function CsvViewer(props: { path: string; content?: string; truncated?: boolean }): ReactNode {
  const rows = (props.content ?? '').split('\n').filter(Boolean).slice(0, 50)
  const cells = (line: string) => line.split(',').map((c) => c.trim())
  return createElement('div', { style: { height: '100%', overflow: 'auto', background: T.bg, color: T.ink, fontSize: 12, fontFamily: MONO } },
    createElement('div', { style: { padding: 8, borderBottom: `1px solid ${T.line}`, display: 'flex', gap: 8, alignItems: 'center' } },
      createElement('span', { style: { color: T.accent, fontWeight: 600 } }, 'CSV'),
      createElement('span', { style: MUTED }, props.path),
      props.truncated ? createElement('span', { style: MUTED }, '（已截断）') : null,
    ),
    createElement('table', { style: { borderCollapse: 'collapse', width: '100%' } },
      createElement('tbody', null,
        rows.map((line, i) => createElement('tr', { key: i },
          cells(line).map((c, j) => createElement('td', {
            key: j,
            style: { borderBottom: `1px solid ${T.line}`, padding: '4px 8px', whiteSpace: 'nowrap', color: i === 0 ? T.ink : T.aux, fontWeight: i === 0 ? 700 : 400 },
          }, c)),
        )),
      ),
    ),
  )
}
