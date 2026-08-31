import { test, expect, type Page } from "@playwright/test"

const ROUTES = [
  "/",
  "/data",
  "/products",
  "/orders",
  "/tasks",
  "/collection",
  "/pricing",
  "/bestsellers",
  "/stores",
  "/templates",
  "/settings",
  "/admin",
  "/keys",
  "/discovery",
  "/site",
]

// 危险操作关键词：审计只做“可点且安全”的交互，避免误删/误归档/误注销。
const DESTRUCTIVE = /删除|归档|注销|下架|清空|重置|退出|reject|archive|delete|clear|reset|logout/i

async function ensureSession(page: Page) {
  const token = process.env.E2E_TOKEN
  if (token) {
    await page.addInitScript((t) => {
      localStorage.setItem("ozon_webui_token", t)
      localStorage.setItem("ozon_webui_role", "admin")
      localStorage.setItem("ozon_webui_username", "e2e")
    }, token)
    return
  }
  const user = process.env.E2E_USERNAME
  const pass = process.env.E2E_PASSWORD
  if (user && pass) {
    await page.goto("/app/login")
    await page.getByRole("button", { name: /账号密码登录/ }).click()
    await page.getByPlaceholder("请输入账号").fill(user)
    await page.getByPlaceholder("请输入密码").fill(pass)
    await page.getByRole("button", { name: "登录" }).click()
    await page.waitForURL(/\/app\/$/)
    return
  }
  throw new Error("缺少 E2E_TOKEN 或 E2E_USERNAME/E2E_PASSWORD；请提供真实会话后再审计")
}

test.describe("webui 全页审计", () => {
  test.beforeEach(async ({ page }) => {
    const errors: string[] = []
    const pageerrors: string[] = []
    const badResponses: string[] = []
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()) })
    page.on("pageerror", (e) => pageerrors.push(e.message))
    page.on("response", (r) => { if (r.status() >= 400) badResponses.push(`${r.status()} ${r.url()}`) })
    ;(page as unknown as { __ai?: Record<string, unknown> }).__ai = { errors, pageerrors, badResponses }
  })

  for (const route of ROUTES) {
    test(`审计 ${route}`, async ({ page }) => {
      await ensureSession(page)
      const { errors, pageerrors, badResponses } = (page as unknown as { __ai: Record<string, unknown> }).__ai as { errors: string[]; pageerrors: string[]; badResponses: string[] }
      errors.length = 0
      pageerrors.length = 0
      badResponses.length = 0
      await page.goto(`/app${route}`)
      // 部分页面有轮询请求（店铺/任务同步），不用 networkidle/load（会卡 pending 请求）
      await page.waitForLoadState("domcontentloaded")
      await page.waitForTimeout(800)
      // 页面不能空白/报错回退
      const body = page.locator("body")
      await expect(body).toBeVisible()
      const text = (await body.innerText().catch(() => "")).trim()
      expect(text.length).toBeGreaterThan(0)
      expect(text).not.toMatch(/^\s*暂无横幅\s*$/)  // 只允许真实空态，不允许整页只剩单个空态文案
      // 加载渲染硬断言：无未捕获异常、无 5xx、非空白（抓 403 门控/空页类 bug）
      expect(pageerrors.length).toBe(0)
      expect(badResponses.filter((x) => x.startsWith("5")).length).toBe(0)

      // 数据表页(商品/订单)的“编辑/同步/导出”等按钮会触发整页导航/下载，点击扫描噪声大、
      // 会 35s 超时——这两页只做渲染硬断言（足以覆盖空页/403/5xx），交互问题另列。
      if (route !== "/products" && route !== "/orders") {
        // 逐个点击页内安全按钮（JS 派发，避免 Playwright 对触发导航的按钮等待 30s；只点 button）
        const btnCount = await page.locator("button").count()
        for (let i = 0; i < Math.min(btnCount, 30); i++) {
          if (page.isClosed()) break
          const el = page.locator("button").nth(i)
          const label = (await el.innerText().catch(() => "")).trim()
          if (!label || DESTRUCTIVE.test(label)) continue
          if ((await el.getAttribute("target")) === "_blank") continue
          await el.evaluate((node) => { (node as HTMLElement).click() }).catch(() => {})
          await page.waitForTimeout(120)
        }
        await page.waitForTimeout(600).catch(() => {})
        // 点击扫描仅报未捕获异常与新增 5xx；点导航/下载导致跳页/空白不计失败（噪声）
        expect(pageerrors.length).toBe(0)
        expect(badResponses.filter((x) => x.startsWith("5")).length).toBe(0)
      }
      // console error 仅记录，不阻断（如 /keys 本地 401 已被优雅空态处理）
      if (errors.length) console.warn(`[audit] ${route} console errors: ${errors.join(" | ")}`)
    })
  }
})
