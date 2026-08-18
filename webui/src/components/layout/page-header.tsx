import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

/**
 * PageHeader —— 页面报头（spec §03 排版 + 高留白）
 * kicker（眉题 11/600）+ h1 页面标题 + sub 说明 + 右侧操作区。
 */
export interface PageHeaderProps {
  kicker?: ReactNode
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  className?: string
}

export function PageHeader({ kicker, title, description, actions, className }: PageHeaderProps) {
  return (
    <div className={cn('mb-6 flex flex-wrap items-end justify-between gap-4', className)}>
      <div className="min-w-0">
        {kicker != null && (
          <div className="mb-1.5 text-overline text-ink-aux">{kicker}</div>
        )}
        <h1 className="text-h1 text-ink">{title}</h1>
        {description != null && (
          <p className="mt-1.5 max-w-[640px] text-body-sm text-ink-3">{description}</p>
        )}
      </div>
      {actions != null && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}
