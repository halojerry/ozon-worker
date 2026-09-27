# 01 · skill 线：采集 → 选品 → 信封（不上架）

> 范围：`skill/scripts/`。skill 的铁律：只抓取、只组装 `GraphInput` 信封，**不调任何 Ozon 上架 API**。
> 锚点为 2026-09-25 dev 工作区快照。出口码总约定（cli.py:19-24）：0=成功（含 --no-submit/--to-box）、
> 1=鉴权/环境/参数、2=产品数据校验失败、3=提交失败/拦截/--wait 终态 failed、4=heavy 闸被占。

## 1. CLI 命令矩阵

`main():3904` → win32 UTF-8 → `_init_sentry:3407` → 运行日志 → `_preflight_runtime:3969`（3.12 re-exec/依赖自愈 venv）→ 静默更新检查 → `args.func`。

| 命令 | 闸 | 调用链要点 | 出口码 |
|---|---|---|---|
| `check` `--logs [id]` | 无 | `cmd_check:1089`：浏览器→CDP 自启→1688 登录→aibuy 反爬 cookie→Ozon DataDome→seller 会话探针(`probe_seller_session_alive`,ozon_seller_analytics.py:1273，只看状态码)→凭证→Sentry→/health→余额→Ozon API 鉴权 | 0/1 |
| `search` | 无 | `cmd_search:338`→AK 搜索→本地利润估算(366-390)→批量 `_submit_one:455`（**已接三闸**：preflight 无条件 + min-margin/min-density flag 缺省 0=关；全拦/全败 exit 3） | 0/1/3 |
| `graph` | ✅ | `cmd_graph:713`→readiness→`parse_platform_url` 分派→`build_graph_envelope_with_retry`→竞品属性透传(789-810)→预估+min-margin(852)→preflight(882)/min-density(894)→submit→`--wait`→`_wait_task_terminal:154` | 1/2/3/0 |
| `follow` | ✅ | `cmd_follow:1514`→`cloud_probe.follow_sell_cloud:4010`→低利润 exit 3(1573)→`--wait` | 1/3/0 |
| `discover` | ✅ | `cmd_discover:1715`→fx 三级(1726)→readiness→蓝海行(1757)→`collect_and_analyze:820`→可选 fission(1812)→`_finish_discover_flow:1846`（挑选→`match_selected:1949`→评审→导出→`build_envelope_from_discovery:2163` 批量提交） | 2/1/0 |
| `discover-multi` | ✅ | `cmd_discover_multi:2388`→`_split_keywords:2226`→串行采集→单次并行分析(2274)→同 discover 收尾 | 同上 |
| `discover-task` | ✅ | `cmd_discover_task:2987`→拓店预算映射(2562)→filters 白名单(2717)→`rank_match_pool`+`match_selected`（限额/连击早停 3200）→逐条入箱/提交(3273)→状态落盘(2528) | 2/1/0 |
| `image_search` | 有意无 | `cmd_image_search:961`：aibuy(996)/cdp(1001)/ak(1009) 三源 | 0/1 |
| `queries` | 有意无 | `cmd_queries:4065`：cookie 直调优先(4081)→CDP 兜底(4097)→`analytics_upload`(4131)→ozon-bestsellers 回馈池(4141) | 0/1 |
| `seller` | ✅ | `cmd_seller:4051`→`ozon_discovery.fetch_seller_analysis:3656` | 0 |
| `session-sync` | 无 | `cmd_session_sync:4395`→收割 seller cookie→POST /credentials/{id}/session；无 sc_company_id `SystemExit(2)`(4451) | 0/1/2 |
| `query` | 无 | `cmd_query:4254`→check/poll→`_print_query_result:4190` | 0/1 |
| `report` | 无 | `cmd_report:4334`→POST /api/v1/error_reports | 0/1 |
| `set_store/list_stores/set_token/set_ak/get_ak/category/probe/update/cleanup/import-cookies/probe-win-cookies` | 无 | 见 cli.py 各 cmd | 0/1 |

NEXT 行统一 `_print_next`（cli.py:143-151，`👉 NEXT:` 前缀）；错误出口=四件套（what/why/修复命令/下一步）。

## 2. GraphInput 信封结构（外层 `{token, ozon_client_id, ozon_api_key, envelope:{draft,source,extensions}}`，cloud_probe.py:2567）

装配四链：1688 直采 `build_graph_envelope`（cloud_probe.py:1815）、跨平台 `_build_graph_envelope_cross_platform:1532`、discover 候选 `build_envelope_from_discovery:2910`、follow `follow_sell_cloud`（信封段 4443-4602）。

### draft 层（核心键）

| 键 | 写入点 | 语义 |
|---|---|---|
| item_id/title/description/images/weight/dimensions/purchase_* | cloud_probe.py:2446-2459 | 基础字段；图片过滤占位/追踪像素(1901-1944)；attributes 三源合并上限 40(2137-2174) |
| `ozon_category` | manual 1954-1967 / search_kw 猜测 2030-2036（过 `_category_guess_consistent:1338`）/ discover 页面真值 `_apply_discover_page_truth:2766` / follow 4567-4582 | `{description_category_id,type_id,source,namespace[,category_path,breadcrumb_language,web_category_id]}`；source ∈ manual/search_kw/what_to_sell/page；无真值不写键 |
| source_category/source_category_id | 2469-2474 | 1688 中文类目路径+叶子 cid（**跨平台链有意不写 cid** 1719；discover 降级链两者皆无） |
| variants / sku_id 三键 | 2476-2497（笛卡尔积 2217-2348、引流 SKU 过滤 2391-2409、单产品折叠 2411-2420） | is_multi 才带 variants |
| ozon_product_id / ozon_attributes(+_category) / ozon_url / ozon_title / competitor_price | follow 4466/4528-4546/4583-4586；discover 2799-2846 | 跟卖/竞品上下文；`ozon_attributes_category` 只信 what_to_sell 权威 dc |
| dimensions_estimated / weight_estimated | `_validate_and_fix_product_data:1025` | 缺重→**50g** 兜底(1049-1052)；缺尺寸 400kg/m³ 2:1.5:1 估算(1055-1078)；密度脏数据重估(1108-1130) |
| weight_from_pool_variant | `_apply_pool_variant_weight:2872` | 池 variant 重量覆盖，clamp [10,200000]g |
| **notes** | **不进 draft/payload**——`submit_draft` 只放请求体顶层 `body["notes"]`（371-375，红线注释） | 运营态，worker 零消费 |

### source 层
purchase_url/purchase_cost(2501-2503)、source_category_path/category_id(2504-2505，仅 1688 链)、platform(1745，仅跨平台)、`match_category_id/name`（`_inject_discovery_match_category:2689`；discover 两路 3013/3088 + follow 4505-4519 都注入）。

### extensions 层

| 键 | 写入点 | 语义 |
|---|---|---|
| margin_rate/commission_rate/fx_buffer/margin_floor/margin_anchor/variable_cost_rate/promo_variable_cost_rate | `_merge_config_tiers:1490`（graph/跨平台链）；**follow 腿手工只注 7 数值键(4479-4484)；discover 降级腿只注 3 键(3096-3099)** | 三段降级：显式 > worker 模板(`get_template_profile`,config_store.py:589) > stores.json。三条腿口径不一致 → 见 09-#5 |
| offer_id_prefix / traffic_keywords / follow_type | `_merge_config_tiers` 键集 1478-1482 | 9048 前缀/SEO 流量词只有 graph/跨平台主链带；**follow/降级不带** |
| follow_sell / follow_type | discover 2960/2966；follow 4467/4475 | 跟卖标记；hand=重建（默认）、api=import-by-sku、discover=变体 |
| commission_segments | discover 2978-2984 | `{fbs,fbo}` 三段费率（rfbs→fbs、fbp→fbo） |
| match_evidence | `_assemble_match_evidence:2577`；discover 2991/follow 4490 | `{confidence,badge_eff[,method],trusted}`；trusted = method=="aibuy" 或 badge_eff≥1.0 |
| discovery_meta | `_assemble_discovery_meta:2618`（键清单 2641-2663） | 采集箱展示快照：ozon_*/蓝海分/月销/follow_profit_cny(默认 0.0)/ozon_old_price(None 省略) 等 |
| competitor_weight_g / competitor_dimensions_mm | discover 2850-2869；follow 4591-4597 | worker `_resolve_weight_dimensions` 兜底链 |
| cdp_degraded | 2535 | CDP 全败降级 api_only 标记 |

## 3. 选品引擎（ozon_discovery.py `collect_and_analyze:820` 漏斗）

| 阶段 | 函数 | 内容 |
|---|---|---|
| ① 行级粗筛 | `_passes_base_filter:1699`（规则 1606-1625；ai 档只判 seller_count 1711） | 18 项 BASE 规则 |
| ② widget 全量 | `_analyze_product:575` + `_apply_filters:920` | 品牌/关键词/价格过滤；`min_competing_price`(641) |
| ②b 运营富化 | `_enrich_with_seller_metrics:743` | **池优先** `_apply_pool_metrics:683`（stale 只填值不计命中）→ cookie 直调(775) → CDP(789) → 逐 SKU(811)；map 定稿钩 `_giveback_metrics:652`(802) |
| ②c 完整档 | `_apply_profile_filter:1738` + 发货模式 `_apply_sales_mode_filter:1641` | ai 档/FBO-FBS-rFBS 标注 |
| ④ 1688 匹配 | `match_selected:1270` → `_search_1688_source:3321`（**aibuy→CDP→AK 图搜→AK 关键词**四级）→ `_pick_best_match:2840`（评分 = idx_rank×50+conf×30+badge×20，护栏阈值 2826；trusted 前列放行 conf≥0.5 3024；LLM 兜底 3044）→ `_process_match:1326`（conf<0.3→no_match，`_MIN_SOURCE_CONFIDENCE:54`；`_backfill_1688_category:3250`；`_category_semantic_review:3282` 不一致 conf 封顶 0.5） | 货源有效性门槛 |
| ⑤ 利润/蓝海 | `_calculate_profit:3887` / `calculate_blue_ocean_score:3969`（9 因子） | profitable/rejected 分流 |
| ⑥ 挑选 | `split_selection_rules:1793`（采集期 vs 匹配期 margin 两段）/`apply_selection_rules:1821`/`rank_match_pool:1811` | 规则两段式 |

**利润公式**（`_calculate_profit:3887`）：收入 = ozon_price × fx（fx 三级 CLI>店铺>settings>0.075，cli.py:1726）；佣金四级 = 显式 > worker `/commissions/lookup`(`_query_commission_from_worker:3815`) > 候选分段(`_commission_band_rate:3781`) > 默认 12/14/18(`DEFAULT_COMMISSION_SEGMENTS:36`)；物流 = worker `/logistics/quote`(`_query_logistics_from_worker:3718`，缺重按 `DEFAULT_WEIGHT_G=500` 查表) → last-good 同重量带 24h(`_last_good_quote:3556`) → 40 CNY/kg 保底(3940) → `estimate_shipping_cny:57` 分段 ¥6/¥8/¥15。利润=收入−(采购+物流+佣金)；`follow_profit_cny/follow_margin` 同链换收入端（佣金沿用主价带，已知简化 3961）。

**四个选品出口键**：follow_profit_cny/follow_margin 默认 0.0（234-235，唯一写入 3964）；ozon_old_price 默认 None（236，widget originalPrice，**绝不写 draft.original_price** 608-609）；match_1688_freight_cny 默认 None（237，唯一读者 1332 读 `match["freightCny"]`——**当前无通道产出该键，恒空**，见 09）。

## 4. 类目真值链（skill 侧）

- widget breadCrumbs：`ozon_widget.py:303-309` 解析 → `_derive_category_from_breadcrumbs:522` 抠 `/category/xxx-14500/` 数字 → `web_category_id/category_path`。
- `_apply_discover_page_truth`（cloud_probe.py:2806-2838）优先级：**candidate 数字 dc/tp（what_to_sell）> draft 既有值 > page 路径先验**（page 只补路径/语言/web_category_id 三键，绝不伪造 dc/tp）。
- `_category_guess_consistent:1338`：R1 gram 覆盖率≥0.34 / R2 CJK 尾字核对 / R3 零交集毒猜——任一命中拒写。
- search_kw 猜测：1688 类目末级词→`_category_search_variants:1294`→max_results=3 逐个过闸取第一个一致（2001-2019）。
- manual `--category-id/--type-id`：两者同时非空才生效（1954-1967），source="manual"，跳过自校验。
- follow：dc/tp 只信 what_to_sell 权威形态（4567-4582）；面包屑只带 web_category_id 线索（**绝不由面包屑伪造 dc/tp**，2026-09-24 follow×5 事故）。

## 5. 重量/尺寸真值链

`_sanitize_weight_g`（cloud_probe.py:1520）：(0,10)g 归零（Ozon 10g 硬下限）→ 下游 50g 兜底。页面重量来源链：packaging_rows(2055) → dim_text(2078) → 描述正则(2093) → contextPath unitWeight(2175)。竞品重量：what_to_sell（follow Step2.5 4179-4236；discover 2850）→ 属性表 `extract_weight_dims_from_attrs`（ozon_scraper.py:57）。池 variant 真值 clamp [10,200000]g。**variant_v2 真值链（fetch_variant_truth，ozon_widget.py:918）三段断链仍未接线**（零生产调用/无人传 variant_payloads/needs_variant_sync 零消费）——AGENTS 口径在代码现状成立。

## 6. 门禁体系（skill 侧）

| 门禁 | 实现 | 行为 |
|---|---|---|
| `_heavy_gate` 跨进程锁 | cli.py:640-693；锁 `data/locks/heavy_cdp.lock`（flock，lock_utils.py:41-70）；受闸六命令 discover/discover-multi/discover-task/graph/follow/seller；**batch_test 进程内直调不进闸**（见 09-#12） | 被占 exit 4 + 人话 holder 信息；`--wait` 30s 心跳排队(622)；`--force` 跳(652) |
| `_source_preflight` | cloud_probe.py:1206-1237：图>0 且属性=0→反爬嫌疑；purchase_cost≤0→源失效 | graph 腿 cli.py:882-891 拦 exit 3，`--to-box` 只 warning 放行；follow 腿已接线（提交段 `blocked_reason=source_preflight` → exit 3；展示态仅警示） |
| `--min-margin` | `_min_margin_block_reason:128`；graph cli.py:852 / follow 在 follow_sell_cloud:4608（缓存命中也过闸 4071） | exit 3（blocked_reason=low_margin） |
| `--min-density` | `_check_min_density`（cloud_probe.py:1240） | exit 3 |
| `--no-submit` | cli.py:859-861 跳过整个提交段（**连 preflight 也跳**，09-#9） | 展示态，NEXT 提示确认后重跑 |
| `--to-box` | `submit_draft`（cloud_probe.py:329-408）POST /api/v1/drafts，fail-hard 不降级直上(381-387) | 0（draft_id 出口） |
| `--wait` | 双语义：闸排队 + 提交后 `_wait_task_terminal:154` 轮询终态；failed → graph 腿 exit 3(925)，**discover/discover-task 批量腿只打印不回传失败码**（09-#10） | — |
| check | aibuy 反爬 cookie 就绪（1269-1282，未预热自动开页预热）+ seller 会话探针（cookie 在 ≠ 会话活） | all_ok 汇总 |

## 7. follow 跟卖链（`follow_sell_cloud:4010-4680`）

1. `parse_ozon_url:3204` → **信封级缓存 6h**（key=`product_id:store_id`，4056-4098；命中也过 min-margin；重测须同清 `ozon_cdp/`+`follow/` 双 ns——cleanup 无按 ns 精清入口，09-#4）。
2. `scrape_ozon_product_via_cdp`（ozon_scraper.py:447）：webShortCharacteristics / **webCharacteristics 全表**（API 已不下发 → **DOM 兜底 `bring_to_front()` 前置** + 点展开 + 滚动×10 + dl/dt 解析，792-842——后台 tab IntersectionObserver 永不派发是 gate 首跑失败根因）/ webAspects / breadCrumbs / webSellerList（卖家数+minPrice）。
3. Step2.5 竞品运营数据：cookie 直调 what_to_sell 优先（4188）→ CDP 兜底；**Seller 空间 category2Id/3Id 覆盖面包屑**（4210-4228）。
4. 1688 找货源四级（4238-4333）+ 相关性护栏（4368）+ `--review` 人工评审（4381）+ 无货源硬拦截（4654，不再 api 空壳跟卖）。
5. 信封差异：`draft.images = 竞品主图[:1]`（4524，绝不混 1688 图 4452）；title=俄语原标题；`follow_type="hand"`；竞品图只进 `extensions.competitor_ref_images`；**不注 offer_id_prefix**；提交前预估+min-margin（4604-4626）。
6. worker 侧消费差异 → 见 `08-selection-follow-ecosystem.md` §2。

## 8. 模型调用（skill 侧）

**生图：零**（image_preprocessor 只压缩）。**LLM：4 处 deepseek-v4-flash 直调**（绕过 worker 封装：无限流/无台账/无余额闸——见 09-#8）：
① cloud_probe.py:3566 类目 ZH→RU 翻译兜底；② cloud_probe.py:3910 `_translate_slug_to_cn`（30 天缓存）；③ ozon_discovery.py:3127 `_llm_semantic_match`（同品判定/类目一致性）；④ ozon_discovery.py:3187 `_llm_disambiguate_category`。
