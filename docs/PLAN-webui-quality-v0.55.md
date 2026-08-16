# WebUI 质量加固执行方案（v0.55 系列）

> 基于 2026-08-17 深度调研（4 通道并行：测试基础设施 / CI 覆盖 / mxou 规范基线 / 业务页技术债）。
> 目标：消除「规范不可执行」硬伤 + 补齐测试与 CI 空白 + 渐进收齐业务页与 mxou 框架差距。

---

## 一、调研事实基线（2026-08-17 实测）

### 1.1 测试基础设施——从零搭建
| 项 | 现状 |
|---|---|
| 单元测试 | **仅 1 个** `src/components/ui/dropdown-menu.test.tsx`（node:test，非 vitest） |
| vitest | **未安装**（bun.lock 0 匹配），无 vitest.config，无 setup，无 test-utils |
| 测试库 | @testing-library/react / jest-dom / jsdom 均未装 |
| E2E/视觉 | `tests/visual/smoke.spec.ts` 预留但**不可跑**（import @playwright/test 未装）；实际用 CDP 截图（capture_cdp.py + diff_images.py）零依赖方案 |
| test script | package.json **无** |

### 1.2 CI 覆盖——日常 CI 完全空白 + 发版链路隐患
| 项 | 现状 |
|---|---|
| ci.yml | 10 个 job 全 Python，**无任何 webui 步骤**（webui 改动触发 CI 但无人检查） |
| scripts/ci.sh | 无 webui 步骤 |
| cd.yml | **唯一构建 webui 处**：`npm ci` + `npm run build`——⚠️ 但 webui 是 Bun 项目，**无 package-lock.json**，`npm ci` 会失败（发版隐患） |
| Docker | 镜像不构建 webui，compose bind mount 预构建 dist（无需改） |

### 1.3 mxou 规范基线（业务页应遵循的标准）
- **i18n**：131 文件用 useTranslation，6 语言 fallback zh，labelKey 常量模式，static-keys 登记
- **React Query**：8 文件用 useQuery/useMutation，数组 queryKey + placeholderData + toast 错误
- **features 结构**：`features/<feature>/{api.ts,types.ts,constants.ts,index.tsx,components/,lib/,hooks/}`（keys 为完整样例）
- **测试规范**：webui/AGENTS.md §3.14 权威目标（Vitest + RTL + 80% 覆盖率 + 测行为不测实现 + 禁 smoke/sleep 测试）
- **lint/format**：oxlint 1.78 / oxfmt 0.63 **已装但零配置**，无 script，无 .oxlintrc/.oxfmtrc

### 1.4 业务页技术债分布（14 页）

| 页面 | 行数 | 中文串 | useState/useEffect | 旧CSS类 | Tailwind类 | 共享import | 性质 |
|---|---|---|---|---|---|---|---|
| Products | 1550 | 30 | 28 | 132 | 12 | 0 | 最大技术债 |
| Orders | 870 | 17 | 38 | 117 | 3 | 2 | 高交互 |
| OnSale | 797 | 15 | 39 | 94 | 6 | 2 | 高交互 |
| Tasks | 791 | 7 | 18 | 48 | 5 | 4 | 已部分共享 |
| Stores | 595 | 9 | 21 | 62 | 4 | 1 | 中 |
| CollectBox | 569 | 4 | 21 | 61 | 8 | 4 | 已部分共享 |
| Templates | 555 | 13 | 14 | 68 | 7 | 1 | 中 |
| Home | 445 | 11 | 11 | 58 | 11 | 4 | 已部分共享 |
| Admin | 272 | 7 | 12 | 34 | 1 | 2 | 小 |
| Login | 315 | 2 | 12 | 42 | 0 | 0 | ⚠️框架页 |
| PricingTool | 134 | 6 | 5 | 30 | 3 | 1 | 小 |
| Bestsellers | 120 | 1 | 9 | 15 | 0 | 1 | 小 |
| DataScreen | 101 | 0 | 7 | 1 | 0 | 2 | 小 |
| ImageStudio | 57 | 0 | 0 | 6 | 0 | 0 | ⚠️框架页 |

- **Login / ImageStudio 归框架**（mxou 登录页 + embed 组件薄壳），不参与业务页迁移
- **client.ts：59 个端点函数 + 59 个手写类型**（注释声明应 openapi 生成但未做 → 契约漂移风险）

---

## 二、执行方案（3 阶段）

### P0 — 基建（1-2 天，消除规范不可执行硬伤）

**P0-1 接入 lint + typecheck + format 脚本**
1. 建 `.oxlintrc.json`：`{"categories": {"correctness": "error", "suspicious": "error", "perf": "warn", "style": "warn"}}`，规则对齐 AGENTS.md §3.2（禁嵌套三元用 oxlint 规则或留文档约束）
2. 建 `.oxfmtrc.json`（format-with-protected-headers.mjs 已引用该名，补上即可跑通）+ 补 webui 级 `.gitignore`（format 脚本依赖它做 ignore-path）
3. package.json 加脚本：`"typecheck": "tsc -b"`、`"lint": "oxlint"`、`"format": "node scripts/format-with-protected-headers.mjs --write"`、`"format:check": "node scripts/format-with-protected-headers.mjs --check"`
4. 跑 `lint` 修现有告警（预期少量：oxlint 默认规则对 mxou 代码较宽松）
5. **验收**：`npm run typecheck && npm run lint && npm run format:check` 全绿

**P0-2 搭建 vitest 单元测试**
1. 装依赖：`bun add -d vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom`
2. 建 `vitest.config.ts`：`@` 别名同步 vite.config、environment: jsdom、setupFiles、include `src/**/*.test.{ts,tsx}`
3. 建 `vitest.setup.ts`：`@testing-library/jest-dom` 引入 + 全局 cleanup
4. 迁移 `dropdown-menu.test.tsx`：node:test → vitest（对齐 AGENTS.md §3.14 标准断言）
5. **首批业务纯逻辑测试**（低 hanging fruit，无 UI 依赖）：
   - `src/lib/business/format.ts`：fmtTime/fmtMoney/fmtRate 边界（null/无效/货币符号）
   - `src/lib/business/status.ts`：taskStatusMeta/draftStatusMeta 未知状态兜底
   - `src/lib/business/errors.ts`：extractError detail/message/fallback 三链
   - `src/lib/roles.ts`（mxou）：ROLE 判断
6. **验收**：`npm test` 全绿，覆盖率报告可看（`vitest run --coverage` 可选）

**P0-3 接入 CI**
1. `scripts/ci.sh` 加 Step 5c：webui typecheck + lint + test + build（bun 命令）
2. `.github/workflows/ci.yml` 加 `webui` job：`bun install --frozen-lockfile` → typecheck → lint → test → build（ubuntu，bun 官方 setup）
3. **修复 cd.yml 发版隐患**：`npm ci` → `bun install --frozen-lockfile`，cache path `webui/bun.lock`
4. **验收**：本地 `bash scripts/ci.sh --quick` 含 webui 步骤全绿；cd.yml 语法检查通过

### P1 — 业务页规范收敛（3-5 天，渐进不破坏）

**P1-1 契约测试（0.5 天，防 client.ts 漂移）**
- `src/api/client.ts` 注释声明 openapi 生成但未做 → 建 **契约守卫测试**：锁定 59 个端点函数的签名（请求 URL 前缀 `/api/v1/`、token 注入、类型导出完整性）
- 不引入 openapi-typescript 全量生成（worker 侧契约变更需联动，属远期）；先锁现有签名防误改
- **验收**：新增契约测试通过；改 client.ts 端点签名必先改测试

**P1-2 React Query 样板页迁移（1 天）**
- 选 **DataScreen**（101 行最小）做样板：useEffect+useState → useQuery（queryKey `['datascreen', period]` + toast 错误）
- 模式固化后写进 CONVENTIONS（照抄 keys feature 的 useQuery 模式）
- **验收**：DataScreen 无 useState/useEffect 残留、build 绿、渲染验证

**P1-3 i18n 渐进（2-3 天，按中文串密度排）**
- 顺序：PricingTool(6) → Templates(13) → OnSale(15) → Orders(17) → Products(30)
- 模式：页面 JSX 中文 → `const { t } = useTranslation()` + zh locale 文件；labelKey 常量走 static-keys 登记
- **硬约束**：业务页迁移不碰 mxou 框架文件（Login/i18n/config 不动）；**不做** 6 语言全翻译（只建 zh key + en fallback 占位）
- **验收**：迁移页 build 绿 + 渲染中文正常 + `sync-i18n` 无缺失 key

### P2 — 增强（可选，1-2 天）

**P2-1 视觉回归启用**（tests/visual 已备好）
- 方案 A（零依赖，推荐）：CDP 截图 + diff_images.py，基线已存，业务页改动后跑
- 方案 B：装 @playwright/test（或改 import 为 `playwright/test`，playwright 1.62 自带导出），补 playwright.config.ts
- **验收**：冒烟基线 12 用例生成/对比通过

**P2-2 路由懒加载**
- TanStack Router `routeTree` 对 14 页 lazy——首屏 3.45MB 单 chunk 降到几百 KB
- **验收**：build 产物分 chunk，首屏加载时间可测

---

## 三、测试验证方案

| 层 | 工具 | 范围 | 验证点 |
|---|---|---|---|
| 单元 | vitest + RTL | lib/business、lib/ 纯函数、业务页纯逻辑 | 边界/兜底/契约 |
| 组件 | RTL（P1 后补） | 迁移样板页关键交互 | 行为不测实现（§3.14） |
| 契约 | vitest | client.ts 59 端点签名 | 防漂移 |
| 构建 | tsc -b + vite build | 全量 | 0 错误 |
| 视觉 | CDP diff（现有） | 12 冒烟用例 | 像素回归 |
| E2E | playwright（P2） | 登录→业务页→管理员 | 关键流程 |
| 回归 | worker pytest | 1096 基线 | webui 改动不碰 worker，应全绿 |

**每次改动验收门禁**（提交前）：
1. `npm run typecheck` 0 错误
2. `npm run lint` 0 error
3. `npm test` 全绿
4. `npm run build` 0 错误
5. 受影响页 playwright 渲染无 JS 错误
6. worker pytest 1096 基线不降

---

## 四、审计方案

### 4.1 量化指标（可追踪）
| 指标 | 当前 | 目标（v0.55 系列末） |
|---|---|---|
| webui 测试文件数 | 1 | ≥ 10 |
| 单元测试用例数 | 2 | ≥ 60 |
| 覆盖率（lib/ 纯逻辑） | 0% | ≥ 70% |
| package.json 脚本 | 4 | 8（+typecheck/lint/test/format） |
| CI 覆盖 webui | 无 | typecheck+lint+test+build 全挂 |
| client.ts 契约测试 | 0 | 59 端点签名锁定 |
| 业务页共享 import | 10 页 | 12 页（Products/ImageStudio 除外可解释） |
| 业务页 i18n | 0 页 | 5 页（P1-3 范围） |

### 4.2 门禁机制
- **提交门禁**：`bash scripts/ci.sh --quick` 含 webui 步骤（本地）+ CI 全绿（远程）
- **发版门禁**：cd.yml 修复后 `bun install --frozen-lockfile` 保证可复现构建；`webui/dist/index.html` 校验保留
- **覆盖门禁**：新增/改动 lib/business 与 client.ts 必须带测试（评审拦截）

### 4.3 回归机制
- 每次 webui 改动：worker pytest 1096 基线 + webui 全量门禁 + 受影响页渲染
- 每轮 P1 迁移：迁移页前后对比（功能 + 渲染 + 无 JS 错误）

### 4.4 审计节奏
- 每完成一个 P 阶段：更新 CHANGELOG + 本文件指标表 + PRD DoD 回填
- v0.55.0 发版前：全量回归 + 指标表核对

---

## 五、风险与决策

| 风险 | 缓解 |
|---|---|
| oxlint 默认规则对 mxou 代码大量告警 | P0-1 先跑 --fix + 评估规则集；style 级只 warn 不阻断 |
| vitest 接入 jsdom 影响现有组件 | setup 隔离，测试文件独立于运行代码 |
| i18n 迁移破坏中文显示 | 每页迁移后立即渲染验证 + sync-i18n 缺 key 检查 |
| cd.yml npm→bun 改动影响发版 | 本地跑通 `bun install --frozen-lockfile` + build 再提交 |
| 视觉回归基线漂移 | CDP 方案固定视口/冻结动画，基线 git 管理 |

**已决策不执行**（避免无谓重构）：
- 14 页全量 Tailwind 重写（纯视觉重构，已达标）
- 业务页全量 React Query 迁移（只做样板 + 规范）
- openapi-typescript 全量生成（需 worker 联动，远期）
- 6 语言全翻译（只建 zh + en fallback）
