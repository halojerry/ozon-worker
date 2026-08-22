import { useCallback, useEffect, useRef, useState } from "react"
import { ApiError } from "./client"

// ── API response types（对齐 worker/src/api/schemas.py）──────────────────

export interface Credential {
  id: string
  ozon_client_id: string
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

export interface ValidateResponse {
  valid: boolean
  reason: string
  last_validated_at?: string | null
}

export interface StoreStats {
  credential_id: string
  ozon_client_id: string
  stats_date: string
  today_orders: number
  today_sales_amount: number
  today_commission: number
  today_profit: number
  today_product_count: number
}

export interface StoreSyncStatus {
  credential_id: string
  orders_last_synced_at?: string | null
  products_last_synced_at?: string | null
  orders_error?: string
  products_error?: string
}

export interface StoreSyncResult {
  credential_id: string
  ozon_client_id: string
  orders?: { synced: number; error?: string }
  products?: { synced: number; error?: string }
}

export interface DraftEnvelopeDraft {
  title?: string
  description?: string
  images?: string[]
  item_id?: string
  purchase_cost?: number
  purchase_url?: string
  weight?: number
  dimensions?: { length?: number; width?: number; height?: number; depth?: number }
  attributes?: Record<string, unknown>
  [key: string]: unknown
}

export interface DraftPayload {
  draft?: DraftEnvelopeDraft
  source?: { purchase_url?: string; purchase_cost?: number }
  extensions?: Record<string, unknown>
  [key: string]: unknown
}

export interface Draft {
  id: string
  tenant_id: string
  payload: DraftPayload
  source: string
  version: number
  created_at?: string | null
  updated_at?: string | null
  submission_status?: string | null
}

export interface DraftAiResponse {
  field: string
  value: string
}

export interface EstimateResponse {
  price: number
  old_price: number
  profit_cny: number
  profit_rate: number
  logistics_cost_cny: number
  currency: string
  commission_rate: number
  commission_source: string
  promo_price?: number
  margin_anchor?: number
  margin_floor?: number
  variable_cost_rate?: number
}

export interface SubmitResponse {
  ok: boolean
  draft_id: string
  submission_id?: string | null
  task_id: string
  status: string
  confirm_required: boolean
  existing_stores: string[]
}

export interface TaskProgress {
  stage?: string
  percent?: number
  stages_completed?: string[]
  stages_remaining?: string[]
  message?: string
}

export interface TaskListItem {
  id: string
  status: string
  progress?: TaskProgress | null
  product_summary?: Record<string, unknown>[]
  created_at?: string | null
  updated_at?: string | null
  title?: string | null
  image?: string | null
  item_id?: string | null
  ozon_client_id?: string | null
  shop_name?: string | null
  follow_sell?: boolean
  update_mode?: boolean
  parent_task_id?: string | null
}

export interface TaskListResponse {
  items: TaskListItem[]
  total: number
  limit: number
  offset: number
}

export interface TaskStatusResponse {
  id?: string
  status: string
  progress?: TaskProgress | null
  error_message?: string | null
  result?: Record<string, unknown> | null
}

export interface TaskDraftResponse {
  draft_id?: string | null
}

export interface TaskImageItem {
  slot: string
  version: number
  url: string
  params?: Record<string, unknown> | null
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

export interface OzonProduct {
  product_id: string
  offer_id: string
  name: string
  image?: string | null
  price?: number | null
  stock?: number | null
  currency?: string
}

export interface OzonProductListResponse {
  items: OzonProduct[]
  total: number
  limit: number
  offset: number
  store?: { id?: string; ozon_client_id?: string }
  last_synced_at?: string | null
  sync_error?: string | null
}

export interface ProductListItem {
  product_id: string
  offer_id: string
  task_id: string
  draft_id?: string | null
  credential_id?: string | null
  created_at?: string | null
  moderation_status?: string | null
}

export interface ProductListResponse {
  items: ProductListItem[]
  total: number
  limit: number
  offset: number
}

export interface ProductEditResponse {
  product_id: string
  offer_id: string
  credential_id?: string | null
  draft_id: string
  payload: DraftPayload
  moderation_status?: string | null
}

// ── Data fetching hooks ────────────────────────────────────────────────

export function useApi<T>(
  fetcher: () => Promise<T>,
  deps: readonly unknown[] = [],
): { data: T | null; loading: boolean; error: string; reload: () => void } {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let live = true
    setLoading(true)
    setError("")
    fetcher()
      .then((d) => { if (live) setData(d) })
      .catch((e) => { if (live) setError(apiErrorMessage(e)) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])
  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { data, loading, error, reload }
}

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  active: boolean,
): { data: T | null; error: string } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState("")
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher
  useEffect(() => {
    if (!active) return
    let live = true
    const run = async () => {
      try {
        const d = await fetcherRef.current()
        if (live) { setData(d); setError("") }
      } catch (e) {
        if (live) setError(apiErrorMessage(e))
      }
    }
    void run()
    const timer = setInterval(() => { void run() }, Math.max(intervalMs, 500))
    return () => { live = false; clearInterval(timer) }
  }, [active, intervalMs])
  return { data, error }
}

// ── Helpers ────────────────────────────────────────────────────────────

export function apiErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401) return "未登录或会话已过期，请重新登录"
    if (e.status === 403) return "无权限访问"
    if (e.status === 404) return "资源不存在"
    if (e.status === 429) return "请求过于频繁，请稍后再试"
    if (e.status === 504) return "请求超时，请稍后重试"
    return e.message || `请求失败（${e.status}）`
  }
  return e instanceof Error ? e.message : String(e)
}

export function formatPrice(n: number | null | undefined, currency = ""): string {
  if (n === null || n === undefined) return "—"
  const text = Number(n).toLocaleString("zh-CN", { maximumFractionDigits: 2 })
  return currency ? `${text} ${currency}` : text
}

export function formatDateTime(s: string | null | undefined): string {
  if (!s) return "—"
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return s
  return d.toLocaleString("zh-CN", { hour12: false })
}

export function draftFields(d: Draft): DraftEnvelopeDraft {
  return d?.payload?.draft ?? {}
}

export function draftFieldsFromPayload(payload?: DraftPayload): DraftEnvelopeDraft {
  return payload?.draft ?? {}
}

const TASK_STATUS_TEXT: Record<string, string> = {
  pending: "排队中",
  running: "运行中",
  uploading: "上传中",
  completed: "已完成",
  published: "已上架",
  failed: "失败",
  cancelled: "已取消",
  rejected: "审核被拒",
}

export function taskStatusText(s: string | undefined | null): string {
  return (s && TASK_STATUS_TEXT[s]) || s || "—"
}

export function taskStatusClass(s: string | undefined | null): string {
  if (s === "completed" || s === "published") return "red"
  if (s === "running" || s === "pending" || s === "uploading") return "dark"
  return "line"
}

const SUBMISSION_STATUS_TEXT: Record<string, string> = {
  pending: "已提交排队",
  uploading: "上传中",
  published: "已上架",
  failed: "失败",
  rejected: "被拒",
}

export function submissionStatusText(s: string | undefined | null): string {
  if (!s) return "未上架"
  return SUBMISSION_STATUS_TEXT[s] ?? s
}

export function submissionStatusClass(s: string | undefined | null): string {
  if (s === "published") return "red"
  if (s === "failed" || s === "rejected") return "line"
  if (!s) return "muted"
  return "dark"
}

// ── Orders types ────────────────────────────────────────────────

export interface OrderProduct {
  name: string
  sku?: number | null
  quantity: number
  price?: number | null
  offer_id: string
  product_id?: number | null
  image?: string | null
}

export interface OrderItem {
  posting_number: string
  status: string
  raw_status: string
  created_at?: string | null
  products: OrderProduct[]
  product_count: number
  total_amount: number
  commission_amount: number
  profit?: number | null
  warehouse: string
  delivery_method: string
  cancel_reason: string
  cancellation: string
}

export interface OrderListResponse {
  items: OrderItem[]
  total: number
  limit: number
  offset: number
  store: Record<string, unknown>
  last_synced_at?: string | null
  sync_error?: string | null
}

export interface CancelReason {
  id: number
  title: string
}

export interface OrderNote {
  posting_number: string
  tenant_id: string
  source_url: string
  source_cost?: number | null
  source_remark: string
  purchase_no: string
  purchase_carrier: string
  purchase_tracking: string
  created_at?: string | null
  updated_at?: string | null
}

// ── Templates types ─────────────────────────────────────────────

export interface TemplateConfig {
  margin_rate?: number | null
  commission_rate?: number | null
  fx_buffer?: number | null
  margin_floor?: number | null
  margin_anchor?: number | null
  variable_cost_rate?: number | null
  promo_variable_cost_rate?: number | null
  traffic_keywords?: string[] | null
  offer_id_prefix?: string | null
  follow_type?: string | null
  stock?: number | null
  warehouse_id?: string | null
}

export interface Template {
  id: string
  tenant_id: string
  name: string
  description: string
  platform: string
  is_default: boolean
  config: TemplateConfig
  store_overrides?: Record<string, TemplateConfig> | null
  created_at?: string | null
  updated_at?: string | null
}

// ── Discovery types ─────────────────────────────────────────────

export interface DiscoveryRun {
  id: string
  keyword: string
  filters?: Record<string, unknown> | null
  candidates?: unknown[] | null
  created_at?: string | null
  contributed_by_token_id: string
}

export interface DiscoveryRunsResponse {
  items: DiscoveryRun[]
  total: number
  limit: number
  offset: number
}

export interface MappingLookupResult {
  found: boolean
  mappings: Array<{ dc: string; tp: string; confidence: number }>
}

export interface SeoKeywordItem {
  query?: string
  keyword?: string
  count?: number
  uniq_queries_wca?: number
}

export interface SeoKeywordsResponse {
  keywords: SeoKeywordItem[]
  total: number
}

// ── Keys types ──────────────────────────────────────────────────

export interface MxouKey {
  id: string
  name: string
  masked: boolean
  status: number
}

export interface MxouKeyCreateResponse {
  id: string
  name: string
  key: string
}

// ── Admin types ─────────────────────────────────────────────────

export interface AdminUser {
  id: string
  username: string
  quota?: number | null
  role: string
  created_at?: string | null
  store_count: number
  task_count: number
}

export interface AdminStore {
  id: string
  tenant_id: string
  ozon_client_id: string
  shop_name: string
  currency: string
  is_default: boolean
  status: string
  last_validated_at?: string | null
}

export interface AdminOverview {
  user_count: number
  store_count: number
  task_total: number
  task_today: number
  success_rate: number
  statistics: Record<string, unknown>
}

export interface AdminUserDetail {
  id: string
  stores: AdminStore[]
  task_total: number
  task_completed: number
  task_failed: number
}

// ── Site types ──────────────────────────────────────────────────

export interface SiteBanner {
  id: string
  title: string
  content: string
  image_url?: string | null
  link_url?: string | null
  is_active: boolean
  sort_order: number
  created_at?: string | null
}

export interface SiteAnnouncement {
  id: string
  title: string
  content: string
  priority: string
  is_active: boolean
  created_at?: string | null
  expires_at?: string | null
}

// ── Analytics types (Batch 5) ───────────────────────────────────

export interface MarketOverview {
  total_gmv: number
  total_orders: number
  total_products: number
  total_discovery_runs: number
  bestseller_count: number
}

export interface SalesTrendItem {
  date: string
  gmv: number
  orders: number
}

export interface SalesTrendResponse {
  items: SalesTrendItem[]
}

export interface HotQueryItem {
  query: string
  count: number
  ca: number | null
  avg_ca_rub: number | null
  avg_count_items: number | null
  items_views: number | null
  uniq_queries_wca: number | null
  uniq_sellers: number | null
}

export interface HotQueriesResponse {
  items: HotQueryItem[]
}

// ── Image Tasks types (Batch 5) ────────────────────────────────

export interface ImageTaskItem {
  id: string
  type: string
  input_image_url: string
  status: string
  result_image_url?: string | null
  params?: Record<string, unknown> | null
  error_message?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface ImageTaskListResponse {
  items: ImageTaskItem[]
  total: number
  limit: number
  offset: number
}

// ── Estimate types (standalone) ─────────────────────────────────

export interface EstimateStandaloneRequest {
  envelope: {
    draft: {
      purchase_cost?: number
      weight?: number
      dimensions?: { length?: number; width?: number; height?: number }
      [key: string]: unknown
    }
    extensions?: Record<string, unknown>
    [key: string]: unknown
  }
  margin_rate?: number
  commission_rate?: number
  fx_buffer?: number
}

// ── Logistics types ─────────────────────────────────────────────

export interface LogisticsQuoteRequest {
  weight_g: number
  depth_cm: number
  width_cm: number
  height_cm: number
  tpl_provider?: string
  service_level?: string
  ozon_client_id?: string
  ozon_api_key?: string
}

export interface LogisticsQuoteResponse {
  logistics_cost_cny: number
  channel: string
  tpl_provider_used: string
  service_level_used: string
  base_cost: number
  per_gram_rate: number
  billable_weight: number
  weight: number
  dims_cm: { depth: number; width: number; height: number }
  fallback_chain: string[]
}
