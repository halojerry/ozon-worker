import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

/**
 * Badge —— spec §06 徽标实样（3 态）
 * - neutral：灰底 #F1EFEA + 灰字（已上架 / 已完成）
 * - red：红浅底 #FDEBEB + 深红字 #B30C0C（待上架 / 告警 / 待处理）
 * - dark：黑底白字（平台侧 / 独立全屏）
 */
export type BadgeVariant = 'neutral' | 'red' | 'dark'

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant
}

const variantClasses: Record<BadgeVariant, string> = {
  neutral: 'bg-badge-neutral text-ink-3',
  red: 'bg-badge-accent text-accent-dark',
  dark: 'bg-ink text-on-dark',
}

export function Badge({ variant = 'neutral', className, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex h-[22px] items-center whitespace-nowrap rounded-badge px-2.5 text-[11px] font-medium',
        variantClasses[variant],
        className,
      )}
      {...props}
    />
  )
}

/**
 * Tag —— spec §06 标签（细边框 4px 圆角，字段级）
 */
export interface TagProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: 'default' | 'red'
}

export function Tag({ variant = 'default', className, ...props }: TagProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center whitespace-nowrap rounded-[4px] border px-2.5 py-px text-[11px]',
        variant === 'default'
          ? 'border-line bg-surface text-ink-3'
          : 'border-accent bg-badge-accent text-accent-dark',
        className,
      )}
      {...props}
    />
  )
}
