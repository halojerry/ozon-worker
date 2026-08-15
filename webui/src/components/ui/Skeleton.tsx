/**
 * Skeleton — 加载占位基元（M2.4 组件抽象基线）
 * 消费 .skeleton 类（color/radius/duration/easing 全套 token），
 * 供列表/卡片加载态渐进迁移；prefers-reduced-motion 下自动降级为静态底色。
 */

export interface SkeletonProps {
  width?: number | string
  height?: number | string
  circle?: boolean
  className?: string
  style?: React.CSSProperties
}

export default function Skeleton({ width, height = 14, circle = false, className = '', style }: SkeletonProps) {
  const classes = ['skeleton', circle ? 'circle' : '', className].filter(Boolean).join(' ')
  return <span className={classes} style={{ width, height, ...style }} aria-hidden="true" />
}
