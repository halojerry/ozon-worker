import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '@/components/layout/page-header'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Empty } from '@/components/ui/empty'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type TaskListItem = components['schemas']['TaskListItem']
type TaskListResponse = components['schemas']['TaskListResponse']
type TaskImagesResponse = components['schemas']['TaskImagesResponse']

export const Route = createFileRoute('/_authenticated/image-studio')({
  component: ImageStudioRoute,
})

function ImageStudioRoute() {
  const [taskId, setTaskId] = useState<string | null>(null)
  const [regenLoading, setRegenLoading] = useState<string | null>(null)

  const { data: tasks } = useQuery<TaskListResponse>({
    queryKey: ['tasks-images'],
    queryFn: async () => {
      const { data } = await api.get<TaskListResponse>('/tasks', { params: { limit: 30 } })
      return data
    },
  })

  const { data: images } = useQuery<TaskImagesResponse>({
    queryKey: ['task-images', taskId],
    queryFn: async () => {
      const { data } = await api.get<TaskImagesResponse>(`/tasks/${taskId}/images`)
      return data
    },
    enabled: !!taskId,
  })

  const taskList = (tasks?.items ?? []) as TaskListItem[]

  async function handleRegen(slot: string) {
    if (!taskId) return
    setRegenLoading(slot)
    try {
      await api.post(`/tasks/${taskId}/images/${slot}/regen`)
      window.location.reload()
    } catch {
      alert('重新生图失败')
    } finally {
      setRegenLoading(null)
    }
  }

  return (
    <>
      <PageHeader
        kicker="运营 · AI 核心"
        title="图片工坊"
        description="任务图片管理：查看、重新生图、更新在线商品。"
      />

      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">任务数</p>
          <p className="font-mono text-[24px] font-bold text-accent">{taskList.length}</p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">已完成</p>
          <p className="font-mono text-[24px] font-bold text-ink">
            {taskList.filter((t) => t.status === 'completed').length}
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">运行中</p>
          <p className="font-mono text-[24px] font-bold text-ink">
            {taskList.filter((t) => t.status === 'running').length}
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">失败</p>
          <p className="font-mono text-[24px] font-bold text-ink">
            {taskList.filter((t) => t.status === 'failed').length}
          </p>
        </Card>
      </div>

      <Card className="mb-6">
        <div className="border-b border-line px-4 py-3">
          <p className="mb-2 text-[12px] font-semibold text-ink-3">选择任务</p>
          <select
            value={taskId ?? ''}
            onChange={(e) => setTaskId(e.target.value || null)}
            className="h-9 w-full max-w-[420px] rounded-input border border-line bg-surface px-3 text-[13px] text-ink focus:border-accent focus:outline-none"
          >
            <option value="">请选择任务</option>
            {taskList.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title || t.id.slice(0, 12)}（{t.status}）
              </option>
            ))}
          </select>
        </div>
      </Card>

      {images?.images?.length ? (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-5">
          {images.images.map((img) => (
            <Card key={`${img.slot}-${img.version}`} className="overflow-hidden">
              <div className="aspect-square w-full bg-thumb">
                {img.url ? (
                  <img
                    src={img.url}
                    alt={img.slot}
                    className="size-full object-cover"
                    loading="lazy"
                    onError={(e) => {
                      ;(e.target as HTMLImageElement).style.display = 'none'
                    }}
                  />
                ) : null}
              </div>
              <div className="flex items-center justify-between p-3">
                <Badge>{img.slot}</Badge>
                <button
                  className="text-[11px] text-accent hover:underline"
                  onClick={() => handleRegen(img.slot)}
                  disabled={regenLoading === img.slot}
                >
                  {regenLoading === img.slot ? '生成中...' : '重新生图'}
                </button>
              </div>
            </Card>
          ))}
        </div>
      ) : taskId ? (
        <Card>
          <Empty description="该任务暂无图片" />
        </Card>
      ) : (
        <Card>
          <Empty description="选择任务后查看图片" />
        </Card>
      )}
    </>
  )
}
