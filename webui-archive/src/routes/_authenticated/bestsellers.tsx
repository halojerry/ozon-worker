import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '@/components/layout/page-header'
import { Card } from '@/components/ui/card'
import { Empty } from '@/components/ui/empty'
import { api } from '@/api/client'

interface BestsellerItem {
  sku_or_id: string
  brand: string
  category_path: string
  ordering_amount: number
  ordering_count: number
  avg_price_rub: number
}

export const Route = createFileRoute('/_authenticated/bestsellers')({
  component: BestsellersRoute,
})

const PERIODS = [
  { key: '', label: '全部' },
  { key: 'today', label: '今日' },
  { key: 'week', label: '本周' },
  { key: 'month', label: '本月' },
]

function BestsellersRoute() {
  const [period, setPeriod] = useState('')

  const { data: bestsellers, isLoading } = useQuery<BestsellerItem[]>({
    queryKey: ['bestsellers', period],
    queryFn: async () => {
      const { data } = await api.get('/analytics/bestsellers', {
        params: { limit: 50, offset: 0, period: period || undefined },
      })
      return Array.isArray(data) ? data : []
    },
  })

  const list = bestsellers ?? []
  const totalSales = list.reduce((s, i) => s + (i.ordering_amount ?? 0), 0)
  const totalCount = list.reduce((s, i) => s + (i.ordering_count ?? 0), 0)

  return (
    <>
      <PageHeader
        kicker="数据与配置 · 排行"
        title="热销榜"
        description="Ozon 热销商品排行：今日/本周/本月。"
      />

      <div className="mb-6 flex gap-2">
        {PERIODS.map((p) => (
          <button
            key={p.key}
            onClick={() => setPeriod(p.key)}
            className={`h-9 rounded-input px-4 text-[13px] transition-colors duration-fast ${
              period === p.key
                ? 'bg-accent font-medium text-white'
                : 'border border-line bg-surface text-ink-3 hover:text-ink'
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">商品总销量</p>
          <p className="font-mono text-[24px] font-bold text-accent">{totalSales.toLocaleString()}</p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">总销量（单）</p>
          <p className="font-mono text-[24px] font-bold text-ink">{totalCount.toLocaleString()}</p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">热卖商品数</p>
          <p className="font-mono text-[24px] font-bold text-ink">{list.length}</p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">均价（₽）</p>
          <p className="font-mono text-[24px] font-bold text-ink">
            {list.length > 0
              ? Math.round(list.reduce((s, i) => s + (i.avg_price_rub ?? 0), 0) / list.length).toLocaleString()
              : '--'}
          </p>
        </Card>
      </div>

      <Card>
        {isLoading ? (
          <div className="p-8 text-center text-ink-aux">加载中...</div>
        ) : list.length ? (
          <div className="divide-y divide-line">
            {list.map((item, index) => {
              const top3 = index < 3
              return (
                <div key={item.sku_or_id} className="flex items-center gap-4 px-4 py-3 transition-colors duration-fast hover:bg-header">
                  <span
                    className={`w-10 text-center font-bold ${
                      top3 ? 'text-[24px] text-accent' : 'text-[15px] text-ink-aux'
                    }`}
                  >
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[14px] font-medium text-ink">{item.brand || item.sku_or_id}</p>
                    <p className="truncate text-[11px] text-ink-aux">{item.category_path || '—'}</p>
                  </div>
                  <div className="text-right">
                    <p className="font-mono text-[14px] font-bold text-accent">
                      {item.ordering_amount?.toLocaleString() ?? '0'}
                    </p>
                    <p className="text-[11px] text-ink-aux">{item.ordering_count ?? 0} 单</p>
                  </div>
                  <div className="w-28 text-right">
                    <p className="font-mono text-[13px] text-ink">₽ {item.avg_price_rub?.toLocaleString() ?? '—'}</p>
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          <Empty description="暂无热销数据" />
        )}
      </Card>
    </>
  )
}
