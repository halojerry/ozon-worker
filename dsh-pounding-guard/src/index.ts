/**
 * dsh-pounding-guard — 三级安全门控（老板眼皮底下 vs 黑盒）。
 *
 * 订阅 `tools/pre-execute`，对 `mcp__pounding__*` 工具按 read/write/destructive 分级：
 * - read：黑盒直跑（next()）
 * - write / destructive：返回 `{ kind: 'ask', reason }`，由 harness 的 approval seam 解析
 *
 * 依据 dsh 官方契约（0.1.0-rc.7）：
 * - `PreToolDecision = { kind:'allow' } | { kind:'deny', reason } | { kind:'ask', reason? }`
 * - `ask` 走 approval seam（需挂载 @deepseek-ai/dsh-user-approval + 一个 answerer）
 * - ApprovalRequest 不携带工具参数，参数摘要须塞进 reason 字符串
 *
 * @module dsh-pounding-guard
 */

import type { Context } from '@deepseek-ai/cordis'
import type { PreToolDecision } from '@deepseek-ai/dsh-tools'

export const name = 'pounding-guard'
export const inject = ['tools']

/** 工具名前缀（dsh-mcp-client 注册的 serverName=ozon 工具）。 */
const PREFIX = 'mcp__pounding__'

/** 三级安全分级：read（黑盒直跑）/ write（需审批）/ destructive（需审批）。 */
type Safety = 'read' | 'write' | 'destructive'

const SAFETY_MAP: Record<string, Safety> = {
  // read —— 只读/采集，黑盒直跑
  check: 'read',
  list_stores: 'read',
  search: 'read', // --auto-submit 时动态升级 write
  probe: 'read',
  image_search: 'read',
  category: 'read',
  follow: 'read', // --auto-submit / --to-box 时动态升级 write
  discover: 'read', // --auto-submit / --to-box 时动态升级 write
  discover_multi: 'read', // --auto-submit / --to-box 时动态升级 write
  seller: 'read',
  queries: 'read',
  query: 'read',
  // write —— 配置/提交，老板眼皮底下
  set_store: 'write',
  set_token: 'write',
  set_ak: 'write',
  get_ak: 'write',
  graph: 'write', // 默认提交；--no-submit 时降为 read
  update: 'write',
  // destructive —— 破坏性，需审批
  cleanup: 'destructive',
}

/** 提交类 flag：命中即把 read 动态升级为 write。 */
const ESCALATING_FLAGS = ['auto_submit', 'to_box', 'submit']

/** 根据工具名 + 参数解析最终安全级别。 */
function resolveSafety(rawName: string, args: unknown): Safety {
  const base = SAFETY_MAP[rawName] ?? 'read'
  if (base === 'read' && args && typeof args === 'object') {
    for (const flag of ESCALATING_FLAGS) {
      if ((args as Record<string, unknown>)[flag] === true) return 'write'
    }
  }
  return base
}

/** 生成人读审批摘要（ApprovalRequest 不携带参数，须塞进 reason）。 */
function summarize(rawName: string, safety: Safety, args: unknown): string {
  const a = (args && typeof args === 'object' ? args : {}) as Record<string, unknown>
  const store = a.store ? `店铺『${a.store}』` : '默认店铺'
  switch (rawName) {
    case 'set_store':
      return `配置 Ozon 店铺凭证（${store}）`
    case 'set_token':
      return '设置 MXOU 平台 token'
    case 'set_ak':
      return '设置 1688 Access Key'
    case 'get_ak':
      return '浏览器自动获取 1688 AK'
    case 'graph':
      if (a.no_submit === true) return '只组装上架信封（不提交）'
      if (a.to_box === true) return `组装并写入采集箱（${store}）`
      return `提交上架到 ${store}（1688 商品 ${a.item_id || a.url || ''}）`
    case 'search':
    case 'follow':
    case 'discover':
    case 'discover_multi':
      return `提交上架（${rawName}，${store}）`
    case 'update':
      return '检查并应用 skill 自动更新'
    case 'cleanup':
      return '清理缓存/临时数据（破坏性操作）'
    default:
      return `执行 ${rawName}${safety === 'destructive' ? '（破坏性操作）' : ''}`
  }
}

/**
 * 挂载审批门控：read 放行，write/destructive 返回 ask。
 * 需要与 @deepseek-ai/dsh-user-approval + 一个 answerer 一起挂载，
 * 否则 ask 会 fail-closed（unavailable → deny）。
 */
export function apply(ctx: Context): void {
  ctx.on('tools/pre-execute', async (exec, next): Promise<PreToolDecision> => {
    if (!exec.name.startsWith(PREFIX)) return next()
    const rawName = exec.name.slice(PREFIX.length)
    const safety = resolveSafety(rawName, exec.arguments)
    if (safety === 'read') return next()
    return { kind: 'ask', reason: summarize(rawName, safety, exec.arguments) }
  })
}
