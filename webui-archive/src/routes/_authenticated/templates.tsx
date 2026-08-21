import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '@/components/layout/page-header'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Empty } from '@/components/ui/empty'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type ListingTemplateOut = components['schemas']['ListingTemplateOut']

export const Route = createFileRoute('/_authenticated/templates')({
  component: TemplatesRoute,
})

const PARAM_FIELDS: Array<{ key: keyof NonNullable<ListingTemplateOut['config']>; label: string; fmt: (v: unknown) => string }> = [
  { key: 'margin_rate', label: '加价率', fmt: (v) => `${(((v as number) ?? 0) * 100).toFixed(2)}%` },
  { key: 'commission_rate', label: '佣金率', fmt: (v) => `${(((v as number) ?? 0) * 100).toFixed(2)}%` },
  { key: 'fx_buffer', label: '汇率缓冲', fmt: (v) => `${(((v as number) ?? 0) * 100).toFixed(2)}%` },
  { key: 'offer_id_prefix', label: 'OfferID 前缀', fmt: (v) => String(v ?? '—') },
  { key: 'stock', label: '库存', fmt: (v) => String(v ?? '—') },
  { key: 'warehouse_id', label: '仓库', fmt: (v) => String(v ?? '—') },
]

function TemplatesRoute() {
  const [showNew, setShowNew] = useState(false)
  const [name, setName] = useState('')
  const [margin, setMargin] = useState('25')
  const [commission, setCommission] = useState('10')
  const [fxBuffer, setFxBuffer] = useState('5')
  const [stock, setStock] = useState('100')

  const { data: templates, isLoading } = useQuery<ListingTemplateOut[]>({
    queryKey: ['templates'],
    queryFn: async () => {
      const { data } = await api.get<ListingTemplateOut[]>('/templates')
      return data
    },
  })

  async function handleCreate() {
    if (!name.trim()) return
    try {
      await api.post('/templates', {
        name: name.trim(),
        description: '',
        config: {
          margin_rate: Number(margin) / 100,
          commission_rate: Number(commission) / 100,
          fx_buffer: Number(fxBuffer) / 100,
          stock: Number(stock),
        },
      })
      setShowNew(false)
      setName('')
      window.location.reload()
    } catch {
      alert('创建模板失败')
    }
  }

  return (
    <>
      <PageHeader
        kicker="数据与配置 · 配置"
        title="上架模板"
        description="模板卡片：加价率、佣金率、汇率缓冲、库存、仓库。"
        actions={<Button onClick={() => setShowNew(true)}>+ 新建模板</Button>}
      />

      {isLoading ? (
        <div className="p-8 text-center text-ink-aux">加载中...</div>
      ) : templates?.length ? (
        <div className="space-y-4">
          {templates.map((tpl) => (
            <Card key={tpl.id}>
              <div className="flex items-center justify-between border-b border-line px-5 py-3">
                <div className="flex items-center gap-2.5">
                  <span className="text-[16px]">📋</span>
                  <div>
                    <div className="flex items-center gap-2">
                      <p className="text-[14px] font-semibold text-ink">{tpl.name}</p>
                      {tpl.is_default && <Badge variant="red">默认</Badge>}
                    </div>
                    <p className="text-[11px] text-ink-aux">{tpl.description || `模板 ID: ${tpl.id.slice(0, 8)}`}</p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <button className="text-[12px] text-ink-aux hover:text-ink">编辑</button>
                  <button
                    className="text-[12px] text-accent hover:underline"
                    onClick={() => api.post(`/templates/${tpl.id}/default`).then(() => window.location.reload())}
                  >
                    设为默认
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-4 px-5 py-4 lg:grid-cols-6">
                {PARAM_FIELDS.map((f) => (
                  <div key={String(f.key)}>
                    <p className="text-[11px] text-ink-aux">{f.label}</p>
                    <p className={`font-mono text-[14px] font-bold ${f.key === 'margin_rate' ? 'text-accent' : 'text-ink'}`}>
                      {f.fmt(tpl.config?.[f.key])}
                    </p>
                  </div>
                ))}
              </div>

              {tpl.store_overrides && Object.keys(tpl.store_overrides).length > 0 && (
                <div className="border-t border-line px-5 py-3">
                  <p className="mb-2 text-[12px] font-semibold text-ink-3">店铺差异化覆盖</p>
                  <div className="overflow-x-auto">
                    <table className="w-full text-[12px]">
                      <thead>
                        <tr className="bg-header text-left text-ink-3">
                          <th className="px-3 py-1.5">店铺</th>
                          <th className="px-3 py-1.5">加价率</th>
                          <th className="px-3 py-1.5">库存</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(tpl.store_overrides).map(([storeId, cfg]) => (
                          <tr key={storeId} className="border-t border-line">
                            <td className="px-3 py-1.5 text-ink">{storeId.slice(0, 8)}</td>
                            <td className="px-3 py-1.5 font-mono text-ink">
                              {cfg.margin_rate != null ? `${(cfg.margin_rate * 100).toFixed(2)}%` : '继承'}
                            </td>
                            <td className="px-3 py-1.5 font-mono text-ink">{cfg.stock ?? '继承'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </Card>
          ))}
        </div>
      ) : (
        <Empty description="暂无模板，点击右上角新建" />
      )}

      {showNew && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6"
          onClick={() => setShowNew(false)}
        >
          <div className="w-full max-w-[420px] rounded-panel bg-surface p-6 shadow-overlay" onClick={(e) => e.stopPropagation()}>
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-h3 text-ink">新建模板</h3>
              <button className="text-ink-aux hover:text-ink" onClick={() => setShowNew(false)}>✕</button>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="col-span-2">
                <label className="mb-1 block text-[13px] font-medium text-ink">模板名称</label>
                <input
                  className="w-full rounded-input border border-line bg-surface px-3 py-2 text-[13px] focus:border-accent focus:outline-none"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="默认模板"
                />
              </div>
              {[
                { label: '加价率 (%)', value: margin, set: setMargin },
                { label: '佣金率 (%)', value: commission, set: setCommission },
                { label: '汇率缓冲 (%)', value: fxBuffer, set: setFxBuffer },
                { label: '库存', value: stock, set: setStock },
              ].map((f) => (
                <div key={f.label}>
                  <label className="mb-1 block text-[13px] font-medium text-ink">{f.label}</label>
                  <input
                    className="w-full rounded-input border border-line bg-surface px-3 py-2 text-[13px] focus:border-accent focus:outline-none"
                    value={f.value}
                    onChange={(e) => f.set(e.target.value)}
                    type="number"
                  />
                </div>
              ))}
            </div>
            <Button className="mt-5 w-full" onClick={handleCreate} disabled={!name.trim()}>
              创建
            </Button>
          </div>
        </div>
      )}
    </>
  )
}
