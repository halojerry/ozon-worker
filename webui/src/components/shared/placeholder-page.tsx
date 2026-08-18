import type { ReactNode } from 'react'
import { PageHeader } from '@/components/layout/page-header'
import { Empty } from '@/components/ui/empty'

/**
 * PlaceholderPage —— W0 阶段占位页骨架。
 * 展示页面标题 + 模块范围说明 + 空态；W1-W3 各波次落地时逐页替换为真实实现。
 * （非「敬请期待」营销文案，是明确的模块职责说明。）
 */
export interface PlaceholderPageProps {
  kicker: string
  title: string
  description: string
  /** 该页面将接入的核心 API 端点（如实说明，供实现核对） */
  endpoints?: string[]
  actions?: ReactNode
}

export function PlaceholderPage({ kicker, title, description, endpoints = [], actions }: PlaceholderPageProps) {
  return (
    <div className="mx-auto w-full max-w-[1180px]">
      <PageHeader kicker={kicker} title={title} description={description} actions={actions} />
      <Empty
        title="模块脚手架已就位"
        description={`本页属于 ${title}。计划接入：${endpoints.join('、')}。\n将在对应实施波次落地真实数据与交互。`}
      />
    </div>
  )
}
