# 05 · 定价链：三档公式 / 佣金 / 物流 / 汇率 / min_price

> 唯一入口 `worker/src/utils/pricing_estimate.py compute_price`（pricing_node、/estimate 双端点、webui 计算器、变体循环、retry repair_pricing 全部经它——禁止内联公式）。

## 1. compute_price 公式（逐字核对 pricing_estimate.py）

入参：`total_cost_cny`（采购+物流+包装，包装固定 ¥2.0）、`margin_rate`、`commission_rate`、`fx_buffer`（仅 RUB 生效）、`currency_code`、`exchange_rate`（None→强制 CNY 口径 :102）、keyword-only `margin_anchor/margin_floor/variable_cost_rate/promo_variable_cost_rate`。

**三档**（divisor = 1 − c − vcr；促销档 1 − c − pvcr）：
- 日常价：`price = ⌈cost × (1+m) / (1−c−vcr)⌉`；RUB 再 ×(1+fx_buffer)×fx
- 划线价：`old = ⌈cost × (1+anchor_eff) / divisor [×fx]⌉`，anchor 缺省 = m×1.2；随后**强制 old ≥ ⌈1.2×price⌉**（price≤25 时 max(price+5, ·)）（:150-166）
- 促销底线：`promo = ⌈cost × (1+floor) / (1−c−pvcr)⌉`；margin_floor=None → 不产出
- 利润口径 = **销售净利率** `profit_rate = (price×(1−c−vcr)−cost)/price`（:187-193）

**单档 legacy**（margin_anchor 与 margin_floor 都 None）：`price = ⌈cost×(1+m)/(1−c)⌉`；old 强制 1.2×；利润口径 = **成本利润率**（:134）。

⚠️ **语义陷阱**：`compute_price` 自身全缺省→单档；但 pricing_node/estimate_service 外层判定全缺省→**三档**（1.5/2.0/0.6/0.155/0.245）。裸调 compute_price 的第三方代码会得到与 graph 完全不同的价（09-#4）。

## 2. 三档/单档激活判定

- pricing_node（pricing_node.py:160-176）：`dual_margin = ("margin_floor" in extensions) or ("margin_anchor" in extensions) or (margin_rate 缺失)`——**键存在**即三档。
- estimate_service（:93-124）：`three_tier = floor 非 None 或 (margin_rate 与 ext_margin 均 None)`——**值非 None**。
- 微差后果：extensions 同时有 margin_rate+margin_anchor 无 floor → graph 定价三档、/estimate 单档，两处给用户看的价不同（09-#1-定价）。
- 未配置 margin 的店：skill v0.65 起不兜底注入（cloud_probe.py:3094-3099）→ 走三档默认——**上架价基线本身已换**（行为变更，发版说明已提）。

## 3. 佣金解析链（utils/commission_resolver.py）

优先级：**explicit > category_commission 缓存表（band 选段，≤180d 新鲜）> extensions segments > 0.10**。

- `pick_price_band:66`：RUB 价 ≤1500→leq_1500 / ≤5000→leq_5000 / >5000→gt_5000；无价→leq_1500（自称最保守）。
- `resolve_commission_rate_detail:127`：缓存段值 ≤0 视同未命中（0% 污染守卫读侧）；>180d→stale；超龄且无 segments→`fallback:stale`（0.10）。
- **provisional-price band pass**（pricing_node:209-235 / estimate_service:142-171）：先 0.10 算临时价→选档→resolve 真佣金→重算，破解「档位依赖价格/价格依赖佣金」环。⚠️ 两处临时价口径不一：pricing_node 不传三档 kwargs、estimate_service 传——选出的价格段可能不同（09-#7-定价）。
- `category_commission` 表：FBS/FBO×三段，全局共享无 tenant；`upsert` 随用续期 updated_at；**写侧无入参守卫**（当前唯一写方 learning approved 回填 `_backfill_category_commission` LR:319-378，parse 0% 守卫天然拦 0，风险受控）。
- `/api/v1/commissions/lookup`（main.py:3612）+ MCP `lookup_commission` + skill `_query_commission_from_worker`（fbs 段优先）。

## 4. 物流（utils/logistics_quote.py）

- 费率表 = 独立 `logistics_rates` 表（~142 条真实费率，非 SystemSettings）；四级 fallback：Q1(3PL+等级+重量+尺寸)→Q2(仅重量)→Q3(同等级跨 3PL)→RETS Standard→绝对兜底 max(5, 0.05×weight)。
- **体积重计费**：billable = max(实重, D×W×H ÷ vol_weight_divisor)；`cost = base + per_gram × billable`（:144-149）。
- 3PL/等级探测 `/v2/delivery-method/list`，失败回退 ("RETS","Standard") 绝不抛。
- 端点 `POST /api/v1/logistics/quote`（`_require_bearer`+专属限流键）；skill `_query_logistics_from_worker`（缺重按 500g 查表→last-good 同重量带 24h→本地分段 ¥6/¥8/¥15）。

## 5. 汇率三级链（utils/fx_rate_service.py:53-82）

① PG 缓存（24h 新鲜度）→ ② open.er-api.com live（10s，成功回写 PG）→ ③ **双失败 fallback 12.0**（marks `exchange_rate_fallback` 留痕）。CNY 店恒 1.0。预热 `refresh_cny_rub_if_due`（24h 节流，周期钩子）。⚠️ fallback_12 是静默价差源：RUB 售价直接随兜底值跳动；/estimate 端点不接汇率（强制 CNY 口径）——UI 对比价与 graph 实际价天然不同币值（09-#10-定价）。

## 6. 变体循环定价

pricing_node:372-421：每变体以 var.price（1688 采购价）+物流+包装为总成本，**统一走 compute_price**（v0.60 消除 ×1.15/×1.2 漂移）→ `pricing_info.variant_prices` → prepare 数量拆分(:3637)与多 SKU(:3872) 覆盖 assemble 侧初值。⚠️ assemble 变体初值是 **CNY 采购价**，依赖 prepare 覆盖才正确——旁路 prepare 直传会让 CNY 数以 RUB 名义上架（v0.14 P1-1 历史事故，prepare:3637 注释，09-#8-定价）。

## 7. min_price 地板（promo_price ↔ Ozon min_price）

- **promo_price 不进 /v3/product/import**；import 只回 task_id，真实 product_id 要等轮询——所以补送点在 **ozon_status_node:269-292**（imported 后）：仅「全新 CREATE + 单变体 + 有 promo_price」调 `/v1/product/import/prices`；UPDATE/跟卖/404 回退/多变体跳过；失败仅 warning。
- `update_min_price_floor`（ozon_client.py:380-433）：`_floor = min(max(请求值, ⌈price×0.5⌉), price)`——≥售价 50% 防 too_small、≤售价防 price_less_than_min_auto_price。
- retry 路径同源：repair_pricing(:2430) 与 `_fix_via_prices_update`(:2848) 都用 `derive_list_prices` 派生。

## 8. 信封侧输入与输出

- 信封**没有 draft.pricing 结构**——定价键全在 `extensions`（margin_rate/commission_rate/fx_buffer/margin_floor/margin_anchor/vcr/pvcr，`_merge_config_tiers` 三段降级注入，见 01§2；follow/降级腿口径不一致见 09-#5）。
- 消费唯一入口：webui PricingPanel→POST /estimate；`/api/v1/drafts/{id}/estimate` 与 `/api/v1/estimate` 共用 `estimate_service.estimate_from_envelope`→compute_price；ai_field_service 亦显式复用。
- 输出 `pricing_info`：cost/logistics/包装/total_cost/margin/commission(+source)/fx marks/price/old_price/[promo_price+三档参数]/profit_estimation{profit_cny,profit_rate,formula,breakdowns}/[variant_prices]/[price_gap_warn]。**PricingOutput.price/old_price 是 str**（state.py:430-435）；下游读 pricing_info 必须声明进 Input（ozon_status/learning 已补）。

## 9. 疑点（并入 09-findings §定价）

1. 三档判定双实现微差（键存在 vs 值非 None）→ graph 与 /estimate 可能给出不同档位。
2. 抬重后定价不重算（volume_weight_guard 在 pricing 之后发生；同带影响趋零，已知 defer）。
3. upsert_category_commission 无入参守卫（0 段值可落库，当前单写方受控）。
4. compute_price 裸调全缺省=单档 vs 外层全缺省=三档（语义相反）。
5. stale 标记误标：缓存超龄但 segments 命中时 source=segments 却 stale=True → pricing 写 commission_source="stale_fallback"，审计带偏。
6. 中性档不一致：pick_price_band(None)=leq_1500 vs pricing_node 无价手工 leq_5000 vs 回填 leq_5000。
7. provisional 选档价口径不一（三档 kwargs 传/不传）。
8. assemble 变体价格初值是 CNY 采购价（依赖 prepare 覆盖）。
9. pricing_node 算出的 band 变量只用于日志（resolve 内部重算，逻辑重复易漂移）。
10. 汇率 fallback_12 静默价差 + /estimate 不接汇率（币值混淆）。
