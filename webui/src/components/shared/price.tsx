import { cn } from '@/lib/cn'
import { formatCurrency } from '@/lib/format'

/**
 * Price —— 金额展示（data-md 等宽数字，spec §03）
 * `emphasis` 时用深红（#B30C0C，正文级红色对比安全）。
 */
export interface PriceProps {
  value: number | string | null | undefined
  currency?: string
  emphasis?: boolean
  className?: string
}

export function Price({ value, currency = 'CNY', emphasis = false, className }: PriceProps) {
  return (
    <span
      className={cn(
        'font-mono text-[13px] font-semibold tabular-nums',
        emphasis ? 'text-accent-dark' : 'text-ink',
        className,
      )}
    >
      {formatCurrency(value, currency)}
    </span>
  )
}
