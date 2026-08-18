import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '@/components/layout/page-header'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Empty } from '@/components/ui/empty'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type CredentialOut = components['schemas']['CredentialOut']

export const Route = createFileRoute('/_authenticated/stores')({
  component: StoresRoute,
})

interface StoreStats {
  today_orders: number
  today_sales_amount: number
  today_profit: number
}

function StoresRoute() {
  const [showAdd, setShowAdd] = useState(false)
  const [clientId, setClientId] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [shopName, setShopName] = useState('')

  const { data: credentials, isLoading } = useQuery<CredentialOut[]>({
    queryKey: ['credentials'],
    queryFn: async () => {
      const { data } = await api.get<CredentialOut[]>('/credentials')
      return data
    },
  })

  const { data: statsMap } = useQuery<Record<string, StoreStats>>({
    queryKey: ['store-stats-map'],
    queryFn: async () => {
      const creds = credentials ?? []
      const map: Record<string, StoreStats> = {}
      await Promise.all(
        creds.map(async (c) => {
          try {
            const { data } = await api.get<StoreStats>(`/stores/${c.id}/stats`)
            map[c.id] = data
          } catch {
            map[c.id] = { today_orders: 0, today_sales_amount: 0, today_profit: 0 }
          }
        }),
      )
      return map
    },
    enabled: (credentials?.length ?? 0) > 0,
  })

  async function handleAdd() {
    if (!clientId.trim() || !apiKey.trim()) return
    try {
      await api.post('/credentials', {
        ozon_client_id: clientId.trim(),
        api_key: apiKey.trim(),
        shop_name: shopName.trim() || undefined,
        currency: 'RUB',
      })
      setShowAdd(false)
      setClientId('')
      setApiKey('')
      setShopName('')
      window.location.reload()
    } catch {
      alert('添加店铺失败，请检查凭证')
    }
  }

  return (
    <>
      <PageHeader
        kicker="数据与配置 · 多店铺"
        title="店铺管理"
        description="管理您的 Ozon 店铺账号。"
        actions={<Button onClick={() => setShowAdd(true)}>+ 添加店铺</Button>}
      />

      {isLoading ? (
        <div className="p-8 text-center text-ink-aux">加载中...</div>
      ) : credentials?.length ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {credentials.map((cred) => {
            const stats = statsMap?.[cred.id]
            const ok = cred.status === 'active' || cred.status === 'valid'
            return (
              <Card key={cred.id} className="flex flex-col">
                <div className="flex items-center justify-between p-4 pb-2">
                  <div className="min-w-0">
                    <p className="truncate text-[14px] font-semibold text-ink">
                      {cred.shop_name || cred.ozon_client_id}
                    </p>
                    <p className="text-[11px] text-ink-aux">Ozon · {cred.currency}</p>
                  </div>
                  <Badge variant={ok ? 'neutral' : 'red'}>{ok ? '正常' : '异常'}</Badge>
                </div>
                <div className="grid grid-cols-3 gap-2 p-4 pt-2">
                  <div>
                    <p className="text-[11px] text-ink-aux">今日订单</p>
                    <p className="font-mono text-[18px] font-bold text-ink">
                      {stats?.today_orders ?? '--'}
                    </p>
                  </div>
                  <div>
                    <p className="text-[11px] text-ink-aux">销售额</p>
                    <p className="font-mono text-[18px] font-bold text-ink">
                      ₽ {stats?.today_sales_amount?.toLocaleString() ?? '--'}
                    </p>
                  </div>
                  <div>
                    <p className="text-[11px] text-ink-aux">利润</p>
                    <p className="font-mono text-[18px] font-bold text-accent">
                      ₽ {stats?.today_profit?.toLocaleString() ?? '--'}
                    </p>
                  </div>
                </div>
                <div className="mt-auto border-t border-line p-3">
                  <button
                    className="w-full rounded-input border border-accent py-1.5 text-[12px] text-accent hover:bg-accent-soft"
                    onClick={() => api.post(`/stores/${cred.id}/sync`).then(() => window.location.reload())}
                  >
                    同步数据
                  </button>
                </div>
              </Card>
            )
          })}
        </div>
      ) : (
        <Empty description="暂无店铺，点击右上角添加" />
      )}

      {showAdd && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6"
          onClick={() => setShowAdd(false)}
        >
          <div className="w-full max-w-[420px] rounded-panel bg-surface p-6 shadow-overlay" onClick={(e) => e.stopPropagation()}>
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-h3 text-ink">添加店铺</h3>
              <button className="text-ink-aux hover:text-ink" onClick={() => setShowAdd(false)}>✕</button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="mb-1 block text-[13px] font-medium text-ink">Ozon Client ID</label>
                <Input value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="4718259" />
              </div>
              <div>
                <label className="mb-1 block text-[13px] font-medium text-ink">API Key</label>
                <Input value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="sk-..." type="password" />
              </div>
              <div>
                <label className="mb-1 block text-[13px] font-medium text-ink">店铺名称（可选）</label>
                <Input value={shopName} onChange={(e) => setShopName(e.target.value)} placeholder="Ozon 旗舰店" />
              </div>
              <Button className="w-full" onClick={handleAdd} disabled={!clientId.trim() || !apiKey.trim()}>
                保存
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
