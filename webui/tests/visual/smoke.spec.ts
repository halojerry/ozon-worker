/**
 * 视觉回归冒烟基线（M2.4）
 *
 * ⚠️ 当前 webui 未安装 Playwright（依赖面刻意最小），本 spec 预留——
 * 安装后（`npm i -D @playwright/test && npx playwright install chromium`）即可跑：
 *   npx playwright test tests/visual/smoke.spec.ts --update-snapshots   # 首次生成基线
 *   npx playwright test tests/visual/smoke.spec.ts                      # 回归对比
 *
 * 未安装 Playwright 期间的替代验证：见 tests/visual/README.md
 * （Chrome CDP 冒烟：capture_cdp.py 截图 + diff_images.py 像素对比，零依赖）。
 *
 * 基线约定（与 capture_cdp.py 保持一致）：
 *   - 生产构建产物（npm run build && npm run preview -- --port 4173）
 *   - 视口 1440x900（desktop）+ 390x844（mobile，窄屏断点 768px 行为）
 *   - 注入 localStorage token（ozon_webui_token）+ 冻结动画（确定性截图）
 *   - 基线存 tests/visual/baseline/<viewport>/<route>.png
 */

import { test, expect } from '@playwright/test'

const BASE = process.env.VISUAL_QA_BASE ?? 'http://localhost:4173/app'
const TOKEN = 'sk-qa-baseline-000000000000'

/** 需要登录态的路由（注入 token 后进入壳布局；无 token 会重定向登录页） */
const ROUTES: Array<{ name: string; path: string; viewports: Array<[number, number]> }> = [
  { name: 'login', path: '/login', viewports: [[1440, 900], [390, 844]] },
  { name: 'collect-box', path: '/collect-box', viewports: [[1440, 900], [390, 844]] },
  { name: 'products', path: '/products', viewports: [[1440, 900]] },
  { name: 'stores', path: '/stores', viewports: [[1440, 900], [390, 844]] },
  { name: 'tasks', path: '/tasks', viewports: [[1440, 900]] },
  { name: 'on-sale', path: '/on-sale', viewports: [[1440, 900]] },
  { name: 'image-studio', path: '/image-studio', viewports: [[1440, 900]] },
]

test.describe('visual baseline', () => {
  for (const route of ROUTES) {
    for (const [width, height] of route.viewports) {
      test(`${route.name} @${width}x${height}`, async ({ page }) => {
        await page.addInitScript((token) => {
          localStorage.setItem('ozon_webui_token', token)
          // 冻结动画：spinner 相位不漂移，截图确定性（测试态覆盖，不改应用源码）
          const style = document.createElement('style')
          style.textContent = '* { animation: none !important; transition: none !important; }'
          document.addEventListener('DOMContentLoaded', () => {
            ;(document.head || document.body).appendChild(style)
          })
        }, TOKEN)

        await page.setViewportSize({ width, height })
        await page.goto(`${BASE}${route.path}`)
        await page.waitForLoadState('networkidle')
        // fetch 错误/空态渲染稳定窗口
        await page.waitForTimeout(1200)

        await expect(page).toHaveScreenshot(
          `baseline/${width === 1440 ? 'desktop' : 'mobile'}/${route.name}.png`,
          { animations: 'disabled', maxDiffPixelRatio: 0.001 },
        )
      })
    }
  }
})
