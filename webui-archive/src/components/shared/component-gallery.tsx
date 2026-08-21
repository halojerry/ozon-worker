import { useState } from 'react'
import { Badge, Tag } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Empty } from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Metric } from '@/components/ui/metric'
import { Table, type TableColumn } from '@/components/ui/table'
import { Tabs } from '@/components/ui/tabs'
import { Pagination } from '@/components/shared/pagination'
import { ImageCell } from '@/components/shared/image-cell'

/**
 * ComponentGallery —— W0 组件实样演示（spec §06 全部原子组件可交互渲染）
 * 在仪表盘页展示，供验收：按钮 6 态 / 徽标 3 态 / 输入框 4 态 / Tab /
 * 指标卡 / 表格（hover+选中）/ 空态 / 卡片 / 分页 / 图片组件。
 */

interface DemoRow {
  id: string
  name: string
  price: number
  status: string
  img?: string | null
}

const demoData: DemoRow[] = [
  { id: 'p1', name: '无线蓝牙耳机 Pro', price: 129, status: '在售' },
  { id: 'p2', name: '智能手表 S3', price: 399, status: '待上架' },
  { id: 'p3', name: '保温杯 500ml', price: 59.9, status: '在售' },
]

export function ComponentGallery() {
  const [tab, setTab] = useState('all')
  const [selected, setSelected] = useState<Set<string>>(new Set(['p2']))
  const [page, setPage] = useState(1)
  const [input, setInput] = useState('')
  const [search, setSearch] = useState('')

  const columns: TableColumn<DemoRow>[] = [
    {
      key: 'product',
      header: '商品',
      render: (row) => (
        <div className="flex items-center gap-3">
          <ImageCell src={row.img} alt={row.name} size="sm" />
          <span className="font-medium text-ink">{row.name}</span>
        </div>
      ),
    },
    {
      key: 'price',
      header: '价格',
      className: 'font-mono text-ink',
      render: (row) => `¥ ${row.price.toFixed(2)}`,
    },
    {
      key: 'status',
      header: '状态',
      render: (row) => <Badge variant={row.status === '待上架' ? 'red' : 'neutral'}>{row.status}</Badge>,
    },
  ]

  return (
    <div className="grid gap-gap-section">
      {/* 指标卡 */}
      <Card title="指标卡 Metric" action={<Badge variant="dark">data-lg 等宽数字</Badge>}>
        <div className="flex flex-wrap gap-4">
          <Metric
            label="今日订单"
            value="1,284"
            accent
            delta={{ label: '较昨日', value: '+12.4%', direction: 'up' }}
          />
          <Metric
            label="上架成功率"
            value="96.8%"
            accent
            delta={{ label: '较昨日', value: '+2.1%', direction: 'up' }}
          />
          <Metric
            label="退款率"
            value="2.1%"
            delta={{ label: '较昨日', value: '-0.3%', direction: 'down' }}
          />
        </div>
      </Card>

      {/* 按钮 */}
      <Card title="按钮 Button" action={<Badge>radius 6px · 100ms</Badge>}>
        <div className="flex flex-wrap items-center gap-3">
          <Button>新建商品</Button>
          <Button variant="secondary">批量导出</Button>
          <Button variant="ghost">取消</Button>
          <Button variant="danger">删除商品</Button>
          <Button loading>提交中</Button>
          <Button disabled>已禁用</Button>
        </div>
      </Card>

      {/* 徽标 */}
      <Card title="徽标与标签 Badge / Tag">
        <div className="flex flex-wrap items-center gap-2">
          <Badge>已上架</Badge>
          <Badge variant="red">待上架</Badge>
          <Badge variant="dark">平台侧</Badge>
          <Tag>在售</Tag>
          <Tag variant="red">缺货</Tag>
          <Tag>AI 生成</Tag>
        </div>
      </Card>

      {/* 输入框 */}
      <Card title="输入框 Input">
        <div className="grid gap-6 sm:grid-cols-2">
          <div className="space-y-4">
            <Input
              leading={<span className="text-ink-5">⌕</span>}
              placeholder="搜索商品名称 / SKU…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              hint="标准态：聚焦后红边框 + 红光晕"
            />
            <Input
              leading={<span>!</span>}
              placeholder="库存不能为负数"
              error
              hint="输入非法时红框 + 红浅底，下方纠错提示"
            />
            <Input placeholder="已锁定的字段" disabled hint="disabled=灰底浅字，不响应交互" />
          </div>
          <div>
            <Input
              leading={<span className="text-ink-5">⌕</span>}
              placeholder="受控输入演示…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              hint={`已输入 ${input.length} 字符（受控组件）`}
            />
          </div>
        </div>
      </Card>

      {/* Tabs */}
      <Card title="Tabs 下划线式">
        <Tabs
          value={tab}
          onChange={setTab}
          items={[
            { key: 'all', label: '全部', count: 128 },
            { key: 'pending', label: '待上架', count: 12 },
            { key: 'oos', label: '缺货', count: 3 },
          ]}
        />
        <p className="mt-3 text-[12px] text-ink-aux">当前选中：{tab === 'all' ? '全部' : tab === 'pending' ? '待上架' : '缺货'} · 选中态红色下划线 + 600 字重</p>
      </Card>

      {/* 表格 */}
      <Card title="表格 Table" action={<Button variant="secondary" onClick={() => setSelected(new Set())}>清空选中</Button>} padded={false}>
        <Table
          columns={columns}
          data={demoData}
          rowKey={(row) => row.id}
          selectedKeys={selected}
          onRowClick={(row) => {
            setSelected((prev) => {
              const next = new Set(prev)
              if (next.has(row.id)) next.delete(row.id)
              else next.add(row.id)
              return next
            })
          }}
          empty={<Empty title="暂无数据" description="空态用虚线框 + 一句话说明，不放插画" />}
        />
        <div className="px-4 pb-2">
          <Pagination page={page} pageSize={10} total={128} onPageChange={setPage} />
        </div>
      </Card>

      {/* 空态 */}
      <Card title="空态 Empty">
        <Empty
          title="暂无采集数据"
          description="通过 1688 图搜 / Ozon 选品采集的商品会出现在这里。"
          action={<Button variant="secondary">开始采集</Button>}
        />
      </Card>
    </div>
  )
}
