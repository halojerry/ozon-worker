import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

/**
 * Empty —— spec §06 空态实样
 * 虚线框 + 一句话说明 + 可选可行动入口（不放插画）。
 */
export interface EmptyProps {
  title?: ReactNode
  description?: ReactNode
  /** 可行动入口（如「新建商品」按钮） */
  action?: ReactNode
  className?: string
}

export function Empty({ title = '暂无数据', description, action, className }: EmptyProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-2 rounded-card border border-dashed border-ink-5 px-5 py-7 text-center',
        className,
      )}
    >
      <div className="text-[13px] font-medium text-ink-3">{title}</div>
      {description != null && <div className="max-w-sm text-[12px] leading-relaxed text-ink-aux">{description}</div>}
      {action != null && <div className="mt-2">{action}</div>}
    </div>
  )
}
