import { type ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { Empty } from './empty'

/**
 * Table —— spec §06 表格实样
 * - 表头：灰底 #FAF9F6 + 12px 600 次要字
 * - 行：1px 分割线 + hover 浅灰底（100ms）+ 选中行红浅底 #FDEBEB
 * - 数据数字由列 render 内配合 `font-mono` 输出
 */
export interface TableColumn<T> {
  key: string
  header: ReactNode
  render?: (row: T, index: number) => ReactNode
  /** th/td 附加类（对齐/宽度） */
  className?: string
  /** 表头类 */
  headerClassName?: string
}

export interface TableProps<T> {
  columns: TableColumn<T>[]
  data: T[]
  rowKey: (row: T, index: number) => string
  /** 选中行 key 集合 */
  selectedKeys?: ReadonlySet<string> | string[]
  onRowClick?: (row: T) => void
  /** 空态定制 */
  empty?: ReactNode
  loading?: boolean
  /** 表格容器类 */
  className?: string
}

function isSelected(key: string, selected: ReadonlySet<string> | string[] | undefined): boolean {
  if (!selected) return false
  return Array.isArray(selected) ? selected.includes(key) : selected.has(key)
}

export function Table<T>({
  columns,
  data,
  rowKey,
  selectedKeys,
  onRowClick,
  empty,
  loading,
  className,
}: TableProps<T>) {
  return (
    <div className={cn('overflow-x-auto rounded-card border border-line bg-surface', className)}>
      <table className="w-full min-w-full border-collapse text-[13px]">
        <thead>
          <tr className="border-b border-line bg-header">
            {columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                className={cn(
                  'whitespace-nowrap px-3.5 py-2.5 text-left text-[12px] font-semibold text-ink-3',
                  col.headerClassName,
                )}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <tr>
              <td colSpan={columns.length} className="px-3.5 py-10 text-center text-[12px] text-ink-4">
                加载中…
              </td>
            </tr>
          ) : data.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-3.5 py-6">
                {empty ?? <Empty title="暂无数据" description="当前筛选条件下没有匹配的记录。" />}
              </td>
            </tr>
          ) : (
            data.map((row, index) => {
              const key = rowKey(row, index)
              const selected = isSelected(key, selectedKeys)
              return (
                <tr
                  key={key}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  className={cn(
                    'border-b border-line transition-colors duration-fast ease-standard last:border-b-0',
                    onRowClick && 'cursor-pointer',
                    selected ? 'bg-badge-accent hover:bg-badge-accent' : 'hover:bg-header',
                  )}
                >
                  {columns.map((col) => (
                    <td key={col.key} className={cn('px-3.5 py-2.5 align-middle text-ink-3', col.className)}>
                      {col.render ? col.render(row, index) : ((row as Record<string, unknown>)[col.key] as ReactNode)}
                    </td>
                  ))}
                </tr>
              )
            })
          )}
        </tbody>
      </table>
    </div>
  )
}
