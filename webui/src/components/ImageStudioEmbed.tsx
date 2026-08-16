import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  aiField,
  getDraft,
  getStoredToken,
  getTaskImages,
  getTaskStatus,
  regenTaskImage,
  verifyToken,
  type Draft,
  type TaskImageItem,
  type TaskStatusDetail,
} from '../api/client'

/* ════════════════════════════════════════════════════════════
 * ImageStudioEmbed — 可复用生图交互组件（T11 自 ImageStudio 页抽取）
 *
 * 核心生图交互（原图选择/卖点输入/图配置/一键生成/轮询预览/
 * 新旧对比）全部封装在组件内，由 props 驱动，不绑定路由/页面级
 * 状态（不使用 useSearchParams）。宿主负责路由与页面级布局
 * （标题/返回按钮/外层 page 容器），T12 将把本组件嵌进商品编辑页。
 *
 * 数据流（与 ImageStudio 页原逻辑一致）：
 *   task/online：getTaskStatus(taskId) → 原图 + 卖点快照 → getTaskImages 轮询预览
 *   draft：getDraft(draftId) → 原图；配置仅保存，提交上架后按计划生成
 *   initialOriginals/initialSelling：宿主注入的初始值（覆盖快照/草稿数据，挂载时生效）
 *   onGenerated：runGenerate/regenOne 完成后回传最新图片 URL 列表（编辑页回填用）
 *
 * ⚠️ 卖点/计划存浏览器 extensions 快照（后端无收口端点，v1
 *    本地持久化；T14 接入后改传后端）。
 * ════════════════════════════════════════════════════════════ */

const MAX_ORIGINALS = 3
const MIN_PLAN_TOTAL = 2
const SCENE_MAX = 3
const SNAP_KEY = (id: string) => `image_studio_ext_${id}`

interface SlotType {
  key: string
  label: string
  desc: string
  /** 置灰类型（材质/尺寸 v1 无节点，T7b C3b） */
  disabled?: boolean
  /** 场景图允许 0-3，其余 0/1 */
  max?: number
}

const SLOT_TYPES: SlotType[] = [
  { key: 'white_bg', label: '白底图', desc: '纯白背景产品图（Phase1 参考图）' },
  { key: 'scene', label: '场景图', desc: '使用场景图，最多 3 张', max: SCENE_MAX },
  { key: 'main_image', label: '卖点图', desc: '营销主图（兼卖点展示）' },
  { key: 'detail', label: '细节图', desc: '产品细节特写' },
  { key: 'comparison', label: '对比图', desc: '同类型产品对比' },
  { key: 'social_proof', label: '社交证明', desc: '社交场景/用户晒图风格' },
  { key: 'multi_angle', label: '多角度', desc: '多角度展示图（Phase1 参考图）' },
  { key: 'material', label: '材质图', desc: '材质特写（v2 开放）', disabled: true },
  { key: 'size', label: '尺寸图', desc: '尺寸标注图（v2 开放）', disabled: true },
]

const DEFAULT_COUNTS: Record<string, number> = {
  white_bg: 1,
  scene: 3,
  main_image: 1,
  detail: 1,
  comparison: 1,
  social_proof: 1,
  multi_angle: 1,
  material: 0,
  size: 0,
}

const RICH_TYPES = [
  '主图文案', '卖点轮播', '参数对比表', '场景长图', '产品故事', '使用教程',
  '规格说明', '材质说明', '保养建议', '售后保障', '品牌介绍', '包装清单',
  '物流说明', '尺码指南', '搭配建议', 'FAQ 问答',
]

/** C3b：plan slot → 该槽位对应的 regen slot（scene 展开为 scene_1..N） */
function planToRegenSlots(plan: Record<string, number>): string[] {
  const out: string[] = []
  for (const [type, count] of Object.entries(plan)) {
    if (type === 'scene') {
      for (let i = 1; i <= Math.min(count, SCENE_MAX); i++) out.push(`scene_${i}`)
    } else if (count >= 1 && type !== 'material' && type !== 'size') {
      out.push(type)
    }
  }
  return out
}

/** 对齐 T7b validate_plan：plan 必须含 Phase1（white_bg 或 multi_angle） */
function planError(counts: Record<string, number>): string {
  const total = Object.entries(counts)
    .filter(([k]) => k !== 'material' && k !== 'size')
    .reduce((acc, [, v]) => acc + v, 0)
  if (total < MIN_PLAN_TOTAL) return `至少选择 ${MIN_PLAN_TOTAL} 张图片`
  if (!(counts.white_bg >= 1 || counts.multi_angle >= 1)) {
    return '需至少包含白底图或多角度图（场景/卖点等图依赖其作参考图）'
  }
  return ''
}

const SLOT_LABELS: Record<string, string> = {
  white_bg: '白底图',
  scene_1: '场景图 1',
  scene_2: '场景图 2',
  scene_3: '场景图 3',
  main_image: '卖点图',
  detail: '细节图',
  comparison: '对比图',
  social_proof: '社交证明',
  multi_angle: '多角度',
}

function slotLabel(slot: string): string {
  return SLOT_LABELS[slot] ?? slot
}

function extractError(err: unknown, fallback: string): string {
  const resp = (err as { response?: { data?: { detail?: string } } } | null)?.response
  return resp?.data?.detail || fallback
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

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  )
}

function SparkIcon() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z" />
      <path d="M19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16z" />
    </svg>
  )
}

function BrokenImageIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="3.5" y="3.5" width="17" height="17" rx="2.5" />
      <circle cx="9" cy="9" r="1.8" />
      <path d="M4.5 18.5l5-5 3.5 3.5 3-3 3.5 3.5" />
    </svg>
  )
}

/* ── 图裂占位（验收项：图片加载失败 → 占位） ── */

function StudioImage({
  src,
  alt,
  className = '',
}: {
  src?: string
  alt: string
  className?: string
}) {
  const [broken, setBroken] = useState(false)
  useEffect(() => setBroken(false), [src])
  if (!src || broken) {
    return (
      <div className={`studio-img studio-img-broken ${className}`} role="img" aria-label={`图片加载失败：${alt}`}>
        <BrokenImageIcon />
      </div>
    )
  }
  return <img className={`studio-img ${className}`} src={src} alt={alt} loading="lazy" onError={() => setBroken(true)} />
}

/* ── 卖点快照（extensions 本地快照：保存/恢复） ── */

interface SellingSnapshot {
  productName: string
  sellingPoints: string
  audience: string
  scenes: string
  params: string
}

const EMPTY_SELLING: SellingSnapshot = {
  productName: '',
  sellingPoints: '',
  audience: '',
  scenes: '',
  params: '',
}

function snapshotFrom(input: SellingSnapshot): string {
  return [input.productName, input.sellingPoints, input.audience, input.scenes, input.params]
    .map((s) => s.trim())
    .filter(Boolean)
    .join('\n')
}

/* ── 单槽位预览卡片（version 标识 + 重新生成 + 旧 vs 新对比） ── */

function SlotCard({
  slot,
  versions,
  busy,
  onRegen,
  onCompare,
}: {
  slot: string
  versions: TaskImageItem[]
  busy: boolean
  onRegen: (slot: string) => void
  onCompare: (slot: string) => void
}) {
  const latest = versions[0]
  return (
    <div className="slot-card">
      <StudioImage src={latest?.url} alt={`${slotLabel(slot)} v${latest?.version ?? '-'}`} className="slot-card-img" />
      <div className="slot-card-head">
        <span className="slot-card-name">{slotLabel(slot)}</span>
        <span className={`badge badge-version`}>v{latest?.version ?? 0}</span>
      </div>
      <div className="slot-card-foot">
        <button className="row-action" disabled={busy} onClick={() => onCompare(slot)}>
          对比
        </button>
        <button className="row-action" disabled={busy} onClick={() => onRegen(slot)}>
          {busy ? '生成中…' : '重新生成'}
        </button>
      </div>
    </div>
  )
}

/* ════════════════════════════════════════════════════════════
 * 组件 props
 * ════════════════════════════════════════════════════════════ */

export interface ImageStudioEmbedProps {
  /** 上下文来源：task（任务生图）/ draft（草稿生图，无任务不实时生成）/ online（在线商品生图，实时重生成） */
  mode: 'task' | 'draft' | 'online'
  /** draft 模式：初始化原图/卖点 */
  draftId?: string
  /** task/online 模式：实时生成与重生成 */
  taskId?: string
  /** 初始原图（外部注入，如编辑页草稿 images；挂载时生效，覆盖草稿/任务数据） */
  initialOriginals?: string[]
  /** 初始卖点文本（外部注入，如编辑页标题/属性提示；挂载时生效，覆盖快照） */
  initialSelling?: string
  /** 生成完成回调（编辑页回填图片列表；仅 task/online 模式有生成时触发） */
  onGenerated?: (images: string[]) => void
  /** 关闭回调（由宿主渲染关闭入口；独立页无关闭按钮，可不传） */
  onClose?: () => void
}

/* ════════════════════════════════════════════════════════════
 * 组件主体（行为与 ImageStudio 页原逻辑一致，仅挂载点/数据源
 * 由 props 驱动；identity = taskId ? task : draftId ? draft）
 * ════════════════════════════════════════════════════════════ */

export default function ImageStudioEmbed({
  draftId,
  taskId,
  initialOriginals,
  initialSelling,
  onGenerated,
}: ImageStudioEmbedProps) {
  const [task, setTask] = useState<TaskStatusDetail | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')

  const [images, setImages] = useState<TaskImageItem[]>([])
  const [imagesLoaded, setImagesLoaded] = useState(false)

  const [originals, setOriginals] = useState<string[]>([])
  const [selling, setSelling] = useState<SellingSnapshot>(EMPTY_SELLING)
  const [counts, setCounts] = useState<Record<string, number>>(DEFAULT_COUNTS)

  const [aiBusy, setAiBusy] = useState(false)
  const [regenBusySlot, setRegenBusySlot] = useState('')
  const [notice, setNotice] = useState('')
  const [noticeKind, setNoticeKind] = useState<'success' | 'error' | 'info'>('info')
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [balanceState, setBalanceState] = useState<{ valid: boolean; reason: string; checking: boolean }>({
    valid: false,
    reason: '',
    checking: false,
  })
  const [generating, setGenerating] = useState(false)
  const [compareSlot, setCompareSlot] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  const identity = useMemo(() => {
    if (taskId) return { kind: 'task' as const, id: taskId }
    if (draftId) return { kind: 'draft' as const, id: draftId }
    return null
  }, [taskId, draftId])

  /* 宿主注入的初始值：挂载时生效（ref 捕获首渲染值，避免宿主内联
   * 传数组/字符串导致 load 依赖漂移而重复请求；T12 编辑页动态回填
   * 走 onGenerated，不走 initialOriginals 反复注入） */
  const initialOriginalsRef = useRef(initialOriginals)
  const initialSellingRef = useRef(initialSelling)

  /* ── 加载任务/草稿 ── */
  const load = useCallback(async () => {
    if (!identity) return
    setLoading(true)
    setLoadError('')
    try {
      const initOriginals = initialOriginalsRef.current
      const initSelling = initialSellingRef.current
      if (identity.kind === 'task') {
        const t = await getTaskStatus(identity.id)
        setTask(t)
        const d = t.payload?.envelope?.draft
        if (d?.images?.length) setOriginals(d.images.slice(0, MAX_ORIGINALS))
        const snap = localStorage.getItem(SNAP_KEY(identity.id))
        if (snap) {
          try {
            const parsed = JSON.parse(snap)
            if (parsed.counts) setCounts(parsed.counts)
            if (parsed.selling && !initSelling) setSelling({ ...EMPTY_SELLING, ...parsed.selling })
          } catch {
            /* 快照损坏忽略，用默认值 */
          }
        }
        if (initSelling) setSelling((s) => ({ ...s, sellingPoints: initSelling }))
      } else {
        const d = await getDraft(identity.id)
        setDraft(d)
        const imgs = d.payload?.draft?.images ?? []
        if (imgs.length) setOriginals(imgs.slice(0, MAX_ORIGINALS))
        if (initSelling) setSelling((s) => ({ ...s, sellingPoints: initSelling }))
      }
      if (initOriginals?.length) setOriginals(initOriginals.slice(0, MAX_ORIGINALS))
    } catch (err) {
      setLoadError(extractError(err, '加载失败'))
    } finally {
      setLoading(false)
    }
  }, [identity])

  useEffect(() => {
    load()
  }, [load])

  /* ── 已生成图片列表（仅 task 模式；返回扁平列表供调用方取最新结果） ── */
  const loadImages = useCallback(async (): Promise<TaskImageItem[]> => {
    if (!taskId) return []
    try {
      const resp = await getTaskImages(taskId)
      const grouped = new Map<string, TaskImageItem[]>()
      for (const img of resp.images) {
        const arr = grouped.get(img.slot) ?? []
        arr.push(img)
        grouped.set(img.slot, arr)
      }
      for (const arr of grouped.values()) arr.sort((a, b) => b.version - a.version)
      const flat = [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b)).flatMap(([, v]) => v)
      setImages(flat)
      setImagesLoaded(true)
      return flat
    } catch {
      setImagesLoaded(true)
      return []
    }
  }, [taskId])

  useEffect(() => {
    if (taskId) loadImages()
  }, [taskId, loadImages])

  const groupedBySlot = useMemo(() => {
    const map = new Map<string, TaskImageItem[]>()
    for (const img of images) {
      const arr = map.get(img.slot) ?? []
      arr.push(img)
      map.set(img.slot, arr)
    }
    return map
  }, [images])

  /* ── 卖点快照持久化（extensions 快照，v1 存本地） ── */
  const persistSnapshot = useCallback(() => {
    const id = taskId || draftId
    if (!id) return
    localStorage.setItem(SNAP_KEY(id), JSON.stringify({ counts, selling }))
  }, [taskId, draftId, counts, selling])

  useEffect(() => {
    persistSnapshot()
  }, [persistSnapshot])

  /* ── 商品图配置：counts 变化 ── */
  const setCount = useCallback((key: string, delta: number) => {
    setCounts((prev) => {
      const max = SLOT_TYPES.find((t) => t.key === key)?.max ?? 1
      const next = Math.min(max, Math.max(0, (prev[key] ?? 0) + delta))
      return { ...prev, [key]: next }
    })
  }, [])

  const plan = useMemo(() => {
    const out: Record<string, number> = {}
    for (const t of SLOT_TYPES) {
      if (t.disabled) continue
      const c = counts[t.key] ?? 0
      if (c > 0) out[t.key] = c
    }
    return out
  }, [counts])

  const planTotal = useMemo(
    () => Object.values(plan).reduce((a, b) => a + b, 0),
    [plan],
  )
  const planIssue = useMemo(() => planError(counts), [counts])

  const draftPayload = task?.payload?.envelope?.draft ?? draft?.payload?.draft
  const productTitle = draftPayload?.title ?? ''

  /* ── ① 原图：从已采集图选择 + 上传（≤3 张） ── */
  const toggleOriginal = (url: string) => {
    setOriginals((prev) => {
      if (prev.includes(url)) return prev.filter((u) => u !== url)
      if (prev.length >= MAX_ORIGINALS) return prev
      return [...prev, url]
    })
  }

  const onUploadFile = (file: File) => {
    if (!file.type.startsWith('image/')) {
      setNotice('仅支持图片文件')
      setNoticeKind('error')
      return
    }
    setOriginals((prev) => {
      if (prev.length >= MAX_ORIGINALS) return prev
      return [...prev, URL.createObjectURL(file)]
    })
  }

  /* ── ② AI 帮写（1 次调用；需 draftId → drafts ai 端点） ── */
  const aiWrite = async () => {
    if (!draftId) {
      setNotice('当前入口无草稿，无法 AI 帮写（请从商品编辑页「AI商品套图」进入）')
      setNoticeKind('info')
      return
    }
    setAiBusy(true)
    setNotice('')
    try {
      const resp = await aiField(draftId, 'description')
      setSelling((prev) => ({
        ...prev,
        productName: prev.productName || productTitle,
        sellingPoints: prev.sellingPoints || resp.value,
      }))
      setNotice('AI 已生成卖点内容，可在此基础上修改')
      setNoticeKind('success')
    } catch (err) {
      setNotice(extractError(err, 'AI 帮写失败'))
      setNoticeKind('error')
    } finally {
      setAiBusy(false)
    }
  }

  /* ── ⑤ 一键生成：余额门 → 确认弹窗 → 逐槽位 regen ── */
  const openGenerateConfirm = async () => {
    setNotice('')
    if (planIssue) {
      setNotice(planIssue)
      setNoticeKind('error')
      return
    }
    setBalanceState((b) => ({ ...b, checking: true }))
    try {
      const resp = await verifyToken({ token: getStoredToken() ?? '' })
      setBalanceState({ valid: resp.valid, reason: resp.reason, checking: false })
      if (!resp.valid && resp.reason === 'balance_insufficient') {
        setNotice('MXOU 余额不足，无法生成图片（请充值）')
        setNoticeKind('error')
        return
      }
      if (!resp.valid) {
        setNotice(resp.reason === 'token_invalid' ? 'Token 无效，请重新登录' : '账号状态异常，无法生成')
        setNoticeKind('error')
        return
      }
      setConfirmOpen(true)
    } catch {
      setBalanceState((b) => ({ ...b, checking: false }))
      setNotice('余额检查失败，请稍后重试')
      setNoticeKind('error')
    }
  }

  const runGenerate = async () => {
    if (!taskId) return
    setGenerating(true)
    setNotice('')
    const slots = planToRegenSlots(plan)
    let okCount = 0
    const skipped: string[] = []
    const failed: string[] = []
    for (const slot of slots) {
      try {
        await regenTaskImage(taskId, slot)
        okCount += 1
      } catch (err) {
        const msg = extractError(err, '')
        if (msg.includes('无缓存')) skipped.push(slotLabel(slot))
        else failed.push(`${slotLabel(slot)}（${msg || '失败'}）`)
      }
    }
    const flat = await loadImages()
    setGenerating(false)
    setConfirmOpen(false)
    onGenerated?.(flat.map((i) => i.url))
    const parts = [`已重新生成 ${okCount} 张`]
    if (skipped.length) parts.push(`跳过（无已生成图片）：${skipped.join('、')}`)
    if (failed.length) parts.push(`失败：${failed.join('、')}`)
    setNotice(parts.join('；'))
    setNoticeKind(okCount > 0 && failed.length === 0 ? 'success' : 'error')
  }

  /* ── ⑥ 单张重新生成 ── */
  const regenOne = async (slot: string) => {
    if (!taskId) return
    setRegenBusySlot(slot)
    setNotice('')
    try {
      await regenTaskImage(taskId, slot)
      const flat = await loadImages()
      onGenerated?.(flat.map((i) => i.url))
      setNotice(`${slotLabel(slot)} 已重新生成（新版本 v${(groupedBySlot.get(slot)?.[0]?.version ?? 0) + 1}）`)
      setNoticeKind('success')
    } catch (err) {
      setNotice(extractError(err, '重新生成失败'))
      setNoticeKind('error')
    } finally {
      setRegenBusySlot('')
    }
  }

  const compareVersions = groupedBySlot.get(compareSlot) ?? []
  const compareNew = compareVersions[0]
  const compareOld = compareVersions[compareVersions.length - 1]

  /* 无 taskId/draftId（宿主未提供上下文）→ 由宿主管辖的页面处理空态，组件不渲染 */
  if (!identity) return null

  return (
    <>
      {notice && (
        <div className={`alert alert-${noticeKind}`} role="status">
          <span>{notice}</span>
          <div className="alert-actions">
            <button className="btn btn-small btn-ghost" onClick={() => setNotice('')}>知道了</button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="card">
          <div className="empty-state">
            <div className="spinner" style={{ borderColor: 'rgba(0, 91, 255, 0.2)', borderTopColor: 'var(--color-brand)' }} />
            <p className="empty-state-text">加载商品数据…</p>
          </div>
        </div>
      ) : loadError ? (
        <div className="card">
          <div className="empty-state">
            <div className="form-error" role="alert">
              <WarningIcon />
              <span>{loadError}</span>
            </div>
            <button className="btn" onClick={load}>重试</button>
          </div>
        </div>
      ) : (
        <>
          {/* ── ① 商品原图（≤3） ── */}
          <section className="card section-card">
            <div className="section-head">
              <h2 className="section-title">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7">
                  <rect x="3.5" y="3.5" width="17" height="17" rx="2.5" />
                  <circle cx="9" cy="9" r="1.8" />
                  <path d="M4.5 18.5l5-5 3.5 3.5 3-3 3.5 3.5" />
                </svg>
                商品原图
              </h2>
              <span className="section-sub">最多 {MAX_ORIGINALS} 张 · 已选 {originals.length}/{MAX_ORIGINALS}</span>
            </div>
            <div className="section-body">
              <div className="originals-grid">
                {originals.map((url, i) => (
                  <div key={url} className="original-item">
                    <StudioImage src={url} alt={`商品原图 ${i + 1}`} />
                    <button
                      className="original-remove"
                      aria-label={`移除原图 ${i + 1}`}
                      onClick={() => setOriginals((prev) => prev.filter((u) => u !== url))}
                    >
                      <CloseIcon />
                    </button>
                  </div>
                ))}
                {originals.length < MAX_ORIGINALS && (
                  <button className="original-add" onClick={() => fileInputRef.current?.click()}>
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7">
                      <path d="M12 5v14M5 12h14" />
                    </svg>
                    添加原图
                  </button>
                )}
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                hidden
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) onUploadFile(f)
                  e.target.value = ''
                }}
              />
              {(draftPayload?.images?.length ?? 0) > 0 && (
                <div className="originals-pick">
                  <span className="originals-pick-label">从已采集图选择：</span>
                  <div className="originals-pick-list">
                    {draftPayload!.images!.slice(0, MAX_ORIGINALS).map((url) => (
                      <button
                        key={url}
                        className={`originals-pick-item${originals.includes(url) ? ' picked' : ''}`}
                        onClick={() => toggleOriginal(url)}
                        title={originals.includes(url) ? '已选（点击移除）' : '点击选择'}
                      >
                        <StudioImage src={url} alt="采集图" />
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </section>

          {/* ── ② 商品卖点&要求 ── */}
          <section className="card section-card">
            <div className="section-head">
              <h2 className="section-title">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7">
                  <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z" />
                  <path d="M19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16z" />
                </svg>
                商品卖点 &amp; 要求
              </h2>
              <span className="section-sub">卖点快照已本地保存（extensions 快照）</span>
            </div>
            <div className="section-body">
              <div className="form-grid">
                <div className="field">
                  <label className="form-label" htmlFor="is-pname">产品名称</label>
                  <input
                    id="is-pname"
                    className="field-input"
                    value={selling.productName}
                    placeholder={productTitle || '产品名称'}
                    onChange={(e) => setSelling((s) => ({ ...s, productName: e.target.value }))}
                  />
                </div>
                <div className="field">
                  <label className="form-label" htmlFor="is-audience">适用人群</label>
                  <input
                    id="is-audience"
                    className="field-input"
                    value={selling.audience}
                    placeholder="如 成人 / 儿童 / 宠物主人"
                    onChange={(e) => setSelling((s) => ({ ...s, audience: e.target.value }))}
                  />
                </div>
                <div className="field span-2">
                  <label className="form-label" htmlFor="is-points">
                    核心卖点
                    <span className="hint">（AI 帮写 1 次调用，从商品编辑页进入可用）</span>
                  </label>
                  <div className="input-wrap">
                    <textarea
                      id="is-points"
                      className="form-textarea"
                      rows={3}
                      value={selling.sellingPoints}
                      placeholder="如 便携无线、大容量、防水防尘…"
                      onChange={(e) => setSelling((s) => ({ ...s, sellingPoints: e.target.value }))}
                    />
                    <button className="ai-btn" disabled={aiBusy || !draftId} onClick={aiWrite} title="AI 帮写（需从商品编辑页进入）">
                      {aiBusy ? <span className="spinner-inline" /> : <SparkIcon />}
                    </button>
                  </div>
                </div>
                <div className="field">
                  <label className="form-label" htmlFor="is-scenes">期望场景</label>
                  <input
                    id="is-scenes"
                    className="field-input"
                    value={selling.scenes}
                    placeholder="如 户外露营 / 家庭使用"
                    onChange={(e) => setSelling((s) => ({ ...s, scenes: e.target.value }))}
                  />
                </div>
                <div className="field">
                  <label className="form-label" htmlFor="is-params">具体参数提示</label>
                  <input
                    id="is-params"
                    className="field-input"
                    value={selling.params}
                    placeholder="如 尺寸 30cm、材质 ABS"
                    onChange={(e) => setSelling((s) => ({ ...s, params: e.target.value }))}
                  />
                </div>
              </div>
            </div>
          </section>

          {/* ── ③ 商品图配置（C3b 映射，材质/尺寸置灰） ── */}
          <section className="card section-card">
            <div className="section-head">
              <h2 className="section-title">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7">
                  <rect x="3" y="5" width="18" height="14" rx="2.5" />
                  <path d="M3 9.5h18M8 5v14" />
                </svg>
                商品图配置
              </h2>
              <span className={`section-sub${planIssue ? ' studio-plan-warn' : ''}`}>
                已选 <strong>{planTotal}</strong> 张（最少 {MIN_PLAN_TOTAL} 张）
                {planIssue ? ` · ${planIssue}` : ''}
              </span>
            </div>
            <div className="section-body">
              <div className="slot-grid">
                {SLOT_TYPES.map((t) => {
                  const count = counts[t.key] ?? 0
                  const max = t.max ?? 1
                  return (
                    <div key={t.key} className={`slot-card config${t.disabled ? ' slot-disabled' : ''}`}>
                      <div className="slot-config-head">
                        <span className="slot-card-name">{t.label}</span>
                        {t.disabled && <span className="badge badge-v2">第二版开放</span>}
                      </div>
                      <span className="slot-config-desc">{t.desc}</span>
                      {t.disabled ? (
                        <div className="slot-config-disabled">v1 暂不提供</div>
                      ) : (
                        <div className="stepper">
                          <button
                            className="stepper-btn"
                            disabled={count <= 0}
                            onClick={() => setCount(t.key, -1)}
                            aria-label={`减少${t.label}`}
                          >
                            −
                          </button>
                          <span className="stepper-val">{count}</span>
                          <button
                            className="stepper-btn"
                            disabled={count >= max}
                            onClick={() => setCount(t.key, 1)}
                            aria-label={`增加${t.label}`}
                          >
                            +
                          </button>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
              <div className="studio-plan-preview">
                <span className="studio-plan-label mono">image_gen_plan</span>
                <code className="mono">{JSON.stringify(plan)}</code>
                <span className="studio-plan-note">N 张 = N 次调用（无积分概念）</span>
              </div>
            </div>
          </section>

          {/* ── ④ 富内容配置（v2 置灰） ── */}
          <section className="card section-card">
            <div className="section-head">
              <h2 className="section-title">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7">
                  <rect x="3.5" y="4" width="17" height="16" rx="2.5" />
                  <path d="M8 9h8M8 13h5M8 17h3" />
                </svg>
                富内容配置
              </h2>
              <span className="section-sub">共 {RICH_TYPES.length} 种 · 第二版开放</span>
            </div>
            <div className="section-body">
              <div className="rich-grid">
                {RICH_TYPES.map((name) => (
                  <div key={name} className="rich-item" title="第二版开放">
                    <span>{name}</span>
                    <span className="badge badge-v2">第二版开放</span>
                  </div>
                ))}
              </div>
            </div>
          </section>

          {/* ── ⑤ 一键生成 ── */}
          <div className="bottom-bar studio-gen-bar">
            <span className="toolbar-count">
              {identity.kind === 'task' ? '将按当前配置逐张重新生成（version++）' : '草稿模式下仅保存配置，提交上架后按计划生成'}
            </span>
            <span className="toolbar-spacer" />
            <button
              className="btn btn-primary"
              disabled={!!planIssue || generating || identity.kind !== 'task'}
              onClick={openGenerateConfirm}
            >
              {balanceState.checking ? <span className="spinner" /> : null}
              {generating ? '生成中…' : '一键生成'}
            </button>
          </div>

          {/* ── ⑥ 效果预览 ── */}
          <section className="card section-card">
            <div className="section-head">
              <h2 className="section-title">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7">
                  <rect x="3.5" y="3.5" width="17" height="17" rx="2.5" />
                  <circle cx="9" cy="9" r="1.8" />
                  <path d="M4.5 18.5l5-5 3.5 3.5 3-3 3.5 3.5" />
                </svg>
                效果预览
              </h2>
              <span className="section-sub">
                {identity.kind === 'task' ? `${groupedBySlot.size} 个槽位 · version 标识 · 单张可重新生成/对比` : '提交上架后生成的图片将显示在这里'}
              </span>
            </div>
            <div className="section-body">
              {identity.kind !== 'task' ? (
                <div className="empty-state">
                  <p className="empty-state-text">该草稿暂无上架任务，暂无已生成图片</p>
                </div>
              ) : !imagesLoaded ? (
                <div className="empty-state">
                  <div className="spinner" style={{ borderColor: 'rgba(0, 91, 255, 0.2)', borderTopColor: 'var(--color-brand)' }} />
                  <p className="empty-state-text">加载已生成图片…</p>
                </div>
              ) : images.length === 0 ? (
                <div className="empty-state">
                  <p className="empty-state-title">暂无已生成图片</p>
                  <p className="empty-state-text">任务执行完 AI 生图阶段后，10 个槽位的结果会显示在这里</p>
                </div>
              ) : (
                <div className="preview-grid">
                  {[...groupedBySlot.entries()].map(([slot, versions]) => (
                    <SlotCard
                      key={slot}
                      slot={slot}
                      versions={versions}
                      busy={regenBusySlot === slot || generating}
                      onRegen={regenOne}
                      onCompare={setCompareSlot}
                    />
                  ))}
                </div>
              )}
            </div>
          </section>
        </>
      )}

      {/* ── 一键生成确认弹窗（余额 + 预计消耗） ── */}
      {confirmOpen && (
        <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && setConfirmOpen(false)}>
          <div className="modal" role="dialog" aria-modal="true" aria-label="确认生成">
            <div className="modal-header">
              <h2 className="modal-title">一键生成</h2>
              <button className="modal-close" aria-label="关闭" onClick={() => setConfirmOpen(false)}>
                <CloseIcon />
              </button>
            </div>
            <div className={`confirm-balance${balanceState.valid ? ' balance-ok' : ' balance-bad'}`}>
              {balanceState.valid ? <CheckIcon /> : <WarningIcon />}
              <span>
                MXOU 余额{balanceState.valid ? '充足' : '不足'}
                {balanceState.valid ? '，可继续生成' : '，请充值后再试'}
              </span>
            </div>
            <p className="modal-text">
              预计消耗 <strong>{planTotal} 次调用</strong>（{planTotal} 张 = 每次生成 1 次调用，无积分概念），
              将按 image_gen_plan 逐张重新生成：
            </p>
            <div className="studio-plan-preview modal-plan">
              <code className="mono">{JSON.stringify(plan)}</code>
            </div>
            {Object.values(selling).some((v) => v.trim()) && (
              <p className="modal-text modal-selling">
                卖点要求：
                <br />
                {snapshotFrom(selling)}
              </p>
            )}
            <div className="modal-foot">
              <button className="btn" disabled={generating} onClick={() => setConfirmOpen(false)}>取消</button>
              <button className="btn btn-primary" disabled={generating || !balanceState.valid} onClick={runGenerate}>
                {generating ? <span className="spinner" /> : null}
                {generating ? '生成中…' : `确认生成 ${planTotal} 张`}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── 图片对比弹窗（旧 vs 新） ── */}
      {compareSlot && (
        <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && setCompareSlot('')}>
          <div className="modal compare-modal" role="dialog" aria-modal="true" aria-label="图片对比">
            <div className="modal-header">
              <h2 className="modal-title">{slotLabel(compareSlot)} · 旧 vs 新</h2>
              <button className="modal-close" aria-label="关闭" onClick={() => setCompareSlot('')}>
                <CloseIcon />
              </button>
            </div>
            <div className="compare-grid">
              <div className="compare-col">
                <span className="compare-tag">旧版本 v{compareOld?.version ?? '-'}</span>
                <StudioImage src={compareOld?.url} alt={`${slotLabel(compareSlot)} 旧版本`} className="compare-img" />
              </div>
              <div className="compare-col">
                <span className="compare-tag compare-tag-new">新版本 v{compareNew?.version ?? '-'}</span>
                <StudioImage src={compareNew?.url} alt={`${slotLabel(compareSlot)} 新版本`} className="compare-img" />
              </div>
            </div>
            {compareVersions.length < 2 && (
              <p className="modal-text">该槽位仅一个版本，重新生成后可对比新旧效果</p>
            )}
            <div className="modal-foot">
              <button className="btn" onClick={() => setCompareSlot('')}>关闭</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
