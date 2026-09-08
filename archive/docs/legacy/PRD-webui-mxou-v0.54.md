# PRD — WebUI 视觉架构对齐 mxou（new-api default 主题 + 组件体系 + 品牌资产）

> 2026-08-16。目标：让 ozon-worker WebUI 与 api.mxou.cn（New API 平台，`/Volumes/os/dev/ponding-api/source-new-api/web/default/`）**视觉一致、架构同源、品牌元素同套**。只读参照 mxou 仓库，**不改动目标仓库一行**；所有改动发生在 `ozon-worker/webui/`。
> 用户拍板：照搬 default 主题 + 组件 + 布局 + lib 模式 + **品牌元素（logo/favicon/品牌图标）** → 适配我们功能；管理员面板独立化（不做在普通页面里）；AGPL-3.0 版权头忽略（同为项目 owner，内部复用风险最低）；**仓库结构整体规划**（不止 src/）。

## 一、背景与目标

### 1.1 现状（ozon-worker/webui）
- React 18 + Vite + react-router-dom 6 + 手写 CSS（`index.css` 3403 行，`:root` token 变量）
- 5 个手写 UI 组件（Button/Badge/Skeleton/EmptyState），12 个页面（Home/CollectBox/Products/Tasks/Orders/OnSale/Stores/Templates/ImageStudio/Admin/Bestsellers/PricingTool/DataScreen），`api/client.ts` 手写类型
- 管理员面板（v0.51 `/admin`）是普通页面路由，任何登录用户可访问（UI 层隐藏，无路由守卫）

### 1.2 参照（api.mxou.cn / new-api default）
- React 19 + Rsbuild + Tailwind 4 + Base UI + TanStack（Query/Router/Table）+ shadcn 风格，**926 个 ts 文件、61 个 ui 组件、22 个 features、完整 i18n**
- `theme.css`：oklch 色板（`--primary: oklch(0.13 0 0)` 近黑、`--background: oklch(1 0 0)` 纯白、暗色模式完整）
- `lib/api.ts`：axios 拦截器 + 401 跳转 + GET 去重 + sonner toast
- 管理员：独立 route group + `requireRole('admin')` 路由守卫

### 1.3 目标
1. **视觉一致**：webui 打开与 api.mxou.cn 观感一致（同一套 oklch 色板、字体、圆角、组件语言）
2. **架构同源**：`lib/`（api 拦截器/format/errors）、`components/ui`（shadcn 风格）、features/ 组织与 mxou 同构
3. **管理员独立**：`/admin` 改造成独立路由组 + 权限守卫，非 admin 用户被路由层拦截
4. **渐进不破坏**：现有功能逐步迁移，每步 build + 手测不回归；tokens.json 仍是单一真相源

### 1.4 明确不做
- 不迁移构建链（保留 Vite + npm，不引入 Rsbuild/bun）
- 不迁移路由（保留 react-router-dom 6，不引入 TanStack Router）
- 不引入全量 i18n（骨架可留，只配 zh 语言包）
- 不引入 TanStack Table/Virtual/Chart（图表后续按需）
- 不复制 mxou 的 Go 专属 features（channels/keys/wallet/playground/usage-logs/redemption-codes 等 22 个里约一半）
- 不改动 `/Volumes/os/dev/ponding-api/` 任何文件
- **用户体系说明**：登录复用 api.mxou.cn 的 New API 用户体系（同一 Supabase users/tokens）。webui 登录走 worker `/api/v1/mxou/login` 代理（已有），登录响应**补 `role` 字段**供前端管理员守卫——worker 侧仅加 role 字段，不改鉴权逻辑

## 二、技术方案

### 2.1 架构决策
| 决策点 | 选择 | 理由 |
|---|---|---|
| 样式 | **Tailwind 4 + `@theme inline` 桥接 tokens** | 与 mxou 同源；`app.css` 只引 theme+utilities 跳过 preflight，旧页面零影响 |
| 组件 | **全量复制 mxou `components/ui`（61 个）+ 装齐全部依赖** | 用户拍板全量；视觉同源最快路径；后续加组件不用再补依赖 |
| React | **升 React 19.2.6（@types/react@19，连带 react-dom）** | mxou 61 组件 + Base UI 1.5 按 React 19 构建；React 18 代码基本兼容 19，迁移风险可控 |
| 数据层 | **复制 mxou `lib/api.ts` 模式重写**（拦截器 + GET 去重 + toast） | 统一错误处理；不引 TanStack Query（一页一数据源，setInterval 够用） |
| 类型 | 暂不引入 openapi-typescript | 需先补 worker response_model，且 openapi 生成依赖运行态；留后续 |
| **用户体系** | **复用 api.mxou.cn 用户体系**：登录走 New API（`/api/user/login`，用户拍板 B2）| 同一套 Supabase users/tokens；登录响应含 `role` 供管理员守卫 |
| 管理员 | **独立 route group `/admin/*` + 前端路由守卫（role 来自登录响应）** | 对标 mxou requireRole；worker `require_admin` 服务端守卫已有，前端补展示层守卫 |
| 布局 | **复制 mxou `components/layout/` 结构重写**（app-sidebar/app-header/mobile-drawer） | 骨架照搬，业务导航重写 |

### 2.2 色板映射（tokens.json → mxou 语义变量）
```
--color-brand       → --primary  oklch(0.13 0 0)    （近黑，原 #005bff 退役）
--color-brand-hover → 深灰黑阶（oklch(0.24 0 0)）   （原 #0049cc 退役）
--color-brand-light → 中灰阶（oklch(0.45 0 0)）     （原 #3d82ff 退役）
--color-brand-soft  → 灰阶淡背景（oklch(0.96 0 0)） （原 rgba 蓝底退役）
--color-bg          → --background oklch(1 0 0)      （纯白）
--color-text        → --foreground oklch(0.145 0 0)
--color-surface     → --card oklch(1 0 0)
--color-border      → --border oklch(0.93 0 0)
--color-danger      → --destructive oklch(0.577 0.245 27.325)
--color-success     → --success oklch(0.596 0.145 163.225)
--color-warning     → --warning oklch(0.681 0.162 75.834)
--radius-lg         → --radius 1rem
```
- 暗色模式：复制 mxou `.dark` 块完整色板（OpenAI 风 charcoal）
- **4 个品牌 token（brand/brand-hover/brand-light/brand-soft）全部改为黑灰阶**，杜绝蓝黑混杂（Momus S1）
- **`--color-brand*` token 值直接在 tokens.json 改为 mxou primary 值**，旧蓝色值全部移除

### 2.3 文件复制清单（只读自 mxou → 写入 ozon-worker/webui）
| mxou 路径 | 动作 | 适配点 |
|---|---|---|
| `src/styles/theme.css` | 复制 → `src/styles/theme.css` | 剥离版权头；保留全部 oklch 变量 + `.dark` |
| `src/styles/theme-presets.css` | 复制（暗色/主题预设） | 剥离版权头 |
| `src/components/ui/*`（61） | 复制 → `src/components/ui/` | 剥离版权头；`@theme inline` 语义类已映射无需改色值；Base UI 依赖装齐 |
| `src/components/layout/*` | 复制结构 → 重写 | sidebar 导航改我们的菜单；保留 header 用户区/drawer |
| `src/lib/api.ts` | **参照重写**（不复制文件） | axios 实例 + Bearer 拦截器 + 401 清 token + GET 去重 + sonner toast；调我们 `/api/v1` |
| `src/lib/format.ts` | 参照重写 | fmtTime/fmtMoney 保留我们现有逻辑 |
| `src/lib/errors.ts` | 参照重写 | extractError（FastAPI `detail` 提取） |
| `src/lib/utils.ts` | 复制 | cn()（tailwind-merge 版） |

### 2.4 品牌资产（复制清单）
> 用户要求「品牌元素这些也可以过来」——logo/图标/视觉资产照搬，但**品牌名/文案适配成我们的**（不出现 "New API"/"AionUi" 字样）。

| mxou 资产 | 动作 | 适配 |
|---|---|---|
| `public/logo.png` | 复制 → `webui/public/logo.png` | 保留图形，去掉 New API 字样（如含文字则替换） |
| `public/favicon.ico` | 复制 → `webui/public/favicon.ico` | index.html 换引用 |
| `src/assets/logo.tsx` | 复制 → `webui/src/assets/logo.tsx` | 剥离版权头；`<title>New API</title>` → 我们标题 |
| `src/assets/brand-icons/*`（25 个社交/服务图标） | 复制 → `webui/src/assets/brand-icons/` | 品牌图标同套（微信/telegram/discord 等，未来集成通知/客服用） |
| `src/assets/custom/*`（sidebar/theme 图标） | 复制 | 布局组件用，直接搬 |
| `src/assets/clerk-logo.tsx` / `clerk-full-logo.tsx` | **不复制** | Clerk 是 mxou 的登录服务商，与我们无关 |
| `public/channel-logos/`、`public/landing/`、pay-*.png | **不复制** | Go 通道/落地页/支付专属 |
| `waffo-logo-*.svg` | **不复制** | 第三方服务 logo |

### 2.5 依赖新增（webui/package.json，版本对齐 mxou）
```
核心（§2.1 决策）：
  react@19.2.6 react-dom@19.2.6 @types/react@19 @types/react-dom@19
  tailwindcss@4.3 @tailwindcss/vite @base-ui/react@1.5.0
  class-variance-authority clsx tailwind-merge lucide-react sonner@2.0.7
61 组件传递依赖（实测 import 清单，全量装齐）：
  @hugeicons/react @hugeicons/core-free-icons   # 40 处 import，图标库
  recharts react-hook-form react-day-picker@10 cmdk embla-carousel-react
  input-otp react-markdown remark-gfm rehype-raw react-resizable-panels
  vaul next-themes react-i18next i18next i18next-browser-languagedetector
  date-fns dayjs motion qrcode.react react-icons nanoid
复制清单补充（components/ui 之外的内部依赖）：
  src/context/theme-provider.tsx   # 暗色模式 Provider（ui 组件依赖）
  src/hooks/use-mobile.ts          # 响应式 hook（sidebar 依赖）
  src/hooks/*                       # 其他 ui 组件引用的 hooks
```
- **React 升级连锁**：现有 12 页 + ImageStudioEmbed 970 行需过 tsc + build + 手测（React 18→19 基本兼容，风险集中在 `useEffect` 时序/`act()` 相关，逐页迁移时验证）
- **不装**：@tanstack/react-query/router/table、zustand、vchart、@visactor/*、shiki、streamdown、tokenlens、@lobehub/icons（mxou 用但我们业务用不到的重依赖）

### 2.6 完整仓库结构规划（ozon-worker/webui/ 目标态）
```
webui/
├── index.html                 # 品牌标题 + favicon 引用（更新）
├── vite.config.ts             # + tailwindcss() 插件
├── components.json            # 【新增】shadcn 配置（style=base-nova，对齐 mxou）
├── package.json               # 依赖新增（§2.5）
├── scripts/
│   └── sync-tokens.mjs        # 保留（tokens 单一真相源）
├── public/
│   ├── favicon.ico            # 【新增】mxou 复制（品牌同套）
│   ├── logo.png               # 【新增】mxou 复制
│   └── favicon.svg            # 保留（或删除，favicon.ico 接管）
└── src/
    ├── app.css                # Tailwind 4 @theme inline 桥接（tokens → 语义类）
    ├── index.css              # 旧页面样式（冻结，不再追加页面样式）
    ├── main.tsx               # + app.css 引入
    ├── styles/                # 【新增】theme.css / theme-presets.css（mxou oklch 色板）
    ├── api/client.ts          # 重写：axios 实例 + 拦截器（保留现有端点封装）
    ├── lib/                   # 【新增】format / errors / status-meta / utils(cn) / constants
    ├── assets/                # 【新增】logo.tsx + brand-icons/ + custom/（mxou 品牌资产）
    ├── components/
    │   ├── ui/                # mxou 61 组件（token 化）
    │   └── layout/            # mxou 布局结构重写（app-sidebar/app-header/mobile-drawer）
    ├── features/              # 【新增】业务域（逐步迁移）
    │   ├── admin/             # 独立路由组 + requireRole 守卫
    │   └── products/ orders/ stores/ templates/ collect-box/ ...（按页面迁移）
    ├── pages/                 # 迁移期保留，逐步迁入 features/
    ├── stores/                # 保留（auth/session）
    ├── tokens/tokens.json     # 品牌色改 mxou primary 值
    └── App.tsx                # 路由：/admin/* 独立组
```

> 仓库结构对齐逻辑：**顶层沿用 mxou default 的 `src/styles` + `src/components/ui` + `src/features` + `src/lib` 组织**（这四层是架构同源的核心）；保留我们的 `tokens/`（单一真相源）和 `scripts/`（token 同步）。`components.json` 对齐 shadcn 配置便于未来 `shadcn add`。

## 三、分阶段执行计划

### Step 1：主题 + 品牌资产 + 组件地基（1 天）——纯视觉，零业务风险
- [x] S1.1 复制 `theme.css`/`theme-presets.css` → `src/styles/`（剥离版权头）
- [x] S1.2 tokens.json 品牌色改 mxou primary（近黑），重跑 `tokens:sync` + `tokens:validate`
- [x] S1.3 品牌资产：logo.png/favicon.ico → public/；logo.tsx + brand-icons/ + custom/ → src/assets/（去 New API 字样）
- [x] S1.4 装依赖（tailwindcss/@tailwindcss/vite/@base-ui/react/cva/clsx/tailwind-merge/lucide-react/sonner）
- [x] S1.5 `src/app.css`：`@import tailwindcss/theme.css + utilities.css`（跳过 preflight）+ `@theme inline` 映射全部语义色
- [x] S1.6 `vite.config.ts` 加 `tailwindcss()` 插件；`main.tsx` 引入 app.css；index.html 换 favicon/标题
- [x] S1.7 复制 `components/ui/` 61 个 → 剥离版权头 + 装 Base UI 依赖；`components.json` 落盘
- [x] S1.8 复制 `lib/utils.ts`(cn)
- **验收**：`npm run build` 绿；现有 12 页面布局不破（仅品牌色变化）；favicon/logo 生效；新建临时组件 `bg-primary text-background` 渲染为近黑

### Step 2：数据层 + 布局（1 天）
> **执行备注（2026-08-17 实测）**：S2.1 `lib/api.ts` 保留 mxou 原版（业务独立走 `src/api/client.ts`），
> 未按原计划重写为 worker 拦截器——双 API 通道是最终形态（见 WEBUI-CONVENTIONS §6）。
> S2.2 工具函数抽取未做（业务页仍用本地实现，功能正常，归入 Tailwind 渐进迁移）。

- [~] S2.1 已决策不执行：保留 mxou 原版 lib/api.ts，业务独立走 src/api/client.ts（双 API 通道是最终形态，见执行备注）
- [x] S2.2 工具函数抽取（webui/src/lib/business/，10 页去重，commit 0241ccf）
- [x] S2.3 布局：复制 mxou layout 结构重写（app-sidebar 我们菜单 + app-header 用户区 + KeyManager 保留）
- **验收**：`npm run build` 绿；三处重复工具函数清零；导航结构照 mxou 骨架

### Step 3：页面迁移（14 页，约 8 天；按优先级排）——功能保真
- [x] S3.1 Login（mxou auth 页模式；登录链路走 worker `/api/v1/mxou/login` 代理，响应带 role）
- [x] S3.2 Home / Tasks / CollectBox / Products（迁移样板：新组件 + lib/）
- [x] S3.3 Orders / OnSale / Stores / Templates / Bestsellers / PricingTool / DataScreen（标准页 0.5 天/页）
- [x] S3.4 ImageStudio（**970 行 embed 大组件，1.5-2 天**）+ Admin 拆入 features/admin（Step 4）
- 每个页面：tokens 语义类替换硬编码 hex → 新组件 → 迁移完成手测（React 19 兼容性在此验证）
- **验收**：每页 build + 手测核心交互不回归；`tokens:validate` 硬编码 hex = 0

### Step 4：管理员独立化（0.5 天）——安全修复
- [x] S4.1 **worker `mxou/login` 响应 + 登录后用户信息接口补 `role` 字段**（查 `users.role`，已有 `require_admin` 逻辑复用）；登录响应/session store 增加 role
- [x] S4.2 `features/admin/` 独立路由组 `/admin/*`
- [x] S4.3 前端路由守卫：session.role !== 'admin' → 跳 403/登录页（展示层；worker `require_admin` 服务端守卫保持最终防线）
- [x] S4.4 Admin 页面组件用新体系重写
- **验收**：普通用户访问 `/admin/*` 被路由层拦截；admin 正常进入；worker 侧 403 仍生效

### Step 5：收尾（0.5 天）
- [x] S5.1 `docs/WEBUI-CONVENTIONS.md`（组件书写规则 + 禁硬编码 hex + import 顺序 + 出处备注「源自 new-api default，AGPL-3.0」）
- [x] S5.2 全量回归：`npm run build` + `tokens:validate` + worker 全量 pytest（webui 改动不碰 worker，应全绿）+ 手测关键页
- [x] S5.3 版本 v0.54.0 四源 + CHANGELOG + 提交（commit df891fe）

## 四、风险与应对
| 风险 | 应对 |
|---|---|
| 旧页面样式被 Tailwind 重置 | 已规避：app.css 只引 theme+utilities，不引 preflight |
| 61 组件依赖缺失/版本不匹配 | 复制时逐个 import 检查，缺依赖按 mxou package.json 版本装 |
| 品牌色突变（蓝色→近黑） | 用户明确要求视觉一致；Step 1 一步到位，避免蓝黑混杂期 |
| 品牌资产含 New API 字样 | logo.tsx title / 页面文案统一替换为我们品牌名 |
| 版权（AGPL） | 用户拍板忽略；CONVENTIONS.md 留出处备注 |
| 页面迁移破坏功能 | 每页独立 commit + build + 手测；失败回滚该页 |

## 五、测试
- `npm run build` + `npm run tokens:validate`（每次改动后）
- 手测：Login / 列表页 / 弹窗交互 / 管理员入口 / favicon 与 logo 展示
- worker pytest 全量（webui 不碰 worker，预期 1094 passed 不变）

## 六、DoD
1. `npm run build` exit 0；`tokens:validate` 硬编码 hex = 0
2. 视觉与 api.mxou.cn 一致（近黑 primary、纯白背景、同字体圆角、同 favicon/logo）
3. 61 组件全量就位（含全部传递依赖）+ `lib/` 四件套（api/format/errors/status-meta）就位；三处重复工具函数清零
4. React 19 升级完成，现有 12 页 + ImageStudio 无回归
5. 品牌资产就位（favicon/logo/brand-icons），无 "New API"/"AionUi" 字样残留
6. 仓库结构对齐 mxou 四层（styles/components/ui/features/lib）+ 保留 tokens/scripts
7. 14 个页面全部迁移新体系（或明确标记遗留页）
8. `/admin/*` 独立路由组 + 前端路由守卫（role 来自登录响应）+ worker `require_admin` 保持
9. `docs/WEBUI-CONVENTIONS.md` 落盘
10. 版本 v0.54.0 + CHANGELOG + 提交；worker pytest 与当前基线一致（执行前先跑确认基线，不硬编码数字）

## 七、实施顺序（T 里程碑）
- T1 主题+品牌+组件地基（Step 1）→ 验收「布局不破 + 品牌色一致」；**React 19 升级在此步完成**
- T2 数据层+布局（Step 2）→ 验收「重复清零 + 骨架同源」
- T3 页面迁移（Step 3，按页分批 commit；ImageStudio 970 行单独 1.5-2 天）
- T4 管理员独立化（Step 4：role 字段 + 路由守卫）
- T5 收尾（Step 5：CONVENTIONS + 回归 + 版本）
