/*
Copyright (C) 2023-2026 QuantumNous

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.
...
For commercial licensing, please contact support@quantumnous.com
*/
import { useEffect, useState, useRef } from 'react'
import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import i18next from 'i18next'
import { useAuthStore } from '@/stores/auth-store'
import { HeroNew } from './sections/hero-new'
import { Cowork } from './sections/cowork'
import { Demo } from './sections/demo'
import { Assistants } from './sections/assistants'
import { Cron } from './sections/cron'
import { Platform } from './sections/platform'
import { Faq } from './sections/faq'
import { CtaFinal } from './sections/cta-final'
import { PoundingHeartLogo } from './pounding-heart-logo'

export function LandingPage() {
  const { t } = useTranslation()
  const { auth } = useAuthStore()
  const isAuth = !!auth.user
  const [scrolled, setScrolled] = useState(false)
  const [langOpen, setLangOpen] = useState(false)
  const currentLang = i18next.language?.startsWith('zh') ? 'zh' : 'en'

  const switchLang = (lang: string) => {
    i18next.changeLanguage(lang)
    setLangOpen(false)
  }

  // Scroll detection for nav glass effect
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // Reveal animations on scroll
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach(entry => {
          if (entry.isIntersecting) entry.target.classList.add('visible')
        })
      },
      { threshold: 0.1 }
    )
    // Observe all reveal elements — those already in viewport will fire immediately
    document.querySelectorAll('.reveal').forEach(el => observer.observe(el))
    // Also immediately show all reveal elements (failsafe — in case observer misses)
    setTimeout(() => {
      document.querySelectorAll('.reveal').forEach(el => el.classList.add('visible'))
    }, 500)
    return () => observer.disconnect()
  }, [])

  return (
    <div style={{ background: '#f5f5f5', color: '#13111c', minHeight: '100vh' }}>
      {/* ════════════════════════════ Sticky Navigation ════════════════════════════ */}
      <nav id='siteNav' className={scrolled ? 'is-scrolled' : ''}>
        {/* Left: Logo + GitHub stars */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexShrink: 0 }}>
          <Link to='/' aria-label='POUNDING home' style={{
            display: 'flex', alignItems: 'center', gap: '6px',
            textDecoration: 'none', color: 'inherit',
          }}>
            <PoundingHeartLogo size={28} compact />
            <span style={{ fontSize: '16.5px', fontWeight: 600, color: 'var(--ink)', letterSpacing: '-0.3px' }}>
              POUNDING 胖丁
            </span>
          </Link>
        </div>

        {/* Center: Nav links */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '32px', margin: '0 auto' }}>
          {[
            ['#cowork', t('Agent 协作')],
            ['#assistants', t('内置助手')],
            ['#remote', t('远程控制')],
            ['#automation', t('自动化')],
            ['#platform', t('平台')],
          ].map(([href, label]) => (
            <a key={href} href={href} style={{
              fontSize: '13.5px', fontWeight: 450, color: 'var(--ink-70)',
              textDecoration: 'none', transition: 'color 0.2s',
            }}>{label}</a>
          ))}
        </div>

        {/* Right: Lang + CTA */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}>
          {/* Language switcher */}
          <div style={{ position: 'relative' }}>
            <button
              onClick={() => setLangOpen(!langOpen)}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 4,
                padding: '5px 10px', borderRadius: 100,
                fontSize: '12px', fontWeight: 500, color: 'var(--ink-50)',
                background: 'transparent', border: '1px solid rgba(19,17,28,0.08)',
                cursor: 'pointer', fontFamily: 'inherit',
              }}
            >
              <svg width='13' height='13' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2'><circle cx='12' cy='12' r='10'/><path d='M2 12h20'/><path d='M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z'/></svg>
              {currentLang === 'zh' ? '中文' : 'EN'}
            </button>
            {langOpen && (
              <div style={{
                position: 'absolute', top: '100%', right: 0, marginTop: 6,
                background: '#fff', borderRadius: 10,
                boxShadow: '0 4px 20px rgba(0,0,0,0.12)', overflow: 'hidden',
                zIndex: 200, minWidth: 100,
              }}>
                {[
                  { code: 'zh', label: '中文' },
                  { code: 'en', label: 'English' },
                ].map(l => (
                  <button
                    key={l.code}
                    onClick={() => switchLang(l.code)}
                    style={{
                      display: 'block', width: '100%', padding: '8px 16px',
                      fontSize: '13px', color: currentLang === l.code ? 'var(--plum)' : 'var(--ink-70)',
                      fontWeight: currentLang === l.code ? 600 : 400,
                      background: currentLang === l.code ? '#f4f2f9' : 'transparent',
                      border: 'none', cursor: 'pointer', fontFamily: 'inherit',
                      textAlign: 'left',
                    }}
                  >
                    {l.label}
                  </button>
                ))}
              </div>
            )}
          </div>
          {/* Close overlay when clicking outside */}
          {langOpen && <div style={{ position: 'fixed', inset: 0, zIndex: 199 }} onClick={() => setLangOpen(false)} />}

          {/* Download button — always visible */}
          <Link to='/download' style={{
            display: 'inline-flex', alignItems: 'center', gap: '4px',
            padding: '8px 18px', borderRadius: '100px',
            fontSize: '13.5px', fontWeight: 450, color: 'var(--ink-70)',
            textDecoration: 'none', transition: 'all 0.2s',
            background: 'transparent', border: '1px solid rgba(19,17,28,0.1)',
          }}>
            <svg width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2'><path d='M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4'/><polyline points='7 10 12 15 17 10'/><line x1='12' y1='15' x2='12' y2='3'/></svg>
            下载
          </Link>

          {isAuth ? (
            <Link to='/dashboard' className='btn-primary' style={{
              padding: '8px 18px', fontSize: '13.5px', textDecoration: 'none',
              animation: 'none', boxShadow: '0 1px 2px rgba(19,17,28,0.08), 0 4px 12px rgba(75,60,122,0.18)', borderRadius: '100px', display: 'inline-flex', alignItems: 'center', gap: '6px', fontWeight: 500,
              background: 'var(--ink)', color: 'var(--canvas)',
            }}>
              {t('Dashboard')}
            </Link>
          ) : (
            <Link to='/sign-up' className='btn-primary' style={{
              padding: '8px 18px', fontSize: '13.5px', textDecoration: 'none',
              animation: 'none', boxShadow: '0 1px 2px rgba(19,17,28,0.08), 0 4px 12px rgba(75,60,122,0.18)', borderRadius: '100px', display: 'inline-flex', alignItems: 'center', gap: '6px', fontWeight: 500,
              background: 'var(--ink)', color: 'var(--canvas)',
            }}>
              免费注册
            </Link>
          )}
        </div>
      </nav>

      {/* ════════════════════════════ Sections ════════════════════════════ */}
      <HeroNew isAuthenticated={isAuth} />
      <Demo />
      <Cowork />
      <Assistants />
      <Cron />
      <Platform />
      <Faq />
      <CtaFinal isAuthenticated={isAuth} />

      {/* ════════════════════════════ Footer ════════════════════════════ */}
      <footer style={{
        padding: '44px 60px', background: 'var(--ink)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        flexWrap: 'wrap', gap: '16px', fontSize: '13px',
        color: 'var(--ink-50)', borderTop: '1px solid rgba(250,249,246,0.06)',
      }}>
        <span>POUNDING 胖丁 &copy; {new Date().getFullYear()}. 专注于电商及办公的 Agent 基座。</span>
      </footer>

      {/* ════════════════════════════ Nav CSS ════════════════════════════ */}
      <style>{`
        #siteNav {
          position: sticky; top: 0; z-index: 100;
          display: flex; align-items: center;
          height: clamp(56px, 7vh, 64px);
          padding: 0 44px;
          background: rgba(255,255,255,0.82);
          backdrop-filter: saturate(170%) blur(20px);
          -webkit-backdrop-filter: saturate(170%) blur(20px);
          transition: background-color 0.3s ease;
        }
        #siteNav:after {
          content: "";
          position: absolute; left: 0; right: 0; bottom: 0; height: 1px;
          background: linear-gradient(to right, transparent 0%, rgba(19,17,28,0.07) 7%, rgba(19,17,28,0.07) 93%, transparent 100%);
        }
        #siteNav.is-scrolled {
          background: rgba(255,255,255,0.92);
        }
        #siteNav a:hover { color: var(--ink) !important; }
        @media (max-width: 900px) {
          #siteNav { padding: 0 24px; }
          #siteNav > div:nth-child(2) { display: none; }
        }
        @media (max-width: 767px) {
          #siteNav { height: 56px; padding: 0 16px; }
        }
        footer a:hover { color: var(--canvas) !important; }
      `}</style>
    </div>
  )
}
