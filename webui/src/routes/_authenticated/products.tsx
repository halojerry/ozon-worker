import { createFileRoute } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '@/components/layout/page-header'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Empty } from '@/components/ui/empty'
import { ImageCell } from '@/components/shared/image-cell'
import { Pagination } from '@/components/shared/pagination'
import { useDefaultCredential, NoStoreHint } from '@/api/hooks/use-default-credential'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type OzonProductOut = components['schemas']['OzonProductOut']
type OzonProductListResponse = components['schemas']['OzonProductListResponse']

export const Route = createFileRoute('/_authenticated/products')({
  component: ProductsRoute,
})

const PAGE_SIZE = 20

function ProductsRoute() {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)

  const { credential, isLoading: credLoading } = useDefaultCredential()
  const credentialId = credential?.id

  const { data, isLoading } = useQuery<OzonProductListResponse>({
    queryKey: ['products-ozon', credentialId, page],
    queryFn: async () => {
      const { data } = await api.get<OzonProductListResponse>('/products/ozon', {
        params: { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE, credential_id: credentialId },
      })
      return data
    },
    enabled: !!credentialId,
  })

  const items = useMemo(() => {
    const list = data?.items ?? []
    const kw = search.trim().toLowerCase()
    let filtered = list
    if (kw) {
      filtered = filtered.filter(
        (p) =>
          p.name?.toLowerCase().includes(kw) ||
          p.offer_id?.toLowerCase().includes(kw) ||
          p.product_id?.toLowerCase().includes(kw),
      )
    }
    if (status) {
      filtered = filtered.filter((p) => {
        const stock = p.stock ?? 0
        if (status === 'onsale') return stock > 0
        if (status === 'out_of_stock') return stock <= 0
        return true
      })
    }
    return filtered
  }, [data, search, status])

  const total = data?.total ?? 0

  if (!credLoading && !credentialId) {
    return (
      <>
        <PageHeader
          kicker="运营 · 商品"
          title="商品管理"
          description="在售货架与 Ozon 在线商品统一管理。"
        />
        <NoStoreHint />
      </>
    )
  }

  return (
    <>
      <PageHeader
        kicker="运营 · 商品"
        title="商品管理"
        description="在售货架与 Ozon 在线商品统一管理。"
      />

      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
          <Input
            placeholder="搜索商品标题、SKU..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="max-w-[240px]"
          />
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="h-9 rounded-input border border-line bg-surface px-3 text-[13px] text-ink focus:border-accent focus:outline-none"
          >
            <option value="">全部状态</option>
            <option value="onsale">在售</option>
            <option value="out_of_stock">缺货</option>
          </select>
          <Button className="ml-auto">+ 新建商品</Button>
        </div>

        {isLoading ? (
          <div className="p-8 text-center text-ink-aux">加载中...</div>
        ) : items.length ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="border-b border-line bg-header text-left text-[12px] text-ink-3">
                    <th className="px-4 py-2.5">商品</th>
                    <th className="px-4 py-2.5 text-right">价格</th>
                    <th className="px-4 py-2.5 text-right">库存</th>
                    <th className="px-4 py-2.5">状态</th>
                    <th className="px-4 py-2.5 text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((product: OzonProductOut) => {
                    const stock = product.stock ?? 0
                    const onsale = stock > 0
                    return (
                      <tr
                        key={product.product_id}
                        className="border-b border-line transition-colors duration-fast hover:bg-header"
                      >
                        <td className="px-4 py-2.5">
                          <div className="flex items-center gap-2.5">
                            <ImageCell src={product.image} alt={product.name} size="sm" />
                            <div className="min-w-0">
                              <p className="max-w-[260px] truncate text-ink">{product.name}</p>
                              <p className="font-mono text-[11px] text-ink-aux">{product.offer_id}</p>
                            </div>
                          </div>
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono text-ink">
                          {product.currency} {product.price?.toLocaleString() ?? '—'}
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono text-ink">{stock}</td>
                        <td className="px-4 py-2.5">
                          <Badge variant={onsale ? 'neutral' : 'red'}>{onsale ? '在售' : '缺货'}</Badge>
                        </td>
                        <td className="px-4 py-2.5 text-right">
                          <button className="text-[12px] text-accent hover:underline">编辑</button>
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
    </>
  )
}
