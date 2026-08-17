/*
Copyright (C) 2023-2026 QuantumNous — GNU AGPL v3
*/
import { useEffect, useRef, useState } from 'react'

// vite base 前缀（生产 /app/）；landing 静态资源必须带前缀否则 404
const L = `${import.meta.env.BASE_URL ?? ''}landing/`
import { useTranslation } from 'react-i18next'

const CHANNELS = [
  { name: 'Telegram', bg: '#2AABEE', src: `${L}telegram.svg` },
  { name: 'Discord', bg: '#5865F2', src: `${L}discord.svg` },
  { name: 'Slack', bg: '#4A154B', src: `${L}slack.svg` },
  { name: 'Lark', bg: '#3370FF', src: `${L}lark.svg` },
  { name: 'Web UI', bg: 'var(--plum)', icon: true },
]

export function Remote() {
  const { t } = useTranslation()
  const ref = useRef<HTMLElement>(null)
  const [paused, setPaused] = useState(true)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const obs = new IntersectionObserver(([e]) => setPaused(!e.isIntersecting), { threshold: 0.3 })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  return (
    <section ref={ref} className={`remote-section reveal ${paused ? 'remote-flow-section-paused' : ''}`} id='remote'>
      <div className='remote-inner'>
        <p className='section-label'>{t('远程控制')}</p>
        <h2 className='section-title'>{t('离开座位，')}<br />{t('没离开控制台。')}</h2>
        <p className='section-sub'>
          {t('用 Telegram、微信、飞书、钉钉或内置 WebUI 发指令——查进度、切模型、收报告，随时随地掌控你的 AI 团队。')}
        </p>

        <div className='remote-flow'>
          <div className='remote-pill'>
            <div className='remote-phone-icon'>
              <svg width='22' height='22' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='1.5'><rect width='14' height='20' x='5' y='2' rx='2'/><path d='M12 18h.01'/></svg>
            </div>
            <span className='remote-pill-label'>{t('Anywhere')}</span>
          </div>
          <div className='remote-flow-line' /><span className='remote-flow-dot' />
          <div className='remote-channels'>
            <div className='remote-channel-title'>{t('Any Channel')}</div>
            <div className='remote-channel-row'>
              {CHANNELS.map(ch => (
                <span key={ch.name} className='remote-ch-icon' title={ch.name} style={{ background: ch.bg }}>
                  {ch.src
                    ? <img src={ch.src} alt={ch.name} style={{ width: '20px', height: '20px', filter: 'brightness(0) invert(1)' }} />
                    : <svg width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='1.5'><rect width='20' height='14' x='2' y='3' rx='2'/><path d='M8 21h8'/><path d='M12 17v4'/></svg>
                  }
                </span>
              ))}
            </div>
          </div>
          <div className='remote-flow-line' /><span className='remote-flow-dot' />
          <div className='remote-pill'>
            <div className='remote-desktop-icon'>
              <svg width='22' height='22' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='1.5'><rect width='20' height='14' x='2' y='3' rx='2'/><path d='M8 21h8'/><path d='M12 17v4'/></svg>
            </div>
            <span className='remote-pill-label'>{t('Your Server')}</span>
            <div className='remote-desktop-agents'>
              {['#10a37f', '#d97706', '#4285f4', '#4b3c7a'].map((bg, i) => (
                <span key={i} className='remote-desktop-agent' style={{ background: bg, color: '#fff', fontSize: '7px', fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  {['O','C','G','D'][i]}
                </span>
              ))}
              <span className='remote-desktop-agent-more'>+46</span>
            </div>
          </div>
        </div>

        <div className='remote-screenshot'>
          <img
            src={`${L}remote-telegram.webp`}
            alt={t('Remote Telegram Control')}
            style={{ width: '100%', height: 'auto', display: 'block' }}
          />
        </div>
      </div>
    </section>
  )
}
