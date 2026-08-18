import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '@/components/layout/page-header'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Empty } from '@/components/ui/empty'
import { Pagination } from '@/components/shared/pagination'
import { resolveStatusMap, TASK_STATUS_MAP } from '@/lib/constants'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type TaskListItem = components['schemas']['TaskListItem']
type TaskListResponse = components['schemas']['TaskListResponse']
type TaskStatisticsResponse = components['schemas']['TaskStatisticsResponse']

export const Route = createFileRoute('/_authenticated/tasks')({
  component: TasksRoute,
})

const PAGE_SIZE = 20

function TasksRoute() {
  const [page, setPage] = useState(1)

  const { data: stats } = useQuery<TaskStatisticsResponse>({
    queryKey: ['task-statistics'],
    queryFn: async () => {
      const { data } = await api.get<TaskStatisticsResponse>('/task_statistics')
      return data
    },
    refetchInterval: 15_000,
  })

  const { data, isLoading } = useQuery<TaskListResponse>({
    queryKey: ['tasks', page],
    queryFn: async () => {
      const { data } = await api.get<TaskListResponse>('/tasks', {
        params: { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE },
      })
      return data
    },
  })

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const done = (stats?.completed ?? 0) + (stats?.failed ?? 0)

  return (
    <>
      <PageHeader
        kicker="运营 · 自动化"
        title="任务中心"
        description="自动化任务概览与管理。"
      />

      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">运行中</p>
          <p className="font-mono text-[28px] font-bold text-accent">{stats?.running ?? 0}</p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">已完成</p>
          <p className="font-mono text-[28px] font-bold text-ink">{stats?.completed ?? 0}</p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">失败</p>
          <p className="font-mono text-[28px] font-bold text-accent">{stats?.failed ?? 0}</p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">总成功率</p>
          <p className="font-mono text-[28px] font-bold text-ink">
            {done > 0 ? `${(((stats?.completed ?? 0) / done) * 100).toFixed(1)}%` : '--'}
          </p>
        </Card>
      </div>

      <Card>
        {isLoading ? (
          <div className="p-8 text-center text-ink-aux">加载中...</div>
        ) : items.length ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="border-b border-line bg-header text-left text-[12px] text-ink-3">
                    <th className="px-4 py-2.5">任务</th>
                    <th className="px-4 py-2.5">商品</th>
                    <th className="px-4 py-2.5">状态</th>
                    <th className="px-4 py-2.5">创建时间</th>
                    <th className="px-4 py-2.5 text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((task: TaskListItem) => {
                    const st = resolveStatusMap(TASK_STATUS_MAP, task.status)
                    return (
                      <tr
                        key={task.id}
                        className="border-b border-line transition-colors duration-fast hover:bg-header"
                      >
                        <td className="px-4 py-2.5">
                          <p className="max-w-[280px] truncate text-ink">{task.title || task.id.slice(0, 8)}</p>
                          <p className="font-mono text-[11px] text-ink-aux">{task.id.slice(0, 8)}</p>
                        </td>
                        <td className="px-4 py-2.5 text-ink-aux">{task.item_id || '—'}</td>
                        <td className="px-4 py-2.5">
                          <Badge variant={st.variant}>{st.label}</Badge>
                        </td>
                        <td className="px-4 py-2.5 text-ink-aux">
                          {task.created_at ? String(task.created_at).slice(0, 19).replace('T', ' ') : '—'}
                        </td>
                        <td className="px-4 py-2.5 text-right">
                          {task.status === 'running' || task.status === 'pending' ? (
                            <button
                              className="text-[12px] text-accent hover:underline"
                              onClick={() => api.post(`/cancel_task/${task.id}`).then(() => window.location.reload())}
                            >
                              取消
                            </button>
                          ) : task.status === 'failed' ? (
                            <button
                              className="text-[12px] text-accent hover:underline"
                              onClick={() => api.post(`/resubmit_task/${task.id}`).then(() => window.location.reload())}
                            >
                              重试
                            </button>
                          ) : null}
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
          <Empty description="暂无任务数据" />
        )}
      </Card>
    </>
  )
}
