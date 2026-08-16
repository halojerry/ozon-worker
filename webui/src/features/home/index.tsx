/*
Copyright (C) 2023-2026 QuantumNous
For commercial licensing, please contact support@quantumnous.com
*/
import { useTranslation } from 'react-i18next'
import { Markdown } from '@/components/ui/markdown'
import { useHomePageContent } from './hooks'
import { LandingPage } from './components/landing-page'

export function Home() {
  const { t } = useTranslation()
  const { content, isLoaded, isUrl } = useHomePageContent()

  if (!isLoaded) return <div style={{ display:'flex', minHeight:'100vh', alignItems:'center', justifyContent:'center', background:'#f5f5f5', color:'#6b6575' }}>{t('Loading...')}</div>

  if (content) return isUrl
    ? <iframe src={content} style={{ width:'100%', height:'100vh', border:'none' }} title={t('Custom Home Page')} />
    : <div className='container mx-auto py-8' style={{ background:'#f5f5f5' }}><Markdown>{content}</Markdown></div>

  return <LandingPage />
}
