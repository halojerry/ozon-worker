import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '@/components/layout/page-header'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Empty } from '@/components/ui/empty'
import { ImageCell } from '@/components/shared/image-cell'
import { Pagination } from '@/components/shared/pagination'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type OzonProductOut = components['schemas']['OzonProductOut']
type OzonProductListResponse = components['schemas']['OzonProductListResponse']

export const Route = createFileRoute('/_authenticated/pricing')({
  component: PricingRoute,
})

const PAGE_SIZE = 10

function PricingRoute() {
  const [page, setPage] = useState(1)
  const [applying, setApplying] = useState<string | null>(null)

  const { data, isLoading } = useQuery<OzonProductListResponse>({
    queryKey: ['products-ozon-pricing', page],
    queryFn: async () => {
      const { data } = await api.get<OzonProductListResponse>('/products/ozon', {
        params: { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE },
      })
      return data
    },
  })

  const { data: bestsellers } = useQuery({
    queryKey: ['bestsellers-pricing'],
    queryFn: async () => {
      const { data } = await api.get('/analytics/bestsellers', { params: { limit: 20 } })
      return Array.isArray(data) ? data : []
    },
  })

  const items = (data?.items ?? []) as OzonProductOut[]
  const total = data?.total ?? 0

  async function applyPrice(product: OzonProductOut) {
    setApplying(product.product_id)
    try {
      const res = await api.post('/estimate', {
        draft: {
          purchase_cost: product.price,
          weight_g: 300,
          dimensions: { length: 10, width: 10, height: 10 },
        },
        extensions: { margin_rate: 0.25, commission_rate: 0.1, fx_buffer: 0.05 },
      })
      const suggested = (res.data as { price?: number }).price
      if (suggested) {
        await api.post('/products/bulk-prices', { items: [{ product_id: product.product_id, price: suggested }] })
        alert(`已应用建议价 ₽ ${suggested.toFixed(2)}`)
      }
    } catch {
      alert('定价失败，请检查参数')
    } finally {
      setApplying(null)
    }
  }

  return (
    <>
      <PageHeader
        kicker="数据与配置 · AI 核心"
        title="智能定价"
        description="定价策略、调价列表与竞品对比。"
      />

      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-3">
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">在线商品数</p>
          <p className="font-mono text-[24px] font-bold text-accent">{total.toLocaleString()}</p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">竞品均价（₽）</p>
          <p className="font-mono text-[24px] font-bold text-ink">
            {bestsellers?.length
              ? Math.round(
                  bestsellers.reduce((s: number, i: { avg_price_rub?: number }) => s + (i.avg_price_rub ?? 0), 0) /
                    bestsellers.length,
                ).toLocaleString()
              : '--'}
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">定价策略</p>
          <p className="font-mono text-[24px] font-bold text-ink">25% / 10% / 5%</p>
          <p className="text-[11px] text-ink-aux">加价 / 佣金 / 汇率缓冲</p>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <Card>
          {isLoading ? (
            <div className="p-8 text-center text-ink-aux">加载中...</div>
          ) : items.length ? (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-[13px]">
                  <thead>
                    <tr className="border-b border-line bg-header text-left text-[12px] text-ink-3">
                      <th className="px-4 py-2.5">商品</th>
                      <th className="px-4 py-2.5 text-right">当前价</th>
                      <th className="px-4 py-2.5 text-right">竞品均价</th>
                      <th className="px-4 py-2.5 text-right">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((product) => {
                      const compAvg =
                        bestsellers?.length
                          ? Math.round(
                              bestsellers.reduce(
                                (s: number, i: { avg_price_rub?: number }) => s + (i.avg_price_rub ?? 0),
                                0,
                              ) / bestsellers.length,
                            )
                          : null
                      return (
                        <tr key={product.product_id} className="border-b border-line transition-colors duration-fast hover:bg-header">
                          <td className="px-4 py-2.5">
                            <div className="flex items-center gap-2.5">
                              <ImageCell src={product.image} alt={product.name} size="sm" />
                              <div className="min-w-0">
                                <p className="max-w-[200px] truncate text-ink">{product.name}</p>
                                <p className="font-mono text-[11px] text-ink-aux">{product.offer_id}</p>
                              </div>
                            </div>
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono text-ink">
                            {product.currency} {product.price?.toLocaleString() ?? '—'}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono text-ink">
                            {compAvg ? `₽ ${compAvg.toLocaleString()}` : '—'}
                          </td>
                          <td className="px-4 py-2.5 text-right">
                            <Button
                              onClick={() => applyPrice(product)}
                              disabled={applying === product.product_id}
                            >
                              {applying === product.product_id ? '计算中...' : '一键应用'}
                            </Button>
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
            <Empty description="暂无商品数据" />
          )}
        </Card>

        <Card title="竞品对比曲线">
          <div className="p-4">
            <Empty description="接入竞品价格历史数据后展示趋势曲线" />
          </div>
        </Card>
      </div>
    </>
  )
}
