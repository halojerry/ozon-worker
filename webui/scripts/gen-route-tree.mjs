// 独立 routeTree 生成脚本：在 build 前显式生成（vite build 模式 configResolved 不触发，见 CONVENTIONS）
// 用法：node scripts/gen-route-tree.mjs  （npm run build 前执行）
import { tanstackRouterGenerator } from '@tanstack/router-plugin/vite'

const plugin = tanstackRouterGenerator({ target: 'react' })
const hooks = Array.isArray(plugin) ? plugin : [plugin]

for (const p of hooks) {
  const resolved = p.vite || p
  if (typeof resolved.configResolved === 'function') {
    await resolved.configResolved({ root: process.cwd() })
  }
}
console.log('routeTree.gen.ts 已生成')
