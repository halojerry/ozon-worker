import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

/**
 * Button —— spec §06 按钮实样
 * 6 种状态：pri（主）/ sec（次）/ ghost（幽灵）/ danger（危险）/ disable / loading
 *
 * - 主按钮：红底白字，hover 深红 #B30C0C，按压下移 1px（100ms 即时反馈）
 * - 危险按钮：白底红框红字（二次确认类操作）
 * - loading：内嵌旋转指示器（文字隐藏），不响应指针
 * - disabled：灰底灰字，不响应 hover
 */

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  loading?: boolean
}

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    'bg-accent text-white hover:bg-accent-dark active:bg-accent-dark active:translate-y-px',
  secondary:
    'bg-surface border border-line text-ink hover:border-ink-3 hover:bg-header active:bg-header',
  ghost:
    'bg-transparent text-ink-3 hover:text-ink hover:bg-neutral-bg active:bg-neutral-bg',
  danger:
    'bg-surface border border-accent text-accent-dark hover:bg-accent-soft active:bg-accent-soft',
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', loading = false, className, disabled, children, type = 'button', ...props },
  ref,
) {
  const isDisabled = disabled || loading
  return (
    <button
      ref={ref}
      type={type}
      disabled={isDisabled}
      className={cn(
        'inline-flex h-9 select-none items-center justify-center gap-2 rounded-button px-[18px]',
        'text-[13px] font-medium leading-none whitespace-nowrap',
        'transition-[background-color,border-color,color,transform] duration-fast ease-standard',
        'focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2',
        variantClasses[variant],
        isDisabled && 'pointer-events-none bg-badge-neutral text-ink-5 border-transparent',
        loading && 'relative text-transparent',
        className,
      )}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading && (
        <span
          aria-hidden
          className="absolute left-1/2 top-1/2 size-[14px] -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white/35 border-t-white animate-spin-slow"
        />
      )}
      {children}
    </button>
  )
})
