import { useCallback, useEffect, useState } from 'react'
import {
  getDraftSubmissions,
  listCredentials,
  type DraftSubmission,
  type DraftSubmissionStatus,
} from '../api/client'

/**
 * M2.2 提交历史弹窗 —— 某草稿的提交时间线（每次提交：店铺 / 状态徽章 / 错误原因 / 提交时间）。
 * 入口：采集箱行「提交历史」按钮（简化方案，不在顶级导航加 tab）。
 */

/* ── 状态徽章映射（任务 M2.2 规定：published→已上架绿 / failed、rejected→红 / uploading→审核中黄 / pending→排队中灰） ── */
const STATUS_META: Record<DraftSubmissionStatus, { label: string; className: string }> = {
  pending: { label: '排队中', className: 'status-muted' },
  uploading: { label: '审核中', className: 'status-uploading' },
  published: { label: '已上架', className: 'status-published' },
  failed: { label: '失败', className: 'status-failed' },
  rejected: { label: '审核被拒', className: 'status-failed' },
}

function statusMeta(status: DraftSubmissionStatus) {
  return STATUS_META[status] ?? STATUS_META.pending
}

const ERROR_STATES: DraftSubmissionStatus[] = ['failed', 'rejected']

function fmtTime(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function extractError(err: unknown, fallback: string): string {
  const resp = (err as { response?: { data?: { detail?: string } } } | null)?.response
  return resp?.data?.detail || fallback
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  )
}

function WarningIcon() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M12 3.5L22 20H2L12 3.5z" />
      <path d="M12 9.5v5M12 17.2v.1" />
    </svg>
  )
}

export default function SubmissionHistory({
  draftId,
  draftTitle,
  onClose,
}: {
  draftId: string
  draftTitle?: string
  onClose: () => void
}) {
  const [submissions, setSubmissions] = useState<DraftSubmission[] | null>(null)
  const [error, setError] = useState('')
  /** 已展开错误原因的提交（failed/rejected 默认展开，其余收起） */
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  /** store_client_id → 店铺名（credentials 掩码列表解析；失败降级显示 client_id，非致命） */
  const [shopNames, setShopNames] = useState<Map<string, string>>(new Map())

  useEffect(() => {
    listCredentials()
      .then((creds) => {
        const map = new Map<string, string>()
        for (const c of creds) {
          if (c.ozon_client_id) map.set(c.ozon_client_id, c.shop_name || c.ozon_client_id)
        }
        setShopNames(map)
      })
      .catch(() => {
        /* 拿不到店铺名时直接显示 client_id，不阻断时间线 */
      })
  }, [])

  const load = useCallback(async () => {
    setError('')
    try {
      const data = await getDraftSubmissions(draftId)
      setSubmissions(data)
      setExpanded(new Set(data.filter((s) => ERROR_STATES.includes(s.status)).map((s) => s.id)))
    } catch (err) {
      setError(extractError(err, '加载提交历史失败'))
    }
  }, [draftId])

  useEffect(() => {
    load()
  }, [load])

  const toggleError = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const storeLabel = (s: DraftSubmission): string => {
    if (!s.store_client_id) return '—'
    return shopNames.get(s.store_client_id) ?? s.store_client_id
  }

  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal sub-history-modal" role="dialog" aria-modal="true" aria-label="提交历史">
        <div className="modal-header">
          <h2 className="modal-title">提交历史</h2>
          <button type="button" className="modal-close" aria-label="关闭" onClick={onClose}>
            <CloseIcon />
          </button>
        </div>

        <div className="sub-history-title" title={draftTitle}>
          {draftTitle || '（无标题）'}
        </div>

        {submissions === null ? (
          <div className="empty-state">
            <div className="spinner-inline" aria-hidden="true" />
            <p className="empty-state-text">加载提交记录…</p>
          </div>
        ) : error ? (
          <div className="empty-state">
            <div className="form-error" role="alert">
              <WarningIcon />
              <span>{error}</span>
            </div>
            <button className="btn" onClick={load}>重试</button>
          </div>
        ) : submissions.length === 0 ? (
          <div className="empty-state">
            <div className="placeholder-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.6">
                <path d="M12 8v5l3.5 2M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <p className="empty-state-title">该草稿还未提交上架</p>
            <p className="empty-state-text">在「编辑上架」页配置完成后提交，记录会显示在这里</p>
          </div>
        ) : (
          <ol className="sub-history-list">
            {submissions.map((s) => {
              const meta = statusMeta(s.status)
              const hasError = !!s.error_message
              const isOpen = expanded.has(s.id)
              return (
                <li key={s.id} className={`sub-history-item ${s.status}`}>
                  <span className="sub-history-dot" aria-hidden="true" />
                  <div className="sub-history-body">
                    <div className="sub-history-head">
                      <span className={`status-badge ${meta.className}`}>{meta.label}</span>
                      <span className="sub-history-store" title={s.store_client_id ?? undefined}>
                        {storeLabel(s)}
                      </span>
                      <span className="sub-history-time">{fmtTime(s.created_at)}</span>
                    </div>
                    {s.submitted_task_id && (
                      <div className="sub-history-task mono" title={s.submitted_task_id}>
                        任务 {s.submitted_task_id}
                      </div>
                    )}
                    {hasError && (
                      <div className="sub-history-error">
                        <button
                          type="button"
                          className="sub-history-error-toggle"
                          aria-expanded={isOpen}
                          onClick={() => toggleError(s.id)}
                        >
                          <WarningIcon />
                          {isOpen ? '收起错误原因' : '查看错误原因'}
                        </button>
                        {isOpen && <pre className="sub-history-error-text">{s.error_message}</pre>}
                      </div>
                    )}
                  </div>
                </li>
              )
            })}
          </ol>
        )}

        <div className="modal-foot">
          <button type="button" className="btn" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}
