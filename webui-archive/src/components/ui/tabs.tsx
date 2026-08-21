import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

/**
 * Tabs —— spec §06 下划线式 Tab
 * 选中态：红色下划线 #E20E0E + 600 字重；hover 文字变深；100ms 过渡。
 */
export interface TabItem {
  key: string
  label: ReactNode
  /** 附加计数（显示在标签后的小数字） */
  count?: number
}

export interface TabsProps {
  items: TabItem[]
  value: string
  onChange?: (key: string) => void
  className?: string
}

export function Tabs({ items, value, onChange, className }: TabsProps) {
  return (
    <div role="tablist" className={cn('flex gap-1 border-b border-line', className)}>
      {items.map((item) => {
        const active = item.key === value
        return (
          <button
            key={item.key}
            role="tab"
            aria-selected={active}
            type="button"
            onClick={() => onChange?.(item.key)}
            className={cn(
              'flex items-center gap-1.5 border-b-2 px-4 py-2 text-[13px]',
              'transition-colors duration-fast ease-standard focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-[-2px]',
              active ? 'border-accent font-semibold text-ink' : 'border-transparent text-ink-aux hover:text-ink',
            )}
          >
            {item.label}
            {item.count != null && (
              <span className={cn('font-mono text-[11px]', active ? 'text-accent-dark' : 'text-ink-4')}>
                {item.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
