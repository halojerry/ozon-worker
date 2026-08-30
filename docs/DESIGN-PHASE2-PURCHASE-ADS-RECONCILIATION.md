# 阶段二预留设计:采购域 / 广告 OAuth / 财务对账

> 对应 PRD-store-sync-erp-v1.md §4.5、§8(P2)、§21.3。本文件是设计预留,
> 不进入 v1 实现;新开里程碑时以此为准,逐项拆 TODO。

## 1. 采购域 purchase_orders

### 1.1 目标

把「订单 → 商品 → 货源 → 采购单 → 1688 下单/发货 → 入库」闭环落 PG,
联动在售 + 在途库存,让运营不再用 Excel 管采购。

### 1.2 数据模型(DDL 摘要)

```sql
CREATE TABLE purchase_orders (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    purchase_no VARCHAR(32) NOT NULL,          -- PO-20260901-0001
    supplier VARCHAR(128) NOT NULL DEFAULT '',
    supplier_1688_id VARCHAR(64) NOT NULL DEFAULT '',
    status VARCHAR(16) NOT NULL DEFAULT 'draft', -- draft|ordered|shipped|received|cancelled
    source_cost_cny NUMERIC(14,2),              -- 合并采购总成本
    freight_cny NUMERIC(14,2),
    total_cny NUMERIC(14,2),
    expected_arrival_at TIMESTAMPTZ,
    created_by VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_purchase_no UNIQUE (tenant_id, purchase_no)
);

CREATE TABLE purchase_order_lines (
    id BIGSERIAL PRIMARY KEY,
    purchase_order_id BIGINT NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    tenant_id VARCHAR(50) NOT NULL,
    credential_id UUID,
    product_id VARCHAR(32) NOT NULL,
    source_offer_id VARCHAR(64) NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    qty INT NOT NULL DEFAULT 1,
    unit_cost_cny NUMERIC(14,2),
    line_cost_cny NUMERIC(14,2),
    status VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending|ordered|shipped|received
    purchase_tracking TEXT NOT NULL DEFAULT ''
);

CREATE TABLE incoming_stock (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    credential_id UUID NOT NULL,
    product_id VARCHAR(32) NOT NULL,
    qty INT NOT NULL,
    expected_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ,
    purchase_order_line_id BIGINT REFERENCES purchase_order_lines(id)
);
```

### 1.3 核心流程与异步边界

| 动作 | 同步 | 异步 | 说明 |
|---|---|---|---|
| 多订单合并生成采购单 | ✅ 合并计算 + 落单(<1s) | — | 按货源聚合,支持拆单 |
| 1688 实时价/库存查询 | ❌ | ✅ job | 需 1688 授权,5min 缓存,失败退避 |
| 下单/改价/发货跟踪同步 | ❌ | ✅ job | 1688 API + 回调轮询 |
| 到货确认 → 入库 → 库存联动 | ✅ 确认动作 | ✅ 联动回写 | 在售 = 现货 + 在途,低库存告警 |
| 采购单状态机推进 | ✅ | — | 人工/事件驱动,operation_log 审计 |

### 1.4 端点

- `POST /purchase-orders`(多行合并生成)、`GET /purchase-orders`、`GET/PATCH /purchase-orders/{id}`
- `POST /purchase-orders/{id}/lines`(加行/拆单)
- `POST /incoming-stock/{id}/receive`(确认收货)
- `GET /products/{id}/supply`(1688 实时价/库存,授权后)

### 1.5 竞态

- 合并生成 vs 订单同步:采购单按 source_offer_id 聚合,行级幂等唯一键
  `(purchase_order_id, product_id, source_offer_id)`;订单成本变更不回写已下单行,只告警。
- 收货 vs 库存同步:Ozon 库存为最终权威,入库先写 incoming_stock 再触发该店增量同步,
  页面以 Ozon 最终态一致。

## 2. 广告 OAuth(Ozon Performance API)

### 2.1 范围

Ozon Performance API(ads.ozon.ru)独立 OAuth2,与 Seller API 密钥分离。
拉取:campaigns、daily stats(impressions/clicks/ctr/spend/cpc)、订单归因。

### 2.2 凭据与落盘

```sql
CREATE TABLE ad_credentials (
    tenant_id VARCHAR(50) NOT NULL,
    credential_id UUID NOT NULL REFERENCES credentials(id) ON DELETE CASCADE,
    access_token_enc BYTEA NOT NULL,
    refresh_token_enc BYTEA NOT NULL,
    token_expires_at TIMESTAMPTZ NOT NULL,
    scope VARCHAR(64) NOT NULL DEFAULT '',
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    PRIMARY KEY (tenant_id, credential_id)
);

CREATE TABLE ad_campaigns (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    credential_id UUID NOT NULL,
    campaign_id VARCHAR(64) NOT NULL,
    name TEXT, state VARCHAR(32), type VARCHAR(32),
    budget_amount NUMERIC(14,2),
    raw JSONB,
    CONSTRAINT uq_ad_campaign UNIQUE (credential_id, campaign_id)
);

CREATE TABLE ad_daily_stats (
    tenant_id VARCHAR(50) NOT NULL,
    credential_id UUID NOT NULL,
    campaign_id VARCHAR(64) NOT NULL,
    stat_date DATE NOT NULL,
    impressions INT, clicks INT, ctr NUMERIC(8,4),
    spend_rub NUMERIC(14,2), orders INT, revenue_rub NUMERIC(14,2),
    raw JSONB,
    PRIMARY KEY (credential_id, campaign_id, stat_date)
);
```

### 2.3 同步与展示

- 日级 job 拉取(与 store_sync_jobs 同框架:分节 + 水位 + 退避),失败不影响卖家数据同步。
- 广告花费 = 财务对账的「费用」输入;ROI = revenue_rub / spend_rub 聚合进 store_daily_metrics 扩展列。
- webui 店铺分析页新增「广告」tab:花费趋势 / 投产比 / 活动列表(接 ad_daily_stats + ad_campaigns)。

### 2.4 OAuth 流程

- 授权跳转 → 回调存密文(token 加密复用 credential_cipher,版本前缀 v1:)
- 刷新:expires_at 前 1h 异步刷新,失败 → status=expired + 前端重新授权引导。

## 3. 财务对账(结算对账)

### 3.1 目标

把 Ozon 结算报告(realization report / finance transaction)与本地订单/成本/广告对账,
产出「平台结算 vs 本地应结」差异清单,定位扣款/佣金/物流争议。

### 3.2 数据模型

```sql
CREATE TABLE finance_settlements (
    tenant_id VARCHAR(50) NOT NULL,
    credential_id UUID NOT NULL,
    settlement_id VARCHAR(64) NOT NULL,        -- 结算单号
    period_start DATE, period_end DATE,
    amount_rub NUMERIC(14,2),
    status VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending|matched|mismatch|reconciled
    raw JSONB,
    PRIMARY KEY (credential_id, settlement_id)
);

CREATE TABLE finance_transactions (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    credential_id UUID NOT NULL,
    settlement_id VARCHAR(64) NOT NULL,
    posting_number VARCHAR(64) NOT NULL DEFAULT '',
    product_id VARCHAR(32) NOT NULL DEFAULT '',
    txn_type VARCHAR(32) NOT NULL,             -- sale|refund|commission|logistics|ad_spend|fee
    amount_rub NUMERIC(14,2),
    txn_date DATE,
    raw JSONB
);
```

### 3.3 对账规则与异步边界

- 拉单:月度/日级 job 拉 settlement + transactions(与同步任务同框架)。
- 对账 job(24h):订单级金额 + 佣金 + 物流 + 广告花费 vs 结算行;差额 > 1 卢布 → mismatch + 差异清单。
- 手工核销:webui 对账页标记 reconciled,写 operation_log。
- 利润修正:核销后 real_profit 以结算口径回写(成本不变,只调费用/扣款),保留历史。

### 3.4 展示

- 财务对账页:结算列表(状态/期间/金额)、差异清单(订单号/类型/期望/实际/差额)、
  按店铺筛选 + CSV 导出。

## 4. 其余阶段二预留(用户视角补充)

| 项 | 说明 | 优先级 |
|---|---|---|
| 1688 授权直连 | 授权后 worker 直连 1688 实时价/库存,替代 skill CDP;成本刷新 R4 联动 | P1 |
| 仓库级库存明细 | warehouse_cache 升维到 SKU × 仓库 × 在途 | P2 |
| 退货利润扣减 | 退货结算进入 finance_transactions 后,回写订单 real_profit 修正 | P2 |
| 买家黑名单 | 高风险买家标记 + 订单拦截提醒 | P3 |
| 未匹配货源工作台增强 | 候选合并/批量绑定/1688 实时比对 | P2 |
| 批量 AI 填写 | 采集箱 CSV 导入后批量 AI 补属性/标题 | P2 |

## 5. 边界与不做清单

- 不做自营 1688 店铺运营(只读采购域)。
- 不做 Ozon 广告投放(只读统计 + 花费对账)。
- 不做银行/第三方支付对接(对账以 Ozon 结算报告为权威)。
