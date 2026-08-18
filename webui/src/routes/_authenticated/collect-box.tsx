import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '@/components/layout/page-header'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Empty } from '@/components/ui/empty'
import { ImageCell } from '@/components/shared/image-cell'
import { Pagination } from '@/components/shared/pagination'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type DraftOut = components['schemas']['DraftOut']

export const Route = createFileRoute('/_authenticated/collect-box')({
  component: CollectBoxRoute,
})

const PAGE_SIZE = 20

function CollectBoxRoute() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<string[]>([])

  const { data: drafts, isLoading } = useQuery<DraftOut[]>({
    queryKey: ['drafts-collect', page],
    queryFn: async () => {
      const { data } = await api.get<DraftOut[]>('/drafts', {
        params: { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE },
      })
      return data
    },
  })

  const list = drafts ?? []
  const pendingCount = list.filter((d) => !d.submission_status || d.submission_status === 'draft').length
  const submittedCount = list.filter((d) => d.submission_status).length

  function toggleSelect(id: string) {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  function batchSubmit() {
    if (selected.length === 0) return
    void Promise.all(
      selected.map((id) => api.post(`/drafts/${id}/submit`).catch(() => null)),
    ).then(() => {
      setSelected([])
      window.location.reload()
    })
  }

  return (
    <>
      <PageHeader
        kicker="运营 · 采集"
        title="采集箱"
        description="采集任务统计与商品管理。"
        actions={
          <Button onClick={batchSubmit} disabled={selected.length === 0}>
            批量导入（{selected.length}）
          </Button>
        }
      />

      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">已采集</p>
          <p className="font-mono text-[24px] font-bold text-accent">{list.length}</p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">待导入</p>
          <p className="font-mono text-[24px] font-bold text-ink">{pendingCount}</p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">已导入</p>
          <p className="font-mono text-[24px] font-bold text-ink">{submittedCount}</p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">采集来源</p>
          <p className="font-mono text-[24px] font-bold text-ink">
            {[...new Set(list.map((d) => d.source))].join(' / ') || '—'}
          </p>
        </Card>
      </div>

      <Card>
        <div className="flex items-center gap-3 border-b border-line px-4 py-3">
          <Input
            placeholder="搜索商品标题或 ID..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="max-w-[240px]"
          />
        </div>

        {isLoading ? (
          <div className="p-8 text-center text-ink-aux">加载中...</div>
        ) : list.length ? (
          <>
            <div className="divide-y divide-line">
              {list
                .filter((d) => {
                  const kw = search.trim().toLowerCase()
                  if (!kw) return true
                  const payload = d.payload as Record<string, unknown>
                  const draft = payload?.draft as Record<string, unknown> | undefined
                  return String(draft?.title ?? '').toLowerCase().includes(kw) || d.id.includes(kw)
                })
                .map((draft) => {
                  const payload = draft.payload as Record<string, unknown>
                  const draftData = payload?.draft as Record<string, unknown> | undefined
                  const images = (draftData?.images as string[]) ?? []
                  const title = (draftData?.title as string) || draft.id
                  const price = draftData?.purchase_cost as number | undefined
                  const submitted = !!draft.submission_status
                  return (
                    <div key={draft.id} className="flex items-center gap-3 px-4 py-3 transition-colors duration-fast hover:bg-header">
                      <input
                        type="checkbox"
                        className="accent-accent"
                        checked={selected.includes(draft.id)}
                        onChange={() => toggleSelect(draft.id)}
                      />
                      <ImageCell src={images[0]} alt={title} size="sm" />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[14px] font-medium text-ink">{title}</p>
                        <p className="text-[11px] text-ink-aux">
                          来源: {draft.source} · 版本 {draft.version}
                        </p>
                      </div>
                      <div className="w-24 text-right">
                        <p className="font-mono text-[13px] text-ink">
                          {price != null ? `¥ ${price.toLocaleString()}` : '—'}
                        </p>
                      </div>
                      <Badge variant={submitted ? 'neutral' : 'red'}>
                        {submitted ? '已导入' : '待处理'}
                      </Badge>
                    </div>
                  )
                })}
            </div>
            <Pagination total={list.length} page={page} pageSize={PAGE_SIZE} onPageChange={setPage} />
          </>
        ) : (
          <Empty description="暂无采集数据" />
        )}
      </Card>
    </>
  )
}
