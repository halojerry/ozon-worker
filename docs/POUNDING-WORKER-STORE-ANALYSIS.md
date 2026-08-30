# Pounding ⇆ Ozon Worker 店铺分析对接文档

> 本仓库（pounding-harness）从 **ozon-worker 的 store-analysis 批次**（`harness-store-analysis-prd.md`）接入
> 「店铺分析（读）+ 店铺执行（写）」两条新端点。本文件是**对接手册**——harness 前端/网关侧怎么接、
> 已有哪些代理、需要补什么。

---

## 1. 一段话总结

ozon-worker 侧新增了**店铺维度**的两条 REST 端点：
- `GET /api/v1/stores/{credential_id}/analysis` —— 整店分析（读）：汇总 + 利润趋势 + 低利润/缺货/可促销清单
- `POST /api/v1/stores/{credential_id}/actions` —— 店铺执行（写）：批量改价/改库存/上下架 + 活动报名/自建促销

**harness 网关已自动透传**（`/api/worker/(.+)` 正则，见 3.2），**前端 `worker.ts` 缺这 2 个方法**（见 4 接入步骤）。
网关零代码改动，只需前端按现有模式补两个函数即可在采集箱/任务中心/店铺板块调用。

---

## 2. 相关端口速查

| 端口 | 组件 | 说明 |
| --- | --- | --- |
| `8766` | 本仓库 gateway（`web/boujoy_server.py`） | 前端唯一入口，**同源代理**所有 `/api/*` |
| `8080` | ozon-worker REST | 云端 worker，`/api/v1/*` 端点 |
| `8902` | pounding-mcp tasks 服务 | 采集任务服务（独立于 worker REST） |
| `8765/9222` | knowledge preview / CDP | 与本章无关 |

---

## 3. 网关代理不变式（勿改，理解即可）

`web/boujoy_server.py` 的 worker 桥是**通用正则透传**，不是逐端点白名单：

```python
# do_GET / do_POST 内：
match = re.fullmatch(r"/api/worker/(.+)", path)      # 任意路径
url = f"{self.config.worker_url}/api/v1/{api_path}"  # 前缀 /api/v1/
headers["Authorization"] = f"Bearer {self.config.worker_token}"  # 可选注入
```

- **任何** `/api/worker/<path>` 都被转发到 worker `/api/v1/<path>`，无需为新端点改网关。
- `POUNDING_WORKER_URL`（默认 `http://127.0.0.1:8080`）+ `POUNDING_WORKER_TOKEN` 由环境变量
  或 `pounding-gateway.json` 提供。**未配 token 时网关透传，worker 会回 401**（前端据此显示「未配置凭证」）。
- worker 侧用 **Bearer token → user_id**（`_authenticate_token`），凭证归属经 `credential_service.get_decrypted`
  校验——跨租户访问返回 **404**（不是 403）。

---

## 4. 前端接入步骤（`frontend/src/harness-client/worker.ts`）

现有文件只有 `fetchDrafts / fetchTasks / fetchPoundingTasks / createListingTask`。补下面 2 个函数即可接入
店铺分析/执行。**保持同源**：只发 `/api/worker/...` 相对路径，不直接 fetch worker 绝对地址。

```ts
/** 店铺分析（读）：worker GET /api/v1/stores/{credential_id}/analysis */
export function fetchStoreAnalysis(credentialId: string): Promise<WorkerResult<StoreAnalysis>> {
  return workerFetch<StoreAnalysis>(`stores/${credentialId}/analysis`);
}

/** 店铺执行（写）：worker POST /api/v1/stores/{credential_id}/actions */
export function runStoreAction(
  credentialId: string,
  body: StoreActionBody,
): Promise<WorkerResult<{ ok: boolean; result?: unknown }>> {
  return workerFetch(`stores/${credentialId}/actions`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
```

### 4.2 同步 `harness-client/index.ts` 的 re-export（否则组件经官方出口拿不到）

`index.ts` 是 worker 桥的官方出口，已在 import + export 两个块里 re-export `worker.ts` 的函数/类型。
**新增 `fetchStoreAnalysis` / `runStoreAction` 及其类型必须同步加到这两块**，否则前端组件
`import ... from "./harness-client"` 会用不到：

```ts
// import 块：在 from "./worker" 的列表里追加 fetchStoreAnalysis, runStoreAction,
//           以及 type StoreActionBody, type StoreAnalysis
// export 块：在 worker 桥 export 块里追加 fetchStoreAnalysis, runStoreAction;
//           并在 export type 里追加 StoreActionBody, StoreAnalysis
```

### 类型定义（加到 worker.ts 顶部）

```ts
/** 店铺分析返回（worker store_analysis_service.analyze_store）。 */
export interface StoreAnalysis {
  summary: {
    product_count: number;
    low_stock_count: number;
    active_discount_count: number;
    avg_profit_rate: number | null;   // 无成本商品 → null，不编造利润
  };
  profit_trend: {
    snapshot_at: string | null;
    profit_rate: number | null;
    sales_amount: number | null;
  }[];
  low_margin_products: { product_id: string; name: string; price_rub: number | null; profit_rate: number; suggestion: string }[];
  out_of_stock_products: { product_id: string; name: string; stock: number | null }[];
  promo_ready_products: { product_id: string; name: string; profit_rate: number; candidate_action: string }[];
}

/** 店铺执行请求体（operation 决定分发到 shelf_service 还是 promo_client）。 */
export interface StoreActionBody {
  operation: "bulk_update_prices" | "bulk_update_stocks" | "bulk_archive" | "actions_register" | "seller_action_discount";
  target_ids?: string[];
  prices?: { offer_id: string; price: string | number }[];
  stocks?: { offer_id: string; stock: number }[];
  action_id?: string;
  discount?: Record<string, unknown>;
  [key: string]: unknown;
}
```

---

## 5. 端点契约（harness 侧需要知道的）

### 5.1 `GET /api/v1/stores/{credential_id}/analysis`（读）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `summary.product_count` | int | 本店未归档商品数 |
| `summary.low_stock_count` | int | 库存 `0 < stock < 10` 的商品数 |
| `summary.active_discount_count` | int | `old_price > price`（在促销）商品数 |
| `summary.avg_profit_rate` | float\|null | **有成本**商品平均利润率；全无成本 → `null` |
| `profit_trend[]` | array | 历史快照 `{snapshot_at, profit_rate, sales_amount}`（按时间升序） |
| `low_margin_products[]` | array | 有成本且 `profit_rate < 0.15`，含 `suggestion` |
| `out_of_stock_products[]` | array | `stock <= 0` |
| `promo_ready_products[]` | array | 有成本且 `profit_rate >= 0.25`（可促销/改价） |

> ⚠️ **无成本商品不填 `profit_rate`**（OzonProductCache 无成本字段，绝不编造利润）。只出现在
> `summary` + 裸商品列表，不进 `low_margin / promo_ready`。

### 5.2 `POST /api/v1/stores/{credential_id}/actions`（写）

`operation` 分发：

| operation | 分发到 | 说明 |
| --- | --- | --- |
| `bulk_update_prices` | shelf_service | 批量改价 |
| `bulk_update_stocks` | shelf_service | 批量改库存 |
| `bulk_archive` | shelf_service | 批量上下架 |
| `actions_register` | promo_client | 活动报名（`/v1/seller-actions/products/add`） |
| `seller_action_discount` | promo_client | 自建促销（`/v1/seller-actions/create/discount`） |

每个 operation 均落 `store_operation_log` 审计（success/failed 都写一行，`result` 不依赖成功率）。
`promo_client` 只碰 Seller 白名单端点，**绝不碰 Performance API `/api/client/*`**（广告投放列 roadmap）。

---

## 6. 安全须知

1. **Bearer token**：网关自动注入 `POUNDING_WORKER_TOKEN`（`_worker_proxy`）。token 必须是 mxou key
   （worker `_authenticate_token` 解出 user_id）。
2. **跨租户 404**：worker 对不属于当前 user 的 credential 返回 **404**（`get_decrypted` 校验），不是 403。
   前端按 404 处理即可。
3. **写操作需用户确认**：`run_store_action` 是真实改价/改库存/活动报名。**UI 必须在调用前给用户确认弹窗**
   （对应 ozon-worker AGENTS「执行端点只包装，不自动执行」）。
4. **访问码**：前端请求经 `authHeaders()` 自动带 `X-Boujoy-Access`（读 `localStorage["boujoy-access-code"]`），
   网关对非回环 `/api/` 调用强制校验——与现有 RPC 一致，无需额外处理。

---

## 7. 验证清单

```bash
# 网关健康（确认 worker 可达）
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8766/api/health

# （配好 token 后）店铺分析是否被网关透传
curl -s http://127.0.0.1:8766/api/worker/stores/<credential_id>/analysis -H "X-Boujoy-Access: <code>"
# 期望：200 + JSON（summary/profit_trend/...） 或 401（未配 token）

# worker 侧直接验证（不经网关）
curl -s http://127.0.0.1:8080/api/v1/stores/<credential_id>/analysis -H "Authorization: Bearer <token>"
```

前端侧：采集箱 / 任务中心新增按钮「店铺分析」（读）+「执行操作」（写，带确认弹窗），
调用 `fetchStoreAnalysis` / `runStoreAction` 即可。

---

## 8. 与 worker 仓库的对应关系

- **端点实现**：ozon-worker `worker/src/routes/store_sync_routes.py`（analysis）+
  `worker/src/routes/store_actions_routes.py`（actions）
- **服务层**：`store_analysis_service.py` / `store_operation_log.py` / `promo_client.py`
- **契约文档**：ozon-worker `docs/CONTRACT-v4.md`（已更新两侧端点与三张历史表）
- **测试**：ozon-worker `test_store_analysis.py` / `test_store_actions.py`（worker 全量 1387 passed）
