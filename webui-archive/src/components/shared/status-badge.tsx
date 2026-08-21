import { Badge } from '@/components/ui/badge'
import { resolveStatusMap } from '@/lib/constants'

/**
 * StatusBadge —— 状态映射（任务/商品/订单/审核 四类映射见 lib/constants.ts）
 * 未知状态回退为中性徽标，label 直接显示原始值。
 */
export interface StatusBadgeProps {
  status?: string | null
  /** 状态映射表（默认任务状态） */
  map?: Record<string, { label: string; variant: 'neutral' | 'red' | 'dark' }>
  className?: string
}

export function StatusBadge({ status, map, className }: StatusBadgeProps) {
  const resolved = resolveStatusMap(map ?? {}, status)
  return (
    <Badge variant={resolved.variant} className={className}>
      {resolved.label}
    </Badge>
  )
}
