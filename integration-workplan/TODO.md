# TODO · 当前待办清单

> 谁 / 何时 / 卡在哪 · 更新日期 2026-08-18 · 项目 v0.56.6 · 对应 PRD v2.0

## 高优先级

| # | 待办 | 负责人 | 目标日期 | 阻塞/备注 |
|---|---|---|---|---|
| 1 | **W1 视觉 token 全站落地**（theme.css 映射 + 校验脚本 + token 收敛） | 前端 | 待排期 | 决策已定：全站生效 + dark 保留适配 |
| 2 | **W5 静默采集改造**（aibuy 毒 token 修复 I-8 + 热销榜 cookie 直调） | skill | 待排期 | 根因已定位（ozon_image_search.py:545/705/571-573）|
| 3 | **W4 API 接线**（KPI/物流费率/订单图+v4/在售列表/店铺统计） | 后端+前端 | 待排期 | 全部已就绪能力，零新 Ozon 集成 |
| 4 | **W4b 多用户公共数据聚合**（热销榜/发现归档全局共享；蓝海/榜单本次不开放） | 后端 | 待排期 | 决策已定：私有隔离 + 公共共享（热销榜/发现归档） |
| 5 | **六份文档评审**（PRD/PLAN/TASKS/ISSUES/TODO/TEST v2.0） | 全员 | 已评审 | ✅ 评审 GATE: APPROVE（2026-08-18，2 轮复核后）——待拍板开干 |

## 中优先级

| # | 待办 | 负责人 | 备注 |
|---|---|---|---|
| 6 | W3 页面回归 + 硬编码 hex 清理 + 登录页迁移 | 前端 | 依赖 W1/W2 |
| 7 | W2 组件级打磨（KPI 卡/表格 mono/空态） | 前端 | 依赖 W1 |
| 8 | W6 占位页（竞品曲线/图工坊 AI 编辑）+ T7.3 类型迁移 | 前端 | 依赖 W1-W5 |
| 9 | openapi.json 快照与运行实例一致性核对 | 后端 | 后端改 schema 后需重新拉取 + 重生成类型 |

## 低优先级 / 后续

| # | 待办 | 备注 |
|---|---|---|
| 10 | 竞品曲线/图工坊 AI 背景编辑的新端点（数据源候选：Ozon `/v1/product/info/prices`；MXOU 图像 API vs PIL） | 单独 PRD，本次只占位 |
| 11 | `/v1/search-queries/top`（热门搜索词）worker 侧能力 | Premium Pro 门槛 + 语义是搜索词非商品榜，未来项 |
| 12 | 蓝海/榜单 webui 读端点（`/admin/queries` admin-only 需开放；`market_bestsellers` 无 GET 端点需新增）——W4b.2 本次**不开放**这两类 | 依赖新读端点，未来项 |
| 13 | CI 接入 openapi-typescript 自动生成 | 自动化 |
| 14 | webui→skill 采集桥（客户端 daemon） | 架构不可行，除非单独立项 |

## 已完成 ✅

- 对接文档 `api-integration/API-INTEGRATION-GUIDE.md`（109+ 端点 + 实测验证）
- TS 类型 `api-integration/generated.d.ts`（6970 行，tsc 通过）
- OpenAPI 快照 `api-integration/openapi.json`（97 路径）
- 设计交付 `design-deliverables/`（规格书 v1.3 + design-tokens.json + 20 张图）
- 竞品逆向：毛子/上品帮 Premium 绕过机制（改 Vuex store / 拦截 premium-status 响应）
- 根因定位：aibuy 毒 token（I-8）+ Ozon Seller API 无榜单端点（I-13 数据链路确认）
- 工作包 v2.0 六件套（PRD/PLAN/TASKS/ISSUES/TODO/TEST）

## 决策记录（2026-08-18 已定）

| # | 决策 | 结论 |
|---|---|---|
| D1 | Dark 模式 | 保留并适配（黑侧栏 #111 两模式一致，暗暖背景） |
| D2 | 视觉生效方式 | 直接改 `theme.css` `:root`（default = 新视觉，显式选过 preset 的用户保留选择） |
| D3 | 多用户数据 | 订单/商品/草稿/凭证/任务租户隔离；热销榜（`ozon_bestsellers`）+ 发现归档（`discovery_runs` GET）全局共享（保留贡献者）；**蓝海/榜单不开放**（无 webui 读端点，见 #12） |
| D4 | 静默采集 | 修复 aibuy 毒 token + 热销榜 cookie 直调（复用 aibuy 模式）；无需 Premium 绕过代码 |
| D5 | 订单图 | product_id → `/v3/product/info/list`（复用 `_fetch_info_map`，非 offer_id），顺带迁 `/v4/posting/fbs/list` |
| D6 | 文档位置 | 原位升级 `integration-workplan/` 六件套 v2.0 |
| D7 | getMyKey 死函数 | **已决：保留**——探针「0 引用」误报：`getMyKey`(client.ts:206) 被 `ensureMyKey`(client.ts:213) 调用，后者被 `use-auth-redirect.ts:72` 消费（登录自动取 key 链路，v0.56 设计）。删除会破坏登录免手动建 key 流程 |
