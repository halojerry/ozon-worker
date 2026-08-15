# WebUI + Skill 双向互通执行计划 v1（MVP）

> 日期: 2026-08-14
> 范围: 全仓（worker 后端 + webui 前端 + skill 采集箱）
> 依据: hyperplan 对抗性规划（5 成员 × 3 轮）+ 竞品实测 15 页面（上品帮/毛子ERP）+ 代码库 6 轮调研

---

## 1. 需求与目标

### 1.1 用户需求

做 WebUI 让用户也能操作和查看，且 **WebUI 与 skill 双向互通**（用户既可用 skill 也可用 webui，两边都能操作和查看）。凭证托管（P2）纳入第一版。用户强调「在计划前把所有该考虑的事情考虑清楚」。

### 1.2 收敛架构（已辩护，勿再辩论）

- **单向权威 + 双向可见**：skill=采集权威（CDP）、webui=审阅/编辑权威、worker=执行权威（状态机仲裁）。两者永不直接通信，只读写 worker 状态。最终一致 + 2-5s 轮询。
- **worker 是业务层中心**：React SPA（Vite）静态托管在 worker 域名，零 CORS。不建独立 BFF/DB、不用 Next.js/htmx。
- **独立草稿表**（非复用 `ozon_product_tasks`）：规避不可变 sku_key 换店铺删插 + 唯一索引污染。
- **凭证三层防御**：掩码 + AES-256-GCM 列级加密（env 主密钥 ~20 行）+ 轮换提醒，credential_type 预留 OAuth。
- **生图快照 = 完整节点 Input schema JSONB**，键改为 `(task_id, slot, version)`。
- **两个缓存陷阱统一修复**：version++ 显式重生成 + image_parent_task_id 回溯。
- **三段式链路**（竞品验证）：采集箱 → 商品编辑页（手动+AI）→ 上架至OZON。

### 1.3 MVP 范围

1. 店铺管理（凭证三层防御，P2 纳入）
2. 采集箱（独立草稿表 + skill --to-box + 确认提交）
3. 商品编辑页（手动+AI 双模式，对标上品帮 editGoods）
4. 任务进度页（上架记录视图 + 异常重上）
5. 生图工作台（AI商品套图：原图≤3/卖点AI帮写/7类型数量可调/额度前置检查）
6. 更新在线商品（全量重传）

**不做（第二版起）**：MCP / 完整参数快照 / KMS vault / 独立 BFF / 审计日志 / 利润报表 / 富内容配置 / 定时上架调度器 / 批量搬家。

### 1.4 目录结构 + 架构约束（路由薄、service 厚）

**核心约束**：新增 WebUI 端点一律进 `routes/` + `services/` 分层，**禁止在 main.py 内联业务逻辑**（main.py 已 2100 行，内联 = 未来抽 BFF 必须重写）。业务层是唯一实现，REST/MCP/WebUI 都是门面。

```
worker/src/
├── main.py                  # 入口（仅路由注册 + 薄 handler 调 service）
├── api/                     # 现有：errors.py / schemas.py（Pydantic 契约，新增端点 schema 在此）
├── routes/                  # 🆕 路由薄层：参数解析 + 鉴权 + 调 service（无业务逻辑）
│   ├── __init__.py          #   注册到 FastAPI（v1 router）
│   ├── credentials_routes.py
│   ├── drafts_routes.py
│   ├── tasks_routes.py
│   ├── images_routes.py
│   └── products_routes.py
├── services/                # 🆕 业务厚层：唯一实现，被 routes 调用、被未来 MCP/BFF 复用
│   ├── __init__.py
│   ├── credential_service.py   # 加密/掩码/轮换/校验（T2+T5 逻辑）
│   ├── draft_service.py        # 草稿 CRUD + 提交 + C5 重复校验 + 凭证剥离（T6 逻辑）
│   ├── task_service.py         # 任务列表 + 状态查询（T8 逻辑）
│   ├── image_service.py        # 生图重生成 + params 快照 + 更新在线商品（T7a+T14 逻辑）
│   └── ai_field_service.py     # 单字段 AI 重生成（T14b 逻辑）
├── graphs/                  # 现有：LangGraph 节点（不动）
└── utils/                   # 现有：pricing/attributes/category 等纯工具（复用）
```

```
webui/                       # 🆕 React SPA（Vite），静态托管在 worker 域名 /app
├── src/
│   ├── api/                 #    Axios 客户端（openapi-typescript 生成的类型）
│   │   └── client.ts        #    从 worker openapi.json 生成（单一真相源）
│   ├── pages/               #    采集箱/商品编辑/店铺管理/任务进度/生图工作台/登录
│   ├── components/          #    变体表格/图片网格/确认弹窗等
│   └── stores/              #    前端状态（token/店铺/当前草稿）
├── vite.config.ts
└── package.json
```

**分层规则**（写入代码评审门）：
- `routes/*` 只做：参数解析 → 鉴权（`_authenticate_token`）→ 调 `services/*` → 返回。
- `services/*` 是唯一业务逻辑：调 DB / `utils/*` / `graphs` 节点能力 / Ozon API。**未来抽独立 BFF = 把 services/ 包搬走 + 加 HTTP 门面，零手术**。
- `api/schemas.py` 是全部新端点的 Pydantic 契约（OpenAPI 自动生成，前端 `openapi-typescript` 消费）。
- main.py **只注册路由**，不新增业务函数。

**图片显示约定**（复用 1.4 之前调研结论）：WebUI 直接引用 URL（COS/1688 alicdn/Ozon），**DB 不存二进制**（`task_generated_images` 只存 URL 元数据）。前端必须做**图裂占位 + 提示**（COS 生命周期删除 / alicdn 防盗链 / 竞品图时效三类失效）。

---

## 2. 关键契约（冻结，编码前定稿）

### C1 采集箱（两表模型：草稿保留 + 多次提交）

```sql
-- 永久草稿（文档），手动删除前保留，可被多次引用（多店铺）
CREATE TABLE product_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(50) NOT NULL,          -- user_id from _authenticate_token
    payload JSONB NOT NULL,                   -- envelope {draft,source,extensions}; NO raw credentials
    source TEXT NOT NULL DEFAULT 'skill',     -- 'skill' | 'webui'
    version INT NOT NULL DEFAULT 1,           -- optimistic concurrency; 编辑页修改 → version++
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_product_drafts_tenant ON product_drafts (tenant_id, updated_at DESC);

-- 提交记录：每次「上架至OZON」= 一行；换店铺 = 新行，draft.id 永不变
CREATE TABLE draft_submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    draft_id UUID NOT NULL REFERENCES product_drafts(id) ON DELETE CASCADE,
    credential_id UUID,                        -- NULL → 用 is_default=true 店铺
    store_client_id TEXT,                      -- 提交时选定的店铺 client_id
    extensions JSONB,                          -- 提交时定价/仓库/库存快照
    status TEXT NOT NULL DEFAULT 'pending',    -- pending/uploading/published/failed（毛子「上架状态」列）
    submitted_task_id TEXT,                    -- ozon_product_tasks.id
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_draft_submissions_draft ON draft_submissions (draft_id);
```

**状态机**：无独立草稿状态机（草稿是文档）；`draft_submissions.status` 驱动上架状态列（未上架=无行 / 已上架=published / 失败=failed）。

### C1b 商品↔任务索引（BLOCKER 3 补齐）

```sql
CREATE TABLE product_task_index (
    product_id VARCHAR(64) PRIMARY KEY,        -- Ozon product_id（上传成功后回填）
    tenant_id VARCHAR(50) NOT NULL,
    offer_id VARCHAR(128) NOT NULL,            -- 信封 sku_id / follow_{id}
    task_id UUID NOT NULL REFERENCES ozon_product_tasks(id),
    credential_id UUID REFERENCES credentials(id),  -- 定位店铺
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_pti_tenant_offer ON product_task_index (tenant_id, offer_id);
```

**写入时机**：T14 上传成功 + `ozon_status` approved 时回填；查询用于 T14「① product_task_index 定位商品」。
**与 `ozon_product_tasks` 去重关系**：`uq_ozon_product_tasks_tenant_sku`（partial，status IN pending/running）是「任务入队去重」；`product_task_index` 是「商品↔任务定位」。职责不同，**都保留**。

### C2 凭证表（三层防御 + 绑定弹窗字段）

```sql
CREATE TABLE credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(50) NOT NULL,
    ozon_client_id TEXT NOT NULL,                 -- 半公开
    ozon_api_key_enc BYTEA NOT NULL,              -- AES-256-GCM 密文（env CREDENTIAL_MASTER_KEY）
    api_key_masked TEXT NOT NULL,                 -- "****abcd"（仅后 4 位）
    shop_name TEXT,                               -- 店铺名称（毛子绑定弹窗）
    currency TEXT NOT NULL DEFAULT 'CNY',         -- CNY/RUB
    is_default BOOLEAN NOT NULL DEFAULT false,    -- 「默认上传产品的店铺」radio
    credential_type TEXT NOT NULL DEFAULT 'api_key', -- 'api_key' | 'oauth'（预留）
    status TEXT NOT NULL DEFAULT 'active',        -- active/revoked
    last_validated_at TIMESTAMPTZ,
    last_rotated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX uq_credentials_tenant_client ON credentials (tenant_id, ozon_client_id);
CREATE UNIQUE INDEX uq_credentials_default ON credentials (tenant_id) WHERE is_default;
```

**规则**：API key 永不完整回显（仅 api_key_masked）；`get_decrypted(id)` 仅服务端调用；轮换 = 新行 + 旧行 revoked；cipher = `worker/src/utils/credential_cipher.py`（~20 行 cryptography AES-256-GCM，env `CREDENTIAL_MASTER_KEY` 32 字节，随机 nonce/值，AAD = tenant_id:ozon_client_id）。

### C3 生图缓存迁移

```sql
ALTER TABLE task_generated_images
  ADD COLUMN version INTEGER NOT NULL DEFAULT 1,
  ADD COLUMN params JSONB,                        -- 完整节点 Input schema 原样（如 Scene1Input 7 字段）
  ADD COLUMN image_parent_task_id TEXT;           -- resubmit 血缘；区别于任务级 payload.parent_task_id（main.py:1689）
-- PK 改为 (task_id, slot, version)
```

**语义**：
- `get_image(task_id, slot, version=None)` → 最新或指定版本；正常管线读路径不变。
- **重生成**（webui 按钮）：`force_regen` → 节点绕过缓存读 + `save_image(..., version=prev+1, params=<node Input>)`。
- **resubmit**：新任务 `payload.parent_task_id`（任务级，main.py:1689）携带原 task_id；缓存查询自身 miss → 读 `image_parent_task_id`（图缓存列，存原 task_id）→ 查父行（一层）→ 否则生图。无静默 reburn。⚠️ 两处 parent_task 语义不同（任务血缘 vs 图片血缘），勿混用。

### C3b image_gen_plan 受限映射（BLOCKER 1 修复）

> 现有 10 个**硬编码**生图节点（graph.py:76-85）+ 硬编码边（:235-245）：`white_bg_gen / multi_angle_gen / variant_primary_loop / main_image_gen / detail_gen / social_proof_gen / scene_1/2/3_gen / comparison_gen`。现有 slot（model.py:407）：`main/white_bg/multi_angle/detail/social_proof/comparison/scene_1/2/3/variant_{idx}`。**没有 selling_point / material / size 节点**。

`image_gen_plan` = **现有 slot 子集选择 + 计数**（type→count），只控制「执行/跳过」，**不新增 slot、不做 graph 层重构**：

| UI 类型 | slot 映射 | 说明 |
|---|---|---|
| 白底图 | `white_bg` | 现有节点 |
| 场景图 | `scene_1/2/3` | 计数 0–3 |
| 卖点图 | `main_image` | 主图兼卖点 |
| 细节图 | `detail` | 现有节点 |
| 对比图 | `comparison` | 现有节点 |
| 社交证明 | `social_proof` | 现有节点 |
| 多角度 | `multi_angle` | 现有节点 |
| 材质图 / 尺寸图 | **无现成节点** | v1 置灰，不提供 |

- **跳过机制**：节点硬编码 → `image_gen_plan` 只能控制「哪些 slot 执行」；跳过 = 生图节点前置条件检查（plan 无该 slot → 节点直接返回 None，不调生图 API）。
- **默认 plan** = 全 10 张（向后兼容，管线行为不变）。

### C4 信封 extensions 新字段

```
extensions: {
  margin_rate, commission_rate, fx_buffer, follow_sell, follow_type,   # 现有
  warehouse_id:   str | null,   # 选择仓库（上品帮 editGoods）
  stock:          int | null,   # 库存数量（每 SKU）
  scheduled_at:   str | null    # 定时上架 ISO-8601（⚠️ v2；v1 仅 UI 预留 + 字段透传）
}
```

**Flow**：skill envelope → `product_drafts.payload.extensions` → (submit) 快照进 `draft_submissions.extensions` → worker `prepare_ozon_upload_node` 透传到 Ozon `/v3/product/import`。`warehouse_id`/`stock` 透传归入 T6（submit 路径权威）+ T14（Ozon 侧消费）。

### C5 多店铺重复商品约束（BLOCKER 2 修复：两层规则）

上品帮上架配置页警告：**「请勿在OZON配置中添加多个相同个人中心下的店铺，OZON不允许个人中心的店铺存在相同的商品，上架后会审核失败，提示重复商品」**。

**⚠️ 个人中心维度的准确语义未经 Ozon 文档/实测确认 → 拆两层：**

- **v1 保守规则（可落地）**：
  1. **per-store 校验**：submit 前按确定性 `offer_id` 查**目标店铺** `/v1/product/info/list`，已存在 → **409「重复商品：目标店铺已存在相同商品」**。Ozon API 错误 fail-open（log warning 不阻塞，对齐 auth/balance fail-open 先例）。
  2. **跨店铺提醒**：submit 时检查该 draft 是否已提交到**其他店铺** → 若存在，弹确认「该商品已上架到店铺X，确认继续上架到店铺Y？注意 Ozon 个人中心可能拒绝重复商品」。**不硬拦截跨店**（约束未实测确认）。
- **v2 完整规则（需确认后）**：确认 Ozon「个人中心跨店重复」真实语义后，实现跨店硬拦截（credentials 表加个人中心维度字段）。

### C6 新端点（全部 `/api/v1`，token body 或 Bearer）

| 端点 | 方法 | 用途 |
|---|---|---|
| `/api/v1/credentials` | GET/POST | 列表（掩码）/创建（加密） |
| `/api/v1/credentials/{id}` | PATCH/DELETE | 轮换/吊销 |
| `/api/v1/credentials/{id}/validate` | POST | 解密 → Ozon probe → 有效性 |
| `/api/v1/drafts` | GET/POST | 列表 / 创建（skill --to-box 目标） |
| `/api/v1/drafts/{id}` | GET/PATCH | 读取 / 编辑（webui 权威，version 乐观锁） |
| `/api/v1/drafts/{id}/submit` | POST | 重复校验 → 凭证注入 → submission 行 + 入队 |
| `/api/v1/drafts/{id}/ai/{field}` | POST | 单字段 AI 重新生成（T14b） |
| `/api/v1/tasks` | GET | 任务列表（租户隔离 + 分页） |
| `/api/v1/tasks/{id}/images` | GET | 10 slot + versions + params |
| `/api/v1/tasks/{id}/images/{slot}/regen` | POST | 强制重生成（version++） |
| `/api/v1/products/{product_id}/update_images` | POST | 全量重传（存活检查 + 重新审核 + 索引） |

### C7 skill --to-box

`graph`/`follow` 加 `--to-box` flag：替代 `submit_envelope` → POST `/api/v1/drafts`（worker 剥离凭证 → credential_id + 加密存储，只留信封）。打印 `draft_id` + 「已入采集箱，请到 WebUI 认领」。**冷启动**：老 skill 无 `--to-box` → 直接 submit（不变）+ WebUI 横幅「检测到旧版 skill 直接上架，升级后可用采集箱」。

---

## 3. 任务依赖图

| 任务 | 依赖 | 理由 |
|---|---|---|
| T1 DB 迁移 | — | 地基（4 表 + image ALTER） |
| T2 credential_cipher | — | 独立 crypto 工具 |
| T3 鉴权门（4 端点） | — | 复用 `_authenticate_token`/`rate_limiter` |
| T4 前端脚手架 | — | Vite SPA + 登录 + 静态托管 |
| T5 凭证 API | T1,T2 | 表 + cipher 才能存/解密/掩码 |
| T6 草稿 API | T1,T2,T3 | 草稿/提交两表 + 凭证剥离 + C5 校验 + warehouse/stock |
| T7a 生图缓存版本化 | T1 | ALTER + cache + 10 节点 + regen + image_parent_task_id 回溯 |
| T8 任务列表端点 | T3 | 读队列，无新 schema |
| T14b 草稿 AI 单字段端点 | T6 | 复用 LLM/翻译节点 |
| T7b image_gen_plan 类型选择 | T7a | 前置：C3b 映射（BLOCKER1 已冻结）；跳过机制 |
| T9 skill --to-box | T6 | POST /drafts + 冷启动降级 |
| T10 采集箱页面 | T6,T4 | draft 端点消费 |
| T10b 商品编辑页 | T6,T4,T14b | draft 字段镜像 + 每字段 AI |
| T11 店铺管理页面 | T5,T4 | 凭证端点（+3 字段） |
| T12 任务进度页面 | T8,T4 | 任务列表 + progress |
| T13 生图工作台页面 | T7a,T7b,T8,T4 | images + regen + image_gen_plan + 余额 |
| T14 更新在线商品端点 | T1,T2,T7a | 索引 + 解密 + 重传 |
| T15 E2E + 契约测试 | T9..T14 | 全路径 |
| T16 文档 + 版本 | T15 | 最后 |

## 4. 并行执行图

```
Wave 1（无依赖，4 并行）:
├── T1  DB 迁移（product_drafts + draft_submissions + credentials(+3) + product_task_index + image ALTER）
├── T2  credential_cipher
├── T3  鉴权门 4 端点
└── T4  React SPA 脚手架 + 登录 + 静态托管

Wave 2（Wave 1 后，5 并行）:
├── T5   凭证 CRUD + validate     (T1,T2)
├── T6   草稿 + submissions API   (T1,T2,T3)
├── T7a  生图缓存版本化 + regen   (T1)
├── T8   任务列表端点              (T3)
└── T14b 草稿 AI 单字段端点        (T6，波内靠后)

Wave 3（Wave 2 后，6 并行）:
├── T7b  image_gen_plan 类型选择  (T7a)
├── T9   skill --to-box           (T6)
├── T10  采集箱页面                (T6,T4)
├── T10b 商品编辑页面              (T6,T4,T14b)
├── T11  店铺管理页面              (T5,T4)
└── T12  任务进度页面              (T8,T4)

Wave 4（Wave 3 后，2 并行）:
├── T13 生图工作台页面             (T7a,T7b,T8,T4)
└── T14 更新在线商品端点           (T1,T2,T7a)

Wave 5（Wave 4 后）:
├── T15 E2E + 契约测试 + 安全评审门
└── T16 文档 + 版本四源            (T15)

关键路径: T1→T6→T14b→T10b→T15→T16 / T1→T7a→T7b→T13→T15→T16 / T1→T7a→T14→T15→T16
提速 vs 串行: ~55%
```
> **W2 说明**：T7 拆为 T7a（缓存版本化，Wave 2）+ T7b（image_gen_plan，Wave 3，仅依赖 T7a）。T7b 放 Wave 3 而非 Wave 4 是为让 T13（Wave 4）依赖它而无需同波内排序；BLOCKER 1 映射已在 C3b 冻结。T6 不拆（dup check 属 C5 一部分）。
```

---

## 5. 任务详情（含验收门 / 委派 / skills）

### T1 DB 迁移
- **内容**：`ProductDraft`/`DraftSubmission`/`Credential`/`ProductTaskIndex` 进 `model.py`；ALTER `task_generated_images`（version/params/parent_task_id，新 PK）；`init_data.py` 幂等建表 + `migrate_webui_v1.py` 存量迁移（ADD COLUMN IF NOT EXISTS + version=1 回填）。
- **委派**：`unspecified-high` + [`programming`]
- **验收**：本地 Docker `init_data.py` 幂等建 4 表；迁移二次运行 no-op；`tests/test_webui_migrations.py` 绿。

### T2 credential_cipher
- **内容**：`encrypt(value, aad)/decrypt(ct, aad)/mask(value)`；env `CREDENTIAL_MASTER_KEY`；随机 12-byte nonce 前置。
- **委派**：`ultrabrain` + [`programming`]
- **验收**：round-trip；错 key/篡改 → GCM 认证失败（非静默垃圾）；`mask("sk-abc123XYZ9")=="****XYZ9"`；无明文 key 进日志（caplog）。

### T3 鉴权门 4 端点
- **内容**：`/run`/`stream_run`/`node_run`/`v1/chat/completions` 加 `_authenticate_token` + `rate_limiter.check()`；nginx deny 兜底。
- **委派**：`quick` + [`programming`]
- **验收**：4 端点无/空 token → 401，超限 → 429，有效 token 通过；`tests/test_auth_gates.py` 绿。

### T4 前端脚手架
- **内容**：`webui/` Vite React TS（结构见 1.4）：登录（token → Bearer）、路由、布局、Axios 拦截器；`src/api/client.ts` 由 worker `openapi.json` 用 openapi-typescript 生成（单一真相源）；FastAPI `/app` StaticFiles + SPA fallback。
- **委派**：`visual-engineering` + [`frontend`]
- **验收**：`npm run build` 产物被 `http://localhost:8080/app/` 服务无 CORS 错误；登录有效 token 持久化跳首页；`client.ts` 类型与 worker openapi.json 对齐（改端点类型自动变）。

### T5 凭证 CRUD + validate（W3 隔离）
- **内容**：C6 凭证端点；创建加密 + mask；列表仅掩码；PATCH 轮换（revoke 旧 + insert 新）；validate 解密 → `/v1/product/info/list` probe；DELETE 软删。
- **委派**：`unspecified-high` + [`programming`]
- **验收**：`tests/test_credentials_api.py` 绿：断言响应 JSON **永不出现**明文 key；validate 坏 key → valid:false + reason；**租户隔离断言：A 看不到 B 的凭证（列表按 tenant_id 过滤）**。

### T6 草稿 CRUD + submit（+ C5 校验 + 凭证剥离 + 透传 + 隔离）
- **内容**：POST /drafts 收 GraphInput → 剥离凭证存 credential_id → envelope-only payload；GET/PATCH（version 乐观锁，409）；POST /drafts/{id}/submit → **C5 两层**（per-store 409 + 跨店确认标记）→ 解密凭证 → 重建 GraphInput → `task_processor.submit_task` → submission 行 + status。warehouse_id/stock 透传进 extensions 快照。
- **委派**：`deep` + [`programming`]
- **验收**：`tests/test_drafts_api.py` 绿：创建剥离凭证（payload 无 api_key）；PATCH stale version → 409；submit 产生 pending 任务行 + submission 行；**per-store 重复 → 409「重复商品」**；**跨店 → 返回 confirm 标记（不硬拦）**；Ozon 错误 fail-open；换店铺第二次 submit → 新行且 draft.id 不变；**租户隔离断言：A 看不到 B 的草稿**。

### T7a 生图缓存版本化 + params + image_parent_task_id 回溯
- **内容**：`task_image_cache.py` get/save 支持 version/`image_parent_task_id`；save 写 params（Input schema 原样）；10 节点改调用；regen 端点（force + version++）；resubmit 血缘回溯（`image_parent_task_id` 存原 task_id，与任务级 `payload.parent_task_id` main.py:1689 区分）。
- **委派**：`ultrabrain` + [`programming`]
- **验收**：`tests/test_image_cache_version.py` 绿：(a) force_regen → 新行 version+1 新 URL（无静默缓存命中）；(b) resubmit + `image_parent_task_id` → 复用父图（断言 `call_mxou_image_api` **不被调用**）；(c) 正常 retry 同 task_id 命中缓存（回归保留）。

### T7b image_gen_plan 类型选择（受限映射，BLOCKER 1 修复）
- **内容**：按 C3b 冻结映射，`image_gen_plan`（type→count）注入节点 input；10 节点加**前置条件检查**（plan 无该 slot → return None）；默认 = 10 张。**不新增 selling_point/material/size 节点、不改 graph 层结构**。⚠️ **plan 校验规则（Momus W1）**：plan 必须含 Phase1（`white_bg` 或 `multi_angle`）——Phase2 节点依赖 Phase1 输出作参考图（graph.py:235-245），仅选 Phase2 类型 → 拒绝并提示「需至少包含白底图或多角度图」。
- **委派**：`unspecified-high` + [`programming`]
- **验收**：`tests/test_image_gen_plan.py` 绿：plan {white_bg:1, scene_1:1} → **仅执行这 2 节点，其余跳过**（断言其余节点未调生图 API）；**仅 Phase2 类型 plan（如 {scene_1:1}）→ 拒绝（plan 校验规则）**；默认 plan → 10 张回归；确认无新增节点。

### T8 任务列表端点
- **内容**：GET /tasks?limit=&offset= 按 tenant 隔离，返回 status/progress/product_summary。
- **委派**：`quick` + [`programming`]
- **验收**：`tests/test_tasks_api.py` 绿：租户隔离（A 看不到 B）、分页、progress 字段。

### T14b 草稿 AI 单字段端点
- **内容**：POST /drafts/{id}/ai/{field}，field ∈ {title,description,attributes,brand,tags,...}；复用 `call_mxou_chat_api` + 翻译路径；返回 RU 值**只读**（前端决定 PATCH 保存）。
- **委派**：`unspecified-high` + [`programming`]
- **验收**：`tests/test_draft_ai_endpoint.py` 绿：每 field 返回非空 RU 无中文/拉丁残留；未知 field → 400；未认证 → 401；断言 mock `call_mxou_chat_api` 被调用（复用非新客户端）。

### T9 skill --to-box
- **内容**：cli.py graph/follow 加 `--to-box` → `submit_draft()`（cloud_probe.py 新函数，POST /drafts）；404 → 降级 submit_envelope + 提示。改明文分发文件（cloud_probe.py 已在 COPY_FILES）。
- **委派**：`unspecified-high` + [`programming`]
- **验收**：`python3.12 scripts/cli.py graph --url <URL> --to-box` 命中 /drafts 打印 draft_id；无 flag 仍直接提交；`test_to_box.py` + `test_compile_lists.py` 绿。

### T10 采集箱页面
- **内容**：列 = ☐|图片|产品名称|采集价格（variants → 区间 ¥0.1-¥3.4）|sku数量|采集来源|备注|上架状态（draft_submissions）|创建/更新时间|操作（编辑上架/删除）；工具栏批量删除/清空采集箱；`[编辑上架]` → T10b（毛子模型，采集箱不选店铺）。
- **委派**：`visual-engineering` + [`frontend`]
- **验收**：visual-qa：区间价正确；上架状态列反映 submissions；清空/删除级联删 submissions。

### T10b 商品编辑页面（上品帮 editGoods 版）
- **内容**：三区块锚点导航（主要信息/产品属性/变体设置）。主要信息：上架店铺下拉/产品类目/品牌（无品牌）/标题（3 AI 按钮+加号）/包装重量克🤖/包装长宽高mm×3🤖。产品属性：型号名称（合并提示）/简介（1688 属性自动拼接）/主题标签/JSON富内容/填写更多属性。变体设置：变体表格（列=图片/视频/货号一键生成/我的售价/划线价/最低价/长度/颜色/颜色名称/宽度/类目属性 + 同首行按钮；工具栏自动颜色样本/批量水印/批量翻译/批量删除/添加变体）。其它：货源地址/货源备注/选择仓库（extensions.warehouse_id）/库存数量（每SKU）。底部：保留采集数据/AI填写产品信息/AI商品套图（→T13）/引用模板/关闭/定时上架（v1 stub，persist scheduled_at）/立即上架。
- **委派**：`visual-engineering` + [`frontend`]
- **验收**：visual-qa 三区块渲染；同首行批量填充；仓库/库存写入 PATCH body；立即上架 → submission 行 + 409 重复商品透出；**立即上架收到跨店 confirm 标记（C5 v1）→ 弹确认框「该商品已上架到店铺X，确认继续上架到店铺Y？」→ 用户确认后二次提交（Momus W2 契约闭环）**；定时上架持久化 scheduled_at 无调度器。

### T11 店铺管理页面
- **内容**：毛子绑定弹窗式：shop_name/currency（默认 CNY）/is_default radio；列表仅掩码；添加/轮换/吊销/立即校验；轮换提醒 banner（last_rotated_at）。
- **委派**：`visual-engineering` + [`frontend`]
- **验收**：仅掩码显示；validate 实时 valid/reason；设置 is_default 清旧默认（409 第二默认被唯一索引拒）；轮换产生新掩码 + revoke 旧。

### T12 任务进度页面（上架记录视图）
- **内容**：筛选（平台/账号/店铺/状态/货号/时间范围/方式/竞品代码）；列 = ☐|商品信息|平台,店铺,账号|上架状态|库存状态|最低价状态|售价|划线价|货源信息|竞品代码|上架方式|操作时间|上架时间|操作；工具栏查询/**异常重上**（→ resubmit_task）/**释放帮豆**（v2）/**批量搬家**（v2 入口）/批量操作；今日上架数量 N。
- **委派**：`visual-engineering` + [`frontend`]
- **验收**：异常重上 on failed/rejected → 调 resubmit_task 新任务行；新列从 product_summary/result 渲染。

### T13 生图工作台页面（AI商品套图规格，W4 移除积分）
- **内容**：①商品原图（添加原图，最多 3 张）②商品卖点&要求（AI帮写 1 次调用 + textbox：名称/卖点/人群/场景/参数）③商品图配置（已选 N 最少 2 张；**现有 slot 类型 ±1** → image_gen_plan，材质/尺寸置灰）④富内容配置 16 种（**v2**，v1 置灰）⑤一键生成 → **生成前显示 MXOU 余额（`_check_mxou_balance`）+ 预计消耗（每图 1 次调用，N 张 = N 次）+ 确认弹窗** ⑥效果预览（商品图下载/富内容/富内容图文，富内容 v2）。
- **委派**：`visual-engineering` + [`frontend`]
- **验收**：①上限 3 张；③最少 2 张 + 数量变化产生合法 image_gen_plan（对齐 T7b 测试）；⑤余额 ≤0 阻止生成、显示预计消耗 N 次调用（**无积分概念**）；成功渲染分类型结果。

### T14 更新在线商品端点（W1 引用修正）
- **内容**：POST /products/{product_id}/update_images：①product_task_index 定位（C1b）②URL 存活检查（复用 image_quality_evaluator GET+Range）③/v3/product/import 全量重传（product_id+offer_id + 新 images；**验证/删除死代码 `skill/scripts/lib/ozon_api.py:740 update_existing_product`——skill 侧未接线死函数，worker 侧无此函数**）④status → pending_moderation + 「重新审核中」UI ⑤索引行写入（upload 成功 + ozon_status approved 路径挂钩）。
- **委派**：`deep` + [`programming`]
- **验收**：`tests/test_update_product.py` 绿：重传 payload 含 product_id/offer_id/新 images；死 URL 过滤；status 迁移 pending_moderation；索引行写入（C1b）；skill 死代码验证或删除。

### T15 E2E + 契约测试 + 安全评审门
- **内容**：本地 Docker 全路径（skill --to-box → webui 认领 → 编辑 → 上架 → 进度 → 重生成 → 更新在线）；轻量 security-review 门（凭证/草稿/鉴权面）；**架构评审门（1.4 约束）：main.py 无新增业务函数（diff 检查 routes/services 分层）**。
- **委派**：`unspecified-high` + [`programming`, `playwright`]
- **验收**：E2E 绿（无云访问）；worker 691 / skill 487 回归全绿；安全评审无 P0；**架构评审通过（新增端点全部走 routes/services，main.py 仅注册路由）**。

### T16 文档 + 版本四源
- **内容**：CONTRACT-v4.md 新端点 + draft/credential 契约；`docs/WEBUI.md`；CHANGELOG；版本四源统一。
- **委派**：`writing` + [`git-master`]
- **验收**：四源一致；frontmatter 校验过；CHANGELOG 有 entry。

---

## 6. 风险登记表

| 风险 | 缓解 | 验证 |
|---|---|---|
| 缓存陷阱 A：重生成静默失效 | version++ + force_regen（T7a） | test_image_cache_version.py 新行新 URL |
| 静默 reburn：resubmit 烧 10+ 张 | image_parent_task_id 回溯（T7a） | 断言 call_mxou_image_api 不被调用 |
| 4 个裸奔端点被滥用烧额度 | 鉴权门（T3）+ nginx deny | test_auth_gates.py 401/429 |
| 凭证泄露（DB dump/端点回显） | AES-GCM + 掩码 + 不可导出 + 轮换（T2/T5） | test_credentials_api.py 明文 grep + tamper 测试 |
| 冷启动：老 skill 无 --to-box | 降级直接 submit + 横幅（T9） | test_to_box.py 404 fallback |
| 改图触发重新审核 | status → pending_moderation + UI（T14） | test_update_product.py status 断言 |
| 换店铺破坏草稿身份 | 独立草稿表 + submission 多行（T6） | 换店铺测试 draft.id 不变 |
| raw api_key 嵌 draft payload | POST /drafts 剥离 + credential_id（T6） | test_drafts_api.py payload 无 key |
| 多店铺同商品审核失败 | C5 重复校验（T6） | 409「重复商品」测试 |
| 生图烧额度 | T13 余额 + 预计消耗前置检查（无积分） | 余额 ≤0 阻止生成 |
| 固定 10 张 vs 可调需求（无新节点） | T7b image_gen_plan 现有 slot 子集 + 跳过 | test_image_gen_plan.py 回归 + 跳过断言 |
| 跨租户越权（凭证/草稿） | tenant_id 过滤（T5/T6） | 隔离断言 A 看不到 B |
| 跨店同商品审核失败（个人中心约束未确认） | C5 v1：per-store 409 + 跨店提醒（不硬拦）；v2 硬拦 | 409「重复商品」+ 跨店 confirm 标记测试 |
| warehouse_id/stock 缺失字段 | extensions 契约 + T6/T14 透传 | E2E 断言达 Ozon payload |
| 定时上架未实现 | v1 stub + v2 调度器 | scheduled_at 持久化 |
| product_id 7 天归档后不可定位 | product_task_index 表（C1b/T1/T14） | test_update_product.py 索引查找 |
| main.py 业务逻辑膨胀（未来 BFF 难拆） | 1.4 架构约束：routes/services 分层，main.py 仅注册路由 | T15 架构评审门 diff 检查 |
| WebUI 图片失效（COS 生命周期/alicdn 防盗链/竞品图时效） | 前端图裂占位 + 提示；task_generated_images 只存 URL 元数据 | visual-qa 图裂占位断言 |

---

## 7. 测试策略

**现有回归防线**（必须保持绿）：worker 691 / skill 487 / `test_compile_lists.py`（14 模块不变式）/ `test_contract_attr_consistency.py` / `test_cache_versioning.py`（缓存工作blast radius）。

**新测试（TDD，每任务先写）**：test_webui_migrations（T1，断言 product_task_index + image_parent_task_id）、test_credential_cipher（T2）、test_auth_gates（T3）、test_credentials_api（T5，含跨租户隔离）、test_drafts_api（T6，含重复商品 409 + fail-open + 跨店 confirm + 隔离）、test_image_cache_version（T7a）+ test_image_gen_plan（T7b，含跳过断言）、test_tasks_api（T8）、test_draft_ai_endpoint（T14b）、test_to_box（T9）、test_update_product（T14）、test_webui_e2e（T15）。

**本地纪律**（AGENTS.md）：本地测试前清 pending/failed/running 任务表；worker 用 skill venv python + 本地 PG 5433；skill 测试 Docker python:3.12-slim。

---

## 8. Commit 策略（原子，19 个，中文，feat/webui-* 分支）

```
T1   feat(worker): WebUI 数据层迁移（product_drafts/draft_submissions/credentials/product_task_index + 生图缓存 version/params/image_parent_task_id）
T2   feat(worker): credential_cipher AES-256-GCM 列级加密 + 掩码工具
T3   fix(worker): 为 /run /stream_run /node_run /v1/chat 补齐鉴权与限流
T4   feat(webui): React SPA 脚手架 + 登录 + 静态托管
T5   feat(worker): 凭证 CRUD + 校验端点（shop_name/currency/is_default + 掩码回显/轮换 + 租户隔离）
T6   feat(worker): 采集箱草稿端点（凭证剥离 + 草稿/提交记录两表分离 + per-store 重复校验 + 跨店确认 + warehouse_id/stock 透传 + 租户隔离）
T7a  feat(worker): 生图缓存 version++ 显式重生成 + params 快照 + image_parent_task_id 回溯
T8   feat(worker): 任务列表端点（租户隔离 + 分页）
T14b feat(worker): 草稿 AI 单字段重新生成端点（POST /drafts/{id}/ai/{field} 复用 LLM/翻译节点）
T7b  feat(worker): image_gen_plan 现有 slot 子集选择 + 计数（跳过机制，默认 10 张）
T9   feat(skill): graph/follow --to-box 入采集箱 + 老版本降级
T10  feat(webui): 采集箱页面（编辑上架入口/上架状态列/区间价格/批量删除/清空）
T10b feat(webui): 商品编辑页（上品帮 editGoods 版：三区块导航/变体表格/仓库库存/定时+立即上架）
T11  feat(webui): 店铺管理页面（shop_name/currency/is_default + 掩码 + 轮换提醒）
T12  feat(webui): 任务进度页（上架记录列 + 异常重上 + 批量搬家入口）
T13  feat(webui): 生图工作台（AI商品套图：原图≤3/卖点AI帮写/现有slot类型选择/余额+预计消耗前置检查）
T14  feat(worker): 在线商品改图全量重传端点（存活检查 + 重新审核 + 商品索引 + warehouse_id/stock 消费）
T15  test(worker): WebUI 全链路 E2E + 契约测试 + 安全评审门
T16  docs: CONTRACT-v4 + WEBUI.md + CHANGELOG + 版本四源统一
```

---

## 9. 成功判据（7 条）

1. `bash scripts/ci.sh --strict` 通过（lint → worker/skill 测试 → docker build）。
2. 本地 Docker 全链路 E2E：skill --to-box → WebUI 认领 → 编辑 → 上架 → 实时进度 → 单 slot 重生成 → 更新在线商品，无云访问。
3. 4 个裸奔端点无 token 返回 401。
4. 无明文 API key 出现在任何 DB 列（仅 BYTEA 密文）或任何 API 响应（仅掩码）。
5. 重生成和 resubmit 永不静默烧生图额度（缓存版本测试证明）。
6. 版本四源一致；`git tag v0.x.0` 触发 build-skill + cd 干净。
7. 三段式流程（采集箱 → 编辑页选店铺/改字段/AI辅助 → 上架至OZON）端到端跑通；一个草稿可提交多个店铺产生多个 submission 行，各自独立上架状态，**跨店提交触发确认提示（C5 v1 跨店提醒，不硬拦）**。
