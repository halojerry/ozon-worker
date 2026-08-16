# PRD — 上架配置模板（P0-1，对标上品帮 UpGoodsSetting）

> 2026-08-16。竞品调研产物：`docs/competitor/shangpinbang-full.md` §2.3（上品帮上架配置 7 区块字段）、`docs/competitor/maozier-plugin-full.md` §3（毛子插件一键上架 11 字段）。
> 本 PRD 为「自有 WebUI 复刻路线」P0 第一项：上架配置模板。

## 一、背景与目标

### 1.1 问题
当前 WebUI 上架流程中，每次提交草稿时的定价参数（利润率/佣金率/汇率缓冲）、库存、货号前缀等散落在：
- 草稿 envelope.extensions（skill 采集时写入，WebUI 只透传）
- worker pricing_node 默认值（margin=0.25 / commission=0.0 自动查 / fx=0.05）

用户无法在 WebUI 统一配置「这次批量上架用什么定价策略、什么货号前缀」，每次都要依赖采集数据里的旧参数或 worker 默认值。

### 1.2 目标
对标上品帮 `/upGoodsSetting`（上架配置模板，可设默认），为 WebUI 增加**上架配置模板**：
1. Worker 新增 `listing_templates` 表 + CRUD API（租户隔离，仿 credentials）
2. `submit_draft` 接受 `template_id` → 模板参数注入 envelope.extensions（仅提交副本，不持久化草稿）
3. WebUI 新增「上架配置」管理页（列表/新建/编辑/删除/设默认）
4. 商品编辑页（Products）提交时可选模板；草稿自身 extensions 优先级高于模板（模板只补缺省）

### 1.3 非目标（P0 范围外）
- 图片顺序打乱（worker `_IMG_ORDER` 固定，AI 生图按序生成，暂不可配）
- 图片水印（worker 无此能力）
- 多店铺差异化配置（P1 再做：模板可绑定多店铺不同参数）
- 定时任务（已有 scheduled_at 透传，模板不掺和）
- 批量搬家/自动上下架

## 二、模板字段设计

对标上品帮字段，**裁剪到 worker 真实支持**：

| 模板字段 | 类型 | 默认 | 说明 | worker 消费点 |
|---|---|---|---|---|
| `name` | str | 必填 | 配置名称 | — |
| `description` | str | "" | 备注 | — |
| `platform` | str | "OZON" | 平台（当前仅 OZON） | — |
| `is_default` | bool | false | 默认配置（提交未指定 template_id 时自动使用） | submit_draft |
| `margin_rate` | float | None→0.25 | 利润率 | pricing_node |
| `commission_rate` | float | None→0(自动查) | 佣金率；0=让 worker 自动查店铺真实佣金 | pricing_node |
| `fx_buffer` | float | None→0.05 | 汇率缓冲 | pricing_node |
| `offer_id_prefix` | str | None | 货号前缀（同店铺多批次防重；prepare 层 offer_id 前加 `{prefix}_`） | prepare 层 |
| `follow_type` | str | None→hand | 跟卖方式（hand 防侵权 / api 强制）；非跟卖忽略 | follow_sell_import_node |
| `stock` | int | None | 上架后库存（extensions.stock） | prepare 库存写入 |
| `warehouse_id` | str | None | 仓库（extensions.warehouse_id） | prepare 仓库选择 |

**注入语义**：模板参数只在「草稿 extensions 未显式设置该字段」时注入（模板补缺省，不覆盖草稿已有值）。这样 skill 采集带入的 margin 等保持优先，模板兜底。

**offer_id_prefix 规则**：`new_offer_id = f"{prefix}_{original}"`（如 prefix="W1" → `W1_893731855956`）。**只对新建上架生效，更新模式（update_product_id）忽略**——更新必须保持原 offer_id 不变（重上不变式）。

## 三、Worker 改动

### 3.1 新表 `listing_templates`
```sql
CREATE TABLE IF NOT EXISTS listing_templates (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     TEXT NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    platform      TEXT NOT NULL DEFAULT 'OZON',
    is_default    BOOLEAN NOT NULL DEFAULT FALSE,
    config        JSONB NOT NULL DEFAULT '{}',   -- {margin_rate, commission_rate, fx_buffer, offer_id_prefix, follow_type, stock, warehouse_id}
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_listing_templates_tenant ON listing_templates(tenant_id);
```
- 租户隔离：所有查询带 `tenant_id`
- 默认模板约束：同租户最多 1 个 `is_default=true`（设默认时先清旧默认）
- `config` 字段 whitelist：只接受表内定义的 7 个 key，非法 key 拒绝（防注入）

### 3.2 新文件 `worker/src/services/template_service.py`
- `list_templates(tenant_id)` / `create_template(tenant_id, data)` / `get_template(tenant_id, id)` / `update_template(...)` / `delete_template(...)` / `set_default(tenant_id, id)` / `get_default_template(tenant_id)`
- `apply_template_to_envelope(envelope, template) -> envelope`：深拷贝 envelope，把模板 config 中「草稿 extensions 未显式设置」的字段注入；offer_id_prefix 特殊处理（仅新建，见 §2）
- 校验：margin_rate∈[0,1]、commission_rate∈[0,0.5]、fx_buffer∈[0,0.5]、stock≥0

### 3.3 新路由 `worker/src/routes/templates_routes.py`
仿 credentials_routes（`_authenticate` + tenant_id）：
```
GET    /api/v1/templates            → list[TemplateOut]
POST   /api/v1/templates            → TemplateOut (201)
PATCH  /api/v1/templates/{id}       → TemplateOut
DELETE /api/v1/templates/{id}       → 204
POST   /api/v1/templates/{id}/default → TemplateOut（设默认，清旧默认）
```

### 3.4 `submit_draft` 扩展
`POST /drafts/{id}/submit` 请求体新增可选 `template_id`：
- 有 `template_id` → 校验归属 → `apply_template_to_envelope`
- 无 `template_id` → `get_default_template(tenant_id)` 命中则应用，否则原样
- 更新模式（update_product_id）→ **忽略 offer_id_prefix**（其余字段仍应用）
- graph_payload 用注入后的 envelope；draft_submissions.extensions 快照记录注入后值

### 3.5 schemas 新增
- `ListingTemplateCreate` / `ListingTemplateUpdate` / `ListingTemplateOut` / `ListingTemplateConfig`（whitelist 字段）
- `SubmitDraftRequest` 增加 `template_id: Optional[str]`

## 四、WebUI 改动

### 4.1 新页面 `webui/src/pages/Templates.tsx`
- 路由 `/templates`（Layout 侧边栏新增「上架配置」）
- 列表：名称/备注/默认标记/定价参数摘要（利润率/佣金率/货号前缀）/操作（编辑/删除/设默认）
- 新建/编辑弹窗：名称/备注 + 定价区块（利润率%、佣金率% 或"自动"、汇率缓冲%）+ 货号前缀 + 跟卖方式（hand/api）+ 库存 + 仓库
- 删除确认（仿 Stores 吊销确认）

### 4.2 编辑页（Products.tsx）集成
- 提交栏新增「上架配置」下拉（加载 /templates；默认选中 is_default 模板）
- `submitDraft` / `submitDraftUpdate` 增加 `template_id` 参数
- 商品编辑页已有 extensions（margin 等）保持优先；模板仅兜底（worker 侧语义）

### 4.3 路由
`App.tsx` 加 `<Route path="templates" element={<Templates />} />`；Layout 导航加菜单项。

## 五、测试计划

### Worker（pytest）
1. `test_template_service.py`：CRUD + 租户隔离（A 租户看不到 B）+ 设默认清旧 + config whitelist 拒绝非法 key + 数值校验
2. `test_template_apply.py`：注入语义（草稿有值不覆盖/无值补缺省）；offer_id_prefix 仅新建（update_product_id 忽略）
3. `test_templates_api.py`：路由鉴权（无 token 401）、CRUD 端点、默认端点
4. `test_submit_draft_template.py`：submit_draft 带 template_id 应用、无 template_id 用默认模板、更新模式忽略 prefix

### WebUI
- build + tokens:validate 绿
- 手动冒烟：新建模板 → 编辑页选择 → 提交 → task payload 校验 extensions 注入

## 六、验收标准（DoD）
1. Worker CRUD + 默认模板 + 注入语义全部通过 pytest（新增 ≥10 用例）
2. WebUI 模板页可新建/编辑/删除/设默认，编辑页可选模板提交
3. 提交后 task payload 的 extensions 含模板注入的 margin/commission/fx/prefix
4. 更新模式（在线商品编辑）下 offer_id 不变（前缀被忽略）
5. 全量回归 worker 976 + skill 493 不破

## 七、实施顺序
T0 建表 + template_service → T1 路由 + schemas → T2 submit_draft 集成 → T3 WebUI 模板页 → T4 编辑页下拉集成 → T5 版本+CHANGELOG+全量回归
