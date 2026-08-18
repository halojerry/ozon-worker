import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

/**
 * Metric —— spec §06 指标卡
 * - `val`：data-lg 等宽数字（28/700 mono），`accent` 时品牌红（KPI 强调）
 * - `delta`：涨跌，上涨红色正号（#B30C0C）/ 下跌深灰负号，避免满屏红
 */
export interface MetricDelta {
  /** 前缀说明，如「较昨日」 */
  label?: string
  /** 涨跌值，如「+12.4%」/「-0.3%」 */
  value: string
  direction: 'up' | 'down' | 'flat'
}

export interface MetricProps {
  label: ReactNode
  value: ReactNode
  /** KPI 大数字红色强调 */
  accent?: boolean
  delta?: MetricDelta
  /** 角标（可选，如 "今日" 周期） */
  meta?: ReactNode
  className?: string
  onClick?: () => void
}

export function Metric({ label, value, accent = false, delta, meta, className, onClick }: MetricProps) {
  const Comp = onClick ? 'button' : 'div'
  return (
    <Comp
      onClick={onClick}
      className={cn(
        'rounded-card border border-line bg-surface px-[18px] py-4 text-left shadow-card',
        onClick && 'transition-shadow duration-fast ease-standard hover:shadow-raised',
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] text-ink-aux">{label}</span>
        {meta != null && <span className="text-[11px] text-ink-4">{meta}</span>}
      </div>
      <div className={cn('mt-1 font-mono text-[28px] font-bold leading-tight tracking-tight', accent ? 'text-accent' : 'text-ink')}>
        {value}
      </div>
      {delta && (
        <div className="mt-0.5 text-[11px] text-ink-aux">
          {delta.label != null && <span>{delta.label} </span>}
          <b className={cn('font-semibold', delta.direction === 'down' ? 'text-ink-3' : 'text-accent-dark')}>
            {delta.direction === 'flat' ? '—' : delta.value}
          </b>
        </div>
      )}
    </Comp>
  )
}
