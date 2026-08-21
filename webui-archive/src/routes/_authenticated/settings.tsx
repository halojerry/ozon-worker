import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '@/components/layout/page-header'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Empty } from '@/components/ui/empty'
import { Tabs } from '@/components/ui/tabs'
import { api } from '@/api/client'
import type { components } from '@/api/generated'

type SiteAnnouncementOut = components['schemas']['SiteAnnouncementOut']
type SiteBannerOut = components['schemas']['SiteBannerOut']
type QueryRow = components['schemas']['QueryRow']
type BackupItem = components['schemas']['BackupItem']

export const Route = createFileRoute('/_authenticated/settings')({
  component: SettingsRoute,
})

function SettingsRoute() {
  const [tab, setTab] = useState('announcements')
  const [annTitle, setAnnTitle] = useState('')
  const [annContent, setAnnContent] = useState('')
  const [queryInput, setQueryInput] = useState('')

  const { data: announcements } = useQuery<SiteAnnouncementOut[]>({
    queryKey: ['admin-announcements'],
    queryFn: async () => {
      const { data } = await api.get('/admin/site/announcements')
      return Array.isArray(data) ? data : []
    },
    enabled: tab === 'announcements',
  })

  const { data: banners } = useQuery<SiteBannerOut[]>({
    queryKey: ['admin-banners'],
    queryFn: async () => {
      const { data } = await api.get('/admin/site/banners')
      return Array.isArray(data) ? data : []
    },
    enabled: tab === 'announcements',
  })

  const { data: configs } = useQuery<{ name: string }[]>({
    queryKey: ['admin-configs'],
    queryFn: async () => {
      const { data } = await api.get('/admin/config')
      return Array.isArray(data) ? data : []
    },
    enabled: tab === 'config',
  })

  const { data: backups } = useQuery<BackupItem[]>({
    queryKey: ['admin-backups'],
    queryFn: async () => {
      const { data } = await api.get('/admin/config/system-default.json/backups')
      return Array.isArray(data) ? data : []
    },
    enabled: tab === 'config',
  })

  const { data: queries } = useQuery<QueryRow[]>({
    queryKey: ['admin-queries'],
    queryFn: async () => {
      const { data } = await api.get('/admin/queries')
      return Array.isArray(data) ? data : []
    },
    enabled: tab === 'queries',
  })

  async function publishAnnouncement() {
    if (!annTitle.trim() || !annContent.trim()) return
    try {
      await api.post('/admin/site/announcements', {
        title: annTitle.trim(),
        content: annContent.trim(),
        enabled: true,
      })
      setAnnTitle('')
      setAnnContent('')
      window.location.reload()
    } catch {
      alert('发布失败')
    }
  }

  async function importQueries() {
    const keywords = queryInput
      .split(/[\n,，]/)
      .map((s) => s.trim())
      .filter(Boolean)
    if (keywords.length === 0) return
    try {
      await api.post('/admin/queries/import', { items: keywords.map((keyword) => ({ keyword })) })
      setQueryInput('')
      window.location.reload()
    } catch {
      alert('导入失败')
    }
  }

  return (
    <>
      <PageHeader
        kicker="数据与配置 · 配置"
        title="系统设置"
        description="站点公告、配置备份、查询词管理、业务参数。"
      />

      <Tabs
        className="mb-6"
        value={tab}
        onChange={setTab}
        items={[
          { key: 'announcements', label: '站点公告' },
          { key: 'config', label: '配置备份' },
          { key: 'queries', label: '查询词管理' },
          { key: 'business', label: '业务参数' },
        ]}
      />

      {tab === 'announcements' && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card title="发布公告 / 横幅">
            <div className="space-y-3 p-4">
              <div>
                <label className="mb-1 block text-[13px] font-medium text-ink">标题</label>
                <Input value={annTitle} onChange={(e) => setAnnTitle(e.target.value)} placeholder="请输入标题" />
              </div>
              <div>
                <label className="mb-1 block text-[13px] font-medium text-ink">内容</label>
                <textarea
                  className="w-full rounded-input border border-line bg-surface px-3 py-2 text-[13px] focus:border-accent focus:outline-none"
                  value={annContent}
                  onChange={(e) => setAnnContent(e.target.value)}
                  placeholder="请输入内容"
                  rows={4}
                />
              </div>
              <Button onClick={publishAnnouncement} disabled={!annTitle.trim() || !annContent.trim()}>
                发布
              </Button>
            </div>
          </Card>
          <Card title="公告列表">
            {announcements?.length || banners?.length ? (
              <div className="divide-y divide-line">
                {(announcements ?? []).map((a) => (
                  <div key={a.id} className="flex items-center justify-between px-4 py-3">
                    <div className="min-w-0">
                      <p className="truncate text-[13px] font-medium text-ink">{a.title}</p>
                      <p className="truncate text-[11px] text-ink-aux">{a.content}</p>
                    </div>
                    <Badge>{a.enabled ? '启用' : '停用'}</Badge>
                  </div>
                ))}
                {(banners ?? []).map((b) => (
                  <div key={b.id} className="flex items-center justify-between px-4 py-3">
                    <p className="truncate text-[13px] font-medium text-ink">{b.title || '横幅'}</p>
                    <Badge>{b.enabled ? '启用' : '停用'}</Badge>
                  </div>
                ))}
              </div>
            ) : (
              <Empty description="暂无公告" />
            )}
          </Card>
        </div>
      )}

      {tab === 'config' && (
        <Card>
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <p className="text-[13px] font-medium text-ink">配置文件</p>
            <Button variant="secondary" onClick={() => api.post('/admin/config/system-default.json/rollback')}>
              一键回滚
            </Button>
          </div>
          {backups?.length ? (
            <div className="divide-y divide-line">
              {backups.map((b) => (
                <div key={b.name} className="flex items-center justify-between px-4 py-3">
                  <div>
                    <p className="font-mono text-[13px] text-ink">{b.name}</p>
                    <p className="text-[11px] text-ink-aux">{b.size} bytes</p>
                  </div>
                  <Badge>备份</Badge>
                </div>
              ))}
            </div>
          ) : (
            <Empty description="暂无备份记录" />
          )}
          <div className="border-t border-line px-4 py-3">
            <p className="text-[12px] text-ink-aux">
              配置文件：{(configs ?? []).map((c) => c.name).join(', ') || 'system-default.json'}
            </p>
          </div>
        </Card>
      )}

      {tab === 'queries' && (
        <Card>
          <div className="flex items-center gap-3 border-b border-line px-4 py-3">
            <Input
              placeholder="输入查询词（逗号/换行分隔，支持中英俄）"
              value={queryInput}
              onChange={(e) => setQueryInput(e.target.value)}
              className="max-w-[360px]"
            />
            <Button onClick={importQueries} disabled={!queryInput.trim()}>
              导入查询词
            </Button>
          </div>
          {queries?.length ? (
            <div className="divide-y divide-line">
              {queries.map((q) => (
                <div key={String(q.id ?? q.query)} className="flex items-center justify-between px-4 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-[13px] text-ink">{q.query}</p>
                    <p className="text-[11px] text-ink-aux">{q.count ?? 0} 次搜索</p>
                  </div>
                  <Badge>{q.source || '—'}</Badge>
                </div>
              ))}
            </div>
          ) : (
            <Empty description="暂无查询词" />
          )}
        </Card>
      )}

      {tab === 'business' && (
        <Card>
          <div className="p-4">
            <Empty description="业务参数（自动同步/库存预警/发货单等）将在后续版本开放" />
          </div>
        </Card>
      )}
    </>
  )
}
