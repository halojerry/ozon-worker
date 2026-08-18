import { ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/cn'
import { Button } from '@/components/ui/button'

/**
 * Pagination —— 分页（PRD §6.2 业务组件）
 * 页码紧凑模式：当前页 ±2，超范围折叠为 …。
 */
export interface PaginationProps {
  page: number
  pageSize: number
  total: number
  onPageChange: (page: number) => void
  onPageSizeChange?: (pageSize: number) => void
  pageSizeOptions?: number[]
  className?: string
}

export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [10, 20, 50],
  className,
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const safePage = Math.min(Math.max(1, page), totalPages)

  function pageItems(): Array<number | 'ellipsis'> {
    const items: Array<number | 'ellipsis'> = []
    const start = Math.max(1, safePage - 2)
    const end = Math.min(totalPages, safePage + 2)
    if (start > 1) items.push(1)
    if (start > 2) items.push('ellipsis')
    for (let p = start; p <= end; p++) items.push(p)
    if (end < totalPages - 1) items.push('ellipsis')
    if (end < totalPages) items.push(totalPages)
    return items
  }

  return (
    <div className={cn('flex flex-wrap items-center justify-between gap-3 py-2', className)}>
      <div className="flex items-center gap-2 text-[12px] text-ink-aux">
        <span>
          共 <b className="font-mono text-ink-3">{total}</b> 条
        </span>
        <span className="text-ink-4">·</span>
        <span>
          第 <b className="font-mono text-ink-3">{safePage}</b>/{totalPages} 页
        </span>
        {onPageSizeChange && (
          <select
            aria-label="每页条数"
            value={pageSize}
            onChange={(e) => onPageSizeChange(Number(e.target.value))}
            className="ml-1 h-7 rounded-input border border-line bg-surface px-1.5 text-[12px] text-ink-3 outline-none focus:border-accent"
          >
            {pageSizeOptions.map((n) => (
              <option key={n} value={n}>
                {n} 条/页
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="flex items-center gap-1">
        <Button
          variant="secondary"
          className="size-8 px-0"
          disabled={safePage <= 1}
          onClick={() => onPageChange(safePage - 1)}
          aria-label="上一页"
        >
          <ChevronLeft className="size-4" />
        </Button>
        {pageItems().map((item, i) =>
          item === 'ellipsis' ? (
            <span key={`e${i}`} className="px-1 text-[12px] text-ink-4">
              …
            </span>
          ) : (
            <button
              key={item}
              type="button"
              onClick={() => onPageChange(item)}
              aria-current={item === safePage ? 'page' : undefined}
              className={cn(
                'h-8 min-w-8 rounded-input px-2 text-[12px] transition-colors duration-fast ease-standard',
                item === safePage
                  ? 'bg-accent font-semibold text-white'
                  : 'text-ink-3 hover:bg-badge-neutral',
              )}
            >
              {item}
            </button>
          ),
        )}
        <Button
          variant="secondary"
          className="size-8 px-0"
          disabled={safePage >= totalPages}
          onClick={() => onPageChange(safePage + 1)}
          aria-label="下一页"
        >
          <ChevronRight className="size-4" />
        </Button>
      </div>
    </div>
  )
}
