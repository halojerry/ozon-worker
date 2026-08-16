/*
Copyright (C) 2023-2026 QuantumNous — GNU AGPL v3
*/
import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { PoundingHeartLogo } from '../pounding-heart-logo'

function GeminiGradientIcon({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox='0 0 24 24' aria-hidden='true'>
      <defs><radialGradient id='cronGem' cx='0.35' cy='0.85' r='0.8'><stop offset='0%' stopColor='#1BA15D'/><stop offset='40%' stopColor='#4285F4'/><stop offset='70%' stopColor='#A25BF2'/><stop offset='100%' stopColor='#EA4335'/></radialGradient></defs>
      <path d='M20.616 10.835a14.147 14.147 0 01-4.45-3.001 14.111 14.111 0 01-3.678-6.452.503.503 0 00-.975 0 14.134 14.134 0 01-3.679 6.452 14.155 14.155 0 01-4.45 3.001c-.65.28-1.318.505-2.002.678a.502.502 0 000 .975c.684.172 1.35.397 2.002.677a14.147 14.147 0 014.45 3.001 14.112 14.112 0 013.679 6.453.502.502 0 00.975 0c.172-.685.397-1.351.677-2.003a14.145 14.145 0 013.001-4.45 14.113 14.113 0 016.453-3.678.503.503 0 000-.975 13.245 13.245 0 01-2.003-.678z' fill='url(#cronGem)' />
    </svg>
  )
}

function ClaudeCronIcon({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox='0 0 24 24'><circle cx='12' cy='12' r='12' fill='#D97757'/><path d='M4.709 15.955l4.72-2.647.08-.23-.08-.128H9.2l-.79-.048-2.698-.073-2.339-.097-2.266-.122-.571-.121L0 11.784l.055-.352.48-.321.686.06 1.52.103 2.278.158 1.652.097 2.449.255h.389l.055-.157-.134-.098-.103-.097-2.358-1.596-2.552-1.688-1.336-.972-.724-.491-.364-.462-.158-1.008.656-.722.881.06.225.061.893.686 1.908 1.476 2.491 1.833.365.304.145-.103.019-.073-.164-.274-1.355-2.446-1.446-2.49-.644-1.032-.17-.619a2.97 2.97 0 01-.104-.729L6.283.134 6.696 0l.996.134.42.364.62 1.414 1.002 2.229 1.555 3.03.456.898.243.832.091.255h.158V9.01l.128-1.706.237-2.095.23-2.695.08-.76.376-.91.747-.492.584.28.48.685-.067.444-.286 1.851-.559 2.903-.364 1.942h.212l.243-.242.985-1.306 1.652-2.064.73-.82.85-.904.547-.431h1.033l.76 1.129-.34 1.166-1.064 1.347-.881 1.142-1.264 1.7-.79 1.36.073.11.188-.02 2.856-.606 1.543-.28 1.841-.315.833.388.091.395-.328.807-1.969.486-2.309.462-3.439.813-.042.03.049.061 1.549.146.662.036h1.622l3.02.225.79.522.474.638-.079.485-1.215.62-1.64-.389-3.829-.91-1.312-.329h-.182v.11l1.093 1.068 2.006 1.81 2.509 2.33.127.578-.322.455-.34-.049-2.205-1.657-.851-.747-1.926-1.62h-.128v.17l.444.649 2.345 3.521.122 1.08-.17.353-.608.213-.668-.122-1.374-1.925-1.415-2.167-1.143-1.943-.14.08-.674 7.254-.316.37-.729.28-.607-.461-.322-.747.322-1.476.389-1.924.315-1.53.286-1.9.17-.632-.012-.042-.14.018-1.434 1.967-2.18 2.945-1.726 1.845-.414.164-.717-.37.067-.662.401-.589 2.388-3.036 1.44-1.882.93-1.086-.006-.158h-.055L4.132 18.56l-1.13.146-.487-.456.061-.746.231-.243 1.908-1.312-.006.006z' fill='#fff' fillRule='nonzero' transform='translate(4.5,3) scale(.625)'/></svg>
  )
}

function CodexCronIcon({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox='0 0 160 160' fill='none'><circle cx='80' cy='79' r='65' fill='white'/><path d='M135 80C135 49.6 110.4 25 80 25C49.6 25 25 49.6 25 80C25 110.4 49.6 135 80 135V149C41.9 149 11 118.1 11 80C11 41.9 41.9 11 80 11C118.1 11 149 41.9 149 80C149 118.1 118.1 149 80 149V135C110.4 135 135 110.4 135 80Z' fill='black'/><path d='M50.9 54.4C54 52.6 58 53.6 59.8 56.7L70.9 75.7C72.7 78.7 72.7 82.3 70.9 85.3L59.8 104.3C58 107.4 54 108.4 50.9 106.6C47.8 104.8 46.8 100.8 48.6 97.7L58.7 80.5L48.6 63.3C46.8 60.2 47.8 56.2 50.9 54.4Z' fill='black'/><path d='M112 89.5C115.6 89.5 118.5 92.4 118.5 96C118.5 99.6 115.6 102.5 112 102.5H85C81.4 102.5 78.5 99.6 78.5 96C78.5 92.4 81.4 89.5 85 89.5H112Z' fill='black'/></svg>
  )
}

const CRON_CARDS = [
  { name: 'Weekly sales report', schedule: 'Every Mon · 8:00 AM', status: 'active' as const, agent: 'Gemini CLI', icon: <GeminiGradientIcon size={14} /> },
  { name: 'Daily code review', schedule: 'Every day · 9:00 AM', status: 'running' as const, agent: 'Claude Code', icon: <ClaudeCronIcon size={14} /> },
  { name: 'Summarize emails', schedule: 'Every day · 8:30 AM', status: 'active' as const, agent: 'POUNDING', icon: <PoundingHeartLogo size={14} /> },
  { name: 'Backup project files', schedule: 'Every day · 11:00 PM', status: 'paused' as const, agent: 'POUNDING', icon: <PoundingHeartLogo size={14} /> },
  { name: 'Generate standup notes', schedule: 'Every Mon · 9:00 AM', status: 'active' as const, agent: 'Codex', icon: <CodexCronIcon size={14} /> },
]

const CRON_TYPING_TEXT = 'Every Monday at 8am, generate the weekly sales report.'

export function Cron() {
  const { t } = useTranslation()
  const [showChat, setShowChat] = useState(true)
  const [typedLen, setTypedLen] = useState(0)
  const [visibleCards, setVisibleCards] = useState<number[]>([])
  const [triggered, setTriggered] = useState(false)
  const ref = useRef<HTMLElement>(null)

  useEffect(() => {
    const el = ref.current; if (!el) return
    const obs = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) {
        // Typing animation
        let i = 0; setTypedLen(0)
        const iv = setInterval(() => { i += 1; setTypedLen(i); if (i >= CRON_TYPING_TEXT.length) clearInterval(iv) }, 30)
        // After delay, switch to cards
        setTimeout(() => { setShowChat(false); setTriggered(true)
          CRON_CARDS.forEach((_, ci) => setTimeout(() => setVisibleCards(p => [...p, ci]), 100 + ci * 120))
        }, 2500)
        return () => clearInterval(iv)
      }
    }, { threshold: 0.3 })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  return (
    <section className='cron-section reveal' id='automation' ref={ref}>
      <div className='cron-inner'>
        <div className='cron-feature'>
          <div className='cron-text'>
            <p className='section-label'><span>{t('自动化')}</span></p>
            <h2 className='section-title'>{t('设一次，')}<br />{t('它记得。你不必守着。')}</h2>
            <p className='section-sub'>Schedule agents to run tasks on a cron. Code review every morning, backup every night, report every Monday. They work while you sleep.</p>
          </div>

          <div className='cron-visual'>
            <div className='cron-app' id='cronApp'>
              <div className='cron-app-titlebar'>
                <span className='cron-app-dot' style={{ background: '#ff5f57' }} />
                <span className='cron-app-dot' style={{ background: '#ffbd2e' }} />
                <span className='cron-app-dot' style={{ background: '#28c840' }} />
                <div className='cron-app-titlebar-meta'>
                  <svg width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='#86909c' strokeWidth='2'><circle cx='12' cy='13' r='8'/><path d='M12 9v4l2 2'/><path d='M5 3L2 6'/><path d='M22 6l-3-3'/></svg>
                  Scheduled Tasks
                </div>
              </div>

              <div className='cron-app-body'>
                <div className='cron-app-header'>
                  <div className='cron-app-title'>
                    <svg className={`cron-alarm-icon${triggered ? ' ringing' : ''}`} width='18' height='18' viewBox='0 0 24 24' fill='none' stroke='#4b3c7a' strokeWidth='2'><circle cx='12' cy='13' r='8'/><path d='M12 9v4l2 2'/><path d='M5 3L2 6'/><path d='M22 6l-3-3'/></svg>
                    Scheduled Tasks
                  </div>
                  <div className='cron-app-count'>{showChat ? 'Triggering soon...' : '5 tasks'}</div>
                </div>

                {/* Chat input phase */}
                <div className={`cron-chat${!showChat ? ' hidden' : ''}`} id='cronChat'>
                  <div className='cron-chat-agents'>
                    <div className='cron-chat-agent-selected'>
                      <PoundingHeartLogo size={20} /> POUNDING
                    </div>
                    <div className='cron-chat-agent-divider' />
                    {[
                      <ClaudeCronIcon key='cl' size={24} />,
                      <CodexCronIcon key='cdx' size={24} />,
                      <GeminiGradientIcon key='gm' size={24} />,
                    ].map((ic, i) => (
                      <div key={i} className='cron-chat-agent-icon' aria-label={['Claude Code','Codex','Gemini'][i]}>
                        {ic}
                      </div>
                    ))}
                    <div className='cron-chat-agent-plus' aria-hidden='true'>+</div>
                  </div>
                  <div className='cron-chat-card'>
                    <div className='cron-chat-top'>
                      <span className='cron-chat-typed' id='cronChatText'>
                        {CRON_TYPING_TEXT.slice(0, typedLen)}<span className='cron-chat-cursor' />
                      </span>
                    </div>
                    <div className='cron-chat-bottom'>
                      <div className='cron-chat-pill-add' aria-hidden='true'>+</div>
                      <div className='cron-chat-pill'>
                        <svg viewBox='0 0 24 24' fill='none' stroke='#4b3c7a' strokeWidth='2'><circle cx='12' cy='13' r='8'/><path d='M12 9v4l2 2'/><path d='M5 3L2 6'/><path d='M22 6l-3-3'/></svg>
                        <span className='cron-chat-pill-accent'>Scheduled Tasks</span>
                      </div>
                      <div className='cron-chat-bottom-spacer' />
                      <div className='cron-chat-send ready'><svg width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='#fff' strokeWidth='3'><path d='M12 19V5'/><path d='m5 12 7-7 7 7'/></svg></div>
                    </div>
                  </div>
                </div>

                {/* Card grid */}
                <div className='cron-app-grid' id='cronGrid'>
                  {CRON_CARDS.map((card, i) => (
                    <div key={i} className={`cron-card${visibleCards.includes(i) ? ' card-in' : ' card-hidden'}`} style={{ animationDelay: `${i * 0.08}s` }}>
                      <div className='cron-card-top'>
                        <span className='cron-card-name'>{card.name}</span>
                        <span className={`cron-card-toggle ${card.status === 'paused' ? 'off' : 'on'}`} />
                      </div>
                      <div className='cron-card-schedule'>{card.schedule}</div>
                      <div className='cron-card-meta'>
                        <span className={`cron-card-status ${card.status}`}>
                          {card.status === 'active' ? '● Active' : card.status === 'running' ? '◉ Running' : '○ Paused'}
                        </span>
                        <span className='cron-card-agent'>
                          {card.icon}
                          <span className='cron-card-agent-name'> {card.agent}</span>
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
