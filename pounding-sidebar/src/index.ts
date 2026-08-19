import type { Context } from 'cordis'

/**
 * pounding-sidebar host half。
 *
 * 本插件的业务全部在 client half（浏览器侧）：经 ctx.betterSidebar
 * 服务注册 7 个业务板块 tab。host 半仅占一个挂载位，保持最小实现；
 * 后续如需 host 侧能力（如代理 worker 鉴权、扫描 vault 目录），在此扩展。
 */
export const name = 'pounding-sidebar'

export function apply(_ctx: Context): void {
  // host half 预留：无 Node 侧逻辑（服务只在 client half）
}
