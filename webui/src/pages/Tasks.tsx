import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  getTaskDraft,
  getTaskStatus,
  listTasks,
  resubmitTask,
  type ProductSummary,
  type TaskListItem,
  type TaskProgress,
  type TaskStatus,
  type TaskStatusDetail,
} from '../api/client'

/* ── T12 常量：13 阶段（与 worker main.py STAGE_ORDER 一致） ── */

const STAGE_ORDER = [
  'auth',
  'ingest',
  'category_match',
  'pricing',
  'attributes',
  'description',
  'image_generation',
  'prepare_ozon_upload',
  'ozon_validate',
  'check_quota',
  'ozon_upload',
  'ozon_status',
  'learning_record',
]

const STAGE_LABELS: Record<string, string> = {
  auth: '鉴权',
  ingest: '数据摄入',
  category_match: '类目匹配',
  pricing: '定价',
  attributes: '属性填充',
  description: '描述翻译',
  image_generation: 'AI 生图',
  prepare_ozon_upload: '上传准备',
  ozon_validate: 'Ozon 校验',
  check_quota: '配额检查',
  ozon_upload: '上传',
  ozon_status: '审核状态',
  learning_record: '学习记录',
}

const TOTAL_STAGES = STAGE_ORDER.length

/* ⚠️ 已知坑（AGENTS.md）：progress.percent 字段可能恒 0，
   进度百分比一律用 stages_completed.length / 13 计算 */
function progressPercent(p: TaskProgress | null | undefined): number {
  const done = p?.stages_completed?.length ?? 0
  return Math.min(100, Math.round((done / TOTAL_STAGES) * 100))
}

const RESUBMITTABLE: TaskStatus[] = ['failed', 'rejected']

const STATUS_META: Record<TaskStatus, { label: string; className: string }> = {
  pending: { label: '排队中', className: 'status-muted' },
  running: { label: '上架中', className: 'status-uploading' },
  completed: { label: '已完成', className: 'status-published' },
  failed: { label: '失败', className: 'status-failed' },
  rejected: { label: '审核被拒', className: 'status-failed' },
}

function statusMeta(status: TaskStatus) {
  return STATUS_META[status] ?? STATUS_META.pending
}

/* ── 工具函数 ── */

function fmtTime(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function isToday(iso?: string | null): boolean {
  if (!iso) return false
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return false
  const now = new Date()
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  )
}

function formatPrice(v: number | string | undefined): string {
  if (v === undefined || v === null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return String(v)
  return `¥${n.toFixed(2)}`
}

function extractError(err: unknown, fallback: string): string {
  const resp = (err as { response?: { data?: { detail?: string } } } | null)?.response
  return resp?.data?.detail || fallback
}

/* ── 图标 ── */

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  )
}

function WarningIcon() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M12 3.5L22 20H2L12 3.5z" />
      <path d="M12 9.5v5M12 17.2v.1" />
    </svg>
  )
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M4.5 12.5l5 5 10-11" />
    </svg>
  )
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

/* ── 详情弹窗（13 阶段进度条，2.5s 轮询 task_status） ── */

function DetailModal({
  task,
  onClose,
}: {
  task: TaskListItem
  onClose: () => void
}) {
  const [detail, setDetail] = useState<TaskStatusDetail | null>(null)
  const [polling, setPolling] = useState(true)

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const fetchStatus = async () => {
      try {
        const d = await getTaskStatus(task.id)
        if (!cancelled) {
          setDetail(d)
          const terminal = d.status === 'completed' || d.status === 'failed' || d.status === 'rejected'
          if (terminal) {
            setPolling(false)
          } else {
            timer = setTimeout(fetchStatus, 2500)
          }
        }
      } catch {
        if (!cancelled && !polling) setPolling(false)
      }
    }

    fetchStatus()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.id])

  const draftTitle = detail?.payload?.envelope?.draft?.title ?? task.title
  const draftImage = detail?.payload?.envelope?.draft?.images?.[0] ?? task.image
  const itemId = detail?.payload?.envelope?.draft?.item_id ?? task.item_id
  const status = detail?.status ?? task.status
  const meta = statusMeta(status)
  const progress = detail?.progress ?? task.progress
  const pct = progressPercent(progress)
  const errorMsg = detail?.error_message ?? ''

  const summary: ProductSummary | undefined = task.product_summary?.[0]

  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal task-detail-modal" role="dialog" aria-modal="true" aria-label="任务详情">
        <div className="modal-header">
          <h2 className="modal-title">任务详情</h2>
          <button type="button" className="modal-close" aria-label="关闭" onClick={onClose}>
            <CloseIcon />
          </button>
        </div>

        <div className="task-detail-head">
          <ImageCell src={draftImage} alt={draftTitle ?? task.id} />
          <div className="task-detail-info">
            <div className="task-detail-title" title={draftTitle}>
              {draftTitle || '（无标题）'}
            </div>
            <div className="task-detail-meta">
              <span className="mono">{itemId || task.id}</span>
              <span className="task-detail-time">{fmtTime(task.created_at)}</span>
            </div>
            <div className="task-detail-meta">
              <span className={`status-badge ${meta.className}`}>{meta.label}</span>
              {polling && <span className="task-polling-hint">进度轮询中…</span>}
            </div>
          </div>
        </div>

        <div className="task-progress">
          <div className="task-progress-head">
            <span className="task-progress-stage">
              {progress?.stage ? STAGE_LABELS[progress.stage] ?? progress.stage : meta.label}
              {progress?.message ? ` · ${progress.message}` : ''}
            </span>
            <span className="task-progress-pct">{pct}%</span>
          </div>
          <div className="task-progress-track" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            <div className="task-progress-fill" style={{ width: `${pct}%` }} />
          </div>
        </div>

        <ol className="task-stages">
          {STAGE_ORDER.map((stage, idx) => {
            const doneCount = progress?.stages_completed?.length ?? 0
            const done = idx < doneCount
            const current = !done && (status === 'running' || status === 'pending') && idx === doneCount
            const cls = done ? 'stage-done' : current ? 'stage-current' : 'stage-pending'
            return (
              <li key={stage} className={`task-stage ${cls}`}>
                <span className="task-stage-dot" aria-hidden="true">
                  {done ? <CheckIcon /> : null}
                </span>
                <span className="task-stage-name">{STAGE_LABELS[stage] ?? stage}</span>
                {current && <span className="task-stage-tag">进行中</span>}
              </li>
            )
          })}
        </ol>

        {summary && (
          <div className="task-detail-result">
            <span>售价 <strong>{formatPrice(summary.price)}</strong></span>
            <span>划线价 <strong>{summary.old_price ? formatPrice(summary.old_price) : '—'}</strong></span>
            <span>利润率 <strong>{(Number(summary.profit_rate) * 100).toFixed(0)}%</strong></span>
          </div>
        )}

        {errorMsg && (
          <div className="alert alert-error" role="alert">
            <WarningIcon />
            <span>{errorMsg}</span>
          </div>
        )}

        <div className="modal-foot">
          <button type="button" className="btn" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}

/* ── 任务进度页 ── */

type PlatformFilter = 'all' | 'graph' | 'follow'

interface Filters {
  platform: PlatformFilter
  account: string
  status: 'all' | TaskStatus
  keyword: string
  dateFrom: string
  dateTo: string
}

const EMPTY_FILTERS: Filters = { platform: 'all', account: 'all', status: 'all', keyword: '', dateFrom: '', dateTo: '' }

export default function Tasks() {
  const navigate = useNavigate()
  const [tasks, setTasks] = useState<TaskListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [detailTask, setDetailTask] = useState<TaskListItem | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [confirmResubmit, setConfirmResubmit] = useState(false)
  /** 已探明「无草稿来源」的任务（直连提交）→ 回采集箱改按钮禁用 */
  const [noDraftIds, setNoDraftIds] = useState<Set<string>>(new Set())
  /** 正在查询 draft_id 的任务（按钮显示查询中…） */
  const [draftBusyIds, setDraftBusyIds] = useState<Set<string>>(new Set())
  const fetchingRef = useRef(false)

  const load = useCallback(async (silent = false) => {
    if (fetchingRef.current) return
    fetchingRef.current = true
    if (!silent) setLoading(true)
    try {
      const data = await listTasks({ limit: 100 })
      setTasks(data.items)
      setLoadError('')
    } catch (err) {
      if (!silent) setLoadError(extractError(err, '加载任务列表失败'))
    } finally {
      fetchingRef.current = false
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  /* 列表 5s 轮询（静默刷新，不打断筛选/选中态） */
  useEffect(() => {
    const timer = setInterval(() => load(true), 5000)
    return () => clearInterval(timer)
  }, [load])

  /* ── 筛选（前端本地过滤，可简化） ── */
  const accounts = useMemo(() => {
    const set = new Set<string>()
    tasks.forEach((t) => {
      const acc = t.ozon_client_id || ''
      if (acc) set.add(acc)
    })
    return [...set].sort()
  }, [tasks])

  const filtered = useMemo(() => {
    return tasks.filter((t) => {
      if (filters.platform !== 'all') {
        const isFollow = !!t.follow_sell
        if (filters.platform === 'follow' ? !isFollow : isFollow) return false
      }
      if (filters.account !== 'all' && (t.ozon_client_id || '') !== filters.account) return false
      if (filters.status !== 'all' && t.status !== filters.status) return false
      if (filters.keyword.trim()) {
        const kw = filters.keyword.trim().toLowerCase()
        const hay = `${t.item_id ?? ''} ${t.title ?? ''}`.toLowerCase()
        if (!hay.includes(kw)) return false
      }
      if (filters.dateFrom || filters.dateTo) {
        const day = (t.created_at ?? '').slice(0, 10)
        if (filters.dateFrom && day < filters.dateFrom) return false
        if (filters.dateTo && day > filters.dateTo) return false
      }
      return true
    })
  }, [tasks, filters])

  const todayCount = useMemo(() => tasks.filter((t) => isToday(t.created_at)).length, [tasks])

  const selectedResubmittable = useMemo(() => {
    const ok = new Set<string>()
    tasks.forEach((t) => {
      if (selected.has(t.id) && RESUBMITTABLE.includes(t.status)) ok.add(t.id)
    })
    return ok
  }, [tasks, selected])

  const allChecked = filtered.length > 0 && selected.size === filtered.length
  const someChecked = selected.size > 0 && !allChecked

  const toggleAll = () => {
    setSelected(allChecked ? new Set() : new Set(filtered.map((t) => t.id)))
  }

  const toggleOne = (id: string) => {
    const next = new Set(selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSelected(next)
  }

  /* ── 异常重上：勾选 failed/rejected → POST resubmit_task ── */
  const handleResubmit = useCallback(
    async (ids: string[]) => {
      setBusy(true)
      setNotice('')
      let ok = 0
      const errors: string[] = []
      for (const id of ids) {
        try {
          await resubmitTask(id)
          ok += 1
        } catch (err) {
          errors.push(extractError(err, '重上失败'))
        }
      }
      if (ok > 0) {
        setNotice(`已重新提交 ${ok} 个任务，新任务进入队列（列表已刷新）`)
        setSelected(new Set())
        await load(true)
      }
      if (errors.length > 0) {
        setNotice((prev) => (prev ? `${prev}；${errors.length} 个失败：${errors[0]}` : `${errors.length} 个重上失败：${errors[0]}`))
      }
      setBusy(false)
      setConfirmResubmit(false)
    },
    [load],
  )

  const resubmitOne = async (id: string) => {
    setBusy(true)
    setNotice('')
    try {
      await resubmitTask(id)
      setNotice('已重新提交，新任务进入队列（列表已刷新）')
      setSelected(new Set())
      await load(true)
    } catch (err) {
      setNotice(extractError(err, '重上失败'))
    } finally {
      setBusy(false)
    }
  }

  /* ── 回采集箱改：查询任务草稿来源 → 跳编辑页；无草稿（直连）→ 禁用提示 ── */
  const openInCollectBox = useCallback(async (taskId: string) => {
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
  }, [navigate])

  return (
    <div className="page">
      <header className="page-header">
        <h1 className="page-title">任务进度</h1>
        <span className="page-badge">T12</span>
      </header>

      {/* 筛选栏 */}
      <div className="card task-filters-card">
        <div className="task-filters">
          <div className="task-filter">
            <label htmlFor="tf-platform">上架平台</label>
            <select
              id="tf-platform"
              className="field-select"
              value={filters.platform}
              onChange={(e) => setFilters((f) => ({ ...f, platform: e.target.value as PlatformFilter }))}
            >
              <option value="all">全部</option>
              <option value="graph">Ozon 选品</option>
              <option value="follow">Ozon 跟卖</option>
            </select>
          </div>
          <div className="task-filter">
            <label htmlFor="tf-account">账号</label>
            <select
              id="tf-account"
              className="field-select"
              value={filters.account}
              onChange={(e) => setFilters((f) => ({ ...f, account: e.target.value }))}
            >
              <option value="all">全部</option>
              {accounts.map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
          </div>
          <div className="task-filter">
            <label htmlFor="tf-status">上架状态</label>
            <select
              id="tf-status"
              className="field-select"
              value={filters.status}
              onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value as Filters['status'] }))}
            >
              <option value="all">全部</option>
              <option value="pending">排队中</option>
              <option value="running">上架中</option>
              <option value="completed">已完成</option>
              <option value="failed">失败</option>
              <option value="rejected">审核被拒</option>
            </select>
          </div>
          <div className="task-filter task-filter-grow">
            <label htmlFor="tf-keyword">货号/标题</label>
            <input
              id="tf-keyword"
              className="field-input"
              type="text"
              value={filters.keyword}
              placeholder="搜索货号或标题"
              onChange={(e) => setFilters((f) => ({ ...f, keyword: e.target.value }))}
            />
          </div>
          <div className="task-filter">
            <label htmlFor="tf-from">开始时间</label>
            <input
              id="tf-from"
              className="field-input"
              type="date"
              value={filters.dateFrom}
              onChange={(e) => setFilters((f) => ({ ...f, dateFrom: e.target.value }))}
            />
          </div>
          <div className="task-filter">
            <label htmlFor="tf-to">结束时间</label>
            <input
              id="tf-to"
              className="field-input"
              type="date"
              value={filters.dateTo}
              onChange={(e) => setFilters((f) => ({ ...f, dateTo: e.target.value }))}
            />
          </div>
          <div className="task-filter">
            <label>&nbsp;</label>
            <button
              className="btn"
              onClick={() => setFilters(EMPTY_FILTERS)}
              disabled={JSON.stringify(filters) === JSON.stringify(EMPTY_FILTERS)}
            >
              重置
            </button>
          </div>
        </div>
      </div>

      {/* 工具栏 */}
      <div className="toolbar">
        <span className="toolbar-count">
          共 {filtered.length} 条{filters !== EMPTY_FILTERS && filtered.length !== tasks.length ? `（全部 ${tasks.length}）` : ''}
          {selected.size > 0 ? ` · 已选 ${selected.size}` : ''}
          {selectedResubmittable.size > 0 ? `（可重上 ${selectedResubmittable.size}）` : ''}
        </span>
        <button
          className="btn btn-primary"
          disabled={selectedResubmittable.size === 0 || busy}
          onClick={() => setConfirmResubmit(true)}
        >
          异常重上
          {selectedResubmittable.size > 0 ? ` (${selectedResubmittable.size})` : ''}
        </button>
        <button className="btn" onClick={() => load()} disabled={loading || busy}>
          刷新
        </button>
        <span className="toolbar-spacer" />
        <span className="toolbar-count today-count" title="今日创建的上架任务数（按当前列表统计）">
          今日上架 {todayCount}
        </span>
      </div>

      {notice && (
        <div className="alert alert-success" role="status">
          <span>{notice}</span>
          <button className="btn btn-small btn-ghost" onClick={() => setNotice('')}>知道了</button>
        </div>
      )}

      <div className="card">
        {loading ? (
          <div className="empty-state">
            <div className="spinner" style={{ borderColor: 'rgba(0, 91, 255, 0.2)', borderTopColor: 'var(--color-brand)' }} />
            <p className="empty-state-text">加载任务列表…</p>
          </div>
        ) : loadError ? (
          <div className="empty-state">
            <div className="form-error" role="alert">
              <WarningIcon />
              <span>{loadError}</span>
            </div>
            <button className="btn" onClick={() => load()}>重试</button>
          </div>
        ) : filtered.length === 0 ? (
          <div className="empty-state">
            <div className="placeholder-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.6">
                <rect x="3.5" y="4" width="17" height="16" rx="2.5" />
                <path d="M8 9h8M8 13h5M8 17h3" />
              </svg>
            </div>
            <p className="empty-state-title">暂无上架记录</p>
            <p className="empty-state-text">
              {tasks.length === 0
                ? '使用 Skill 的 graph / follow 提交上架任务后，进度会显示在这里'
                : '当前筛选条件下没有匹配的任务'}
            </p>
          </div>
        ) : (
          <table className="draft-table tasks-table">
            <thead>
              <tr>
                <th className="col-check">
                  <input
                    type="checkbox"
                    checked={allChecked}
                    ref={(el) => {
                      if (el) el.indeterminate = someChecked
                    }}
                    onChange={toggleAll}
                    aria-label="全选"
                  />
                </th>
                <th>商品信息</th>
                <th>平台</th>
                <th>店铺 / 账号</th>
                <th>上架状态</th>
                <th className="col-price">售价</th>
                <th className="col-price">划线价</th>
                <th>货源信息</th>
                <th>上架方式</th>
                <th className="col-time">创建时间</th>
                <th className="col-actions">操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((t) => {
                const meta = statusMeta(t.status)
                const resubmittable = RESUBMITTABLE.includes(t.status)
                const summary = t.product_summary?.[0]
                return (
                  <tr key={t.id} className={selected.has(t.id) ? 'row-selected' : undefined}>
                    <td className="col-check">
                      <input
                        type="checkbox"
                        checked={selected.has(t.id)}
                        onChange={() => toggleOne(t.id)}
                        aria-label={`选择 ${t.title ?? t.id}`}
                      />
                    </td>
                    <td>
                      <div className="task-product">
                        <ImageCell src={t.image} alt={t.title ?? t.id} />
                        <div className="task-product-info">
                          <span className="draft-title" title={t.title}>{t.title || '（无标题）'}</span>
                          <span className="task-product-meta mono">
                            {t.item_id || t.id.slice(0, 8)}
                            {summary?.ozon_status ? ` · ${summary.ozon_status}` : ''}
                          </span>
                        </div>
                      </div>
                    </td>
                    <td>
                      <span className="badge badge-platform">Ozon</span>
                    </td>
                    <td>
                      <div className="task-account">
                        <span className="task-shop">{t.shop_name || '—'}</span>
                        <span className="task-client mono">{t.ozon_client_id || '—'}</span>
                      </div>
                    </td>
                    <td>
                      <span className={`status-badge ${meta.className}`}>{meta.label}</span>
                      {resubmittable && summary?.ozon_error ? (
                        <div className="task-error-hint" title={summary.ozon_error}>
                          {summary.ozon_error.slice(0, 40)}
                          {summary.ozon_error.length > 40 ? '…' : ''}
                        </div>
                      ) : null}
                    </td>
                    <td className="col-price">
                      <span className="task-price">{formatPrice(summary?.price)}</span>
                    </td>
                    <td className="col-price">
                      <span className="task-old-price">
                        {summary?.old_price ? formatPrice(summary.old_price) : '—'}
                      </span>
                    </td>
                    <td>
                      {summary?.purchase_url ? (
                        <a
                          className="task-source-link"
                          href={summary.purchase_url}
                          target="_blank"
                          rel="noreferrer"
                          title={summary.purchase_url}
                        >
                          货源链接
                        </a>
                      ) : (
                        <span className="task-source-empty">—</span>
                      )}
                    </td>
                    <td>
                      <span className={`badge ${t.follow_sell ? 'badge-follow' : 'badge-currency'}`}>
                        {t.follow_sell ? '跟卖' : '选品'}
                      </span>
                    </td>
                    <td className="col-time">{fmtTime(t.created_at)}</td>
                    <td className="col-actions">
                      <div className="row-actions">
                        <button className="row-action" disabled={busy} onClick={() => setDetailTask(t)}>
                          详情
                        </button>
                        <button
                          className="row-action"
                          disabled={busy}
                          onClick={() => navigate(`/image-studio?taskId=${t.id}`)}
                          title="生图工作台：查看/重新生成 AI 套图"
                        >
                          生图
                        </button>
                        {resubmittable && (
                          <button
                            className="row-action"
                            disabled={busy || draftBusyIds.has(t.id) || noDraftIds.has(t.id)}
                            onClick={() => openInCollectBox(t.id)}
                            title={noDraftIds.has(t.id) ? '无草稿来源（直连提交，不可回采集箱修改）' : '回采集箱修改草稿'}
                          >
                            {draftBusyIds.has(t.id) ? '查询中…' : '回采集箱改'}
                          </button>
                        )}
                        {resubmittable && (
                          <button className="row-action danger" disabled={busy} onClick={() => resubmitOne(t.id)}>
                            异常重上
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {confirmResubmit && (
        <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && setConfirmResubmit(false)}>
          <div className="modal" role="dialog" aria-modal="true" aria-label="确认异常重上">
            <h3 className="modal-title">异常重上</h3>
            <p className="modal-text">
              将重新提交 {selectedResubmittable.size} 个失败/被拒任务（复制原载荷 + 图片重新生成标记入队），
              确认继续？
            </p>
            <div className="modal-actions">
              <button className="btn" disabled={busy} onClick={() => setConfirmResubmit(false)}>取消</button>
              <button className="btn btn-primary" disabled={busy} onClick={() => handleResubmit([...selectedResubmittable])}>
                {busy ? '提交中…' : '确认重上'}
              </button>
            </div>
          </div>
        </div>
      )}

      {detailTask && <DetailModal task={detailTask} onClose={() => setDetailTask(null)} />}
    </div>
  )
}
