# API 对接状态与待办

> 更新时间：2026-08-18  
> OpenAPI 基线：`src/imports/openapi.json`（对接包标注 v0.56.6 / v0.57 增量）  
> 本文是当前 WebUI 的接口接入清单。**未列为“已接入”的功能均不应被视为已联通生产数据。**

## 运行约定

- 默认 API 基址：同域 `/api/v1`。
- 非同域部署时，设置 `VITE_API_BASE_URL=https://<worker-host>:8080/api/v1`。
- 已登录会话存储键：`ozon_webui_token`、`ozon_webui_role`、`ozon_webui_username`。
- 除登录、公告、横幅、健康检查外，均通过 `Authorization: Bearer <token>` 请求。
- 生产环境不得在前端代码、静态配置或示例数据中写入共享 API Key。

---

## 已接入（第一期）

| 界面 | 接口 | 当前能力 | 降级策略 |
|---|---|---|---|
| 登录 | `POST /auth/verify` | 验证 API Key 并保存普通成员会话 | 显示接口错误，不进入应用 |
| 登录 | `POST /mxou/login` | 账号密码登录，读取 `key`、`username`、`role` | 缺少 key 或登录失败时停留登录页 |
| 管理员路由 | `MxouLoginResponse.role` | `admin` 才显示并可进入管理员后台 | 普通成员隐藏入口并显示无权限页 |
| 管理员后台 KPI | `GET /admin/overview` | 成员、店铺、今日任务、成功率 | 保留演示 KPI 并提示未同步 |
| 管理员成员表 | `GET /admin/users` | 读取成员列表 | 保留最小演示成员行 |
| 选品广场榜单 | `GET /analytics/bestsellers` | 读取并映射商品标题、品牌、类目、销售指标、图片 | 保留示例商品，并提示未读取实时榜单 |
| 采集箱 | `GET /drafts` | 读取草稿并映射为采集列表 | 保留示例采集记录 |

### 已接入但需要联调确认的字段

以下端点已绑定页面，但其 OpenAPI 返回字段是开放对象或当前文档未列出完整展示结构。联调时需以真实响应校准字段映射：

- `GET /analytics/bestsellers`：目前兼容读取 `items/data` 与 `title/product_name/name`、`image_url/image` 等常见字段。
- `GET /admin/users`：目前兼容读取数组、`items` 或 `users`，并读取 `username/name/email`、`role`、`status`、`created_at`。
- `GET /drafts`：目前兼容读取数组、`items` 或 `drafts`，以及 `envelope.draft`、`payload`、顶层字段。

联调完成后，应收紧为接口的确定类型，不再依赖兼容字段回退。

---

## 未接入：当前页面已经有入口，但仍是占位或演示数据

### 选品广场

| 功能 / 分类 | 现状 | 已有相关接口 | 缺口与后续动作 |
|---|---|---|---|
| 大盘总览 | 占位 | `GET /analytics/bestsellers`、`GET /discovery/runs` | 缺少面向大盘 KPI 的聚合接口或明确的榜单统计字段。 |
| 类目分析 | 占位 | `GET /mappings/lookup` | 只有映射查询，没有类目销量、增长、竞争度聚合接口。 |
| 热销产品 | 已接榜单读取 | `GET /analytics/bestsellers` | 需要补齐类目、时间、市场、仓库筛选参数的真实契约。 |
| 中国储热销 | 占位 | `GET /analytics/bestsellers`（不能确认仓维度） | 需要“仓库库存 × 市场榜单”接口，或在榜单接口增加仓库筛选与库存字段。 |
| 热词精选 | 占位 | `POST /analytics/queries`、`GET /admin/queries` | 前者是上报，后者是管理员词库；缺少给选品前台的热词读取接口。 |
| 标签反查 | 占位 | 无 | 需要“商品/关键词 → 标签”查询接口。 |
| WB 热销 | 占位 | 无 | 需要 Wildberries 榜单采集、存储与读取接口。 |
| 自定义筛选 | 仅 UI | `GET /analytics/bestsellers` | 文档未定义 query 参数；确认 `category`、`brand`、`fulfillment`、价格、销量、增长等参数后再接。 |
| 加入采集箱 | 仅提示 | `POST /drafts`、`GET /credentials` | 创建草稿前必须选择店铺凭证，且 payload 需满足 `DraftCreate` 的 `envelope.draft` 必填字段。 |
| 批量上架 | 占位 | `POST /drafts/{draft_id}/submit` | 需先完成草稿创建、凭证选择、提交确认和异步任务状态跟踪。 |

### 管理员后台

| 功能 | 现状 | 已有相关接口 | 缺口与后续动作 |
|---|---|---|---|
| 用户详情 | 未接 | `GET /admin/users/{user_id}` | 可作为下一步读取详情抽屉。 |
| 新增成员、编辑角色、停用成员 | 占位 | 无 | 需要管理员成员写接口；不要在前端伪造成功状态。 |
| 店铺总览 | 未接 | `GET /admin/stores` | 可补充为管理员店铺页。 |
| 任务总览 | 未接 | `GET /admin/tasks` | 可补充为管理员任务页和异常任务筛选。 |
| 类目配置 | 仅本地草稿 | `GET/PUT /admin/config/{name}` | 必须先确认允许的配置名、读写 JSON 结构、排序/启停/发布语义；建议约定 `selection_plaza_categories` 配置名。 |
| 数据源管理 | 仅本地草稿 | `/health`、`/store/health` 仅整体健康 | 缺少数据源 CRUD、单源健康、手动同步、字段映射和调度配置接口。 |
| 权限与审计 | 占位 | 无明确审计接口 | 需要角色权限、操作日志、筛选和导出接口。 |
| 查询词库 | 未接 | `GET/POST/DELETE /admin/queries` | 可用于管理员“蓝海词库”管理，不应直接替代实时热词榜。 |
| 物流费率 | 未接 | `GET/PUT /admin/logistics/rates`、`POST /admin/logistics/rates/import` | 适合后续加入系统配置。 |
| 站点公告和横幅 | 未接 | `/admin/site/announcements`、`/admin/site/banners` CRUD | 可在管理员后台新增运营内容模块。 |

### 既有 ERP 页面

| 模块 | 未接入接口 | 建议顺序 / 注意事项 |
|---|---|---|
| 店铺管理 | `GET/POST/PATCH/DELETE /credentials`、`POST /credentials/{id}/validate`、`GET /stores/{id}/stats`、同步接口 | 优先接凭证列表、店铺卡统计和同步状态；凭证内容不得回显敏感 Key。 |
| 商品管理 | `GET /products`、`GET /products/ozon`、`GET /products/{id}/edit`、批量价格/库存/归档 | 先列表和详情，再接批量操作；批量操作需二次确认。 |
| 订单中心 | `GET /orders`、发货/取消/标签/消息/备注接口 | 先列表筛选，再接高风险写操作；取消和发货必须给出确认与失败反馈。 |
| 任务中心 | `GET /tasks`、`GET /task_status/{id}`、任务草稿和图片接口 | 任务进度在 worker 重启后可能降级，UI 要允许“进度不可用”。 |
| 上架模板 | `/templates` CRUD、设默认接口 | 完整 CRUD 可直接排期。 |
| 定价、物流报价 | `POST /estimate`、`POST /logistics/quote` | 需要与商品成本、尺寸重量、目的地字段做表单映射。 |
| 图片工坊 | 图片列表、重生成、商品改图接口 | 先读任务图片，再做单槽位重生成；长任务应轮询状态。 |
| 系统通知 | `GET /site/announcements`、`GET /site/banners` | 公开读取，可作为低风险快捷接入项。 |
| 数据大屏 | `GET /task_statistics`、店铺统计等 | 需确认订单和销售趋势的时间序列接口；当前仅有 KPI 级数据源。 |

---

## 后端待补接口建议

这些能力没有可确认的领域接口，建议后端先明确再开始前端正式联调：

1. **选品分类管理**：分类树读取、保存、排序、启停和发布。
2. **选品数据源管理**：数据源 CRUD、授权状态、最近同步、同步频率、手动同步、填充范围和字段映射。
3. **中国仓热销**：按仓库维度返回库存、销量、毛利、周转和排名。
4. **热词精选与标签反查**：热词趋势读取、商品/词标签反查、时间和类目筛选。
5. **Wildberries 榜单**：平台数据采集状态、榜单读取和平台维度筛选。
6. **管理员成员写操作和审计日志**：成员邀请、角色授予/撤销、停用、操作记录。
7. **榜单查询参数**：对 `/analytics/bestsellers` 明确并在 OpenAPI 中定义分页、筛选、排序和返回 schema。

---

## 推荐实施顺序

1. **采集链路闭环**：`GET /credentials` → 选择店铺 → `POST /drafts` → 编辑 → `POST /drafts/{id}/submit` → 轮询任务状态。
2. **管理员读取完善**：用户详情、店铺、任务、查询词库、物流费率。
3. **商品和订单真实化**：先只读列表/详情，再接危险写操作。
4. **确认 `admin/config` 契约**：完成分类配置持久化。
5. **补齐数据源与选品领域接口**：再发布中国仓、WB、热词和标签反查功能。

## 联调验收清单

- [ ] 同域 `/api/v1` 或 `VITE_API_BASE_URL` 已在目标环境配置。
- [ ] 登录接口返回真实可用的 `key` 和 `role`。
- [ ] 普通成员无法发现或直达管理员写接口。
- [ ] `/analytics/bestsellers`、`/admin/users`、`/drafts` 的真实响应已完成字段映射确认。
- [ ] 401、403、409、429、503 在 UI 中均有明确反馈。
- [ ] 全局共享榜单/选品归档在 UI 中显示来源或贡献者，避免误解为私有数据。
- [ ] 草稿创建前已选择凭证，并对提交/发货/归档等写操作增加确认。
