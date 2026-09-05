# AGENTS.md — ozon-worker 工作区

本文件是工作区级导航。各子项目（skill/worker/pounding-sidebar）有更详细的文档，改动前请先读对应文档（见「深入阅读」）。

## 最近更新（v0.65.0 — 三档双价格默认激活 + promo 落地 min_price + 部署/时区修复）

> 2026-09-05。已发版（VERSION 四源 0.65.0）。v0.60 三档双价格体系（用户拍板口径）真正默认
> 生效 + v0.64.1 后一批行为变更与部署问题修复（P1-1/P1-2/P1-3 收口）。

- **三档默认激活（行为变更）**：`pricing_node` dual_margin 判定对齐 `estimate_service`——信封
  floor/anchor 在场**或 margin 键全缺** → 三档（缺省日常 margin 1.5/anchor 2.0/floor 0.6/vcr
  0.155/pvcr 0.245）；显式 margin_rate 无 floor/anchor → 旧单档逐字保持。⚠️ **未配置 margin 的
  店上架价会变**（旧成本利润率 0.25 → 三档净利率口径），这是 v0.60 拍板本意；显式配置店零变化。
- **skill 去兜底注入**：`build_envelope_from_discovery` 不再强注 0.25/0.10（曾压住三档 + 短路
  佣金解析链），「配置了才注入」对齐 follow 路径。
- **webui**：模板编辑器补 4 三档字段 + 定价器默认留空（走 worker 三档）。
- **promo_price → min_price**：`/v3/product/import` 异步只给 task_id，真实 product_id 须
  `ozon_status_node` 轮询 import/info imported 后才到手 → 确认新建 CREATE 单 SKU 后经
  `/v1/product/import/prices` 补送（防御 ≥售价50% 且 ≤售价）；UPDATE/follow/多 SKU 跳过；
  失败仅 warning。改 min_price 链路见 `update_min_price_floor`（ozon_client）+ `try_set_min_price_floor`（ozon_upload）。
- **部署 P1-1**：cos-update.sh 解包自动恢复定制 compose（`.env` host≠postgres 或
  `COS_UPDATE_PRESERVE_COMPOSE=1`）+ 回滚含 compose——服务器升级可直接跑脚本不再白费构建。
- **时区 P1-2**：metrics_aggregation 两步日期口径统一 `::date`（会话时区）——+08 服务器
  profit_amount 恒 NULL 修复。
- **vision-exp P1-3 已实测可用**（本地真实 token：纯文本 200 + 真实 alicdn 图文 200），回滚预案
  = 服务器 `sed -i 's/deepseek-v4-flash-vision-exp/deepseek-v4-flash/g' worker/config/*.json`
  热加载即回退（bind mount，无需重建镜像）。

## 最近更新（v0.64.1 — MXOU 有余额却误报 402 余额不足 紧急修复）

> 2026-09-05。已发版（VERSION 四源 0.64.1）。v0.64.0 上线后紧急止血：多用户「平台有余额
> 却 submit_task 402 current: 0.0」——根因在 v0.62.4 起余额源读法 + api.mxou.cn 响应形态。

- **根因**：api.mxou.cn(newapi) 给订阅/无限账号的 `subscription` 响应带字面 `balance` 字段
  （常为 0 哨兵，非欠费）。`get_mxou_balance`（`utils/mxou_api.py`）拿到 `balance:0` 时未
  consult 同响应 `soft_limit_usd/hard_limit_usd`（订阅=100M 哨兵）→ 0.0 当欠费 →
  `_check_balance_cached` 30s 缓存放大 → 批量 402「余额不足 (current: 0.0)」。
- **修复三件套（f7fbcaed）**：B1 `get_mxou_balance` 字面 `balance≤0` + 任一 limit>0 → 返回
  None 降级（兜底放行），仅 `balance<0` 无 limit 哨兵（真欠费）原样拒；B2 `_check_balance_cached`
  首查 0.0 二次直查防缓存污染；B3 402 文案带 source 标识（真欠费原文案不变）。回归
  `test_balance_zero_confidence_v0641.py` 6 + 余额专项 59 passed。
- ⚠️ **余额判定红绿灯**（改余额链前必读）：真欠费 = MXOU 实查返回**负数**（无 limit 哨兵）；
  订阅/无限账号字面 `balance:0` 是**哨兵不是欠费**；Supabase `users.quota` 是从不同步的
  stale 镜像，只在 MXOU 实查失败时兜底且 unlimited 恒放行。**不要**把任一 0.0 简单当欠费拒绝。

## 最近更新（v0.64.0 — 视觉模型切换 + 生图精简 10→5 + 属性/类目/状态流三类修复）

> 2026-09-05。已发版（VERSION 四源 0.64.0）。v0.64 vision 切换（类目/属性/生图接
> deepseek-v4-flash-vision-exp）+ 本批修复（Sentry 五簇 aca6d61f / 登录等待 3633425d /
> 砍图 cf0fc5f7 / 类目 B1B3 6b6acfd6 / 状态流 C1-C4 42f3d61d+beab721d+a6d60932+0f59d067）。

- **视觉模型切换（v0.64 主体）**：`call_mxou_chat_api` 加 `image_urls`（Vision array ≤4 张）；类目 LLM 匹配传图；属性消歧接线（多候选 LLM 消歧带图，不再盲补首值）；新增 `_infer_attrs_from_vision`（非硬编码 ID）。9 config + 8 py 全量换 `deepseek-v4-flash-vision-exp`。
- **生图默认精简 10→5**：`image_gen_plan.DEFAULT_PLAN` = main/white_bg/multi_angle/detail/scene_1 + variant 变体主图；social_proof/comparison/scene_2/scene_3 默认关（槽位仍在 ALL_SLOTS，可经 `config.configurable.image_gen_plan` 覆盖重开，非删除）。prepare 跟卖告警阈值 10→3。
- **属性填充 A1/A2**：`_infer_attrs_from_vision` `_INFER_KW` 补中文同义词（原全俄语 → 中文 schema 空转）；`_fill_optional_dict_attrs` 加中文直搜旁路（schema/draft 名共享中文字符才触发，覆盖形状/图案/产地等无同义词组属性）。
- **类目错放 B1/B3**：skill 权威类目标 `source=what_to_sell`（discover 复用不再降标 search_kw 被 L2 挤掉）；Step6.5 一致性重配对 `match_layer==Skill` 豁免（guard 式）。
- **状态流 C1-C4**：N4 retry 子图 dc/tp/属性回传主图（learning 不再按旧类目落库）；N5 BR_chinese 回灌转 Ozon 格式；N6 validate 对带 product_id 的 UPDATE 豁免类目必填；N5b 学习表原子 upsert。

## 最近更新（v0.63.1 — v0.63.0 补漏加固 + 凭证端点 422 化）

> 2026-09-02。已发版（VERSION 四源 0.63.1）。v0.63.0 上线后第一批补漏 + 链路测试实证的凭证端点修复。

- **MXOU 永久错误全链路 Fatal 化（v0.63.1 补漏）**：5 处上层 `except` 二次吞掉 OutOfQuota 修复 + scene_2/3 内容违规 re-raise + 退避告警分级 + 退避变量登记（git `v0.63.0..HEAD` 7 commits，7477b626 起）。
- **凭证创建/轮换校验失败 500 → 422**（链路测试实证）：`credentials_routes` 手拆 body 的 `model_validate` 包 `ValidationError` → `HTTPException(422, detail)`——此前字段缺失/类型错（如把 `api_key` 写成信封词汇 `ozon_api_key`）冒泡成裸 500。回归 `test_credentials_validation_422.py`（纯 mock 2 passed）。
- **契约文档补齐 `POST /api/v1/credentials` 请求体**：`api_key` 字段名纠错 + credential_id 来源（`docs/CONTRACT-v4.md` 1b.1.1 + `webui/docs/BACKEND-API-REQUIREMENTS.md` + AGENTS.md 端点表补 /credentials、/drafts）。

## 最近更新（v0.62.0 — Sentry 六类根因修复 R1–R6）

> 2026-08-31。已发版（VERSION 四源 0.62.0）。针对生产 Sentry 100 issues / 3645 events
> （集中在 2026-08-11 堆积）按根因分六类修复，执行记录见 `docs/PLAN-sentry-r1r6-v1.md`。

- **R1 余额治理**：`_is_out_of_quota_response` 401 纳入永久错误分类（MXOU 余额耗尽/认证失效同返 401，
  不再走普通 4xx 重试）；低余额用户通知 `BALANCE_ALERT_THRESHOLD`（默认 ¥50，可配）+ token 指纹
  30min 去重 + `TASK_NOTIFY_URL`（与任务终态通知同通道）；`_check_balance_cached` 缓存升级为
  **token 指纹绑定**（多用户不互相污染），main.py `_check_mxou_balance` 复用该缓存。
- **R2 字典分页真 bug**：`fetch_ru_dict_value` 增加分页循环（最多 5 页，`has_next` 驱动）——
  此前单次 POST limit=5000 无翻页，大字典（8229 类型等）目标 id 不在首页 → 空值 → 必填属性缺失。
- **R3 属性缺失联动**：23487（Производитель/制造商）supplier 缺失 → `Нет бренда` 安全兜底，
  prepare/assemble/retry 三处一致；5379 维持宁缺毋滥；一致性测试锁定三处接线。
- **R4 生图内容违规分类**：新增 `MxouContentViolationError`，grsai violation/关键词命中 → 不重试
  不降级（防重复烧额度）；普通 failed 保留有界重试；8 个生图节点异常透传 → 任务明确失败。
- **R5 描述拉丁误报**：尺寸乘号归一化（`10x10x5` → `10×10×5`）+ 残留单字母拉丁清理（保护西里尔词边界）。
- **R6 Sentry 噪音**：`task_rerun` 仅 STALE_RUNNING/ZOMBIE/超时恢复才上报；stale 全零不报守卫保留。
- **测试**：worker Docker 全量 **1502 passed**（原 1454 + 47 新增 + 1 拆分）；CI 11 jobs 全绿；
  CD 镜像/Release/COS 包就位。⚠️ 发版踩坑见下方「版本管理」——skill 二进制打包校验依赖
  `skill/VERSION`（非 SKILL.md frontmatter 源码），bump 版本必须四源同步。

## 最近更新（开发中 — harness-store-analysis 数据沉淀：3 张新表 + 店铺分析/执行端点 + 专家版图）

> 2026-08-22。**未发版**（VERSION 四源仍 0.60.0），属「数据沉淀 + 店铺精细化运营」阶段。本批把 store/store 指标、操作审计、选品洞察落 PG，暴露整店分析（读）与单店执行（写）两大端点，并新增店铺优化/选品/营销三位专家参考。执行记录见 `docs/PLAN-store-analytics-v1.md`（harness-store-analysis）。

- **3 张新表**（`worker/src/storage/database/shared/model.py`，均 append-only / 去重聚合）：
  - `store_metrics_history`：店铺指标快照 `{tenant_id, credential_id, store_id, snapshot_at, order_count, sales_amount, commission_amount, profit_amount, product_count, low_stock_count, active_discount_count, profit_rate, raw(jsonb)}`。**无业务唯一键**（同 store 多条 snapshot_at 靠自增 id）。`store_sync_service._append_metrics_snapshot` 每次同步末尾追加一条（失败静默降级）。**profit_amount/profit_rate 无成本时写 NULL，绝不编造利润**。
  - `store_operation_log`：店铺操作审计 `{tenant_id, credential_id, store_id, operation, target_id, before, after, result, error, operator, created_at}`。**唯一写入口是 `services/store_operation_log.py` 的 `_write_operation_log`**（result 不依赖成功率：pending/failed 同样落一行）。`store_actions_routes` 只业务 + 计算 after，不重复插入逻辑。
  - `selection_insights`：选品洞察 `{keyword, category_path, avg_price_rub, avg_profit_margin, match_1688_count, sold_count, source, contributed_by_token_id, created_at}`，**唯一键 `(keyword, contributed_by_token_id)`**。`selection_insight_service.upsert_from_discovery_run` 从 `discovery_runs.candidates_json` 聚合（`/api/v1/discovery/runs` 上报后非致命回调，见 main.py:2170）。
- **店铺分析端点**：`GET /api/v1/stores/{credential_id}/analysis`（`store_sync_routes` → `services/store_analysis_service.py`）——整店 `summary`（product_count/low_stock_count/active_discount_count/avg_profit_rate）+ `profit_trend`（读 `store_metrics_history` 的 profit_rate/sales_amount 聚合）+ 三组清单（low_margin/out_of_stock/promo_ready）。**无成本商品只给当前价 + 库存，不填 profit_rate**（`get_decrypted` 跨租户校验 404）。利润率经 `estimate_from_envelope`（commission_resolver + 物流唯一入口）provisional band pass 计算。
- **店铺执行端点**：`POST /api/v1/stores/{credential_id}/actions`（`store_actions_routes`）——`operation ∈ {bulk_update_prices, bulk_update_stocks, bulk_archive, actions_register, seller_action_discount}`。改价/库存/归档分发 `shelf_service`，活动报名/自建促销分发 `promo_client`；每个 operation 成功/失败都接 `_write_operation_log`。**只做包装 + 卖货 API 调用，不自动执行**（skill/前端触发）。
- **新 MCP 工具**（`pounding-mcp/pounding_mcp/server.py` → `worker_http.py`，**直接 HTTP 调 worker API，非 skill CLI**）：`mcp__pounding__analyze_store`（读）/ `mcp__pounding__run_store_action`（写，dsh 侧审批）。
- **专家版图**（`pounding-mcp/references/`）：expert-store-optimizer.md / expert-selection-master.md / expert-marketing-master.md / expert-tool-map.md（给 agent「那位专家 → 该用哪些工具」速查，工具名已逐一对 server.py 21 工具 grep 核对，无幻影）。
- **店铺跨租户绑定拦截**（`services/credential_service.py` `_assert_client_not_bound_elsewhere`）：同 `ozon_client_id` 已被**其他 tenant** 绑定 → 409 `该店铺已被其他用户绑定`（防跨租户绑定同一店）。
- **测试**（可用对应 worker 测试文件核对，非本批全量断言）：test_store_analysis / test_store_actions / test_store_history_models / test_store_metrics_sync / test_store_operation_log / test_selection_insights；pounding-mcp test_store_tools（monkeypatch `_request`，断言 analyze/run 两个工具在 server 注册）。

## 最近更新（v0.60 — 三档双价格体系 + 标题 SEO 流量词 + 9048 防并卡前缀 + 对话入口）

> 2026-08-21。已发版（VERSION 四源 0.60.0）。定价/标题/9048/对话入口 + 48 存量测试修复。执行记录见 `docs/PLAN-conversation-entry-v1.md`（Q3）+ `docs/PLAN-card-merge-fix-v1.md`（Q4）。

- **三档双价格体系（Q1）**：`compute_price`（`utils/pricing_estimate.py`）新增 keyword-only `margin_anchor`(划线原价 2.0)/`margin_floor`(促销底线 0.6)/`variable_cost_rate`(日常变动成本 0.155)/`promo_variable_cost_rate`(促销 0.245)。售价 = 总成本×(1+margin) / (1-commission-variable_cost_rate)；**利润口径改销售净利率** `profit/price`（不再用成本利润率）。三档：price=日常价 / old_price=划线价（强制 ≥ 日常×1.2）/ promo_price=促销底线（供 Ozon `min_seller_price` 防自动调价跌破成本）。全缺省 → 旧单档行为逐字保持。
- **定价公式唯一入口**：`pricing_node`/estimate 端点/前端计算器三处共用 `compute_price`（v0.40 共享层纪律延伸）——**禁止各处内联定价公式**。变体循环从内联 ×1.15/×1.2 改调 compute_price（消除漂移）。
- **标题公式共享模块 `utils/title_formula.py`**（Q5）：`build_title_formula_prompt(lang, traffic_keywords)`「核心词+属性+场景」公式 + `parse_title_formula_keywords`（纯西里尔过滤/去重/≤3）。**统一 4 份公式拷贝**（prepare 主路径/兜底/内部 fallback + ai_field_service 草稿重生成）——新增标题公式逻辑必须进本模块。
- **SEO 流量词链路**：`extensions.traffic_keywords`（what-to-sell all-queries 流量词）→ 标题生成 prompt 建议行（LLM 自主融入不硬塞）+ hashtag 23171 优先（`_generate_hashtags(name, traffic_keywords)`，流量词 > `_HASHTAG_RU` 字典 > 西里尔提取 > `#товар`）。数据源端点 `GET /api/v1/seo/keywords?q=&limit=`（Bearer + 限流，读 `blue_ocean_queries` 按 uniq_queries_wca DESC）。
- **9048 防并卡前缀（Q4 P1-1）**：`_derive_model_name_9048`（prepare L1228）→ `f"{item_id}~{sha1(normalize(supplier)|normalize(中文标题))[:8]}"`——自家同 item+supplier+标题 → 同 hash → 变体仍合并；跨卖家同 item 不同 supplier/标题 → 不同 hash → **不并卡**。**跟卖（is_follow_sell UPDATE 模式）刻意不加前缀**（本就要并卡）。hash 只用确定性字段（item_id/supplier/中文标题），**绝不用 LLM 翻译后标题**（retry 重生成会拆卡）。
- **对话入口（Q3）**：`pounding-mcp/pounding_mcp/router.py` 意图路由层（把 SKILL.md 决策树固化为 URL 正则 + 九类意图词表，输出 pipeline A/B/C/D1/E/F/unknown + needs_confirmation/needs_clarification/questions）；`tasks_server` 加 `POST /ask` + CORS 头 + OPTIONS 预检；专家 tab 卡片点击复制 CLI 命令 + 「去 Agent 对话」按钮（openTab type:'agent'）。
- **COS env 透传**：`deploy/docker-compose.yml` 新增 `COS_SECRET_ID/KEY/BUCKET/REGION/PUBLIC_DOMAIN` 透传（b64 生图转存 + E1 原始图兜底）——`.env.example` 仍未收录，生产配不配都可（缺省静默降级）。
- **测试**：worker **1306 passed**（此前 1252 + 54；含 48 个存量失败修复——测试侧适配 v0.56「MXOU key 即用户」派生租户 `_key_user_id` 动态派生，mock 层 6 文件 + PG 层 6 文件 + main.py `::text` cast 真 bug）；pounding-mcp **22 passed**（自身 .venv，见下方测试坑）。

## 最近更新（v0.59 — 类目佣金缓存 + 定价佣金修正 + 多 SKU 配额调研）

> 2026-08-20。佣金链路修复（费率权威化）+ 选品发货模式对齐，均未发版（VERSION 仍 0.56.6）。执行记录见 `.omo/plans/category-commission-cache.md`（Momus 评审 OKAY），问题台账 `docs/TEST-ISSUES-2026-08.md`。

- **Ozon 佣金是「类目 × 发货模式 × 价格段」三维矩阵，无公开按类目查佣金的 API**（`/v5/product/info/prices` 需真实 offer_id、销售报告需已售记录、类目树无佣金字段）。唯一选品时可用的是 what_to_sell 的 `rfbs_rate`/`fbp_rate` 分段对象（`{leq_1500, leq_5000, gt_5000}`）。详见 `docs/OZON-MULTI-SKU-QUOTA.md` 同批调研。
- **佣金缓存表 `category_commission`**（`worker/src/storage/database/shared/model.py`）：`description_category_id` 唯一，FBS/FBO 三段佣金%，全局共享无 tenant_id（对齐 category_mapping W11）。两条数据源渐进积累：上架成功回填（prices_api 源）+ what_to_sell 分段。
- **`commission_resolver.py`**（`worker/src/utils/`）：`pick_price_band`/`parse_prices_commissions`/`resolve_commission_rate`（explicit > 缓存表 > extensions segments > 0.10）/`get_category_commission`/`upsert_category_commission`——佣金解析唯一入口，**定价/选品/estimate 三处共用**。
- **pricing_node 佣金三重 bug 修复（P0）**：原读 `price_resp.get("result",{}).get("commissions",{})` 拿 `sales_percent_rfbs`——① `/v5/product/info/prices` 顶层无 result（是 `items[0].commissions`）② 空 filter `{"offer_id":[]}` 查不到数据 ③ 字段名 `sales_percent_rfbs` 本身是对的（2026 新增）。修复用 **provisional-price band pass**（先 0.10 算临时价 → 选档 → resolve 真实佣金 → 重算）破解「档位依赖价格/价格依赖佣金」鸡生蛋。删除空 filter 调用（该 API 移到 learning_record 回填）。
- **上架回填**：`learning_record_node` approved 路径新增非致命 `_backfill_category_commission`（真实 product_id 查 prices → upsert source=prices_api）。
- **skill 佣金分段**：`_to_rate` → `_to_rate_segments` 保留完整三段（不再只取中段）；`ProductCandidate` 加 `commission_rfbs_segments`/`commission_fbp_segments`；信封 `extensions.commission_segments = {fbs:{...}, fbo:{...}}`（rfbs→fbs/fbp→fbo）透传 worker。
- **佣金查询端点**：`GET /api/v1/commissions/lookup?category_id=`（+ legacy 路径），Bearer 鉴权 + 限流，返回 `{found, fbs, fbo, source}`。skill `_query_commission_from_worker` 接入 `_calculate_profit`（worker 真实分段 > 本地分段 > 12/14/18 默认）。
- **发货模式对齐**：`sales_schema` 标注（FBO/FBS/rFBS 全抓不剔除，仅标注导出/webui 筛选）+ `_match_sales_schema` 子串过滤（`"FBS" in schema` 隐式覆盖 RFBS，对齐竞品 `.includes()`）+ `sales_mode` 配置（stores.json 默认 rFBS，后置过滤默认不过滤只标注）。
- **测试**：worker **1252 passed**（此前 1212；含 6 个 pre-existing 修复：4 个类目树导入 + 2 个测试预期对齐现行实现）；skill **597 passed**（此前 566）；webui `tsc -b` 0 错误 + `npm run build` 通过。

## 最近更新（v0.58 — 重量估算同源统一 + batch_test 复用 discover 货源 + pounding-sidebar 插件骨架）

> 2026-08-19。重量链路修复（费率表权威）+ 客户端插件骨架，均未发版（VERSION 仍 0.56.6，代码注释已标 v0.58）。

- **重量估算同源统一**：`ozon_discovery.py` 新增 `DEFAULT_WEIGHT_G=500` + `estimate_shipping_cny()`（分段 ≤500g ¥6 / ≤1000g ¥8 / >1000g ¥15）共享函数，cloud_probe `price_estimate` 改调它——修掉 discover 与 graph/follow 上架管线默认重量不一致（曾差 ¥9/单，轻小件被选品分析误判「利润不足」）。
- **无重量也查费率表**：`_query_logistics_from_worker` 重量 None/≤0 时按 `DEFAULT_WEIGHT_G(500g)` 查表（此前直接 return None 跳过费率表落本地估算）——**费率表是权威**，无重量只是少一个查询维度，不是放弃查表的理由。last-good 缓存键/查询同步 eff_weight。
- **batch_test 复用 discover 货源直上**：`_find_discover_source()` 优先复用 discover 已匹配好的 1688 货源（免重跑 CDP + 图搜），未命中才走 follow；pre-flight `_need_cdp` 预扫描，纯复用批次不启 Chrome。
- **pounding-sidebar 插件骨架**：新子项目（dsh-better-sidebar 消费插件），注册 7 个业务板块 tab（采集箱/任务中心/专家/知识库/爆品新闻/计算器/用量）+ CSV 预览器；`ctx.betterSidebar.registerTab` **必须包 effect**（HMR/禁用自动撤销，否则报 already registered）；构建参照社区标准插件 dsh-sentinel 模板。
- **测试**：skill **566 passed**（基线 556 + 10 新增 `test_batch_test_reuse_discover.py`）；worker 1212 不变（本轮未改 worker）。

## 最近更新（v0.57 — webui 视觉 v2.0 全站落地 + 能力补齐 + 多用户聚合 + 静默采集）

> 2026-08-18。按 `integration-workplan/` 六件套（PRD/PLAN/TASKS/ISSUES/TODO/TEST v2.0，经评审 GATE APPROVE）执行。前端 6 文件 + worker 5 文件 + skill 6 文件 + 测试 8 文件。

- **视觉全站落地（W1-W3）**：`theme.css` `:root`(light)/`.dark` 映射设计 token（bg `#F7F6F2` / primary `#E20E0E` / sidebar `#111` / border `#E6E4DF` / radius 10px）+ `--font-mono` 加入 `@theme inline`；`scripts/verify-design-tokens.mjs` 校验脚本（断言 theme.css 与 design-tokens.json 一致，防漂移）；`src/tokens/tokens.json` 标注 legacy；KPI 卡组件（data-lg 等宽数字）、表格 mono、空态组件对齐 spec；登录页迁移出 `src/index.css`（`styles/login.css`）；`main.tsx:46` `./index.css` **保留**（3519 行业务样式未迁移，不得整体删除）。
- **API 接线（W4）**：`getTaskStatistics()`（GET `/api/v1/task_statistics`）新增 + KPI 卡接真实数字（今日订单=各店 today_orders 聚合 / AI 上品数=completed / 上架成功率）；SystemSettings 业务 Tab 接 logistics 三函数（表格/编辑/CSV 导入）；订单商品图 `OrderProductOut.image`（worker 按 **product_id** 批量 `/v3/product/info/list`，复用 `_fetch_info_map_by_ids`）；**订单接口 v3→v4**（`/v4/posting/fbs/list` cursor/has_next，order_service.py:293 + store_sync_service.py:58-72）；在售列表 `/products/ozon` 图/价；Stores 页今日统计列（`GET /stores/{id}/stats`，store_sync 聚合，**无评分字段**）。
- **多用户聚合（W4b）**：热销榜（`list_bestsellers` 去 `contributed_by_token_id` 过滤）+ 发现归档（`GET /api/v1/discovery/runs` 去 tenant 过滤）**全局共享**（保留贡献者列）；订单/商品/草稿/凭证/任务隔离不动；蓝海（admin-only）/榜单（无读端点）**不开放**（TODO #12）。
- **静默采集（W5）**：aibuy 毒 token 修复 4 处（`_aibuy_token_valid` value 级校验 + 读取失效 + 保存守卫 + token 舞步轮询 8s）；降级出声（cloud_probe.py:3353/ozon_discovery.py:2190 debug→warning）；热销榜 `queries --type ozon-bestsellers/all-queries` **cookie 直调**（`_fetch_seller_session_cookies` + `fetch_*_direct`，免 Chrome 导航，CDP 兜底）；compile.py stub 特征校验（`search_by_image_aibuy` 存在性 warning）；`check` 命令 1688 反爬 cookie 就绪检测。
- **类型迁移（W6）**：client.ts **42 接口**迁移 `generated.d.ts`（27 纯别名 + 14 Omit 覆盖 + 2 内联），30 保留手写+注释；openapi 快照重新生成（97→98 paths，新增 `/stores/{id}/stats`）；占位页按 spec 空态规范（PricingTool 竞品对比 → /bestsellers、ImageStudio AI 背景编辑 → /products）。
- **I-9 结论**（实测）：`ir.ozone.ru` 竞品图 **可直接 aibuy 图搜**（1688 服务器阿里出口可达）；本机 curl 403/fake-IP 是本地代理特性，与 aibuy 无关——**无需 COS 转存**（relay 过度设计已撤销）。
- **测试**：worker **1212 passed**（基线 1209 + 新增 store stats/全局共享/v4 迁移）；skill **556 passed**（基线 537 + 19 新增：毒 token/轮询/直调/特征校验）；webui `tsc -b` 0 错误 + `npm run build` 通过。

## 历史更新（v0.40.1 / v0.40 — 完整记录见 CHANGELOG.md）

> 2026-08-12~13。属性填满工程 + 图搜增强。**COS 图片消失根因**见下方「⚠️ COS 图片存储真相」。

- **v0.40.1**：成人用品生图不注入标题（`prompt_assembler.is_adult_product`）+ 跟卖参考图泄漏修复（`_assemble_follow_sell` images=`[]` + `cos_uploader._is_reference_image` 拦竞品 CDN）+ 8229 类型值空修复（`fetch_ru_dict_value`）+ 部分尺寸缺失兜底（2:1.5:1 补齐）。
- **v0.40**：属性共享匹配层 `attr_value_matcher.py`（三处统一）+ LLM 消歧安全三件套（-1 出口/候选索引/abstain）+ 缺口量化（`attr_gap.should_fill`）+ aibuy 图搜主通道（mtop 直调免浏览器）+ 类目 LLM 消歧 + 定价复用 worker 公式 + 词典扩充 333 词对。详见 CHANGELOG + 下方「v0.40/v0.39 关键约定」。


## 工作区概述

两段式 Ozon 上架系统（Skill/Worker 两段负责上架管线）+ 客户端侧边栏插件（pounding-sidebar，dsh-better-sidebar 消费插件），职责严格分离：

| | Skill | Worker |
|---|---|---|
| **角色** | Agent 调用的工具（ZCode/Claude Code 等） | 云端 Docker 管线，消费信封完成上架 |
| **位置** | 客户本地 | 云端服务器（Docker） |
| **入口** | `skill/SKILL.md`（Agent 操作手册） | `worker/src/main.py`（FastAPI + CLI） |
| **职责** | 1688/Ozon CDP 抓取 + 以图搜款 → 组装 GraphInput 信封，**不上架** | 接收信封 → 类目→定价→属性→生图→校验→上传→自学习 |
| **接口** | 输出 `GraphInput` JSON（三层结构 `{draft, source, extensions}`） | 输入 `GraphInput`，输出 `GraphOutput` |

接口契约详见 `docs/CONTRACT-v4.md`（v4.0，最新）。Agent 调用指南详见 `skill/SKILL.md`。部署指南详见 `docs/DEPLOY.md`。

## 目录结构

```
ozon-worker/
├── skill/                      # 客户本地:1688/Ozon 抓取 + 以图搜款 + 信封组装 (Python ≥3.12, pip)
│   ├── compile.py              # Cython 编译脚本（核心库 → .so/.pyd，源码保护）
│   └── scripts/
│       ├── cli.py              # CLI 入口:check/graph/follow/image_search/get_ak/batch_test
│       ├── cloud_probe.py      # build_graph_envelope + follow_sell_cloud + submit_envelope
│       ├── batch_test.py       # 批量处理 URL 列表
│       ├── lib/
│       │   ├── ak_1688_client.py      # 1688 AK API 搜索
│       │   ├── chrome_launcher.py     # 跨平台 Chrome CDP 自动启动（用户零配置）
│       │   ├── ozon_scraper.py        # Ozon 商品页 CDP 抓取（完整字段）
│       │   ├── ozon_image_search.py   # 1688 以图搜款（aibuy mtop API 直调 + CDP 网页版双通道）
│       │   ├── config_store.py        # 凭证管理
│       │   ├── cdp_client.py          # 原生 CDP WebSocket 客户端（替代 Playwright）
│       │   ├── ozon_widget.py         # Ozon Widget API 客户端（产品信息/跟卖/SKU）
│       │   ├── ozon_seller.py         # Ozon Seller API 客户端（佣金/重量/品牌）
│       │   ├── ozon_discovery.py      # Ozon 选品发现引擎（蓝海评分/1688匹配）
│       │   ├── cache.py              # 通用磁盘缓存（命名空间 + TTL + SHA256 key）
│       │   └── utils.py              # 共享工具函数（parse_price 等）
│       └── capabilities/browser_probe/   # Chrome CDP 探针 + 反检测
├── worker/                     # 云端 Docker:LangGraph 上架工作流 (Python ≥3.12)
│   ├── src/
│   │   ├── main.py             # FastAPI + CLI 入口(-m http/flow/node)
│   │   ├── api/                # 错误码 + Pydantic schemas（自动生成 OpenAPI）
│   │   ├── graphs/
│   │   │   ├── graph.py        # main_graph 编排(auth→...→learning_record)
│   │   │   ├── state.py        # GlobalState / GraphInput / GraphOutput
│   │   │   ├── nodes/          # ~28 个节点
│   │   │   └── validation_retry_loop.py   # 校验失败重试子图
│   │   ├── storage/            # database(PG) / memory(checkpoint)
│   │   └── utils/              # task_processor / logger / ozon_client / ozon_category_query / mxou_api / ...
│   ├── assets/                 # 类目树 JSON、物流费率 Excel、Ozon API 文档
│   ├── config/                 # LLM prompt 配置 (category_match / attributes / ...)
│   ├── tests/                  # pytest 测试
│   └── scripts/                # init_data.py / import_logistics.py / ci.sh
├── deploy/                     # 部署包
│   ├── docker-compose.yml      # 生产环境（含 PG + Worker）
│   ├── docker-compose.dsh.yml  # pounding dsh 电商客户端本地测试沙箱（127.0.0.1:3080）
│   ├── dsh/                    # dsh 沙箱 Dockerfile + setup-profile.py
│   ├── deploy.sh               # 一键部署（含自动初始化数据）
│   ├── update.sh               # 一键更新
│   └── .env.example            # 环境变量模板
├── pounding-mcp/               # dsh Agent 调用入口：把 skill CLI 包成 19 个 MCP 工具（FastMCP 薄封装）
│   ├── pounding_mcp/router.py  # Q3 对话入口意图路由层（URL 正则 + 九类意图词表 → pipeline A-F）
│   ├── pounding_mcp/server.py  # FastMCP 工厂 + 19 工具（参数映射 → subprocess 调 skill CLI）
│   └── README.md               # 挂载/独立 venv 说明（测试坑见下方）
├── pounding-sidebar/           # 客户端侧边栏插件（dsh-better-sidebar 消费插件）
│   ├── src/client/index.tsx    # 7 业务板块 tab（采集箱/任务中心/专家/知识库/爆品新闻/计算器/用量）+ CSV viewer
│   ├── cordis.patch.yml        # 手动挂载补丁（~/.dsh/profiles/web/cordis.patch.yml）
│   └── README.md               # ⭐ 插件开发指南（registerTab 契约/构建/挂载，见「深入阅读」）
├── webui/                      # React 前端（bun 生态，非 npm）——与 worker 同一 docker-compose 部署
│   ├── src/api/client.ts       # API client（42 接口迁移 generated.d.ts + 30 手写）
│   ├── src/app.tsx             # 路由 + 布局（routeTree.gen.ts 由 TanStack Router 生成）
│   ├── src/styles/theme.css    # 视觉 token（:root light / .dark），verify-design-tokens.mjs 防漂移
│   └── scripts/                # build（bun run build）→ 产物 bind mount 进 worker 容器同进程伺服
├── docs/
│   ├── CONTRACT-v4.md          # Skill↔Worker API 契约 v4.0（最新；v3.0 旧版已归档 archive/docs/legacy/）
│   ├── DEPLOY.md               # Worker 云端部署完整指南
│   ├── LOGGING.md              # 日志系统架构 + 查看命令 + 故障排查
│   ├── WORKER-TOPOLOGY.md      # ⭐ Worker 拓扑 + 错误映射 + 数据流 + 改代码快速参考
│   └── ...                     # PRD、Ozon API 文档、物流费率 Excel
├── archive/                    # 归档区（过时产物统一存放，见 archive/README.md）
│   ├── docs/legacy/            # 过时文档（旧版 PRD/CONTRACT，git mv 保留历史）
│   ├── screenshots/            # 开发/竞品截图（本地保留，未入库）
│   └── packages/               # 历史压缩包（本地保留，未入库）
└── scripts/
    └── ci.sh                   # 本地 CI（lint → test → build）
```

## Skill 能力

| 能力 | 命令 | 说明 |
|------|------|------|
| 环境检查 | `check` | 自动启动 Chrome、检测登录态、验证凭证、Sentry 状态诊断 |
| 1688 选品 | `graph` | CDP 抓取 1688 → 组装信封 → 提交 Worker（提交前打印预估售价） |
| Ozon 跟卖 | `follow` | Ozon 竞品图搜 1688 同款 → 组装信封 → 提交 Worker |
| Ozon 选品 | `discover` | Ozon 中国站/搜索/类目页自动选品，蓝海评分，1688匹配，利润计算，CSV/JSON导出 |
| 以图搜款 | `image_search` | 1688 图搜（`--source aibuy` 默认免浏览器 / `cdp` 网页版 / `ak` API） |
| 1688 关键词选品 | `search` | 1688 AK 关键词搜索 + 利润估算 + 排序 + CSV 导出 + `--rules` 筛选 + `--auto-submit` 批量上架 |
| 类目查询 | `category` | Ozon 类目树查询（`--lang ZH_HANS\|EN\|RU`，替代临时脚本，Issue4） |
| 获取 AK | `get_ak` | 浏览器自动获取 1688 AK |
| 批量处理 | `batch_test` | 批量处理 URL 列表（v0.36 支持 `--resume` 断点续传——读上次 log_file 跳过已成功项，失败项重试） |
| what-to-sell 查询 | `queries` | Ozon 蓝海/榜单数据查询（v0.34，all-queries/ozon-bestsellers/market-bestsellers，采集后自动上报 worker PG） |

**Chrome 自动启动**：用户零配置，Skill 自动检测系统、启动 Chrome、保留登录态。

**源码保护**：`compile.py` 用 Cython 编译核心库为二进制 `.so`/`.pyd`。当前编译 **14 个**（`COMPILE_FILES`，均在 `scripts/lib/`）：ak_1688_client、ak_callback、config_store、image_preprocessor、ozon_scraper、ozon_image_search、reference_images、ozon_api、ozon_seller_analytics、analytics_upload、ozon_fission、ozon_discovery、ozon_seller、cdp_client。

明文复制分两批（`compile.py` 的 `COPY_FILES` 7 个 + `AUX_FILES` 中的明文模块 8 个，依赖复杂/改动频繁/跨平台编译失败）：
- **COPY_FILES（入口/核心明文）**：cli.py、batch_test.py、runtime_probe.py、cloud_probe.py、bootstrap_update.py、lib/chrome_launcher.py、capabilities/browser_probe/stealth.py
- **AUX_FILES 明文模块**：lib/（utils、cache、ozon_widget、updater、task_paths、logging_utils、review_log）、capabilities/browser_probe/service.py
- **编译/明文判断**：改模块归属必须同步改 compile.py 三清单（COMPILE_FILES/COPY_FILES/AUX_FILES）+ 跑 `test_compile_lists.py`（锁定 14 模块不变式 + 三清单互斥）——模块两属会被 AUX 复制覆盖回明文。
- **cloud_probe.py 明文**（2026-08-02 移回）：非语法问题（macOS 同 Cython 编译成功），是 Cython 生成 65k 行 C + 单个 ~9000 行函数击穿 **MSVC 编译器堆限制**（仅 win32 失败 → 缺 .pyd → graph/follow 报 `No native binary for cloud_probe on win32`）。信封组装核心、改动频繁，明文跨平台一致。
- **service.py 明文**（2026-08-01 移回）：探针改动最频繁。
- **stealth.py 明文**（2026-08-07 移回）：反检测是对抗性代码（真实指纹无需伪造），1688/Ozon 升级检测需快速调。已在 COPY_FILES（非编译清单）。
- **ozon_discovery.py 已编译**（v0.37 P6）：从 COPY_FILES 晋升编译（同批还有 ozon_seller_analytics/analytics_upload/ozon_fission/ozon_seller/cdp_client），8 → 14。⚠️ 用户 Python 3.14 环境跑 discover 需用 py312 ABI 兼容解释器（Docker 3.12 或符号链接修复后的 python3.12），编译态 .so 无法在 3.14 加载。
- **compile.py 编译失败"带响"**（v0.12.0）：失败打印完整 stderr（最后 30 行）+ `failed>0` 时 `sys.exit(1)`，CI 不再静默发布残缺包。CI 另有产物完整性校验（**4 平台 × 14 模块 = 56 个二进制必须就位**，build-skill.yml）。
- 编译必须用 **Python 3.12**（与目标运行环境 ABI 一致）。⚠️ 曾因 Homebrew 从 /opt/homebrew 迁移到 /Volumes/os 导致 python3.12 前缀解析失败——已用符号链接 `/opt/homebrew -> /Volumes/os/opt/homebrew` 修复（2026-08-11），无 PYTHONHOME 可直接跑。Cython 用 `--user --break-system-packages` 装。

**依赖**：仅 4 个 — `requests`、`websocket-client`、`Pillow`、`sentry-sdk`（Sentry 错误上报，v0.35 起；缺失时 cli.py lazy import 静默降级，不阻塞任何命令）。

**三条管线**：
- **1688 选品**：1688 URL → CDP 抓取 → 组装信封 → Worker 全流程
- **Ozon 跟卖**：Ozon URL → CDP 抓取 → 图搜 1688 → 组装信封 → Worker 跟卖管线
- **Ozon 选品**：Ozon 页面 → CDP 抓取产品列表 → 蓝海评分 → 1688 匹配+利润计算 → 用户确认 → Worker 提交

## Skill → Worker 契约（最重要）

交接载荷是 `GraphInput`，定义在 `worker/src/graphs/state.py`:

```
GraphInput = { token, ozon_client_id, ozon_api_key, envelope }
```

`envelope` 采用**三层结构** `{draft, source, extensions}`:

- **`draft`** — 产品数据:
  - 必填: `item_id`、`title`、`images[]`(str URL 数组)、`weight`(克, int)、`dimensions{length,width,height}`(mm, int)
  - 定价相关: `purchase_cost`(CNY, float)、`purchase_url`、`currency`("CNY")
  - 可选: `attributes{}`(dict[中文属性名→值])、`supplier`、`stock`、`ozon_category{description_category_id,type_id}`
  - 单SKU（默认）: 顶层 `sku_id`、`price`、`original_price`(均平铺在 draft 下)
  - 多SKU 信封: `variants` 最多 1 个元素（Skill 层已折叠）

- **`source`** — 采购源信息: `{purchase_url, purchase_cost}`

- **`extensions`** — 定价配置: `{margin_rate, commission_rate, fx_buffer}`(可选,默认 0.25/0.10/0.05)
- **`extensions.follow_sell`** — 跟卖标记: Worker 走跟卖管线

> ⚠️ **关键约定:**
> - **单产品上传**: Skill 层自动将多变体折叠为单产品（`_collapse_variants_to_single`），一个 1688 item = 一个 Ozon 产品卡。
> - **`purchase_cost` = 代表变体价格 + 1688 国内运费(freightCny)**，已在 Skill 层完成。
> - **`dimensions` 单位 mm**: 1688 原数据 cm → skill ×10。worker 再 /10 转回 cm 定价。
> - **`weight` 单位克**: 直传。

## Worker API 端点

所有端点同时暴露在旧路径和 `/api/v1/` 前缀下（向后兼容）：

| 功能 | v1 路径 | 方法 |
|------|---------|------|
| 提交任务 | `POST /api/v1/submit_task` | POST |
| 鉴权验证 | `POST /api/v1/auth/verify` | POST |
| 查询状态 | `GET /api/v1/task_status/{id}` | GET |
| 取消任务 | `POST /api/v1/cancel_task/{id}` | POST |
| 任务统计 | `GET /api/v1/task_statistics` | GET |
| LangGraph 进度 | `GET /progress/{run_id}` | GET |
| 健康检查 | `GET /api/v1/health` | GET |
| Swagger UI | `GET /docs` | GET |
| 蓝海数据上报 | `POST /api/v1/analytics/queries` | POST |
| 畅销榜数据上报 | `POST /api/v1/analytics/ozon-bestsellers` | POST |
| 跨平台畅销榜上报 | `POST /api/v1/analytics/market-bestsellers` | POST |
| 选品结果归档（v0.56） | `POST/GET /api/v1/discovery/runs` | POST/GET |
| 类目映射查询（v0.56） | `GET /api/v1/mappings/lookup?keyword=` | GET |
| 店铺手动同步（v0.56） | `POST /api/v1/stores/{id}/sync` + `GET /stores/{id}/sync-status` | POST/GET |
| 上架配置模板（v0.56） | `GET/POST/PATCH/DELETE /api/v1/templates` + `POST /templates/{id}/default` | 全 |
| 店铺凭证管理（v0.41+） | `GET/POST /api/v1/credentials` + `PATCH/DELETE /credentials/{id}` + `POST /credentials/{id}/validate` | 全 |
| 采集箱草稿（v0.41+） | `GET/POST /api/v1/drafts` + `GET/PATCH/DELETE /drafts/{id}` + `POST /drafts/{id}/submit`（+ `/resubmit`、`/batch-submit`、`/drafts/{id}/ai/{field}`） | 全 |

**`task_status` 返回 `progress` 字段**：`{stage, percent, stages_completed[], stages_remaining[], message}`。
进度基于内存中 12 阶段 `STAGE_ORDER` 计算，节点执行时 `ProgressCallback` 自动更新。
⚠️ 进度存储在内存中，Worker 重启后丢失（task_status 降级为无进度模式）。

鉴权: `token` 字段在请求体中（非 header），通过 Supabase `tokens` 表校验。
限流: 每 token 每分钟 ≤ 300 次（`RATE_LIMIT_PER_MINUTE` 可配置）。
并发: 最多 50 个任务同时执行（`MAX_CONCURRENT` 可配置）。

**`auth/verify` 端点**：Skill 调用的轻量鉴权接口。验证 token 有效性 → MXOU 余额 → 账户状态 → 可选 Ozon API。返回 `{"valid": bool, "reason": "ok|token_invalid|balance_insufficient|account_inactive|service_unavailable", "expires_in": 86400, "ozon_valid": bool|null}`。DB 不可用时安全降级返回 `valid: false`（不会误放行）。
- **余额判定（v0.12.0 修正，2026-08-02 充值实证）**：`_check_mxou_balance()`（main.py）——`tokens.unlimited_quota=true` 直接放行；否则查 `users.quota`（实时剩余额度）> 0 放行。**绝不用 `tokens.remain_quota`**：它是僵尸字段（git 历史移除扣减后从未同步，实证：同用户 3 个 key 数值各异可为负数、充值后仍 0/-10），旧逻辑用它+5.0 阈值导致无限额度/有余额 key 全被误判。`users.quota` 充值直接加、每次调用扣；`used_quota` 是历史累计，判定不参与。auth_verify/submit_task/auth_node 三处一致。

## 架构边界

- **Worker 三层**: FastAPI `/submit_task`(鉴权+入队) → PG 队列 `ozon_product_tasks`(`FOR UPDATE SKIP LOCKED`) → 50 并发 LangGraph worker(`SupabaseTaskProcessor`)
- **Skill 是无状态本地抓取**，不调用任何 Ozon 上架 API
- **编辑时不越界**: 别给 skill 加上架调用，别给 worker 加 1688 抓取
- **错误码**: 统一在 `worker/src/api/errors.py`（12 个 `WorkerErrorCode`）

## 组件关系与更新联动（改任何一块前必读）

> 本节厘清 skill/worker/pounding-mcp/pounding-harness/webui/ozon-mcp 六者的职责边界、暴露范围、更新联动规则。pounding-harness 是独立仓库（`/Volumes/os/dev/pounding-harness`，Boujoy Harness 改造版），不并入本仓库——但发版/更新前必须做适配同步检查。

### 六者职责与暴露范围

| 组件 | 位置 | 给谁用 | 暴露范围 |
|---|---|---|---|
| **skill** | 本仓库 `skill/` | agent 对话（经 pounding-mcp）+ 客户端面板 | 1688/Ozon CDP 抓取、以图搜款、信封组装。**不上架** |
| **worker** | 本仓库 `worker/`（云端 Docker） | webui + 客户端面板 + pounder-mcp | 类目→定价→属性→生图→上传→自学习全流程 + REST API |
| **pounding-mcp** | 本仓库 `pounding-mcp/` | 用户（agent 对话） | skill 19 命令包成 MCP 工具（`mcp__pounding__*`）+ 意图路由 `/ask`。**用户可见** |
| **pounding-harness** | 独立仓库 | 终端用户（桌面客户端） | Electron 客户端 + 本地网关 `:8766`（代理 skill-config / tasks→:8902 / worker REST）。界面含**部分** webui 功能 |
| **webui** | 本仓库 `webui/`（云端） | 终端用户（浏览器直访 worker :8080） | 完整 ERP 后台。与 worker 同 docker-compose 部署 |
| **ozon-mcp**（PCDCK/ozon-mcp） | 外部参考，不直接入库 | **仅我们内部开发** | 466 Ozon API 方法索引 + swagger + transport 层。**不暴露给用户** |

### 关键边界

- **pounding-harness ≠ webui**：客户端只含部分 webui 功能（采集箱/任务/Dashboard 等），请求本地 skill 脚本 + worker API + 数据库；webui 是完整 ERP，请求 worker REST。
- **ozon-mcp 是开发武器不是用户功能**：它的 466 方法 swagger + 分页/限流/安全知识库用来：①补 worker 端点时查 API 契约 ②抽取 SellerClient transport 进 worker 替换 `ozon_client.py`。用户永远不直接调 ozon-mcp。
- **不破坏 dsh**：pounding-mcp 是标准 MCP 桥接（`@deepseek-ai/dsh-mcp-client`），pounding-sidebar 是标准 cordis 插件（`dsh.client.inject`），pounding-harness 的 `boujoy_server.py` 是 Python stdlib-only——三层都不引入 dsh 之外的依赖耦合。

### 更新联动规则（改 X 必须同步检查 Y）

| 改了什么 | 必须同步检查 | 不用动 |
|---|---|---|
| skill CLI **命令签名/参数** | pounding-mcp `server.py` 参数映射 + `router.py` 意图词表 | worker / webui |
| skill **信封字段结构**（GraphInput 增减字段） | worker `state.py` + AGENTS.md 契约节 + webui 草稿展示 | pounding-mcp（只传不解析） |
| worker **新增 API 端点** | AGENTS.md API 表 + webui 接入 + 客户端按需接入 | skill / pounding-mcp |
| worker **定价/标题公式** | `compute_price`/`title_formula` 唯一入口已锁定，面板展示三档价 | skill |
| skill **内部抓取/图搜逻辑** | **什么都不用改**（工具签名/产出结构不变） | 全部 |
| **版本发版**（VERSION 四源） | CHANGELOG + AGENTS.md 顶部更新块 + 测试基线 | — |
| **契约变更**（CONTRACT-v4） | skill/worker/pounding-mcp 三处同步 + 客户端面板 | — |

### 发版前适配同步检查清单

每次发版前必须确认：
1. skill 改了 → pounding-mcp 工具签名是否对齐？`router.py` 意图词表是否要加？
2. worker API 改了 → webui/客户端面板是否要接？AGENTS.md API 表是否更新？
3. 信封结构改了 → CONTRACT-v4 文档是否更新？
4. pounding-harness 那边的 `boujoy_server.py` 代理路由是否要加新路径？（pounding-harness 独立仓库，靠代理路由连 worker）
5. 版本四源是否一致（VERSION / skill/VERSION / deploy/skill/VERSION / SKILL.md frontmatter）？

### 待执行：webui 替换 + ozon-mcp 引入（dev 阶段，零用户访问）

- **ozonwebui 替换 webui**：`/Volumes/os/dev/ozonwebui`（Figma 导出 React 19 + Vite 8）替换现有 `webui/`（bun 生态）。迁移第一步切包管理器：删 `pnpm-lock.yaml` + `.mise.toml`，`bun install` 生成 `bun.lock`。CI `cd.yml` 已是 bun（v0.56.1），不用改。ozonwebui 的 `docs/` 两份对接文档（API-INTEGRATION-STATUS + BACKEND-API-REQUIREMENTS）是 worker 补 API 的依据。
- **ozon-mcp 内部引入**（不暴露给用户）：`PCDCK/ozon-mcp`（MIT）索引 466 Ozon API 方法（420 Seller + 46 Performance）+ seller/performance swagger + 分页/限流/安全知识库。定位 = **开发武器**：①补 worker 端点查 API 契约 ②抽取 SellerClient transport 进 worker 替换 `ozon_client.py`。用户永远不直接调 ozon-mcp。
- **数据落盘策略**：worker 发生的 Ozon API 请求，数据按 user_id + credential_id 落盘 PG 缓存（已有 `ozon_orders_cache`/`ozon_products_cache`，15min 同步）。列表读走缓存（快），实时写（发货/取消/改价）直接调 Ozon 成功后同步更新缓存。

## ⚠️ Agent 使用 Skill 时的硬约束

**当用户请求涉及 Skill 子项目（1688 抓取、Ozon 跟卖、选品、上架）时，必须遵守：**

1. **先读 `skill/SKILL.md`**，不要凭记忆或自己探索项目结构操作
2. **只用 SKILL.md 中的命令**，不要自己写 Python 代码、不要用 requests/urllib 抓取
3. **严格按意图路由选择管线**（A/B/C/D），不要混用蓝海逻辑和跟卖逻辑
4. **不要修改 Skill 的 Python 代码**，除非用户明确要求改代码
5. **趋势/蓝海选品必须先 web_search**：命令层无 `trend`（v0.31 移除）。流程 = agent 先用 web_search 搜 `"{品类} Ozon 热门趋势 蓝海 细分品类 2025"`（可加俄语/平台角度）+ 自带 LLM 提炼细分关键词 → 再 `discover --keyword <关键词>` 执行；**禁止跳过搜索直接猜关键词**（选品质量明显下降）
6. **每次操作前重新判断用户意图**，不要因为上下文中提过某个概念就默认使用它

违反以上约束会导致：空白 Chrome 窗口泛滥、登录态丢失、管线混乱、数据错误。

## 测试

> ⚠️ **测试环境规范（v0.31 红线）**：本地开发/测试一律走**本地环境**——
> - Worker 功能测试用**本地 Docker**（`cd deploy && docker compose up`，`http://localhost:8080`）；skill 指向本地 `WORKER_URL=http://localhost:8080`
> - **禁止用云端生产环境（worker.mxou.cn）做功能测试**（auth/verify、submit_task 等只读/写操作都不行——生产是真实数据 + 真实上架凭证）
> - 云端只能做「用户视角」验证（如确认服务在线/用户反馈问题复现），做完不留测试痕迹
> - skill 的 Chrome/Ozon 页面抓取测试本身在本地浏览器（用户机器），不涉及云
> - 本地测试前按 zombie 警告清空任务表（`DELETE FROM ozon_product_tasks WHERE status IN ('pending','failed','running')`），避免误激活旧任务真实上架

```bash
# Worker 全量测试（关键：必须用 skill venv 的 python——系统 python3 无 pytest/psycopg2/pytest-asyncio；
# 需连本地 Docker PG，端口 5433 密码 localdev123，URL 见下）
cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/ -q
# 无本地 PG 时跑单文件（纯 mock 用例）：
cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_attr_defaults_wave1.py -q

# Worker 单元测试（Mock 模式，无需 PG/GPU）
cd worker && PYTHONPATH=src ../skill/.venv314/bin/python tests/test_full_pipeline_mock_images.py

# Skill 测试（本机 3.14 快速验证；⚠️ python3.12 曾因 Homebrew 迁移前缀损坏，已用符号链接修复可直接用）
cd skill && .venv314/bin/python -m pytest tests/ -q

# Skill 测试（Docker 3.12——与编译 ABI 一致，v0.36 起 CI 标准；本机 3.14 测不出环境差异）
docker run --rm -e PYTHONUTF8=1 -v $PWD:/workspace -w /workspace/skill python:3.12-slim \
  sh -c "pip install -q -r requirements.txt pytest && timeout 600 python -m pytest tests/ -q"

# Skill 单节点测试
cd skill && python3.12 scripts/cli.py graph --url "<1688 URL>"
```

> ⚠️ **测试环境前置（v0.34 实测）**：worker 测试的 pytest 全家桶（pytest-asyncio/psycopg2-binary）装在 `skill/.venv314`；CI（ci.yml）已声明这些依赖，本地需自己 `skill/.venv314/bin/pip install pytest-asyncio psycopg2-binary`。本地 Docker PG 端口 **5433**（非 5432），密码 `localdev123`（见 `deploy/.env` 的 `POSTGRES_PASSWORD`）。

> ⚠️ **worker 全量测试失败先查类目树（v0.59 实测）**：本地 PG 若 `category_tree_nodes` 空（未跑 init_data），learning_record_gate / skill_category_direct / attr_4958 / index_backfill 等测试会失败（`_mapping_valid` 走真实 PG 查树）。先导入：`cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -c "from sqlalchemy import create_engine; from scripts.init_data import import_category_tree; import os; import_category_tree(create_engine(os.environ['PGDATABASE_URL']), language='ZH_HANS', tree_file='category_tree.json')"`。

> ⚠️ **pounding-mcp 测试必须用自身 .venv（v0.60 实测）**：`server.py` import FastMCP（`pounding-mcp/pyproject.toml` 依赖），用 `../skill/.venv314` 跑 `pytest tests/` 会 collection error（`pounding_mcp` 未安装）。需 `cd pounding-mcp && python3 -m venv .venv && .venv/bin/pip install -e .` 后跑 `.venv/bin/python -m pytest tests/ -q`（22 passed：test_router 19 + test_smoke 3）。

| 子项目 | 命令 |
|---|---|
| skill | `pip install -r requirements.txt` |
| skill | `python3.12 scripts/cli.py check`（环境检查 + 自动启动 Chrome） |
| skill | `python3.12 scripts/cli.py graph --url <1688 URL>`（1688 选品） |
| skill | `python3.12 scripts/cli.py follow --ozon-url <Ozon URL>`（Ozon 跟卖） |
| skill | `python3.12 scripts/cli.py discover --keyword "宠物用品" --max-products 50`（Ozon 选品） |
| skill | `python3.12 scripts/cli.py discover --keyword "..." --export csv --output results.csv`（选品+导出） |
| skill | `python3.12 scripts/cli.py image_search --image <URL>`（以图搜款，默认 --source aibuy 免浏览器） |
| skill | `python3.12 scripts/cli.py category "护手霜" --lang ZH_HANS`（类目查询，Issue4） |
| skill | `python3.12 scripts/cli.py search "宠物饮水机" --sort price_desc --rules "profit_margin>=0.1" --export out.csv`（1688 选品+筛选+导出） |
| skill | `python3.12 -m pytest tests/test_aibuy_search.py -q`（aibuy 图搜 + trusted_source + 类目消歧 27 单测） |
| skill | `python3.12 -m pytest tests/test_batch_test_reuse_discover.py -q`（v0.58 batch_test 复用 discover 货源直上 10 单测） |
| skill | `python3.12 scripts/batch_test.py --urls-file urls.txt --client-id xxx --api-key xxx --submit` |
| skill | `python3.12 compile.py`（Cython 编译核心库 → .so/.pyd，必须用 Python 3.12） |
| skill | `python3.12 compile.py --clean`（清理 build/dist 后重新编译） |
| worker | `cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/ -q`（全量，需本地 PG） |
| worker | `cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_attr_defaults_wave1.py -q`（单文件，纯 mock 无需 PG） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/test_attribute_fill_v013.py`（属性字典值回归，8 断言，无需 PG/GPU） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/test_audit_a_fixes.py`（A 批审计修复回归：P1-4 阻断路由 + P0-2 跟卖属性，5 断言，无需 PG） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/integration_attribute_fill_v013.py`（assemble→prepare 全链路，mock 外部 API） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/test_image_prompts_config.py`（生图提示词配置热加载单测，12 断言，无需 PG/GPU） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/test_attribute_fill_v016.py`（v0.16 属性填满/中文零容忍/海关跳过单测，10 断言，无需 PG/GPU） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/test_learning_record_gate.py`（v0.21 成功判据收紧回归，5 用例，无需 PG） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/test_hazard_attr_fallback.py`（v0.21 危险品安全兜底回归，7 用例，无需 PG） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/test_category_match_v021.py`（v0.21 类目同义词/学习缓存一致性，5 用例） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python tests/test_language_routing.py`（v0.29 语言路由：1688 中文→ZH_HANS/Ozon 类目名→RU/无中文残留，4 用例） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_shop_usage_stats.py tests/test_analytics_endpoints.py tests/test_llm_suggest_rerank.py -q`（v0.34 C5/C6/类目 suggest 单测，纯 mock 无需 PG） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_commission_resolver.py tests/test_category_commission_model.py tests/test_commissions_lookup_endpoint.py -q`（v0.59 佣金缓存/端点，纯 mock 无需 PG） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_pricing_dual_margin.py tests/test_title_formula.py tests/test_hashtag_keywords.py tests/test_seo_keywords_endpoint.py tests/test_model_name_9048.py -q`（v0.60 三档定价/标题公式/流量词 tag/SEO 端点/9048 前缀，纯 mock 无需 PG） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_pricing_node_commission.py tests/test_commission_backfill.py tests/test_estimate_endpoint.py -q`（v0.59 定价佣金/回填/estimate，需 PG 或 mock 注入） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_discovery_runs_api.py tests/test_mappings_lookup_api.py tests/test_listing_template_store_overrides.py -q`（v0.56 W10/W11/W9 端点单测，mock 无需 PG） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_store_sync.py -q`（v0.56 店铺缓存 9 单测：租户隔离/upsert/archived/懒同步/调度器，需本地 PG） |
| worker | `PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_mxou_balance_precheck.py tests/test_learning_record_index_backfill.py -q`（v0.56 W12 余额复查 + W6 索引回填单测，mock 无需 PG） |
| pounding-mcp | `cd pounding-mcp && .venv/bin/python -m pytest tests/ -q`（v0.60 对话入口：router 意图路由 19 + server 冒烟 3，须用自身 .venv——skill/.venv314 无 pounding_mcp 包） |
| skill | `python3.12 -m pytest tests/test_selection_rules.py tests/test_ai_preset.py -q`（v0.56 粗筛字段 + --rules ai 单测） |
| skill | `python3.12 -m pytest tests/test_graph_envelope_competitor.py tests/test_discovery_report_hook.py -q`（v0.56 S1 信封竞品 + D12 上报单测） |
| skill | `python3.12 -m pytest tests/test_discover_multi.py tests/test_discover_to_box.py tests/test_template_profile.py -q`（v0.56 discover-multi/to-box/模板单测） |
| skill | `python3.12 tests/test_updater.py`（v0.18 自动更新器单测，11 断言，mock 网络） |
| skill | `python3.12 tests/test_envelope_fields.py`（v0.21 信封字段完整性，2 用例） |
| worker | `bash scripts/local_run.sh -m flow -i '{...}'` 跑全流程 |
| worker | `bash scripts/local_run.sh -m node -n <节点ID> -i '{...}'` 跑单节点 |
| 本地Docker | `cd deploy && docker compose up -d --build`（启动 Worker + PG） |
| 本地Docker | `docker compose exec worker python scripts/init_data.py --force`（初始化数据） |
| 本地Docker | `docker compose exec worker python scripts/warm_category_cache.py --limit 100`（预热 top-100 类目属性缓存） |
| 本地Docker | `docker compose exec worker python scripts/warm_category_cache.py --all --pg-only`（预热全部 7424 类目，~16h，可screen后台） |
| 本地Docker | `docker compose exec worker python scripts/warm_category_cache.py --export-only`（导出 JSON 到 assets/ 供 git 提交） |
| 本地Docker | `curl http://localhost:8080/api/v1/health`（健康检查） |
| 本地Skill | `WORKER_URL=http://localhost:8080 python3.12 scripts/cli.py check`（指向本地 Worker） |
| CI | `bash scripts/ci.sh`（lint → test → docker build） |
| 部署 | `bash deploy/deploy.sh`（一键部署，含自动初始化数据） |
| 更新 | `bash deploy/update.sh`（git pull → rebuild → restart） |

## 环境与密钥

- **Worker 凭证随请求传**（`GraphInput` 里的 `token`/`ozon_client_id`/`ozon_api_key`），不是环境变量。
- Worker 平台级环境变量（`deploy/.env`，完整模板见 `deploy/.env.example`）:
  - `PGDATABASE_URL` — PostgreSQL 连接串（必填）
  - `SUPABASE_URL` / `SUPABASE_KEY` — Supabase `tokens` 表鉴权
  - `APP_WORKSPACE_PATH` — 定位 `assets/` 和 `config/`（Docker 内 `/app`）
  - `PYTHONPATH=/app/src` — Python 模块路径
  - `GRSAI_API_KEY` — MXOU 生图进度轮询（grsai.dakka.com.cn）
  - `RATE_LIMIT_PER_MINUTE` — API 限流（默认 300）
  - `MAX_CONCURRENT` — 并发任务数（**默认 30**，v0.14 起；4核4G 服务器安全值。⚠️ `num_workers` 已联动此值，旧版硬编码 10 已修）
  - `LOG_FORMAT` / `LOG_LEVEL` / `LOG_FILE` — 日志配置（见 LOGGING.md）
  - `SENTRY_DSN` — Sentry 错误监测（v0.23 起可配，任务异常/超时自动上报）
- Skill 环境变量: `WORKER_URL`（Worker 地址）、`OZON_CLIENT_ID`、`OZON_API_KEY`
- ⚠️ 已移除: `COZE_BUCKET_*`（worker 不再自建 S3 存储；**但图片仍托管在 COS**——见「⚠️ COS 图片存储真相」）、`MXOU_TOKEN`（Worker 从请求 token 获取）

## ⚠️ COS 图片存储真相（2026-08-13 实测定位，改图片链路前必读）

> **图片「消失」根因**：AI 生图 URL 由 MXOU 直接托管在**项目自己的 COS bucket `yss-1256275613`（ap-guangzhou）**（`file/images/*` 路径），worker 把该 URL 原样传给 Ozon（`prepare_ozon_upload_node.py`「图片URL直接使用COS URL」）。**Ozon 不存图片副本**，商品卡实时引用 URL——若 bucket 在腾讯云控制台开了生命周期规则（如 N 天删除），对象被删 → URL 404 → Ozon 商品图全部消失，且不可恢复（需重新上架）。

- **仓库内无任何生命周期规则**（仅 CI 清理 `ozon-skill/`/`ozon-worker/` 部署包，保留最近 2 个）——规则只可能在腾讯云控制台手动配置。
- **bucket 多用途共享**：产品图（`file/images/`、`ozon/images/`、`ozon-1688/salvage/`）+ skill 包（`ozon-skill/`）+ worker 部署包（`ozon-worker/`）。生命周期规则**绝不能覆盖图片路径**，否则图消失 + 更新中断。
- **worker 侧唯一 COS 上传**是 `utils/cos_uploader.py`（v0.28.5 E1 兜底）：AI 生图全失败时把 1688 原图转存 COS `ozon-1688/salvage/{md5}.jpg` 补位；未配置 `COS_SECRET_ID/KEY/BUCKET/REGION` 时静默降级（`deploy/.env.example` 未收录这些变量，生产可能未启用）。
- **`_rewrite_payload_images_to_accelerate`**（prepare L1311）：COS 区域域名 → 全球加速域名 `cos.accelerate.myqcloud.com`（Ozon 跨境抓图更稳，幂等）。改图 URL 链路勿绕过。

## 部署

详见 **`docs/DEPLOY.md`**。

```bash
# 一键部署
cd deploy
cp .env.example .env  # 填入凭证
bash deploy.sh

# 一键更新
bash update.sh
```

架构：Docker Compose（Worker + PostgreSQL），轻量级，主要瓶颈在外部 API（MXOU/Ozon），不在本地服务器。

> 💡 **dsh 客户端本地沙箱**：`deploy/docker-compose.dsh.yml` + `deploy/dsh/` 起 pounding dsh 电商客户端（`docker compose -f docker-compose.dsh.yml up -d --build` → `http://127.0.0.1:3080`），供 pounding-sidebar 插件本地开发/挂载调试，与生产 worker 沙箱隔离。

> 💡 **config 热加载**：`deploy/docker-compose.yml` 把 `../worker/config:/app/config:ro` bind mount——宿主机改任何 config JSON（LLM prompt、生图提示词、同义词表）**无需重建镜像/重启容器**，保存即生效（下次调用）。改提示词流程：服务器 `vim worker/config/image_prompts.json` → 保存生效。

> ⚠️ **Docker 清理（v0.34）**：升级走 `cos-update.sh`（服务器无法访问 GitHub → COS 分发），每次 `docker compose build --no-cache` 全量构建会累积历史镜像层 + BuildKit 缓存。**升级成功后脚本自动清理**（builder prune + dangling image + 旧 ozon-worker 非 latest 镜像）；安全边界：不用 `prune -a`（防误伤服务器其他项目）。手动清理可跑 `docker image prune -f && docker builder prune -a -f`。

## GitHub 仓库

- 地址: https://github.com/halojerry/ozon-worker （私有仓库）
- 克隆: `git clone https://github.com/halojerry/ozon-worker.git`

## 数据初始化

首次部署时 `deploy.sh` 自动运行 `scripts/init_data.py`:
- `CREATE TABLE`（全部表，幂等）
  - 导入类目树 → `category_tree_nodes`
  - 导入物流费率 → `logistics_rates`
  - 重复运行安全：已有数据跳过；`--force` 强制覆盖

部署后 `deploy.sh` 后台运行 `warm_category_cache.py --limit 200 --pg-only`，预热 top-200 类目属性到 PG（~5 分钟）。

**为什么不用 JSON 文件存储属性缓存：**
- JSON 裸文件：全量 ~70GB（太大，不能 git）
- PG JSONB（TOAST 压缩）：全量 ~600MB（完全可行）
- 策略：属性 schema + 字典值直接写 PG，运行时懒加载补全
- **v0.11.5 补充**：top-200 子集 JSON（~2MB）提交 git 随 Docker 镜像分发，`init_data.py` 启动时直接导入（详见 `CHANGELOG.md` 0.11.5 段）

### 属性缓存机制

```
1688 中文属性 "白色"
  → PG dictionary_value_cache (ZH_HANS) 查找
  → 命中 → dict_id=61571 ✅（跨语言通用！）
  → 未命中 → Ozon /values API (ZH_HANS) → 写入 PG → 匹配
  → 上传: { dictionary_value_id: 61571, value: "Белый" }
```

dictionary_value_id **跨语言通用**：ZH_HANS 的 `id=61571` 在 RU 下展示为 `"Белый"`，是同一个 ID。

### 属性缓存脚本

> ⚠️ **v1.1 修复（2026-08-01 云端崩溃根因）**：原 `warm_category_cache.py` 把
> 全部类目数据攒内存（峰值 1.5GB+ OOM）且单事务提交全部（PG 内存暴涨锁表 →
> 服务卡死）。已改：**逐节点小事务写 PG**（--pg-only 内存 O(单节点)）、429 限流
> 指数退避上限 3 次（原无限递归）、并发 3→2、API_DELAY 0.05→0.3、导出流式写。
> 全量预热建议分片：`--offset N --pg-only` 每 1000 个跑一次。

```bash
# 预热 top-200 类目（部署后自动跑）
python scripts/warm_category_cache.py --limit 200

# 预热全部 7424 类目（~16 小时，建议分片跑，每 1000 个一段）
python scripts/warm_category_cache.py --all --pg-only
python scripts/warm_category_cache.py --all --offset 1000 --pg-only
python scripts/warm_category_cache.py --all --offset 2000 --pg-only

# 导出 JSON 到 assets/（提交 git，部署时自动导入）
python scripts/warm_category_cache.py --limit 500 --export-only

# 从 JSON 导入到 PG（部署时 init_data.py 自动调用）
python scripts/warm_category_cache.py --import-only

# 断点续传
python scripts/warm_category_cache.py --all --offset 2000 --pg-only
```

## 日志系统

结构化 JSON 日志，四种审计类型：

| 类型 | logger | 说明 |
|------|--------|------|
| 任务生命周期 | `task.lifecycle` | submitted/started/completed/failed/retried |
| 节点执行 | `node.{name}` | 开始/完成/失败 + 耗时 + 输出摘要 |
| Ozon API | `ozon.api` | 方法/端点/状态码/耗时 + 请求/响应摘要 |
| 链路追踪 | 所有日志自动携带 | trace_id / task_id / user_id |

环境变量：`LOG_FORMAT=json`（生产）、`LOG_LEVEL=INFO`、`LOG_FILE`（可选）

代码中使用：
```python
from utils.logger import get_logger, set_trace_context, log_task_event, log_ozon_api_call, audit_node
```

详见 **`docs/LOGGING.md`**。

## 版本管理

- 版本号: `VERSION` 文件（语义化版本 `MAJOR.MINOR.PATCH`）
- 变更记录: `CHANGELOG.md`
- ⚠️ **生产部署必配（v0.62.1 P1-3）**：`CREDENTIAL_MASTER_KEY`（凭证 AES-256-GCM 加密）。
  - 生成 `openssl rand -base64 32`，写入云端 `deploy/.env`（cos-update.sh 不覆盖 .env）。
  - **启用后不可随意更换**：换 key = 存量凭证全部不可解密（GCM 认证失败，不可逆）。
    轮换必须走 `worker/scripts/rotate_master_key.py`（双 key 平滑过渡）。
  - 缺失时：凭证 CRUD 显式 500（v0.62.1 起）、store_sync 解密失败进入退避止损
    （不再 5s 刷屏）；升级脚本会提示缺失。
- ⚠️ **版本四源统一（v0.36，v0.62.0 发版踩坑强化）**: 以下四个文件必须全部一致，缺一不可：

  | # | 文件 | 用途 / 谁读它 |
  |---|------|--------------|
  | 1 | 根 `VERSION` | worker 镜像 `APP_VERSION`（cd.yml）、README/CHANGELOG 对照 |
  | 2 | `skill/VERSION` | **compile.py 打包时用它覆写 dist/SKILL.md frontmatter**（Q10/Q11）；cache.py 缓存指纹也读它 |
  | 3 | `deploy/skill/VERSION` | skill 部署包/update 校验（若滞后，发版包内版本与 tag 不一致） |
  | 4 | `skill/SKILL.md` frontmatter `version` | Agent 侧读取的技能版本 |

  **关键坑（2026-08-31 v0.62.0 实证）**：build-skill.yml 校验的是「打包产物 dist/SKILL.md 的
  frontmatter == 发布 tag」，而 dist/SKILL.md 由 compile.py **用 `skill/VERSION` 覆写**——只改
  `skill/SKILL.md` frontmatter 源码**不会**让校验通过，必须改 `skill/VERSION`；同时 `deploy/skill/VERSION`
  滞后也会让发版包内版本与 tag 不符。三个 skill 相关文件（skill/VERSION / deploy/skill/VERSION /
  SKILL.md frontmatter）任一滞后 → skill 二进制打包失败或产物版本错。

- 发版流程（强制顺序）:
  1. 改四源版本号（根 `VERSION` / `skill/VERSION` / `deploy/skill/VERSION` / `skill/SKILL.md` frontmatter）
  2. 更新 `CHANGELOG.md` 顶部 + AGENTS.md 顶部「最近更新」块
  3. 本地验收全绿（worker `tests/` + skill `tests/` + webui build）
  4. `git tag v{x.y.z} && git push origin v{x.y.z}`（触发 build-skill.yml 4 平台编译 + cd.yml 部署两条链路）
  5. 确认 CD 两个 workflow 均 success（Docker 镜像 + Release + COS 部署包 + skill 二进制包）
  6. 服务器 `bash deploy/cos-update.sh` 升级 worker；skill 用户端 updater 自动更新
- 发版前快速核对命令：
  ```bash
  grep -H "" VERSION skill/VERSION deploy/skill/VERSION | sed 's/:$/: /'
  grep -m1 '^version:' skill/SKILL.md
  # 四个输出必须都是同一版本号
  ```

## 开发规范

- Commit: `<type>(<scope>): <中文描述>`（如 `feat(worker): 结构化日志`）
- 分支: `feat/`、`fix/`、`refactor/`、`docs/`、`hotfix/`
- Pre-commit: `git config core.hooksPath .githooks`（自动检查 .env + 密钥 + 语法）
- 详见 **`docs/CONVENTIONS.md`**

## 深入阅读（改前先看）

- **`docs/PLAN-conversation-entry-v1.md`** — ⭐ Q3 对话入口方案（dsh Agent tab 先行 + 专家 tab 对话 UI 二期；router.py 意图路由层设计——改对话入口前必读）
- **`docs/PLAN-card-merge-fix-v1.md`** — ⭐ Q4 并卡修复方案（9048 前缀/标题 SEO/生图回退告警/上架前检测四阶段——改 9048/变体合并前必读）
- **`docs/PRD-skill-learn-shangpinbang-v1.md`** — ⭐ skill 学习上品帮 v1 PRD（F1-F13 需求分解 + S1 混合键/D12 白名单/W11 全局共享 3 决策定稿——改 discover 漏斗/graph 信封前必读）
- **`docs/competitor/shangpinbang-skill-learning-notes.md`** — 上品帮客户端源码调研底稿（scrollPage 缓动公式/parse.services/BASE 粗筛/AI 阶梯门槛事实，改漏斗逻辑前查依据）
- **`docs/competitor/README.md`** — ⭐ 竞品 ERP 调研总索引（上品帮 × 毛子ERP 全量字段级分析 + 自有 WebUI 复刻路线 P0-P3——做 WebUI 功能前必读）
- **`docs/competitor/shangpinbang-full.md`** — 上品帮全站 24 章（18 菜单 40+ 页面字段/状态机/跳转/计费）
- **`docs/competitor/maozier-backend-full.md`** — 毛子ERP 网页后台 18 章（11 菜单全路由/弹窗/毛豆计费/实操验证）
- **`docs/competitor/maozier-plugin-full.md`** — 毛子ERP 插件 9 章（一键上架 11 字段/选品规则 22 条件/AI 套图/API）
- **`docs/PLAN-skill-image-search-v1.md`** — ⭐ 图搜改造方案（aibuy 通道/trusted_source/类目消歧/定价复用/6 Issue 落地记录——改图搜/类目前必读）
- **`skill/SKILL.md`** — ⭐ Agent 调用指南（Chrome 启动、选品、跟卖、以图搜款、批量处理；100 行精简版，越界/裂变/趋势细则在 `references/`）
- **`skill/references/command-reference.md`** — ⭐ 全命令参考：意图路由决策树 + 各管线（A/B/C/D/E/F）触发条件、完整参数、示例、输入输出
- **`skill/references/env-setup.md`** — 环境准备：凭证获取/配置、check 故障排查、data/ 目录语义（防误删）
- **`skill/references/error-codes.md`** — Worker 错误码表 + 进度查询口径 + CLI 错误处理 + 错误恢复决策
- **`skill/references/output-schema.md`** — 输出字段解析：submit_result / check_task_status / product_summary / discover 分析文档
- **`skill/references/discover-fission.md`** — 裂变选品（discover --fission）流程、预算限制、参数、数据字段
- **`skill/references/trend-selection.md`** — 趋势选品（agent 自主分析 + discover 执行）三步法 + 纪律
- **`skill/references/anti-patterns.md`** — 越界行为 → 后果 → 正确做法对照表 + 核心纪律
- **`skill/field_mapping.md`** — 1688/Ozon 字段 → 信封字段映射规则 + 单位转换 + 图片顺序规范
- **`skill/envelope_example.json`** — 完整信封结构示例（单 SKU + 跟卖两种模式）
- **`pounding-sidebar/README.md`** — ⭐ dsh-better-sidebar 消费插件开发指南（registerTab 契约三步：type-import/inject/包 effect；构建/挂载/调试；7 板块数据源）
- **`docs/DEPLOY.md`** — ⭐ Worker 云端部署完整指南（Docker、Nginx、HTTPS、运维）
- **`docs/WORKER-TOPOLOGY.md`** — Worker 拓扑与错误处理手册（节点流、错误映射、数据流、改代码快速参考）
- **`docs/CONTRACT-v4.md`** — ⭐ Skill↔Worker API 契约 v4.0（端点、请求/响应、错误码、节点合约；v3.0 旧版已归档 `archive/docs/legacy/`）
- **`docs/LOGGING.md`** — 日志系统架构 + 查看命令 + 故障排查流程
- **`docs/CONVENTIONS.md`** — 分支命名 + commit 规范 + 发版流程
- **`docs/OZON-ATTRIBUTE-API.md`** — ⭐ Ozon 属性/类目 API 参考（5 接口定义 + 属性填满策略 + 关键属性 ID 表，开发直接查）
- **`docs/OZON-MULTI-SKU-QUOTA.md`** — ⭐ Ozon 多 SKU 上传与商品配额机制（9048/model_id 绑定合并 = 1 卡 1 配额；竞品 merge 开关/变体上限；我们已对齐但缺 merge 开关/上限校验）——改多 SKU 变体逻辑前必读
- **`worker/AGENTS.md`** — Worker 完整文档：节点流程、Ozon API 坑
- **`worker/src/api/errors.py`** — 统一错误码（改错误响应前必看）
- **`worker/src/api/schemas.py`** — Pydantic schemas（改 API 前必看）
- **`worker/config/*.json`** — LLM prompt 配置（均走 mxou deepseek-v4-flash）

## 需牢记的约定

- **单产品上传**：Skill 层折叠变体（`_collapse_variants_to_single`），一个 1688 item = 一个 Ozon 产品卡。
  - 数量变体 → 选"1只装"
  - 颜色/尺寸变体 → 中位数选价
  - 采购成本 = 代表变体价格 + 1688 国内运费(`freightCny`)
  - 标题不加颜色/数量后缀
  - Worker 层零改动：`variants=[]` 走现有单产品路径
- **类目树 ID 跨语言一致**：`category_tree_nodes` 存中俄双语（`language=ZH_HANS` / `language=RU`），同一 `description_category_id`/`type_id` 跨语言一致。类目匹配用 `ZH_HANS` 搜索（与 1688 中文类目名匹配），上传时 `dictionary_value_id` 跨语言通用。属性 schema 从 Ozon API 获取时也用 `ZH_HANS`（v0.5.0 起从 `RU` 改为 `ZH_HANS`），与 1688 产品属性名匹配。
- **品牌默认无品牌**：所有产品强制默认为 `Нет бренда`（dictionary_value_id=126745801）。不管 1688 数据或 LLM 匹配到什么品牌，一律覆盖。品牌属性不存在时自动补充。代码位置：`assemble_ozon_product_node.py:1007-1022`。
- **制造商用 supplier 填充**：attr=23487（Производитель）是自由文本属性（dictionary_id=0），不是字典属性。用 `draft.supplier`（1688供应商名）填充，不写空值。
- **描述强制净化**：`_sanitize_description()` 在翻译后移除拉丁文、中文、URL、邮件、电话、营销词。代码位置：`prepare_ozon_upload_node.py`。
- **9782 危险品等级安全兜底**（v0.21 修正）：attr=9782 是某些类目的必填属性，从 SKIP_ATTR_IDS 中移除（3处：prepare/validate/status），但取值**只挑「非危险」安全默认** `get_safe_hazard_default`，取不到则跳过——删除「取第一个字典值」兜底（曾填成「爆炸物 Category 1」被拒 BR_hazard_class1）。代码位置见 `WORKER-TOPOLOGY.md` 关键属性ID表。
- **cm→mm 阈值 200**：`max_dim < 200` 判断为 cm 转 mm（原 50 太保守，推车等大物品被误判）。
- **小重量自动乘 1000**：`weight_g < 10g` 但尺寸 > 50mm 时自动乘 1000（疑似 kg→g 单位错误）。
- **物流费率表必须初始化**：`logistics_rates` 表为空时兜底费率 `weight * 0.15 CNY` 严重虚高。deploy.sh 须确保 `init_data.py` 在 worker 启动前执行完毕。
- **定价公式唯一入口**：CNY 店铺不使用 fx_buffer（无汇率风险）。v0.60 起售价 = 总成本×(1+margin)/(1-commission-variable_cost_rate)，三档语义见上方「v0.60 新增关键约定」——**禁止在各处内联定价公式**（三处共用 `compute_price`）。兜底物流费率从 0.15 降到 0.05 CNY/g。
- **图片顺序规范**：`primary_image` = `main_image`（营销主图，单独指定），`images` 数组按 IMG_ORDER：social_proof → detail → scene_1 → scene_2 → scene_3 → comparison → multi_angle（倒数第二）→ white_bg（最后）。
- **变体图片降级**：白底图生成失败 → 统一营销主图（非 1688 alicdn 原图）。
- WARNING 级 Ozon 错误过滤不算失败；`ozon_status` 返回 `pending` 视为软成功（**仅限审核中状态路由**；v0.21 起成功判据收紧——learning_record 只认 `moderate_status=="approved"`，「pending+product_id 视为成功」已删除）。
- `GlobalState` 自定义 reducer：`progress_counter`=max、`error_message`=覆盖、`failed_stage`/`stages`=合并。
- **Docker 部署**: `deploy/docker-compose.yml` 含 PG + Worker，`HEALTHCHECK` 已配置。
- **API 版本化**: 新端点走 `/api/v1/`，旧路径保持兼容。

### v0.60 新增关键约定（改定价/标题/9048/对话入口前必看）

- **定价公式唯一入口 `compute_price`**（`utils/pricing_estimate.py`）：pricing_node / estimate 端点 / skill 前端计算器三处共用，**禁止各处内联定价公式**（v0.40 共享层纪律延伸——变体循环曾内联 ×1.15/×1.2 与主路径漂移）。改定价必须进 compute_price + 三处接线。
- **三档双价格语义**：`price`=日常价(margin_rate) / `old_price`=划线原价(margin_anchor=2.0，强制 ≥ 日常×1.2) / `promo_price`=促销底线(margin_floor=0.6)。**利润口径是销售净利率 `profit/price`**（profit = price×(1-commission-variable_cost_rate) - 总成本），不是成本利润率。全缺省 → 旧单档行为（0.25/0.10/0.05）逐字保持。
- **变动成本率**：日常 `variable_cost_rate=0.155`（推广6+退货5+提现1.5+汇损2+附加1），促销 `promo_variable_cost_rate=0.245`（推广12+退货8+提现1.5+汇损2+附加1）——Ozon 佣金/推广/退货都按售价比例扣，只扣佣金会利润虚高 20-30%。promo_price 供 `min_seller_price` 防 Ozon 自动促销跌破成本。
- **标题公式唯一入口 `utils/title_formula.py`**：`build_title_formula_prompt` / `parse_title_formula_keywords`（纯西里尔过滤/去重/≤3）——prepare 主路径/兜底/内部 fallback + ai_field_service 草稿重生成 **4 份拷贝已统一**，新增标题公式逻辑必须进本模块。
- **SEO 流量词链路**：`extensions.traffic_keywords` → 标题生成 prompt 建议行（LLM 自主融入不硬塞）+ `_generate_hashtags` 流量词优先（> `_HASHTAG_RU` 字典 > 西里尔提取 > `#товар`）。数据源 `GET /api/v1/seo/keywords` 读 `blue_ocean_queries`——改流量词注入勿绕过这单一链路。
- **9048 防并卡前缀**：`_derive_model_name_9048`（prepare L1228）只加在 CREATE 路径；**跟卖 UPDATE（is_follow_sell）刻意不加前缀**（本就要并卡）。hash 只用确定性字段（item_id/supplier/中文标题），**绝不用 LLM 翻译后标题**（retry 重生成会拆卡）。改 prepare 变体分支勿破坏（`test_model_name_9048.py` 9 单测锁定）。
- **对话入口意图路由**：`pounding-mcp/pounding_mcp/router.py` 是 SKILL.md 决策树固化层（URL 正则 + 九类意图词表），`tasks_server` `/ask` 消费。新增意图/管线映射必须改 router.py，勿在 server.py 手写路由逻辑。

### harness-store-analysis 新增关键约定（改数据沉淀/店铺分析/执行端点/专家版图前必看）

- **数据沉淀三表只 append**：`store_metrics_history`/`store_operation_log` 均无业务唯一键（append-only，靠自增 id）；`selection_insights` 唯一键 `(keyword, contributed_by_token_id)` 去重 upsert。**改这三表逻辑时保持 append/去重语义**，勿加改动覆盖历史记录。
- **无成本不编造利润**：`store_metrics_history.profit_amount/profit_rate` 无成本时写 NULL；`store_analysis_service` 对无成本商品（`has_cost=False`）只填当前价 + 库存，**不填 profit_rate**——禁止给无成本商品造利润（`get_decrypted` 跨租户校验 404）。
- **利润唯一入口**：`store_analysis_service` 的利润率经 `estimate_from_envelope`（复用 `commission_resolver` + 物流费率唯一入口）provisional band pass 计算，**禁止在分析服务另写估算公式**（与定价同源，防漂移）。
- **审计唯一写入口**：`store_operation_log` 唯一写入口是 `services/store_operation_log.py` 的 `_write_operation_log`（result 不依赖成功率：pending/failed 同样落一行）。`store_actions_routes` 只做业务 + 计算 after，**不重复插入逻辑**。
- **执行端点只包装 + 卖货 API 调用**：`POST /stores/{id}/actions` 的改价/库存/归档分发 `shelf_service`、活动报名/自建促销分发 `promo_client`；**只做包装，不自动执行**（由 skill/前端触发）。**不调用 Performance API（`/api/client/*`）**——需独立广告 OAuth，`promo_client` 白名单禁止（在 roadmap），广告投放**不是**已实现能力。
- **promo_client 契约取自 ozon-mcp**：`promo_client` 的 Seller 端点白名单（`/v1/actions` 等）以 `PCDCK/ozon-mcp` 的 466 方法索引为权威，只碰卖货相关端点；改促销逻辑勿绕过白名单或触碰 `/api/client/*`。
- **店铺跨租户绑定拦截**：`credential_service._assert_client_not_bound_elsewhere`——同 `ozon_client_id` 已被其他 tenant 绑定 → 409 `该店铺已被其他用户绑定`。改创建/轮换凭证逻辑勿移除（`uq_credentials_tenant_client` 唯一槽只能拦同租户，跨租户需此显式断言）。
- **MCP 店铺工具直接走 worker HTTP**：`mcp__pounding__analyze_store`/`run_store_action` 在 `worker_http.py` 直接 `_request` 调 worker REST（**非 skill CLI**），失败返回 error dict 不 raise。新增 store 相关 MCP 工具勿回归 skill CLI 子进程模式。

### v0.59 新增关键约定（改佣金/定价/发货模式前必看）

- **佣金解析唯一入口** `utils/commission_resolver.py`：`resolve_commission_rate` 链（explicit > category_commission 缓存表(band 选段) > extensions.commission_segments > 0.10）。**定价/选品/estimate 三处共用**，新增佣金逻辑必须进 resolver，禁止各处内联（防漂移）。
- **佣金是「类目 × 发货模式 × 价格段」矩阵**：`/v5/product/info/prices` 的 `sales_percent_rfbs` 需真实 offer_id（上架后才有），选品时只能用 what_to_sell 的 `rfbs_rate`/`fbp_rate` 分段对象。**不要在定价时调 `/v5/product/info/prices` 空 filter**（三重 bug 教训，见 v0.59 区块）。
- **provisional-price band pass**（pricing_node）：佣金档位依赖售价、售价依赖佣金——先 0.10 算临时价选档，再 resolve 真实佣金重算。RUB 店铺直接用临时价；CNY 店铺用 `leq_5000` 中性档（无真实汇率换算）。
- **信封佣金透传**：skill `extensions.commission_segments = {fbs:{leq_1500,leq_5000,gt_5000}, fbo:{...}}`（rfbs→fbs/fbp→fbo 映射），worker resolver 作 `segments` 源消费。无分段时**不加该键**（向后兼容）。
- **发货模式**：`sales_schema` 标注所有模式（FBO/FBS/rFBS）不剔除，仅导出/webui 筛选；过滤用 `_match_sales_schema` 子串匹配（`"FBS" in "RFBS"` = True，对齐竞品），`sales_mode` 默认不过滤只标注。
- **多 SKU 变体合并**：颜色/尺寸变体用属性 9048（=item_id）绑定 → 合并 1 卡 = 1 配额；数量变体（`variant_type="quantity"`）走独立产品（N 配额）。改 `prepare_ozon_upload_node.py` 变体分支勿破坏（详见 `docs/OZON-MULTI-SKU-QUOTA.md`）。

### v0.58 新增关键约定（改重量估算/费率表/batch_test 货源复用前必看）

- **重量估算同源统一**：skill 侧所有运费估算走 `ozon_discovery.estimate_shipping_cny(weight_g)` 共享函数（分段 ≤500g ¥6 / ≤1000g ¥8 / >1000g ¥15，缺失按 `DEFAULT_WEIGHT_G=500`）——cloud_probe `price_estimate` 与 discover `_calculate_profit` 同源调用，**禁止各自内联分段**（曾差 ¥9/单导致轻小件误判利润不足）。
- **费率表是权威，无重量也查**：`_query_logistics_from_worker` 重量 None/≤0 时按 `DEFAULT_WEIGHT_G(500g)` 查 `/api/v1/logistics/quote`（`eff_weight` 同步进 last-good 缓存键）——无重量只是少一个查询维度，不是放弃查表的理由。本地估算仅作 worker 离线且无 last-good 时的末级兜底。
- **batch_test 复用 discover 货源直上**：`process_ozon_url` 优先 `_find_discover_source()` 复用 discover 已匹配好的 1688 货源（`build_envelope_from_discovery` 直上，免 CDP + 图搜），未命中才走 follow；`_need_cdp` pre-flight 预扫描，纯复用批次不启 Chrome——改 batch_test 勿破坏复用分支（`test_batch_test_reuse_discover.py` 10 单测锁定）。

### v0.56 新增关键约定（改余额/额度/店铺缓存/skill 漏斗前必看）

- **余额判定三处一致**（auth_verify/submit_task/auth_node）：**优先查 MXOU 平台真实余额**（`get_mxou_balance`），失败降级 Supabase `users.quota`；`unlimited_quota` 仅兜底分支放行标记，**MXOU 实查欠费必拒**。绝不用 `tokens.remain_quota`（僵尸字段）。
- **余额不足 fast-fail 契约**（v0.56.4）：`call_mxou_chat_api`/`call_mxou_image_api` 入口都做余额 pre-check（`_check_balance_cached` 30s TTL，`MIN_BALANCE_THRESHOLD=1.0`），不足抛 `MxouOutOfQuotaError`；403/OUT_OF_QUOTA 响应**不当普通 4xx 静默返回 None**。改 MXOU 调用链时勿把 403 当普通失败重试。
- **key 无限额度 vs 真实消费**：webui 自动创建 key 写 `unlimited_quota=true`（`_upsert_supabase_token`），但消费仍走 MXOU 真实余额——**勿因 unlimited_quota 跳过 MXOU 实查**（Sentry 实证：unlimited=true 但平台欠费仍放行 → 生图 403）。
- **店铺缓存**：订单/商品读取走 PG 缓存表（`ozon_orders_cache`/`ozon_products_cache`/`credential_sync_state`，均含 `tenant_id` + 租户唯一键）；15min 调度器 + `POST /stores/{id}/sync` 手动 + 懒同步。**改读取必须按 tenant_id + credential_id 过滤**，凭证归属一律 `get_decrypted` 校验（跨租户 404）。
- **S1 混合键**（skill 信封竞品数据）：`extensions.competitor_weight_g`/`competitor_dimensions_mm` + `draft.ozon_attributes`/`competitor_price`/`follow_min_price`——**勿用嵌套 `extensions.competitor.*`**（worker 读扁平键）。
- **D12 上报裁剪**：`REPORT_FIELDS` 20 字段白名单（~25KB/run）单次 POST `/api/v1/discovery/runs`——勿上报 `competing_seller_list`/`match_1688_images` 大字段。
- **W11 类目映射全局共享**：`category_mapping` 表无 tenant_id，保持全局——**勿加 tenant 隔离**（碎片化达不到 MIN_SUCCESS_COUNT=3 → 学习表失效）。
- **webui 部署形态**：webui + worker **同一云端 docker-compose**，webui 静态产物 bind mount 进 worker 容器（`WEBUI_DIST=/app/webui/dist`），由 FastAPI `_mount_webui_static` 同进程伺服 `/app`——**不是独立服务**，单端口 8080 同时服务 API + webui。
- **gitleaks v3**：CI secret-scan 用 `gitleaks/gitleaks-action@v3`（内部按 push/PR 范围扫描），**勿加回 log-opts**（v3 不支持，v2 floating 拉新版会 Unexpected input 失败）。

### v0.40 新增关键约定（改属性填充/匹配/遥测前必看）

- **共享匹配层 attr_value_matcher.py**: 三处（assemble 构建期/prepare 补全期/retry 修复期）属性匹配统一走 `utils/attr_value_matcher.py`（L1 纯函数：match_attr_name/match_dict_value/unique_or_none/lang_route）。**新增属性匹配逻辑必须进 matcher，禁止三处各自内联**（防漂移，合同测试 test_contract_attr_consistency 锁定三处 dict_id 一致）。
- **LLM 消歧安全三件套**（`disambiguate_candidates`，默认关 `ATTR_MATCH_LLM_DISAMBIGUATE`）：① prompt 显式「以上都不对 → 输出 -1」出口 ② LLM 只输出**候选索引**，dict_id 一律从确定性候选列表重查证（绝不信任 LLM 数字）③ 解析失败/越界/异常 → abstain 跳过，**绝不降级取第一个**。照搬 skill `_llm_disambiguate_category`（无 none 出口）会系统性错填。
- **多候选绝不盲补首值**: `unique_or_none` 单候选才填（matched）、多候选 → llm_eligible（Phase 4 消歧）、0 候选 → skipped。**禁止恢复「取第一个」**（v0.13 套娃教训 + 实测多命中取 [0] 是既有雷）。
- **搜索语言路由 lang_route**: /values/search 无 language 参数（语言无关），**搜索词语言决定结果**。中文词 → ZH 优先、俄语词 → RU 优先（retry `_search_dictionary_values_chain` 已改，prepare 搜索路径中文值优先搜、RU 映射词兜底——实测 '白色'→61571 命中/'инсектицид'→空）。
- **缺口量化**: `utils/attr_gap.py` 的 `should_fill` 过滤五类"本就不该填"（海关 is_customs_attr/23536 标记码/9782 危险品/品牌 85-31-5076 强制 Нет бренда/4389 原产国硬编码/9048-4191-4180-23171 系统生成/23487-22390 专用路径）——改 `_fill_optional_dict_attrs` 前先跑 `scripts/gap_report.py` 看真实缺口。
- **属性审计 attr_match_log**: 每次属性解析写 `attr_match_log` 表（非致命 writer `utils/attr_match_log.py`，task_id 空跳过/DB 异常 warning）。**attempted_fill_rate 做即时回归**（每次改动可测），**verified_fill_rate 做月度校准**（fetch-back 回读确认；注意 fetch-back 只回读 approved 卡，审核超时 pending 无数据 → 双通道：零错误 import + 审核通过）。

### v0.39 新增关键约定（改图搜/类目匹配/定价/查询展示前必看）

- **aibuy 图搜是主通道（免浏览器）**: `search_by_image_aibuy`（ozon_image_search.py）——Chrome cookie（`_m_h5_tk` 等 4 个）→ mtop 签名直调 imagesearch API。**冷启动 requests 拿不到 token（1688 反爬）**，必须从 Chrome 会话读。token 缓存在 settings.json（key `aibuy_mtop_token`，6h 过期自动刷新）；**fail-fast 纪律**：无 token/失败快速返回 [] 由调用方降级 CDP/AK，不 raise 不慢等（test_follow_* 未 mock aibuy 依赖此）。缓存 ns `aibuy_search` 6h。
- **mtop 签名**：`md5(token&t&appKey&data)` + `H5Request=true` + UA/Referer/cookie 组合——封装为 `_mtop_sign`/`_mtop_request`，**不复用 `_post_1688`**（那是 AK 网关 x-csk，认证体系不同）。
- **trusted_source 分通道护栏**: `_pick_best_match(..., trusted_source=True)` 仅 aibuy 来源置 True——放行规则 = `idx ≤ 1`（前 2 位），**normalizationScore 不是放行信号**（实测最高分≠最相似，仅 norm_bonus 加分 ≤5）。AK/CDP 恒 False 维持 conf≥0.3 + LLM rescue。改 `_pick_best_match` 勿全放松（历史错配案例"花插¥1/活体羊驼¥2000"）。
- **AK similarity_score 上膛**: `_pick_best_match` 消费 AK 候选 `similarity_score`（0-100 归一化，评分加分 ≤20 + no-badge 高分放行 ≥0.8）。aibuy 候选无此字段不受影响。
- **类目匹配三层信号**: ① `source_category_path` 末级词优先（1688 三级类目面包屑）② 复合词顿号分拆（`_category_search_variants`，"化妆刷、刷包"→"化妆刷"命中）③ 候选>1 时 LLM 消歧（`_llm_disambiguate_category`，护手霜/粉扑/收纳盒等深度歧义词）。**匹配不到→可见告警**（不再静默放行）。改 publish_product_new/build_graph_envelope 类目搜索须同步两处。
- **定价复用 worker 公式**: skill 端利润估算 = `总成本×(1+margin)/(1-commission)`（与 worker pricing_node 同源）+ 真实运费（`_query_logistics_from_worker` 调 `/api/v1/logistics/quote`）+ 店铺 margin/commission（与信封 extensions 同源）。**勿在 skill 另写独立估算公式**（两套公式会漂移）。
- **query 展示完整明细**: `_print_query_result` 展示 product_summary 的 purchase_url/purchase_cost/logistics_cost（worker 已算好，skill 展示层此前丢弃）+ 比价建议。新增字段勿只展示 OzonID/售价/利润率。

### v0.36 新增关键约定（改缓存/删除/版本/浏览器链路前必看）

- **缓存纪律**: 磁盘缓存统一走 `cache.py`（namespace+TTL+SHA256 key），只缓存成功结果。新增缓存 namespace：`probe1688`(24h)/`slug_cn`(30d)/`follow`(6h)/`ak_img_search`(6h)/`ak_search`(24h)/`ozon_sellers`(6h)。**key 必须含语言/ID 维度**（防固化错误货币数据——用户 Ozon 页面可能 CNY/RUB 混杂）。改 follow/discover 链路勿移除这些缓存。
- **safe_unlink/safe_rmtree（utils.py）**: Windows 沙箱删除文件必须用（fail-open：unlink → os.remove → warning 返回 False 不 raise）。**新增裸 `Path.unlink()`/`shutil.rmtree()` 调用会被 CI 之外的 review 拦截**——统一走安全删除。
- **discover 竞品数据注入信封**: `build_envelope_from_discovery` 把候选的 `weight_g/dimensions_mm` 注入 `extensions.competitor_weight_g/competitor_dimensions_mm`（worker `_resolve_weight_dimensions` 兜底链 C2）；`ozon_category` 优先用 what_to_sell 权威类目（category2_id/3_id）。改 discover 提交链路勿丢这两个注入。
- **what_to_sell 27 字段**: `_extract_metrics`（ozon_seller_analytics.py）解析全部运营指标（sold_count/sold_sum/sales_dynamics/drr/days_in_promo/discount/promo_revenue_share/days_with_trafarets/qty_view_pdp/conv_to_cart_pdp/session_count_search/conv_to_cart_search/conv_view_to_order/custom_click_rate/sales_schema/nullable_redemption_rate/重量4497/尺寸9454/9455/9456/权威类目）。新增字段时保持 camelCase→snake_case 映射一致。
- **浏览器检测统一**: `chrome_launcher._find_chrome_executable` 委托 `service.find_browser_executable`（富实现），探测期间**禁用自动安装**（`_auto_install_browser` 置 False——防无 Chrome 环境挂起 300MB 下载）；支持 `CHROME_PATH` env（Phase 0）。改浏览器查找勿分裂成两条逻辑。
- **登录误判**: `_wait_for_login_session` 返回 `{ok, session, reason}`（reason: no_cdp/timeout/cdp_error）——enrich 按 reason 区分「未找到浏览器/登录超时/启动失败」。勿把三者混为一谈（曾误导用户反复扫码）。
- **version 四源**: 改版本必须同步 root/skill/deploy-skill VERSION + SKILL.md frontmatter（compile.py 打包时覆写 frontmatter，但源码 frontmatter 也保持同步避免误导）。

### v0.34 新增关键约定（改类目匹配/品牌/Sentry/analytics 前必看）

- **竞品尺寸重量兜底**：`prepare_ozon_upload_node.py` 的 `_resolve_weight_dimensions(draft, extensions)`——draft 原值 → `extensions.competitor_weight_g/competitor_dimensions_mm`（skill 信封 extensions 传入）→ 100g/300×200×50mm 硬编码三级兜底；`draft_sanity` 对 weight=0+竞品数据放行。改 prepare 重量/尺寸逻辑必须走此函数（v0.34 抽取，勿改回内联死代码）。
- **类目末级词搜索**：`specific_terms = cat_terms[-1:]`（原 `[-2:]` 会被上级词 token 稀释——「科教玩具 其他益智玩具」分词后 sim 0.5→0.333 错配）。只留末级词整体辨识度最高。改 `assemble_ozon_product_node.py` 类目匹配时勿改回 `[-2:]`。
- **LLM 类目 fallback max_tokens=4096**：deepseek-v4-flash 推理模型 `reasoning_tokens` 吃 `max_tokens` 配额，10/200 输出必空 → fallback 恒失败。改 `_llm_rank_categories` 时勿改小 max_tokens。
- **suggest 二次搜索**：`_llm_rank_categories` 返回 `{"_llm_suggest": True, "suggest_keywords": ...}` 时，上层**必须重跑 LLM 排名**（合并候选后）或从合并后 candidates top1 回退——否则 best_by_llm 无 full_path → 重叠检查恒失败 → 硬阻断（review 修复的 dead code）。
- **品牌 85/31/5076 直写无品牌**：`BRAND_ATTRIBUTE_IDS` 强制段统一补充 `Нет бренда`(126745801)，missing_required 循环里跳过品牌 ID（不走字典兜底，避免误导性 ERROR + 无谓 API 拉取）。
- **规格表中文属性名净化**：`_append_spec_table` 对属性名 `name` 也要做中文/拉丁净化（schema ZH_HANS 中文属性名进规格表 → 描述含中文 → validate 拦截）。改描述净化时同步 name。
- **analytics 端点安全**：`/api/v1/analytics/*` 三端点——按 token 限流（复用 RateLimiter）+ 单次 ≤2000 条 + 错误不回显内部异常。`contributed_by_token_id` 存完整 token key（与 payload 同先例）。
- **Sentry token 指纹**：`_token_fingerprint`（前 8 位 + sha1 前 6）不泄露明文；mxou 错误分支 + capture_task_error 都带用户上下文——云端可按 username 筛选错误定位「哪个账号余额不足」。

### v0.30 新增关键约定（改 retry/属性匹配/学习闭环/CDP 前必看）

- **retry 字典属性纪律(测试锁定)**: 语义匹配 → type_id → 标题2-gram → 唯一值 → None（绝不取第一个）。`validation_retry_loop.py` Step 2.5 与 assemble/prepare 已统一，改 retry 属性修复勿再引入盲补首值。
- **revalidate 双守卫**: hazard（9782 只放行安全默认）+ is_aspect（schema `is_aspect=true` 属性创建后不可改，retry 跳过）。`attribute_utils.is_aspect_attr` 是唯一入口，schema 缺字段时按名称关键词兜底。
- **fetch-back 是唯一 dict 漂移校准机制**: approved 后 `fetch_back_node` 调 `/v4/product/info/attributes` 回读 → diff → `attr.outcome` 遥测。改 graph 路由时**不要移除** `成功 → fetch_back → learning_record` 边。
- **学习门**: 被擦除（erased）/Ozon 自动填默认（`attributes_with_defaults`）的属性**不写入** `ozon_attribute_mappings`。
- **provenance 消费**: `ozon_attribute_mappings.source` 列——learned_approved/fetch_back_corrected 可复用；default_fallback 可出场但 success_count 不增长；retry_recovered 隔离待 fetch-back 确认；fabricated `[{name}]` source_value 一律跳过。历史数据用 `worker/scripts/backfill_mapping_source.py` 回填。
- **skill 顶层 preflight**: `_preflight_runtime`（Python≥3.12+requests/websocket/PIL）在 `main()` 解析后立即执行，缺依赖 return 1。新命令要豁免需显式加入豁免清单。
- **Chrome profile 统一**: 工具 Chrome 全部用 `data/browser/profiles/1688/default`（`chrome_launcher._default_profile_dir`/`cli._chrome_profile_dir`/`service._profile_dir` 三处一致）。改 Chrome 启动逻辑勿再引入第二 profile 路径；老路径迁移用 `scripts/migrate_profile.py`。
- **find_tab 释放契约**: `find_tab` 命中**用户已有 tab** → 必须 `cdp.release(tab)`，否则 `conn.close()` 远程关闭用户标签页。新代码复用已有 tab 一律先 release。
- ⚠️ **zombie recovery 本地陷阱**: 启动清理复活 failed→pending，本地测试会误激活旧任务真实上架。本地 Docker 测试前先 `DELETE FROM ozon_product_tasks WHERE status IN ('pending','failed','running')`。

### v0.29 新增关键约定（改属性匹配/运费/Chrome/余额前必看）

- **属性语言路由(测试锁定)**: `values/search` **无 language 参数**(语言无关, 中文直查); schema/values 全量接口 language 决定返回文本语言。1688 中文值 → 中文直查; Ozon 类目名 → RU。字典属性 value 文本**禁止中文**(命中 ZH_HANS 缓存时置空/用 RU, dict_id 权威)。
- **运费端点**: `POST /api/v1/logistics/quote`(worker)与 `utils/logistics_quote.py` 公共模块同源, pricing_node 已改用它。skill 端 `_query_logistics_from_worker` 失败降级本地 40 CNY/kg。
- **Chrome 常驻**: 工具 Chrome 独立 profile(`data/browser/profile`)+ 常驻, **命令出口不关**; close_tool_chrome 仅显式调用。用户手动关后下次命令独立 profile 重启必成功(无单实例锁)。
- **余额统一**: Worker auth/submit/auth_node + skill check 全部查 MXOU 平台真实余额(`users.quota`), 不用 `remain_quota`(僵尸字段)。
- **Sentry**: `~/.sentryclirc`(sntryu_ token, us.sentry.io, pouding_ozon); 查错误用 `sentry-cli issues list` 或 REST API。

### v0.17-v0.25 新增关键约定（改跟卖/成功判据/属性兜底前必看）

- **跟卖双模式** `extensions.follow_type`：hand（默认，CREATE 重建防侵权）/ api（import-by-sku 复制）；skill 有货源→hand、无货源→api，worker hand 缺货源数据自动降级 api。
- **offer_id 统一 `follow_{竞品ID}`**：import-by-sku/assemble/prepare 三处一致，防 api 模式双卡（旧代码不一致 → import-by-sku 建一张 + upload 又 CREATE 一张）。
- **成功判据收紧**：learning_record 只认 `moderate_status=="approved"`；假成功三处已改（imported 即 success / pending+product_id / 不可修复标 success → pending_moderation/rejected_unfixable）。学习缓存污染用 `worker/scripts/clean_category_mapping.py` 清理。
- **危险品安全兜底**：9782 必填但只挑非危险默认 `get_safe_hazard_default`，不取第一个字典值。
- **竞品数据兜底**：`apply_competitor_fallback` 用竞品重量/尺寸填补 1688 缺失；`ozon_attributes` 竞品俄语属性值优先填充。
- **禁竞品图补位**：AI 生图不足 10 张不用竞品 ir.ozone.ru 图补（整卡 0 图下架根因）。
- **draft_sanity 入队防线**：`worker/src/utils/draft_sanity.py`——weight>50kg/单边>5m 信封 submit 直接 INVALID_REQUEST。

### v0.13/v0.14 新增关键约定（改属性/图搜/CDP 前必看）

- **字典属性绝不手填文本**：Ozon 字典属性只接受列表中的 `dictionary_value_id`，手填 `dictionary_value_id=0 + 文本` → 报「属性值不正确，请从列表中选择」（用途/商品颜色/风格报错来源）。三处（assemble/prepare/retry_loop）统一为「未匹配 → 跳过该属性」，由 `/values/search` 修正或补默认字典值。
- **自由文本属性翻译失败跳过**：含中文值的自由文本属性（颜色名称等）LLM 翻译失败/仍含中文 → 跳过该属性，绝不回退中文或写空值上传（否则报「请用俄文填写该字段」）。
- **可选字典属性不盲补**：仅当字典**唯一值**时才补；多值且无 1688 匹配 → 跳过（避免填语义错误值）。
- **品牌 85/5076 强制保留 dict_id**：`"Нет бренда"(126745801)` 在 prepare 层强制标记为字典属性，防止因 schema 缺失被当自由文本归零。
- **定价失败阻断**：pricing 异常返回 `[PRICING_FAILED]` 标记，graph 路由阻断，**不再 ¥1000 兜底上架**。
- **竞品价字段**：skill 抓取 Ozon 竞品售价 → `draft.competitor_price`（独立字段，勿用 `draft.price`——那是 1688 CNY 采购价）。worker `follow_sell_import_node` 优先读 `competitor_price`。
- **quantity 变体定价**：数量拆分 SKU 用 `pricing_info.variant_prices[i]`（含利润/佣金/物流），**绝不直接用 1688 采购价当售价**。
- **图搜相关性护栏**：follow 图搜结果经 `_pick_best_match`（ozon_discovery.py）筛选——badge「符合0/N」跳过、RU→ZH 标题重叠打分、badge 轻微匹配(<0.5)但标题相关性弱(conf<0.3)拒绝。拒绝时 `no_relevant_match=true` 不组装信封。改图搜代码勿绕过此护栏。
- **图搜弹窗**：1688 图搜 `window.open` 已被 Chrome `--disable-popup-blocking`（chrome_launcher 启动参数）+ JS 层 `window.open` 覆盖（image_search）双保险解决，无需手动放行站点。
- **CDP 统一走 cdp_client.py**：E4 后 4 处裸 websocket/CDP 已统一封装。新代码必须用 `CdpConnection`/`CdpTab`，勿手写 `websocket.create_connection`。复用用户已有 tab 时用 `conn.release(tab)` + `tab.close(close_remote=False)`，否则会误关用户浏览器标签页。
- **生图提示词为中文版**（v0.13 回退）：main/scene/comparison/detail/social/white_bg/multi_angle 均用中文 inline prompt（v2 英文版出图质量问题已回退）。调提示词时勿改回英文版。
- **Skill 验证环境**：本机可用 `/Volumes/OS/opt/homebrew/bin/python3.14` + `skill/.venv314`（已装 requests/websocket-client/Pillow）；`check`/`follow` 真实冒烟需 Chrome CDP 9222 运行且 1688/Ozon 已登录。
- **COS 只随 release 分发**：skill 包 COS 上传仅在 `skill-distribute.yml`（release published 触发）；tag push 时 build-skill 编译需 20-30 分钟，distribute 会轮询等待包就位（竞态已修）。日常 CI（push/PR）不发 COS。

## 已知坑

- **进度已持久化**（v0.9）：`_task_progress` 同时写内存和 PG `progress` 列，重启后从 PG 恢复。`task_processor.py` 注入 `task_id` 到 payload 修复了 key="unknown" 的问题。
- **deepseek-v4-flash reasoning tokens**：该模型默认启用推理，`reasoning_tokens` 消耗 `max_tokens` 配额。翻译/生图 prompt 的 `max_tokens` 至少设为 200，否则输出为空。
- **DESCRIPTION_DECLINE 多重根因**：① 产品名含拉丁/中文 → validate 阻断 ② 属性值含中文 → 俄语类目树 ID 映射 ③ 图片含文字/URL → warning 不阻断 ④ 类目不匹配 → 一致性检查。均已在 v0.14+ 修复。
- **LLM 类目匹配**：v0.5.0 起主路径不用 LLM 选类目（pg_trgm + jieba 末级词），但低置信度时 `_llm_rank_categories` 作为 fallback（v0.34 修复 max_tokens 后可用）——LLM 输出 candidate_index 或 suggest_keywords 二次搜索。类目一致性检查保留但不阻断上传（保留原 category ID 让 Ozon 验证）。
- **物流费率表为空导致价格虚高**：兜底费率 `weight * 0.15 CNY` 是实际费率的 3-4 倍。部署时必须确保 `import_logistics.py` 先于 worker 执行（Dockerfile 已加 openpyxl，deploy.sh 自动跑）。
- **Chrome 重启后 probe 偶发失败**：Skill 的 `probe_1688_page` 在 Chrome 崩溃后自动重启时，内部的 `_resolve_browser_session` 二次调用可能导致 session 状态不一致。直接使用 `CdpTab` + `_single_pass_probe` 可绕过。受影响命令：`graph`/`follow`（偶发），不影响 Worker。
- **属性ID细节**：
  - 9782（危险品等级）：字典属性，某些类目必填，不能跳过（只挑非危险默认值）
  - 22508（品牌注册国）：自由文本属性，需硬编码为"Китай"
  - 23487（制造商）：自由文本属性，用 `draft.supplier` 填充
  - 23536（标记码）：Ozon 自动设置，必须跳过
- **`init_data.py` `walk` 函数**：`description_category_id` 需从父节点继承，`disabled` 字段 NOT NULL 需填 `false`。中文树是 `{"result":[...]}` dict，俄语树是 `[...]` 直接 list，walk 调用需兼容两种格式。

> 完整记录见 `CHANGELOG.md`（commit ad1164c/8041b3d/b78fe64/8231639/93ddd1a，2026-08-03）。v0.9 深度审计的多数问题已在 v0.14 修复（✅已修），下方「关键约定」节保留决策；未修项低优先级，详见 CHANGELOG v0.9 段。

## CDP 稳定性注意事项

CDP（Chrome DevTools Protocol）是 Skill 的核心数据通道。全部通过 `cdp_client.py` 的 `CdpConnection`/`CdpTab` 操作。改 CDP 相关代码时注意：

- **Tab 泄漏**：CDP 打开的 tab 必须在 finally 中关闭（`GET /json/close/{tabId}`）。`ozon_scraper.py` 和 `ozon_image_search.py` 已修复，新代码必须遵循。
- **消息 ID 碰撞**：CDP WebSocket 是共享通道，`Runtime.evaluate` 的 `id` 必须全局唯一。`CdpTab` 用 `itertools.count()` 原子计数器。
- **导航等待**：用 `Page.loadEventFired` 事件驱动（`CdpTab.navigate()` 已封装），不要 `time.sleep()` 硬等。
- **致命断连检测**：`CdpTab` 检测 `Target closed`/`Browser closed` 等异常时应立即退出轮询。
- **进程 kill 等待**：Chrome 多 tab 时 SIGTERM 可能需要 5-10s，用轮询 + SIGKILL 回退（`chrome_launcher.py` 已实现）。
- **验证码暂停**：1688 滑块验证时 Skill 自动暂停，提示用户在浏览器中滑动后按 Enter 继续。
- **连接复用**：`fetch_product_info`/`fetch_competing_sellers` 支持可选 `cdp` 参数复用连接，避免 N*2 冗余连接。
- **裸 CDP 已统一封装（v0.14 E4）**：手写 websocket/CDP 的 4 处已改为 `cdp_client`（ozon_scraper/cli.py check/batch_test/ozon_image_search）。新代码必须用 `CdpConnection`/`CdpTab`。复用用户已有 tab 时先 `conn.release(tab)` 再 `tab.close(close_remote=False)`，否则 `conn.close()` 会误关用户浏览器标签页。图搜/登录弹窗已被 `--disable-popup-blocking` + JS `window.open` 覆盖解决。

## Windows 兼容性

Skill 已适配 Windows，但有以下注意事项：

- **进程扫描**：用 `_list_browser_commands()` 辅助函数（`service.py`），Windows 用 `wmic`，macOS/Linux 用 `ps -axo`。不要直接调 `ps`。
- **进程启动**：Windows 不支持 `start_new_session=True`，用 `creationflags=CREATE_NEW_PROCESS_GROUP`。
- **路径提取**：用 `Path(p).name` 或 `os.path.basename(p)`，不要 `.split('/')`。
- **文件锁**：`os.replace()` 在 Windows 上可能因文件锁失败，需重试。
- **headless 检测**：Windows 通过 `SESSIONNAME` 环境变量判断（无则为服务/CI 环境）。
- **wmic 废弃**：`wmic` 在 Windows 10 21H1+ 已废弃但仍在工作，未来可迁移到 `Get-CimInstance`。
- **编译产物**：Windows 需 `.pyd` 文件（`win32` 或 `win_amd64`），在 Windows 机器上运行 `python3.12 compile.py` 生成。

## Skill dist 分发

`compile.py` 生成自包含的 `skill/dist/` 目录：

- `scripts/lib/_native/{platform}/` — 编译后的二进制（darwin-arm64、win32、linux）
- `scripts/lib/*.py` — 编译模块的自动加载 stub（检测平台 → 加载对应二进制）
- `scripts/capabilities/browser_probe/stealth.py` — stub 位于原始目录（非 lib/），指向 `../../lib/_native/`
- `scripts/lib/ozon_widget.py` — Ozon Widget API（明文 AUX 复制）
- `scripts/lib/utils.py` / `cache.py` / `updater.py` / `task_paths.py` / `logging_utils.py` / `review_log.py` — 明文 AUX 复制
- `data/config/settings.json` / `stores.json` — **空模板**（编译时自动生成，不泄露凭证）

跨平台分发流程：在 macOS/Windows/Linux 各跑一次 `python3.12 compile.py`，合并 `_native/` 目录后打包。

## CI/CD

GitHub Actions 自动检查每次 push/PR（`ci.yml`）：
- **repo-hygiene**: 禁止跟踪运行时/构建产物（skill/data/browser 等）
- **Syntax**: 全量 .py 语法检查（阻断）
- **secret-scan**: gitleaks（v3，内部按 push/pull_request 范围扫描——勿加回 log-opts input，v3 不支持）
- **Quality**: ruff（worker src/ 全量；skill scripts/ --select F）
- **Import**: Worker + Skill 核心模块导入验证（阻断）
- **test-worker**: ubuntu + postgres:16 service + pytest 全量
- **test-skill**: **Docker python:3.12-slim 容器跑 pytest**（v0.36 起——ubuntu 预装 Chrome 测不出无浏览器场景；cp312 ABI 与发布二进制一致）
- **docker-build**: worker/Dockerfile 构建（gha 缓存）
- **CD**（cd.yml）: `git tag v*` → Docker build → push ghcr.io → GitHub Release → COS 部署包（服务器 `cos-update.sh` 用）
- **Skill 构建**（build-skill.yml）: `git tag v*` → 4 平台编译（darwin-arm64/x86_64/linux/win32）→ 合并 32 二进制 → 完整性校验 → frontmatter 校验 → 上传 COS
  （`/skill/<包>.tar.gz` + `/manifest.json`）→ 用户每次命令静默检查，`skill update`
  应用（sha256 校验 + 备份 + 保留 data/）。需配置 GitHub Secrets：
  `COS_SECRET_ID/COS_SECRET_KEY/COS_BUCKET/COS_REGION/COS_MANIFEST_BASE_URL`。
- ⚠️ **build-skill 冒烟导入（v0.36）**: 每平台编译后 Python 3.12 实际 import 编译模块（防 .so 存在但 import 崩；v0.37 P6 起 14 个）。**darwin-x86_64 跳过冒烟**——macos-latest 已切 Apple Silicon，x86_64 .so 无法 dlopen（基础设施限制，静态校验覆盖）。

本地: `bash scripts/ci.sh [--quick] [--strict]`（Step 5b 会用 skill/.venv314 跑 skill pytest）
Pre-commit: `git config core.hooksPath .githooks`（语法 + 密钥拦截）

⚠️ **密钥轮换**: MXOU_TOKEN、1688 AK、Ozon API Key 曾暴露在 git 历史中，已移除追踪但历史仍存在，请尽快轮换。
