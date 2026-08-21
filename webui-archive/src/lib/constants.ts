import {
  LayoutDashboard,
  Package,
  Rocket,
  ShoppingCart,
  ListTodo,
  Image as ImageIcon,
  Inbox,
  Calculator,
  Flame,
  MonitorPlay,
  LayoutTemplate,
  Store,
  Settings,
  ShieldCheck,
  type LucideIcon,
} from 'lucide-react'

/** 侧栏导航（分组 + 路由 + 图标）。路由为 TanStack Router 类型化路径。 */
export interface NavItem {
  to: '/' | '/products' | '/on-sale' | '/orders' | '/tasks' | '/image-studio' | '/collect-box' | '/pricing' | '/bestsellers' | '/data-screen' | '/templates' | '/stores' | '/settings' | '/admin'
  label: string
  icon: LucideIcon
}

export interface NavGroup {
  label: string
  items: NavItem[]
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: '运营',
    items: [
      { to: '/', label: '仪表盘', icon: LayoutDashboard },
      { to: '/products', label: '商品管理', icon: Package },
      { to: '/on-sale', label: '上架工作台', icon: Rocket },
      { to: '/orders', label: '订单中心', icon: ShoppingCart },
      { to: '/tasks', label: '任务中心', icon: ListTodo },
      { to: '/image-studio', label: '图片工坊', icon: ImageIcon },
      { to: '/collect-box', label: '采集箱', icon: Inbox },
    ],
  },
  {
    label: '数据与配置',
    items: [
      { to: '/pricing', label: '智能定价', icon: Calculator },
      { to: '/bestsellers', label: '热销榜', icon: Flame },
      { to: '/data-screen', label: '数据大屏', icon: MonitorPlay },
      { to: '/templates', label: '上架模板', icon: LayoutTemplate },
      { to: '/stores', label: '店铺管理', icon: Store },
      { to: '/settings', label: '系统设置', icon: Settings },
    ],
  },
  {
    label: '平台',
    items: [{ to: '/admin', label: '管理员后台', icon: ShieldCheck }],
  },
]

/* ── 任务状态（任务中心 / 上架工作台） ──────────────────────────── */
export const TASK_STATUS_MAP: Record<string, { label: string; variant: 'neutral' | 'red' | 'dark' }> = {
  pending: { label: '排队中', variant: 'neutral' },
  running: { label: '进行中', variant: 'red' },
  completed: { label: '已完成', variant: 'neutral' },
  failed: { label: '失败', variant: 'red' },
  cancelled: { label: '已取消', variant: 'neutral' },
}

/* ── 商品状态（商品管理） ───────────────────────────────────────── */
export const PRODUCT_STATUS_MAP: Record<string, { label: string; variant: 'neutral' | 'red' | 'dark' }> = {
  onsale: { label: '在售', variant: 'neutral' },
  out_of_stock: { label: '缺货', variant: 'red' },
  pending: { label: '待上架', variant: 'red' },
  archived: { label: '已归档', variant: 'neutral' },
  draft: { label: '草稿', variant: 'neutral' },
}

/* ── 订单状态（订单中心） ───────────────────────────────────────── */
export const ORDER_STATUS_MAP: Record<string, { label: string; variant: 'neutral' | 'red' | 'dark' }> = {
  awaiting_registration: { label: '待发货', variant: 'red' },
  awaiting_deliver: { label: '待发货', variant: 'red' },
  delivered: { label: '已发货', variant: 'neutral' },
  cancelled: { label: '已取消', variant: 'neutral' },
  accepted: { label: '已接单', variant: 'neutral' },
  arbitration: { label: '仲裁中', variant: 'red' },
  driver_pickup: { label: '取件中', variant: 'neutral' },
}

/* ── 审核状态（Ozon moderate_status） ───────────────────────────── */
export const MODERATE_STATUS_MAP: Record<string, { label: string; variant: 'neutral' | 'red' | 'dark' }> = {
  approved: { label: '已通过', variant: 'neutral' },
  pending: { label: '审核中', variant: 'red' },
  declined: { label: '已拒绝', variant: 'red' },
  rejected: { label: '已拒绝', variant: 'red' },
  imported: { label: '已导入', variant: 'neutral' },
}

/** 通用状态兜底 */
export function resolveStatusMap(map: Record<string, { label: string; variant: 'neutral' | 'red' | 'dark' }>, key: string | null | undefined): { label: string; variant: 'neutral' | 'red' | 'dark' } {
  if (!key) return { label: '未知', variant: 'neutral' }
  return map[key] ?? { label: key, variant: 'neutral' }
}

/** 图片槽位顺序（PRD §3.3 / 图片顺序规范） */
export const IMAGE_SLOT_ORDER = [
  'main',
  'social_proof',
  'detail',
  'scene_1',
  'scene_2',
  'scene_3',
  'comparison',
  'multi_angle',
  'white_bg',
  'multi_info',
] as const

/** 任务进度阶段（worker STAGE_ORDER 对齐，用于任务中心展示） */
export const TASK_STAGES = ['auth', 'ingest', 'category', 'pricing', 'attributes', 'image', 'prepare', 'validate', 'upload', 'status', 'learning'] as const
