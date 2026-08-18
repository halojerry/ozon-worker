import { createFileRoute } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '@/components/layout/page-header'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Empty } from '@/components/ui/empty'
import { Metric } from '@/components/ui/metric'
import { ImageCell } from '@/components/shared/image-cell'
import { Pagination } from '@/components/shared/pagination'
import { resolveStatusMap, ORDER_STATUS_MAP } from '@/lib/constants'
import { useDefaultCredential, NoStoreHint } from '@/api/hooks/use-default-credential'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type OrderOut = components['schemas']['OrderOut']
type OrderListResponse = components['schemas']['OrderListResponse']

export const Route = createFileRoute('/_authenticated/orders')({
  component: OrdersRoute,
})

const PAGE_SIZE = 20

const STATUS_GROUPS = [
  { key: 'awaiting_deliver', label: '待发货', variant: 'red' as const },
  { key: 'delivered', label: '已发货', variant: 'neutral' as const },
  { key: 'delivering', label: '配送中', variant: 'neutral' as const },
  { key: 'cancelled', label: '已取消', variant: 'neutral' as const },
]

function OrdersRoute() {
  const [status, setStatus] = useState('')
  const [credentialId, setCredentialId] = useState('')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<string[]>([])
  const [detail, setDetail] = useState<OrderOut | null>(null)

  const { credential, isLoading: credLoading } = useDefaultCredential()
  const effectiveCredentialId = credentialId || credential?.id

  const { data, isLoading } = useQuery<OrderListResponse>({
    queryKey: ['orders', status, effectiveCredentialId, page],
    queryFn: async () => {
      const params: Record<string, unknown> = { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }
      if (status) params.status = status
      if (effectiveCredentialId) params.credential_id = effectiveCredentialId
      const { data } = await api.get<OrderListResponse>('/orders', { params })
      return data
    },
    enabled: !!effectiveCredentialId,
  })

  const { data: credentials } = useQuery({
    queryKey: ['credentials'],
    queryFn: async () => {
      const { data } = await api.get('/credentials')
      return Array.isArray(data) ? data : []
    },
  })

  const stats = useMemo(() => {
    const s: Record<string, number> = {}
    for (const g of STATUS_GROUPS) s[g.key] = 0
    const items = data?.items ?? []
    for (const o of items) {
      const st = o.status
      if (s[st] !== undefined) s[st] += 1
    }
    return s
  }, [data])

  const items = data?.items ?? []
  const total = data?.total ?? 0

  function toggleSelect(id: string) {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  if (!credLoading && !effectiveCredentialId) {
    return (
      <>
        <PageHeader
          kicker="运营 · 订单"
          title="订单中心"
          description="订单统计、筛选与管理。"
        />
        <NoStoreHint />
      </>
    )
  }

  return (
    <>
      <PageHeader
        kicker="运营 · 订单"
        title="订单中心"
        description="订单统计、筛选与管理。"
      />

      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Metric label="待发货" value={String(stats.awaiting_deliver)} accent />
        <Metric label="已发货" value={String(stats.delivered)} />
        <Metric label="配送中" value={String(stats.delivering)} />
        <Metric label="已取消" value={String(stats.cancelled)} />
      </div>

      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
          <select
            value={status}
            onChange={(e) => { setStatus(e.target.value); setPage(1) }}
            className="h-9 rounded-input border border-line bg-surface px-3 text-[13px] text-ink focus:border-accent focus:outline-none"
          >
            <option value="">全部状态</option>
            {STATUS_GROUPS.map((g) => (
              <option key={g.key} value={g.key}>{g.label}</option>
            ))}
          </select>
          <select
            value={credentialId}
            onChange={(e) => { setCredentialId(e.target.value); setPage(1) }}
            className="h-9 rounded-input border border-line bg-surface px-3 text-[13px] text-ink focus:border-accent focus:outline-none"
          >
            <option value="">全部店铺</option>
            {(credentials ?? []).map((c: { id: string; shop_name?: string | null }) => (
              <option key={c.id} value={c.id}>{c.shop_name || c.id}</option>
            ))}
          </select>
          <Input placeholder="搜索订单号..." className="max-w-[220px]" />
          <Button
            variant="secondary"
            onClick={() => {
              setSelected([])
              if (selected.length > 0) {
                api.post('/orders/batch/ship', { posting_numbers: selected }).then(() => setSelected([]))
              }
            }}
          >
            批量发货
          </Button>
        </div>

        {isLoading ? (
          <div className="p-8 text-center text-ink-aux">加载中...</div>
        ) : items.length ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="border-b border-line bg-header text-left text-[12px] text-ink-3">
                    <th className="w-10 px-4 py-2.5">
                      <input
                        type="checkbox"
                        className="accent-accent"
                        checked={items.length > 0 && selected.length === items.length}
                        onChange={() =>
                          setSelected(selected.length === items.length ? [] : items.map((o) => o.posting_number))
                        }
                      />
                    </th>
                    <th className="px-4 py-2.5">订单号</th>
                    <th className="px-4 py-2.5">商品</th>
                    <th className="px-4 py-2.5 text-right">数量</th>
                    <th className="px-4 py-2.5 text-right">金额</th>
                    <th className="px-4 py-2.5">下单时间</th>
                    <th className="px-4 py-2.5">状态</th>
                    <th className="px-4 py-2.5 text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((order) => {
                    const st = resolveStatusMap(ORDER_STATUS_MAP, order.status)
                    return (
                      <tr
                        key={order.posting_number}
                        className="border-b border-line transition-colors duration-fast hover:bg-header"
                      >
                        <td className="px-4 py-2.5">
                          <input
                            type="checkbox"
                            className="accent-accent"
                            checked={selected.includes(order.posting_number)}
                            onChange={() => toggleSelect(order.posting_number)}
                          />
                        </td>
                        <td className="px-4 py-2.5 font-mono text-ink">{order.posting_number}</td>
                        <td className="px-4 py-2.5">
                          <div className="flex items-center gap-2.5">
                            <ImageCell src={order.products?.[0]?.image} alt="" size="sm" />
                            <div className="min-w-0">
                              <p className="max-w-[240px] truncate text-ink">
                                {order.products?.[0]?.name || '—'}
                              </p>
                              {order.product_count > 1 && (
                                <p className="text-[11px] text-ink-aux">等 {order.product_count} 件</p>
                              )}
                            </div>
                          </div>
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono text-ink">{order.product_count}</td>
                        <td className="px-4 py-2.5 text-right font-mono text-ink">
                          ₽ {order.total_amount?.toLocaleString() ?? '—'}
                        </td>
                        <td className="px-4 py-2.5 text-ink-aux">
                          {order.created_at ? String(order.created_at).slice(0, 19).replace('T', ' ') : '—'}
                        </td>
                        <td className="px-4 py-2.5">
                          <Badge variant={st.variant}>{st.label}</Badge>
                        </td>
                        <td className="px-4 py-2.5 text-right">
                          <button
                            className="text-[12px] text-accent hover:underline"
                            onClick={() => setDetail(order)}
                          >
                            查看
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <Pagination total={total} page={page} pageSize={PAGE_SIZE} onPageChange={setPage} />
          </>
        ) : (
          <Empty description="暂无订单数据" />
        )}
      </Card>

      {detail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6" onClick={() => setDetail(null)}>
          <div className="w-full max-w-[560px] rounded-panel bg-surface p-6 shadow-overlay" onClick={(e) => e.stopPropagation()}>
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-h3 text-ink">订单详情</h3>
              <button className="text-ink-aux hover:text-ink" onClick={() => setDetail(null)}>✕</button>
            </div>
            <div className="space-y-3">
              <div className="flex justify-between text-body-sm">
                <span className="text-ink-aux">订单号</span>
                <span className="font-mono text-ink">{detail.posting_number}</span>
              </div>
              <div className="flex justify-between text-body-sm">
                <span className="text-ink-aux">状态</span>
                <Badge variant={resolveStatusMap(ORDER_STATUS_MAP, detail.status).variant}>
                  {resolveStatusMap(ORDER_STATUS_MAP, detail.status).label}
                </Badge>
              </div>
              <div className="flex justify-between text-body-sm">
                <span className="text-ink-aux">金额</span>
                <span className="font-mono text-ink">₽ {detail.total_amount?.toLocaleString() ?? '—'}</span>
              </div>
              <div className="flex justify-between text-body-sm">
                <span className="text-ink-aux">平台费用</span>
                <span className="font-mono text-ink">₽ {detail.commission_amount?.toLocaleString() ?? '—'}</span>
              </div>
              <div className="flex justify-between text-body-sm">
                <span className="text-ink-aux">估算利润</span>
                <span className="font-mono text-ink">₽ {detail.profit?.toLocaleString() ?? '—'}</span>
              </div>
              <div className="flex justify-between text-body-sm">
                <span className="text-ink-aux">仓库</span>
                <span className="text-ink">{detail.warehouse || '—'}</span>
              </div>
              <div className="flex justify-between text-body-sm">
                <span className="text-ink-aux">配送方式</span>
                <span className="text-ink">{detail.delivery_method || '—'}</span>
              </div>
            </div>
            <div className="mt-6 flex justify-end gap-3">
              <Button variant="secondary" onClick={() => api.post(`/orders/${detail.posting_number}/label`)}>
                打印面单
              </Button>
              <Button
                onClick={() => {
                  api.post(`/orders/${detail.posting_number}/ship`).then(() => {
                    setDetail(null)
                    window.location.reload()
                  })
                }}
              >
                发货
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
