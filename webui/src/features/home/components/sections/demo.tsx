/*
Copyright (C) 2023-2026 QuantumNous — GNU AGPL v3
*/
import { useState, useEffect, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { PoundingHeartLogo } from '../pounding-heart-logo'

type ViewId = 'guid' | 'claude' | 'ppt' | 'excel' | 'gemini' | 'paper' | 'scheduled' | 'team'

interface ChatItem { id: ViewId; icon: 'img' | 'svg'; img?: string; svg?: React.ReactNode; label: string }
interface TeamItem { label: string }

const AUTO_ROTATE_MS = 10000

export function Demo() {
  const { t } = useTranslation()
  const [view, setView] = useState<ViewId>('guid')
  const [autoIdx, setAutoIdx] = useState(0)
  const [step, setStep] = useState(0)
  const [taskDone, setTaskDone] = useState<Set<string>>(new Set())
  const timerRef = useRef<ReturnType<typeof setInterval>>(undefined)
  const stepTimerRef = useRef<ReturnType<typeof setInterval>>(undefined)

  const VIEW_ORDER: ViewId[] = ['guid', 'claude', 'ppt', 'excel', 'gemini', 'paper', 'scheduled', 'team']

  // Auto-rotate views
  useEffect(() => {
    timerRef.current = setInterval(() => {
      setAutoIdx(prev => (prev + 1) % VIEW_ORDER.length)
    }, AUTO_ROTATE_MS)
    return () => clearInterval(timerRef.current)
  }, [])

  // Sync autoIdx → view
  useEffect(() => {
    setView(VIEW_ORDER[autoIdx])
    setStep(0)
    setTaskDone(new Set())
  }, [autoIdx])

  // Click sidebar to stop auto-rotate temporarily
  const selectView = useCallback((v: ViewId) => {
    setView(v)
    setAutoIdx(VIEW_ORDER.indexOf(v))
    setStep(0)
    setTaskDone(new Set())
    // Reset auto-rotate timer
    clearInterval(timerRef.current)
    timerRef.current = setInterval(() => {
      setAutoIdx(prev => (prev + 1) % VIEW_ORDER.length)
    }, AUTO_ROTATE_MS)
  }, [])

  // Animate steps for detail views
  useEffect(() => {
    if (['guid', 'scheduled', 'team'].includes(view)) return
    stepTimerRef.current = setInterval(() => {
      setStep(s => s + 1)
    }, 900)
    return () => clearInterval(stepTimerRef.current)
  }, [view])

  const chatItems: ChatItem[] = [
    { id: 'ppt', icon: 'img', img: '/landing/ppt-creator.jpg', label: 'Q2 融资路演' },
    { id: 'claude', icon: 'svg', svg: <ClaudeSvg />, label: '重构 auth 模块' },
    { id: 'codex' as ViewId, icon: 'img', img: '/landing/cowork.jpg', label: '整理下载文件夹' },
    { id: 'excel', icon: 'img', img: '/landing/excel-creator.jpg', label: '周销售报表' },
    { id: 'gemini', icon: 'img', img: '/landing/ui-ux-pro-max.jpg', label: '产品图精修' },
    { id: 'paper', icon: 'img', img: '/landing/academic-paper.jpg', label: '拓扑绝缘体论文' },
  ]

  const teamItems: TeamItem[] = [
    { label: '官网改版' },
    { label: '移动 App 上线' },
    { label: 'Q3 营销规划' },
  ]

  const activeChatItem = chatItems.find(c => c.id === view)
  const hasPreview = ['ppt', 'excel', 'gemini', 'paper'].includes(view)
  const hasWorkspace = view === 'claude'

  return (
    <section className='demo-section reveal visible' id='demo' aria-label='产品演示'>
      <div className='demo-inner demo-inner-desktop' style={{ maxWidth: 1400, margin: '0 auto' }}>
        <div className='demo-frame'>
          {/* ── Titlebar ── */}
          <div className='demo-titlebar'>
            <div className='demo-tb-dots'>
              <span className='demo-dot demo-dot-r' />
              <span className='demo-dot demo-dot-y' />
              <span className='demo-dot demo-dot-g' />
            </div>
            <div className='demo-tb-left'>
              <div className='demo-tb-btn'><SidebarIcon /></div>
              <div className='demo-tb-btn'><ChevronLeft /></div>
              <div className='demo-tb-btn'><ChevronRight /></div>
            </div>
            <div className='demo-tb-center'>POUNDING — Playground</div>
            <div className='demo-tb-right' />
          </div>

          {/* ── Body ── */}
          <div className='demo-body'>
            {/* Sidebar */}
            <aside className='demo-sider'>
              <div className='demo-sider-header'>
                <span className='demo-sider-logo' style={{ background: 'transparent', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <PoundingHeartLogo size={28} compact />
                </span>
                <span className='demo-sider-brand'>POUNDING</span>
              </div>
              <div className='demo-sider-toolbar'>
                <button type='button' className='demo-sider-btn'>
                  <svg width='13' height='13' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2.5'><path d='M12 5v14'/><path d='M5 12h14'/></svg>
                  {t('New Session')}
                </button>
              </div>
              <div className='demo-sider-search'>
                <svg width='13' height='13' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2'><circle cx='11' cy='11' r='8'/><path d='m21 21-4.3-4.3'/></svg>
                {t('Search')}
              </div>
              <button
                type='button'
                className={`demo-sider-nav${view === 'scheduled' ? ' active' : ''}`}
                onClick={() => selectView('scheduled')}
              >
                <svg width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2'><circle cx='12' cy='12' r='10'/><polyline points='12 6 12 12 16 14'/></svg>
                {t('Scheduled Tasks')}
              </button>
              <div className='demo-sider-divider' />
              <div className='demo-sider-scroll'>
                <div className='demo-sider-group'>{t('Teams')}</div>
                {teamItems.map(tm => (
                  <button
                    key={tm.label}
                    type='button'
                    className={`demo-sider-item${view === 'team' && tm.label === '官网改版' ? ' active' : ''} static`}
                    onClick={() => tm.label === '官网改版' && selectView('team')}
                  >
                    <span className='demo-sider-item-icon'>
                      <svg width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='#7583b2' strokeWidth='2'><path d='M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2'/><circle cx='9' cy='7' r='4'/><path d='M23 21v-2a4 4 0 0 0-3-3.87'/><path d='M16 3.13a4 4 0 0 1 0 7.75'/></svg>
                    </span>
                    <span className='demo-sider-item-name'>{tm.label}</span>
                  </button>
                ))}
                <div className='demo-sider-group'>{t('Sessions')}</div>
                {chatItems.map(ch => (
                  <button
                    key={ch.id}
                    type='button'
                    className={`demo-sider-item${view === ch.id ? ' active' : ''}`}
                    onClick={() => selectView(ch.id)}
                  >
                    <span className='demo-sider-item-icon'>
                      {ch.icon === 'img' && ch.img
                        ? <img src={ch.img} alt='' style={{ width: 20, height: 20, borderRadius: '50%', objectFit: 'cover' }} />
                        : ch.svg}
                    </span>
                    <span className='demo-sider-item-name'>{ch.label}</span>
                  </button>
                ))}
              </div>
              <div className='demo-sider-footer'>
                <div className='demo-sider-footer-btn'>
                  <svg width='15' height='15' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2'><circle cx='12' cy='12' r='3'/><path d='M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z'/></svg>
                </div>
              </div>
            </aside>

            {/* Main content */}
            <main className='demo-main' style={{ display: 'flex', flexDirection: new Set(['claude', 'ppt', 'excel', 'gemini', 'paper']).has(view) ? 'row' : 'column' }}>
              {/* Left chat area */}
              <div className='demo-chat-left' style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden' }}>
                {activeChatItem && ['claude', 'ppt', 'excel', 'gemini', 'paper'].includes(view) && (
                  <div className='demo-chat-header'>
                    <div className='demo-chat-header-pill' style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                      {activeChatItem.icon === 'img' && activeChatItem.img
                        ? <img src={activeChatItem.img} alt='' style={{ width: 22, height: 22, borderRadius: '50%', border: '1.5px solid #fff', boxShadow: '0 1px 3px rgba(0,0,0,0.1)', objectFit: 'cover' }} />
                        : <span style={{ width: 22, height: 22, borderRadius: '50%', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: '#d97706' }}><span style={{ color: '#fff', fontSize: 9, fontWeight: 700 }}>C</span></span>}
                      {activeChatItem.label}
                    </div>
                    <span className='demo-chat-header-name' style={{ fontSize: 13, fontWeight: 600, color: '#1d2129', flex: 1, textAlign: 'center' }}>
                      {view === 'claude' ? '重构 auth 模块' : view === 'ppt' ? 'Q2 融资路演 PPT' : view === 'excel' ? '周销售报表' : view === 'gemini' ? '产品图精修' : '拓扑绝缘体论文'}
                    </span>
                    <span className='demo-chat-header-pill-model' style={{ fontSize: '10.5px', color: '#4e5969', padding: '3px 10px', borderRadius: 100, background: '#f2f3f5' }}>
                      {view === 'excel' ? 'Gemini 2.5' : view === 'gemini' ? 'GPT-4o' : view === 'paper' ? 'GPT-5.4' : 'Claude Sonnet 4'}
                    </span>
                  </div>
                )}

                {view === 'guid' && <GuidView />}
                {view === 'scheduled' && <ScheduledView />}
                {view === 'team' && <TeamView />}
                {view === 'claude' && <ClaudeView step={step} />}
                {view === 'ppt' && <PptView step={step} />}
                {view === 'excel' && <ExcelView step={step} />}
                {view === 'gemini' && <GeminiView step={step} />}
                {view === 'paper' && <PaperView step={step} />}

                {/* Send box for detail views */}
                {['claude', 'ppt', 'excel', 'gemini', 'paper'].includes(view) && (
                  <div className='demo-chat-sendbox' style={{ margin: '0 16px 8px' }}>
                    <div className='demo-chat-sendbox-input' style={{ fontSize: 12, color: '#a1a2aa' }}>
                      {view === 'claude' ? '给 Claude Code 发消息...' :
                       view === 'ppt' ? '给 Patrick · PPT 制作师 发消息...' :
                       view === 'excel' ? '给 Emily · Excel 数据师 发消息...' :
                       view === 'gemini' ? '给 Stella · UI/UX 设计师 发消息...' :
                       '给 Albert · 论文写作师 发消息...'}
                    </div>
                    <div className='demo-chat-sendbox-bar'>
                      <div className='demo-chat-sendbox-tools'>
                        <div className='demo-chat-plus'><svg width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2.5'><path d='M12 5v14'/><path d='M5 12h14'/></svg></div>
                      </div>
                      <div className='demo-send-btn'><svg width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='#fff' strokeWidth='3'><path d='M12 19V5'/><path d='m5 12 7-7 7 7'/></svg></div>
                    </div>
                  </div>
                )}
              </div>

              {/* Right panel: preview or workspace */}
              {hasPreview && <PreviewPanel view={view} step={step} />}
              {hasWorkspace && <WorkspacePanel />}
            </main>
          </div>
        </div>
      </div>

      {/* Mobile fallback */}
      <div className='demo-inner demo-inner-mobile' aria-hidden='true' style={{ maxWidth: 1400, margin: '0 auto' }}>
        <img src='/landing/mockup-mobile-fallback.webp' alt='New API Dashboard' style={{ width: '100%', height: 'auto', display: 'block' }} />
      </div>
    </section>
  )
}

/* ═══════════════════════ Sub-views ═══════════════════════ */

function GuidView() {
  return (
    <div className='demo-guid' style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '6% 28px', gap: 0, flex: 1, overflow: 'auto', background: '#f9fafb' }}>
      <div className='demo-guid-skills-toggle' style={{ position: 'absolute', top: 12, right: 16, display: 'flex', alignItems: 'center', gap: 6, fontSize: '10.5px', color: '#4e5969' }}>
        Skills Market <span className='demo-toggle on' />
      </div>
      <div className='demo-guid-greeting' style={{ fontSize: 22, fontWeight: 700, color: '#1d2129', textAlign: 'center', marginBottom: 14 }}>你好，今天打算做点什么？</div>
      <div className='demo-guid-agent-bar' style={{ display: 'flex', alignItems: 'center', gap: 4, background: '#eaecf7', borderRadius: 20, padding: '5px 10px', justifyContent: 'center', flexWrap: 'wrap', marginBottom: 12, maxWidth: 520 }}>
        <div className='demo-guid-pill active' style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '6px 12px', borderRadius: 100, background: '#fff', fontWeight: 600, boxShadow: '0 1px 3px rgba(0,0,0,0.08)', fontSize: 11 }}>
          <span style={{ width: 18, height: 18, borderRadius: '50%', background: '#10a37f', color: '#fff', fontSize: 9, fontWeight: 700, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>C</span>
          <span className='pill-name'>Claude Sonnet 4</span>
        </div>
        {['#d97706', '#4285f4', '#4b3c7a'].map((bg, i) => (
          <div key={i} className='demo-guid-pill' style={{ display: 'flex', alignItems: 'center', padding: 5, borderRadius: 100, cursor: 'pointer', opacity: 0.55 }}>
            <span style={{ width: 18, height: 18, borderRadius: '50%', background: bg, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }} />
          </div>
        ))}
        <span className='demo-guid-sep'>|</span>
        <span className='demo-guid-sep' style={{ fontSize: 14, color: '#86909c', marginLeft: 2 }}>+</span>
      </div>

      <div className='demo-guid-input' style={{ width: '100%', maxWidth: 560, border: '1px solid #e5e6eb', borderRadius: 18, background: '#fff', padding: '14px 16px 12px', display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div className='demo-guid-textarea' style={{ fontSize: 13, color: '#86909c', border: 'none', outline: 'none', resize: 'none', fontFamily: 'inherit', minHeight: 36 }}>
          发消息、上传文件，或创建定时任务...
        </div>
        <div className='demo-guid-toolbar' style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div className='demo-guid-tools' style={{ display: 'flex', gap: 4 }}>
            <div className='demo-guid-chip' style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px', borderRadius: 100, fontSize: '10.5px', color: '#86909c', background: '#f2f3f5' }}>
              <svg width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2'><path d='M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z'/></svg>
              在文件夹里工作
            </div>
          </div>
          <div className='demo-guid-send' style={{ width: 28, height: 28, borderRadius: '50%', background: 'var(--plum)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <svg width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='#fff' strokeWidth='3'><path d='m22 2-10 20-4-10Z'/><path d='M22 2 12 12'/></svg>
          </div>
        </div>
      </div>

      <div className='demo-guid-asst-section' style={{ width: '100%', maxWidth: 560, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, marginTop: 16 }}>
        <div className='demo-guid-asst-label' style={{ fontSize: 11, color: '#86909c' }}>选一个助手开始任务</div>
        <div className='demo-guid-asst-grid' style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 6, width: '100%' }}>
          {[
            { name: 'Patrick · PPT 制作师', desc: '要点转精致 PPT', img: '/landing/ppt-creator.jpg' },
            { name: 'Emily · Excel 数据师', desc: '透视、图表、数据清洗', img: '/landing/excel-creator.jpg' },
            { name: 'Warren · 财务建模师', desc: 'DCF、股权表、三表模型', img: '/landing/financial-model-creator.jpg' },
            { name: 'Albert · 论文写作师', desc: '提纲或完整初稿', img: '/landing/academic-paper.jpg' },
            { name: 'Stella · UI/UX 设计师', desc: '最佳实践驱动的设计', img: '/landing/ui-ux-pro-max.jpg' },
            { name: 'Marco · 动态 PPT 师', desc: '电影级 PPT 演示', img: '/landing/morph-ppt.jpg' },
          ].map(a => (
            <div key={a.name} className='demo-guid-asst-card' style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', borderRadius: 10, border: '1px solid #e5e6eb', background: '#fff' }}>
              <img src={a.img} alt='' style={{ width: 28, height: 28, borderRadius: '50%', objectFit: 'cover', flexShrink: 0 }} />
              <div className='demo-guid-asst-info' style={{ minWidth: 0, flex: 1 }}>
                <div className='demo-guid-asst-name' style={{ fontSize: 11, fontWeight: 600, color: '#1d2129', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.name}</div>
                <div className='demo-guid-asst-desc' style={{ fontSize: '9.5px', color: '#86909c' }}>{a.desc}</div>
              </div>
            </div>
          ))}
        </div>
        <div className='demo-guid-asst-more' style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4, padding: '8px 10px', borderRadius: 10, border: '1px dashed #c9cdd4', color: '#86909c', fontSize: 11, width: '100%' }}>
          +10 更多
        </div>
      </div>
    </div>
  )
}

/* ── View: Scheduled ── */
function ScheduledView() {
  const cards = [
    { name: '每日代码评审', when: '每天 9:00', next: '下次：2 小时后', tag: 'running' as const },
    { name: '周销售报表', when: '周一 8:00', next: '下次：3 天后', tag: 'running' as const },
    { name: '备份项目文件', when: '每天 23:00', next: '已暂停', tag: 'paused' as const },
    { name: '邮件摘要', when: '每天 7:30', next: '下次：明天 7:30', tag: 'running' as const },
    { name: '生成站会纪要', when: '工作日 8:45', next: '下次：14 小时后', tag: 'running' as const },
    { name: '监测竞品价格', when: '每周五 17:00', next: '已暂停', tag: 'paused' as const },
  ]

  return (
    <div className='demo-scheduled' style={{ display: 'flex', flexDirection: 'column', padding: '28px 80px', gap: 16, overflow: 'auto', flex: 1 }}>
      <div className='demo-scheduled-header' style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span className='demo-scheduled-title' style={{ fontSize: 16, fontWeight: 600, color: '#1d2129' }}>定时任务</span>
        <div className='demo-scheduled-add' style={{ width: 30, height: 30, borderRadius: 8, background: '#f2f3f5', border: '1px solid #e5e6eb', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <svg width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2'><path d='M12 5v14'/><path d='M5 12h14'/></svg>
        </div>
      </div>
      <p className='demo-scheduled-desc' style={{ fontSize: 12, color: '#86909c', lineHeight: 1.5 }}>把重复任务交给 cron。Agent 在你电脑上 24/7 运转。</p>
      <div className='demo-scheduled-grid' style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10 }}>
        {cards.map((c, i) => (
          <div key={i} className='demo-sched-card'>
            <div className='demo-sched-card-top'>
              <span className='demo-sched-card-name' style={{ fontSize: '12.5px', fontWeight: 600, color: '#1d2129' }}>{c.name}</span>
              <span className={`demo-sched-tag ${c.tag}`}>{c.tag === 'running' ? '运行中' : '已暂停'}</span>
            </div>
            <div className='demo-sched-row' style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: '#86909c' }}>
              <svg width='13' height='13' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2'><circle cx='12' cy='12' r='10'/><polyline points='12 6 12 12 16 14'/></svg>
              {c.when}
            </div>
            <div className='demo-sched-row' style={{ fontSize: 10 }}>{c.next}</div>
            <div className='demo-sched-bottom'>
              <span className={`demo-sched-toggle ${c.tag === 'paused' ? 'off' : 'on'}`} />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ── View: Team ── */
function TeamView() {
  const cols = [
    {
      name: 'PM', bg: '#7c3aed', role: 'Leader',
      userMsg: '重做首页。协调 Dev 和 QA。',
      msgs: ['明白。拆解一下：', '1. 新组件库', '2. Dev 负责实现', '3. QA 写 E2E 测试', '开始派活给 Dev 和 QA...'],
      status: '正在查看 QA 报告',
    },
    {
      name: 'Dev', bg: '#10a37f', role: 'Dev',
      userMsg: '实现 Hero、FeatureGrid 和 Footer。用新的 design tokens。',
      msgs: ['好的，先读一下 design tokens。', 'Hero.tsx——响应式布局完成。', 'FeatureGrid.tsx——三列网格进行中。'],
      crossMsgs: ['Hero 全部通过。等 FeatureGrid。'],
      status: '正在修复对比度',
    },
    {
      name: 'QA', bg: '#d97706', role: 'QA',
      userMsg: '给所有新页面写 E2E 测试。再跑一遍可访问性审计。',
      msgs: ['启动 E2E 测试套件。', '首页：4/4 通过', '定价页：5/5 通过', '正在跑可访问性审计...'],
      crossMsgs: ['FeatureGrid.tsx 已就绪，请跑测试。'],
      status: '正在测试 FeatureGrid',
    },
  ]

  return (
    <div className='demo-team' style={{ display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden' }}>
      <div className='demo-team-header' style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 18px', borderBottom: '1px solid #e5e6eb', background: '#fff' }}>
        <span className='demo-team-name' style={{ fontSize: 14, fontWeight: 600, color: '#1d2129' }}>官网改版</span>
        <span className='demo-team-badge' style={{ fontSize: 10, color: '#86909c', background: '#f2f3f5', padding: '3px 10px', borderRadius: 100 }}>3 个 Agent · 进行中</span>
        <div className='demo-team-avatars' style={{ display: 'flex', alignItems: 'center', marginLeft: 'auto' }}>
          {['#7c3aed', '#10a37f', '#d97706'].map((bg, i) => (
            <div key={i} className='tav' style={{ width: 22, height: 22, borderRadius: '50%', border: '2px solid #fff', marginLeft: i > 0 ? -6 : 0, background: bg, color: '#fff', fontSize: 9, fontWeight: 600, display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 3 - i }}>
              {['P','D','Q'][i]}
            </div>
          ))}
        </div>
      </div>
      <div className='demo-team-grid-wrap' style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        <div className='demo-team-grid' style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', height: '100%' }}>
          {cols.map((col, ci) => (
            <div key={ci} className='demo-team-slot' style={{ display: 'flex', flexDirection: 'column', borderRight: ci < 2 ? '1px solid #f0f0f0' : 'none' }}>
              <div className='demo-team-slot-header' style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '8px 12px', borderBottom: '1px solid #f0f0f0', background: '#fafbfc', fontSize: 11 }}>
                <span style={{ width: 16, height: 16, borderRadius: '50%', background: col.bg, color: '#fff', fontSize: 7, fontWeight: 700, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>{col.name}</span>
                <span className='demo-team-slot-name' style={{ fontWeight: 600, color: '#1d2129' }}>{col.name}</span>
                <span className='demo-team-slot-mode' style={{ fontSize: 10, color: '#4e5969', marginLeft: 'auto' }}>{col.role}</span>
              </div>
              <div className='demo-team-messages' style={{ flex: 1, padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 7, overflow: 'auto', background: '#fff' }}>
                <div className='demo-team-msg demo-team-msg-user' style={{ alignSelf: 'flex-end', background: '#f2f3f5', color: '#1d2129', padding: '7px 10px', borderRadius: '8px 0 8px 8px', maxWidth: '88%', fontSize: '10.5px', lineHeight: 1.5 }}>{col.userMsg}</div>
                {col.msgs.map((m, mi) => (
                  <div key={mi} className='demo-team-msg demo-team-msg-agent' style={{ alignSelf: 'flex-start', padding: '4px 0', maxWidth: '90%', color: '#4e5969', fontSize: '10.5px', lineHeight: 1.5 }}>{m}</div>
                ))}
                {col.crossMsgs?.map((m, mi) => (
                  <div key={`cross-${mi}`} className='demo-team-msg demo-team-msg-cross' style={{ alignSelf: 'flex-start', background: '#fafbff', border: '1px solid #e8ecf5', padding: '7px 10px', borderRadius: 10, maxWidth: '88%', fontSize: '10.5px' }}>
                    <div className='demo-team-msg-cross-header' style={{ fontSize: 9, fontWeight: 600, color: '#6b7785', marginBottom: 3 }}>
                      {ci === 0 ? 'Dev' : ci === 1 ? 'QA' : 'PM'}
                    </div>
                    {m}
                  </div>
                ))}
              </div>
              <div className='demo-team-sendbox' style={{ margin: '8px 10px', border: '1px solid #e5e6eb', borderRadius: 14, padding: '7px 12px', fontSize: 10, color: '#a1a2aa', display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: '#fff' }}>
                <span>给 {col.name} 发消息...</span>
                <div className='demo-team-send' style={{ width: 22, height: 22, borderRadius: '50%', background: col.name === 'PM' ? 'var(--moss)' : '#d3d4d9', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <svg width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='#fff' strokeWidth='3'><path d='m22 2-10 20-4-10Z'/><path d='M22 2 12 12'/></svg>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

/* ── Detail views (claude, ppt, excel, gemini, paper) ── */

function ChatMessages({ children }: { children: React.ReactNode }) {
  return <div className='demo-chat-messages' style={{ flex: 1, display: 'flex', flexDirection: 'column', padding: '16px 20px', gap: 10, overflow: 'auto' }}>{children}</div>
}

function ClaudeView({ step }: { step: number }) {
  return (
    <ChatMessages>
      <div className='demo-msg-user-file' style={{ alignSelf: 'flex-end', background: '#fff', border: '1px solid #e5e6eb', borderRadius: 8, padding: '8px 10px', display: 'flex', alignItems: 'center', gap: 8, maxWidth: '76%' }}>
        <div className='demo-msg-user-file-icon' style={{ width: 32, height: 32, borderRadius: 6, background: '#eef0f8', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><span style={{ fontSize: 14 }}>📄</span></div>
        <div className='demo-msg-user-file-info' style={{ fontSize: 11 }}>
          <div className='demo-msg-user-file-name' style={{ fontWeight: 600, color: '#1d2129' }}>auth.ts</div>
          <div className='demo-msg-user-file-meta' style={{ fontSize: '9.5px', color: '#86909c' }}>TypeScript · 3.2 KB</div>
        </div>
      </div>
      <div className='demo-msg demo-msg-user' style={{ alignSelf: 'flex-end', background: '#f2f3f5', color: '#1d2129', padding: '8px 12px', borderRadius: '8px 0 8px 8px', maxWidth: '76%' }}>把 auth middleware 改用 JWT，测试也一并更新。</div>
      {step >= 1 && <div className='demo-msg demo-msg-assistant' style={{ alignSelf: 'flex-start', color: '#1d2129', fontSize: 12, lineHeight: 1.7 }}>好的，我先读当前实现和 3 个测试文件...</div>}
      {step >= 2 && (
        <div className='demo-msg demo-msg-assistant' style={{ alignSelf: 'flex-start', fontSize: '10.5px', color: '#86909c', padding: '6px 12px', background: '#f8f7fa', borderRadius: 10, lineHeight: 1.6 }}>
          <div style={{ color: '#4e5969', fontWeight: 600, marginBottom: 4, fontSize: 10 }}>文件变更 (4)</div>
          {[
            '✓ 读取 src/auth/index.ts（412 行）',
            '✓ 抽取 session 逻辑 → handlers/session.ts',
            '◐ 抽取 token 逻辑 → handlers/token.ts',
            '○ 更新 routes/* 下 3 处引用',
          ].map((s, i) => <div key={i}>{s}</div>)}
        </div>
      )}
      {step >= 4 && <div className='demo-msg-status' style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '10.5px', color: '#86909c', paddingTop: 8 }}><span className='dot' style={{ width: 6, height: 6, borderRadius: '50%', background: '#00b42a', boxShadow: '0 0 6px rgba(0,180,42,0.4)' }} />正在编辑 tests/jwt.test.ts...</div>}
    </ChatMessages>
  )
}

function PptView({ step }: { step: number }) {
  return (
    <ChatMessages>
      <div className='demo-msg-user-file' style={{ alignSelf: 'flex-end', background: '#fff', border: '1px solid #e5e6eb', borderRadius: 8, padding: '8px 10px', display: 'flex', alignItems: 'center', gap: 8, maxWidth: '76%' }}>
        <div className='demo-msg-user-file-icon' style={{ width: 32, height: 32, borderRadius: 6, background: '#fff2e5', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><span style={{ fontSize: 14 }}>📊</span></div>
        <div className='demo-msg-user-file-info' style={{ fontSize: 11 }}>
          <div className='demo-msg-user-file-name' style={{ fontWeight: 600, color: '#1d2129' }}>Q2-financials.csv</div>
          <div className='demo-msg-user-file-meta' style={{ fontSize: '9.5px', color: '#86909c' }}>CSV · 48 KB</div>
        </div>
      </div>
      <div className='demo-msg demo-msg-user' style={{ alignSelf: 'flex-end', background: '#f2f3f5', color: '#1d2129', padding: '8px 12px', borderRadius: '8px 0 8px 8px', maxWidth: '76%' }}>帮我做一份 Q2 投资人更新的路演 PPT。营收 280 万美金，深色主题，12 页。</div>
      {step >= 1 && <div className='demo-msg demo-msg-assistant'>收到——深色主题 12 页投资人 PPT，先解析财务数据。</div>}
      {step >= 2 && (
        <div className='demo-msg demo-msg-assistant' style={{ alignSelf: 'flex-start', fontSize: '10.5px', color: '#86909c', padding: '6px 12px', background: '#f8f7fa', borderRadius: 10, lineHeight: 1.6 }}>
          <div style={{ color: '#4e5969', fontWeight: 600, marginBottom: 4, fontSize: 10 }}>步骤</div>
          {[
            '✓ 解析 Q2-financials.csv — 847 行，12 列',
            '✓ 结构：封面 → 增长 → 模式 → 团队 → 诉求',
            '✓ 根据季度 ARR 生成柱状图',
            '✓ 应用深色主题 (#1a2332) + 亮色 #f5a623',
            step >= 4 ? '⟳ 正在生成第 10 页 / 共 12 页...' : '◐ 正在生成第 5 页...',
          ].map((s, i) => <div key={i}>{s}</div>)}
        </div>
      )}
      {step >= 3 && <div className='demo-msg-status' style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '10.5px', color: '#86909c', paddingTop: 8 }}><span className='dot' style={{ width: 6, height: 6, borderRadius: '50%', background: '#d19b2c', boxShadow: '0 0 6px rgba(209,155,44,0.4)' }} />正在生成第 10/12 页...</div>}
    </ChatMessages>
  )
}

function ExcelView({ step }: { step: number }) {
  return (
    <ChatMessages>
      <div className='demo-msg-user-file' style={{ alignSelf: 'flex-end', background: '#fff', border: '1px solid #e5e6eb', borderRadius: 8, padding: '8px 10px', display: 'flex', alignItems: 'center', gap: 8, maxWidth: '76%' }}>
        <div className='demo-msg-user-file-icon' style={{ width: 32, height: 32, borderRadius: 6, background: '#e6f7ec', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><span style={{ fontSize: 14 }}>📈</span></div>
        <div className='demo-msg-user-file-info' style={{ fontSize: 11 }}>
          <div className='demo-msg-user-file-name' style={{ fontWeight: 600, color: '#1d2129' }}>sales-weekly.xlsx</div>
          <div className='demo-msg-user-file-meta' style={{ fontSize: '9.5px', color: '#86909c' }}>Excel · 126 KB</div>
        </div>
      </div>
      <div className='demo-msg demo-msg-user' style={{ alignSelf: 'flex-end', background: '#f2f3f5', color: '#1d2129', padding: '8px 12px', borderRadius: '8px 0 8px 8px', maxWidth: '76%' }}>基于数据生成周销售报表，带各大区对比图。</div>
      {step >= 1 && <div className='demo-msg demo-msg-assistant'>正在解析你的数据——3 个大区、本周 847 笔交易。报表生成中。</div>}
      {step >= 2 && (
        <div className='demo-msg demo-msg-assistant' style={{ alignSelf: 'flex-start', fontSize: '10.5px', color: '#86909c', padding: '6px 12px', background: '#f8f7fa', borderRadius: 10, lineHeight: 1.6 }}>
          <div style={{ color: '#4e5969', fontWeight: 600, marginBottom: 4, fontSize: 10 }}>步骤</div>
          {['✓ 解析 sales-raw.csv — 2,403 行，9 列', '✓ 生成透视：大区 × 周', '✓ 添加异常值条件格式', '✓ 插入柱状图 + 迷你图', '✓ 导出 sales-w16.xlsx'].map((s, i) => <div key={i} style={{ opacity: step >= i + 2 ? 1 : 0.3 }}>{s}</div>)}
        </div>
      )}
      {step >= 4 && <div className='demo-msg-status' style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '10.5px', color: '#00b42a', paddingTop: 8 }}><span className='dot' style={{ width: 6, height: 6, borderRadius: '50%', background: '#00b42a' }} />已保存 sales-w16.xlsx · 4 个工作表</div>}
    </ChatMessages>
  )
}

function GeminiView({ step }: { step: number }) {
  return (
    <ChatMessages>
      <div className='demo-msg-user-img' style={{ alignSelf: 'flex-end', maxWidth: '45%', borderRadius: 8, overflow: 'hidden', border: '1px solid #e5e6eb' }}>
        <div style={{ width: '100%', aspectRatio: '4/3', background: '#e8e8e8', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <svg width='48' height='48' viewBox='0 0 24 24' fill='none' stroke='#999' strokeWidth='1'><rect width='18' height='18' x='3' y='3' rx='2'/><circle cx='8.5' cy='8.5' r='1.5'/><path d='m21 15-5-5L5 21'/></svg>
        </div>
      </div>
      <div className='demo-msg demo-msg-user' style={{ alignSelf: 'flex-end', background: '#f2f3f5', color: '#1d2129', padding: '8px 12px', borderRadius: '8px 0 8px 8px', maxWidth: '76%' }}>去背景 + 加柔和渐变。落地页 hero 区用，要干净。</div>
      {step >= 1 && <div className='demo-msg demo-msg-assistant'>来了——抠图 + 马卡龙渐变。阴影保留给质感。</div>}
      {step >= 2 && (
        <div className='demo-msg demo-msg-assistant' style={{ alignSelf: 'flex-start', fontSize: '10.5px', color: '#86909c', padding: '6px 12px', background: '#f8f7fa', borderRadius: 10, lineHeight: 1.6 }}>
          <div style={{ color: '#4e5969', fontWeight: 600, marginBottom: 4, fontSize: 10 }}>步骤</div>
          {['✓ 识别主体（置信度 98.2%）', '✓ 去除背景——边缘细化完成', '✓ 应用渐变：#e8eeff → #f0e8ff → #ffe8f0', '✓ 添加投影（12px 模糊、15% 透明度）', '✓ 输出：1920×1440 PNG、2.1 MB'].map((s, i) => <div key={i} style={{ opacity: step >= i + 2 ? 1 : 0.3 }}>{s}</div>)}
        </div>
      )}
      {step >= 3 && <div className='demo-msg-img-output' style={{ alignSelf: 'flex-start', maxWidth: '45%', borderRadius: 8, overflow: 'hidden', border: '1px solid #e5e6eb', background: 'linear-gradient(135deg, #e8eeff, #f0e8ff, #ffe8f0)' }}>
        <div style={{ width: '100%', aspectRatio: '4/3', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <svg width='48' height='48' viewBox='0 0 24 24' fill='none' stroke='#9b8cd8' strokeWidth='1'><rect width='18' height='18' x='3' y='3' rx='2'/><circle cx='8.5' cy='8.5' r='1.5'/><path d='m21 15-5-5L5 21'/></svg>
        </div>
      </div>}
      {step >= 5 && <div className='demo-msg demo-msg-assistant'>搞定！背景已去除并应用渐变。图片已可用于你的 hero 区，查看右侧预览面板。</div>}
    </ChatMessages>
  )
}

function PaperView({ step }: { step: number }) {
  return (
    <ChatMessages>
      <div className='demo-msg demo-msg-user' style={{ alignSelf: 'flex-end', background: '#f2f3f5', color: '#1d2129', padding: '8px 12px', borderRadius: '8px 0 8px 8px', maxWidth: '76%' }}>写一篇关于拓扑绝缘体的物理论文：带公式排版、双栏摘要、定理/定义块、横向插图。</div>
      {step >= 1 && <div className='demo-msg demo-msg-assistant'>我来搭建完整手稿：封面、目录、编号章节、公式块（Berry 曲率、Chern 数）、定义/定理块、横向插图。</div>}
      {step >= 2 && (
        <div className='demo-msg demo-msg-assistant' style={{ alignSelf: 'flex-start', fontSize: '10.5px', color: '#86909c', padding: '6px 12px', background: '#f8f7fa', borderRadius: 10, lineHeight: 1.6 }}>
          <div style={{ color: '#4e5969', fontWeight: 600, marginBottom: 4, fontSize: 10 }}>文档结构</div>
          {['封面：标题、作者、单位', '双栏摘要（230 字）', '§1 引言 — §2 Berry 相位 — §3 体边对应', '4 个公式（LaTeX → Word MathML）', '2 个定理块、1 个定义块', '图 1：能带结构（横向、带注释）', '28 条 APS 格式参考文献'].map((s, i) => <div key={i} style={{ opacity: step >= i + 2 ? 1 : 0.3 }}>{step >= i + 2 ? '✓ ' : '○ '}{s}</div>)}
        </div>
      )}
      {step >= 6 && <div className='demo-msg-status' style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '10.5px', color: '#00b42a', paddingTop: 8 }}><span className='dot' style={{ width: 6, height: 6, borderRadius: '50%', background: '#00b42a' }} />已保存 topological_insulator_paper.docx · 18 页</div>}
    </ChatMessages>
  )
}

/* ── Preview Panel ── */
function PreviewPanel({ view, step }: { view: ViewId; step: number }) {
  if (view === 'ppt') return <PptPreview step={step} />
  if (view === 'excel') return <ExcelPreview step={step} />
  if (view === 'gemini') return <GeminiPreview />
  if (view === 'paper') return <PaperPreview />
  return null
}

function PptPreview({ step }: { step: number }) {
  return (
    <div className='demo-preview-panel' style={{ width: '44%', flexShrink: 0, borderLeft: '1px solid #e5e6eb', display: 'flex', flexDirection: 'column', background: '#f9fafb' }}>
      <div className='demo-preview-tab' style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 12px', borderBottom: '1px solid #e5e6eb', fontSize: 11, background: '#fff', minHeight: 38 }}>
        <span style={{ background: '#f2f3f5', padding: '3px 8px', borderRadius: 6, fontSize: '10.5px' }}>Q2-pitch-deck.pptx</span>
        <span className='demo-preview-tab-close' style={{ color: '#86909c', cursor: 'default' }}>×</span>
        <span className='demo-preview-tab-expand' style={{ marginLeft: 'auto', color: '#86909c' }}><svg width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2'><path d='M15 3h6v6'/><path d='M9 21H3v-6'/><path d='M21 3l-7 7'/><path d='M3 21l7-7'/></svg></span>
      </div>
      <div className='demo-preview-content' style={{ flex: 1, overflow: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'center', justifyContent: 'center', background: '#1a2332' }}>
        <div style={{ width: '100%', aspectRatio: '16/9', borderRadius: 6, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: '#fff', textAlign: 'center', padding: 20 }}>
          <div style={{ fontSize: 10, color: '#f5a623', letterSpacing: 2, textTransform: 'uppercase', marginBottom: 16 }}>投资人更新</div>
          <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 8 }}>用 AI</div>
          <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 32 }}>加速效率</div>
          <div style={{ display: 'flex', gap: 24 }}>
            {[
              { val: '$2.8M', label: 'ARR' },
              { val: '+23%', label: '同比增长' },
              { val: '4.2K', label: '活跃用户' },
            ].map(m => (
              <div key={m.label} style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 18, fontWeight: 700, color: '#f5a623' }}>{m.val}</div>
                <div style={{ fontSize: 9, color: '#889' }}>{m.label}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function ExcelPreview({ step }: { step: number }) {
  return (
    <div className='demo-preview-panel' style={{ width: '44%', flexShrink: 0, borderLeft: '1px solid #e5e6eb', display: 'flex', flexDirection: 'column', background: '#fff' }}>
      <div className='demo-preview-tab' style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '8px 12px', borderBottom: '1px solid #e5e6eb', fontSize: 11, background: '#fff' }}>
        {['汇总', '大区', '图表', '原始数据'].map((t, i) => (
          <span key={t} style={{ padding: '4px 10px', borderRadius: 4, fontSize: '10.5px', color: i === 0 ? '#1d2129' : '#86909c', fontWeight: i === 0 ? 500 : 400 }}>{t}</span>
        ))}
      </div>
      <div className='demo-preview-content' style={{ flex: 1, overflow: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
        <table style={{ width: '100%', fontSize: 10, borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #e5e6eb' }}>
              {['大区', '上周', '本周', '增长'].map(h => <th key={h} style={{ textAlign: 'left', padding: '4px 8px', color: '#4e5969', fontWeight: 600 }}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {[
              { region: 'APAC', last: '$42K', this: '$58K', growth: '+38%' },
              { region: 'EMEA', last: '$34K', this: '$41K', growth: '+20%' },
              { region: 'Americas', last: '$65K', this: '$78K', growth: '+20%' },
            ].map(r => (
              <tr key={r.region} style={{ borderBottom: '1px solid #f0f0f0' }}>
                <td style={{ padding: '5px 8px', fontWeight: 600, color: '#1d2129' }}>{r.region}</td>
                <td style={{ padding: '5px 8px', color: '#86909c' }}>{r.last}</td>
                <td style={{ padding: '5px 8px', color: '#1d2129' }}>{r.this}</td>
                <td style={{ padding: '5px 8px', color: '#00b42a', fontWeight: 600 }}>{r.growth}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function GeminiPreview() {
  return (
    <div className='demo-preview-panel' style={{ width: '44%', flexShrink: 0, borderLeft: '1px solid #e5e6eb', display: 'flex', flexDirection: 'column', background: '#f9fafb' }}>
      <div className='demo-preview-tab' style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 12px', borderBottom: '1px solid #e5e6eb', fontSize: 11, background: '#fff' }}>
        <span style={{ background: '#f2f3f5', padding: '3px 8px', borderRadius: 6, fontSize: '10.5px' }}>product-hero-edited.png</span>
        <span style={{ color: '#86909c', cursor: 'default' }}>×</span>
      </div>
      <div className='demo-preview-content' style={{ flex: 1, overflow: 'auto', padding: 12, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: 'linear-gradient(135deg, #e8eeff, #f0e8ff, #ffe8f0)' }}>
        <svg width='80' height='80' viewBox='0 0 24 24' fill='none' stroke='#9b8cd8' strokeWidth='0.8' style={{ filter: 'drop-shadow(0 10px 30px rgba(0,0,0,0.2))' }}>
          <rect width='18' height='18' x='3' y='3' rx='2'/><circle cx='8.5' cy='8.5' r='1.5'/><path d='m21 15-5-5L5 21'/>
        </svg>
        <div style={{ fontSize: 9, color: '#86909c', marginTop: 8 }}>1920 × 1440 · PNG · 2.1 MB</div>
      </div>
    </div>
  )
}

function PaperPreview() {
  return (
    <div className='demo-preview-panel' style={{ width: '44%', flexShrink: 0, borderLeft: '1px solid #e5e6eb', display: 'flex', flexDirection: 'column', background: '#fff', overflow: 'auto' }}>
      <div className='demo-preview-content' style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12, fontFamily: 'Georgia, "New York", serif', fontSize: 11 }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 8, textTransform: 'uppercase', letterSpacing: 2, color: '#86909c', marginBottom: 8 }}>Topological Insulators</div>
          <div style={{ fontSize: 14, fontWeight: 700, lineHeight: 1.3, marginBottom: 4 }}>Topological Insulators: Berry Curvature, Bulk-Boundary Correspondence, and Quantum Transport</div>
          <div style={{ fontSize: 9, color: '#4e5969', marginBottom: 12 }}>Albert · April 2026</div>
        </div>
        <div style={{ columnCount: 2, columnGap: 16, fontSize: 9, lineHeight: 1.6, color: '#3d3850' }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>摘要：</div>
          拓扑绝缘体同时具有带隙体能谱与对称性保护的边界模式，其鲁棒性由全局拓扑不变量而非局部序参量决定。本文回顾量子自旋霍尔及三维拓扑绝缘相的带论起源...
        </div>
        <div style={{ border: '1px solid #e5e6eb', borderRadius: 6, padding: 10, background: '#fafbfc' }}>
          <div style={{ fontSize: 10, fontWeight: 700, marginBottom: 4 }}>定理 1（体-边对应）</div>
          <div style={{ fontSize: 9, color: '#4e5969' }}>对晶格上带边界的有能隙哈密顿量，拓扑保护的边界模式数等于体 Chern 数 Cₙ。</div>
        </div>
        <div style={{ textAlign: 'center', fontSize: 9, color: '#86909c' }}>18 页 · APS 格式</div>
      </div>
    </div>
  )
}

/* ── Workspace Panel (Claude) ── */
function WorkspacePanel() {
  return (
    <div className='demo-workspace-panel' style={{ width: '44%', flexShrink: 0, borderLeft: '1px solid #e5e6eb', display: 'flex', flexDirection: 'column', background: '#fff' }}>
      <div className='demo-workspace-header' style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px 0' }}>
        <span className='demo-workspace-title' style={{ fontSize: 13, fontWeight: 600, color: '#1d2129' }}>工作区</span>
        <div className='demo-workspace-icons' style={{ display: 'flex', gap: 6, color: '#86909c' }}>
          <svg width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2'><path d='M12 5v14'/><path d='M5 12h14'/></svg>
        </div>
      </div>
      <div className='demo-workspace-tabs' style={{ display: 'flex', gap: 0, padding: '6px 14px', borderBottom: '1px solid #e5e6eb', fontSize: 11 }}>
        <span className='demo-workspace-tab active' style={{ padding: '4px 10px', color: '#1d2129', fontWeight: 500 }}>文件</span>
        <span className='demo-workspace-tab' style={{ padding: '4px 10px', color: '#86909c' }}>变更</span>
        <span className='demo-workspace-tab-branch' style={{ display: 'flex', alignItems: 'center', gap: 3, marginLeft: 'auto', fontSize: 10, color: '#86909c' }}>
          <svg width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2'><path d='M6 3v12'/></svg>
          main
        </span>
      </div>
      <div className='demo-workspace-tree' style={{ flex: 1, overflow: 'auto', padding: '8px', fontSize: 11, color: '#1d2129' }}>
        {['src/auth', 'src/auth/handlers', 'tests'].map(folder => (
          <div key={folder}>
            <div className='demo-workspace-tree-item folder' style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '3px 6px', borderRadius: 4, fontWeight: 500 }}>
              <svg width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2'><path d='M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z'/></svg>
              {folder}
            </div>
            {folder === 'src/auth' && (
              <div className='demo-workspace-tree-indent' style={{ marginLeft: 16 }}>
                {['index.ts', 'session.ts', 'token.ts'].map(f => (
                  <div key={f} className='demo-workspace-tree-item' style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '3px 6px', borderRadius: 4, color: f.includes('token') ? '#7c3aed' : '#1d2129' }}>
                    <svg width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2'><path d='M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z'/><polyline points='14 2 14 8 20 8'/></svg>
                    {f}
                    {f === 'token.ts' && <span style={{ fontSize: 8, color: '#7c3aed', marginLeft: 'auto' }}>写入中...</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

/* ── SVG Icons ── */
function SidebarIcon() {
  return <svg width='16' height='16' viewBox='0 0 48 48' fill='none' stroke='#86909c' strokeWidth='3.5' strokeLinecap='round'><rect x='6' y='10' width='36' height='28' rx='5'/><line x1='18' y1='10' x2='18' y2='38'/></svg>
}
function ChevronLeft() {
  return <svg width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='#c9cdd4' strokeWidth='2.5' strokeLinecap='round'><path d='m15 18-6-6 6-6'/></svg>
}
function ChevronRight() {
  return <svg width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='#c9cdd4' strokeWidth='2.5' strokeLinecap='round'><path d='m9 18 6-6-6-6'/></svg>
}
function ClaudeSvg() {
  return (
    <svg width='18' height='18' viewBox='0 0 24 24'><path d='M4.709 15.955l4.72-2.647.08-.23-.08-.128H9.2l-.79-.048-2.698-.073-2.339-.097-2.266-.122-.571-.121L0 11.784l.055-.352.48-.321.686.06 1.52.103 2.278.158 1.652.097 2.449.255h.389l.055-.157-.134-.098-.103-.097-2.358-1.596-2.552-1.688-1.336-.972-.724-.491-.364-.462-.158-1.008.656-.722.881.06.225.061.893.686 1.908 1.476 2.491 1.833.365.304.145-.103.019-.073-.164-.274-1.355-2.446-1.446-2.49-.644-1.032-.17-.619a2.97 2.97 0 01-.104-.729L6.283.134 6.696 0l.996.134.42.364.62 1.414 1.002 2.229 1.555 3.03.456.898.243.832.091.255h.158V9.01l.128-1.706.237-2.095.23-2.695.08-.76.376-.91.747-.492.584.28.48.685-.067.444-.286 1.851-.559 2.903-.364 1.942h.212l.243-.242.985-1.306 1.652-2.064.73-.82.85-.904.547-.431h1.033l.76 1.129-.34 1.166-1.064 1.347-.881 1.142-1.264 1.7-.79 1.36.073.11.188-.02 2.856-.606 1.543-.28 1.841-.315.833.388.091.395-.328.807-1.969.486-2.309.462-3.439.813-.042.03.049.061 1.549.146.662.036h1.622l3.02.225.79.522.474.638-.079.485-1.215.62-1.64-.389-3.829-.91-1.312-.329h-.182v.11l1.093 1.068 2.006 1.81 2.509 2.33.127.578-.322.455-.34-.049-2.205-1.657-.851-.747-1.926-1.62h-.128v.17l.444.649 2.345 3.521.122 1.08-.17.353-.608.213-.668-.122-1.374-1.925-1.415-2.167-1.143-1.943-.14.08-.674 7.254-.316.37-.729.28-.607-.461-.322-.747.322-1.476.389-1.924.315-1.53.286-1.9.17-.632-.012-.042-.14.018-1.434 1.967-2.18 2.945-1.726 1.845-.414.164-.717-.37.067-.662.401-.589 2.388-3.036 1.44-1.882.93-1.086-.006-.158h-.055L4.132 18.56l-1.13.146-.487-.456.061-.746.231-.243 1.908-1.312-.006.006z' fill='#D97757'/></svg>
  )
}
