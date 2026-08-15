/**
 * Button — 按钮基元（M2.4 组件抽象基线）
 * 消费现有 .btn/.btn-primary/.btn-ghost/.btn-danger 类（全部 token 驱动），
 * 供后续页面渐进迁移使用；现有页面不受影响。
 */

type ButtonVariant = 'primary' | 'ghost' | 'danger'
type ButtonSize = 'sm' | 'md'

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
}

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  primary: 'btn btn-primary',
  ghost: 'btn btn-ghost',
  danger: 'btn btn-danger',
}

const SIZE_CLASS: Record<ButtonSize, string> = {
  sm: 'btn-small',
  md: '',
}

export default function Button({
  variant = 'primary',
  size = 'md',
  className = '',
  type = 'button',
  ...rest
}: ButtonProps) {
  const classes = [VARIANT_CLASS[variant], SIZE_CLASS[size], className].filter(Boolean).join(' ')
  return <button type={type} className={classes} {...rest} />
}
