/*
Copyright (C) 2023-2026 QuantumNous
This program is free software... (GNU AGPL v3)
For commercial licensing, please contact support@quantumnous.com
*/
import { useState, useEffect, useCallback } from 'react'

// vite base 前缀（生产 /app/）；landing 静态资源必须带前缀否则 404
const L = `${import.meta.env.BASE_URL ?? ''}landing/`
import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

interface HeroNewProps { isAuthenticated?: boolean }

const ROTATING_WORDS = [
  { word: '协作', demo: 'guid' },
  { word: '写代码', demo: 'claude' },
  { word: '做 PPT', demo: 'ppt' },
  { word: '组队干活', demo: 'team' },
  { word: '整理文件', demo: 'codex' },
  { word: '算数据', demo: 'excel' },
  { word: '24/7 运行', demo: 'scheduled' },
  { word: '修图', demo: 'gemini' },
  { word: '写论文', demo: 'paper' },
]

const MODEL_AVATARS = [
  { name: 'OpenAI', img: `${L}morph-ppt.jpg`, letter: 'O' },
  { name: 'Claude', img: `${L}excel-creator.jpg`, letter: 'C' },
  { name: 'Gemini', img: `${L}word-creator.jpg`, letter: 'G' },
  { name: 'DeepSeek', img: `${L}ppt-creator.jpg`, letter: 'D' },
  { name: 'Qwen', img: `${L}dashboard-creator.jpg`, letter: 'Q' },
  { name: 'Llama', img: `${L}story-roleplay.jpg`, letter: 'L' },
  { name: 'Mistral', img: `${L}academic-paper.jpg`, letter: 'M' },
  { name: 'Cohere', img: `${L}ui-ux-pro-max.jpg`, letter: 'C' },
  { name: 'xAI', img: `${L}financial-model-creator.jpg`, letter: 'X' },
  { name: '+More', img: `${L}pitch-deck-creator.jpg`, letter: '+' },
]

export function HeroNew({ isAuthenticated }: HeroNewProps) {
  const { t } = useTranslation()
  const [activeIndex, setActiveIndex] = useState(0)
  const [exitingIndex, setExitingIndex] = useState<number | null>(null)
  const [visible, setVisible] = useState(false)

  // Mark as visible on mount (hero shows immediately, no scroll reveal needed)
  useEffect(() => { setVisible(true) }, [])

  const cycle = useCallback(() => {
    const next = (activeIndex + 1) % ROTATING_WORDS.length
    setExitingIndex(activeIndex)
    setTimeout(() => {
      setActiveIndex(next)
      setExitingIndex(null)
    }, 550)
  }, [activeIndex])

  useEffect(() => {
    const timer = setInterval(cycle, 3000)
    return () => clearInterval(timer)
  }, [cycle])

  // Highlight the agent avatar matching current word
  const activeDemo = ROTATING_WORDS[activeIndex].demo

  return (
    <section className='hero' id='hero'>
      <div className='hero-inner'>
        {/* Section label with dashes */}
        <p className={`section-label reveal ${visible ? 'visible' : ''}`}>
          {t('AI API Gateway')}
        </p>

        {/* Main heading — matching AionUi structure exactly */}
        <h1 className={`section-title reveal ${visible ? 'visible' : ''}`}>
          <span className='hero-lead'>{t('一个桌面。你的 AI Agent 们，')} </span>
          <span className='hero-tail'>
            {' '}{t('真的在协作。')}{' '}
            <em>
              <span className='hero-rotating-wrap' id='heroRotator' style={{ minWidth: 208, overflow: 'visible' }}>
                {ROTATING_WORDS.map((w, i) => {
                  let cls = 'hero-rotating-word'
                  if (i === activeIndex && exitingIndex === null) cls += ' is-active'
                  if (i === exitingIndex) cls += ' is-exiting'
                  return (
                    <span key={w.word} className={cls} data-demo={w.demo} style={{ width: 'max-content' }}>
                      {w.word}
                    </span>
                  )
                })}
              </span>
            </em>
          </span>
        </h1>

        {/* Subtitle */}
        <p className={`section-sub reveal reveal-delay-1 ${visible ? 'visible' : ''}`}>
          {t('它们住在你电脑上。单独派活、组队协作，或远程指挥——事情 24 小时不停往前推。')}
        </p>

        {/* CTA row — matching AionUi hero-cta structure */}
        <div className={`hero-cta reveal reveal-delay-1 ${visible ? 'visible' : ''}`}>
          {/* Primary CTA button */}
          {isAuthenticated ? (
            <Link to='/' className='btn-primary' id='hero-download'>
              <svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2.5' strokeLinecap='round' strokeLinejoin='round'>
                <path d='M5 12h14' /><path d='m12 5 7 7-7 7' />
              </svg>
              {t('Go to Dashboard')}
            </Link>
          ) : (
            <Link to='/sign-up' className='btn-primary' id='hero-download'>
              <svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2.5' strokeLinecap='round' strokeLinejoin='round'>
                <path d='M5 12h14' /><path d='m12 5 7 7-7 7' />
              </svg>
              {t('免费开始使用')}
            </Link>
          )}

          {/* Agent avatars — matching AionUi hero-agents */}
          <div className='hero-agents' id='heroAgents' aria-hidden='true'>
            {MODEL_AVATARS.map((av, i) => (
              <img
                key={av.name}
                className={`hero-agent-av ${av.name.toLowerCase() === activeDemo ? 'active' : ''}`}
                data-agent={av.name.toLowerCase()}
                src={av.img}
                alt={av.name}
                width={34}
                height={34}
                loading='lazy'
                style={{ zIndex: MODEL_AVATARS.length - i }}
                title={av.name}
              />
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
