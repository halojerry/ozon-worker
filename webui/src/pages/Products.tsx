import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  aiField,
  deleteDraft,
  getDraft,
  getDrafts,
  listCredentials,
  patchDraft,
  submitDraft,
  type CredentialOut,
  type Draft,
  type DraftVariant,
  type Envelope,
  type SubmitResponse,
} from '../api/client'

const DUP_FALLBACK = '重复商品：目标店铺已存在相同商品'

const SECTIONS = [
  { id: 'section-main', label: '主要信息' },
  { id: 'section-attrs', label: '产品属性' },
  { id: 'section-variants', label: '变体设置' },
] as const

const AI_FIELDS = ['title', 'description', 'tags', 'attributes'] as const
type AiField = (typeof AI_FIELDS)[number]

const AI_FIELD_LABEL: Record<AiField, string> = {
  title: '标题',
  description: '简介',
  tags: '主题标签',
  attributes: '更多属性',
}

const COL_LABEL: Record<string, string> = {
  image: '图片',
  sku_id: '货号',
  price: '我的售价',
  original_price: '我的划线价',
  min_price: '我的最低价',
}

type SameFirstCol = 'image' | 'sku_id' | 'price' | 'original_price' | 'min_price'

/* ── 内联 SVG 图标（无 emoji，沿用全站描边图标风格） ── */

function IconSparkles() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3zM19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9L19 15z" />
    </svg>
  )
}

function IconDown() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M12 4v13m0 0l-5-5m5 5l5-5M4 21h16" />
    </svg>
  )
}

function IconPlus() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M12 5v14M5 12h14" />
    </svg>
  )
}

function IconTrash() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="M4 7h16M9 7V5a1 1 0 011-1h4a1 1 0 011 1v2M6.5 7l.8 13a1 1 0 001 1h7.4a1 1 0 001-1l.8-13M10 11v6M14 11v6" />
    </svg>
  )
}

function IconClose() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  )
}

function IconCheck() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M4.5 12.5l5 5 10-11" />
    </svg>
  )
}

function IconAlert() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="M12 4L2.5 20h19L12 4zM12 10v4m0 3v.01" />
    </svg>
  )
}

function IconInfo() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5m0-8v.01" />
    </svg>
  )
}

/* ────────────────────────────────────────────────
 * 编辑表单模型（输入态字符串，保存时转数值/结构）
 * ──────────────────────────────────────────────── */

interface AttrRow {
  key: string
  value: string
}

interface VariantRow {
  _key: number
  _checked: boolean
  sku_id: string
  price: string
  original_price: string
  min_price: string
  image?: string
  color?: string
  size?: string
  stock?: string
  [key: string]: unknown
}

interface EditForm {
  title: string
  description: string
  tags: string
  remark: string
  weight: string
  length: string
  width: string
  height: string
  stock: string
  warehouseId: string
  attrs: AttrRow[]
  variants: VariantRow[]
}

function variantToRow(v: DraftVariant, i: number): VariantRow {
  return {
    _key: i,
    _checked: false,
    sku_id: String(v.sku_id ?? ''),
    price: v.price != null ? String(v.price) : '',
    original_price: v.original_price != null ? String(v.original_price) : '',
    min_price: v.min_price != null ? String(v.min_price) : '',
    image: v.image || '',
    color: v.color || '',
    size: v.size || '',
    stock: v.stock != null ? String(v.stock) : '',
  }
}

function initForm(payload: Envelope): EditForm {
  const d = payload.draft
  const ext = payload.extensions || {}
  return {
    title: d.title ?? '',
    description: d.description ?? '',
    tags: d.tags ?? '',
    remark: d.remark ?? '',
    weight: d.weight != null ? String(d.weight) : '',
    length: d.dimensions?.length != null ? String(d.dimensions.length) : '',
    width: d.dimensions?.width != null ? String(d.dimensions.width) : '',
    height: d.dimensions?.height != null ? String(d.dimensions.height) : '',
    stock: ext.stock != null ? String(ext.stock) : '',
    warehouseId: ext.warehouse_id != null ? String(ext.warehouse_id) : '',
    attrs: Object.entries(d.attributes ?? {}).map(([key, value]) => ({ key, value: String(value) })),
    variants: (d.variants ?? []).map(variantToRow),
  }
}

function buildEnvelope(f: EditForm, original: Draft, scheduledAt: string): Envelope {
  const env = JSON.parse(JSON.stringify(original.payload)) as Envelope
  const d = env.draft
  d.title = f.title
  d.description = f.description || undefined
  d.tags = f.tags || undefined
  d.remark = f.remark || undefined
  if (f.weight.trim() !== '') d.weight = Number(f.weight)
  d.dimensions = {
    length: f.length.trim() !== '' ? Number(f.length) : 0,
    width: f.width.trim() !== '' ? Number(f.width) : 0,
    height: f.height.trim() !== '' ? Number(f.height) : 0,
  }
  const attrs: Record<string, string> = {}
  for (const row of f.attrs) {
    const key = row.key.trim()
    if (key) attrs[key] = row.value
  }
  d.attributes = attrs
  d.variants = f.variants.map((v) => {
    const { _key, _checked, price, original_price, min_price, ...rest } = v
    return {
      ...rest,
      price: price !== '' ? Number(price) : undefined,
      original_price: original_price !== '' ? Number(original_price) : undefined,
      min_price: min_price !== '' ? Number(min_price) : undefined,
    } as DraftVariant
  })
  const ext: Record<string, unknown> = { ...(env.extensions || {}) }
  if (f.warehouseId.trim()) ext.warehouse_id = f.warehouseId.trim()
  else delete ext.warehouse_id
  if (f.stock.trim()) ext.stock = Number(f.stock)
  else delete ext.stock
  if (scheduledAt) ext.scheduled_at = new Date(scheduledAt).toISOString()
  else delete ext.scheduled_at
  env.extensions = ext
  return env
}
function isoToLocalInput(iso: unknown): string {
  if (!iso) return ''
  const dt = new Date(String(iso))
  if (Number.isNaN(dt.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}`
}

function errText(e: unknown): string {
  const err = e as { response?: { data?: { detail?: string } }; message?: string }
  return err?.response?.data?.detail ?? err?.message ?? '操作失败'
}

/* ────────────────────────────────────────────────
 * 路由入口：/products（列表）与 /products/{draftId}（编辑）
 * ──────────────────────────────────────────────── */

export default function Products() {
  const { draftId } = useParams<{ draftId: string }>()
  if (draftId) return <EditDraft draftId={draftId} key={draftId} />
  return <DraftPicker />
}

/* ────────────────────────────────────────────────
 * /products 无参数兜底：草稿选择列表
 * ──────────────────────────────────────────────── */

function DraftPicker() {
  const [drafts, setDrafts] = useState<Draft[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    let alive = true
    getDrafts()
      .then((rows) => {
        if (alive) setDrafts(rows)
      })
      .catch((e: unknown) => {
        if (alive) setError(errText(e))
      })
    return () => {
      alive = false
    }
  }, [])

  return (
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">商品编辑</h1>
        <span className="page-badge">T10b</span>
      </div>
      {error && (
        <div className="alert alert-error">
          <IconAlert />
          <span>{error}</span>
        </div>
      )}
      {!drafts && !error && (
        <div className="page-loading">
          <span className="spinner-inline" />
          加载草稿列表…
        </div>
      )}
      {drafts && drafts.length === 0 && (
        <div className="card placeholder-card">
          <p className="placeholder-text">采集箱为空。请先通过 Skill 采集商品（graph/follow --to-box），或到采集箱选择商品编辑。</p>
          <Link className="btn" to="/collect-box">
            去采集箱
          </Link>
        </div>
      )}
      {drafts && drafts.length > 0 && (
        <div className="draft-list">
          {drafts.map((d) => (
            <div key={d.id} className="card draft-row">
              <div className="draft-row-title">{d.payload.draft.title || '（未命名草稿）'}</div>
              <div className="draft-row-meta">
                {d.payload.draft.variants?.length ?? 1} 个 SKU · v{d.version} ·{' '}
                {d.updated_at ? new Date(d.updated_at).toLocaleString() : ''}
              </div>
              <button className="btn btn-small btn-primary" onClick={() => navigate(`/products/${d.id}`)}>
                编辑上架
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/* ────────────────────────────────────────────────
 * EditDraft：三区块锚点导航 + 编辑 + 保存 + 立即上架
 * ──────────────────────────────────────────────── */

function EditDraft({ draftId }: { draftId: string }) {
  const [draft, setDraft] = useState<Draft | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [form, setForm] = useState<EditForm | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [aiErrors, setAiErrors] = useState<Partial<Record<AiField, string>>>({})
  const [aiBusy, setAiBusy] = useState<Set<AiField>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  const [submitBusy, setSubmitBusy] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitSuccess, setSubmitSuccess] = useState<SubmitResponse | null>(null)
  const [confirm, setConfirm] = useState<{ stores: string[]; target: string } | null>(null)
  const [credentials, setCredentials] = useState<CredentialOut[]>([])
  const [credentialId, setCredentialId] = useState('')
  const [keepCollected, setKeepCollected] = useState(true)
  const [scheduledAt, setScheduledAt] = useState('')
  const [activeSection, setActiveSection] = useState<string>(SECTIONS[0].id)
  const nextKeyRef = useRef(0)
  const navigate = useNavigate()

  function updateForm(patch: Partial<EditForm> | ((f: EditForm) => EditForm)) {
    setForm((prev) => {
      if (!prev) return prev
      return typeof patch === 'function' ? patch(prev) : { ...prev, ...patch }
    })
    setDirty(true)
  }

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const [d, creds] = await Promise.all([getDraft(draftId), listCredentials()])
        if (!alive) return
        setDraft(d)
        setCredentials(creds)
        const def = creds.find((c) => c.is_default)
        setCredentialId(def?.id ?? '')
        setForm(initForm(d.payload))
        setScheduledAt(isoToLocalInput(d.payload.extensions?.scheduled_at))
        nextKeyRef.current = (d.payload.draft.variants ?? []).length
      } catch (e: unknown) {
        if (alive) setLoadError(errText(e))
      }
    })()
    return () => {
      alive = false
    }
  }, [draftId])

  useEffect(() => {
    const scroller: Element | Window = document.querySelector('.app-main') ?? window
    const onScroll = () => {
      let current: string = SECTIONS[0].id
      for (const s of SECTIONS) {
        const el = document.getElementById(s.id)
        if (el && el.getBoundingClientRect().top <= 88) current = s.id
      }
      setActiveSection(current)
    }
    scroller.addEventListener('scroll', onScroll, { passive: true })
    onScroll()
    return () => scroller.removeEventListener('scroll', onScroll)
  }, [])

  const richText = useMemo(() => {
    if (!draft) return null
    const d = draft.payload.draft as Record<string, unknown>
    const raw = d.rich_content ?? d.json_content
    if (raw === undefined) return null
    if (typeof raw === 'string') {
      try {
        return JSON.stringify(JSON.parse(raw), null, 2)
      } catch {
        return raw
      }
    }
    return JSON.stringify(raw, null, 2)
  }, [draft])

  const ozonCategory = draft?.payload.draft.ozon_category
  const categoryText =
    ozonCategory && (ozonCategory.description_category_id || ozonCategory.type_id)
      ? `description_category_id=${String(ozonCategory.description_category_id ?? '')} · type_id=${String(ozonCategory.type_id ?? '')}`
      : '未指定（Worker 自动匹配类目）'

  const purchaseUrl =
    draft?.payload.draft.purchase_url ?? draft?.payload.source?.purchase_url ?? ''

  const targetShopName = (() => {
    if (!credentialId) return '默认店铺'
    const c = credentials.find((x) => x.id === credentialId)
    return c?.shop_name || c?.ozon_client_id || '目标店铺'
  })()

  async function runAi(field: AiField): Promise<boolean> {
    if (!draft) return false
    setAiBusy((prev) => new Set(prev).add(field))
    try {
      const res = await aiField(draft.id, field)
      if (field === 'title') updateForm((f) => ({ ...f, title: res.value }))
      else if (field === 'description') updateForm((f) => ({ ...f, description: res.value }))
      else if (field === 'tags') updateForm((f) => ({ ...f, tags: res.value }))
      else {
        const obj = JSON.parse(res.value) as Record<string, string>
        updateForm((f) => ({
          ...f,
          attrs: Object.entries(obj).map(([key, value]) => ({ key, value })),
        }))
      }
      setAiErrors((prev) => ({ ...prev, [field]: undefined }))
      setNotice(`${AI_FIELD_LABEL[field]}已由 AI 生成（俄语），保存草稿后生效`)
      return true
    } catch (e: unknown) {
      setAiErrors((prev) => ({ ...prev, [field]: errText(e) }))
      return false
    } finally {
      setAiBusy((prev) => {
        const next = new Set(prev)
        next.delete(field)
        return next
      })
    }
  }

  async function aiFillAll() {
    setBulkBusy(true)
    let done = 0
    for (const field of AI_FIELDS) {
      if (await runAi(field)) done += 1
    }
    setNotice(`AI 填写完成：${done}/${AI_FIELDS.length} 个字段（空字段/失败自动跳过）`)
    setBulkBusy(false)
  }

  async function saveDraft(): Promise<Draft | null> {
    if (!draft || !form) return null
    setSaving(true)
    try {
      const saved = await patchDraft(draft.id, draft.version, buildEnvelope(form, draft, scheduledAt))
      setDraft(saved)
      setDirty(false)
      setNotice(`草稿已保存（version ${saved.version}）`)
      return saved
    } catch (e: unknown) {
      if ((e as { response?: { status?: number } })?.response?.status === 409) {
        const fresh = await getDraft(draft.id)
        setDraft(fresh)
        setForm(initForm(fresh.payload))
        setDirty(false)
        setNotice('草稿在其他窗口被修改，已刷新为最新版本（未保存的编辑已丢弃）')
      } else {
        setSubmitError(`保存草稿失败：${errText(e)}`)
      }
      return null
    } finally {
      setSaving(false)
    }
  }

  async function afterSubmit(res: SubmitResponse) {
    setSubmitSuccess(res)
    if (!keepCollected) {
      try {
        await deleteDraft(res.draft_id)
        setNotice('已按「不保留采集数据」从采集箱移除该草稿')
      } catch {
        setNotice('提交成功；采集数据清理失败，请到采集箱手动删除')
      }
    }
  }

  async function doSubmit() {
    if (!draft) return
    setSubmitBusy(true)
    setSubmitError(null)
    try {
      let current = draft
      if (dirty) {
        const saved = await saveDraft()
        if (!saved) return
        current = saved
      }
      const res = await submitDraft(current.id, credentialId || undefined)
      if (res.confirm_required && res.existing_stores.length > 0) {
        setConfirm({ stores: res.existing_stores, target: targetShopName })
        return
      }
      await afterSubmit(res)
    } catch (e: unknown) {
      if ((e as { response?: { status?: number } })?.response?.status === 409) {
        setSubmitError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? DUP_FALLBACK)
      } else {
        setSubmitError(`上架提交失败：${errText(e)}`)
      }
    } finally {
      setSubmitBusy(false)
    }
  }

  async function confirmSubmit() {
    if (!draft) return
    setConfirm(null)
    setSubmitBusy(true)
    setSubmitError(null)
    try {
      const res = await submitDraft(draft.id, credentialId || undefined)
      await afterSubmit(res)
    } catch (e: unknown) {
      if ((e as { response?: { status?: number } })?.response?.status === 409) {
        setSubmitError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? DUP_FALLBACK)
      } else {
        setSubmitError(`上架提交失败：${errText(e)}`)
      }
    } finally {
      setSubmitBusy(false)
    }
  }

  function sameAsFirst(col: SameFirstCol) {
    if (!form || form.variants.length < 2) return
    const first = form.variants[0][col]
    updateForm((f) => ({
      ...f,
      variants: f.variants.map((r, i) => (i === 0 ? r : { ...r, [col]: first })),
    }))
    setNotice(`已把「${COL_LABEL[col]}」首行值应用到全部 ${form.variants.length} 个变体`)
  }

  function addVariant() {
    updateForm((f) => ({
      ...f,
      variants: [
        ...f.variants,
        {
          _key: nextKeyRef.current++,
          _checked: false,
          sku_id: '',
          price: '',
          original_price: '',
          min_price: '',
          image: '',
          color: '',
          size: '',
        },
      ],
    }))
  }

  function removeSelectedVariants() {
    if (!form) return
    const selected = form.variants.filter((v) => v._checked).length
    if (!selected) {
      setNotice('请先勾选要删除的变体')
      return
    }
    updateForm((f) => ({ ...f, variants: f.variants.filter((v) => !v._checked) }))
    setNotice(`已删除 ${selected} 个变体`)
  }

  function setVariantField(key: number, field: string, value: unknown) {
    updateForm((f) => ({
      ...f,
      variants: f.variants.map((r) => (r._key === key ? { ...r, [field]: value } : r)),
    }))
  }

  function setModelName(value: string) {
    updateForm((f) => {
      const idx = f.attrs.findIndex((r) => /型号|货号|модель|модел/i.test(r.key))
      if (idx >= 0) {
        const rows = f.attrs.slice()
        rows[idx] = { ...rows[idx], value }
        return { ...f, attrs: rows }
      }
      return { ...f, attrs: [...f.attrs, { key: '型号', value }] }
    })
  }

  if (loadError) {
    return (
      <div className="page">
        <div className="alert alert-error">
          <IconAlert />
          <span>{loadError}</span>
        </div>
        <Link className="btn" to="/collect-box">
          返回采集箱
        </Link>
      </div>
    )
  }

  if (!draft || !form) {
    return (
      <div className="page">
        <div className="page-loading">
          <span className="spinner-inline" />
          加载草稿…
        </div>
      </div>
    )
  }

  const modelIdx = form.attrs.findIndex((r) => /型号|货号|модель|модел/i.test(r.key))
  const modelValue = modelIdx >= 0 ? form.attrs[modelIdx].value : ''
  const aiErrLines = (Object.entries(aiErrors) as [AiField, string][]).filter(([, m]) => m)

  return (
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">商品编辑</h1>
        <span className="page-badge">T10b</span>
        <span className="draft-row-meta">
          {draft.id.slice(0, 8)}… · v{draft.version}
        </span>
      </div>

      <nav className="anchor-nav" aria-label="编辑区块导航">
        {SECTIONS.map((s) => (
          <a
            key={s.id}
            href={`#${s.id}`}
            className={`anchor-link${activeSection === s.id ? ' active' : ''}`}
            onClick={() => setActiveSection(s.id)}
          >
            {s.label}
          </a>
        ))}
        <span className="anchor-save-state">
          {dirty ? '有未保存的修改' : '已保存'}
        </span>
      </nav>

      {submitError && (
        <div className="alert alert-error">
          <IconAlert />
          <span>{submitError}</span>
          <span className="alert-actions">
            <button className="btn btn-sm" onClick={() => setSubmitError(null)}>
              关闭
            </button>
          </span>
        </div>
      )}

      {submitSuccess && (
        <div className="alert alert-success">
          <IconCheck />
          <span>
            已提交上架任务：task_id <code>{submitSuccess.task_id}</code>（状态 {submitSuccess.status}
            ）{!keepCollected && '；已按设置移除采集箱草稿'}
          </span>
          <span className="alert-actions">
            <Link className="btn btn-sm" to={`/tasks?task_id=${submitSuccess.task_id}`}>
              查看进度
            </Link>
            <Link className="btn btn-sm" to="/collect-box">
              返回采集箱
            </Link>
          </span>
        </div>
      )}

      {notice && (
        <div className="alert alert-info">
          <IconInfo />
          <span>{notice}</span>
          <span className="alert-actions">
            <button className="btn btn-sm" onClick={() => setNotice(null)}>
              关闭
            </button>
          </span>
        </div>
      )}

      {aiErrLines.length > 0 && (
        <div className="alert alert-error">
          <IconAlert />
          <span>{aiErrLines.map(([f, m]) => `${AI_FIELD_LABEL[f]}：${m}`).join('；')}</span>
          <span className="alert-actions">
            <button className="btn btn-sm" onClick={() => setAiErrors({})}>
              关闭
            </button>
          </span>
        </div>
      )}

      {/* ── 主要信息 ── */}
      <section id="section-main" className="card section-card">
        <div className="section-head">
          <h2 className="section-title">
            <IconInfo />
            主要信息
          </h2>
          <span className="section-sub">类目/品牌由采集数据决定，其余可手动或 AI 编辑</span>
        </div>
        <div className="section-body form-grid">
          <div className="field">
            <label className="form-label" htmlFor="store-select">
              上架店铺
            </label>
            <select
              id="store-select"
              className="form-select"
              value={credentialId}
              onChange={(e) => {
                setCredentialId(e.target.value)
                setDirty(true)
              }}
            >
              <option value="">默认店铺（未选择时使用 is_default）</option>
              {credentials.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.shop_name || c.ozon_client_id}（{c.ozon_client_id} · {c.currency}
                  {c.is_default ? ' · 默认' : ''}）
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label className="form-label">产品类目</label>
            <div className="readonly-value">{categoryText}</div>
          </div>
          <div className="field">
            <label className="form-label">品牌</label>
            <div className="readonly-value">Нет бренда（无品牌，平台强制）</div>
          </div>
          <div className="field span-2">
            <label className="form-label" htmlFor="title-input">
              标题
              <span className="hint">AI 生成俄语标题（调 T14b ai/title）</span>
            </label>
            <div className="input-wrap">
              <input
                id="title-input"
                className="field-input"
                value={form.title}
                placeholder="产品标题"
                onChange={(e) => updateForm({ title: e.target.value })}
              />
              <button
                className="ai-btn"
                title="AI 生成/翻译（俄语）"
                disabled={aiBusy.has('title')}
                onClick={() => runAi('title')}
              >
                {aiBusy.has('title') ? <span className="spinner-inline" /> : <IconSparkles />}
              </button>
            </div>
          </div>
          <div className="field">
            <label className="form-label" htmlFor="weight-input">
              包装重量（克）
              <span className="hint">AI 暂未开放该字段</span>
            </label>
            <div className="input-wrap">
              <input
                id="weight-input"
                className="field-input"
                type="number"
                min="0"
                value={form.weight}
                placeholder="如 350"
                onChange={(e) => updateForm({ weight: e.target.value })}
              />
              <button className="ai-btn" title="AI 暂未开放该字段（T14b 仅 title/description/tags/attributes）" disabled>
                <IconSparkles />
              </button>
            </div>
          </div>
          <div className="field">
            <label className="form-label">
              包装尺寸（长 × 宽 × 高，mm）
              <span className="hint">AI 暂未开放该字段</span>
            </label>
            <div className="input-wrap">
              <input
                className="field-input dim-input"
                type="number"
                min="0"
                value={form.length}
                placeholder="长"
                onChange={(e) => updateForm({ length: e.target.value })}
              />
              <span className="dim-sep">×</span>
              <input
                className="field-input dim-input"
                type="number"
                min="0"
                value={form.width}
                placeholder="宽"
                onChange={(e) => updateForm({ width: e.target.value })}
              />
              <span className="dim-sep">×</span>
              <input
                className="field-input dim-input"
                type="number"
                min="0"
                value={form.height}
                placeholder="高"
                onChange={(e) => updateForm({ height: e.target.value })}
              />
              <button className="ai-btn" title="AI 暂未开放该字段（T14b 仅 title/description/tags/attributes）" disabled>
                <IconSparkles />
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* ── 产品属性 ── */}
      <section id="section-attrs" className="card section-card">
        <div className="section-head">
          <h2 className="section-title">
            <IconSparkles />
            产品属性
          </h2>
          <span className="toolbar-spacer" />
          <button
            className="btn btn-small"
            disabled={aiBusy.has('attributes')}
            onClick={() => runAi('attributes')}
          >
            {aiBusy.has('attributes') ? <span className="spinner-inline" /> : <IconSparkles />}
            AI 翻译属性（俄语）
          </button>
        </div>
        <div className="section-body form-grid">
          <div className="field">
            <label className="form-label" htmlFor="model-input">
              型号名称
              <span className="hint">合并自 1688 属性「型号/货号」</span>
            </label>
            <input
              id="model-input"
              className="field-input"
              value={modelValue}
              placeholder="型号/货号（合并到产品属性）"
              onChange={(e) => setModelName(e.target.value)}
            />
          </div>
          <div className="field">
            <label className="form-label" htmlFor="tags-input">
              # 主题标签
              <span className="hint">逗号分隔；AI 生成俄语标签（调 T14b ai/tags）</span>
            </label>
            <div className="input-wrap">
              <input
                id="tags-input"
                className="field-input"
                value={form.tags}
                placeholder="标签1, 标签2"
                onChange={(e) => updateForm({ tags: e.target.value })}
              />
              <button
                className="ai-btn"
                title="AI 生成主题标签（俄语）"
                disabled={aiBusy.has('tags')}
                onClick={() => runAi('tags')}
              >
                {aiBusy.has('tags') ? <span className="spinner-inline" /> : <IconSparkles />}
              </button>
            </div>
          </div>
          <div className="field span-2">
            <label className="form-label" htmlFor="desc-input">
              简介
              <span className="hint">采集时由 1688 属性自动拼接；AI 生成俄语描述（调 T14b ai/description）</span>
            </label>
            <div className="input-wrap input-wrap-start">
              <textarea
                id="desc-input"
                className="form-textarea"
                value={form.description}
                placeholder="产品简介（俄语）"
                onChange={(e) => updateForm({ description: e.target.value })}
              />
              <button
                className="ai-btn"
                title="AI 生成简介（俄语）"
                disabled={aiBusy.has('description')}
                onClick={() => runAi('description')}
              >
                {aiBusy.has('description') ? <span className="spinner-inline" /> : <IconSparkles />}
              </button>
            </div>
          </div>
          <div className="field span-2">
            <label className="form-label">JSON 富内容</label>
            <div className="readonly-value">
              {richText !== null ? <code>{richText}</code> : '（无 JSON 富内容，可由生图工作台生成）'}
            </div>
          </div>
          <div className="field span-2">
            <label className="form-label">填写更多属性</label>
            <div className="attr-rows">
              {form.attrs.map((row, i) => (
                <div key={i} className="attr-row">
                  <input
                    className="field-input"
                    value={row.key}
                    placeholder="属性名"
                    onChange={(e) =>
                      updateForm((f) => ({
                        ...f,
                        attrs: f.attrs.map((r, j) => (j === i ? { ...r, key: e.target.value } : r)),
                      }))
                    }
                  />
                  <input
                    className="field-input"
                    value={row.value}
                    placeholder="属性值"
                    onChange={(e) =>
                      updateForm((f) => ({
                        ...f,
                        attrs: f.attrs.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)),
                      }))
                    }
                  />
                  <button
                    className="icon-btn"
                    title="删除该属性"
                    onClick={() =>
                      updateForm((f) => ({ ...f, attrs: f.attrs.filter((_, j) => j !== i) }))
                    }
                  >
                    <IconTrash />
                  </button>
                </div>
              ))}
              <button
                className="btn btn-small"
                onClick={() => updateForm((f) => ({ ...f, attrs: [...f.attrs, { key: '', value: '' }] }))}
              >
                <IconPlus />
                添加属性
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* ── 变体设置 ── */}
      <section id="section-variants" className="card section-card">
        <div className="section-head">
          <h2 className="section-title">
            <IconDown />
            变体设置
          </h2>
          <span className="section-sub">同首行 = 把首行值应用到全部变体</span>
        </div>
        <div className="section-body">
          <div className="variant-toolbar">
            <button className="btn btn-small" onClick={addVariant}>
              <IconPlus />
              添加变体
            </button>
            <button className="btn btn-small btn-danger" onClick={removeSelectedVariants}>
              <IconTrash />
              批量删除
            </button>
            <span className="toolbar-spacer" />
            <span className="toolbar-hint">共 {form.variants.length} 个变体</span>
          </div>
          {form.variants.length === 0 ? (
            <div className="variant-empty">暂无变体，点击「添加变体」创建（单 SKU 商品也可不填变体）</div>
          ) : (
            <div className="stores-table-wrap">
              <table className="variant-table">
                <thead>
                  <tr>
                    <th className="col-check" aria-label="选择">
                      <input
                        type="checkbox"
                        checked={form.variants.every((v) => v._checked)}
                        onChange={(e) =>
                          updateForm((f) => ({
                            ...f,
                            variants: f.variants.map((v) => ({ ...v, _checked: e.target.checked })),
                          }))
                        }
                      />
                    </th>
                    <th>
                      <span className="col-head">
                        图片
                        <button
                          className="same-row-btn"
                          disabled={form.variants.length < 2}
                          onClick={() => sameAsFirst('image')}
                        >
                          <IconDown />
                          同首行
                        </button>
                      </span>
                    </th>
                    <th>
                      <span className="col-head">
                        货号
                        <button
                          className="same-row-btn"
                          disabled={form.variants.length < 2}
                          onClick={() => sameAsFirst('sku_id')}
                        >
                          <IconDown />
                          同首行
                        </button>
                      </span>
                    </th>
                    <th>
                      <span className="col-head">
                        我的售价
                        <button
                          className="same-row-btn"
                          disabled={form.variants.length < 2}
                          onClick={() => sameAsFirst('price')}
                        >
                          <IconDown />
                          同首行
                        </button>
                      </span>
                    </th>
                    <th>
                      <span className="col-head">
                        我的划线价
                        <button
                          className="same-row-btn"
                          disabled={form.variants.length < 2}
                          onClick={() => sameAsFirst('original_price')}
                        >
                          <IconDown />
                          同首行
                        </button>
                      </span>
                    </th>
                    <th>
                      <span className="col-head">
                        我的最低价
                        <button
                          className="same-row-btn"
                          disabled={form.variants.length < 2}
                          onClick={() => sameAsFirst('min_price')}
                        >
                          <IconDown />
                          同首行
                        </button>
                      </span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {form.variants.map((v) => (
                    <tr key={v._key}>
                      <td className="col-check">
                        <input
                          type="checkbox"
                          checked={v._checked}
                          onChange={(e) => setVariantField(v._key, '_checked', e.target.checked)}
                        />
                      </td>
                      <td>
                        {v.image ? (
                          <img className="variant-thumb" src={v.image} alt="变体图" />
                        ) : (
                          <div className="thumb-placeholder">无图</div>
                        )}
                        {(v.color || v.size) && (
                          <div className="variant-name">
                            {[v.color, v.size].filter(Boolean).join(' / ')}
                          </div>
                        )}
                      </td>
                      <td>
                        <input
                          className="field-input"
                          value={v.sku_id}
                          placeholder="货号"
                          onChange={(e) => setVariantField(v._key, 'sku_id', e.target.value)}
                        />
                      </td>
                      <td>
                        <input
                          className="field-input"
                          type="number"
                          step="any"
                          min="0"
                          value={v.price}
                          placeholder="售价"
                          onChange={(e) => setVariantField(v._key, 'price', e.target.value)}
                        />
                      </td>
                      <td>
                        <input
                          className="field-input"
                          type="number"
                          step="any"
                          min="0"
                          value={v.original_price}
                          placeholder="划线价"
                          onChange={(e) => setVariantField(v._key, 'original_price', e.target.value)}
                        />
                      </td>
                      <td>
                        <input
                          className="field-input"
                          type="number"
                          step="any"
                          min="0"
                          value={v.min_price}
                          placeholder="最低价"
                          onChange={(e) => setVariantField(v._key, 'min_price', e.target.value)}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="form-grid">
            <div className="field">
              <label className="form-label" htmlFor="purchase-url">
                货源地址
              </label>
              <div className="readonly-value">{purchaseUrl || '（无货源链接）'}</div>
            </div>
            <div className="field">
              <label className="form-label" htmlFor="remark-input">
                货源备注
              </label>
              <input
                id="remark-input"
                className="field-input"
                value={form.remark}
                placeholder="备注（仅供采集箱查看）"
                onChange={(e) => updateForm({ remark: e.target.value })}
              />
            </div>
            <div className="field">
              <label className="form-label" htmlFor="warehouse-select">
                选择仓库
                <span className="hint">写入 extensions.warehouse_id</span>
              </label>
              <input
                id="warehouse-select"
                className="field-input"
                list="warehouse-options"
                value={form.warehouseId}
                placeholder="默认仓库（不指定）"
                onChange={(e) => updateForm({ warehouseId: e.target.value })}
              />
              <datalist id="warehouse-options">
                <option value="FBO" />
                <option value="FBS" />
              </datalist>
            </div>
            <div className="field">
              <label className="form-label" htmlFor="stock-input">
                库存数量
                <span className="hint">应用于全部 SKU（写入 extensions.stock）</span>
              </label>
              <input
                id="stock-input"
                className="field-input"
                type="number"
                min="0"
                value={form.stock}
                placeholder="如 100"
                onChange={(e) => updateForm({ stock: e.target.value })}
              />
            </div>
          </div>
        </div>
      </section>

      {/* ── 底部操作栏 ── */}
      <div className="bottom-bar">
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={keepCollected}
            onChange={(e) => setKeepCollected(e.target.checked)}
          />
          保留采集数据
        </label>
        <button className="btn" onClick={aiFillAll} disabled={bulkBusy || submitBusy || !!submitSuccess}>
          {bulkBusy ? <span className="spinner-inline" /> : <IconSparkles />}
          {bulkBusy ? 'AI 填写中…' : 'AI 填写产品信息'}
        </button>
        <button
          className="btn"
          onClick={() => navigate(`/image-studio?draftId=${draftId}`)}
          disabled={submitBusy}
          title="生图工作台：配置 AI 商品套图"
        >
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7">
            <rect x="3.5" y="3.5" width="17" height="17" rx="2.5" />
            <circle cx="9" cy="9" r="1.8" />
            <path d="M4.5 18.5l5-5 3.5 3.5 3-3 3.5 3.5" />
          </svg>
          AI商品套图
        </button>
        <button
          className="btn"
          onClick={() => saveDraft()}
          disabled={saving || submitBusy || !dirty || !!submitSuccess}
        >
          {saving ? '保存中…' : '保存草稿'}
        </button>
        <span className="toolbar-spacer" />
        <div className="schedule-field">
          <span className="field-label">定时上架</span>
          <input
            type="datetime-local"
            value={scheduledAt}
            title="v1 仅保存设置（extensions.scheduled_at），调度在后续版本生效"
            onChange={(e) => {
              setScheduledAt(e.target.value)
              setDirty(true)
            }}
          />
        </div>
        <button className="btn btn-ghost" onClick={() => navigate('/collect-box')} disabled={submitBusy}>
          <IconClose />
          关闭
        </button>
        <button className="btn btn-primary" onClick={doSubmit} disabled={submitBusy || !!submitSuccess}>
          {submitBusy ? <span className="spinner" /> : null}
          {submitBusy ? '提交中…' : '立即上架'}
        </button>
      </div>

      {/* ── 跨店确认弹窗（C5 v1：不硬拦，确认后二次提交） ── */}
      {confirm && (
        <div className="modal-mask" role="dialog" aria-modal="true" aria-labelledby="cross-store-title">
          <div className="modal">
            <h3 className="modal-title" id="cross-store-title">
              跨店上架确认
            </h3>
            <p className="modal-text">
              该商品已上架到店铺 <strong>{confirm.stores.join('、')}</strong>，确认继续上架到店铺{' '}
              <strong>{confirm.target}</strong>？
            </p>
            <div className="modal-actions">
              <button className="btn" onClick={() => setConfirm(null)}>
                取消
              </button>
              <button className="btn btn-primary" onClick={confirmSubmit} disabled={submitBusy}>
                {submitBusy ? <span className="spinner" /> : null}
                确认上架
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
