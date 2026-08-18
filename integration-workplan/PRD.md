# PRD · Ozon ERP WebUI 视觉全站落地 + 能力补齐

> 版本：2.0 · 状态：待评审 · 对应项目 v0.56.6 · 更新 2026-08-18
> 关联：`../design-deliverables/`（设计资产）、`../api-integration/`（API 对接材料）、`TEST.md`（验收）

---

## 1. 背景与目标

Ozon AI 自动化运营 ERP 已有完整后端（worker）、前端（webui）与对接材料（api-integration + integration-workplan v1 已完成 M0）。2026-08-17 交付了**新版设计稿**（`design-deliverables/`：规格书 v1.3 + design-tokens.json + 15 页原型 + 参考图），设计语言为「黑白灰 + 品牌红 #E20E0E + 黑侧栏 + 暖白底」的编辑式商业 SaaS 风格。

**本 PRD 定义两个核心目标**：

1. **视觉全站落地**：新设计稿在 webui 全站生效（不 opt-in），dark 模式保留并适配。
2. **能力最大化对接 + 多用户公共数据聚合**：现有能力（worker API / skill 采集 / Ozon API）100% 接线，订单/在线商品等私有数据租户隔离、热销榜/发现归档等公共数据全局共享（蓝海/榜单本次不开放，见 TODO #12）；静默采集通道（aibuy 先例）修复并扩展；真缺口按设计稿空态规范占位。

**范围外**：竞品对比曲线、图工坊 AI 背景编辑等**真实新端点**（占位，单独 PRD）；webui→skill 桥接（架构不可行，见 ISSUES I-12）；Ozon Premium Pro 绕过（无需，见 ISSUES I-13）。

## 2. 范围

### 2.1 在范围内（In Scope）

| 域 | 内容 | 说明 |
|---|---|---|
| 视觉 token 收敛 | `design-deliverables/design-tokens.json` 作为唯一事实源 → `theme.css` `:root(light)` + `.dark(dark适配)` | 消除三套并行 token（I-1）；dark 保留适配（决策 #1） |
| 视觉全站生效 | 改 `theme.css` `:root`（default 即新视觉，显式选过 preset 的用户保留其选择） | 决策 #2；黑侧栏/红按钮/暖白底/等宽数字 |
| 组件级打磨 | KPI 等宽数字卡（data-lg）、表格数字 mono、空态组件对齐 spec §06 | 复用现有 shadcn token 组件 |
| 页面回归 | 15 页逐页对照 proto + 清理 ~6 处硬编码 hex + 登录页迁移出 legacy CSS | 双 CSS 系统收敛 |
| API 接线（已就绪能力） | ① 首页 KPI 接 `getTaskStatistics`/`getAdminOverview` ② SystemSettings「业务」Tab 接 logistics 三函数 ③ 订单商品图（product_id → `/v3/product/info/list`）+ **顺带迁移 `/v4/posting/fbs/list`** ④ 在售列表图/价（`/products/ozon`）⑤ 店铺卡统计（store_sync 聚合，新增端点） | 全部零新 Ozon 集成（I-4/I-5/I-11 对应修复） |
| 多用户公共数据聚合 | 热销榜（`ozon_bestsellers`）+ 发现归档（`discovery_runs` GET）：去掉 `contributed_by_token_id` 过滤 → 全局共享（保留贡献者标注）；**蓝海（admin-only）/榜单（无读端点）本次不开放**；订单/商品/草稿/凭证：租户隔离不变 | 决策 #3 + TODO #12 |
| 静默采集改造 | ① aibuy 毒 token 修复（I-8，4 处小改）② 热销榜/蓝海 cookie 直调 `what_to_sell/data/v3`（免 Chrome 导航，复用 aibuy cookie 模式） | 静默化三步走第 2 步（第 1 步 aibuy 已有） |
| 缺口占位 | 竞品曲线、图工坊 AI 背景编辑：按设计稿「空态 + 一句话说明 + 可行动入口」规范，不做假按钮 | 设计稿 Do/Don't 第 7 章硬性要求 |

### 2.2 不在范围（Out of Scope）

- 竞品对比曲线 / 图工坊 AI 背景替换 / 去背景 / 裁剪的**新端点实现**——占位，未来单独 PRD（数据源候选：Ozon `/v1/product/info/prices`；MXOU 图像 API vs PIL）
- Ozon Premium Pro 绕过——不需要（`what_to_sell/data/v3` 端点本身不校验 premium，前端 UI gate 由 skill CDP 直调天然绕过）
- webui→skill 采集桥——架构不可行（skill 客户端本地，worker 无回连通道）
- worker 内部图编排（`/run`、`/async_run` 等）与 `/api/{path}` 通配代理

## 3. 用户故事

- 作为**运营**，我希望打开 webui 看到统一的新视觉（黑侧栏/红强调/暖白底），而不是新旧风格混杂。
- 作为**运营**，我希望首页 KPI、订单商品图、物流费率 Tab 显示真实数据，而不是空占位或缺失入口。
- 作为**运营**，我希望热销榜/发现归档数据是全网聚合的（别人采集的优质数据我也能看），但我的订单/商品/凭证只属于我。
- 作为**运营**，我希望静默采集（aibuy/热销榜）不弹浏览器窗口、不打断操作；登录 1688 与否都不影响 aibuy 可用。
- 作为**开发**，我希望改设计只改一个 token 文件（唯一事实源），不担心三套 CSS 打架。
- 作为**集成方**，我希望能按 TEST.md 快速验收本次改造，无类型错误、无视觉回归。

## 4. 验收标准（用户视角）

1. webui 全站为设计稿新视觉：暖白底 `#F7F6F2`、黑侧栏 `#111`、主按钮红 `#E20E0E`、数字等宽；dark 模式可用且风格一致。
2. 首页 KPI（今日订单/AI 上品数/上架成功率）显示真实数字（接 `task_statistics` / `admin_overview`）。
3. 订单列表商品行显示缩略图（product_id → `/v3/product/info/list`，复用 `_fetch_info_map` 模式）；订单接口已迁移 `/v4/posting/fbs/list`。
4. 系统设置「业务」Tab 可浏览/编辑物流费率 + CSV 导入。
5. 热销榜显示**全局聚合**数据（非仅本人采集）；订单/商品/草稿/凭证仍严格租户隔离。
6. aibuy 静默图搜在**未登录 1688** 的干净 profile 下也能出结果（匿名可用）；热销榜采集免开 Chrome。
7. 竞品曲线/图工坊 AI 编辑按空态规范占位，无假按钮、无「敬请期待」空洞文案。
8. `design-tokens.json` 为唯一事实源，`theme.css` 由校验脚本锁定一致（防漂移）。
9. 前端 `tsc --noEmit` 0 错误；`generated.d.ts` 已接入（T7.3 类型迁移）。
10. `src/index.css` 业务样式（`.app-shell`/`.sidebar`/`.card` 等）**无视觉回归**——迁移评估完成前保留全局 import。
11. TEST.md 全部用例 PASS。

## 5. 技术约束

- **鉴权**：Bearer token（MXOU key），仅 5 个免鉴权端点；订单/商品/草稿/凭证/任务按 tenant_id 隔离（决策 #3a）。
- **公共数据**：热销榜（`ozon_bestsellers`）+ 发现归档（`discovery_runs` GET）全局共享，**保留 `contributed_by_token_id` 贡献者标注**（决策 #3b）；**蓝海（`blue_ocean_queries`，admin-only）与榜单（`market_bestsellers`，无读端点）本次不开放**（TODO #12）。
- **静默采集前提**：aibuy 与 what_to_sell cookie 直调**不需要登录态**（`_m_h5_tk` 是匿名反爬 cookie）；需要「浏览器加载过对应页面完成 mtop token 舞步」。工具 Chrome 常驻 + 1688 页面加载过即可（I-8 修复后）。
- **token 唯一事实源**：`design-deliverables/design-tokens.json` → `theme.css` `:root/.dark`（校验脚本断言）→ 组件消费。废弃 `src/tokens/tokens.json`；`src/index.css`（3519 行，含 `.app-shell`/`.sidebar`/`.card`/`.btn` 等 36+ 业务选择器）**非纯 legacy——先评估再迁移，不得整体删除 import**（见 W3.4）。
- **错误约定**：FastAPI 标准 `{"detail": "..."}`；401 清 token、503 勿清、429 退避（沿用 client.ts 拦截器）。
- **占位规范**：设计稿 Do/Don't——空态用虚线框 + 一句话说明 + 可行动入口；禁止「敬请期待」空洞文案与假按钮。

## 6. 里程碑（关联 PLAN.md）

| 里程碑 | 内容 | 依赖 |
|---|---|---|
| M0 | 材料就绪（设计稿 + API 材料 + 工作清单）✅ 已完成 | — |
| M1 | 视觉 token 全站落地 + 组件打磨 | M0 |
| M2 | 页面回归 + 清理（15 页 + 登录页迁移） | M1 |
| M3 | API 接线（KPI/物流费率/订单图/v4/在售列表/店铺统计） | M1 |
| M4 | 多用户公共数据聚合（后端过滤改共享） | M3 后可并行 |
| M5 | 静默采集改造（aibuy 修复 + 热销榜 cookie 直调） | M0 |
| M6 | 缺口占位 + 类型迁移（T7.3）+ 测试验收 | M1-M5 |

## 7. 风险（详见 ISSUES.md）

- 三套并行 token 漂移（I-1/I-2）——用唯一事实源 + 校验脚本收敛
- aibuy 静默失效（I-8 毒 token / I-9 Ozon 图可达性）——修复 + 降级出声
- 静默采集合规灰区（I-13）——保留显式模式兜底 + 文档免责
- 多用户数据池产品决策（I-14）——公共数据共享、贡献者标注，私有数据严格隔离
- `/v3/posting/fbs/list` 废弃（I-11）——随订单图改造一并迁 v4
- 编译 .so 无版本校验（I-10）——stub 加特征检查
