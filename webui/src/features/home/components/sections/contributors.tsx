/*
Copyright (C) 2023-2026 QuantumNous — GNU AGPL v3
*/
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

interface Contributor {
  login: string
  avatar_url: string
  html_url: string
  contributions: number
}

export function Contributors() {
  const { t } = useTranslation()
  const [contributors, setContributors] = useState<Contributor[]>([])

  useEffect(() => {
    const ctrl = new AbortController()
    fetch('https://api.github.com/repos/Calcium-Ion/new-api/contributors?per_page=30', { signal: ctrl.signal })
      .then(r => r.json())
      .then(d => { if (Array.isArray(d)) setContributors(d) })
      .catch(() => {})
    return () => ctrl.abort()
  }, [])

  return (
    <section className='contributors-section reveal' id='contributors'>
      <div className='contributors-inner'>
        <h2 className='section-title'>{t('Powered by the open-source community')}</h2>
        <p className='section-sub'>{t('Join thousands of developers building the future of AI infrastructure.')}</p>

        <div className='contributors-badges'>
          <a href='https://github.com/Calcium-Ion/new-api' target='_blank' rel='noopener noreferrer'>
            <img src='https://img.shields.io/github/stars/Calcium-Ion/new-api?style=social' alt='GitHub stars' height={54} />
          </a>
        </div>

        <div className='contributors-grid' id='contributorsGrid'>
          {contributors.length > 0
            ? contributors.slice(0, 20).map(c => (
                <a key={c.login} href={c.html_url} target='_blank' rel='noopener noreferrer' className='contributor'>
                  <img src={c.avatar_url} alt={c.login} loading='lazy' width={52} height={52} />
                  <span className='contributor-name'>{c.login}</span>
                  <span className='contributor-commits'>{c.contributions} {t('commits')}</span>
                </a>
              ))
            : Array.from({ length: 10 }).map((_, i) => (
                <div key={i} className='contributor'>
                  <div style={{ width: 52, height: 52, borderRadius: '50%', background: 'var(--tint)', border: '2px solid #fff' }} />
                  <span className='contributor-name'>&nbsp;</span>
                </div>
              ))}
        </div>

        <a href='https://github.com/Calcium-Ion/new-api' target='_blank' rel='noopener noreferrer' className='contributors-link'>
          <svg width='16' height='16' viewBox='0 0 24 24' fill='currentColor'><path d='M12 0C5.37 0 0 5.37 0 12c0 5.3 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61-.546-1.385-1.335-1.755-1.335-1.755-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.605-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 21.795 24 17.295 24 12 24 5.37 18.63 0 12 0z'/></svg>
          {t('View all contributors on GitHub')} &rarr;
        </a>
      </div>
    </section>
  )
}
