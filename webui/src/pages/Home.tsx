import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from '@/lib/router-compat'
import {
  estimateDraft,
  getDrafts,
  getTaskDraft,
  listTasks,
  resubmitTask,
  type Draft,
  type DraftEstimate,
  type DraftSubmissionStatus,
  type TaskListItem,
  type TaskStatus,
} from '../api/client'

/* ── 状态元数据（与 Tasks/CollectBox 同源映射） ── */

const TASK_STATUS_META: Record<TaskStatus, { label: string; className: string }> = {
  pending: { label: '排队中', className: 'status-muted' },
  running: { label: '上架中', className: 'status-uploading' },
  completed: { label: '已完成', className: 'status-published' },
  failed: { label: '失败', className: 'status-failed' },
  rejected: { label: '审核被拒', className: 'status-failed' },
}

const DRAFT_STATUS_META: Record<string, { label: string; className: string }> = {
  pending: { label: '未上架', className: 'status-muted' },
  uploading: { label: '上架中', className: 'status-uploading' },
  published: { label: '已上架', className: 'status-published' },
  failed: { label: '失败', className: 'status-failed' },
  rejected: { label: '审核被拒', className: 'status-failed' },
}

function taskStatusMeta(status: TaskStatus) {
  return TASK_STATUS_META[status] ?? TASK_STATUS_META.pending
}

function draftStatusMeta(status: DraftSubmissionStatus | null | undefined) {
  return DRAFT_STATUS_META[status ?? ''] ?? DRAFT_STATUS_META.pending
}

/* ── 工具函数（与 Tasks/CollectBox 同款本地实现） ── */

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

function ImageCell({ src, alt }: { src?: string; alt: string }) {
  const [broken, setBroken] = useState(false)
  if (!src || broken) {
    return (
      <div className="img-placeholder" role="img" aria-label={`图片加载失败：${alt}`}>
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6">
          <rect x="3.5" y="3.5" width="17" height="17" rx="2.5" />
          <circle cx="9" cy="9" r="1.8" />
          <path d="M4.5 18.5l5-5 3.5 3.5 3-3 3.5 3.5" />
        </svg>
      </div>
    )
  }
  return <img className="draft-thumb" src={src} alt={alt} loading="lazy" onError={() => setBroken(true)} />
}

/* ── M1.2 预估懒加载：模块级 Promise 缓存去重 + 并发节流（同 CollectBox） ── */
const estimateCache = new Map<string, Promise<DraftEstimate | null>>()
const ESTIMATE_MAX_IN_FLIGHT = 4
let estimateInFlight = 0
const estimateWaiters: Array<() => void> = []

function acquireEstimateSlot(): Promise<void> {
  if (estimateInFlight < ESTIMATE_MAX_IN_FLIGHT) {
    estimateInFlight++
    return Promise.resolve()
  }
  return new Promise((resolve) => estimateWaiters.push(resolve))
}

function releaseEstimateSlot(): void {
  estimateInFlight--
  estimateWaiters.shift()?.()
}

function loadEstimate(draftId: string): Promise<DraftEstimate | null> {
  const hit = estimateCache.get(draftId)
  if (hit) return hit
  const pending = (async () => {
    await acquireEstimateSlot()
    try {
      return await estimateDraft(draftId)
    } catch {
      return null // 预估失败静默降级「—」，不阻断卡片
    } finally {
      releaseEstimateSlot()
    }
  })()
  estimateCache.set(draftId, pending)
  return pending
}

const CURRENCY_SYMBOL: Record<string, string> = { CNY: '¥', RUB: '₽', USD: '$' }

function fmtMoney(v: number | undefined, currency?: string): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—'
  const sym = (currency && CURRENCY_SYMBOL[currency]) || '¥'
  return `${sym}${v.toFixed(2)}`
}

function fmtRate(v: number | undefined): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—'
  return `${(v * 100).toFixed(1)}%`
}

/** 待处理草稿的决策注入：预估售价/利润/利润率（worker 定价引擎值，前端不自算） */
function EstimateBadges({ draftId }: { draftId: string }) {
  const [est, setEst] = useState<DraftEstimate | null>(null)
  useEffect(() => {
    let alive = true
    loadEstimate(draftId).then((r) => {
      if (alive) setEst(r)
    })
    return () => {
      alive = false
    }
  }, [draftId])
  return (
    <span className="home-estimate" title="预估售价 / 预估利润 / 利润率（worker 定价引擎）">
      <span className="home-est-price">售价 {est ? fmtMoney(est.price, est.currency) : '—'}</span>
      <span className="home-est-profit">利润 {est ? fmtMoney(est.profit_cny, est.currency) : '—'}</span>
      <span className="home-est-rate">率 {est ? fmtRate(est.profit_rate) : '—'}</span>
    </span>
  )
}

/* ── 分组卡片头 ── */

function GroupHead({
  title,
  count,
  toneClass,
  hint,
  children,
}: {
  title: string
  count: number
  toneClass: string
  hint: string
  children?: React.ReactNode
}) {
  return (
    <div className="home-group-head">
      <span className={`home-group-dot ${toneClass}`} aria-hidden="true" />
      <h2 className="home-group-title">{title}</h2>
      <span className="home-group-count">{count}</span>
      <span className="home-group-hint">{hint}</span>
      {children}
    </div>
  )
}

export default function Home() {
  const navigate = useNavigate()
  const [tasks, setTasks] = useState<TaskListItem[]>([])
  const [drafts, setDrafts] = useState<Draft[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  /** 已探明「无草稿来源」的任务（直连提交）→ 回采集箱改按钮禁用 */
  const [noDraftIds, setNoDraftIds] = useState<Set<string>>(new Set())
  /** 正在查询 draft_id 的任务 */
  const [draftBusyIds, setDraftBusyIds] = useState<Set<string>>(new Set())
  const fetchingRef = useRef(false)

  /** 并行聚合任务 + 草稿；单源失败不拖垮整页（部分渲染 + 双源全败才报错） */
  const load = useCallback(async (silent = false) => {
    if (fetchingRef.current) return
    fetchingRef.current = true
    if (!silent) setLoading(true)
    try {
      const [taskRes, draftRes] = await Promise.allSettled([
        listTasks({ limit: 100 }),
        getDrafts(),
      ])
      if (taskRes.status === 'fulfilled') {
        setTasks(taskRes.value.items)
        setLoadError('')
      }
      if (draftRes.status === 'fulfilled') setDrafts(draftRes.value)
      if (taskRes.status === 'rejected' && draftRes.status === 'rejected') {
        setLoadError(extractError(taskRes.reason, '加载工作台数据失败'))
      }
    } finally {
      fetchingRef.current = false
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  /* ── 坏消息优先分组（①被拒 ②失败 ③待处理草稿 ④排队/审核中 ⑤已上架） ── */
  const groups = useMemo(() => {
    const rejectedTasks = tasks.filter((t) => t.status === 'rejected')
    const failedTasks = tasks.filter((t) => t.status === 'failed')
    const pendingDrafts = drafts.filter((d) => !d.submission_status)
    const inflightTasks = tasks.filter((t) => t.status === 'pending' || t.status === 'running')
    const inflightDrafts = drafts.filter(
      (d) => d.submission_status === 'pending' || d.submission_status === 'uploading',
    )
    return {
      rejectedTasks,
      failedTasks,
      pendingDrafts,
      inflightTasks,
      inflightDrafts,
      completedCount: tasks.filter((t) => t.status === 'completed').length,
      publishedCount: drafts.filter((d) => d.submission_status === 'published').length,
    }
  }, [tasks, drafts])

  const hasAny = tasks.length > 0 || drafts.length > 0
  const badNewsCount = groups.rejectedTasks.length + groups.failedTasks.length
  const inflightCount = groups.inflightTasks.length + groups.inflightDrafts.length
  const publishedTotal = groups.completedCount + groups.publishedCount

  /* ── 动作：一键重上（复用 Tasks resubmit 逻辑，直连无确认） ── */
  const resubmitOne = async (taskId: string) => {
    setBusy(true)
    setNotice('')
    try {
      await resubmitTask(taskId)
      setNotice('已重新提交，新任务进入队列（列表已刷新）')
      await load(true)
    } catch (err) {
      setNotice(extractError(err, '重上失败'))
    } finally {
      setBusy(false)
    }
  }

  /* ── 动作：回采集箱改（M1.1 getTaskDraft → 跳编辑页；无草稿 → 禁用提示） ── */
  const openInCollectBox = useCallback(
    async (taskId: string) => {
      setNotice('')
      setDraftBusyIds((prev) => new Set(prev).add(taskId))
      try {
        const { draft_id } = await getTaskDraft(taskId)
        if (draft_id) {
          navigate(`/products/${draft_id}`)
        } else {
          setNoDraftIds((prev) => new Set(prev).add(taskId))
          setNotice('该任务无草稿来源（直连提交），不可回采集箱修改')
        }
      } catch (err) {
        setNotice(extractError(err, '查询草稿来源失败'))
      } finally {
        setDraftBusyIds((prev) => {
          const next = new Set(prev)
          next.delete(taskId)
          return next
        })
      }
    },
    [navigate],
  )

  const badgeText =
    badNewsCount > 0 || inflightCount > 0 || groups.pendingDrafts.length > 0
      ? `待处理 ${groups.pendingDrafts.length} · 需处理 ${badNewsCount} · 进行中 ${inflightCount}`
      : publishedTotal > 0
        ? `已上架 ${publishedTotal}`
        : ''

  return (
    <div className="page">
      <header className="page-header">
        <h1 className="page-title">工作台</h1>
        {badgeText && <span className="page-badge">{badgeText}</span>}
        <span className="toolbar-spacer" />
        <button className="btn" onClick={() => navigate('/products')} title="从采集箱草稿开始上架">
          新建
        </button>
        <button className="btn btn-ghost" onClick={() => load()} disabled={loading}>
          {loading ? '加载中…' : '刷新'}
        </button>
      </header>

      {notice && (
        <div className="alert alert-success" role="status">
          <span>{notice}</span>
          <button className="btn btn-small btn-ghost" onClick={() => setNotice('')}>
            知道了
          </button>
        </div>
      )}

      {loading ? (
        <div className="card">
          <div className="empty-state">
            <div
              className="spinner"
              style={{ borderColor: 'rgba(0, 91, 255, 0.2)', borderTopColor: 'var(--color-brand)' }}
            />
            <p className="empty-state-text">加载工作台…</p>
          </div>
        </div>
      ) : loadError && !hasAny ? (
        <div className="card">
          <div className="empty-state">
            <div className="form-error" role="alert">
              <span>{loadError}</span>
            </div>
            <button className="btn" onClick={() => load()}>
              重试
            </button>
          </div>
        </div>
      ) : !hasAny ? (
        <div className="card">
          <div className="empty-state">
            <div className="placeholder-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.6">
                <path d="M4 7h16M4 7l1.5 13h13L20 7M9 7V5a2 2 0 012-2h2a2 2 0 012 2v2" />
              </svg>
            </div>
            <p className="empty-state-title">开始采集</p>
            <p className="empty-state-text">
              使用 Skill 的 graph / follow --to-box 采集商品，或点右上角「新建」进入采集箱草稿
            </p>
            <button className="btn btn-primary" onClick={() => navigate('/collect-box')}>
              去采集箱
            </button>
          </div>
        </div>
      ) : (
        <div className="home-groups">
          {/* ① 审核被拒 */}
          {groups.rejectedTasks.length > 0 && (
            <div className="card home-group">
              <GroupHead title="审核被拒" count={groups.rejectedTasks.length} toneClass="tone-danger" hint="Ozon 拒绝上架，建议处理后重上">
                <button className="row-action" onClick={() => navigate('/tasks')}>
                  全部 →
                </button>
              </GroupHead>
              <div className="home-group-body">
                {groups.rejectedTasks.map((t) => {
                  const summary = t.product_summary?.[0]
                  const meta = taskStatusMeta(t.status)
                  return (
                    <div className="home-item" key={t.id}>
                      <ImageCell src={t.image} alt={t.title ?? t.id} />
                      <div className="home-item-info">
                        <span className="home-item-title" title={t.title}>
                          {t.title || '（无标题）'}
                        </span>
                        <span className="home-item-meta mono">
                          {t.item_id || t.id.slice(0, 8)}
                          {t.shop_name ? ` · ${t.shop_name}` : ''} · {fmtTime(t.created_at)}
                        </span>
                        {summary?.ozon_error ? (
                          <span className="task-error-hint" title={summary.ozon_error}>
                            {summary.ozon_error.slice(0, 44)}
                            {summary.ozon_error.length > 44 ? '…' : ''}
                          </span>
                        ) : null}
                      </div>
                      <span className={`status-badge ${meta.className}`}>{meta.label}</span>
                      <div className="home-item-actions">
                        <button
                          className="row-action"
                          disabled={busy || draftBusyIds.has(t.id) || noDraftIds.has(t.id)}
                          onClick={() => openInCollectBox(t.id)}
                          title={noDraftIds.has(t.id) ? '无草稿来源（直连提交，不可回采集箱修改）' : '回采集箱修改草稿'}
                        >
                          {draftBusyIds.has(t.id) ? '查询中…' : '回采集箱改'}
                        </button>
                        <button className="row-action danger" disabled={busy} onClick={() => resubmitOne(t.id)}>
                          重上
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* ② 上架失败 */}
          {groups.failedTasks.length > 0 && (
            <div className="card home-group">
              <GroupHead title="上架失败" count={groups.failedTasks.length} toneClass="tone-danger" hint="管线中断，可一键重上或回采集箱改">
                <button className="row-action" onClick={() => navigate('/tasks')}>
                  全部 →
                </button>
              </GroupHead>
              <div className="home-group-body">
                {groups.failedTasks.map((t) => {
                  const summary = t.product_summary?.[0]
                  const meta = taskStatusMeta(t.status)
                  return (
                    <div className="home-item" key={t.id}>
                      <ImageCell src={t.image} alt={t.title ?? t.id} />
                      <div className="home-item-info">
                        <span className="home-item-title" title={t.title}>
                          {t.title || '（无标题）'}
                        </span>
                        <span className="home-item-meta mono">
                          {t.item_id || t.id.slice(0, 8)}
                          {t.shop_name ? ` · ${t.shop_name}` : ''} · {fmtTime(t.created_at)}
                        </span>
                        {summary?.ozon_error ? (
                          <span className="task-error-hint" title={summary.ozon_error}>
                            {summary.ozon_error.slice(0, 44)}
                            {summary.ozon_error.length > 44 ? '…' : ''}
                          </span>
                        ) : null}
                      </div>
                      <span className={`status-badge ${meta.className}`}>{meta.label}</span>
                      <div className="home-item-actions">
                        <button
                          className="row-action"
                          disabled={busy || draftBusyIds.has(t.id) || noDraftIds.has(t.id)}
                          onClick={() => openInCollectBox(t.id)}
                          title={noDraftIds.has(t.id) ? '无草稿来源（直连提交，不可回采集箱修改）' : '回采集箱修改草稿'}
                        >
                          {draftBusyIds.has(t.id) ? '查询中…' : '回采集箱改'}
                        </button>
                        <button className="row-action danger" disabled={busy} onClick={() => resubmitOne(t.id)}>
                          重上
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* ③ 待处理草稿（决策注入：预估售价/利润） */}
          {groups.pendingDrafts.length > 0 && (
            <div className="card home-group">
              <GroupHead
                title="待处理草稿"
                count={groups.pendingDrafts.length}
                toneClass="tone-brand"
                hint="未上架，可直接去上架"
              >
                <button className="row-action" onClick={() => navigate('/collect-box')}>
                  采集箱 →
                </button>
              </GroupHead>
              <div className="home-group-body">
                {groups.pendingDrafts.map((d) => (
                  <div className="home-item" key={d.id}>
                    <ImageCell src={d.payload?.draft?.images?.[0]} alt={d.payload?.draft?.title ?? d.id} />
                    <div className="home-item-info">
                      <span className="home-item-title" title={d.payload?.draft?.title}>
                        {d.payload?.draft?.title ?? '（无标题）'}
                      </span>
                      <span className="home-item-meta">
                        <span className={`source-tag source-${d.source}`}>
                          {d.source === 'skill' ? 'Skill 采集' : 'WebUI'}
                        </span>
                        <span className="mono"> · {d.payload?.draft?.item_id || d.id.slice(0, 8)}</span>
                        <span> · {fmtTime(d.created_at)}</span>
                      </span>
                    </div>
                    <EstimateBadges draftId={d.id} />
                    <div className="home-item-actions">
                      <button className="btn btn-small btn-primary" onClick={() => navigate(`/products/${d.id}`)}>
                        去上架
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ④ 排队 / 审核中 */}
          {inflightCount > 0 && (
            <div className="card home-group">
              <GroupHead title="排队 / 审核中" count={inflightCount} toneClass="tone-warn" hint="管线自动推进，无需操作">
                <button className="row-action" onClick={() => navigate('/tasks')}>
                  进度 →
                </button>
              </GroupHead>
              <div className="home-group-body">
                {groups.inflightTasks.map((t) => {
                  const meta = taskStatusMeta(t.status)
                  return (
                    <div className="home-item" key={t.id}>
                      <ImageCell src={t.image} alt={t.title ?? t.id} />
                      <div className="home-item-info">
                        <span className="home-item-title" title={t.title}>
                          {t.title || '（无标题）'}
                        </span>
                        <span className="home-item-meta mono">
                          {t.item_id || t.id.slice(0, 8)}
                          {t.shop_name ? ` · ${t.shop_name}` : ''}
                        </span>
                      </div>
                      <span className={`status-badge ${meta.className}`}>{meta.label}</span>
                      <div className="home-item-actions">
                        <button className="row-action" onClick={() => navigate('/tasks')}>
                          查看进度
                        </button>
                      </div>
                    </div>
                  )
                })}
                {groups.inflightDrafts.map((d) => {
                  const meta = draftStatusMeta(d.submission_status)
                  return (
                    <div className="home-item" key={d.id}>
                      <ImageCell src={d.payload?.draft?.images?.[0]} alt={d.payload?.draft?.title ?? d.id} />
                      <div className="home-item-info">
                        <span className="home-item-title" title={d.payload?.draft?.title}>
                          {d.payload?.draft?.title ?? '（无标题）'}
                        </span>
                        <span className="home-item-meta mono">{d.payload?.draft?.item_id || d.id.slice(0, 8)}</span>
                      </div>
                      <span className={`status-badge ${meta.className}`}>{meta.label}</span>
                      <div className="home-item-actions">
                        <button className="row-action" onClick={() => navigate('/tasks')}>
                          查看进度
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* ⑤ 已上架（折叠摘要） */}
          {publishedTotal > 0 && (
            <div className="card home-group">
              <GroupHead title="已上架" count={publishedTotal} toneClass="tone-ok" hint="审核通过的商品">
                <button className="row-action" onClick={() => navigate('/on-sale')}>
                  在售货架 →
                </button>
              </GroupHead>
              <div className="home-group-body">
                <div className="home-published">
                  <span>
                    已完成 {groups.completedCount} 个任务 · {groups.publishedCount} 个草稿已上架，点击查看在售货架
                  </span>
                  <button className="btn btn-small" onClick={() => navigate('/on-sale')}>
                    查看
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
