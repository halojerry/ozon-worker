import { createFileRoute } from '@tanstack/react-router'
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '@/components/layout/page-header'
import { Card } from '@/components/ui/card'
import { Empty } from '@/components/ui/empty'
import { useDefaultCredential, NoStoreHint } from '@/api/hooks/use-default-credential'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type TaskStatisticsResponse = components['schemas']['TaskStatisticsResponse']
type OrderListResponse = components['schemas']['OrderListResponse']
type OzonProductListResponse = components['schemas']['OzonProductListResponse']

export const Route = createFileRoute('/_authenticated/')({
  component: DashboardRoute,
})

function KpiCard({ label, value, icon, accent = false }: { label: string; value: string; icon: string; accent?: boolean }) {
  return (
    <Card className="flex items-center gap-3 p-4">
      <div className="flex size-10 shrink-0 items-center justify-center rounded-full bg-accent-soft">
        <span className="text-[16px]" aria-hidden>{icon}</span>
      </div>
      <div className="min-w-0">
        <p className="truncate text-[12px] text-ink-aux">{label}</p>
        <p className={`font-mono text-[22px] font-bold leading-tight ${accent ? 'text-accent' : 'text-ink'}`}>
          {value}
        </p>
      </div>
    </Card>
  )
}

function TrendChart({ orders }: { orders: OrderListResponse | undefined }) {
  const points = useMemo(() => {
    const counts = new Map<string, number>()
    for (const o of orders?.items ?? []) {
      const d = o.created_at ? String(o.created_at).slice(0, 10) : ''
      if (d) counts.set(d, (counts.get(d) ?? 0) + 1)
    }
    return Array.from(counts.entries()).slice(-7)
  }, [orders])

  const max = Math.max(1, ...points.map(([, c]) => c))

  return (
    <div>
      <div className="flex h-36 items-end gap-2">
        {points.length ? (
          points.map(([date, count]) => (
            <div key={date} className="group flex flex-1 flex-col items-center gap-1">
              <span className="font-mono text-[10px] text-ink-3 opacity-0 transition-opacity group-hover:opacity-100">
                {count}
              </span>
              <div
                className="w-full rounded-t-[3px] bg-accent/70 transition-colors hover:bg-accent"
                style={{ height: `${Math.max(6, (count / max) * 100)}%` }}
                title={`${date}: ${count} 单`}
              />
            </div>
          ))
        ) : (
          <div className="flex h-full w-full items-center justify-center">
            <span className="text-[12px] text-ink-aux">暂无趋势数据</span>
          </div>
        )}
      </div>
      <div className="mt-2 flex justify-between border-t border-line pt-1">
        {points.length ? (
          points.map(([date]) => (
            <span key={date} className="font-mono text-[10px] text-ink-aux">{date.slice(5)}</span>
          ))
        ) : null}
      </div>
    </div>
  )
}

function DashboardRoute() {
  const { credential, isLoading: credLoading } = useDefaultCredential()
  const credentialId = credential?.id

  const { data: stats } = useQuery<TaskStatisticsResponse>({
    queryKey: ['task-statistics'],
    queryFn: async () => {
      const { data } = await api.get<TaskStatisticsResponse>('/task_statistics')
      return data
    },
    refetchInterval: 30_000,
  })

  const { data: orders, isLoading: ordersLoading } = useQuery<OrderListResponse>({
    queryKey: ['orders-recent', credentialId],
    queryFn: async () => {
      const { data } = await api.get<OrderListResponse>('/orders', {
        params: { limit: 200, credential_id: credentialId },
      })
      return data
    },
    enabled: !!credentialId,
  })

  const { data: products } = useQuery<OzonProductListResponse>({
    queryKey: ['products-ozon-count', credentialId],
    queryFn: async () => {
      const { data } = await api.get<OzonProductListResponse>('/products/ozon', {
        params: { limit: 1, credential_id: credentialId },
      })
      return data
    },
    enabled: !!credentialId,
  })

  const { data: storeStats } = useQuery<{ today_orders: number }>({
    queryKey: ['store-stats-today', credentialId],
    queryFn: async () => {
      const { data } = await api.get(`/stores/${credentialId}/stats`)
      return data
    },
    enabled: !!credentialId,
  })

  const { data: bestsellers } = useQuery({
    queryKey: ['bestsellers-dash'],
    queryFn: async () => {
      const { data } = await api.get('/analytics/bestsellers', { params: { limit: 5 } })
      return Array.isArray(data) ? data : []
    },
  })

  const { data: announcements } = useQuery({
    queryKey: ['announcements-dash'],
    queryFn: async () => {
      const { data } = await api.get('/site/announcements')
      return Array.isArray(data) ? data : []
    },
  })

  const totalOrders = orders?.total ?? 0
  const successRate = useMemo(() => {
    if (!stats) return '--'
    const done = (stats.completed ?? 0) + (stats.failed ?? 0)
    if (done === 0) return '0%'
    return `${(((stats.completed ?? 0) / done) * 100).toFixed(1)}%`
  }, [stats])

  const kpis = [
    { label: '今日订单', value: storeStats?.today_orders != null ? String(storeStats.today_orders) : '--', icon: '🛍️' },
    { label: '总订单', value: totalOrders ? totalOrders.toLocaleString() : '--', icon: '📄' },
    { label: 'AI 上品个数', value: stats?.completed?.toLocaleString() ?? '0', icon: '🤖', accent: true },
    { label: 'AI 上架成功率', value: successRate, icon: '✅', accent: true },
    { label: '在线商品数', value: products?.total?.toLocaleString() ?? '--', icon: '🗂️' },
  ]

  if (!credLoading && !credentialId) {
    return (
      <>
        <PageHeader kicker="KPI · 今日概览" title="仪表盘" description="今日订单、AI 上品与上架成功率一览。" />
        <NoStoreHint />
      </>
    )
  }

  return (
    <>
      <PageHeader
        kicker="KPI · 今日概览"
        title="仪表盘"
        description="今日订单、AI 上品与上架成功率一览。"
      />

      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-5">
        {kpis.map((k) => (
          <KpiCard key={k.label} label={k.label} value={k.value} icon={k.icon} accent={k.accent} />
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
        <div className="space-y-6">
          <Card title="订单趋势" action={<span className="text-[11px] text-ink-aux">近 7 天</span>}>
            <div className="p-4">
              {ordersLoading ? (
                <div className="flex h-36 items-center justify-center text-[12px] text-ink-aux">加载中...</div>
              ) : (
                <TrendChart orders={orders} />
              )}
            </div>
          </Card>

          <Card title="最近自动化任务" action={<span className="text-[11px] text-ink-aux">查看全部 →</span>}>
            <Empty description="暂无任务记录" />
          </Card>
        </div>

        <div className="space-y-6">
          <Card title="热销商品">
            {bestsellers?.length ? (
              <div className="divide-y divide-line">
                {(bestsellers as Array<{ sku_or_id: string; brand?: string; ordering_amount?: number }>)
                  .slice(0, 5)
                  .map((item, i) => (
                    <div key={String(item.sku_or_id)} className="flex items-center gap-3 px-4 py-2.5">
                      <span className={`w-5 text-center font-bold ${i < 3 ? 'text-accent' : 'text-ink-aux'}`}>{i + 1}</span>
                      <span className="flex-1 truncate text-[13px] text-ink">{item.brand || item.sku_or_id}</span>
                      <span className="font-mono text-[12px] text-ink">{item.ordering_amount ?? 0}</span>
                    </div>
                  ))}
              </div>
            ) : (
              <Empty description="暂无热销数据" />
            )}
          </Card>

          <Card title="运营公告">
            {announcements?.length ? (
              <div className="divide-y divide-line">
                {(announcements as Array<{ id: string; title: string; content?: string }>).slice(0, 5).map((a) => (
                  <div key={a.id} className="flex items-start gap-2 px-4 py-2.5">
                    <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-accent" aria-hidden />
                    <div className="min-w-0">
                      <p className="truncate text-[13px] text-ink">{a.title}</p>
                      {a.content && <p className="truncate text-[11px] text-ink-aux">{a.content}</p>}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <Empty description="暂无公告" />
            )}
          </Card>
        </div>
      </div>
    </>
  )
}
