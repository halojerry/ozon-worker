/**
 * Axios 客户端 —— WebUI ↔ Worker API 统一入口（baseURL = /api/v1）。
 *
 * ⚠️ 类型来源（单一真相源，见 docs/PLAN-webui-v1.md §1.4）：
 *   本文件应从 worker 的 openapi.json 用 openapi-typescript 生成。
 *   生成脚本落地后（T15 接入 CI），下方手写类型由
 *   `import type { components, paths } from './generated'` 取代：
 *
 *   npx openapi-typescript http://localhost:8080/api/v1/openapi.json \
 *     -o src/api/generated.d.ts
 *
 *   届时保留本文件的结构（api 实例 + 拦截器 + verifyToken 薄封装），
 *   只替换类型引用 —— 端点变化自动反映到类型，前端编译期兜底。
 */

import axios from 'axios'

/** localStorage 中 token 的存储键（auth 持久化唯一位置） */
export const TOKEN_STORAGE_KEY = 'ozon_webui_token'

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY)
}

/* ────────────────────────────────────────────────────────────
 * 类型占位（openapi-typescript 生成后删除此区块）
 * ──────────────────────────────────────────────────────────── */

export interface AuthVerifyRequest {
  /** MXOU API Key（可带 sk- 前缀，服务端自动剥离） */
  token: string
  /** Ozon Client-Id（可选；传了才会触发 Ozon API 有效性探测） */
  client_id?: string
  api_key?: string
}

export type AuthVerifyReason =
  | 'ok'
  | 'token_invalid'
  | 'balance_insufficient'
  | 'account_inactive'
  | 'invalid_request'

export interface AuthVerifyResponse {
  valid: boolean
  reason: AuthVerifyReason
  expires_in: number
  ozon_valid?: boolean | null
}

/* ── T10 采集箱：草稿 + 提交状态（C1 两表模型，响应见 worker DraftOut） ── */

/** draft_submissions.status 状态机（C1）：无行 = 未上架；rejected = Ozon 审核被拒（M0.3 写回） */
export type DraftSubmissionStatus = 'pending' | 'uploading' | 'published' | 'failed' | 'rejected'

export interface Envelope {
  draft: {
    item_id?: string
    title?: string
    description?: string
    tags?: string
    remark?: string
    images?: string[]
    weight?: number
    dimensions?: { length: number; width: number; height: number }
    purchase_cost?: number
    purchase_url?: string
    currency?: string
    supplier?: string
    stock?: number
    attributes?: Record<string, string>
    variants?: Array<{
      sku_id?: string
      color?: string
      size?: string
      image?: string
      price?: number
      original_price?: number
      stock?: number
    }>
    ozon_category?: Record<string, unknown>
    [key: string]: unknown
  }
  source?: { purchase_url?: string; purchase_cost?: number }
  extensions?: Record<string, unknown>
}

export interface Draft {
  id: string
  tenant_id: string
  payload: Envelope
  source: 'skill' | 'webui'
  version: number
  created_at?: string | null
  updated_at?: string | null
  /** 最新一次提交状态；NULL = 未上架（T10 上架状态列） */
  submission_status?: DraftSubmissionStatus | null
}

/* ────────────────────────────────────────────────────────────
 * Axios 实例 + 拦截器
 * ──────────────────────────────────────────────────────────── */

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
})

// 请求拦截器：注入 Authorization: Bearer {token}
api.interceptors.request.use((config) => {
  const token = getStoredToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截器：401 → 清除本地凭证并回登录页
api.interceptors.response.use(
  (resp) => resp,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem(TOKEN_STORAGE_KEY)
      if (window.location.pathname !== '/app/login') {
        window.location.href = '/app/login'
      }
    }
    return Promise.reject(error)
  },
)

/* ────────────────────────────────────────────────────────────
 * API 薄封装（业务函数按端点递增，T5 起新增端点在此追加）
 * ──────────────────────────────────────────────────────────── */

/** POST /api/v1/auth/verify —— 校验 MXOU token（body 传 token，见 main.py auth_verify） */
export async function verifyToken(payload: AuthVerifyRequest): Promise<AuthVerifyResponse> {
  const { data } = await api.post<AuthVerifyResponse>('/auth/verify', payload)
  return data
}

/* ────────────────────────────────────────────────────────────
 * /api/v1/credentials (T5) — 店铺凭证（掩码回显/轮换/校验，T11 消费）
 * ──────────────────────────────────────────────────────────── */

export interface CredentialOut {
  id: string
  /** Ozon 卖家 Client-Id（半公开） */
  ozon_client_id: string
  /** 掩码 ****XXXX（仅后 4 位；明文 key 永不回显） */
  api_key_masked: string
  shop_name?: string | null
  currency: string
  is_default: boolean
  credential_type: string
  status: string
  last_validated_at?: string | null
  last_rotated_at?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface CredentialCreateRequest {
  ozon_client_id: string
  /** 明文 Api-Key，仅请求体携带 */
  api_key: string
  shop_name?: string
  currency?: string
  is_default?: boolean
}

export interface CredentialUpdateRequest {
  /** 新 Api-Key（轮换：旧行 revoked + 新行 active，默认标记继承） */
  api_key: string
  shop_name?: string
  currency?: string
}

export interface ValidateResponse {
  valid: boolean
  /** ok / invalid_key / ozon_api_error / decrypt_failed */
  reason: string
  last_validated_at?: string | null
}

/** GET /api/v1/credentials —— 列表（仅掩码） */
export async function listCredentials(): Promise<CredentialOut[]> {
  const { data } = await api.get<CredentialOut[]>('/credentials')
  return data
}

/** POST /api/v1/credentials —— 创建（加密 + 掩码） */
export async function createCredential(payload: CredentialCreateRequest): Promise<CredentialOut> {
  const { data } = await api.post<CredentialOut>('/credentials', payload)
  return data
}

/** PATCH /api/v1/credentials/{id} —— 轮换（旧行 revoked + 新行 active） */
export async function rotateCredential(
  id: string,
  payload: CredentialUpdateRequest,
): Promise<CredentialOut> {
  const { data } = await api.patch<CredentialOut>(`/credentials/${id}`, payload)
  return data
}

/** DELETE /api/v1/credentials/{id} —— 吊销（软删 status=revoked） */
export async function revokeCredential(id: string): Promise<{ ok: boolean; id: string }> {
  const { data } = await api.delete<{ ok: boolean; id: string }>(`/credentials/${id}`)
  return data
}

/** POST /api/v1/credentials/{id}/validate —— 解密 → Ozon probe → {valid, reason} */
export async function validateCredential(id: string): Promise<ValidateResponse> {
  const { data } = await api.post<ValidateResponse>(`/credentials/${id}/validate`)
  return data
}

/* ────────────────────────────────────────────────────────────
 * /api/v1/drafts (T6) — 采集箱草稿（T10 消费）
 * ──────────────────────────────────────────────────────────── */

/** GET /api/v1/drafts —— 采集箱列表（租户隔离 + 最新 submission 状态） */
export async function getDrafts(): Promise<Draft[]> {
  const { data } = await api.get<Draft[]>('/drafts')
  return data
}

/** DELETE /api/v1/drafts/{id} —— 删除草稿（draft_submissions 由 FK 级联删） */
export async function deleteDraft(id: string): Promise<void> {
  await api.delete(`/drafts/${id}`)
}

/* ────────────────────────────────────────────────────────────
 * /api/v1/drafts (T6/T14b) — 草稿单读/乐观锁编辑/立即上架/AI 单字段（T10b 消费）
 * 注：submit 与 ai 端点 body 需携带 token（submit 的 token 用于重建 GraphInput）
 * ──────────────────────────────────────────────────────────── */

/** 变体行（T10b 编辑态扩展：min_price「我的最低价」随草稿持久化，worker v1 透传） */
export type DraftVariant = NonNullable<Envelope['draft']['variants']>[number] & {
  min_price?: number
}

/** GET /api/v1/drafts/{id} —— 草稿详情（租户隔离；不存在/跨租户 → 404） */
export async function getDraft(draftId: string): Promise<Draft> {
  const { data } = await api.get<Draft>(`/drafts/${draftId}`)
  return data
}

/** PATCH /api/v1/drafts/{id} —— 乐观锁编辑（version 必须等于当前，成功 version++） */
export async function patchDraft(
  draftId: string,
  version: number,
  payload: Envelope,
): Promise<Draft> {
  const { data } = await api.patch<Draft>(`/drafts/${draftId}`, { version, payload })
  return data
}

export interface SubmitResponse {
  ok: boolean
  draft_id: string
  submission_id?: string | null
  task_id: string
  status: string
  /** C5 v1 跨店提醒：该草稿已提交到其他店铺（不硬拦） */
  confirm_required: boolean
  existing_stores: string[]
}

/** POST /api/v1/drafts/{id}/submit —— 立即上架（per-store 重复 → 409；跨店 → confirm_required） */
export async function submitDraft(draftId: string, credentialId?: string): Promise<SubmitResponse> {
  const { data } = await api.post<SubmitResponse>(`/drafts/${draftId}/submit`, {
    token: getStoredToken(),
    credential_id: credentialId || undefined,
  })
  return data
}

export interface DraftAiResponse {
  field: string
  value: string
}

/** POST /api/v1/drafts/{id}/ai/{field} —— 单字段 AI 重新生成（只读 RU 值，前端 PATCH 保存） */
export async function aiField(draftId: string, field: string): Promise<DraftAiResponse> {
  const { data } = await api.post<DraftAiResponse>(`/drafts/${draftId}/ai/${field}`, {
    token: getStoredToken(),
  })
  return data
}

/** M1.2 定价预估响应（worker 定价引擎计算；前端只展示，禁止自算公式） */
export interface DraftEstimate {
  price: number
  old_price?: number
  profit_cny: number
  /** 利润率（小数，如 0.32 = 32%；前端仅格式化展示） */
  profit_rate: number
  logistics_cost_cny?: number
  currency: string
}

/** POST /api/v1/drafts/{id}/estimate —— 预估售价/利润/利润率（采集箱决策列） */
export async function estimateDraft(draftId: string): Promise<DraftEstimate> {
  const { data } = await api.post<DraftEstimate>(`/drafts/${draftId}/estimate`, {
    token: getStoredToken(),
  })
  return data
}

/* ────────────────────────────────────────────────────────────
 * /api/v1/tasks (T8) + /task_status/{id} + /resubmit_task/{id} (T12)
 * 任务进度页：列表 / 详情进度（13 阶段）/ 异常重上
 * ──────────────────────────────────────────────────────────── */

/** 任务状态（ozon_product_tasks.status；rejected 为 Ozon 审核被拒终态） */
export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'rejected'

export interface TaskProgress {
  stage?: string
  stage_index?: number
  total_stages?: number
  /** ⚠️ 已知坑：percent 字段可能恒 0，前端进度一律用 stages_completed.length/13 计算 */
  percent?: number
  message?: string
  stages_completed?: string[]
  stages_remaining?: string[]
}

/** result.product_summary 单行（v0.22 产品明细） */
export interface ProductSummary {
  purchase_url?: string
  purchase_cost?: number
  margin_rate?: number
  price?: number | string
  old_price?: number | string
  logistics_cost?: number
  profit_rate?: number
  product_id?: string
  ozon_status?: string
  ozon_error?: string
  sku_id?: string
  category_path?: string
  [key: string]: unknown
}

export interface TaskListItem {
  id: string
  status: TaskStatus
  progress?: TaskProgress | null
  product_summary: ProductSummary[]
  created_at?: string | null
  updated_at?: string | null
  /** T12：task_service 从 payload 安全提取的非敏感展示字段 */
  title?: string
  image?: string
  item_id?: string
  ozon_client_id?: string
  shop_name?: string | null
  follow_sell?: boolean
}

export interface TaskListResponse {
  items: TaskListItem[]
  total: number
  limit: number
  offset: number
}

export interface TaskStatusDetail {
  id: string
  tenant_id?: string
  status: TaskStatus
  payload?: { ozon_client_id?: string; shop_name?: string; envelope?: Envelope } | null
  result?: Record<string, unknown> | null
  error_message?: string | null
  created_at?: string | null
  updated_at?: string | null
  started_at?: string | null
  completed_at?: string | null
  progress?: TaskProgress | null
}

export interface ResubmitResponse {
  task_id: string
  status: string
  message?: string
}

/* ────────────────────────────────────────────────────────────
 * /api/v1/tasks/{id}/images + /images/{slot}/regen (T7a/T13)
 * 生图工作台：图片列表（slot/version/url/params）+ 单槽位强制重生成
 * ──────────────────────────────────────────────────────────── */

export interface TaskImageItem {
  /** 槽位: main/white_bg/multi_angle/detail/social_proof/comparison/scene_1..3/variant_{idx} */
  slot: string
  /** 生成版本（1 起；regen 递增） */
  version: number
  /** 图片 URL（COS/1688 alicdn/Ozon，前端自行处理失效 → 图裂占位） */
  url: string
  /** 节点 Input schema 原样快照 */
  params?: Record<string, unknown> | null
  /** resubmit 图片血缘（原 task_id） */
  image_parent_task_id?: string | null
  created_at?: string | null
}

export interface TaskImagesResponse {
  ok: boolean
  task_id: string
  images: TaskImageItem[]
}

export interface ImageRegenResponse {
  ok: boolean
  task_id: string
  slot: string
  version: number
  url: string
  params?: Record<string, unknown> | null
  image_parent_task_id?: string | null
}

/** GET /api/v1/tasks/{id}/images —— 该任务全部生图缓存行（slot × version） */
export async function getTaskImages(taskId: string): Promise<TaskImagesResponse> {
  const { data } = await api.get<TaskImagesResponse>(`/tasks/${taskId}/images`)
  return data
}

/** POST /api/v1/tasks/{id}/images/{slot}/regen —— 强制重生成（version++ 新行） */
export async function regenTaskImage(taskId: string, slot: string): Promise<ImageRegenResponse> {
  const { data } = await api.post<ImageRegenResponse>(`/tasks/${taskId}/images/${slot}/regen`)
  return data
}

/** GET /api/v1/tasks —— 任务列表（租户隔离 + 分页） */
export async function listTasks(params?: { limit?: number; offset?: number }): Promise<TaskListResponse> {
  const { data } = await api.get<TaskListResponse>('/tasks', { params })
  return data
}

/** GET /api/v1/task_status/{id} —— 任务详情（含 payload/error_message/progress） */
export async function getTaskStatus(taskId: string): Promise<TaskStatusDetail> {
  const { data } = await api.get<TaskStatusDetail>(`/task_status/${taskId}`)
  return data
}

/** POST /api/v1/resubmit_task/{id} —— 异常重上（仅 failed/rejected；body 需带 token） */
export async function resubmitTask(taskId: string): Promise<ResubmitResponse> {
  const { data } = await api.post<ResubmitResponse>(`/resubmit_task/${taskId}`, {
    token: getStoredToken(),
  })
  return data
}

/** GET /api/v1/tasks/{id}/draft —— 任务 → 采集箱草稿来源（直连提交任务无草稿 → draft_id=null） */
export interface TaskDraftResponse {
  draft_id: string | null
}

export async function getTaskDraft(taskId: string): Promise<TaskDraftResponse> {
  const { data } = await api.get<TaskDraftResponse>(`/tasks/${taskId}/draft`)
  return data
}

/* ────────────────────────────────────────────────────────────
 * /api/v1/products (M2.1) — 在售货架（在线商品索引 product_task_index）
 * ──────────────────────────────────────────────────────────── */

/** Ozon 在线商品审核状态（product_task_index.moderation_status / 任务状态回写） */
export type ProductModerationStatus =
  | 'approved'
  | 'pending_moderation'
  | 'pending'
  | 'failed'
  | 'declined'
  | 'rejected'

export interface ProductItem {
  /** Ozon 商品 product_id */
  product_id: string
  offer_id: string
  task_id: string
  draft_id?: string | null
  credential_id?: string | null
  /** 上架/改图时间 */
  created_at?: string | null
  /** 审核状态：approved=已上架 / pending_moderation=重新审核中 / 其他=未知 */
  moderation_status?: ProductModerationStatus | null
}

export interface ProductListResponse {
  items: ProductItem[]
  total: number
  limit: number
  offset: number
}

/** GET /api/v1/products —— 在售货架列表（租户隔离 + limit/offset 分页） */
export async function listProducts(params?: { limit?: number; offset?: number }): Promise<ProductListResponse> {
  const { data } = await api.get<ProductListResponse>('/products', { params })
  return data
}

/** POST /api/v1/products/{product_id}/update_images —— 在线商品改图全量重传（T14） */
export interface UpdateProductImagesResponse {
  ok: boolean
  product_id: string
  offer_id?: string
  /** Ozon /v3/product/import 返回的 task_id（pending_moderation 时有值） */
  import_task_id?: string
  /** 触发重新审核时返回 */
  status?: string
  re_under_review?: boolean
}

export async function updateProductImages(
  productId: string,
  images: string[],
): Promise<UpdateProductImagesResponse> {
  const { data } = await api.post<UpdateProductImagesResponse>(`/products/${productId}/update_images`, { images })
  return data
}

export default api
