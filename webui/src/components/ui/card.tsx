import type { HTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/cn'

/**
 * Card —— spec §06 卡片容器
 * 圆角 10px（radius.card）+ 阴影 `0 1px 2px rgba(17,17,17,.04)`（shadow.card）
 * 可选 `title`（h3 卡片标题）+ `action`（右上操作区）。
 */
export interface CardProps extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  title?: ReactNode
  action?: ReactNode
  /** 卡片主体是否需要内边距（表格类卡片传 false 铺满） */
  padded?: boolean
}

export function Card({ title, action, padded = true, className, children, ...props }: CardProps) {
  return (
    <div
      className={cn('rounded-card border border-line bg-surface shadow-card', className)}
      {...props}
    >
      {title != null && (
        <div className="flex items-center justify-between gap-3 px-inset-card pb-3 pt-inset-card">
          <h3 className="text-h3 text-ink">{title}</h3>
          {action != null && <div className="shrink-0">{action}</div>}
        </div>
      )}
      <div className={cn(padded && 'px-inset-card pb-inset-card')}>{children}</div>
    </div>
  )
}

/**
 * Panel —— 大容器（radius.panel 12px），用于侧栏面板 / 弹层类区域
 */
export function Panel({ className, children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('rounded-panel border border-line bg-surface shadow-raised', className)}
      {...props}
    >
      {children}
    </div>
  )
}
