import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '@/components/layout/page-header'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Empty } from '@/components/ui/empty'
import { Tabs } from '@/components/ui/tabs'
import { Pagination } from '@/components/shared/pagination'
import { resolveStatusMap, TASK_STATUS_MAP } from '@/lib/constants'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type AdminOverviewOut = components['schemas']['AdminOverviewOut']
type AdminUserOut = components['schemas']['AdminUserOut']
type AdminStoreOut = components['schemas']['AdminStoreOut']
type TaskListItem = components['schemas']['TaskListItem']
type TaskListResponse = components['schemas']['TaskListResponse']

export const Route = createFileRoute('/_authenticated/admin')({
  component: AdminRoute,
})

const PAGE_SIZE = 20

function AdminRoute() {
  const [tab, setTab] = useState('overview')
  const [page, setPage] = useState(1)

  const { data: overview } = useQuery<AdminOverviewOut>({
    queryKey: ['admin-overview'],
    queryFn: async () => {
      const { data } = await api.get<AdminOverviewOut>('/admin/overview')
      return data
    },
  })

  const { data: users } = useQuery<AdminUserOut[]>({
    queryKey: ['admin-users'],
    queryFn: async () => {
      const { data } = await api.get<AdminUserOut[]>('/admin/users')
      return data
    },
    enabled: tab === 'users',
  })

  const { data: stores } = useQuery<AdminStoreOut[]>({
    queryKey: ['admin-stores'],
    queryFn: async () => {
      const { data } = await api.get<AdminStoreOut[]>('/admin/stores')
      return data
    },
    enabled: tab === 'stores',
  })

  const { data: tasks } = useQuery<TaskListResponse>({
    queryKey: ['admin-tasks', page],
    queryFn: async () => {
      const { data } = await api.get<TaskListResponse>('/admin/tasks', {
        params: { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE },
      })
      return data
    },
    enabled: tab === 'tasks',
  })

  const taskItems = (tasks?.items ?? []) as TaskListItem[]
  const taskTotal = tasks?.total ?? 0

  return (
    <>
      <PageHeader
        kicker="平台 · 管理"
        title="管理员后台"
        description="平台概览统计与用户/店铺/任务管理。"
      />

      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">注册用户数</p>
          <p className="font-mono text-[24px] font-bold text-accent">{overview?.user_count ?? '--'}</p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">绑定店铺数</p>
          <p className="font-mono text-[24px] font-bold text-ink">{overview?.store_count ?? '--'}</p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">任务总数</p>
          <p className="font-mono text-[24px] font-bold text-ink">{overview?.task_total ?? '--'}</p>
        </Card>
        <Card className="p-4">
          <p className="text-[12px] text-ink-aux">今日任务</p>
          <p className="font-mono text-[24px] font-bold text-ink">{overview?.task_today ?? '--'}</p>
        </Card>
      </div>

      <Tabs
        className="mb-6"
        value={tab}
        onChange={setTab}
        items={[
          { key: 'overview', label: '概览' },
          { key: 'users', label: '用户管理' },
          { key: 'stores', label: '店铺管理' },
          { key: 'tasks', label: '任务中心' },
        ]}
      />

      {tab === 'overview' && (
        <Card>
          <div className="p-4">
            <p className="text-[13px] text-ink">上架成功率：{overview?.success_rate != null ? `${(overview.success_rate * 100).toFixed(1)}%` : '--'}</p>
          </div>
        </Card>
      )}

      {tab === 'users' && (
        <Card>
          {users?.length ? (
            <div className="divide-y divide-line">
              {users.map((user) => (
                <div key={user.id} className="flex items-center justify-between px-4 py-3">
                  <div>
                    <p className="text-[13px] font-medium text-ink">{user.username}</p>
                    <p className="text-[11px] text-ink-aux">{user.role}</p>
                  </div>
                  <div className="flex items-center gap-4">
                    <span className="font-mono text-[12px] text-ink-aux">{user.store_count} 店铺</span>
                    <Badge>{user.task_count} 任务</Badge>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Empty description="暂无用户数据" />
          )}
        </Card>
      )}

      {tab === 'stores' && (
        <Card>
          {stores?.length ? (
            <div className="divide-y divide-line">
              {stores.map((store) => (
                <div key={store.id} className="flex items-center justify-between px-4 py-3">
                  <div>
                    <p className="text-[13px] font-medium text-ink">{store.shop_name || store.ozon_client_id}</p>
                    <p className="text-[11px] text-ink-aux">{store.currency}</p>
                  </div>
                  <Badge variant={store.status === 'active' ? 'neutral' : 'red'}>{store.status}</Badge>
                </div>
              ))}
            </div>
          ) : (
            <Empty description="暂无店铺数据" />
          )}
        </Card>
      )}

      {tab === 'tasks' && (
        <Card>
          {taskItems.length ? (
            <>
              <div className="divide-y divide-line">
                {taskItems.map((task) => {
                  const st = resolveStatusMap(TASK_STATUS_MAP, task.status)
                  return (
                    <div key={task.id} className="flex items-center justify-between px-4 py-3">
                      <div className="min-w-0">
                        <p className="truncate text-[13px] font-medium text-ink">{task.title || task.id.slice(0, 8)}</p>
                        <p className="text-[11px] text-ink-aux">{task.item_id || '—'}</p>
                      </div>
                      <Badge variant={st.variant}>{st.label}</Badge>
                    </div>
                  )
                })}
              </div>
              <Pagination total={taskTotal} page={page} pageSize={PAGE_SIZE} onPageChange={setPage} />
            </>
          ) : (
            <Empty description="暂无任务数据" />
          )}
        </Card>
      )}
    </>
  )
}
