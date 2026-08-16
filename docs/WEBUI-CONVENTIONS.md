# WEBUI-CONVENTIONS — ozon-worker WebUI 开发规范

> 2026-08-17。基于 mxou（new-api default）主题/组件/架构照搬后的维护规范。
> **出处**：`webui/src` 组件/样式/布局源自 [New API](https://github.com/QuantumNous/new-api) default 主题（AGPL-3.0，Copyright QuantumNous），
> 经项目 owner 授权内部复用（ozon-worker 与 api.mxou.cn 同属一个运营方）。若未来对外闭源分发 webui 源码，需重新评估 AGPL 义务。

## 一、技术栈（照搬 mxou 后的现状）

| 类别 | 技术 | 说明 |
|---|---|---|
| 框架 | React 19.2.6 | 从 React 18 升级（61 组件 + Base UI 1.5 按 19 构建） |
| 构建 | Vite 5 + npm/bun | 保留 Vite（非 mxou 的 Rsbuild）；依赖用 bun 装（catalog 兼容） |
| 样式 | Tailwind 4 + `@theme inline` | `src/styles/theme.css`（oklch 色板）+ `src/index.css`（业务页样式） |
| 路由 | TanStack Router（文件路由） | `src/routes/` + `routeTree.gen.ts` 自动生成；`basepath=/app` |
| 组件 | mxou 61 个 shadcn 风格组件 | `src/components/ui/`，Base UI 无头组件 + 语义 token 类 |
| 数据 | axios（`src/lib/api.ts` mxou / `src/api/client.ts` 业务）| 业务走 `/api/v1` Bearer sk-token |
| 状态 | Zustand（auth-store 等） | mxou 生态 |
| 登录 | **mxou 原版**（New API `/api/user/login`）| cookie 会话；业务另需 sk-token（worker） |
| i18n | i18next + react-i18next | mxou 骨架保留；业务文案可先写中文直出 |

## 二、双体系并存（关键！）

```
mxou 体系（框架/登录/组件）          Ozon 业务体系（14 页业务功能）
├─ src/routes/（TanStack 文件路由）  ├─ src/pages/（业务页面，react-router 兼容层）
├─ src/components/ui/（61 组件）     ├─ src/api/client.ts（/api/v1 + Bearer）
├─ src/lib/api.ts（New API）         ├─ src/components/ 业务组件
└─ src/features/auth/（登录）        └─ src/stores/auth.ts + session.ts
```

### 关键规则
1. **业务页面**：`src/pages/*.tsx` 用 `@/lib/router-compat`（react-router 兼容层），
   **禁止**直接 import `react-router-dom`（TanStack Router 不提供其 context，运行时报
   `useNavigate() may be used only in the context of a <Router>`）。
2. **路由注册**：新页面建 `src/routes/_authenticated/<path>/index.tsx`，
   `createFileRoute('/_authenticated/<path>/')`，组件用 `@/pages/...`。
   routeTree.gen.ts 由 router-plugin 生成——**dev 的 HMR 自动生成，但 `vite build` 不生成**，
   build 脚本已前置 `npm run gen:route`（`scripts/gen-route-tree.mjs`）显式生成。
   ⚠️ 新增/修改路由后，若 build 报 `Cannot find module './routeTree.gen'` 或路由错乱
   （子路由渲染成 index），先跑 `npm run gen:route` 确认 routeTree 已更新（见 git 58ec4a9）。
3. **导航**：`src/hooks/use-sidebar-data.ts` 的 `navGroups`（我们的业务菜单）。
4. **管理员路由**：`/_authenticated/admin/` beforeLoad 检查 `user.role >= ROLE.ADMIN(10)`，
   不足 `throw redirect({ to: '/403' })`。服务端 `worker require_admin` 是最终防线。
5. **样式**：mxou 用 Tailwind 语义类（`bg-primary` 等，token 驱动）；
   业务页旧样式在 `src/index.css`（main.tsx 在 mxou 样式后加载覆盖 preflight）。
   新业务代码优先 Tailwind；**禁止硬编码 hex**（`tokens:validate` 检查）。
 6. **API 层**：业务请求一律走 `src/api/client.ts`（baseURL=/api/v1 + Bearer sk-token），
    禁止页面裸 fetch/新建 axios 实例。mxou 请求走 `src/lib/api.ts`（New API cookie）。
    ⚠️ 401 拦截器：业务 token 失效 ≠ 需重登——若 mxou 登录态存在（localStorage user）只清 token
    不跳转，否则跳 sign-in（生产构建曾因此无限重定向，见 git dee53f8）。
 7. **业务共享工具（S2.2 抽取，禁再次内联）**：
    - `src/lib/business/errors.ts` → `extractError`（detail → message → fallback）
    - `src/lib/business/format.ts` → `fmtTime` / `fmtMoney(v, currency?)` / `fmtRate`
    - `src/lib/business/status.ts` → `taskStatusMeta` / `draftStatusMeta`（任务/草稿状态映射）
    - `src/lib/business/components.tsx` → `ImageCell` / `loadEstimate` / `EstimateBadges`
    - 页面专属状态映射（OnSale 产品审核 / Orders 订单）**不抽取**，留在页面局部。
    新增页面遇到同款工具函数必须 import 共享版，禁止复制实现（防行为漂移）。

## 三、依赖管理
- 包管理：bun（`bun add`）。mxou 依赖含 `catalog:` 引用——本项目已把 catalog 值
  写入 package.json 顶层（axios/clsx/dayjs 等），新增 catalog: 依赖时同步补值。
- 重型依赖不引：TanStack Table/Virtual/Chart 按需、vchart/recharts 用到的才装。

## 四、品牌
- index.html title「POUNDING 胖丁」、favicon/logo 来自 mxou（同品牌，天然一致）。
- 登录/注册/用户体系完全用 api.mxou.cn（New API），业务 worker 鉴权用同一 Supabase tokens 表。

## 五、测试与验收
- `bun run build`（tsc -b && vite build）必须 0 错误。
- 业务改动手测：登录 → 各业务页渲染 → 管理员 403/进入。
- `tokens:validate`（webui/scripts/sync-tokens.mjs）硬编码 hex = 0。
