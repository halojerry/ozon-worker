import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from 'react'
import { cn } from '@/lib/cn'

/**
 * Input —— spec §06 输入框实样
 * 4 种状态：standard / focus（红边框 + 红光晕）/ error（红框 + 红浅底）/ disabled
 * 可选 `leading`（前缀图标/字符）、`hint`（下方提示，error 时深红）。
 */
export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  leading?: ReactNode
  hint?: ReactNode
  error?: boolean | string
  wrapperClassName?: string
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { leading, hint, error = false, wrapperClassName, className, id, disabled, ...props },
  ref,
) {
  const autoId = useId()
  const inputId = id ?? autoId
  const errorMessage = typeof error === 'string' ? error : undefined
  const hasError = Boolean(error)

  return (
    <div className={cn('w-full max-w-[320px]', wrapperClassName)}>
      <div
        className={cn(
          'flex h-9 items-center gap-2 rounded-input border bg-surface px-3',
          'transition-[border-color,box-shadow,background-color] duration-fast ease-standard',
          hasError
            ? 'border-accent bg-badge-accent'
            : 'border-line focus-within:border-accent focus-within:shadow-glow',
          disabled && 'pointer-events-none border-neutral-bg bg-badge-neutral',
        )}
      >
        {leading != null && (
          <span className={cn('shrink-0 text-[12px]', hasError ? 'text-accent-dark' : disabled ? 'text-ink-5' : 'text-ink-5')}>
            {leading}
          </span>
        )}
        <input
          ref={ref}
          id={inputId}
          disabled={disabled}
          className={cn(
            'h-full w-full min-w-0 bg-transparent text-[13px] text-ink outline-none',
            'placeholder:text-ink-5',
            disabled && 'cursor-not-allowed text-ink-5 placeholder:text-ink-5',
            className,
          )}
          aria-invalid={hasError || undefined}
          aria-describedby={hint ? `${inputId}-hint` : undefined}
          {...props}
        />
      </div>
      {hint && (
        <p
          id={`${inputId}-hint`}
          className={cn('mt-1.5 max-w-[320px] text-[11px] leading-normal', hasError ? 'text-accent-dark' : 'text-ink-aux')}
        >
          {errorMessage ?? hint}
        </p>
      )}
    </div>
  )
})
