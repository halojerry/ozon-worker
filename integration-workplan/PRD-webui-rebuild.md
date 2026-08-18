# PRD · WebUI 重建方案

> 版本：1.0 · 更新：2026-08-18 · 对应设计规格 v1.3
> 状态：待执行

## 1. 背景与目标

### 1.1 背景

旧 webui 建立在「胖丁基座」（POUNDING Agent 平台）之上——一个完全不同的产品（AI Agent 协作平台）。Ozon ERP 页面是嫁接上去的，存在以下问题：

- 登录页是胖丁的「Welcome back 用户名密码」，不是设计稿的「OzonAI + API Key + 余额」
- 入口是胖丁 LandingPage（Agent 基座营销页），不在设计稿15页里
- 大量死代码、平台包袱、New API 依赖
- 页面视觉从未真正对齐参考图

用户已删除旧 webui，决定从零重建。

### 1.2 目标

从零构建纯 Ozon ERP 管理后台，严格对齐设计稿15页原型图，实现：

- **视觉一致**：每个页面对照 proto PNG 逐像素实现
- **数据驱动**：所有页面接真实 API 数据，无占位
- **图片 URL 化**：数据库只存 URL，前端直接渲染，COS/CDN 服务
- **响应式**：支持4个断点（≥1600/1200-1599/768-1199/<768）
- **无平台包袱**：不引入 POUNDING/New API/landing page/keys/wallet

## 2. 设计资产

### 2.1 设计规格书

**位置**：`design-deliverables/ozon-erp-design-spec.html`

**内容**：
- 设计原则（单色克制/高留白/AI驱动叙事/统一骨架）
- 色彩系统（9个原色 + 语义 token，含实测对比度 WCAG）
- 字体排版（10级字号阶梯，中文 PingFang SC + 等宽数字）
- 间距/圆角/阴影（8pt 基准，6级圆角，4种阴影）
- 动效标准（100ms/240ms/400ms/1600ms，支持 prefers-reduced-motion）
- 组件实样（侧栏/按钮/徽标/输入框/指标卡/表格/空态，可交互演示）
- 用法规范（Do/Don't 硬性检查清单）
- 多端适配（4个断点布局策略）

### 2.2 设计令牌

**位置**：`design-deliverables/design-tokens.json`

**内容**：
```json
{
  "color": {
    "primitive": {
      "base": "#F7F6F2",      // 页面底色（暖白）
      "ink-900": "#111111",    // 侧栏/主文字
      "accent": "#E20E0E",     // 品牌红
      "accent-dark": "#B30C0C", // 深红
      "accent-soft": "#FDEBEB"  // 红浅底
    }
  },
  "typography": {
    "family": {
      "sans": "-apple-system, BlinkMacSystemFont, \"PingFang SC\", ...",
      "mono": "SFMono-Regular, Menlo, Consolas, ..."
    },
    "scale": [
      { "name": "data-lg", "size": 28, "weight": 700, "family": "mono" }
    ]
  },
  "spacing": { "scale": [4, 8, 12, 16, 20, 24, 32, 40, 48, 64] },
  "radius": { "semantic": { "input": 6, "card": 10, "panel": 12 } },
  "shadow": { "card": "0 1px 2px rgba(17,17,17,.04)" }
}
```

### 2.3 原型图

**位置**：`design-deliverables/ozon-*-proto.png`（15张）

| # | 页面 | 文件 | 核心内容 |
|---|---|---|---|
| 1 | 登录页 | ozon-login-proto.png | 左黑品牌面板 + API Key/账号双Tab + 余额 |
| 2 | 仪表盘 | ozon-erp-dashboard-proto.png | KPI卡 + 订单趋势 + 热销商品 |
| 3 | 商品管理 | ozon-products-proto.png | 缩略图/类目/价格/库存/状态表格 |
| 4 | 上架工作台 | ozon-onsale-proto.png | 任务统计 + AI卡片 + AI建议面板 |
| 5 | 订单中心 | ozon-orders-proto.png | 订单统计 + 状态/金额/筛选表格 |
| 6 | 任务中心 | ozon-tasks-proto.png | 任务概览 + 任务列表 + 运行日志 |
| 7 | 智能定价 | ozon-pricing-proto.png | 策略卡 + 调价列表 + 竞品对比曲线 |
| 8 | 图片工坊 | ozon-image-studio-proto.png | 背景替换/去背景/裁剪/AI场景图 |
| 9 | 采集箱 | ozon-collect-box-proto.png | 采集统计 + 商品卡片 + 批量导入 |
| 10 | 店铺管理 | ozon-stores-proto.png | 店铺卡片网格 + 验证/同步/统计 |
| 11 | 热销榜 | ozon-bestsellers-proto.png | 榜单筛选 + 排行列表(前三红高亮) |
| 12 | 数据大屏 | ozon-data-screen-proto.png | 全屏：热力图 + 增长曲线 + 实时订单流 |
| 13 | 上架模板 | ozon-templates-proto.png | 模板卡片 + CRUD + 店铺覆盖 |
| 14 | 系统设置 | ozon-settings-proto.png | 公告/配置/查询词/物流费率 四Tab |
| 15 | 管理员后台 | ozon-admin-proto.png | 概览统计 + 用户/店铺/任务三Tab |

## 3. API 基础

### 3.1 端点覆盖

**位置**：`api-integration/openapi.json`（98路径，173端点）

**类型定义**：`api-integration/generated.d.ts`（7034行 TypeScript 类型）

**验证结论**：15页全部数据需求均被现有端点覆盖，无需新增后端端点。

### 3.2 页面 → API 映射

| # | 页面 | 核心 API 端点 | 数据来源 |
|---|---|---|---|
| 1 | 登录页 | `POST /auth/verify` + `POST /mxou/login` + `GET /mxou/keys` | 认证 |
| 2 | 仪表盘 | `GET /task_statistics` + `GET /stores/{id}/stats` + `GET /analytics/bestsellers` | 任务/店铺/热销 |
| 3 | 商品管理 | `GET /products` + `GET /products/ozon` + `POST /products/bulk-*` | 产品 |
| 4 | 上架工作台 | `GET/POST /drafts` + `POST /drafts/{id}/ai/{field}` + `POST /drafts/{id}/submit` | 草稿/任务 |
| 5 | 订单中心 | `GET /orders` + `POST /orders/{id}/ship` + `POST /orders/{id}/cancel` | 订单 |
| 6 | 任务中心 | `GET /tasks` + `GET /task_status/{id}` + `POST /cancel_task/{id}` | 任务 |
| 7 | 智能定价 | `POST /estimate` + `POST /logistics/quote` + `GET /analytics/bestsellers` | 定价/物流/热销 |
| 8 | 图片工坊 | `GET /tasks/{id}/images` + `POST /tasks/{id}/images/{slot}/regen` | 任务图片 |
| 9 | 采集箱 | `GET /drafts` + `GET /discovery/runs` | 草稿/发现 |
| 10 | 店铺管理 | `GET /credentials` + `GET /stores/{id}/stats` + `POST /stores/{id}/sync` | 凭证/统计 |
| 11 | 热销榜 | `GET /analytics/bestsellers` + `POST /analytics/ozon-bestsellers` | 分析 |
| 12 | 数据大屏 | `GET /task_statistics` + `GET /orders` + `GET /stores/{id}/stats` | 任务/订单/店铺 |
| 13 | 上架模板 | `GET/POST/PATCH/DELETE /templates` | 模板 |
| 14 | 系统设置 | `GET/PUT /admin/config/{name}` + `GET /admin/logistics/rates` | 配置/物流 |
| 15 | 管理员后台 | `GET /admin/overview` + `GET /admin/users` + `GET /admin/tasks` | 管理 |

### 3.3 图片数据流

```
数据来源                    存储位置                    前端渲染
─────────────────────────────────────────────────────────────────
1688 商品图    →  alicdn URL (https://cbu01.alicdn.com/...)  →  DB image 字段  →  <img src={url}>
AI 生图        →  COS URL (https://yss-1256275613.cos...)    →  DB image 字段  →  <img src={url}>
Ozon 竞品图   →  ir.ozone.ru URL                            →  DB image 字段  →  <img src={url}>
```

**关键点**：
- 数据库只存 URL 字符串，不存二进制数据
- 图片由 COS/CDN 服务，浏览器自然缓存
- 前端用 `loading="lazy"` 懒加载，减少初始页面请求量
- 加载失败显示占位图，不阻断页面

**API 响应中的图片字段**：

| 端点 | 响应字段 | 类型 |
|---|---|---|
| `GET /products/ozon` | `OzonProductOut.image` | `string` (URL) |
| `GET /orders` | `OrderProductOut.image` | `string` (URL) |
| `GET /tasks` | `TaskListItem.image` | `string` (URL) |
| `GET /drafts` | `DraftOut.payload.draft.images[]` | `string[]` (URL 数组) |
| `GET /tasks/{id}/images` | `TaskImageItem.url` | `string` (URL) |

## 4. 技术架构

### 4.1 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 构建 | Vite 6 | 快速 HMR，成熟生态 |
| 框架 | React 19 + TypeScript | 与 generated.d.ts 类型系统天然契合 |
| 样式 | Tailwind CSS v4 | 直接消费 design-tokens.json，原子化 |
| 路由 | TanStack Router | 类型安全，文件路由 |
| 数据 | TanStack Query + Axios | 缓存/重试/乐观更新 |
| 状态 | Zustand | 轻量，token/session 管理 |
| 表格 | TanStack Table | 虚拟滚动，排序/筛选 |
| 图表 | VChart (@visactor) | 数据大屏/趋势图 |
| 格式化 | Day.js + Intl.NumberFormat | 日期/金额/百分比 |

### 4.2 目录结构

```
webui/
├── src/
│   ├── main.tsx                 # 入口
│   ├── app.tsx                  # 根布局（ThemeProvider + QueryClient + RouterProvider）
│   ├── routes/
│   │   ├── __root.tsx           # 根路由（认证守卫）
│   │   ├── login.tsx            # 登录页（独立全屏）
│   │   └── _authenticated/
│   │       ├── route.tsx        # 认证布局壳（黑侧栏 + 顶栏）
│   │       ├── index.tsx        # 仪表盘
│   │       ├── products.tsx     # 商品管理
│   │       ├── on-sale.tsx      # 上架工作台
│   │       ├── orders.tsx       # 订单中心
│   │       ├── tasks.tsx        # 任务中心
│   │       ├── pricing.tsx      # 智能定价
│   │       ├── image-studio.tsx # 图片工坊
│   │       ├── collect-box.tsx  # 采集箱
│   │       ├── stores.tsx       # 店铺管理
│   │       ├── bestsellers.tsx  # 热销榜
│   │       ├── data-screen.tsx  # 数据大屏（独立全屏）
│   │       ├── templates.tsx    # 上架模板
│   │       ├── settings.tsx     # 系统设置
│   │       └── admin.tsx        # 管理员后台
│   ├── api/
│   │   ├── client.ts            # Axios 实例 + 拦截器
│   │   ├── generated.d.ts       # OpenAPI 类型（自动）
│   │   └── hooks/               # React Query hooks
│   ├── components/
│   │   ├── layout/              # 侧栏/顶栏/内容区
│   │   ├── ui/                  # 原子组件（Button/Badge/Card/Input/Table/Tabs/Empty/Metric）
│   │   └── shared/              # 业务组件（ImageCell/Price/StatusBadge/Pagination）
│   ├── stores/
│   │   ├── auth.ts              # token 管理
│   │   └── session.ts           # 会话状态
│   ├── lib/
│   │   ├── format.ts            # 金额/日期/百分比格式化
│   │   ├── errors.ts            # 错误提取
│   │   └── constants.ts         # 业务常量
│   └── styles/
│       ├── tokens.css           # design-tokens → CSS 变量
│       └── global.css           # 基础样式
├── index.html
├── vite.config.ts
├── tailwind.config.ts
├── tsconfig.json
└── package.json
```

## 5. 页面规格

### 5.1 登录页（#1）

**设计**：左黑品牌面板（40%）+ 右表单区（60%）

**布局**：
- 左侧：黑色背景 + 品牌 Logo + "Ozon AI 自动化运营 ERP" + 副标题 + 版权
- 右侧：暖白背景 + API Key/账号密码双Tab + Token输入框 + 登录按钮 + 底部余额展示

**API**：
- `POST /auth/verify` — token 验证
- `POST /mxou/login` — 账号密码登录
- `GET /mxou/my-key` — 获取当前 key
- `GET /mxou/keys` — 密钥列表

**认证流程**：
1. 输入 token → `POST /auth/verify` 验证
2. 验证成功 → 存 token 到 localStorage → 路由守卫放行 → 进仪表盘
3. 账号密码 → `POST /mxou/login` → 获取 key → 同上
4. 401 响应 → Axios 拦截器清 token → 重定向登录页

### 5.2 仪表盘（#2）

**设计**：KPI 指标卡 + 订单趋势图 + 热销商品表

**布局**：
- 顶部：4个 KPI 卡（今日订单/AI 上品数/上架成功率/待处理任务）
- 中部：订单趋势折线图（VChart）
- 底部：热销商品表格（缩略图/名称/销量/价格）

**API**：
- `GET /task_statistics` — 任务统计（KPI 数据）
- `GET /stores/{id}/stats` — 店铺统计（今日订单/销售额/利润）
- `GET /analytics/bestsellers` — 热销榜（热销商品）

**数据聚合**：
- 今日订单 = 各店铺 `today_orders` 求和
- AI 上品数 = `task_statistics.completed`
- 上架成功率 = `completed / (completed + failed)`
- 待处理任务 = `task_statistics.pending + running`

### 5.3 商品管理（#3）

**设计**：商品表格 + 批量操作

**布局**：
- 顶部：搜索框 + 筛选（状态/类目）+ 批量操作按钮（改价/改库存/归档）
- 表格：缩略图 + 商品名称 + 类目 + 价格 + 库存 + 状态标签（在售/缺货/待上架）
- 分页

**API**：
- `GET /products` — 系统产品列表
- `GET /products/ozon` — Ozon 在线商品
- `POST /products/bulk-prices` — 批量改价
- `POST /products/bulk-stocks` — 批量改库存
- `POST /products/bulk-archive` — 批量归档

**图片**：`OzonProductOut.image`（主图 URL）

### 5.4 上架工作台（#4）

**设计**：任务统计 + AI 卡片 + AI 建议面板

**布局**：
- 左侧：任务统计卡（总任务/进行中/已完成/失败）+ 草稿列表
- 中部：草稿详情（标题/图片/属性/价格）+ AI 填充按钮
- 右侧：AI 建议面板（标题/卖点/关键词建议）

**API**：
- `GET /drafts` — 草稿列表
- `POST /drafts` — 新建草稿
- `GET /drafts/{id}` — 草稿详情
- `PATCH /drafts/{id}` — 更新草稿
- `POST /drafts/{id}/ai/{field}` — AI 填充字段
- `POST /drafts/{id}/estimate` — 估价
- `POST /drafts/{id}/submit` — 提交上架
- `GET /tasks` — 任务列表

**图片**：`DraftOut.payload.draft.images[]`（1688 商品图 URL 数组）

### 5.5 订单中心（#5）

**设计**：订单统计卡 + 订单表格

**布局**：
- 顶部：订单统计卡（今日订单/待发货/已发货/已取消）
- 表格：订单号 + 商品缩略图 + 商品名称 + 金额 + 状态标签 + 操作（发货/取消）
- 筛选：状态/日期范围
- 分页

**API**：
- `GET /orders` — 订单列表
- `POST /orders/{id}/ship` — 发货
- `POST /orders/{id}/cancel` — 取消
- `GET /orders/{id}/label` — 面单
- `POST /orders/batch/labels` — 批量面单
- `POST /orders/batch/ship` — 批量发货

**图片**：`OrderProductOut.image`（商品主图 URL）

### 5.6 任务中心（#6）

**设计**：任务概览 + 任务列表 + 运行日志

**布局**：
- 顶部：任务概览卡（总任务/进行中/已完成/失败）
- 中部：任务列表（任务ID/状态/创建时间/耗时/操作）
- 底部：最近运行日志（失败红色标记）

**API**：
- `GET /tasks` — 任务列表
- `GET /task_status/{id}` — 任务详情
- `POST /cancel_task/{id}` — 取消任务
- `POST /resubmit_task/{id}` — 重试任务

**图片**：`TaskListItem.image`（产品主图 URL）

### 5.7 智能定价（#7）

**设计**：策略卡 + 调价列表 + 竞品对比曲线

**布局**：
- 左侧：策略卡（加价率/佣金率/汇率缓冲）
- 中部：调价列表（当前价/AI 建议价/利润变化）
- 右侧：竞品对比曲线（VChart 折线图）

**API**：
- `POST /estimate` — 估价（独立）
- `POST /logistics/quote` — 物流报价
- `GET /analytics/bestsellers` — 热销榜（竞品价格）
- `GET /products/ozon` — 在线商品（当前价格）

### 5.8 图片工坊（#8）

**设计**：图片网格 + 重新生图 + 在线商品改图

**布局**：
- 顶部：任务选择（下拉选择任务）
- 中部：图片网格（主图/social_proof/detail/scene 等10个槽位）
- 底部：操作按钮（重新生图/设为主图/更新在线商品）

**API**：
- `GET /tasks/{id}/images` — 任务图片
- `POST /tasks/{id}/images/{slot}/regen` — 重新生图
- `POST /products/{id}/update_images` — 更新在线商品图片

**图片**：`TaskImagesResponse.images[].url`（AI 生成图 URL）

### 5.9 采集箱（#9）

**设计**：采集统计 + 商品卡片 + 批量导入

**布局**：
- 顶部：采集统计卡（总采集/待处理/已上架）
- 中部：商品卡片网格（来源平台/图片/价格/状态）
- 底部：批量导入按钮

**API**：
- `GET /drafts` — 草稿列表（采集来源）
- `GET /discovery/runs` — 选品发现历史
- `POST /discovery/runs` — 上报选品结果

**图片**：`DraftOut.payload.draft.images[]`（1688 商品图 URL 数组）

### 5.10 店铺管理（#10）

**设计**：店铺卡片网格

**布局**：
- 顶部：添加店铺按钮
- 中部：店铺卡片网格（店铺名称/状态/今日订单/销售额/利润）
- 每个卡片：验证/同步/编辑/删除操作

**API**：
- `GET /credentials` — 店铺凭证列表
- `POST /credentials` — 添加店铺
- `PATCH /credentials/{id}` — 更新凭证
- `DELETE /credentials/{id}` — 删除店铺
- `POST /credentials/{id}/validate` — 验证凭证
- `GET /stores/{id}/stats` — 店铺统计
- `POST /stores/{id}/sync` — 手动同步

### 5.11 热销榜（#11）

**设计**：榜单筛选 + 排行列表

**布局**：
- 顶部：筛选（今日/本周/本月）+ 类目筛选
- 中部：排行列表（排名/商品名称/销量/价格/趋势）
- 前三名：红色高亮

**API**：
- `GET /analytics/bestsellers` — Ozon 热销榜
- `POST /analytics/ozon-bestsellers` — 上报 Ozon 热销
- `POST /analytics/market-bestsellers` — 上报跨平台热销

### 5.12 数据大屏（#12）

**设计**：全屏：热力图 + 增长曲线 + 实时订单流

**布局**：
- 全屏（无侧栏），CSS `position: fixed`
- 中央：世界地图热力图（VChart 地图组件）
- 左侧：实时订单流（滚动列表）
- 右侧：店铺/商品排行
- 下方：增长曲线（VChart 折线图）
- 光晕动效：CSS `box-shadow` 脉冲动画（`@keyframes pulse`，1600ms）

**API**：
- `GET /task_statistics` — 任务统计
- `GET /orders` — 订单数据（趋势）
- `GET /stores/{id}/stats` — 店铺统计
- `GET /analytics/bestsellers` — 热销榜
- `GET /admin/overview` — 平台概览

**轮询策略**：`useQuery` 配置 `refetchInterval: 30000`（30秒刷新）

### 5.13 上架模板（#13）

**设计**：模板卡片 + CRUD

**布局**：
- 顶部：新建模板按钮
- 中部：模板卡片（模板名称/加价率/佣金率/汇率缓冲/库存）
- 每个卡片：编辑/删除/设为默认操作
- 底部：店铺差异化覆盖明细

**API**：
- `GET /templates` — 模板列表
- `POST /templates` — 创建模板
- `PATCH /templates/{id}` — 更新模板
- `DELETE /templates/{id}` — 删除模板
- `POST /templates/{id}/default` — 设为默认

### 5.14 系统设置（#14）

**设计**：公告/配置/查询词/物流费率 四Tab

**布局**：
- Tab 1：站点公告（CRUD）
- Tab 2：配置备份（读取/写入/回滚）
- Tab 3：查询词管理（列表/删除/导入）
- Tab 4：物流费率（表格/编辑/CSV 导入）

**API**：
- `GET/POST/PUT/DELETE /admin/site/banners` — 公告 CRUD
- `GET/POST/PUT/DELETE /admin/site/announcements` — 公告 CRUD
- `GET/PUT /admin/config/{name}` — 配置读写
- `GET /admin/config/{name}/backups` — 备份列表
- `POST /admin/config/{name}/rollback` — 回滚
- `GET/DELETE /admin/queries` — 查询词管理
- `POST /admin/queries/import` — 导入查询词
- `GET/PUT /admin/logistics/rates` — 物流费率
- `POST /admin/logistics/rates/import` — 导入费率

### 5.15 管理员后台（#15）

**设计**：概览统计 + 用户/店铺/任务三Tab

**布局**：
- 顶部：概览统计卡（用户数/店铺数/任务总数/今日任务）
- Tab 1：用户列表（用户名/余额/角色/注册时间）
- Tab 2：店铺列表（店铺名称/状态/最后同步）
- Tab 3：任务列表（任务ID/状态/创建时间/耗时）

**API**：
- `GET /admin/overview` — 平台概览
- `GET /admin/users` — 用户列表
- `GET /admin/users/{id}` — 用户详情
- `GET /admin/stores` — 店铺列表
- `GET /admin/tasks` — 任务列表

## 6. 组件规格

### 6.1 原子组件（spec §06）

| 组件 | 规格 | 用途 |
|---|---|---|
| Button | 6种状态：pri/sec/ghost/disable/loading/danger | 主要/次要/幽灵/禁用/加载/危险操作 |
| Badge | 3种：neutral/red/dark | 状态标签（已上架/待上架/平台侧） |
| Card | 圆角10px + 阴影 `0 1px 2px rgba(17,17,17,.04)` | 卡片容器 |
| Input | 4种状态：标准/焦点/错误/禁用 | 表单输入 |
| Table | 表头灰底 `#FAF9F6` + hover + 选中行 `#FDEBEB` | 数据表格 |
| Tabs | 下划线式，选中红色 `#E20E0E` | 页面内切换 |
| Empty | 虚线框 + 一句话 + 可行动入口 | 空态展示 |
| Metric | 等宽数字 `data-lg` + delta 涨跌 | KPI 指标卡 |

### 6.2 业务组件

| 组件 | 规格 | 用途 |
|---|---|---|
| ImageCell | 图片 + 占位 + 错误处理 + 懒加载 | 商品/订单/任务图片 |
| Price | 金额格式化 + 货币符号 | 价格显示 |
| StatusBadge | 状态映射 + 颜色 | 订单/任务/商品状态 |
| Pagination | 分页 + 页码 + 每页条数 | 表格分页 |

### 6.3 图片组件

```tsx
function ImageCell({ src, alt, size = 'md' }: ImageCellProps) {
  const [error, setError] = useState(false)
  if (!src || error) return <div className="img-placeholder" />
  return (
    <img 
      src={src} 
      alt={alt}
      className={`img-cell img-${size}`}
      onError={() => setError(true)}
      loading="lazy"
    />
  )
}
```

## 7. 响应式设计

### 7.1 断点定义

| 断点 | 宽度 | 布局 | 侧栏 |
|---|---|---|---|
| 数据大屏 | ≥1600px | 全屏地图 + 左右栏 | 隐藏 |
| 桌面 | 1200-1599px | 固定侧栏236px + 内容流 | 常驻 |
| 平板 | 768-1199px | 侧栏折叠为图标栏 | 折叠 |
| 移动 | <768px | 顶栏 + 底部Tab | 隐藏 |

### 7.2 响应式策略

```css
/* 桌面模式 */
@media (min-width: 1200px) {
  .layout { grid-template-columns: 236px 1fr; }
  .sidebar { display: block; }
}

/* 平板模式 */
@media (min-width: 768px) and (max-width: 1199px) {
  .layout { grid-template-columns: 64px 1fr; }
  .sidebar { width: 64px; }
  .sidebar-label { display: none; }
}

/* 移动模式 */
@media (max-width: 767px) {
  .layout { grid-template-columns: 1fr; }
  .sidebar { display: none; }
  .bottom-tab { display: flex; }
}
```

## 8. 错误处理

### 8.1 Axios 拦截器

```typescript
api.interceptors.response.use(
  response => response,
  error => {
    if (error.response?.status === 401) {
      clearToken()
      window.location.href = '/app/login'
    }
    if (error.response?.status === 503) {
      toast.error('服务暂时不可用，请稍后重试')
    }
    return Promise.reject(error)
  }
)
```

### 8.2 错误状态

| 状态 | 组件 | 行为 |
|---|---|---|
| 加载中 | Skeleton / Spinner | 显示加载占位 |
| 加载失败 | ErrorState + 重试按钮 | 显示错误信息 + 重试 |
| 空数据 | EmptyState（虚线框 + 说明 + 入口） | 显示空态引导 |
| 401 | 重定向登录页 | 清除 token，跳转登录 |
| 503 | Toast 通知 | 显示服务不可用提示 |

## 9. 性能优化

### 9.1 代码分割

```tsx
const Dashboard = lazy(() => import('./routes/_authenticated/index'))
const Products = lazy(() => import('./routes/_authenticated/products'))
const Orders = lazy(() => import('./routes/_authenticated/orders'))
```

### 9.2 图片懒加载

```tsx
<img src={url} loading="lazy" />
```

### 9.3 TanStack Query 缓存

```tsx
const { data } = useQuery({
  queryKey: ['orders'],
  queryFn: () => api.get('/orders'),
  staleTime: 5 * 60 * 1000, // 5分钟内不重新请求
})
```

### 9.4 虚拟滚动

```tsx
// 大列表使用虚拟滚动
const virtualizer = useVirtualizer({
  count: orders.length,
  getScrollElement: () => scrollRef.current,
  estimateSize: () => 48,
})
```

## 10. 视觉 QA

### 10.1 自动化流程

```typescript
// 1. Playwright 截图
await page.goto('http://localhost:5173/app/orders')
await page.screenshot({ path: 'screenshots/orders.png' })

// 2. 多模态模型对比
const diff = await multimodalModel.compare(
  'screenshots/orders.png',
  'design-deliverables/ozon-orders-proto.png'
)

// 3. 输出差异报告
if (diff.hasDifferences) {
  console.log('差异:', diff.differences)
  // 例：「表格行高应为40px，当前为36px」
  // 例：「状态标签颜色应为#FDEBEB，当前为#F5F5F5」
}
```

### 10.2 验证标准

每个页面完成后对照 proto PNG 逐像素检查：
- 布局结构一致
- 颜色/间距/圆角/阴影符合 tokens
- 字体/字号/字重符合 spec §03
- 组件状态（hover/focus/active/disabled）符合 spec §06
- 数据从正确 API 端点加载
- 图片从正确 URL 加载
- 错误/加载/空态处理完整

## 11. 实施波次

### W0 基础（1天）

**目标**：项目脚手架 + 设计系统 + 认证 + 布局 + 组件库 + 路由

**任务**：
1. 项目初始化（Vite + React 19 + TS + Tailwind v4）
2. `design-tokens.json` → CSS 变量 + Tailwind 配置
3. 认证系统（token 存储 + Axios 拦截器 + 路由守卫）
4. 布局壳（黑侧栏236px + 顶栏 + 内容区）
5. 原子组件库（Button/Badge/Card/Input/Table/Tabs/Empty/Metric）
6. 路由注册（15页文件路由）

**验收**：
- `npm run build` 通过
- 登录页可访问（静态）
- 侧栏导航可切换（静态）
- 组件库可交互（spec §06 演示）

### W1 核心（2天）

**目标**：登录 + 仪表盘 + 商品 + 订单 + 任务（5页）

**任务**：
1. 登录页（左黑品牌面板 + API Key/账号双Tab + 余额展示）
2. 仪表盘（KPI卡 + 订单趋势图 + 热销商品表）
3. 商品管理（商品表格 + 批量操作）
4. 订单中心（订单统计 + 订单表格 + 详情弹窗）
5. 任务中心（任务列表 + 状态标签 + 运行日志）

**验收**：
- 每个页面对照 proto PNG 逐像素检查
- 数据从正确 API 端点加载
- 图片从正确 URL 加载
- 错误/加载/空态处理完整

### W2 AI功能（2天）

**目标**：上架工作台 + 定价 + 图片工坊 + 采集箱（4页）

**任务**：
1. 上架工作台（草稿CRUD + AI填充 + 估价 + 提交）
2. 智能定价（策略卡 + 调价列表 + 竞品对比曲线）
3. 图片工坊（图片网格 + 重新生图 + 在线商品改图）
4. 采集箱（采集统计 + 商品卡片 + 批量导入）

**验收**：
- AI 填充功能可用
- 估价功能可用
- 重新生图功能可用
- 批量导入功能可用

### W3 数据配置（2天）

**目标**：店铺 + 热销榜 + 数据大屏 + 模板 + 设置 + 管理（6页）

**任务**：
1. 店铺管理（店铺卡片网格 + 验证/同步/统计）
2. 热销榜（榜单筛选 + 排行列表 + 前三红高亮）
3. 数据大屏（全屏热力图 + 增长曲线 + 实时订单流）
4. 上架模板（模板卡片 + CRUD + 店铺覆盖）
5. 系统设置（公告/配置/查询词/物流费率 四Tab）
6. 管理员后台（概览统计 + 用户/店铺/任务三Tab）

**验收**：
- 数据大屏全屏布局正确
- 热力图/折线图/柱状图渲染正确
- 实时数据轮询正常
- 光晕动效正常

## 12. 预计工期

| 波次 | 页面 | 预计 |
|---|---|---|
| W0 基础 | 项目脚手架 + tokens + 认证 + 布局 + 组件库 + 路由 | 1天 |
| W1 核心 | 登录 + 仪表盘 + 商品 + 订单 + 任务 | 2天 |
| W2 AI功能 | 上架工作台 + 定价 + 图片工坊 + 采集箱 | 2天 |
| W3 数据配置 | 店铺 + 热销榜 + 数据大屏 + 模板 + 设置 + 管理 | 2天 |
| **总计** | **15页** | **7天** |

## 13. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 设计稿与 API 字段不匹配 | 前端无法渲染 | 提前验证 API 响应结构 |
| 数据大屏复杂度高 | 延期 | 单独处理，简化实现 |
| 图片 URL 失效 | 图片无法显示 | 占位图 + 错误处理 |
| 响应式布局复杂 | 样式问题 | 先做桌面，再适配其他 |

---

*PRD 生成：2026-08-18 · 对应设计规格 v1.3 · 15页全量重建*
