# TASKS · 视觉全站落地 + 能力补齐任务分解

> 可勾选执行清单 · 关联 PLAN.md（W1-W6）/ TEST.md · 状态图例：`[ ]` 待办 `[x]` 完成 `[~]` 进行中
> 更新 2026-08-18 · 对应 PRD v2.0

## T1 · 视觉 token 全站落地（PLAN W1）

- [x] 1.1 读 `design-deliverables/design-tokens.json`（唯一事实源）
- [x] 1.2 `webui/src/styles/theme.css` `:root`(light) 映射新 token（bg #F7F6F2 / primary #E20E0E / sidebar #111 / border #E6E4DF / ring #E20E0E / radius 10px 基座）
- [x] 1.3 `theme.css` `.dark` 适配（暗暖背景 / sidebar 保持 #111 / primary 保持红 / 徽标反色）
- [x] 1.4 `@theme inline` 新增 `--font-mono`（SFMono/Menlo/Consolas）+ `--font-sans` 改 spec 中文栈
- [x] 1.5 token 校验脚本（读 design-tokens.json 断言 theme.css 关键值，防漂移）
- [x] 1.6 `src/tokens/tokens.json` 标注 legacy（不再当真相源）
- [x] 1.7 `src/index.css`（3519 行，36+ 业务选择器）`:root` 同步新值 + 标注「业务样式，不得整体删除」
- [x] ✅ 验收：浏览器侧栏变黑/按钮变红/底变暖白；dark 切换正常；校验脚本可发现漂移

## T2 · 组件级打磨（PLAN W2）

- [x] 2.1 KPI 指标卡组件（data-lg 等宽数字 + 红强调 + delta 涨跌，对照 spec §06）
- [x] 2.2 表格数字单元格 `font-mono`（价格/金额/计数列）
- [x] 2.3 空态组件对齐 spec（虚线框 + 一句话 + 可行动入口）
- [x] 2.4 侧栏/按钮/徽标 token 自动翻转验证
- [x] ✅ 验收：组件实样对照 spec §06 截图

## T3 · 页面回归 + 清理（PLAN W3）

- [ ] 3.1 15 页逐页对照 proto（登录/仪表盘/商品/上架/订单/任务/定价/图工坊/采集箱/店铺/热销榜/大屏/模板/设置/管理）
- [x] 3.2 清理硬编码 hex（`features/auth/auth-layout.tsx` / `features/home/components/landing-page.tsx` / `pages/Login.tsx` / `features/wallet/components/invitation-card.tsx` 等 13 文件 ~6 处，排除 brand-icons）
- [x] 3.3 登录页迁移出 `src/index.css`（`Login.tsx:6` 去 `../index.css` import，样式搬入 Tailwind/`styles/`）
- [x] 3.4 `main.tsx:46` `./index.css`：评估业务样式迁移（`.app-shell`/`.sidebar`/`.card`/`.btn` 等）——未迁移完成前**保留 import**
- [x] ✅ 验收：全站无视觉回归，登录页正常

## T4 · API 接线（PLAN W4）

- [x] 4.1 首页 KPI：**新增** client.ts 函数 `getTaskStatistics()`（GET `/api/v1/task_statistics`）+ 接已有 `getAdminOverview`（client.ts:1147）（修复 Gap #1）
- [x] 4.2 SystemSettings「业务」Tab 接 logistics 三函数（listLogisticsRates/updateLogisticsRate/importLogisticsRatesCsv，client.ts:1389-1405，修复 Gap #5）
- [x] 4.3 订单商品图：`order_service` 复用 `_fetch_info_map` 模式按 **product_id** 批量 `/v3/product/info/list`（store_sync_service.py:214-239 先例，**非 offer_id**）→ `OrderProductOut.image`
- [x] 4.4 订单接口迁移 `/v4/posting/fbs/list`（游标分页 + price 对象适配；order_service.py:177 + store_sync_service.py:68）
- [x] 4.5 在售列表图/价：前端改调 `/products/ozon` 或补 `ProductListItem` 字段（修复 I-4）
- [x] 4.6 店铺卡统计端点（store_sync 缓存聚合今日订单数/销售额/利润——缓存无评分字段，不显示评分，修复 I-5）
- [x] ✅ 验收：curl 走通 + 页面真数据（KPI 数字/订单图/费率编辑）

## T5 · 多用户公共数据聚合（PLAN W4b）

- [x] 5.1 `analytics_service.list_bestsellers` 去掉租户过滤（全局共享，保留贡献者列）
- [x] 5.2 `GET /api/v1/discovery/runs`（main.py:2264）去掉 tenant 过滤 → 全局共享（保留贡献者列）；**蓝海/榜单不开放**（无 webui 读端点，见 TODO #12）
- [x] 5.3 订单/商品/草稿/凭证/任务隔离不动 + 租户隔离测试锁定
- [x] ✅ 验收：A 用户采集 → B 用户可见热销榜 + 发现归档；跨租户订单查询仍 404

## T6 · 静默采集改造（PLAN W5，skill 侧）

- [x] 6.1 aibuy token value 校验（`ozon_image_search.py:545`：`_m_h5_tk` token 部分非空才返回）
- [x] 6.2 死 token 不落盘（`:702-705`：加 `_aibuy_token_valid()` helper）
- [x] 6.3 mtop token 舞步等待（`:535-536`：轮询 document.cookie ≤5-10s 而非固定 2s）
- [x] 6.4 降级出声（`cloud_probe.py:3352` / `ozon_discovery.py:2189`：debug → warning 带原因）
- [x] 6.5 日志文案修正（「无 1688 反爬 cookie」替代「无 1688 会话」）
- [x] 6.6 热销榜 cookie 直调：`queries --type ozon-bestsellers/all-queries` 改 requests 直调 `what_to_sell/data/v3`（复用 aibuy cookie 读取模式）
- [x] 6.7 编译 .so 特征校验（stub 加 `search_by_image_aibuy` 存在性检查，`compile.py:296-355`）
- [x] ✅ 验收：未登录 1688 干净 profile aibuy 出结果；热销榜免开 Chrome；旧 .so 明确 warning

## T7 · 缺口占位 + 类型迁移 + 验收（PLAN W6）

- [x] 7.1 竞品曲线占位（空态规范）
- [x] 7.2 图工坊 AI 背景编辑占位（空态规范）
- [x] 7.3 类型迁移：client.ts 手写类型 → `generated.d.ts`（`import type { components } from './generated'`）
- [x] 7.4 按 TEST.md 逐条执行 + 记录
- [x] ✅ 验收：`npx tsc --noEmit` 0 错误；TEST.md 全绿；15 页截图存档

## T7b · 补充任务（ISSUES 处理落位）

- [x] 7b.1 采集箱 `source="webui"`：webui `createDraft` 传 `source="webui"` 区分来源（修复 I-6，draft_service.py:101）
- [x] 7b.2 图片工坊死 URL 容忍：前端图片页容忍死 URL（`image_quality_evaluator.check_url_alive` 已过滤 update_images；I-7 验证）
- [x] 7b.3 aibuy Ozon 图可达性验证：本地 fetch 竞品图测试 `ir.ozone.ru` 可达性；若确认不可达 → aibuy 前经 COS 转存或降级提示（修复 I-9，验证项）
- [x] 7b.4 `check` 命令 cookie 就绪检测：工具 Chrome 是否已加载过 1688 页面（cookie 热）→ 明确提示（缓解 R-4）
- [x] ✅ 验收：I-6/I-7/I-9/R-4 各有落地步骤，ISSUES 处理不悬空

## T8 · 收尾

- [x] 8.1 更新 `integration-workplan/README.md` 状态速览（v2.0）
- [x] 8.2 确认 openapi.json 快照与运行实例一致（后端变更后重新生成 generated.d.ts）
- [x] 8.3 更新 `AGENTS.md` 最近更新段（视觉 v2.0 + 多用户聚合 + 静默采集）
