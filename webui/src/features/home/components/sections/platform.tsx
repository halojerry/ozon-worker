/*
Copyright (C) 2023-2026 QuantumNous — GNU AGPL v3
Platform section — Remote Control flow diagram style
*/
import { useEffect, useRef, useState } from 'react'

// vite base 前缀（生产 /app/）；landing 静态资源必须带前缀否则 404
const L = `${import.meta.env.BASE_URL ?? ''}landing/`
import { useTranslation } from 'react-i18next'

const CHANNELS = [
  { name: 'Telegram', src: '/channel-logos/telegram.svg', bg: '#2AABEE' },
  { name: 'Discord', src: '/channel-logos/discord.svg', bg: '#5865F2' },
  { name: 'Slack', src: '/channel-logos/slack.svg', bg: '#4A154B' },
  { name: 'Lark', src: '/channel-logos/lark.svg', bg: '#3370FF' },
  { name: 'WebUI', bg: '#7583b2', icon: true },
]

const AGENT_AVATARS = [
  `${L}ppt-creator.jpg`,
  `${L}excel-creator.jpg`,
  `${L}word-creator.jpg`,
]

export function Platform() {
  const ref = useRef<HTMLElement>(null)
  const [paused, setPaused] = useState(true)

  useEffect(() => {
    const el = ref.current; if (!el) return
    const obs = new IntersectionObserver(
      ([e]) => setPaused(!e.isIntersecting),
      { threshold: 0.3 }
    )
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  return (
    <section ref={ref} className='remote-section reveal visible' id='platform'>
      <div className='remote-inner'>
        <p className='section-label'><span>远程控制</span></p>
        <h2 className='section-title'>离开座位，<br />没离开控制台。</h2>
        <p className='section-sub'>
          用 Telegram、Discord、Slack、飞书或内置 WebUI 发指令。Agent 在你电脑上 24/7 运行——走到哪里都能控。
        </p>

        {/* Flow diagram: Phone → Channels → Desktop */}
        <div className='remote-flow'>
          {/* Phone pill */}
          <div className='remote-pill'>
            <div className='remote-phone-icon'>
              <svg width='22' height='22' viewBox='0 0 24 24' fill='none' stroke='#7583b2' strokeWidth='2'><rect x='5' y='2' width='14' height='20' rx='3'/><path d='M12 18h.01'/></svg>
            </div>
            <div className='remote-pill-label'>随时随地</div>
          </div>

          {/* Dashed line */}
          <div className='remote-flow-line' /><span className='remote-flow-dot' />

          {/* Channels */}
          <div className='remote-channels'>
            <div className='remote-channel-title'>任意渠道</div>
            <div className='remote-channel-row'>
              {CHANNELS.map(ch => (
                <span key={ch.name} className='remote-ch-icon' title={ch.name} style={{ background: ch.bg }}>
                  {ch.icon
                    ? <svg width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='#fff' strokeWidth='2'><rect x='3' y='4' width='18' height='14' rx='2'/><path d='M8 21h8'/><path d='M12 17v4'/></svg>
                    : <img src={ch.src} alt={ch.name} style={{ width: 20, height: 20, filter: 'brightness(0) invert(1)' }} />
                  }
                </span>
              ))}
            </div>
          </div>

          {/* Dashed line */}
          <div className='remote-flow-line' /><span className='remote-flow-dot' />

          {/* Desktop pill */}
          <div className='remote-pill'>
            <div className='remote-desktop-icon'>
              <svg width='22' height='22' viewBox='0 0 24 24' fill='none' stroke='#7583b2' strokeWidth='2'><rect x='2' y='3' width='20' height='14' rx='2'/><path d='M8 21h8'/><path d='M12 17v4'/></svg>
            </div>
            <div className='remote-pill-label'>你的电脑</div>
            <div className='remote-desktop-agents'>
              {AGENT_AVATARS.map((src, i) => (
                <img key={i} src={src} alt='' className='remote-desktop-agent' width={20} height={20} loading='lazy'
                  style={{ objectFit: 'cover', borderRadius: '50%' }} />
              ))}
              <span className='remote-desktop-agent-more'>+12</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
