/**
 * 业务页共享状态元数据（S2.2 抽取：Home/Tasks/CollectBox 原各自维护 meta 字典）
 *
 * taskStatusMeta / draftStatusMeta：状态 → { label, className }。
 * 调用方不得在页面内重新维护同款字典（避免标签/颜色漂移）。
 */
import type { TaskStatus, DraftSubmissionStatus } from '@/api/client'

export interface StatusMeta {
  label: string
  className: string
}

export const TASK_STATUS_META: Record<TaskStatus, StatusMeta> = {
  pending: { label: '排队中', className: 'status-muted' },
  running: { label: '上架中', className: 'status-uploading' },
  completed: { label: '已完成', className: 'status-published' },
  failed: { label: '失败', className: 'status-failed' },
  rejected: { label: '审核被拒', className: 'status-failed' },
}

export const DRAFT_STATUS_META: Record<string, StatusMeta> = {
  pending: { label: '未上架', className: 'status-muted' },
  uploading: { label: '上架中', className: 'status-uploading' },
  published: { label: '已上架', className: 'status-published' },
  failed: { label: '失败', className: 'status-failed' },
  rejected: { label: '审核被拒', className: 'status-failed' },
}

export function taskStatusMeta(status: TaskStatus): StatusMeta {
  return TASK_STATUS_META[status] ?? TASK_STATUS_META.pending
}

export function draftStatusMeta(status: DraftSubmissionStatus | null | undefined): StatusMeta {
  return DRAFT_STATUS_META[status ?? ''] ?? DRAFT_STATUS_META.pending
}
