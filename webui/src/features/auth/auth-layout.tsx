/*
Copyright (C) 2023-2026 QuantumNous

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

For commercial licensing, please contact support@quantumnous.com
*/
import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { useSystemConfig } from '@/hooks/use-system-config'
import { Skeleton } from '@/components/ui/skeleton'
import { PoundingHeartLogo } from '@/features/home/components/pounding-heart-logo'
import { Bot, Clock, ShieldCheck } from 'lucide-react'

type AuthLayoutProps = {
  children: React.ReactNode
}

export function AuthLayout({ children }: AuthLayoutProps) {
  const { t } = useTranslation()
  const { systemName, logo, loading } = useSystemConfig()

  return (
    <div className='flex min-h-svh'>
      {/* Brand panel - hidden on mobile */}
      <div className='relative hidden w-[480px] shrink-0 flex-col justify-between overflow-hidden bg-zinc-950 p-10 lg:flex'>
        {/* Background gradients — POUNDING purple tones */}
        <div
          aria-hidden
          className='pointer-events-none absolute inset-0'
          style={{
            background: [
              'radial-gradient(ellipse 80% 50% at 20% 50%, rgba(117,131,178,0.35) 0%, transparent 60%)',
              'radial-gradient(ellipse 40% 30% at 80% 20%, rgba(150,120,180,0.2) 0%, transparent 60%)',
            ].join(', '),
          }}
        />

        {/* Logo — POUNDING heart */}
        <Link to='/' className='relative z-10 flex items-center gap-3 transition-opacity hover:opacity-80'>
          <PoundingHeartLogo size={36} compact />
          <span style={{ color: '#fff', fontSize: 18, fontWeight: 600, letterSpacing: '-0.3px' }}>
            POUNDING 胖丁
          </span>
        </Link>

        {/* Middle content */}
        <div className='relative z-10'>
          <div className='mb-3 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1'>
            <Bot className='size-3.5 text-purple-300' />
            <span className='text-[11px] font-medium tracking-widest uppercase text-white/50'>
              专注于电商及办公的 Agent 基座
            </span>
          </div>
          <h2 className='text-2xl leading-tight font-bold tracking-tight text-white'>
            一个桌面，你的 AI Agent 们，真的在协作。
          </h2>
          <p className='mt-3 text-sm leading-relaxed text-white/50'>
            自动发现 Claude Code、Codex、Gemini CLI。并行运行、组队协作、远程指挥——24/7 不停。
          </p>

          {/* Feature highlights */}
          <div className='mt-8 space-y-4'>
            {[
              { icon: <Bot className='size-4' />, text: '20+ 内置助手，开箱即用' },
              { icon: <Clock className='size-4' />, text: 'Cron 定时任务，24/7 自动运行' },
              { icon: <ShieldCheck className='size-4' />, text: '直连 API，数据不经过中转' },
            ].map((item) => (
              <div key={item.text} className='flex items-center gap-3 text-sm text-white/40'>
                <div className='flex size-7 items-center justify-center rounded-lg bg-white/5 text-white/50'>
                  {item.icon}
                </div>
                {item.text}
              </div>
            ))}
          </div>
        </div>

        {/* Footer */}
        <div className='relative z-10'>
          <p className='text-xs text-white/25'>
            &copy; {new Date().getFullYear()} POUNDING 胖丁
          </p>
        </div>
      </div>

      {/* Form panel */}
      <div className='flex flex-1 items-center justify-center px-4 py-10 sm:px-8 bg-[#f5f5f5] dark:bg-zinc-900 dark:text-white'>
        <div className='mx-auto flex w-full flex-col justify-center space-y-6 sm:w-[420px]'>
          {/* POUNDING branding — above form, visible on all screens */}
          <div className='flex flex-col items-center gap-3 lg:hidden'>
            <PoundingHeartLogo size={48} />
            <div className='text-center'>
              <div style={{ fontSize: 20, fontWeight: 700, color: '#13111c', letterSpacing: '-0.5px' }}>POUNDING 胖丁</div>
              <div style={{ fontSize: 13, color: '#6b6575', marginTop: 2 }}>专注于电商及办公的 Agent 基座</div>
            </div>
          </div>
          {children}
        </div>
      </div>
    </div>
  )
}
