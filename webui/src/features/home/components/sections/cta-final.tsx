/*
Copyright (C) 2023-2026 QuantumNous — GNU AGPL v3
*/
import { useEffect, useRef } from 'react'

// vite base 前缀（生产 /app/）；landing 静态资源必须带前缀否则 404
const L = `${import.meta.env.BASE_URL ?? ''}landing/`
import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

interface CtaFinalProps { isAuthenticated?: boolean }

const AVATARS = [
  { label: 'O', img: `${L}morph-ppt.jpg` },
  { label: 'C', img: `${L}excel-creator.jpg` },
  { label: 'G', img: `${L}word-creator.jpg` },
  { label: 'D', img: `${L}ui-ux-pro-max.jpg` },
  { label: 'Q', img: `${L}ppt-creator.jpg` },
  { label: 'L', img: `${L}dashboard-creator.jpg` },
  { label: 'M', img: `${L}academic-paper.jpg` },
  { label: 'R', img: `${L}story-roleplay.jpg` },
]

export function CtaFinal({ isAuthenticated }: CtaFinalProps) {
  const { t } = useTranslation()
  const refs = useRef<(HTMLDivElement | null)[]>([])

  // Random pop animation on avatars — matching AionUi cta-avatar.pop
  useEffect(() => {
    const tms: ReturnType<typeof setTimeout>[] = []
    refs.current.forEach((el, i) => {
      if (!el) return
      const pop = () => {
        el.classList.add('pop')
        setTimeout(() => el.classList.remove('pop'), 500)
        const delay = 800 + Math.random() * 1200 + i * 200
        tms.push(setTimeout(pop, delay))
      }
      tms.push(setTimeout(pop, 1000 + Math.random() * 2000 + i * 400))
    })
    return () => tms.forEach(clearTimeout)
  }, [])

  return (
    <section className='final-cta reveal' id='download-cta'>
      <h2 className='section-title'>
        {t('开工吧，')}<br />
        <em>{t('和你的 AI Agent 们。')}</em>
      </h2>

      <div className='cta-avatars'>
        {AVATARS.map((av, i) => (
          <div key={av.label} ref={el => { refs.current[i] = el }} className='cta-avatar'
            style={{ zIndex: AVATARS.length - i, background: '#1f1a2e' }}>
            <img src={av.img} alt={av.label} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
          </div>
        ))}
      </div>
      <p className='cta-avatars-label'>{t('20+ Agent，随时待命。')}</p>

      <div className='final-cta-buttons'>
        {isAuthenticated ? (
          <Link to='/' className='btn-primary' id='final-download'>
            <svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2.5' strokeLinecap='round' strokeLinejoin='round'>
              <path d='M5 12h14' /><path d='m12 5 7 7-7 7' />
            </svg>
            {t('Go to Dashboard')}
          </Link>
        ) : (
          <Link to='/sign-up' className='btn-primary' id='final-download'>
            <svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2.5' strokeLinecap='round' strokeLinejoin='round'>
              <path d='M5 12h14' /><path d='m12 5 7 7-7 7' />
            </svg>
            {t('Get Started Free')}
          </Link>
        )}
      </div>
    </section>
  )
}
