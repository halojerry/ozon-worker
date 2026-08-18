# PLAN · 视觉全站落地 + 能力补齐执行计划

> 版本：2.0 · 对应 PRD v2.0 · 项目 v0.56.6 · 更新 2026-08-18
> 关联：`../design-deliverables/`、`../api-integration/`、`TASKS.md`、`TEST.md`

## 阶段总览

| 波次 | 名称 | 产出 | 依赖 | 状态 |
|---|---|---|---|---|
| M0 | 材料就绪 | 设计稿 + API 材料 + 工作清单 | — | ✅ 已完成 |
| W1 | 视觉 token 全站落地 | `theme.css` `:root/.dark` 映射 + 校验脚本 + token 收敛 | M0 | ⬜ |
| W2 | 组件级打磨 | KPI 卡/表格 mono/空态对齐 spec | W1 | ⬜ |
| W3 | 页面回归 + 清理 | 15 页对照 + 硬编码 hex 清理 + 登录页迁移 | W2 | ⬜ |
| W4 | API 接线 | KPI/物流费率/订单图+v4/在售列表/店铺统计 | W1 | ⬜ |
| W4b | 多用户公共数据聚合 | 热销榜/发现归档全局共享（保留贡献者）；蓝海/榜单无 webui 读端点，本次不开放 | W4 后可并行 | ⬜ |
| W5 | 静默采集改造 | aibuy 毒 token 修复 + 热销榜 cookie 直调 | M0 | ⬜ |
| W6 | 缺口占位 + 类型迁移 + 验收 | 占位页 + generated.d.ts 接入 + TEST 全绿 | W1-W5 | ⬜ |

## W1 · 视觉 token 全站落地（预计 1-2 天）

| # | 动作 | 验收 |
|---|---|---|
| 1.1 | `theme.css` `:root`(light) 映射：bg `#F7F6F2`、foreground `#111`、primary `#E20E0E`+白字、sidebar `#111`+`#E8E8E8` 字+`#1D1D1D` hover、border `#E6E4DF`、ring `#E20E0E`、radius 10px 基座、`--font-mono` 新增、`--font-sans` 改 spec 中文栈 | 浏览器验证侧栏变黑/按钮变红/底变暖白 |
| 1.2 | `.dark`(dark 适配)：背景暗暖灰、sidebar 保持 `#111`、primary 保持红、红浅底徽标反转为深红底浅红字 | dark 切换风格一致不违和 |
| 1.3 | token 校验脚本（20 行 node）：读 `design-deliverables/design-tokens.json` 断言 `theme.css` 关键值一致 | 脚本可跑，值一致；改 token 文件后脚本能发现漂移 |
| 1.4 | 废弃 `src/tokens/tokens.json`（标注 legacy 不再当真相源）| 文件标注 + 无代码引用 |
| 1.5 | `src/index.css`：同步 `:root` 值（与 theme.css 一致）+ 标注「业务样式 3519 行，**不得整体删除**」| 登录页视觉不违和；业务样式无回归 |

## W2 · 组件级打磨（预计 1-2 天）

| # | 动作 | 验收 |
|---|---|---|
| 2.1 | KPI 指标卡组件（data-lg 等宽数字 + 红色强调 + delta 涨跌）| 对照 spec §06 指标卡实样 |
| 2.2 | 表格数字单元格应用 `font-mono`（价格/金额/计数列）| 对照 spec 表格实样 |
| 2.3 | 空态组件对齐 spec（虚线框 + 一句话 + 可行动入口）| 对照 spec §06 空态实样 |
| 2.4 | 侧栏/按钮/徽标 token 自动翻转验证（shadcn token 驱动）| AppSidebar 黑底白字、按钮红底 |

## W3 · 页面回归 + 清理（预计 2 天）

| # | 动作 | 验收 |
|---|---|---|
| 3.1 | 15 页逐页对照 proto PNG（登录/仪表盘/商品/上架/订单/任务/定价/图工坊/采集箱/店铺/热销榜/大屏/模板/设置/管理）| 逐页截图对比，无风格错位 |
| 3.2 | 清理 ~6 处硬编码 hex（auth-layout/landing/Login/invitation-card 等）| 无裸 hex，改吃 token |
| 3.3 | 登录页迁移出 `src/index.css`（`Login.tsx:6` 去掉 `../index.css` import，将登录页所需样式搬入 Tailwind/`styles/`）| 登录页渲染正常 |
| 3.4 | `main.tsx:46` 的 `./index.css`：**评估业务样式迁移**（`.app-shell`/`.sidebar`/`.card`/`.btn` 等 36+ 选择器，3519 行）——未完成迁移前**保留 import**，禁止整体删除 | 全站无回归（业务样式仍在）|

## W4 · API 接线（预计 2-3 天）

| # | 动作 | 验收 |
|---|---|---|
| 4.1 | 首页 KPI：**新增** client.ts 函数 `getTaskStatistics()`（GET `/api/v1/task_statistics`，租户级）+ 接已有 `getAdminOverview`（client.ts:1147，平台级）| KPI 卡真实数字（Gap #1 修复）|
| 4.2 | SystemSettings「业务」Tab 接 `listLogisticsRates/updateLogisticsRate/importLogisticsRatesCsv`（client.ts:1389-1405）| 费率表可浏览/编辑/CSV 导入（Gap #5 修复）|
| 4.3 | 订单商品图：`order_service` 复用 `_fetch_info_map` 模式（**按 product_id 批量** `/v3/product/info/list`，store_sync_service.py:214-239 先例）→ `OrderProductOut.image` 字段 + 缓存 products JSONB 存图 | 订单列表商品行显示缩略图 |
| 4.4 | **顺带迁移** `/v3/posting/fbs/list` → `/v4/posting/fbs/list`（游标分页 + price 对象）+ 订单字段适配 | 订单接口正常（I-11 修复）|
| 4.5 | 在售列表图/价：前端改调 `/products/ozon`（有 image/price/stock）或补 `ProductListItem` 字段 | 商品管理表显示缩略图/价格（I-4 修复）|
| 4.6 | 店铺卡统计：新增端点（store_sync 缓存聚合：今日订单数/销售额/利润——缓存含 `product_count/total_amount/commission_amount/profit`，**无评分字段**，卡片不显示评分）| 店铺卡片显示统计（I-5 修复）|

## W4b · 多用户公共数据聚合（预计 1 天）

| # | 动作 | 验收 |
|---|---|---|
| 4b.1 | `analytics_service.list_bestsellers` 去掉 `contributed_by_token_id = :tid` 过滤（全局共享）| A 用户采集，B 用户可看热销榜 |
| 4b.2 | 发现归档开放：`GET /api/v1/discovery/runs`（main.py:2264）去掉 tenant 过滤 → 全局共享（保留 `contributed_by_token_id` 贡献者列）。**蓝海（`/admin/queries` admin-only）与榜单（`market_bestsellers` 无读端点，仅 POST main.py:2220）本次不开放**——需新读端点，见 TODO #12 | 贡献者标注保留；A 用户可看 B 用户发现归档 |
| 4b.3 | 订单/商品/草稿/凭证/任务隔离不动 + 补测试锁定租户隔离 | 跨租户查询仍 404/403 |

## W5 · 静默采集改造（预计 2-3 天，skill 侧）

| # | 动作 | 验收 |
|---|---|---|
| 5.1 | aibuy 毒 token 修复：`_fetch_aibuy_cookies_from_chrome` 校验 `_m_h5_tk` **value 非空**（`ozon_image_search.py:545`）| 空 token 不再落盘 |
| 5.2 | 死 token 不缓存：`_aibuy_token_valid()` helper，token 空不 `_save_aibuy_token`（`:702-705`）| 6h 毒 token 问题消除 |
| 5.3 | mtop token 舞步等待：导航后轮询 `document.cookie` ≤5-10s 等 token 非空（`:535-536`）| 冷启动 token 就绪 |
| 5.4 | 降级出声：`cloud_probe.py:3352` / `ozon_discovery.py:2189` debug → warning 带原因 | 日志可见「为什么走 CDP」|
| 5.5 | 日志文案修正：「无 1688 反爬 cookie」替代「无 1688 会话」| 语义准确 |
| 5.6 | 热销榜 cookie 直调：skill `queries --type ozon-bestsellers/all-queries` 改「读 Chrome 会话 cookie → requests 直调 `POST https://seller.ozon.ru/api/site/seller-analytics/what_to_sell/data/v3`」（复用 aibuy cookie 读取模式）| 免开 Chrome 静默出数据，与 CDP 结果一致 |
| 5.7 | 编译 .so 特征校验：stub 加 `search_by_image_aibuy` 存在性检查（`compile.py`）| 旧 .so 明确 warning 不静默（I-10）|

## W6 · 缺口占位 + 类型迁移 + 验收（预计 2 天）

| # | 动作 | 验收 |
|---|---|---|
| 6.1 | 竞品曲线占位（空态规范，不做假按钮）| 截图符合 Do/Don't |
| 6.2 | 图工坊 AI 背景编辑占位（同规范）| 截图符合 Do/Don't |
| 6.3 | 类型迁移：client.ts 手写类型 → `generated.d.ts` | `tsc --noEmit` 0 错误 |
| 6.4 | TEST.md 全部用例执行 + 记录 | 全绿 |

## 排期建议

若 1 人全职：W1(1.5d) → W2(1.5d) → W3(2d) → W4(2.5d) → W4b(1d) → W5(2.5d) → W6(2d) ≈ **13 个工作日**。
W1/W5 可并行（不同负责人：前端 vs skill）；W4b 依赖 W4 数据链路但可并行开发。

## 里程碑依赖图

```
M0(✅) ──> W1 ──> W2 ──> W3 ──┐
   │                          ├──> W6（占位/类型/验收）
   ├────────> W4 ──> W4b ─────┘
   └────────> W5 ─────────────┘
```
