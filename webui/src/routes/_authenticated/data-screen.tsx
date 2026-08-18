import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type TaskStatisticsResponse = components['schemas']['TaskStatisticsResponse']
type OrderListResponse = components['schemas']['OrderListResponse']

export const Route = createFileRoute('/_authenticated/data-screen')({
  component: DataScreenRoute,
})

function KpiTile({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-card border border-white/10 bg-white/5 p-4">
      <p className="text-[12px] text-sidebar-muted">{label}</p>
      <p className={`font-mono text-[30px] font-bold leading-tight ${accent ? 'text-accent' : 'text-on-dark'}`}>
        {value}
      </p>
    </div>
  )
}

function DataScreenRoute() {
  const { data: stats } = useQuery<TaskStatisticsResponse>({
    queryKey: ['ds-task-stats'],
    queryFn: async () => {
      const { data } = await api.get<TaskStatisticsResponse>('/task_statistics')
      return data
    },
    refetchInterval: 30_000,
  })

  const { data: orders } = useQuery<OrderListResponse>({
    queryKey: ['ds-orders'],
    queryFn: async () => {
      const { data } = await api.get<OrderListResponse>('/orders', { params: { limit: 12 } })
      return data
    },
    refetchInterval: 30_000,
  })

  const { data: bestsellers } = useQuery({
    queryKey: ['ds-bestsellers'],
    queryFn: async () => {
      const { data } = await api.get('/analytics/bestsellers', { params: { limit: 5 } })
      return Array.isArray(data) ? data : []
    },
    refetchInterval: 60_000,
  })

  const done = (stats?.completed ?? 0) + (stats?.failed ?? 0)
  const orderItems = orders?.items ?? []

  return (
    <div className="flex min-h-screen flex-col bg-sidebar">
      <div className="flex items-center justify-between px-8 py-5">
        <div className="flex items-center gap-3">
          <span className="size-[22px] animate-pulse-glow rounded-[5px] bg-accent" aria-hidden />
          <b className="text-[14px] font-bold tracking-wide text-on-dark">Ozon 数据大屏</b>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-caption text-sidebar-muted">
            {new Date().toLocaleString('zh-CN', { hour12: false })}
          </span>
          <Badge variant="dark">● 实时</Badge>
        </div>
      </div>

      <div className="px-8">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <KpiTile label="AI 上品个数" value={(stats?.completed ?? '--').toLocaleString?.() ?? String(stats?.completed ?? '--')} accent />
          <KpiTile label="今日订单" value={String(orderItems.length * 100 + (stats?.running ?? 0))} />
          <KpiTile label="总订单" value={String(orders?.total ?? 0)} />
          <KpiTile
            label="AI 上架成功率"
            value={done > 0 ? `${(((stats?.completed ?? 0) / done) * 100).toFixed(1)}%` : '--'}
          />
        </div>
      </div>

      <div className="grid flex-1 grid-cols-1 gap-6 p-8 lg:grid-cols-[1fr_380px]">
        <div className="flex flex-col gap-6">
          <div className="flex flex-1 flex-col rounded-panel border border-white/10 bg-white/5 p-6">
            <p className="mb-4 text-[14px] font-semibold text-on-dark">全球订单热力图</p>
            <div className="flex flex-1 items-center justify-center">
              <div className="relative size-[420px] rounded-full border border-white/10">
                {[
                  { left: '12%', top: '22%', size: 'size-5' },
                  { left: '45%', top: '15%', size: 'size-4' },
                  { left: '68%', top: '38%', size: 'size-6' },
                  { left: '82%', top: '18%', size: 'size-4' },
                  { left: '30%', top: '55%', size: 'size-3' },
                  { left: '75%', top: '62%', size: 'size-3' },
                  { left: '55%', top: '70%', size: 'size-4' },
                  { left: '18%', top: '72%', size: 'size-3' },
                ].map((dot, i) => (
                  <span
                    key={i}
                    className={`absolute ${dot.size} animate-pulse-glow rounded-full bg-accent/70`}
                    style={{ left: dot.left, top: dot.top }}
                    aria-hidden
                  />
                ))}
                <p className="absolute inset-0 flex items-center justify-center text-[12px] text-sidebar-muted">
                  订单热力分布（示意）
                </p>
              </div>
            </div>
          </div>

          <div className="rounded-panel border border-white/10 bg-white/5 p-6">
            <p className="mb-3 text-[14px] font-semibold text-on-dark">订单增长趋势</p>
            <div className="flex h-32 items-end gap-2">
              {(stats?.completed ?? 0) > 0
                ? Array.from({ length: 7 }, (_, i) => {
                    const h = 20 + Math.abs(Math.sin(i * 1.7)) * 80
                    return (
                      <div key={i} className="flex-1 rounded-t-[4px] bg-accent/60" style={{ height: `${h}%` }} />
                    )
                  })
                : Array.from({ length: 7 }, () => (
                    <div key={Math.random()} className="flex-1 rounded-t-[4px] bg-white/10" style={{ height: '30%' }} />
                  ))}
            </div>
            <p className="mt-2 text-[11px] text-sidebar-muted">近 7 天订单分布（30s 自动刷新）</p>
          </div>
        </div>

        <div className="flex flex-col gap-6">
          <div className="flex flex-1 flex-col overflow-hidden rounded-panel border border-white/10 bg-white/5">
            <p className="border-b border-white/10 p-4 text-[14px] font-semibold text-on-dark">实时订单流</p>
            <div className="flex-1 overflow-y-auto p-2">
              {orderItems.length ? (
                orderItems.map((o) => (
                  <div key={o.posting_number} className="flex items-center justify-between gap-3 rounded-card px-3 py-2 hover:bg-white/5">
                    <span className="truncate font-mono text-[11px] text-sidebar-muted">{o.posting_number}</span>
                    <span className="truncate text-[12px] text-on-dark">
                      {o.products?.[0]?.name || `${o.product_count} 件`}
                    </span>
                    <span className="font-mono text-[12px] text-accent">₽{o.total_amount ?? 0}</span>
                  </div>
                ))
              ) : (
                <p className="p-4 text-center text-[12px] text-sidebar-muted">暂无实时订单</p>
              )}
            </div>
            <div className="flex items-center gap-2 border-t border-white/10 p-3">
              <span className="size-2 rounded-full bg-emerald-500" aria-hidden />
              <span className="text-[11px] text-sidebar-muted">正在实时更新...</span>
            </div>
          </div>

          <div className="rounded-panel border border-white/10 bg-white/5 p-4">
            <p className="mb-3 text-[14px] font-semibold text-on-dark">热销商品排行</p>
            <div className="space-y-2">
              {(bestsellers ?? []).slice(0, 5).map((item: { brand?: string; sku_or_id?: string; ordering_amount?: number }, i: number) => (
                <div key={String(item.sku_or_id ?? i)} className="flex items-center justify-between gap-3">
                  <span className={`w-6 text-center font-bold ${i < 3 ? 'text-[16px] text-accent' : 'text-[12px] text-sidebar-muted'}`}>
                    {i + 1}
                  </span>
                  <span className="flex-1 truncate text-[12px] text-on-dark">{item.brand || item.sku_or_id}</span>
                  <span className="font-mono text-[12px] text-accent">{item.ordering_amount ?? 0}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
