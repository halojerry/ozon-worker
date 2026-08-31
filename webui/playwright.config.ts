import { defineConfig, devices } from "@playwright/test"

// v0.62.2: WebUI 全页审计 —— 目标为 worker 同进程伺服的前端（/app 前缀）。
// 启动栈: bash scripts/test-docker-e2e.sh
export default defineConfig({
  testDir: "./e2e",
  timeout: 35_000,
  fullyParallel: false,
  retries: 0,
  reporter: [
    ["list"],
    ["json", { outputFile: "e2e-results.json" }],
  ],
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://localhost:8080/app",
    acceptDownloads: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1600, height: 1000 },
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  outputDir: "e2e/artifacts",
})
