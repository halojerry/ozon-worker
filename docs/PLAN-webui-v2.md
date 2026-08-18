# PLAN-webui-v2: WebUI 架构升级计划（对齐 new-api default 主题）

> 状态: **待评审** | 提出日期: 2026-08-16 | 提出: Claudian | 工时估算: **5.5~7.5 人日**
> 前置文档: `docs/PLAN-webui-v1.md`（v1 架构）、`webui/README.md`、`worker/src/main.py`（`_mount_webui_static`）

---

## 1. 背景与目标

### 1.1 背景

webui（React SPA，托管于 worker 域名 `/app`）自 v1 脚手架以来已长到 12 个页面、29 个源文件，但架构层没有同步升级，出现三类技术债：

| 债 | 证据 |
|----|------|
| **数据获取样板重复** | 12 个页面全部手写 `useEffect + useState` 取数；`fmtTime`/`extractError`/状态元数据映射在 Home / Tasks / CollectBox 中重复实现（代码注释自认「同款本地实现」） |
| **组件层缺失** | `components/ui/` 仅 5 个组件（Button/Badge/Skeleton/EmptyState），页面巨型化：CollectBox 426 行、OnSale 405 行、Products 446 行、Orders 355 行 |
| **类型手写** | `api/client.ts` 全部手写类型；T15（openapi-typescript 生成）已规划未落地，端点每增一个债涨一分 |

### 1.2 目标

1. **开发提速**：组件地基补齐后，新页面/新功能开发速度提升 3~5 倍
2. **债不累积**：数据层、类型层一次性做对，后续每加端点/页面不再产生重复代码
3. **长期可维护**：目录组织、命名、组件书写规则固定下来，后续开发（含 AI 辅助）有章可循
4. **UI 品质对齐**：参照参考项目的 default 主题视觉（组件、布局骨架），但不照搬业务

### 1.3 设计原则

- **渐进迁移，不推倒重来**：现有页面零破坏（Tailwind 接入时跳过 preflight），新代码用新体系，旧页面按阶段逐步重构
- **保留既有资产**：`tokens.json` 设计 token 体系（W3C 规范 + sync/validate 脚本）、`stores/` 手写 store、react-router 路由——都继续用
- **学「骨架」不学「规模」**：参考项目是 939 文件的多租户 SaaS 平台，我们只吸收组件模式/数据层/目录组织，不引入其双主题、文件路由、重型构建链

---

## 2. 现状盘点

| 层 | 现状 | 文件证据 |
|----|------|---------|
| 设计 token | ✅ 已成型：W3C Design Tokens 规范，`tokens.json` 单一真相源 + `tokens:sync/validate` 脚本 | `src/index.css`（3403 行，`:root` 变量）+ `src/tokens/tokens.json` |
| UI 组件 | ⚠️ 仅 5 个；Button 已确立「token 驱动 + 渐进迁移」路线 | `src/components/ui/Button.tsx` |
| 布局 | ✅ 可用：工作流排序侧边栏 + Outlet + KeyManager | `src/components/Layout.tsx`（263 行） |
| 数据获取 | ❌ 每页手写 useEffect；工具函数多页重复 | `src/pages/*.tsx` |
| 类型 | ❌ 手写，T15 未落地 | `src/api/client.ts` 注释 |
| 路由 | ✅ react-router-dom 6，够用 | `src/App.tsx` |
| 状态 | ✅ useSyncExternalStore 手写 store，够用 | `src/stores/{auth,session}.ts` |
| 部署 | ✅ FastAPI 挂 `/app/` SPA fallback 已实现 | `worker/src/main.py` `_mount_webui_static` |

**主要矛盾**：组件层缺失 vs 12 个页面持续膨胀 → **突破口：组件地基先行（阶段 1），数据层紧随（阶段 2），布局对齐最后（阶段 3）**。组件一上，所有页面开发立刻受益；类型生成越早，债越少。

---

## 3. 目标架构

```
webui/src/
├── app.css                    # 【新增】Tailwind 4 + @theme inline token 桥接
├── api/
│   ├── client.ts              # axios 实例 + 拦截器（401 跳转 / 错误 toast / GET 去重）
│   └── generated.d.ts         # 【新增】openapi-typescript 生成，禁止手改
├── lib/                       # 【新增】共享工具：format.ts / errors.ts / status-meta.ts / utils.ts(cn)
├── components/
│   ├── ui/                    # 【扩充】shadcn 风格组件（token 驱动，约 18+ 个）
│   └── layout/                # Layout 壳（header 用户区、移动端 drawer 后续补）
├── features/                  # 【新增】按业务域拆分（阶段 4 启动，如 features/products/）
├── pages/                     # 页面（迁移期保留，巨型页面逐步拆出）
├── stores/                    # 保留现有实现（auth / session）
├── tokens/                    # tokens.json（单一真相源，不动）
├── index.css                  # 保留（旧页面样式；新代码不再往这里加页面样式）
└── App.tsx / main.tsx         # 路由不变
```

依赖新增（全部为社区标准，MIT 许可）：
`tailwindcss@4`、`@tailwindcss/vite`、`@base-ui/react`、`class-variance-authority`、`clsx`、`tailwind-merge`、`lucide-react`（+ `@hugeicons/react` 若抄 default 布局组件）、`@tanstack/react-query`、`sonner`、`openapi-typescript`(dev)

---

## 4. 技术选型决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 样式方案 | **Tailwind CSS v4**（CSS-first） | ① default 主题 61 个组件全部基于它，选它才能「抄代码」而非「学思想」；② Tailwind 4 的 `@theme inline` 可直接映射现有 CSS 变量 → tokens.json 体系保留；③ 2026 社区标准，AI 生成/招聘/生态都顺 |
| 组件基础 | **shadcn/ui 风格**（Base UI 系，对齐 default 的 components.json: `style: base-nova`） | default 即用此配置管理组件；`shadcn add` 可批量生成 |
| 组件来源 | **shadcn 官方 registry 生成（MIT）+ default 源码作参照** | 版权合规红线，见 §8 |
| 数据获取 | **TanStack Query v5** | 轮询（任务进度页刚需）/ 缓存 / 重试 / loading·error 收敛 |
| API 层 | axios 单实例 + 拦截器（照 default `lib/api.ts` 模式重写） | 统一错误处理 + 401 跳转 + GET 去重 |
| 类型 | openapi-typescript 从 FastAPI openapi.json 生成 | FastAPI 原生支持，比 Go 项目更顺；单一真相源 |
| 客户端状态 | 保留现有 useSyncExternalStore store | 已够用，不引入 Zustand |
| 路由 | 保留 react-router-dom 6 | 迁移 TanStack Router 不值得 |
| 图表/表单/i18n | **暂不引入** | 需求未出现；i18n 仅当确定出俄语版时再补（届时学 default 的 i18next 模式） |
| 构建链 | 保留 Vite + tsc | Rsbuild/tsgo/oxlint/knip 是 939 文件项目才需要的 |

---

## 5. 参考项目与资源

| 资源 | 地址 | 用途 |
|------|------|------|
| 参考项目（本地） | `/Volumes/os/dev/ponding-api/source-new-api/web/` | 主参考源（同事同机可直接读） |
| 参考项目（远端） | `https://github.com/halojerry/new-api-private.git` | 同事异地 clone 用（私有仓库，需授权） |
| 上游（公开只读） | `https://github.com/QuantumNous/new-api` | default 主题与上游同步，可公开对照 |
| **组件参照源码** | `web/default/src/components/ui/`（61 个） | 组件实现参照（§7 SOP） |
| **布局参照源码** | `web/default/src/components/layout/` | 学结构：authenticated-layout / app-sidebar / app-header / mobile-drawer / section-page-layout |
| **API 层范例** | `web/default/src/lib/api.ts` | 拦截器 + 请求去重模式（§6.2.1 参照重写） |
| 工具函数参照 | `web/default/src/lib/{format,constants,handle-server-error}.ts` | lib/ 抽取参照 |
| 前端规范参照 | `web/default/AGENTS.md` | 提炼本项目规范（阶段 0） |
| 组件生成器 | `https://ui.shadcn.com`（CLI: `npx shadcn@latest`） | MIT 模板组件生成（安全来源） |
| 类型生成器 | `https://github.com/openapi-ts/openapi-typescript` | T15 落地工具 |

> 阅读顺序建议：`web/default/AGENTS.md`（全貌）→ `components.json`（shadcn 配置）→ `lib/api.ts`（数据层核心）→ `components/ui/button.tsx`（组件范式）→ `components/layout/authenticated-layout.tsx`（布局范式）→ 按需翻 features/。

---

## 6. 分阶段执行计划

### 阶段 0：准备（0.5 天）

| # | 任务 | 产出 |
|---|------|------|
| 0.1 | 通读参考项目 `web/default/AGENTS.md` + `components.json`，理解其组件/样式/文件组织规范 | 笔记 |
| 0.2 | 输出本项目前端规范 `docs/WEBUI-CONVENTIONS.md`（从 AGENTS.md 提炼与本项目相关条目：组件书写规则、token 使用、命名、import 顺序、禁用手写 hex） | `docs/WEBUI-CONVENTIONS.md` |
| 0.3 | 确认同事开发环境：node ≥ 20、`webui/` 可 `npm run dev` 起本地 worker（`VITE_API_PROXY_TARGET`） | 环境就绪 |

### 阶段 1：组件地基（2~3 天）——解决「开发慢」

#### 1.1 接入 Tailwind 4（半天）

```bash
cd webui
npm i -D tailwindcss @tailwindcss/vite
```

- `vite.config.ts` plugins 加 `tailwindcss()`
- 新建 `src/app.css`（**关键文件**，token 桥接层）：

```css
/* 只引入 theme + utilities，跳过 preflight → 现有 3403 行 index.css 零影响 */
@import "tailwindcss/theme.css";
@import "tailwindcss/utilities.css";

/* 语义色映射：shadcn 组件类名 → 现有 token 变量（tokens.json 仍是单一真相源） */
@theme inline {
  --color-background: var(--color-bg);
  --color-foreground: var(--color-text);
  --color-primary: var(--color-brand);
  --color-primary-hover: var(--color-brand-hover);
  --color-primary-light: var(--color-brand-light);
  --color-sidebar: var(--color-sidebar);
  --color-sidebar-hover: var(--color-sidebar-hover);
  --color-muted: var(--color-text-muted);
  --color-muted-foreground: var(--color-text-secondary);
  --color-border: var(--color-border);
  --color-surface: var(--color-surface);
  --color-danger: var(--color-danger);
  --color-danger-light: var(--color-danger-light);
  --color-success: var(--color-success);
  --color-warning: var(--color-warning);
  --radius-lg: var(--radius-lg);
  --radius-md: var(--radius-md);
  --radius-sm: var(--radius-sm);
  --shadow-sm: var(--shadow-sm);
  --shadow-md: var(--shadow-md);
  --shadow-lg: var(--shadow-lg);
  --font-sans: var(--font-sans);
  --font-mono: var(--font-mono);
}
```

- `src/main.tsx` 引入 `app.css`（在 index.css 之后）
- **验收**：`npm run dev` 现有页面无任何视觉变化；新建一个临时组件用 `bg-primary text-background` 类验证渲染为品牌蓝 `#005bff`

#### 1.2 shadcn 初始化（0.5 天）

```bash
npx shadcn@latest init
# 选项对齐 default 的 components.json：style=base-nova / css=src/app.css / cssVariables=true / alias: components=@/components, ui=@/components/ui, lib=@/lib
```

- 若 CLI 不提供 base-nova，选 nearest 风格后手动把 `components.json` 的 style 改为 `base-nova`（与 default 一致，保证后续 `shadcn add` 产物同源）
- 安装 base 依赖：`@base-ui/react`（或 Radix，跟随 init 结果）、`class-variance-authority`、`clsx`、`tailwind-merge`、`lucide-react`

#### 1.3 首批 18 个组件（1~1.5 天）

按 ozon-worker 实际页面需求挑选（高频、跨页面复用）：

| 类别 | 组件 |
|------|------|
| 基础 | `button` `input` `textarea` `label` `badge` `skeleton` |
| 选择 | `select` `checkbox` `switch` `radio-group` |
| 反馈 | `dialog` `drawer` `sonner`(toast) `progress` |
| 展示 | `table` `tabs` `dropdown-menu` `empty` `titled-card` |

方法（SOP 见 §7）：
1. `npx shadcn@latest add <name>` 官方生成（MIT，安全）
2. 与 default `components/ui/<name>.tsx` 对照，吸收其改进（如 size 变体、状态样式）
3. 用 `app.css` 的语义色类（`bg-primary` 等），**禁止在组件里写死色值**

**验收**：挑 CollectBox 中一块视图（如表头操作区 + 状态列）用新组件重构：Table + Badge + Dialog，不再手写弹窗样板。

#### 1.4 落地 T15：openapi-typescript（0.5 天）

```bash
# worker 起在 8080 时：
npx openapi-typescript http://localhost:8080/api/v1/openapi.json -o src/api/generated.d.ts
```

- `package.json` 加 script：`"types:gen": "openapi-typescript http://localhost:8080/api/v1/openapi.json -o src/api/generated.d.ts"`
- `client.ts` 删除手写类型区，改为 `import type { paths, components } from './generated'`；保留文件结构（api 实例 + 拦截器 + 薄封装），只替换类型引用
- 校验：现有页面 `tsc -b` 通过；端点类型变更时编译期报错

### 阶段 2：数据层（2 天）——解决「useEffect 地狱」

#### 2.1 API 客户端拦截器（0.5 天）

参照 `web/default/src/lib/api.ts` **重写**（不复制文件）：
- 响应拦截器：统一 `{detail}` / 业务错误 → `sonner` toast
- 401 → 清 token（`stores/auth.ts` 的 `clearToken`）→ 跳 `/login`
- GET 请求去重（inFlight 并发合并，~20 行）
- 保持 `TOKEN_STORAGE_KEY`、`baseURL=/api/v1` 不变，现有页面不受影响

#### 2.2 TanStack Query 样板页（1 天）

```bash
npm i @tanstack/react-query
```

- `main.tsx` 包 `QueryClientProvider`
- **只重构 Tasks 页做样板**：
  - 列表/详情用 `useQuery` + `refetchInterval: 3000`（任务状态轮询，替代手写 `setInterval`）
  - 提交动作用 `useMutation` + `onSuccess: invalidateQueries`
- 产出「页面接入 Query 的规范写法」，写进 `docs/WEBUI-CONVENTIONS.md`

#### 2.3 抽取共享 lib（0.5 天）

新建并迁移（删除各页重复实现）：
- `src/lib/format.ts`：`fmtTime`、金额/尺寸格式化
- `src/lib/errors.ts`：`extractError`（统一 FastAPI `detail` 提取）
- `src/lib/status-meta.ts`：任务/草稿状态 → label + className 映射（Home/Tasks/CollectBox 三处合一的表格）
- `src/lib/utils.ts`：`cn()`（tailwind-merge 版，shadcn 标配）

**验收**：Home / Tasks / CollectBox 不再有重复工具函数；Tasks 页轮询由 Query 驱动。

### 阶段 3：布局对齐（1~2 天）

对照 `web/default/src/components/layout/`，学结构不抄业务：

| 学什么 | default 参照 | 落地到 ozon |
|--------|-------------|------------|
| header 用户区 | `app-header.tsx` + avatar + dropdown-menu | Layout.tsx 顶部：KeyManager 保留 + 用户菜单（登出） |
| 移动端适配 | `mobile-drawer.tsx` | 侧边导航 <768px 收进 drawer（当前无响应式） |
| 页面统一间距 | `section-page-layout.tsx` | 页面内容区统一 padding + 页头（标题 + 操作按钮区） |
| 导航视觉 | `app-sidebar.tsx` / `nav-group.tsx` | **保留现有工作流排序 + 条件显示逻辑**，仅对齐选中态/图标/间距视觉 |

**验收**：手机宽度（375px）下导航可折叠可用；桌面观感与 default 一致（同一套 token 天然保证）。

### 阶段 4：按 feature 拆分（持续，每次 0.5~1 天/页面）

**触发条件**（满足其一即拆）：
- 页面文件 >400 行
- 同一页面出现第二个弹窗/第二个数据实体

**模式**（照 default `features/channels/`）：
```
features/products/
├── api.ts          # 该域接口调用（基于 generated.d.ts 类型）
├── components/     # 域内组件（表单、图片上传等）
├── hooks.ts        # useProducts 等 Query hooks
├── constants.ts    # 状态映射等
└── index.tsx       # 页面入口（App.tsx 引用路径变更）
```

顺序建议：Products(446) → CollectBox(426) → OnSale(405) → Orders(355)，一次一个。

---

## 7. 抄组件 SOP（方式方法）

每次要一个新组件的标准流程：

```
① 判断优先级：这个组件有几个页面会用？
   ≥2 个页面 → 进 components/ui/；仅 1 个页面 → 先放 features/xxx/components/ 域内
② shadcn 官方生成：npx shadcn@latest add <name>     ← 唯一「复制」来源（MIT 安全）
③ 对照 default 源码改进：读 web/default/src/components/ui/<name>.tsx，
   吸收其变体设计/交互细节，手动改自己的副本
④ token 化检查：grep 组件内是否出现 #hex / rgb( 硬编码 → 一律换成语义色类
⑤ 验证渲染：dev 环境肉眼核对颜色/圆角/间距符合 tokens.json 品牌值
⑥ 记入 docs/WEBUI-CONVENTIONS.md 组件清单（名称/用途/变体）
```

### 依赖搬运清单（抄到哪个组件才装哪个依赖）

| default 组件依赖 | 用途 |
|------------------|------|
| `@base-ui/react` | 无头组件（dialog/drawer/select/dropdown 等交互底座） |
| `class-variance-authority` + `tailwind-merge` + `clsx` | 变体管理（cva）+ 类名合并（cn） |
| `lucide-react` / `@hugeicons/react` | 图标（default 两库都用，跟随组件实际 import） |
| `sonner` | toast |
| `@tanstack/react-query` | 数据获取（阶段 2） |
| `tw-animate-css` | 弹层动画（default 在用，可装） |

### 剥离 i18n（default 组件若有 `t()` 调用）

```ts
// default: const { t } = useTranslation(); t('...')
// 本项目: 直接写中文字面量（阶段 1-3 不引 i18n）
```

---

## 8. 版权与合规（红线，必须执行）

- 参考项目（new-api / QuantumNous）为 **AGPL-3.0**；`ozon-worker` 是商业项目（上品帮），**禁止逐字复制带版权头的源文件**（如 `lib/api.ts` 含 QuantumNous 版权头）——AGPL 传染会使商业闭源分发不合规
- **安全路径**：组件一律以 shadcn 官方 registry（MIT 模板）为基底；default 源码只作「参照实现」——读逻辑、理解设计、自己重写，允许结构/命名相似但不整体搬运
- 通用模式（axios 拦截器、请求去重、cva 变体写法）不属于版权表达，可放心参照重写
- 若确需复用某段 default 代码（如复杂组件），保留版权头并评估 AGPL 义务，先与项目负责人确认

---

## 9. 技术债红线

**必须做对（欠了要加倍还）**：
1. 类型生成（T15）——端点只增不减，手写类型是持续税
2. 拦截器——每页手写 `extractError` 是纯重复
3. 组件只用语义 token 类名，禁止页面/组件写死 hex 色值
4. 新代码一律走 `client.ts`，禁止页面裸 `fetch`/新建 axios 实例

**允许先欠（做早了是浪费）**：
- 不学：双主题、TanStack Router 文件路由、i18n、zod + react-hook-form、VChart、Rsbuild/tsgo/oxlint/knip 构建链
- 巨型页面先不拆，阶段 1-2 落地后自然重构

---

## 10. 风险与应对

| 风险 | 应对 |
|------|------|
| Tailwind preflight 重置现有页面样式 | 已规避：`app.css` 只引 `theme.css + utilities.css`，不引 preflight；旧页面零影响 |
| 旧 CSS 类名与 Tailwind 类名冲突（如 `.flex`） | 上述方案已最小化；若仍有冲突，`components.json` 设 `prefix` 或改旧类名，逐案处理 |
| 双套样式并存期维护成本 | 新代码只用 Tailwind 类；`index.css` 冻结（不再追加页面样式），逐步迁移 |
| default 组件依赖 i18n/未装依赖 | 按 §7 SOP 剥离 i18n；按依赖搬运清单装包 |
| Base UI / shadcn 版本与 default 不一致 | 对齐 default `package.json` 的版本（bun catalog 区可查），复制时以实际 import 为准 |
| AGPL 版权风险 | §8 红线，组件以 shadcn 官方为基底，default 仅参照 |
| 页面迁移破坏现有功能 | 每阶段验收带回归：`npm run build` + 关键页面手测；迁移一次只动一个页面 |

---

## 11. 完成定义（DoD）

- [ ] `app.css` token 桥接生效：组件渲染全部消费 tokens.json 变量，全库无新增硬编码色值
- [ ] `generated.d.ts` 接入 CI 或 npm script，`client.ts` 零手写类型
- [ ] 拦截器就位：错误 toast / 401 跳转 / GET 去重全站生效
- [ ] Tasks 页 Query 化样板完成，规范写入 `docs/WEBUI-CONVENTIONS.md`
- [ ] 新增组件 ≥18 个，CollectBox 至少一块视图完成重构示范
- [ ] 移动端导航可用（drawer），桌面视觉与参考项目一致
- [ ] 至少一个巨型页面（Products）完成 feature 拆分
- [ ] 所有验收项全绿，无回归

---

## 附：参考阅读清单（按需）

| 主题 | 文件 |
|------|------|
| 全貌 | `web/default/AGENTS.md` |
| 数据层核心 | `web/default/src/lib/api.ts`、`web/default/src/features/channels/api.ts` |
| 组件范式 | `web/default/src/components/ui/button.tsx`、`table.tsx` |
| 布局范式 | `web/default/src/components/layout/authenticated-layout.tsx`、`app-sidebar.tsx` |
| 页面范式 | `web/default/src/features/channels/index.tsx`（列表+筛选+分页） |
| 规范 | `docs/PLAN-webui-v1.md`（本项目 v1 决策，勿冲突） |
