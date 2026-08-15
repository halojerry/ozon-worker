/**
 * Badge — 状态徽章基元（M2.4 组件抽象基线）
 * 语义色映射，替代页面里散落的 status-failed / badge-ok / badge-fail 等 class 拼串。
 * 全部映射到现有 .badge-* 家族（token 驱动），供后续页面渐进迁移。
 */

export type BadgeVariant =
  | 'default'
  | 'success'
  | 'danger'
  | 'warning'
  | 'muted'
  | 'currency'
  | 'platform'
  | 'follow'

export interface BadgeProps {
  variant?: BadgeVariant
  children: React.ReactNode
  className?: string
  title?: string
}

const VARIANT_CLASS: Record<BadgeVariant, string> = {
  default: 'badge badge-default',
  success: 'badge badge-ok',
  danger: 'badge badge-fail',
  warning: 'badge badge-warning',
  muted: 'badge status-muted',
  currency: 'badge badge-currency',
  platform: 'badge badge-platform',
  follow: 'badge badge-follow',
}

export default function Badge({ variant = 'default', children, className = '', title }: BadgeProps) {
  const classes = [VARIANT_CLASS[variant], className].filter(Boolean).join(' ')
  return (
    <span className={classes} title={title}>
      {children}
    </span>
  )
}
