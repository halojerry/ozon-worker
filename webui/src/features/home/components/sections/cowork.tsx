/*
Copyright (C) 2023-2026 QuantumNous — GNU AGPL v3
*/
import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { PoundingHeartLogo } from '../pounding-heart-logo'

/* ═══════════ AionUi Agent SVG Icons ═══════════ */
function ClaudeIcon({ size = 24 }: { size?: number }) {
  return (
    <svg viewBox='0 0 24 24' width={size} height={size}>
      <circle cx='12' cy='12' r='12' fill='#D97757' />
      <path d='M4.709 15.955l4.72-2.647.08-.23-.08-.128H9.2l-.79-.048-2.698-.073-2.339-.097-2.266-.122-.571-.121L0 11.784l.055-.352.48-.321.686.06 1.52.103 2.278.158 1.652.097 2.449.255h.389l.055-.157-.134-.098-.103-.097-2.358-1.596-2.552-1.688-1.336-.972-.724-.491-.364-.462-.158-1.008.656-.722.881.06.225.061.893.686 1.908 1.476 2.491 1.833.365.304.145-.103.019-.073-.164-.274-1.355-2.446-1.446-2.49-.644-1.032-.17-.619a2.97 2.97 0 01-.104-.729L6.283.134 6.696 0l.996.134.42.364.62 1.414 1.002 2.229 1.555 3.03.456.898.243.832.091.255h.158V9.01l.128-1.706.237-2.095.23-2.695.08-.76.376-.91.747-.492.584.28.48.685-.067.444-.286 1.851-.559 2.903-.364 1.942h.212l.243-.242.985-1.306 1.652-2.064.73-.82.85-.904.547-.431h1.033l.76 1.129-.34 1.166-1.064 1.347-.881 1.142-1.264 1.7-.79 1.36.073.11.188-.02 2.856-.606 1.543-.28 1.841-.315.833.388.091.395-.328.807-1.969.486-2.309.462-3.439.813-.042.03.049.061 1.549.146.662.036h1.622l3.02.225.79.522.474.638-.079.485-1.215.62-1.64-.389-3.829-.91-1.312-.329h-.182v.11l1.093 1.068 2.006 1.81 2.509 2.33.127.578-.322.455-.34-.049-2.205-1.657-.851-.747-1.926-1.62h-.128v.17l.444.649 2.345 3.521.122 1.08-.17.353-.608.213-.668-.122-1.374-1.925-1.415-2.167-1.143-1.943-.14.08-.674 7.254-.316.37-.729.28-.607-.461-.322-.747.322-1.476.389-1.924.315-1.53.286-1.9.17-.632-.012-.042-.14.018-1.434 1.967-2.18 2.945-1.726 1.845-.414.164-.717-.37.067-.662.401-.589 2.388-3.036 1.44-1.882.93-1.086-.006-.158h-.055L4.132 18.56l-1.13.146-.487-.456.061-.746.231-.243 1.908-1.312-.006.006z' fill='#fff' fillRule='nonzero' transform='translate(4.5,3) scale(.625)' />
    </svg>
  )
}
function CodexIcon({ size = 24 }: { size?: number }) {
  return (
    <svg viewBox='0 0 160 160' width={size} height={size}>
      <circle cx='80' cy='80' r='80' fill='#f0f0f0' />
      <path d='M135 80C135 49.6 110.4 25 80 25C49.6 25 25 49.6 25 80C25 110.4 49.6 135 80 135V149C41.9 149 11 118.1 11 80C11 41.9 41.9 11 80 11C118.1 11 149 41.9 149 80C149 118.1 118.1 149 80 149V135C110.4 135 135 110.4 135 80Z' fill='black' />
      <path d='M50.9 54.4C54 52.6 58 53.6 59.8 56.7L70.9 75.7C72.7 78.7 72.7 82.3 70.9 85.3L59.8 104.3C58 107.4 54 108.4 50.9 106.6C47.8 104.8 46.8 100.8 48.6 97.7L58.7 80.5L48.6 63.3C46.8 60.2 47.8 56.2 50.9 54.4Z' fill='black' />
      <path d='M112 89.5C115.6 89.5 118.5 92.4 118.5 96C118.5 99.6 115.6 102.5 112 102.5H85C81.4 102.5 78.5 99.6 78.5 96C78.5 92.4 81.4 89.5 85 89.5H112Z' fill='black' />
    </svg>
  )
}
function GeminiIcon({ size = 24 }: { size?: number }) {
  return (
    <svg viewBox='0 0 24 24' width={size} height={size}>
      <circle cx='12' cy='12' r='12' fill='#fff' />
      <path d='M20.616 10.835a14.147 14.147 0 01-4.45-3.001 14.111 14.111 0 01-3.678-6.452.503.503 0 00-.975 0 14.134 14.134 0 01-3.679 6.452 14.155 14.155 0 01-4.45 3.001c-.65.28-1.318.505-2.002.678a.502.502 0 000 .975c.684.172 1.35.397 2.002.677a14.147 14.147 0 014.45 3.001 14.112 14.112 0 013.679 6.453.502.502 0 00.975 0c.172-.685.397-1.351.677-2.003a14.145 14.145 0 013.001-4.45 14.113 14.113 0 016.453-3.678.503.503 0 000-.975 13.245 13.245 0 01-2.003-.678z' fill='#3186FF' />
    </svg>
  )
}
function GooseIcon({ size = 24 }: { size?: number }) {
  return (
    <svg viewBox='0 0 45 45' width={size} height={size}>
      <circle cx='22.5' cy='22.5' r='22.5' fill='#f2f2f2' />
      <path d='M43.21 39.25L39.81 36.45C37.96 34.93 36.39 33.1 35.16 31.05C33.46 28.22 31.12 25.82 28.32 24.07L26.95 23.27C26.48 22.95 26.15 22.44 26.11 21.87C26.08 21.5 26.16 21.17 26.37 20.88C27.08 19.87 30.96 15.7 31.64 15.14C32.51 14.42 33.47 13.83 34.37 13.14C34.5 13.04 34.63 12.95 34.75 12.85C35.06 12.61 35.33 12.37 35.55 12.08C36.6 10.86 36.58 9.83 36.58 9.83C36.47 9.55 36.06 8.7 35.24 8.3C35.97 8.28 36.79 8.56 37.16 8.92C37.6 8.22 37.89 7.77 38.34 7C38.45 6.82 38.5 6.49 38.3 6.3C37.98 5.96 37.64 6.02 37.46 6.12C36.47 6.71 35.5 7.33 34.64 7.89C34.64 7.89 33.6 7.86 32.38 8.92C32.1 9.13 31.86 9.41 31.63 9.69C30.64 10.99 30.04 11.96 29.32 12.83C28.77 13.5 24.59 17.38 23.59 18.09C23.3 18.3 22.97 18.39 22.6 18.36C22.02 18.31 21.52 17.98 21.19 17.52L20.39 16.15C18.64 13.34 16.25 11 13.41 9.3C11.36 8.07 9.53 6.5 8.01 4.65L5.22 1.25C5.08 1.09 4.81 1.11 4.71 1.31C4.4 1.93 3.79 3.23 3.32 5C3.31 5.03 3.32 5.08 3.35 5.11C3.93 5.81 5.27 7.35 6.86 8.65C6.97 8.74 6.88 8.92 6.74 8.88C5.37 8.51 4.03 7.91 3.03 7.4C2.95 7.35 2.85 7.41 2.84 7.5C2.68 8.74 2.64 10.11 2.81 11.56C2.81 11.6 2.84 11.64 2.88 11.66C4.03 12.16 5.85 12.88 7.77 13.34C7.91 13.37 7.9 13.57 7.76 13.6C6.27 13.88 4.71 13.94 3.44 13.93C3.35 13.93 3.28 14.01 3.31 14.09C3.56 15 3.91 15.91 4.36 16.83C4.55 17.25 4.76 17.66 4.98 18.05C5.01 18.12 5.09 18.16 5.16 18.16C6.21 18.11 7.48 18 8.75 17.77C8.97 17.73 9.09 18.02 8.9 18.14C8.04 18.72 7.12 19.22 6.26 19.63C6.14 19.68 6.11 19.83 6.18 19.93C6.69 20.62 7.25 21.28 7.86 21.89L10.94 25.22C12.69 23.43 15.57 21.44 18.75 19.7C14.49 23.17 12.12 25.72 10.93 27.18L10.09 28.34C9.66 28.95 9.29 29.6 8.97 30.27C7.92 32.53 6.19 37.1 6.19 37.1C6.06 37.45 6.17 37.81 6.4 38.04C6.66 38.3 7.01 38.41 7.37 38.28C7.37 38.28 11.94 36.54 14.2 35.5C14.87 35.18 15.52 34.81 16.12 34.37L17.41 33.46C18.09 32.97 19.02 33.05 19.62 33.64L22.58 36.61C23.19 37.22 23.84 37.78 24.53 38.29C24.64 38.36 24.78 38.32 24.84 38.21C25.25 37.35 25.75 36.43 26.33 35.56C26.45 35.38 26.73 35.5 26.7 35.71C26.47 36.99 26.36 38.26 26.31 39.3C26.3 39.38 26.35 39.45 26.41 39.49C26.81 39.71 27.22 39.92 27.63 40.11C28.56 40.56 29.47 40.91 30.37 41.16C30.46 41.19 30.54 41.12 30.54 41.03C30.52 39.76 30.59 38.2 30.87 36.7C30.89 36.56 31.09 36.56 31.13 36.7C31.59 38.62 32.31 40.44 32.8 41.58C32.82 41.63 32.86 41.66 32.91 41.66C34.35 41.83 35.72 41.79 36.97 41.63C37.06 41.62 37.11 41.52 37.07 41.44C36.56 40.44 35.96 39.1 35.58 37.72C35.54 37.58 35.72 37.49 35.81 37.6C37.12 39.2 38.66 40.54 39.35 41.12C39.39 41.14 39.43 41.15 39.47 41.14C41.23 40.68 42.54 40.07 43.16 39.75C43.35 39.65 43.38 39.39 43.21 39.25Z' fill='#1d2129' />
    </svg>
  )
}

const COWORK_TYPING = 'Plan and execute our Q3 product launch — research, deck, blog, and social campaign.'

const DASH_AGENTS = [
  { name: 'Claude Code', tag: 'running' as const, bg: '#f5e6dc', icon: <ClaudeIcon size={26} /> },
  { name: 'Codex', tag: 'running' as const, bg: '#f2f2f2', icon: <CodexIcon size={26} /> },
  { name: 'Gemini', tag: 'running' as const, bg: '#e8f0fe', icon: <GeminiIcon size={26} /> },
  { name: 'POUNDING', tag: 'running' as const, bg: 'transparent', icon: <PoundingHeartLogo size={26} /> },
  { name: 'Goose', tag: 'running' as const, bg: '#f2f2f2', icon: <GooseIcon size={26} /> },
]

const COWORK_TASKS = [
  'Market research report',
  'Competitor analysis spreadsheet',
  'Write launch blog post',
  'Build investor pitch deck',
  'Social media campaign plan',
]

const COWORK_ACTIVITY = [
  { text: 'Claude Code — Writing blog post draft', dot: '#d97757', typing: true },
  { text: 'Codex — Generating pitch deck slides', dot: '#000' },
  { text: 'Goose — Completed spreadsheet ✓', dot: '#1d2129' },
]

export function Cowork() {
  const { t } = useTranslation()
  const [phase, setPhase] = useState<'input' | 'dash'>('input')
  const [typedLen, setTypedLen] = useState(0)
  const [typing, setTyping] = useState(true)
  const [enteredAgents, setEnteredAgents] = useState<number[]>([])
  const [taskDone, setTaskDone] = useState<Set<number>>(new Set())
  const timerRef = useRef<ReturnType<typeof setInterval>>(undefined)

  // Typing animation
  useEffect(() => {
    if (phase !== 'input' || !typing) return
    let i = 0; setTypedLen(0)
    const iv = setInterval(() => { i += 1; setTypedLen(i); if (i >= COWORK_TYPING.length) { clearInterval(iv); setTimeout(() => { setTyping(false); setTimeout(() => goDash(), 1200) }, 600) } }, 25)
    return () => clearInterval(iv)
  }, [phase])

  const goDash = () => {
    setPhase('dash')
    // Stagger agents entering
    DASH_AGENTS.forEach((_, i) => setTimeout(() => setEnteredAgents(p => [...p, i]), 200 + i * 120))
    // Stagger task completion
    ;[0, 1].forEach(i => setTimeout(() => setTaskDone(p => new Set([...p, i])), 600 + i * 400))
  }

  // Reset
  useEffect(() => {
    if (phase !== 'dash') return
    timerRef.current = setTimeout(() => {
      setPhase('input'); setTypedLen(0); setTyping(true)
      setEnteredAgents([]); setTaskDone(new Set())
    }, 12000)
    return () => clearTimeout(timerRef.current)
  }, [phase])

  return (
    <section className='cowork-section reveal' id='cowork'>
      <div className='cowork-inner'>
        <div className='cowork-feature'>
          <div className='cowork-text'>
            <p className='section-label'><span>{t('Agent 协作')}</span></p>
            <h2 className='section-title'>{t('最好的 AI Agents,')}<br />{t('都在这里。')}</h2>
            <p className='section-sub'>POUNDING 自动检测 Claude Code、Codex、Gemini CLI 等 20+ 款 CLI 工具。并行运行、分配任务、组队协作——一个统一的工作区搞定一切。</p>
          </div>

          <div className='cowork-visual' id='coworkVisual'>
            {/* Phase 1: Input */}
            <div className={`cowork-phase cowork-input-phase${phase === 'input' ? ' active' : ''}`} id='coworkPhase1'>
              <div className='cowork-greeting'>Hi, what's your plan for today?</div>
              <div className='cowork-agent-bar'>
                <div className='cowork-agent-bar-selected' data-cw='3'>
                  <PoundingHeartLogo size={16} /> POUNDING
                </div>
                <div className='cowork-agent-bar-divider' />
                {[
                  { cw: 0, label: 'Claude Code', icon: <ClaudeIcon size={20} /> },
                  { cw: 1, label: 'Codex', icon: <CodexIcon size={20} /> },
                  { cw: 2, label: 'Gemini', icon: <GeminiIcon size={20} /> },
                ].map(a => (
                  <div key={a.cw} className='cowork-agent-bar-icon' data-cw={a.cw} aria-label={a.label} title={a.label}>
                    {a.icon}
                  </div>
                ))}
                <div className='cowork-agent-bar-icon' aria-label='Goose' title='Goose'><GooseIcon size={18} /></div>
                <div className='cowork-agent-bar-plus' aria-hidden='true'>+</div>
              </div>

              <div className='cowork-input-card'>
                <div className='cowork-input-top'>
                  {typing ? (
                    <span className='cowork-input-text'>{COWORK_TYPING.slice(0, typedLen)}<span className='cowork-input-cursor' /></span>
                  ) : (
                    <span className='cowork-input-typed'>{COWORK_TYPING}<span className='cowork-input-cursor' /></span>
                  )}
                </div>
                <div className='cowork-input-bottom'>
                  <div className='cowork-input-pill-add' aria-hidden='true'>+</div>
                  <div className='cowork-input-pill'>
                    <svg viewBox='0 0 24 24' fill='none' stroke='#4b3c7a' strokeWidth='2'><path d='M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z'/></svg>
                    <span className='cowork-input-pill-folder'>Q3 Launch</span>
                  </div>
                  <div className='cowork-input-pill'>Claude Sonnet 4</div>
                  <div className='cowork-input-bottom-spacer' />
                  <div className={`cowork-input-send${!typing ? ' ready' : ''}`}>
                    <svg width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='#fff' strokeWidth='3'><path d='M12 19V5'/><path d='m5 12 7-7 7 7'/></svg>
                  </div>
                </div>
              </div>
            </div>

            {/* Phase 2: Dashboard */}
            <div className={`cowork-phase cowork-dash-phase${phase === 'dash' ? ' active' : ''}`} id='coworkPhase2'>
              <div className='cowork-layout'>
                <div className='cowork-left'>
                  <div className='cowork-left-title'>
                    <svg width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='#4b3c7a' strokeWidth='2'><path d='M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z'/></svg>
                    Q3 Launch
                  </div>
                  {DASH_AGENTS.map((a, i) => (
                    <div key={a.name} className={`cowork-agent-row${enteredAgents.includes(i) ? ' entered' : ''}`} style={{ transitionDelay: `${i * 0.08}s` }}>
                      <span className='cowork-agent-logo' style={{ background: a.bg }}>{a.icon}</span>
                      <span className='cowork-agent-name'>{a.name}</span>
                      <span className={`cowork-agent-tag ${a.tag}`}>{a.tag}</span>
                    </div>
                  ))}
                </div>

                <div className='cowork-right'>
                  <div className='cowork-tasks-header'>Q3 Launch Tasks</div>
                  {COWORK_TASKS.map((task, i) => (
                    <div key={i} className='cowork-task-item'>
                      <span className={`cowork-task-icon ${taskDone.has(i) ? 'done animating' : i === 2 ? 'progress' : 'pending'}`}>
                        {taskDone.has(i) && <svg width='9' height='9' viewBox='0 0 24 24' fill='none' stroke='#fff' strokeWidth='3'><polyline points='20 6 9 17 4 12'/></svg>}
                        {!taskDone.has(i) && i === 2 && <svg width='9' height='9' viewBox='0 0 24 24' fill='none' stroke='#fff' strokeWidth='3'><circle cx='12' cy='12' r='4'/></svg>}
                      </span>
                      {task}
                    </div>
                  ))}
                  <div className='cowork-activity'>
                    <div className='cowork-activity-title'>Activity</div>
                    {COWORK_ACTIVITY.map((act, i) => (
                      <div key={i} className='cowork-activity-item' style={{ opacity: 1 }}>
                        <span className='cowork-activity-dot' style={{ background: act.dot }} />
                        {act.text}
                        {act.typing && <span className='cowork-typing'><span /><span /><span /></span>}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
