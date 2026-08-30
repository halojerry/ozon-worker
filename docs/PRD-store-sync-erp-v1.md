# PRD v1:Ozon 店铺数据同步 ERP 化(worker + skill + webui 三端完善)

> 版本:v6(2026-08-30,评审迭代后定稿草案)
> 范围:全量 P0+P1+P2;身份=账号级;analytics=用户看自己 + admin 看全局
> 三端职责:worker 负责数据域拉取/任务化/落盘;skill 负责采集与货源匹配来源;webui 负责展示与操作闭环

---

## 0. 摘要

把「全局 15min 轮询 + 懒同步兜底 + key 即租户 + 订单商品两域」升级为「**账号级租户 + 绑定即初始化 + 分节定时增量 + Ozon 全数据域落盘 + 成本/货源主数据 + 只读缓存 + 全链路任务化 + 三端打通 + 竞态可控**」的 ERP 模型。

验收主线:
1. 绑定店铺 5s 内「同步中」,90% 店 3min 首屏,大店可见进度续传。
2. 页面秒开,同步/聚合/镜像不阻塞任何 HTTP 请求。
3. 同店同一时刻只有一个同步;指标快照不重复;成本变更不产生脏利润。
4. 换 key/加 key 不丢数据;租户严格隔离;用户 analytics 只见自己。
5. 订单利润有成本才显示;商品可按 在售/归档/错误/审核中 分栏;订单可追溯货源;采购成本可维护可审计。
6. 本地一键 Docker 测试;PG 自动备份(RPO 24h);迁移幂等可回滚。

---

## 1. 现状盘点(三端)

### 1.1 worker 已有
- 凭证管理(掩码 + AES-GCM 加密 + 轮换 + 跨租户绑定拦截)、草稿/提交/任务队列(zombie 恢复)、类目/属性/定价/生图/上架管线。
- 订单缓存(`/v4/posting/fbs/list`)+ 商品缓存(`/v3/product/list` + `/v3/product/info/list`)、15min 全局同步调度器、店铺分析/执行端点、操作审计、指标快照、选品洞察。
- 限流:`ozon_rate_limiter`(seller/finance/premium 分桶)+ HTTP RateLimiter。

### 1.2 skill 已有
- 1688/Ozon CDP 采集、aibuy/ak 图搜匹配、信封组装、discover 选品与 1688 匹配、利润估算、上报 discovery_runs / selection_insights / bestsellers。

### 1.3 webui 已有(17 面板全量核对)
| 面板 | 已连 API | 状态 |
|---|---|---|
| 店铺管理 | /credentials、/stores/{id}/stats、sync-status、validate | 缺同步进度/评分/配置/一键全店 |
| 采集箱 | /drafts CRUD、ai 字段、estimate、submit | 已通;缺图片镜像指示 |
| 任务中心 | /tasks images/regen/draft | 已通 |
| 订单 | /orders、notes、cancel-reasons、batch ship | 缺退货/净利/行级货源/新鲜度 |
| 在线商品 | /products/ozon、/products/{id}/edit | 缺 在售/归档/错误 Tab、三价、货源/成本、编辑接线待验证 |
| 选品归档 | /discovery/runs、/mappings/lookup、/seo/keywords | 有记录列表;缺候选详情/CSV 导出 |
| 数据大屏 | /analytics/market-overview、sales-trend、hot-queries | 缺角色 scope、店铺分析页 |
| 定价/生图/模板/榜单/密钥/管理/站点 | 各自 API | 已通 |

### 1.4 关键断点
- 同步阻塞事件循环、懒同步=全量同步、绑定后不落盘(P0)。
- 商品缓存只存 5 字段,归档用「未出现」启发式误判、错误商品不落、三档价不存。
- 订单利润 = 金额−佣金,无成本口径;order_notes 订单级无法区分一单多货源。
- 无退货/评分/店铺分析/促销真值/仓库字典;analytics 跨租户聚合泄漏。
- 身份 = key 哈希派生,换 key 丢数据。

---

## 2. 架构决策

| 决策点 | 结论 |
|---|---|
| 同步触发 | 绑定即 initial(先 validate probe);分节定时增量;手动/refresh 入队;sync_enabled=false 只停定时,手动可用 |
| 读取 | 只读缓存;空 → 空 + "never",绝不触发同步 |
| 并发 | store_sync_jobs 唯一部分索引 + FOR UPDATE SKIP LOCKED + PG advisory lock(同步域);草稿乐观锁 version(已有);任务 SKU 去重(已有) |
| 调度 | v1 进程内 5s 扫描 + worker 池(3);独立 scheduler 容器为阶段二 |
| 成本 | product_costs 成本主数据 + product_cost_history;订单同步落库时 join 算 real_profit;成本变更触发存量订单回填 |
| 分节频率 | 订单 15min、商品 30min、退货 30min、促销 60min、仓库 24h、分析/评分日级 |
| 数据口径 | 原始订单 180 天、日聚合永久、快照 30 天、job 30 天+每店 500 条 |
| 身份 | Supabase tokens(key→user_id);已配置异常 fail-closed;未配置回退 key 哈希 |
| 回滚 | STORE_SYNC_JOBS_ENABLED=0 回退旧循环;迁移幂等;cos-update 备份 |
| 密钥 | 密文版本前缀 + 双版本解密 + 批量重加密 |

异步/同步边界:同步 = 写凭证/保存草稿/校验/单条订单操作/读 PG 缓存(<2s 或需即时反馈);异步 = 全量/增量拉取、批量执行、图片镜像、上架、周期聚合/清理/备份。异步 job 内 Ozon 阻塞调用一律 to_thread;HTTP 同步端点只允许短调用(≤15s)。

---

## 3. Ozon 数据域清单与对齐

| 域 | API | 频率 | 落盘 | webui |
|---|---|---|---|---|
| 订单 FBS | /v4/posting/fbs/list | 15min | ozon_orders_cache(+real_profit) | 订单页(净利/毛利/退货 tab) |
| 商品(在售/归档/错误/审核) | /v3/product/list + info/list | 30min | ozon_products_cache(+8 列) | 商品页 Tab |
| 三档价格/指数 | info.list 自带 + /v5/product/info/prices | 随商品 | price/old_price/min_price | 商品表三价列 |
| 退货 | /v1/returns/list | 30min | ozon_returns_cache | 订单退货 tab |
| 店铺分析 | /v1/analytics/data | 日级 | ozon_store_analytics_daily | 店铺分析页 |
| 评分/评价 | /v1/rating/summary + /v1/review/list | 日级 | credentials.rating | 店铺卡评分 |
| 促销/活动 | /v1/actions + seller-actions/list | 60min | 快照 active_discount_count 真值 | 促销 tab |
| 物流/仓库 | /v1/warehouse/list + delivery-method/list | 24h | warehouse_cache | 上架编辑器仓库下拉 |
| 指标快照 | 随同步 | 15-30min | store_metrics_history + store_daily_metrics | 趋势图 |
| 广告 Performance | /api/client/* | — | 阶段二(独立 OAuth) | — |

字段对齐规则:
- 权威字段优先:归档用 is_archived/is_autoarchived,错误用 errors[],废除「未出现→archived」启发式。
- 枚举:订单状态复用 map_status;商品状态 = is_archived + statuses + errors 组合;货币 ₽ NUMERIC(14,2);日期 ISO8601→timestamptz(UTC)。
- 金额/利润:有成本才填 real_profit;评分/转化无源不编造。
- 原始留底:关键域 raw jsonb(探针确认体积后定)。
- 映射表冻结:`docs/ozon-field-map-v1.md`(M0 探针通过后冻结,实现期禁止再猜)。

API 请求对齐:统一 ozon_post + ozon_rate_limiter 分桶;每域独立请求构建器;分页对齐(v4 cursor / v3 offset / v1 has_next / 日期窗口);429/空 result → 3 次 1s/2s 退避 + error_code=rate_limited。

---

## 4. 成本/货源/采购域(ERP 核心补齐)

### 4.1 成本主数据 product_costs
- 聚合 Ozon 商品成本:采购价 + 国内运费 = 到仓成本;来源优先级 **manual(手填)> envelope(自营上架)> discovery(选品)> 1688 刷新**;manual 最高优先级,任何来源不覆盖手填;envelope 是真实成交价,优先于 discovery 候选价。
- 成本历史 append-only(product_cost_history),改价留痕,利润趋势可解释。

### 4.2 货源匹配 source_candidates
- product_id ↔ 1688 offer/url,含 match_score/match_method(aibuy图搜|ak关键词|manual)/status(valid|expired)。
- 自营商品上架成功自动回填;跟卖/存量商品由「未匹配货源」工作台补齐(webui 手动填或 skill 图搜后上报)。
- 1688 定时校验(24h):下架/大幅改价 → expired + 提醒。

### 4.3 订单行级货源 order_notes 升维
- 升级为 (posting_number, product_id) 行级,兼容旧 posting 级数据(product_id='' 表示整单)。
- 自动链路:订单同步落库时 order.products[].product_id → product_costs → 写 **order_line_costs 行级成本表**(posting_number, product_id, source_cost, real_profit, cost_version)→ 按 posting 聚合回写 ozon_orders_cache.real_profit。real_profit = 售价 − Ozon佣金 − 采购价 − 国内运费 − 国际物流(费率表);汇率走 fx_rates;缺项 → NULL。
- 成本变更回填:按 product_id 更新 order_line_costs(cost_version+1),再聚合回写订单缓存,避免全表扫 products jsonb。

### 4.4 汇率 fx_rates
- 日级表(CNY→RUB);缺失用最近一日,再缺失则利润标 NULL。

### 4.5 采购域 purchase_orders(阶段二)
- 多订单合并同货源 → 采购单;状态 待采购→已下单→1688已发货→已入库;低库存联动(在售+在途);供应商档案;1688 授权后实时价/库存。

---

## 5. 三端打通矩阵

| 功能 | worker | skill | webui | 本版动作 |
|---|---|---|---|---|
| 上架管线 | ✅ | ✅ | ✅ | 保持 |
| 店铺同步 | ⚠️ 阻塞→异步 job | — | ⚠️ 按钮→进度轮询 | 打通 |
| 商品(状态/三价/错误) | ⚠️ 扩列 | ✅ 匹配源 | ⚠️ Tab/货源/成本 | 打通 |
| 订单(净利/退货/货源) | ⚠️ 成本口径 | — | ⚠️ 展示 | 打通 |
| discover→webui | ✅ 归档 | ✅ 上报 | ⚠️ 候选详情/CSV | 补 G1 |
| 图搜匹配 | ⚠️ 需 AK | ✅ aibuy/ak | ⚠️ 无入口 | v1=手动+skill 上报;阶段二=1688 授权后 worker 直连 |
| 促销/仓库/评分/分析 | ⚠️ 新增域 | — | ❌ 新增页/tab | 打通 |
| 采购清单 | ⚠️ 阶段二 | — | ⚠️ 阶段二 | 预留 |

---

## 6. 竞态清单与处理(跨端)

| # | 竞态 | 处理 | 归属 |
|---|---|---|---|
| R1 | 手动同步 vs 定时 vs 多副本 vs 同账号多 key | store_sync_jobs 唯一部分索引(每店一个在途)+ SKIP LOCKED + advisory lock | worker |
| R2 | 店铺操作(改价/库存) vs 商品同步覆盖缓存 | 操作成功 → 立即触发该店轻量增量 job(高优先级);页面以 Ozon 最终态一致 | worker+webui |
| R3 | 成本编辑 vs 订单同步 join 成本 | 订单落库存 real_profit 快照;成本变更 → 按 product_id 异步回填存量订单(幂等) | worker |
| R4 | 1688 成本刷新 vs 手动成本 | manual 最高优先级,刷新跳过 manual 行 | worker |
| R5 | 同步中凭证轮换/吊销 | 同步开始快照凭证;invalid_key → job failed+error_code,吊销不重试;轮换下次自动成功 | worker |
| R6 | skill 提交 vs webui 提交同一草稿 | draft_submissions 加 (draft_id, store_client_id) 唯一约束;SKU 去重(已有) | worker |
| R7 | 草稿多端编辑 | version 乐观锁 + 409(已有),保留 | worker+webui |
| R8 | 图片镜像 vs 草稿编辑 | 镜像按 payload version 回写,不匹配则丢弃并告警 | worker |
| R9 | 日聚合 vs 同步半批 | 聚合只统计 last_success_at 之前数据;按订单水位过滤 | worker |
| R10 | 多 tab 轮询风暴 | 前端轮询器单例 + BroadcastChannel;服务端轮询端点短缓存 | webui |
| R11 | 任务重试 vs zombie | task 级 SELECT FOR UPDATE + 状态机(已有模式保留) | worker |
| R12 | 用户发货 vs 同步覆盖状态 | 操作成功本地乐观更新 + 强制一次增量同步;Ozon 为最终权威 | webui+worker |
| R13 | 迁移 vs 在线流量 | 维护窗口 + maintenance 标志;迁移期间业务写 503 | ops |
| R14 | 货源匹配 vs 商品归档 | 匹配记录保留标 archived;商品恢复自动恢复 | worker |
| R15 | 同账号两 key 并发操作 | store_actions 短调用同步 + 批量入队;成本/货源编辑用 updated_at 乐观校验 | worker+webui |

---

## 7. 数据模型(DDL 摘要)

```sql
-- 同步任务
CREATE TABLE store_sync_jobs (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    credential_id UUID NOT NULL,
    kind VARCHAR(16) NOT NULL,          -- initial|incremental|manual|continuation
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    trigger VARCHAR(16) NOT NULL,
    error_code VARCHAR(32),
    orders_synced INT NOT NULL DEFAULT 0,
    products_synced INT NOT NULL DEFAULT 0,
    progress INT NOT NULL DEFAULT 0,
    error TEXT,
    started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX uq_sync_job_one_active ON store_sync_jobs(credential_id)
    WHERE status IN ('pending','running');
CREATE INDEX idx_sync_jobs_tenant_cred_created
    ON store_sync_jobs(tenant_id, credential_id, created_at DESC);

-- 凭证扩展
ALTER TABLE credentials
    ADD COLUMN sync_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN sync_interval_minutes INT NOT NULL DEFAULT 15,
    ADD COLUMN sync_products_interval_minutes INT NOT NULL DEFAULT 30,
    ADD COLUMN rating_total NUMERIC(4,2),
    ADD COLUMN rating_localization_index NUMERIC(6,2),
    ADD COLUMN rating_updated_at TIMESTAMPTZ;

-- 同步状态扩展(域水位 jsonb)
ALTER TABLE credential_sync_state
    ADD COLUMN last_success_at TIMESTAMPTZ,
    ADD COLUMN consecutive_failures INT NOT NULL DEFAULT 0,
    ADD COLUMN orders_window_since TIMESTAMPTZ,
    ADD COLUMN orders_window_to TIMESTAMPTZ,
    ADD COLUMN orders_sync_cursor TEXT,
    ADD COLUMN orders_sync_incomplete BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN last_job_id BIGINT,
    ADD COLUMN domain_state JSONB NOT NULL DEFAULT '{}';

-- 缓存扩展
ALTER TABLE ozon_orders_cache ADD COLUMN real_profit NUMERIC(14,2);
ALTER TABLE ozon_products_cache
    ADD COLUMN old_price NUMERIC(14,2),
    ADD COLUMN min_price NUMERIC(14,2),
    ADD COLUMN status VARCHAR(32) DEFAULT '',
    ADD COLUMN error JSONB,
    ADD COLUMN archived_at TIMESTAMPTZ;

-- 日聚合
CREATE TABLE store_daily_metrics (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    credential_id UUID NOT NULL,
    store_id VARCHAR(64) NOT NULL,
    stat_date DATE NOT NULL,
    order_count INT NOT NULL DEFAULT 0,
    sales_amount NUMERIC(14,2),
    commission_amount NUMERIC(14,2),
    profit_amount NUMERIC(14,2),
    product_count INT NOT NULL DEFAULT 0,
    low_stock_count INT NOT NULL DEFAULT 0,
    active_discount_count INT NOT NULL DEFAULT 0,
    profit_rate NUMERIC(6,4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_store_daily UNIQUE (tenant_id, credential_id, stat_date)
);

-- 退货/分析/仓库
CREATE TABLE ozon_returns_cache (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    credential_id UUID NOT NULL,
    return_id BIGINT NOT NULL,
    posting_number VARCHAR(64) NOT NULL DEFAULT '',
    order_id VARCHAR(32) NOT NULL DEFAULT '',
    return_type VARCHAR(32) NOT NULL DEFAULT '',
    schema VARCHAR(32) NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    compensation_status VARCHAR(32) NOT NULL DEFAULT '',
    product JSONB, status VARCHAR(32) NOT NULL DEFAULT '',
    raw JSONB, synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_returns_cache UNIQUE (tenant_id, credential_id, return_id)
);
CREATE TABLE ozon_store_analytics_daily (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    credential_id UUID NOT NULL,
    stat_date DATE NOT NULL,
    metric VARCHAR(64) NOT NULL,
    value NUMERIC(14,2), raw JSONB,
    CONSTRAINT uq_store_analytics_daily UNIQUE (tenant_id, credential_id, stat_date, metric)
);
CREATE TABLE warehouse_cache (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    credential_id UUID NOT NULL,
    warehouse_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    is_rfbs BOOLEAN NOT NULL DEFAULT FALSE,
    raw JSONB, synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_warehouse_cache UNIQUE (tenant_id, credential_id, warehouse_id)
);

-- 成本/货源/汇率
CREATE TABLE product_costs (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    credential_id UUID NOT NULL,
    product_id VARCHAR(32) NOT NULL,
    offer_id VARCHAR(128) NOT NULL DEFAULT '',
    purchase_url TEXT NOT NULL DEFAULT '',
    purchase_cost NUMERIC(14,2),
    freight_cny NUMERIC(14,2),
    supplier TEXT NOT NULL DEFAULT '',
    weight_g INT, length_mm INT, width_mm INT, height_mm INT,
    currency VARCHAR(8) NOT NULL DEFAULT 'CNY',
    cost_source VARCHAR(16) NOT NULL DEFAULT 'manual',  -- envelope|discovery|manual
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_product_costs UNIQUE (tenant_id, credential_id, product_id)
);
CREATE TABLE product_cost_history (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    credential_id UUID NOT NULL,
    product_id VARCHAR(32) NOT NULL,
    old_cost NUMERIC(14,2), new_cost NUMERIC(14,2),
    changed_by VARCHAR(64), changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE source_candidates (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    credential_id UUID NOT NULL,
    product_id VARCHAR(32) NOT NULL,
    source_offer_id VARCHAR(64) NOT NULL DEFAULT '',
    source_url TEXT NOT NULL,
    price_cny NUMERIC(14,2),
    match_score NUMERIC(5,2),
    match_method VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'valid',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_source_candidates UNIQUE (tenant_id, credential_id, product_id, source_offer_id)
);
CREATE TABLE fx_rates (
    date DATE PRIMARY KEY,
    cny_to_rub NUMERIC(12,6),
    source VARCHAR(16) NOT NULL DEFAULT 'manual',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- order_notes 行级化:主键改为 (posting_number, product_id),旧整单行 product_id='' 兼容
ALTER TABLE order_notes
    ADD COLUMN product_id VARCHAR(32) NOT NULL DEFAULT '',
    ADD COLUMN sku VARCHAR(64) NOT NULL DEFAULT '',
    ADD COLUMN cost_source VARCHAR(16) NOT NULL DEFAULT 'manual';
ALTER TABLE order_notes DROP CONSTRAINT order_notes_pkey;
ALTER TABLE order_notes ADD PRIMARY KEY (posting_number, product_id);

-- 订单行级成本(成本变更回填的关联表,避免扫 products jsonb)
CREATE TABLE order_line_costs (
    posting_number VARCHAR(64) NOT NULL,
    tenant_id VARCHAR(50) NOT NULL,
    credential_id UUID NOT NULL,
    product_id VARCHAR(32) NOT NULL,
    sku VARCHAR(64) NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    source_cost NUMERIC(14,2),            -- 到仓成本(CNY)
    logistics_cny NUMERIC(14,2),
    fx_rate NUMERIC(12,6),
    real_profit NUMERIC(14,2),            -- RUB,缺成本为 NULL
    cost_version INT NOT NULL DEFAULT 1,  -- 成本变更 +1,回填用
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_order_line_costs UNIQUE (posting_number, product_id)
);
CREATE INDEX idx_order_line_costs_product ON order_line_costs(tenant_id, credential_id, product_id);

-- 采集箱提交幂等 + 定时上架 + 任务进度事件(采集箱/任务中心补全)
CREATE UNIQUE INDEX uq_draft_submissions_draft_store
    ON draft_submissions(draft_id, store_client_id)
    WHERE draft_id IS NOT NULL AND status IN ('pending','uploading');

CREATE TABLE scheduled_listings (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    draft_id UUID NOT NULL REFERENCES product_drafts(id) ON DELETE CASCADE,
    credential_id UUID NOT NULL,
    scheduled_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending|submitted|skipped|canceled
    task_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_scheduled_listings UNIQUE (draft_id, credential_id)
);
CREATE INDEX idx_scheduled_listings_due ON scheduled_listings(status, scheduled_at);

CREATE TABLE task_progress_events (
    id BIGSERIAL PRIMARY KEY,
    task_id TEXT NOT NULL,
    seq INT NOT NULL,
    node VARCHAR(64) NOT NULL,
    step VARCHAR(64) NOT NULL DEFAULT '',
    status VARCHAR(16) NOT NULL,           -- started|progress|finished|failed|retry
    message TEXT NOT NULL DEFAULT '',
    detail JSONB,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    CONSTRAINT uq_task_progress_seq UNIQUE (task_id, seq)
);
CREATE INDEX idx_task_progress_events_task ON task_progress_events(task_id, seq);
```

迁移:`worker/scripts/migrate_sync_erp_v1.py`(幂等)接入 init_data/deploy,启动前执行。

保留策略(env 可配):快照 30 天、job 30 天+每店 500 条、订单缓存 180 天、退货/分析/日聚合/成本历史长期。

---

## 8. API / 协议变更

| 端点 | 变更 |
|---|---|
| POST /credentials | 可选同步配置;CredentialOut + initial_sync_job_id + rating |
| PATCH /stores/{id}/sync-config | 新增(免 api_key,间隔下限 5min) |
| POST /stores/{id}/sync | breaking:阻塞 → 202 {job_id} |
| POST /stores/sync-all | 新增(60s 冷却) |
| GET /stores/{id}/sync-jobs、/sync-jobs/{job_id} | 新增(历史/进度) |
| GET /stores/{id}/sync-status | 扩展(配置/各域水位/current_job/is_stale) |
| GET /stores/overview | 新增(P2:新鲜度+今日合计) |
| GET /admin/sync-health | 新增(admin 健康总览) |
| GET /stores/{id}/stats、/orders、/products/ozon | 移除懒同步;refresh=1 入队;空返回 "never" |
| GET /products/ozon?status=archived\|error | 新增过滤(商品 Tab) |
| GET /stores/{id}/returns、/warehouses | 新增(退货/仓库) |
| PATCH /products/{id}/source | 新增(成本/货源手动维护,manual 优先) |
| POST /products/{id}/source/refresh | 新增(1688 成本刷新,手动行跳过) |
| GET /stores/{id}/analysis | 扩展(退货率/评分/转化率) |
| GET /analytics/* | 用户租户内/admin 全局;hot-queries 仅 admin;响应带 scope |
| GET /tasks/{id}/progress | 新增:任务进度事件列表 + 汇总(percent/当前节点/时间线/ETA) |
| GET /progress/{task_id}/stream | 新增:SSE 实时进度(Last-Event-ID 增量回放) |
| POST /drafts/batch-submit | 新增(P2):勾选多草稿批量提交,逐条 version 校验,已变更项跳过并报告 |
| POST /drafts/{id}/resubmit | 新增:failed/rejected 重试(复用 submit_draft) |
| POST /drafts/import | 新增(P2):CSV 导入采集箱(竞品对标) |
| GET /drafts/export | 新增(P2):采集箱 CSV 导出(UTF-8 BOM,Excel 兼容) |
| POST /source-candidates | 新增(M5b):skill 图搜/跟卖匹配上报(credential_id/client_id 解析,缺店占位) |
| GET /products/{id}/source-candidates | 新增(M5b):货源工作台候选展示(含占位店候选) |
| POST /drafts/{id}/submit?scheduled_at= | 扩展:定时上架(scheduled_listings 落表,到点调度触发) |
| DELETE /credentials/{id}/data?confirm= | 新增(P2,默认关闭/需管理端授权):吊销后硬删除该店缓存/历史数据,二次确认 + operation_log 审计 |

job 状态机 pending→running→ok|failed→(手动)新 job;退避 5min→15min→1h;连续 3 次失败 stale;job 超时 30min failed;error_code 前端中文映射。

---

## 9. 核心流程

1. 绑定即初始化:create_credential 提交后 enqueue initial(5s 扫描兜底);先 validate probe,失败落 error_code;成功全域首拉(订单回补 BACKFILL_DAYS、商品全量、退货/评分/仓库/分析)。
2. 分节调度:5s 扫描按域水位建店级 job;worker 池 3,SKIP LOCKED + advisory lock,to_thread 执行,逐域进度/失败隔离。
3. 订单续传:预算 25 页;截断持久化窗口+cursor;续传同窗口;完成才推进水位。
4. 只读缓存:读 PG 不触发同步;空返回 "never";新鲜(<15min)/稍旧(<2h)/过期(>2h 或连续失败≥3)。
5. 利润:订单同步 join product_costs 写 order_line_costs 行级成本 → 聚合回写 ozon_orders_cache.real_profit;成本变更按 product_id 异步回填(幂等,cost_version+1);手动标注覆盖。
6. 周期任务:5s 扫描同时检查 scheduled_listings 到点触发(触发前查重,已提交则 skipped);10min 日聚合+保留清理;24h 1688 成本刷新与货源校验;连续失败告警 Sentry + operation_log。

---

## 10. 鉴权、租户与密钥

- tenant_service.resolve_tenant:Supabase tokens(key→user_id),30-60s LRU,revoked 最前;已配置异常 fail-closed 503;未配置回退 key 哈希。
- analytics:非 admin 加 tenant 过滤;admin 全局;bestseller_count 全局共享;hot-queries 仅 admin。
- 迁移 migrate_key_tenant_to_user.py:dry-run 先行;13 张 tenant 表 + user_id 表 remap;ozon_product_tasks 先重写 sku_key 前缀再合并;credentials 同店合并保留最新 + 子表/store_id 重指向;is_default 冲突保留最新;孤儿报告 + --mapping-file;幂等;维护窗口执行。
- 主密钥轮换:密文版本前缀 v1:,双版本解密,批量重加密工具。

---

## 11. 前端(webui)变更清单

- StoresPanel:绑定后进度轮询;同步按钮+冷却;新鲜度/评分/错误展示;sync-config 编辑;一键全店同步。
- ProductsPanel:Tab 在售/归档/错误/审核中;三价列;错误 code/message + 修复引导;货源匹配工作台(未匹配筛选 + 手动填写 + skill 上报候选展示);成本编辑+历史;编辑接线(G3 验证)。
- OrdersPanel:退货 tab;real_profit 净利(无成本显示毛利);行级「查看货源」;新鲜度徽标。
- 店铺分析页(新):访问/加购/转化/广告展示日趋势。
- 促销 tab(新):活动列表与状态。
- 上架编辑器:仓库下拉(warehouse_cache);定时上架入口(接 scheduled_listings,不再 stub)。
- 采集箱列表:「全部平台/全部状态」筛选按钮实装(现状为占位按钮)。
- DataScreenPanel:scope 渲染 + 一键全店同步。
- CollectionPanel:图片镜像状态提示。
- DiscoveryPanel:候选详情 + CSV 导出(补 G1)。
- 采集箱增强:多选批量提交/删除、失败重试按钮、定时上架入口、CSV 导入导出(P2)。
- 任务中心增强:详情页时间线(节点/耗时/重试)、当前子步高亮、图片逐张出现、ETA、SSE 实时订阅;列表进度条 + 筛选 + 重新提交。
- client.ts/hooks:新类型(SyncJob/StoreSyncStatusV2/AnalyticsScope/ProductCost/SourceCandidate/ReturnItem…);轮询器单例 + BroadcastChannel 防多 tab 风暴。

---

## 12. skill 变更清单

- G4:discover auto-submit 默认 3 线程(--threads);batch_test --parallel N。
- 货源匹配上报:图搜/关键词匹配结果除本地 CSV 外上报 source_candidates(worker 新端点或扩展 discovery_runs 回调)。
- 成本刷新:配合 1688 授权(阶段二)提供实时价/库存抓取;v1 保持现有信封/发现链路。
- 测试:新增并发/上报用例;skill 597 基线保持。

---

## 13. M0 先跑一遍验证(spike,实现前置)

`worker/scripts/probe_ozon_store.py`(真实测试店凭证,原始响应落本地 docs/ozon-probe/<domain>.json,不入库):
1. 商品:visibility=ALL 是否含归档;is_archived/errors/min_price/old_price/statuses/commissions 实际结构;分页/空态。
2. 订单/退货:字段稳定性、has_next、日期窗口、financial_data 结构。
3. 分析:metrics 枚举、日期粒度、dimensions 取值。
4. 评分/促销/仓库:字段、空态、限额。
5. 三档价/指数:/v5/product/info/prices 的 price/old_price/min_price/price_indexes/commissions 实际结构(与 info.list 三价字段互验)。
6. 输出映射校验报告;通过标准 = 每域与 docs/ozon-field-map-v1.md 100% 对齐,冻结后才进入实现。

---

## 14. 测试计划

| 层 | 用例 |
|---|---|
| M0 | 探针逐域跑通;映射冻结 |
| 单元 | job 状态机;去重索引;SKIP LOCKED+advisory lock;退避/stale/超时;续传同窗口;分节 due;利润优先级(手动>成本表>NULL);成本变更回填;日聚合;保留清理;错误分类 |
| 集成 | 绑定→initial;POST /sync 202;sync-all 冷却;refresh 异步;空缓存 never;analytics 用户/admin;sync-config;商品 status/error 过滤;returns/warehouse/rating/source 端点;竞态 R1-R15 各一用例 |
| 采集箱 | 提交幂等(双击/重复提交 → 唯一约束);批量提交 version 冲突跳过;resubmit;定时上架到点触发与重复跳过;CSV 导入;提交成功 → product_costs/source_candidates 回填 |
| 任务中心 | 进度事件 seq 单调/去重;percent 计算(节点权重+子步);SSE Last-Event-ID 重连增量;重启后事件回放 percent 一致;终态 submitted/reviewing/published 区分;24h 回查并发不覆盖终态 |
| 迁移 | 两 key/同店两 key/两默认/sku_key 冲突/孤儿 → dry-run+apply+幂等;store_id 重指向;主密钥轮换 |
| 身份 | tokens→user_id;fail-closed;未配置回退;夹具补 user_id 映射(6 mock+6 PG) |
| 运维 | 特性开关回退;zombie 清理;migrate 幂等;备份恢复演练 |
| 回归 | worker 1306+新增;skill 597+G4;webui tsc+build |
| E2E | 绑定 5s/90% 3min;压测不阻塞;换 key 数据在;商品 Tab/退货/利润/货源正确;竞态压测;test-docker.sh 一键绿 |

---

## 15. 里程碑

- M0:数据域探针 + 映射冻结。
- M1(P0 同步引擎):store_sync_jobs + 分节调度 + 绑定即初始化 + 只读缓存 + 订单续传 + 商品扩列 + zombie 清理 + 特性开关 + 竞态 R1/R2/R5/R12。
- M2(P0 身份隔离):tenant_service + fail-closed + analytics scope + 迁移 + 夹具 + 隔离回归。
- M3(P1 数据域+成本):returns/analytics/rating/warehouse/促销真值 + product_costs/source_candidates/fx_rates/order_notes 行级 + real_profit + 回填 + 日聚合 + 主密钥轮换 + admin sync-health + 竞态 R3/R4/R9/R14。
- M4(P1/P2 前端+skill+采集箱+任务中心):商品 Tab/货源工作台/退货 tab/分析页/促销 tab/新鲜度与错误文案/scope/编辑接线;G1 候选详情+CSV;G4 并发;任务中心真实进度(事件表+SSE+时间线+终态真实化);采集箱 批量提交/失败重试/定时上架。
- M5(P2 运维与采集箱):test compose/test-docker.sh/deploy.sh/备份/硬删除;图片镜像
  (异步 COS + version 守卫 + 存量回填);货源匹配上报(source_candidates 三来源);
  CSV 导入;采购域预留;广告/财务对账预留。

每里程碑独立发版(v0.61 起);M2 迁移需维护窗口+备份。

---

## 16. 采集箱逻辑(补全)

### 16.1 生命周期与状态机

```text
采集(skill 1688/Ozon 跟卖 / webui 手动 / CSV 导入) → 已采集(草稿)
  → 编辑中(AI 字段 / 估价 / 成本货源) → 提交(单店 / 跨店确认 / 定时)
  → submission: pending → uploading → published(已上架)
                          └→ failed | rejected(可重新提交)
草稿级状态 = 派生:任一 submission published → 已上架;否则取最新 submission
```

### 16.2 补充逻辑

| 能力 | 现状 | 本版动作 |
|---|---|---|
| 草稿 CRUD / 版本锁 / 跨店确认 / AI 字段 / 估价 | ✅ | 保留 |
| 提交幂等 | ⚠️ 无唯一约束 | draft_submissions 加 (draft_id, store_client_id) 唯一索引;前端提交防抖 |
| 批量提交/删除/AI 填写 | ❌ | P2:POST /drafts/batch-submit,逐条 version 校验,已变更项跳过并报告 |
| CSV 导入/导出模板 | ✅ | P2:POST /drafts/import(JSON rows / text/csv)+ GET /drafts/export(UTF-8 BOM) |
| 失败重试 | ⚠️ 无入口 | POST /drafts/{id}/resubmit(failed/rejected → 复用 submit_draft) |
| 定时上架 | ⚠️ scheduled_at 仅 UI stub | scheduled_listings 表 + 调度器到点触发;触发前查重,已提交跳过 |
| 货源/成本打通 | ✅ | 提交成功 → product_task_index 回填 → 写 product_costs(envelope 源)+ source_candidates(envelope/手动/skill/discover 三来源) |
| 图片镜像 | ✅ | 草稿保存(POST/PATCH)异步镜像 COS,version 守卫(R8),CollectionPanel 状态徽标,存量回填脚本 |
| 删除语义 | ✅ CASCADE submissions | 保留;任务历史不受影响;二次确认 |

### 16.3 竞态
- 批量提交 vs 单条编辑:逐条 version 校验,冲突项跳过并报告。
- 定时触发 vs 手动提交同草稿:触发前查 uq_draft_submissions_draft_store,已提交则 scheduled status=skipped。
- skill 上报 vs webui 编辑:version 乐观锁(已有)。

---

## 17. 任务中心真实进度可视化(补全)

### 17.1 现状问题(已核实)
- 进度 = STAGE_ORDER 12 阶段索引百分比(每阶段约 8.3%),节点内长耗时(生图/属性 LLM/重试/变体循环)进度冻结 → 百分比不真实。
- `_task_progress` 内存态,重启丢失;PG 只落粗粒度快照。
- 无子步事件、无耗时/时间线/ETA;前端 3s 轮询。
- 上架提交 100% ≠ Ozon 已上架:24h 合并验证是异步 job,终态确认有延迟。

### 17.2 设计

1. **事件模型**:`task_progress_events(task_id, seq, node, step, status, message, detail, started_at, finished_at)` append-only;节点内循环(变体/图片槽位/重试/校验)发子步事件;percent = Σ(已完成节点权重)+ 当前节点内子步完成度 × 节点权重;节点权重按历史耗时标定(初版静态权重表,后续按统计校准)。
2. **实时推送**:SSE `GET /progress/{task_id}/stream`,Last-Event-ID 增量回放,断线重连不丢;前端任务详情订阅,3s 轮询保留为降级。
3. **重启恢复**:事件表持久化;zombie 恢复后历史保留,新执行追加;percent 由事件重算,不依赖内存。
4. **可视化**:任务详情时间线(节点+耗时+重试次数)、当前子步高亮(如「生成图片 2/3」)、错误节点红标、ETA(已完成平均耗时 × 剩余权重)、图片槽位逐张出现。
5. **终态真实化**:状态区分 pending / running / uploading / **submitted(已提交 Ozon)** / **reviewing(审核/合并中,24h 回查)** / published / failed;任务中心不以本地提交为成功,以 Ozon 确认或 24h 回查为准。
6. **列表增强**:进度条 + 当前阶段 + 最近事件摘要;按状态/店铺/时间筛选;failed 行直接「重新提交」。

> 兼容约束:ozon_product_tasks 内部状态枚举保持现状(pending/uploading/completed/failed/…),submitted/reviewing/published 只作为**展示层派生状态**(由阶段/事件/Ozon 回查推导),不改内部枚举,避免破坏 task_status 现有契约。

> 多副本约束:SSE 流由任意副本服务时,事件源必须读 PG 事件表(内部短轮询或 LISTEN/NOTIFY),不依赖任务在本地进程的内存态。

### 17.3 竞态
- 事件 append 与终态写回独立(两字段,读取按 seq 合并),无锁冲突。
- SSE 重连用 Last-Event-ID 增量,幂等。
- 终态写回与 24h 回查 job 并发:以 Ozon 权威状态为准,事件只记录不覆盖终态。

---

## 18. 测试标准与验收门槛

### 19.1 通用标准(每个里程碑必须满足才可发版)
| 门槛 | 标准 |
|---|---|
| 单测 | 新增代码行覆盖率 ≥ 80%;全量用例必须全绿;不允许 skip 掩盖失败 |
| 集成 | 真实 PG + mock Ozon 全绿;竞态 R1-R15 各至少 1 个并发用例 |
| 回归 | worker 全量(1443)全绿;skill 全量(607)全绿;webui `tsc -b` 0 错误 + `bun run build` 通过 |
| 性能 | 同步并发压测(10 店同时同步)时 API P95 响应 < 500ms;页面读缓存 P95 < 300ms;同步期间事件循环无阻塞(压测脚本断言) |
| 数据正确性 | 迁移前后行数对账 0 丢失;利润口径断言(有成本必填/无成本必 NULL);快照不重复(同店并发同步压测) |
| CI 门禁 | scripts/ci.sh 全绿 + docker-compose.test.yml 一键测试通过才允许合并 |

### 19.2 每里程碑验收(done criteria)
- M0:探针逐域跑通,映射表 100% 对齐并冻结,差异清单为空。
- M1:绑定→initial 全流程 E2E 绿;同店并发同步只产生 1 个在途 job;读空缓存返回 never 且不触发同步;重启后 zombie job 恢复。
- M2:身份迁移 dry-run/apply 幂等;隔离回归(两租户互不可见);Supabase 故障 fail-closed 单测;夹具全量适配。
- M3:退货/分析/评分/仓库/促销各域探针→落盘→端点 E2E 绿;成本变更回填后订单 real_profit 一致;日聚合与保留清理正确。
- M4:商品 Tab/货源工作台/退货/分析页/促销 tab 全部接线 E2E 绿;任务中心 SSE 断线重连不丢事件;采集箱批量/重试/定时绿。
- M5:test-docker.sh 一键绿;deploy.sh 对缺失 dist 报错;备份恢复演练通过;硬删除审计完整。

### 19.3 竞态与数据断言标准
- 并发压测:同店 5 并发触发同步(手动×2+定时+refresh+多副本模拟)→ 断言唯一索引拒绝多余 job、快照行数 = 同步次数、订单 upsert 幂等。
- 成本回填:改成本 → 回填后该商品所有订单 real_profit 更新且 cost_version+1;与手动标注冲突时 manual 优先。
- 迁移:构造「同店两 key / 两默认 / sku_key 冲突 / 孤儿」fixture → apply 后行数守恒、store_id 重指向、无残留旧租户。

---

## 19. 预期目标(KPI / SLA)

| 指标 | 目标 |
|---|---|
| 绑定感知 | 绑定成功 → 5s 内页面出现「同步中」 |
| 首屏数据 | 90% 店铺 initial 同步 3min 内出首屏;100% 通过续传完成(无静默截断) |
| 页面延迟 | 店铺/订单/商品页读缓存 P95 < 300ms;同步进行中 API P95 < 500ms |
| 数据新鲜度 | ≥95% 店铺订单数据滞后 ≤ 20min、商品 ≤ 35min;stale 店铺占比 < 1% |
| 同步成功率 | 单店同步成功率 ≥ 99%(剔除凭证无效);连续失败自动恢复 |
| 利润正确性 | 有成本商品 real_profit 对账一致率 ≥ 99.9%;无成本商品恒 NULL(零编造) |
| 进度可视化 | 进度事件 SSE 延迟 < 1s;重启后进度可由事件回放还原 |
| 迁移安全 | 存量数据 0 丢失;迁移可 dry-run、可幂等重跑;维护窗口内完成 |
| 可用性 | 同步 job 超时率 < 0.1%;PG 备份 RPO 24h / RTO ≤ 1h;恢复演练通过 |
| 自动化 | 回归全绿门槛达成率 100%(每个里程碑);测试环境一键可复现 |

---

## 20. TODO 任务清单(按里程碑,实现期逐项勾选)

### M0 数据域探针
- [x] 编写 `worker/scripts/probe_ozon_store.py`(真实测试店凭证,原始响应落 docs/ozon-probe/,不入库)
- [x] 逐域探针:商品(visibility/is_archived/errors/三档价)、订单/退货、分析、评分/促销/仓库、v5 prices
- [x] 产出映射校验报告,冻结 `docs/ozon-field-map-v1.md`
- [x] 冻结差异清零,进入 M1

### M1(P0 同步引擎)
- [x] store_sync_jobs 表 + 唯一部分索引 + 迁移脚本(幂等 DDL)
- [x] 分节调度器:5s 扫描 + worker 池(3)+ SKIP LOCKED + advisory lock + to_thread
- [x] 绑定即初始化(validate probe → initial job → 5s 兜底)
- [x] 只读缓存改造(orders/products/stats 去懒同步)+ refresh=1 异步入队
- [x] 订单续传(窗口 + cursor + incomplete)
- [x] 商品域扩列(old_price/min_price/status/error/archived_at,is_archived 权威)
- [x] zombie 清理 + STORE_SYNC_JOBS_ENABLED 特性开关
- [x] API:POST /sync 202、sync-jobs、sync-status 扩展、sync-config
- [x] 竞态 R1/R2/R5/R12 用例
- [x] webui StoresPanel 进度轮询 + 冷却

### M2(P0 身份隔离)
- [x] tenant_service.resolve_tenant(tokens→user_id,LRU,fail-closed)
- [x] _authenticate_token / _verify_analytics_token 改造
- [x] analytics scope(用户租户内 / admin 全局 / hot-queries admin-only)
- [x] migrate_key_tenant_to_user.py(dry-run/apply/幂等/孤儿报告/sku_key 前缀重写/合并策略)
- [x] worker 测试夹具 user_id 映射(6 mock + 6 PG)
- [ ] 存量迁移执行(维护窗口)+ 隔离回归

### M3(P1 数据域 + 成本)
- [x] returns / analytics_daily / rating / warehouse / 促销真值五域落盘 + 端点
- [x] product_costs + product_cost_history + source_candidates + fx_rates
- [x] order_notes 行级化 + order_line_costs + real_profit 计算与回填
- [x] store_daily_metrics + 保留清理(30 天/180 天/每店 500 条)
- [x] 主密钥版本前缀 + 双版本解密 + 重加密工具
- [x] admin sync-health + Sentry 连续失败告警
- [x] 竞态 R3/R4/R9/R14 用例

### M4(P1/P2 前端 + skill + 采集箱 + 任务中心)
- [x] 商品 Tab(在售/归档/错误/审核中)+ 三价 + 货源工作台 + 成本编辑
- [x] 订单退货 tab + 净利/毛利 + 行级查看货源
- [x] 店铺分析页 + 促销 tab + 仓库下拉 + DataScreen scope
- [x] 任务中心:task_progress_events + percent 权重 + SSE + 时间线 + 终态真实化
- [x] 采集箱:提交幂等 + resubmit + 批量提交 + 定时上架 + 筛选按钮实装
- [x] skill:G4 并发 + 货源匹配上报;G1 候选详情/CSV;G3 编辑接线
- [x] 新鲜度/错误中文文案全站

### M5(P2 运维与采集箱)
- [x] docker-compose.test.yml + scripts/test-docker.sh(密码统一)
- [x] deploy.sh dist 校验 + migrate 接线 + RATE_LIMIT 默认对齐
- [x] backup-pg.sh + cron + 恢复演练
- [x] 图片镜像(COS)+ 存量回填(POST/PATCH /drafts 异步镜像,version 守卫 R8,
      image_mirror_state 状态列 + CollectionPanel 徽标 + backfill_draft_image_mirror.py)
- [x] 货源匹配上报:POST /source-candidates(skill 跟卖/图搜上报,discover 回调派生,
      上架成功 envelope 回填)+ GET /products/{id}/source-candidates
- [x] CSV 导入采集箱(POST /drafts/import,JSON rows / text/csv 双格式)
- [x] CSV 导出采集箱(GET /drafts/export,UTF-8 BOM + webui 导出按钮)
- [x] 硬删除入口(管理端授权 + confirm 二次确认 + ADMIN_HARD_DELETE_ENABLED 开关
      + operation_log 审计;数据合规语义见 data_erasure_service)
- [x] 采购域/广告 OAuth/财务对账预留设计文档(DESIGN-PHASE2-PURCHASE-ADS-RECONCILIATION.md)

### M6(上生产前演示清零,2026-08-30)
- [x] 工作台真实化:GET /dashboard/overview(今日/趋势/热销/最近订单/在售/待办,租户隔离只读 PG)
- [x] 系统设置真实化:user_settings 表 + GET/PUT /settings(业务参数/通知设置,范围校验)
      + 物流费率管理(管理员:列表/改价/CSV 导入)
- [x] 商品编辑抽屉真实化:读 /products/{id}/edit(draft_id/version/credential)→
      PATCH /drafts + submit(update_product_id 更新上架)
- [x] 采集箱:全部平台/全部状态筛选、批量提交(/drafts/batch-submit)、CSV 导入 UI
- [x] 商品货源工作台:/products/ozon?source=matched|unmatched + 候选展示(source-candidates)
- [x] 选品广场:品牌/销量/价格区间真实筛选 + 加入采集建草稿(market 源)
- [x] 站点管理:横幅/公告创建与删除(admin CRUD);字段对齐 enabled/announcement_type
- [x] Admin 平台后台:新增生图配置 tab(/admin/config 读写/回滚)
- [x] bestsellers 服务端筛选(brand/销量区间/价格区间,count 查询参数修正)
- [x] 死代码清理:删除未路由的 Listing/Templates()/Pricing()/Admin()/DataTable 演示组件与 /listing 路由
- [x] 登录余额卡真实化:GET /api/v1/mxou/balance(查询失败 fail-open 显示 —)
- [x] 接线契约测试:worker/tests/test_webui_contract.py 扫描 webui 全部 API 调用路径,
      与 worker OpenAPI 实际注册路由比对(任何缺失 → 测试失败,发版前拦截)

---

## 21. 假设与默认值

1. 频率默认:订单 15min、商品 30min、退货 30min、促销 60min、仓库 24h、分析/评分日级;worker 并发 3、job 预算 25 页、退避 5min→15min→1h、3 次失败 stale、job 超时 30min;env 可配。
2. 保留:快照 30 天、job 30 天+每店 500 条、订单缓存 180 天、退货/分析/日聚合/成本历史长期;回填 BACKFILL_DAYS=90。
3. 阶段二:广告 Performance(独立 OAuth)、财务结算对账、仓库级库存明细、退货利润扣减、采购单/1688 授权/买家黑名单。
4. v1 不拆独立 scheduler 容器;advisory lock 满足多副本。
5. 前端无「同步中心」页;新增店铺分析页与商品 Tab。
6. initial 先校验再拉数;失败退避重试,不自动禁用同步。
7. 图片镜像仅 COS 已配置时生效。
8. contributed_by_token_id 四张全局共享表不随身份迁移改动。
9. 本地无 Supabase 回退 key 哈希;生产 fail-closed。
10. POST /sync 响应与密文格式变更为 breaking,随版本说明发布。
11. 数据删除入口默认关闭,需管理端授权;吊销保留历史。
