import { useState } from 'react'
import { Image as ImagePlaceholderIcon } from 'lucide-react'
import { cn } from '@/lib/cn'

/**
 * ImageCell —— PRD §6.3 图片组件
 * 图片 URL 直接渲染（COS/CDN 服务），`loading="lazy"` 懒加载，
 * 加载失败 / 空 URL 显示占位底（bg.thumb #EAE8E3），不阻断页面。
 */
export type ImageSize = 'sm' | 'md' | 'lg'

export interface ImageCellProps {
  src?: string | null
  alt?: string
  size?: ImageSize
  className?: string
}

const sizeClasses: Record<ImageSize, string> = {
  sm: 'size-8 rounded-[4px]',
  md: 'size-12 rounded-[6px]',
  lg: 'size-16 rounded-card',
}

export function ImageCell({ src, alt = '', size = 'md', className }: ImageCellProps) {
  const [error, setError] = useState(false)

  if (!src || error) {
    return (
      <div
        className={cn(
          'flex shrink-0 items-center justify-center bg-thumb text-ink-4',
          sizeClasses[size],
          className,
        )}
        role="img"
        aria-label={alt || '图片不可用'}
      >
        <ImagePlaceholderIcon className="size-1/2" strokeWidth={1.5} />
      </div>
    )
  }

  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      onError={() => setError(true)}
      className={cn('shrink-0 border border-line object-cover', sizeClasses[size], className)}
    />
  )
}
