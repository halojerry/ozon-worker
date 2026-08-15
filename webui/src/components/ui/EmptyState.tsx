/**
 * EmptyState — 空态基元（M2.4 组件抽象基线）
 * 图标 + 标题 + 描述 + 动作，消费 .empty-state 家族（token 驱动）。
 * 统一散落在各页面的空态结构（ImageStudio / OnSale / Stores 等）。
 */

export interface EmptyStateProps {
  icon?: React.ReactNode
  title?: string
  description?: string
  action?: React.ReactNode
  className?: string
}

export default function EmptyState({ icon, title, description, action, className = '' }: EmptyStateProps) {
  const classes = ['empty-state', className].filter(Boolean).join(' ')
  return (
    <div className={classes}>
      {icon && (
        <div className="placeholder-icon" aria-hidden="true">
          {icon}
        </div>
      )}
      {title && <p className="empty-state-title">{title}</p>}
      {description && <p className="empty-state-text">{description}</p>}
      {action}
    </div>
  )
}
