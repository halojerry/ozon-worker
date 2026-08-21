import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '@/components/layout/page-header'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Empty } from '@/components/ui/empty'
import { ImageCell } from '@/components/shared/image-cell'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type DraftOut = components['schemas']['DraftOut']
type TaskStatisticsResponse = components['schemas']['TaskStatisticsResponse']

export const Route = createFileRoute('/_authenticated/on-sale')({
  component: OnSaleRoute,
})

function OnSaleRoute() {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [aiTitle, setAiTitle] = useState('')
  const [aiDesc, setAiDesc] = useState('')
  const [aiLoading, setAiLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const { data: stats } = useQuery<TaskStatisticsResponse>({
    queryKey: ['task-statistics'],
    queryFn: async () => {
      const { data } = await api.get<TaskStatisticsResponse>('/task_statistics')
      return data
    },
  })

  const { data: drafts, isLoading } = useQuery<DraftOut[]>({
    queryKey: ['drafts-onsale'],
    queryFn: async () => {
      const { data } = await api.get<DraftOut[]>('/drafts', { params: { limit: 50 } })
      return data
    },
  })

  const list = drafts ?? []

  async function handleAiFill(field: 'title' | 'description') {
    if (!selectedId) return
    setAiLoading(true)
    try {
      const { data } = await api.post<{ field: string; value: string }>(
        `/drafts/${selectedId}/ai/${field}`,
        {},
      )
      if (field === 'title') setAiTitle(data.value)
      else setAiDesc(data.value)
    } catch {
      alert('AI 生成失败')
    } finally {
      setAiLoading(false)
    }
  }

  async function handleSubmit() {
    if (!selectedId) return
    setSubmitting(true)
    try {
      await api.post(`/drafts/${selectedId}/submit`)
      alert('已提交上架')
      window.location.reload()
    } catch {
      alert('提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  const done = (stats?.completed ?? 0) + (stats?.failed ?? 0)

  return (
    <>
      <PageHeader
        kicker="运营 · AI 核心"
        title="上架工作台"
        description="草稿列表 + AI 填充 + 估价与提交上架。"
      />

      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">待处理任务</p>
          <p className="font-mono text-[24px] font-bold text-accent">{stats?.pending ?? 0}</p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">上架中</p>
          <p className="font-mono text-[24px] font-bold text-ink">{stats?.running ?? 0}</p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">AI 上架成功率</p>
          <p className="font-mono text-[24px] font-bold text-accent">
            {done > 0 ? `${(((stats?.completed ?? 0) / done) * 100).toFixed(1)}%` : '--'}
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">草稿数</p>
          <p className="font-mono text-[24px] font-bold text-ink">{list.length}</p>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <Card>
          {isLoading ? (
            <div className="p-8 text-center text-ink-aux">加载中...</div>
          ) : list.length ? (
            <div className="divide-y divide-line">
              {list.map((draft) => {
                const payload = draft.payload as Record<string, unknown>
                const draftData = payload?.draft as Record<string, unknown> | undefined
                const images = (draftData?.images as string[]) ?? []
                const title = (draftData?.title as string) || draft.id
                const selected = selectedId === draft.id
                return (
                  <div
                    key={draft.id}
                    onClick={() => {
                      setSelectedId(draft.id)
                      setAiTitle('')
                      setAiDesc('')
                    }}
                    className={`flex cursor-pointer items-center gap-3 px-4 py-3 transition-colors duration-fast ${
                      selected ? 'bg-accent-soft' : 'hover:bg-header'
                    }`}
                  >
                    <ImageCell src={images[0]} alt={title} size="md" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[14px] font-medium text-ink">{title}</p>
                      <p className="text-[11px] text-ink-aux">
                        {draft.id.slice(0, 8)} · 来源 {draft.source}
                      </p>
                    </div>
                    <Badge variant={draft.submission_status ? 'neutral' : 'red'}>
                      {draft.submission_status || '草稿'}
                    </Badge>
                  </div>
                )
              })}
            </div>
          ) : (
            <Empty description="暂无草稿数据" />
          )}
        </Card>

        <div className="space-y-4">
          <Card title="AI 上架建议">
            {selectedId ? (
              <div className="space-y-4 p-4">
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <label className="text-[12px] font-medium text-ink-3">标题</label>
                    <button
                      className="text-[11px] text-accent hover:underline"
                      onClick={() => handleAiFill('title')}
                      disabled={aiLoading}
                    >
                      {aiLoading ? '生成中...' : 'AI 生成'}
                    </button>
                  </div>
                  <textarea
                    className="w-full rounded-input border border-line bg-surface px-3 py-2 text-[13px] focus:border-accent focus:outline-none"
                    value={aiTitle}
                    onChange={(e) => setAiTitle(e.target.value)}
                    placeholder="点击 AI 生成或手动输入标题"
                    rows={2}
                  />
                </div>
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <label className="text-[12px] font-medium text-ink-3">描述</label>
                    <button
                      className="text-[11px] text-accent hover:underline"
                      onClick={() => handleAiFill('description')}
                      disabled={aiLoading}
                    >
                      {aiLoading ? '生成中...' : 'AI 生成'}
                    </button>
                  </div>
                  <textarea
                    className="w-full rounded-input border border-line bg-surface px-3 py-2 text-[13px] focus:border-accent focus:outline-none"
                    value={aiDesc}
                    onChange={(e) => setAiDesc(e.target.value)}
                    placeholder="点击 AI 生成或手动输入描述"
                    rows={4}
                  />
                </div>
                <Button className="w-full" onClick={handleSubmit} disabled={submitting}>
                  {submitting ? '提交中...' : '一键采用并上架'}
                </Button>
              </div>
            ) : (
              <div className="p-4 text-[12px] text-ink-aux">选择左侧草稿后，可在此生成 AI 标题/描述</div>
            )}
          </Card>
        </div>
      </div>
    </>
  )
}
