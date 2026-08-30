# Changelog

## [0.62.0] - 2026-08-31

> Sentry 六类根因修复（R1–R6）：余额治理 / 字典分页真 bug / 属性缺失联动 / 生图内容违规 /
> 拉丁字母误报 / Sentry 噪音。针对生产 Sentry 100 issues / 3645 events（集中在 2026-08-11 堆积）。

### Fix(worker) — 余额治理（R1）

- `_is_out_of_quota_response` 401 纳入永久错误分类（MXOU 余额耗尽/认证失效同返 401，
  此前走普通 4xx 重试 → Sentry 259× mxou chat 401 噪音 + 级联翻译/属性失败）。
- 低余额用户通知：`BALANCE_ALERT_THRESHOLD`（默认 ¥50，可配）触发后按 token 指纹 30min
  去重，fire-and-forget POST 到 `TASK_NOTIFY_URL`（与任务终态通知同通道）+ Sentry/日志留痕。
- `_check_balance_cached` 缓存升级为 token 指纹绑定（多用户不互相污染），
  main.py `_check_mxou_balance` 复用该缓存，auth/verify 不再高频打余额接口。

### Fix(worker) — 字典值补查分页（R2，真 bug）

- `fetch_ru_dict_value` 增加分页循环（最多 5 页，`has_next` 驱动）：此前单次 POST
  limit=5000 无翻页，大字典（8229 类型等）目标 id 不在首页 → 返回 fallback → 空值 →
  「无法获取字典值，跳过」→ 必填属性缺失（Sentry 129× 品牌 / 90× 类型）。

### Fix(worker) — 属性缺失联动（R3）

- 23487（Производитель/制造商）supplier 缺失 → 安全兜底 `Нет бренда`（同品牌纪律），
  prepare/assemble/retry 三处一致；此前 supplier 空则跳过 → 必填缺失（Sentry 31×）。
- 5379（保质期）维持宁缺毋滥（无安全默认不盲填，交 retry 收敛）；一致性测试锁定三处接线。

### Fix(worker) — 生图内容违规分类（R4）

- 新增 `MxouContentViolationError`：grsai 返回 violation 或 error 含内容违规关键词
  （content/violation/sensitive/adult/违规/敏感/成人等）→ 直接抛异常，不重试不降级
  （防重复烧额度，Sentry 311× grsai failed）；普通 failed 保留有界重试，日志降为 warning。
- 8 个生图节点（main/detail/social_proof/scene/comparison/multi_angle/white_bg/variant）
  对违规异常 re-raise → 任务明确失败「图片内容违规，请调整商品图片/标题」。

### Fix(worker) — 描述拉丁误报（R5）

- `_sanitize_description` / `_sanitize_rich_description` 尺寸乘号归一化
  （`10x10x5` → `10×10×5`，单拉丁 x 不再残留）+ 残留单字母拉丁清理（保护西里尔词边界），
  消除「描述含拉丁字符」误报（Sentry 31×）。

### Fix(worker) — Sentry 噪音治理（R6）

- `task_rerun` 仅当 error_message 含 STALE_RUNNING/ZOMBIE/超时（僵尸/超时恢复）才上报
  warning；正常业务失败重试不再刷屏。stale_running 全零不报守卫保留 + 测试锁定。

### Ops

- `deploy/docker-compose.test.yml` 挂载 webui 源码（`test_webui_contract` 容器内扫描
  不再为空）。

### 回归

- worker Docker 全量 **1502 passed**（原 1454 + 47 新增 + 1 拆分）；新增 6 个测试文件：
  test_balance_alert_401 / test_fetch_ru_dict_value_pagination / test_attr_defaults_23487 /
  test_image_content_violation / test_sanitize_latin_normalize / test_sentry_noise。

## [0.61.0] - 2026-08-30

> store-sync-ERP v1 全量落地(PRD-store-sync-erp-v1.md M0-M6)+ webui 演示数据清零,
> 上生产前准备。工作树提交 `91c0cde7` + `b070a8a7` + `e53121b3`。

### Feat(worker) — 店铺同步引擎(M0-M1)

- `store_sync_jobs` 任务化调度:5s 扫描 + worker 池(3)+ SKIP LOCKED + advisory lock + 退避/stale/超时,
  `STORE_SYNC_JOBS_ENABLED=0` 回退旧循环;绑定即初始化(validate → initial job → 5s 兜底)。
- 只读缓存:orders/products/stats 去懒同步,`?refresh=1` 异步入队,空缓存返回 never。
- 订单续传:窗口 + cursor + incomplete,大店首拉完整;商品域扩列(三价/is_archived/errors/status)。
- M0 探针:真实店映射冻结于 `docs/ozon-field-map-v1.md`(info/list 顶层 items、warehouse v2、price 字符串)。

### Feat(worker) — 身份/数据域/成本(M2-M3)

- `tenant_service.resolve_tenant`(Supabase tokens→user_id,LRU,fail-closed;未配置回退 key 哈希)+
  `migrate_key_tenant_to_user.py` 迁移工具(dry-run/apply/幂等/孤儿报告)。
- 五域落盘:returns/analytics_daily/rating/warehouse/促销真值 + 对应读端点;
  `product_costs`(manual>envelope>discovery 优先级)/`source_candidates`(skill/discover/envelope 三来源)/
  `fx_rates`/`order_line_costs` + real_profit 计算与成本变更回填;日聚合 + 保留清理。
- 主密钥版本前缀 v1: + 双版本解密 + `rotate_master_key.py`;admin sync-health + Sentry 告警。

### Feat(worker/webui) — 采集箱/任务中心/商品编辑

- 采集箱:提交幂等(唯一部分索引)/resubmit/batch-submit/定时上架(scheduled_listings)/CSV 导入导出/
  图片镜像(COS 异步,version 守卫 R8,`image_mirror_state`)。
- 任务中心真实进度:`task_progress_events` + percent 权重 + SSE 增量回放 + 时间线。
- webui 演示清零:工作台(`GET /dashboard/overview`)、系统设置(`user_settings` + 物流费率管理)、
  商品编辑真实保存+更新上架(update_product_id)、货源工作台(未匹配筛选 + 候选展示)、
  选品广场真实筛选+加入采集、站点管理 CRUD、Admin 生图配置、登录余额卡;
  删除未路由的 Listing/Templates()/Pricing()/Admin()/DataTable 演示组件。

### Ops / CI

- `deploy/docker-compose.test.yml` + `scripts/test-docker.sh`、`backup-pg.sh`、
  deploy.sh dist 校验 + migrate 接线、硬删除入口(`ADMIN_HARD_DELETE_ENABLED`,默认关闭)、
  `.env.example` 补齐 COS/STORE_SYNC_JOBS_ENABLED。
- CI:ci.yml 新增 webui tsc+build 门禁;`test_webui_contract.py` 接线契约(webui→worker 路由全量比对);
  `.gitleaks.toml` 放行测试夹具假主密钥(extend useDefault 保留默认规则)。

### 回归

- worker 1454 passed / skill 607 passed / webui `tsc -b` + `bun run build` 全绿。

## [0.60.0] - 2026-08-21

> 三档双价格体系（日常价/划线价/促销底线）+ 变动成本率入定价公式 + 销售净利率口径 + SEO 流量词注入标题与标签 + 标题公式共享模块 + 流量词读端点。用户拍板：定价预留 50%+ 利润空间、促销后仍有利润、Ozon 自动调价不跌破成本线。

### Feat(worker) — 定价（Q1）

- **`compute_price` 三档双价格**（`utils/pricing_estimate.py`）：新增 keyword-only `margin_anchor`(划线原价 2.0)/`margin_floor`(促销底线 0.6)/`variable_cost_rate`(日常变动成本 0.155)/`promo_variable_cost_rate`(促销变动成本 0.245)。三档：`price`=日常价、`old_price`=划线原价（强制 ≥ 日常×1.2）、`promo_price`=促销底线（用促销变动成本率分母）。**全缺省 → 旧行为逐字保持**（向后兼容）。
- **变动成本率入分母**：售价 = 总成本 × (1+margin) / (1 - commission - variable_cost_rate)——用户指出佣金与推广/退货/提现/汇损/附加同属性都从售价按比例扣，只扣佣金导致利润虚高 20-30%。
- **利润口径改销售净利率**：`profit = price×(1-comm-vcr) - cost`、`profit_rate = profit/price`（不再用成本利润率「毛利当净利」）。演算：成本 ¥100/佣金 12% → 日常 ¥345 / 划线 ¥414 / 促销 ¥252（7.3 折），日常净利率 43.5%、促销 23.8%——促销后仍保利润且可接 Ozon 自动促销（promo_price 供 min_seller_price）。
- **`pricing_node` 三档接线**：读 extensions 新参（向后兼容门：显式 margin_rate 且无 floor/anchor → 单档）；变体循环从内联公式改调 `compute_price`（消除 ×1.15 vs ×1.2 漂移），`variant_prices` 加 `promo_price`。
- **estimate 端点三档**：`estimate_from_envelope` + `estimate_routes` 支持 margin_anchor/margin_floor/variable_cost_rate/promo_variable_cost_rate 覆盖键，响应带三档价。
- 配置链路：`margin_floor/margin_anchor/variable_cost_rate/promo_variable_cost_rate/traffic_keywords` 穿透三端白名单（template_service CONFIG_KEYS + schemas ListingTemplateConfig + skill config_store/cloud_probe _merge_config_tiers）。
- 测试：`test_pricing_dual_margin.py`(6) + `test_pricing_node_dual_margin.py`(3) + estimate 三档(3) + template 新键(6)；修复 test_estimate_endpoint 存量 auth mock（v0.56 派生租户 404）。

### Feat(worker) — 标题 SEO + 标签（Q5）

- **`utils/title_formula.py` 共享模块**（T1）：`build_title_formula_prompt(lang, traffic_keywords)`「核心词+属性+场景」公式 + 流量词建议行；`parse_title_formula_keywords`（纯西里尔过滤/去重/≤3）。**统一 4 份公式拷贝**（prepare 主路径/兜底/内部 fallback + ai_field_service 草稿 AI 重生成）——新增标题公式逻辑必须进本模块（v0.40 共享层纪律）。
- **traffic_keywords 注入标题**：`extensions.traffic_keywords`（what-to-sell all-queries 流量词）→ prepare 标题生成 prompt 建议行（LLM 自主融入，不硬塞）；仍过 `sanitize_title` + 中文/拉丁零容忍。
- **hashtag 23171 消费流量词**（T8）：`_generate_hashtags(name, traffic_keywords=None)` 优先级 = 流量词(parse 过滤→`#kw`) > `_HASHTAG_RU` 字典 > 西里尔提取 > `#товар`；品牌过滤复用 `filter_brand_from_hashtags`。修掉中文标题恒 `#товар` 的标签质量问题。
- **`GET /api/v1/seo/keywords?q=&limit=`**（T5）：公开读端点（Bearer + 限流），读 `blue_ocean_queries` 按流量（uniq_queries_wca DESC）排，`queries_service.search_public`——标题/tag 流量词的数据源。
- 测试：`test_title_formula.py`(6) + `test_title_formula_wiring.py`(10) + `test_hashtag_keywords.py`(6) + `test_seo_keywords_endpoint.py`(6)。

### Docs

- `docs/PLAN-conversation-entry-v1.md`（Q3 对话入口：推荐 dsh Agent tab 先行 + 专家 tab 对话 UI 二期，路由层把 SKILL.md 决策树固化为 LLM prompt）。
- `docs/PLAN-card-merge-fix-v1.md`（Q4 并卡修复：9048 前缀 `{item_id}~{sha1[:8]}` + 标题 SEO + 生图回退告警 + 上架前检测）。
- Q2 促销调研：seller-actions API 实测可用（`/v1/seller-actions/list` 返回空），促销价受 `min_seller_price`/`min_action_percent` 约束——三档定价已为其预留空间；实现待定价落地后规划。

### ⚠️ 行为变化

- 默认定价档位从 margin 0.25 单档提升为 1.5(日常)/2.0(划线)/0.6(促销) 三档 + 变动成本率——**新上架商品价格将显著高于旧版**（成本 ¥100 → 日常 ¥345）。已有商品卡不受影响（定价只在上架时计算）。skill 端 `price_estimate` 硬编码系数 1.44375 未改（展示层，已知漂移，后续对齐）。

### Test

- worker 核心套件 76+ passed（pricing/title/hashtag/seo/template 全绿）；skill 19 passed（envelope/template）。
- 全量 48 存量失败（auth/credentials/shelf/site 等环境相关）经 git stash 基线对比确认非本改动引入。

## [0.56.5] - 2026-08-17

> webui 自动创建 key 设 `unlimited_quota=true`——key 无限额度，实际消费仍走用户真实余额（MXOU 平台优先 / users.quota 兜底）。

### Fix(worker)

- **`_upsert_supabase_token` 写 `unlimited_quota=true`**：webui 登录/建 key 时 upsert 的 token 在 Supabase 兜底分支不再被误拒。消费语义不变——`_check_mxou_balance`/`auth_node` 优先查 MXOU 平台真实余额（欠费必拒，`unlimited_quota` 仅作 Supabase 兜底分支放行标记），符合「key 无限额度但实际扣用户真实余额」。
- 测试：`test_mxou_login_api` 3 处断言补 `unlimited_quota=true`；worker 1209 passed。

## [0.56.4] - 2026-08-17

> chat 通道余额不足 fast-fail——Sentry 591 次 `insufficient_user_quota` 根因修复（v0.56 W12 只修了 image 通道漏了 chat）。

### Fix(worker)

- **`call_mxou_chat_api` 入口加余额 pre-check**：复用 `_check_balance_cached`（30s TTL），余额 < MIN_BALANCE_THRESHOLD → 抛 `MxouOutOfQuotaError`（零 POST）——与 image 通道对齐。
- **chat 403/insufficient_user_quota 不再静默 return None**：原当普通 4xx 直接返回 None（级联属性空/翻译失败等误导性错误），现识别 OUT_OF_QUOTA 直接抛。
- **prepare 翻译函数 `MxouOutOfQuotaError` re-raise**：不吞成原文回退（否则中文标题上架被 Ozon 拒）。
- **mxou_llm 保持纯 re-export**：异常天然冒泡 → graph → task fail `error_message=OUT_OF_QUOTA`，用户 query 明确看到「余额不足请充值」。
- 新增 chat 通道 3 单测；worker 1209 passed。

## [0.56.3] - 2026-08-17

> CI Skill Tests (python:3.12 Docker) 失败修复：aibuy 图搜去掉 `_require_auth`——无 MXOU_TOKEN 环境不再抛 AuthError。

### Fix(skill)

- **`search_by_image_aibuy` 去掉 `_require_auth()`**：aibuy 走 Chrome cookie 通道（无需 MXOU_TOKEN），且 fail-fast 纪律（「无 token → 快速返回 [] 由调用方降级」）要求不 raise——auth guard 在 CI 无 token 环境抛 AuthError 破坏契约（3 个 aibuy 测试炸）。CDP 通道 `search_by_image_cdp` 的 `_require_auth` 保留（需登录态验证）。
- 验证：无 MXOU_TOKEN 环境 skill 537 passed 全绿。

## [0.56.2] - 2026-08-17

> CI Worker Tests 失败根因修复：`ozon_product_tasks` 关键列补 `server_default`——CI 干净建表后直接 INSERT 不传默认值不再违反 NOT NULL。

### Fix(worker)

- **priority/status/retry_count/max_retries/timeout_seconds 补 server_default**：原用 SQLAlchemy Python 层 `default`（不生成 DB DEFAULT），CI `init_data.py` 全新建表后这些列是 NOT NULL 无默认 → 测试直接 INSERT 违反约束（`psycopg2 NotNullViolation: null value in column "priority"`）。本地表是旧 schema 碰巧可空，1206 测试未覆盖。改为 `server_default`（status='pending'/priority=0/retry_count=0/max_retries=3/timeout_seconds=1800）。
- 模拟 CI 全流程验证：`init_data.py --force` 建表后 FK 正确 + 不传默认值 INSERT 生效；worker 1206 passed。

## [0.56.1] - 2026-08-17

> 发版质检修复（用户追问发版完整性暴露）：F821 缺 import 真 bug（无默认店铺分支 NameError）+ CI/CD ruff 全量清零 + CD bun 生态适配（webui 部署包首次真正上传 COS）。

### Fix(worker)

- **F821 真 bug**（v0.56 店铺缓存引入）：`orders_routes.py`/`shelf_routes.py` 使用 `HTTPException` 但未 import——触发「未配置默认店铺」分支会 NameError 崩溃（1206 测试 mock 了该分支未覆盖）。补 `HTTPException` import。
- **ruff 全量清零**（CI 最新 ruff 暴露的 pre-existing 12 处）：SIM118（dict.keys()）/FURB162（tz 替换）/SIM103（条件直接返回）/RUF100（未用 noqa）/FLY002（join→f-string）/UP009（utf-8 声明）/B023（闭包捕获循环变量）最小修复，不改变行为。
- **skill F402**（T8/T9 引入）：`_check_ai_preset`/`_passes_base_filter` 循环变量 `field` 遮蔽 `dataclasses.field` import → 改 `fkey`。

### Fix(cd)

- **webui 部署包从未上传 COS 的根因**：webui 是 bun 生态（`bun.lock`，`package-lock.json` 从不存在），CD 的 Setup Node `cache: npm` + `npm ci` 必然失败（`Some specified paths were not resolved`）——v0.55 及更早的 CD 一直失败。改为 `oven-sh/setup-bun` + `bun install` + `bun run build`。

### 验证

- worker ruff 全绿 / skill ruff 全绿 / worker 1206 passed / skill 537 passed / compile.py 14 成功 0 失败。

## [0.56.0] - 2026-08-17

> Skill 学习上品帮 v1（13 Task / 4 Wave）：上架成功率对齐 follow（graph 信封补竞品数据）+ discover 2.5× 加速（18 项 BASE 粗筛砍 80% aibuy 配额）+ 选品跨机归档 + 多店铺开箱即用。skill 537 passed（基线 487 + 50 新）/ worker 1206 passed（基线 1179 + 27 新）。

### Feat(skill) 漏斗加速（S5/B3/S6）

- **列表内联解析**：`_COLLECT_URLS_JS` → `_COLLECT_ROWS_JS` 一次抽 price/oPrice/name/cover/rating/reviewCount/id + 同函数内批量 fetch webSellerList 拿 sellerNumber/followMinPrice（仅取低于当前价的跟卖最低价）；`_lazy_collect_rows` 返回全字段行，`_lazy_collect_urls` 签名不变。
- **粗筛 9→22 字段**：`_SELECTION_FIELDS` 补 sales_dynamics/days_in_promo/discount/promo_revenue_share/days_with_trafarets/session_count/conv_to_cart_pdp/conv_to_cart_search/nullable_redemption_rate/weight_g/dimensions/return_cancel_rate/review_count（getattr 缺省 None 安全）；`_check_rule` 加 None=不限（上品帮 checkRange 语义）。
- **18 项 BASE 粗筛**：`_apply_filters` 前新增粗筛，仅通过项才上 widget + aibuy 配额（80% 配额节省，discover 5min→2min）。
- **`--rules ai` 预设**：上架≤365d/跟卖≤30/销售动态>0/DRR≤15 四条硬淘汰 + 销量阶梯（价≤500₽月销>500、≤1000₽>150、≤5000₽>30、≤10000₽>15、其他>5）。
- **滚动缓动**（S7）：4 处 scrollTo 改 3000ms ease-in-out + 80% 视口（上品帮 scrollPage 反爬节奏），`_lazy_collect_urls` stall timeout 调大防误触底。

### Feat(skill+worker) graph 信封补竞品（S1）

- **混合键结构**（PRD §3.1 定稿）：`extensions.competitor_weight_g` / `extensions.competitor_dimensions_mm` + `draft.ozon_attributes` / `draft.competitor_price` / `draft.follow_min_price`——与 follow 路径 100% 对齐，零 worker 改。
- **Ozon 反查同款**：复用 `discover_from_keyword` + `_llm_semantic_match` + `_ru_zh_title_overlap` 语义匹配 top1 → fetch_competing_sellers + fetch_product_info 取重量/尺寸/俄语属性，全程 fail-open。
- worker 消费链已就绪（weight_dimension_normalizer/apply_competitor_fallback），graph 路径上架成功率对齐 follow。

### Feat(skill+worker) 选品归档（D12/W10）+ 多店铺（D11/W9）

- worker `/api/v1/discovery/runs`：新 ORM DiscoveryRun（tenant_id 隔离）+ Pydantic + POST 上报/GET 读取（复用 analytics 模式）。
- skill `_report_discovery_run`：REPORT_FIELDS 20 字段白名单裁剪（~25KB/run，去 competing_seller_list/match_1688_images 大字段）+ 单次 POST + fail-open 不影响本地落盘。
- skill `get_template_profile`：读 worker `/api/v1/templates` is_default + store_overrides 店铺覆盖；cloud_probe 三段降级（显式 extensions > worker 模板 > 本地 stores.json）；cli graph `--template-id`。
- worker `ListingTemplateOut` 补 `store_overrides` 字段（W9）。

### Feat(skill+worker) 类目缓存端点（W11）+ 并行/通道

- worker `/api/v1/mappings/lookup?keyword=`：复用 lookup_mapping（category_mapping 全局共享，无 tenant 隔离）；skill `lookup_category_webhook` 切新端点 + 老 /webhook/cat-lookup-v1 fallback。
- **discover-multi**（D7'）：`--keywords "A,B,C"` 串行滚动（同 Chrome 多 tab 反爬纪律）+ 合并去重 + 复用 ThreadPoolExecutor 并行分析（总时长 ≈ N×滚动 + 1×分析）。
- **discover --to-box**（D13）：`_submit_one` 分支走 submit_draft 入 webui 采集箱（不直接上架）。

### Fix(worker)

- **W6 graph 直连回填 product_task_index**：`_write_direct_submission_row` 从信封反查 credentials 表注入 credential_id；learning_record_node 先从 task payload 兜底解析，仍无才 skip（draft_id 保持 NULL）。
- **W12 MXOU 余额事中复查**：call_mxou_image_api 入口 `_check_balance_cached`（30s TTL）+ MIN_BALANCE_THRESHOLD=1.0 → 余额不足抛 OUT_OF_QUOTA fast-fail；403/OUT_OF_QUOTA 响应不再普通重试。

### 验证

- skill 537 passed（基线 487 + 50 新：U1-U4 selection/rules/ai/envelope + D12/D11/D7'/D13 各测试）；worker 1206 passed（基线 1179 + 27 新：U5-U8 + W9/W10/W11 测试）。
- curl 验收：G4 discovery/runs POST inserted:1 + GET 租户隔离 + 无效 token 401；G5 mappings/lookup hit/miss 结构正确；G10 skill 上报端到端落库（白名单裁剪生效）。
- 真实上架类门（G2/G6/G9）需用户真实环境（Chrome/登录/Ozon 凭证）——本地已用单测 + curl + 模拟覆盖。

## [0.55.0] - 2026-08-17

> 系统设置（运营配置中心）：站点运营/商业/引擎配置三块落地 + 管理员角色判定修复。架构定调——用户/充值/订阅走 api.mxou.cn 复用 New API（零后端开发），业务数据/系统设置走 worker 本地 PG。worker 1148 passed / webui build 0 错误。

### Fix(管理员角色判定)

- **Supabase 整数 role 兼容**：`is_admin_user` 原比较字符串 'admin'，但 users.role 实际是整数（New API RoleRootUser=100/RoleAdminUser=10）→ 管理员永远 403。新增共享 `is_admin_role`（role>=10 即管理员，兼容整数/字符串），admin_service.py 单一真相源。
- **`_fetch_user_role` 查库修正**：原用 get_engine() 查本地 PG（无 users 表恒返回 'user'），改走 Supabase 客户端，与 is_admin_user 同源。
- **auth_node raw REST 不收敛**（决策）：独立 3 次重试/5xx 降级链是可用性关键，标记 PRD 远期收敛项。
- 新增 9 个 role 单测（整数 100/10/1/0 + 字符串兼容 + None/bool 边界），11 个 role 用例全过。

### Feat(系统设置 A 站点运营)

- 新表 `site_banners` / `site_announcements`（BigInteger Identity PK + timestamptz，Base.metadata.create_all 自动建表，无迁移）。
- `site_service.py`：Banner/通告 CRUD + 公开只读（enabled-only + sort_order asc）。
- `admin_site_routes.py`：/admin/site/* 8 端点（require_admin，模块内 Pydantic，announcement_type 校验 400）+ `site_public_routes.py`：/site/* 2 公开只读。
- 13 个测试：公开只返 enabled / 403 守卫 / CRUD / type 校验。

### Feat(系统设置 C 引擎配置)

- **C1 提示词编辑**：`config_service.py`——13 个 config JSON 读/写/备份（保留 5 份）/回滚，路径穿越防护 + 原子写 + 非法 JSON 拒绝（写前备份，改前可回滚）。
- **C2 运费费率管理**：`logistics_service.py`——logistics_rates 列表/单条更新（weight 区间 + vol_divisor 校验）/CSV 导入（代码层按自然键 upsert，utf-8-sig）。
- **C3 选品库**：`queries_service.py`——blue_ocean_queries 列表/CSV+JSON 导入（ON CONFLICT (query,token_id) + admin_import 保留字 + RETURNING xmax 区分 imported/updated）/删除。
- 3 个 admin 路由 + main.py 注册；30 个测试（config 9 文件系统 / logistics 12 / queries 9）。

### Feat(WebUI 系统设置页)

- **侧边栏死链修复**：/system-settings/site（无路由 404）→ /system-settings。
- `client.ts` +244 行：/api/v1/admin/site|config|logistics|queries 22 个类型化函数。
- `SystemSettings.tsx`：4 内页 tab——站点运营（Banner/通告 CRUD）/ 引擎配置（JSON 编辑+校验+备份回滚）/ 选品库（搜索/分页/CSV 导入/删除）/ 商业（订阅+充值启动卡）。
- 路由 _authenticated/system-settings/（admin guard role<10 → /403）。

### Feat(P3 商业接回，复用 New API)

- 从 ponding-api（只读源）复制回 83 文件：features/subscriptions/（17）+ features/wallet/（30）+ components/data-table/（31）+ system-settings api/types/hook + 2 路由。
- 生产 webui 部署于 api.mxou.cn 域，同源 /api/subscription/*、/api/user/topup* 直达 New API（无需 worker 代理）。
- 修复 3 处 ponding 源自身类型错误（ponding 不跑 tsc）：purchase-dialog 4 未定义变量按既有字段推导 / Select onValueChange 适配 webui 模式。
- formatQuota 已存在无需补；/subscriptions + /wallet 注册 routeTree。

### 验证

- worker 全量 1148 passed（基线 1105 + 43 新）；webui build 0 错误。
- 7 个验收场景：权限 403 / 公开只返 enabled / 提示词非法 JSON 拒绝+备份 / 费率区间校验+CSV / 选品库去重 / role 渲染 / 订阅钱包渲染。

## [0.54.0] - 2026-08-17

> WebUI 架构升级：照搬 mxou（api.mxou.cn / new-api default）主题 + 组件 + 布局 + TanStack Router，视觉与 mxou 完全一致。worker 1094 passed / webui build 0 错误。

### Feat(WebUI 照搬 mxou 架构)

- **整包复制** new-api default：theme.css（oklch 色板）+ 61 个 shadcn 风格 UI 组件 + layout + lib + 品牌资产（favicon/logo/landing）。
- **React 18 → 19.2.6** + @base-ui/react 1.5 + Tailwind 4（`@theme inline` 桥接 tokens）。
- **保留 Vite** 构建链（删 rsbuild/netlify/knip）；bun 装依赖（catalog 版本化）。
- **TanStack Router** 文件路由接入（routeTree 自动生成，basepath=/app）；react-router-dom 业务页经 `router-compat.ts` 兼容层迁移（8 页只改 import）。
- **业务 14 页**迁入 `_authenticated` 路由组；导航 `use-sidebar-data.ts` 重写为业务菜单（工作台/运营/配置/管理员）。
- **登录/注册完全保留 mxou**（New API 用户体系，cookie 会话）；业务 worker 鉴权用同一 Supabase tokens 表（sk-token Bearer）。
- **管理员独立化**：/admin 路由组 + role 守卫（`ROLE.ADMIN=10` 不足跳 /403，已验证 role=1→403 / role=100→进入）；worker `require_admin` 服务端防线保持。
- **worker role 字段**：mxou 登录响应加 `role`（查 users.role，admin_service 同源）。
- **连接横幅** WorkbenchConnectBanner（mxou cookie ≠ worker token，未配置 sk-token 时引导去 API Keys）；keys 创建成功自动连接。

### Docs
- `docs/WEBUI-CONVENTIONS.md`（双体系规范 + 版权出处 AGPL 备注）。

### Test
- webui build 0 错误；playwright 验证 6 业务页渲染 + 管理员守卫；worker 1094 passed（mxou 69 测试含 role）。

---

## [0.53.0] - 2026-08-16

> P2/P3 批量交付：消息催评自动化（P2c）+ 数据大屏（P3）。P1a-d 已并入 v0.52。worker 1088 passed / skill 493 / webui build + tokens:validate 绿。

### Feat(P2c 消息催评自动化)

- **内置 3 模板**（催护照/催取货/索好评，俄语文案 + `[货件编号]`/`[商品名称]` 占位符，对标上品帮 autoMsg/毛子）。
- **`send_order_message`**：`/v1/chat/start`（按订单建聊天，实测契约）→ `/v1/chat/send/message` 两步发送。
- **`order_messages` 表**：发送记录留痕（成功/失败都记，含 chat_id/error）；长度校验（空 422 / 超长截断 1000）。
- **API**：`GET /orders/message-templates` + `POST /orders/{pn}/message` + `GET /orders/messages`。
- **WebUI**：订单行「发消息」弹窗（模板下拉 + 俄语预览编辑 + 发送）；工具栏「消息记录」。

### Feat(P3 数据大屏)

- **`/data-screen` 页**：实时时钟 + 平台概览卡片（任务总数/今日/成功率/店铺/用户）+ 实时订单滚动列表（15s 自动刷新），复用 admin overview + orders 端点（纯前端聚合，无新后端）。

### 说明(P2d AI 套图)

- 现有 ImageStudio 已覆盖对标能力（白底/场景/卖点/对比/多角度/社交 6 类型 + 一键批量生成 + 单张重绘 + 失败分类），无需开发。

### Test
- `tests/test_order_messages.py`（8 用例）：chat/start + send 两步请求体、模板/占位符、空 422/超长截断、记录留痕（成功/失败）、无默认 400。
- 全量回归 worker **1088 passed**（1080 基线 + 8 新）；webui build + tokens:validate。

---

## [0.52.0] - 2026-08-16

> WebUI 在线商品批量操作 P1a（PRD `docs/PRD-product-bulk-v0.52.md`）：店铺商品视图批量改价/改库存/归档/恢复。worker 1062 passed（+7）/ skill 493 / webui build + tokens:validate 绿。

### Feat(在线商品批量操作)

- **`shelf_service` 批量写入**：`bulk_update_prices`（`/v1/product/import/prices`，prices 数组透传）、`bulk_update_stocks`（`/v2/products/stocks`）、`bulk_archive`（archive=true → `/v1/product/archive`，false → `/v1/product/unarchive`，product_id 转 int 过滤非数字）。
- **API**：`POST /products/bulk-prices` + `/products/bulk-stocks` + `/products/bulk-archive`（credential_id 或默认店铺；无默认 400 / Ozon 502）。
- **WebUI 店铺商品视图**：复选框多选（全选/单选）+ 工具栏批量操作（批量改价弹窗：新售价/划线价/最低价；批量改库存弹窗；批量归档/恢复带 confirm 真实生效警告）+ 结果提示 + 刷新。

### Test
- `tests/test_shelf_bulk.py`（7 用例）：prices/stocks 请求体透传、archive true/false 端点切换、非数字 product_id 过滤、无默认 400、Ozon 502。
- 全量回归 worker **1062 passed**（1055 基线 + 7 新）；webui build + tokens:validate。

---

## [0.51.0] - 2026-08-16

> WebUI 管理员面板（PRD `docs/PRD-admin-panel-v0.51.md`）：平台运营视图——用户/店铺/任务跨租户聚合。worker 1055 passed（+8）/ skill 493 / webui build + tokens:validate 绿。

### Feat(管理员面板)

- **`admin_service.py`**：只读聚合（不做写操作）——overview（用户数/店铺数/任务总数/今日/成功率）、用户列表（Supabase users + PG 店铺/任务数 JOIN）、用户详情（店铺列表 + 任务统计）、店铺列表（跨用户）、任务统计（复用 task_processor）。
- **管理员鉴权** `require_admin`：Supabase `users.role='admin'`；本地开发（无 Supabase）`local_dev` 放行；非管理员 403。
- **API** `routes/admin_routes.py`：`GET /admin/overview` + `/admin/users` + `/admin/users/{id}` + `/admin/stores` + `/admin/tasks`（全走管理员鉴权）。
- **WebUI 管理后台** `/admin`：概览卡片（用户/店铺/任务/今日/成功率）+ 用户/店铺/任务统计三 tab + 用户详情弹窗（店铺 + 任务统计）；Layout 菜单新增「管理后台」（非管理员访问 403 提示）。

### Test
- `tests/test_admin_service.py`（8 用例）：管理员判定（admin/user/local_dev/403）、overview 聚合、用户列表 JOIN、用户详情、跨租户店铺列表。
- 全量回归 worker **1055 passed**（1047 基线 + 8 新）；webui build + tokens:validate。

---

## [0.50.0] - 2026-08-16

> 修复「配置店铺后看不到在线商品」+ 在线商品实时拉取（PRD `docs/PRD-ozon-shelf-v0.50.md`）。worker 1047 passed（+6）/ skill 493 / webui build + tokens:validate 绿。

### 修复(订单接口)

- **Ozon FBS 订单 400 bug**（v0.47 引入）：`/v3/posting/fbs/list` 的 `filter.since` 用 isoformat（微秒+偏移）且**缺 `to` 字段** → `processed_at_to must be set`。修复：严格 `YYYY-MM-DDTHH:MM:SSZ` 格式 + 必须同时含 since/to。**真实订单已拉到 50 条**（此前接口 502）。
- 本地凭证解密失败：dev 环境 worker 用随机 master key 导致 credentials 密文无法解密——改用 deploy/.env 固定 key。

### Feat(在线商品实时拉取)

- **`shelf_service.list_ozon_products`**：`GET /api/v1/products/ozon`——两步拼接 `/v3/product/list`（245 个商品）→ `/v3/product/info/list`（名称/图片/价格/库存/货币）——**覆盖 Ozon 店铺全部在线商品**（含手动上架/其他工具，此前 OnSale 只显示本系统上架）。
- 踩坑修复（实测发现）：
  - `/v3/product/info/list` 批量查询**必须传整数数组**（字符串返回空 items）
  - Ozon 对 info/list 有**速率限制**：高频下静默返回空 items（不报错）→ **退避重试 3 次**（1s/2s）
  - info 响应结构：price 顶层字符串、stocks.stocks[0].present（非 v1 嵌套）
  - info 失败降级返回列表（不阻断）
- **WebUI OnSale 双视图**：`order-tabs` 切换「本系统上架」（product_task_index，含编辑入口）/「店铺商品」（实时拉取 + 店铺下拉 + 商品图/售价/库存）。

### Test
- `tests/test_shelf_ozon.py`（6 用例）：两步拼接 + 字段提取（v3 结构）+ 无默认店铺 400 + list 502 + info 降级 + 显式 credential。
- 全量回归 worker **1047 passed**（1041 基线 + 6 新）；webui build + tokens:validate。

---

## [0.49.0] - 2026-08-16

> WebUI 订单写入操作 P1-2（PRD `docs/PRD-order-actions-v0.49.md`）：备货发货 + 取消订单（真实影响操作）。worker 1041 passed（+8）/ skill 493 / webui build + tokens:validate 绿。

### Feat(订单写入操作)

- **备货发货** `order_service.ship_order`：`POST /v4/posting/fbs/ship`（packages/posting_number/packages_count=1）→ 对标上品帮批量备货。
- **取消原因** `list_cancel_reasons`：`POST /v1/posting/fbs/cancel-reason` → [{id, title}]。
- **取消订单** `cancel_order`：`POST /v2/posting/fbs/cancel`（cancel_reason_id）→ 对标毛子取消货件选原因。
- **统一凭证解析** `_resolve_credential`（credential_id 或默认店铺，无默认 400）+ `_ozon_action` 包装（失败 502）。
- **路由**：`POST /orders/{pn}/ship` + `GET /orders/{pn}/cancel-reasons` + `POST /orders/{pn}/cancel`。
- **WebUI**：待备货/待发运 tab 行操作「备货发货」（confirm 真实生效警告）；所有行「取消」→ 弹窗（拉取原因 → 下拉选择 → 确认，红色危险按钮）；操作结果 notice 提示 + 刷新。

### Test
- `tests/test_order_actions.py`（8 用例）：ship 请求体断言/成功/无默认 400/Ozon 502、cancel-reasons 成功/502、cancel 请求体/502、显式 credential 优先。
- 全量回归 worker **1041 passed**（1033 基线 + 8 新）；webui build + tokens:validate。

---

## [0.48.0] - 2026-08-16

> WebUI 订单操作 P1-1（PRD `docs/PRD-order-notes-v0.48.md`）：订单货源/采购信息标注（本地元数据）+ 面单 PDF 下载。worker 1033 passed（+10）/ skill 493 / webui build + tokens:validate 绿。

### Feat(订单操作)

- **`order_notes` 表**（`OrderNote`）：货源地址/货源价格/货源备注 + 采购单号/采购快递/采购单号 6 字段，posting_number 主键 + 租户隔离（本地元数据，不对 Ozon 写入）。
- **notes API**：`GET/PUT /api/v1/orders/{posting_number}/notes`——upsert（ON CONFLICT）+ 无记录返回空模板（先标注后同步订单也允许）；租户隔离（B 读 A 的 → 空模板）。
- **面单代理**：`GET /api/v1/orders/{posting_number}/label` → `/v2/posting/fbs/package-label` → PDF base64；无默认店铺 400 / Ozon 失败 502 / 无 PDF 502。
- **WebUI 订单页**：行操作「备注」弹窗（货源 + 采购 6 字段编辑保存）+「下载面单」；列表「备注」列（已标注 → 蓝色 badge 带货源链接 title，否则 —）；保存后本地缓存即时标记。

### Test
- `tests/test_order_notes.py`（10 用例）：upsert/get 幂等、租户隔离、空模板、source_cost 422、label 成功（endpoint/body 断言）/默认凭证/无默认 400/Ozon 错误 502/空 PDF 502。
- 全量回归 worker **1033 passed**（1023 基线 + 10 新）；webui build + tokens:validate。

---

## [0.47.0] - 2026-08-16

> WebUI 订单管理（P0-4，PRD `docs/PRD-orders-v0.47.md`）：Ozon FBS 订单实时拉取 + 状态映射 + 订单页（状态 tab / 表格 / 详情 / CSV 导出）。P0 四项全部落地。worker 1023 passed（+7）/ skill 493 / webui build + tokens:validate 绿。

### Feat(订单管理)

- **`order_service.py`**：`/v3/posting/fbs/list` 实时拉取（不建表）→ 标准化——状态映射（Ozon raw → 统一 7 态：待处理/待备货/待发运/运输中/已签收/已取消/其他，15 个 raw 枚举全映射）+ products/金额/平台费用/估算利润/仓库/配送/取消原因提取。
- **`GET /api/v1/orders`**（`orders_routes.py`）：credential_id（或默认店铺）+ status 筛选 + limit/offset/since_days；凭证归属校验（跨租户 404）；无默认店铺 → 400；Ozon API 失败 → 502。
- **WebUI 订单页** `/orders`（`Orders.tsx`）：店铺下拉（默认店铺默认选中）+ 8 状态 tab（含计数）+ 表格（货件编号/状态/商品信息/金额/费用/利润/仓库配送/时间）+ 详情弹窗（全部商品明细 + 取消原因/取消方）+ CSV 导出（UTF-8 BOM）。
- Layout 菜单新增「订单管理」（在售货架后）。

### Test
- `tests/test_order_service.py`（7 用例）：状态映射全枚举（15 raw）、products/financial/warehouse 标准化提取、取消原因提取、status 筛选透传、无默认店铺 400、Ozon API 失败 502、跨租户凭证 404。
- 全量回归 worker **1023 passed**（1016 基线 + 7 新）；webui build + tokens:validate（新增 order-tabs/order-detail 样式走 design token）。

---

## [0.46.0] - 2026-08-16

> WebUI 上架记录增强（P0-2，PRD `docs/PRD-task-record-v0.46.md`）：上架方式细分（重上/编辑更新/跟卖/选品）+ CSV 导出当前筛选结果。worker 1016 passed（+8）/ skill 493 / webui build + tokens:validate 绿。

### Feat(上架记录增强)

- **上架方式细分**（task_service `_payload_meta` + `TaskListItem`）：新增 `update_mode`（`extensions.update_product_id` → 在线商品编辑更新）和 `parent_task_id`（resubmit 注入 → 重上来源标记）两个字段。
- **前端三态推导**（Tasks.tsx `listingMode`）：优先级 重上 > 编辑更新 > 跟卖 > 选品——上架方式列从「跟卖/选品」二分升级为四态 badge（重上/编辑更新 蓝色、跟卖 绿色、选品 灰色）；重上项 title 提示来源任务。
- **CSV 导出**：工具栏「导出 CSV」按钮导出**当前筛选结果**（11 列：商品标题/货号/店铺/账号/上架状态/售价/划线价/利润率/货源链接/上架方式/创建时间），UTF-8 BOM 兼容 Excel 中文不乱码，文件名 `上架记录-YYYY-MM-DD.csv`。
- 现有批量重上/状态筛选/货源链接/5s 轮询行为不变。

### Test
- `tests/test_task_service_meta.py`（8 用例）：update_mode 提取（true/false）、parent_task_id（有/无）、敏感字段防护（token/api_key 绝不出现在 meta）、既有字段不回归、malformed payload 兜底。
- 全量回归 worker **1016 passed**（1008 基线 + 8 新）；webui build + tokens:validate。

---

## [0.45.0] - 2026-08-16

> WebUI 采集箱批量上架（P0-3，PRD `docs/PRD-batch-submit-v0.45.md`）：勾选多个草稿 → 统一选店铺/上架配置模板 → 逐条提交（失败隔离）。前端改动，Worker 零改动——完全复用 v0.44 模板注入 + submit_draft 已验证逻辑。worker 1008 / skill 493 / webui build + tokens:validate 绿。

### Feat(采集箱批量上架)

- **采集箱工具栏「批量上架」按钮**（勾选 ≥1 草稿后可用，`pages/CollectBox.tsx`）。
- **`BatchSubmitModal` 组件**：选目标店铺（默认店铺默认选中）+ 选上架配置模板（默认模板默认选中，复用 v0.44）+ 汇总（草稿数/采购总成本）。
- **逐条提交循环**：`submitDraft(draftId, credentialId, templateId)` 逐个调用——**失败隔离**：单条 409（目标店铺已存在）/其他错误不中断其余，记录原因。
- **结果视图**：成功/失败分组——成功项 task_id 可跳 `/tasks?task_id=`；失败项显示草稿标题 + 原因 + 「去编辑」链接 `/products/{id}`。
- 提交完成后自动清空勾选并刷新采集箱（新提交状态列更新）。
- 弹窗店铺/模板懒加载（`listCredentials` + `listTemplates`，失败降级为空下拉不阻断提交）。

### Test
- webui build + tokens:validate 绿；dev server 冒烟 200。
- Worker 零改动（复用 submitDraft 1008 测试覆盖），无新增 worker 测试。

---

## [0.44.0] - 2026-08-16

> WebUI 上架配置模板（P0-1，对标上品帮 UpGoodsSetting，PRD `docs/PRD-listing-template-v0.44.md`）：可复用的上架策略模板（定价/货号前缀/跟卖/库存），提交草稿时一键套用。worker 1008 passed（+32 模板用例）/ skill 493 passed / 前端 build + tokens:validate 绿。

### Feat(上架配置模板)

- **`listing_templates` 表**（`shared/model.py` `ListingTemplate`）：租户隔离 + 每租户最多一个 `is_default`（部分唯一索引，设默认先清旧默认）。
- **`template_service.py`**：CRUD + 设默认 + `apply_template_to_envelope` 注入引擎——**模板补缺省，草稿 extensions 已有值优先**（不覆盖采集数据带入的参数）；config 白名单 7 字段（margin_rate/commission_rate/fx_buffer/offer_id_prefix/follow_type/stock/warehouse_id）+ 数值边界校验（非法 key 422）。
- **模板 API** `routes/templates_routes.py`：`GET/POST /templates` + `PATCH/DELETE /{id}` + `POST /{id}/default`（全走 `_authenticate` 租户隔离，仿 credentials_routes）。
- **submit_draft 集成**：请求体新增可选 `template_id`——显式指定 → 校验归属后注入；未指定 → 租户默认模板兜底；模板不存在 → fail-open 不阻断。注入后 envelope 进 graph payload，快照记录注入值（update marker 仍排除，T7 契约不变）。
- **货号前缀 `offer_id_prefix`**（prepare 层）：新建模式 offer_id 前加 `{prefix}_`（同店铺多批次防重）；**更新模式/跟卖忽略**（重上不变式 + follow 绑定保持）。
- **WebUI 上架配置页** `pages/Templates.tsx`：列表（默认标记/定价摘要/货号前缀/库存仓库）+ 新建/编辑弹窗（定价三参数 + 前缀 + 跟卖方式 + 库存/仓库）+ 删除 confirm + 设默认。
- **编辑页集成**（Products.tsx）：提交栏「上架配置」下拉（默认选中 is_default 模板）→ `submitDraft/submitDraftUpdate` 传 `template_id`；update 模式 Worker 自动忽略前缀。

### Test
- `tests/test_template_service.py`（16 用例）：CRUD + 租户隔离 + 设默认清旧 + 白名单/数值校验 + 注入语义（草稿优先/前缀仅新建/空 config 返回副本）。
- `tests/test_templates_api.py`（8 用例）：鉴权 401 + CRUD 端点 + 设默认端点 + 白名单 422 + 跨租户 404。
- `tests/test_submit_draft_template.py`（7 用例）：显式 template 注入 / 草稿值优先 / 默认模板兜底 / 无模板无默认原样 / 更新模式忽略前缀 / 模板不存在 fail-open / 快照记录注入值。
- 全量回归 worker **1008 passed**（976 基线 + 32 新）；skill 493；webui build + tokens:validate。

---

## [0.43.0] - 2026-08-16

> WebUI 运营工作台 v0.43（PRD `docs/PRD-webui-workbench-v0.43.md` 三大功能块全量交付）：MXOU 账号登录 + 商品编辑板块（全量重传更新 + 从零新建 + 生图内嵌）。worker 969 passed / skill 493 passed / 前端 build + tokens:validate 绿。

### Feat(F1 — MXOU 账号登录)

- **双登录模式**：WebUI 登录页「账号密码」tab（新增）+「API Key 直登」tab（存量保留）。
- **登录代理端点** `POST /api/v1/mxou/login`（`routes/mxou_routes.py` + `services/mxou_login_service.py`）：调 api.mxou.cn（newapi 平台）`/api/user/login` → session token → `/api/user/self` 余额 → `/api/token/` 密钥列表；错误映射 401 密码错/400 2FA/429 平台限流/502 不可达；`rate_limiter` 按 username 防爆破。
- **防御解析客户端** `utils/mxou_platform.py`（T1）：success 优先（不信任 HTTP status）、token 阶梯 `access_token→token→session_token`、形态判定 `newapi_jwt/oneapi_cookie/unknown`、`/api/user/self` 白名单脱敏（剥离 password/access_token）、`/api/token/` 三形态兼容 + 脱敏检测、建/吊销 key；密码/完整 key 永不进日志。
- **tokens 幂等 upsert**：登录/新建/选择 key 时 `INSERT ... ON CONFLICT (key) DO UPDATE` 到 Supabase tokens（去 sk- 前缀、status=1）——WebUI 新建的 key 也能通过现有 `_authenticate_token` 鉴权（**关键闭环**：此前 worker 只有 SELECT，新建 key 无同步路径）。
- **密钥管理**：`GET/POST/DELETE /api/v1/mxou/keys` + `POST /keys/{id}/select`（全走 `_authenticate` 租户隔离）；`KeyManager` 组件（列表脱敏/复制激活/新建一次展示完整 key/吊销 confirm）+ 侧边栏用户名 + 余额 badge（`MxouSessionStore` 内存 TTL 60s，单进程安全）。
- **T3 预留 T4-HOOK**：账号登录成功 → session store（username/balance/keys 脱敏）→ 密钥管理激活 key 即完成「账号登录即登录」。

### Feat(F2 — 商品编辑板块：全量重传更新 + 从零新建)

- **`product_index_service.py` 共享抽取**（T6）：`lookup_index/upsert_index` 从 image_service 抽为共享模块（改图/编辑/学习回填三处复用，防漂移）。
- **上传成功回填索引**（T9 前置修复）：`learning_record_node` approved 分支调 `upsert_index`（product_id/task_id=thread_id/tenant_id/credential_id/offer_id 守卫 + DB 异常非阻断）——普通上传也建索引，OnSale/编辑/更新依赖自此完整。
- **在线商品编辑数据** `GET /api/v1/products/{id}/edit`（T6）：索引定位 → 关联草稿 payload 返回（无索引 404 / 无草稿来源 409「仅改图可用 update_images」）。
- **更新模式提交**（T7）：`submit_draft(update_product_id=...)` —— 跳过 per-store 409（更新语义）→ graph_payload 副本注入 `extensions.update_product_id/update_offer_id`（**绝不持久化到草稿**）→ 入队后 upsert 索引新 task_id；正常模式 100% 不变。
- **upload 节点 UPDATE 模式**（T8）：读 `extensions.update_product_id` → item 带 `product_id`（Ozon UPDATE 而非 CREATE），优先于 follow_sell；无 marker → 行为字节级不变（防双卡）。
- **WebUI 三模式**（T10）：`ProductEditor` `mode='draft'|'online'|'new'`——draft 原行为不变 / online（`getProductEdit` → 表单 → 「更新上架」= `submitDraftUpdate`，隐藏保留采集数据防误删草稿来源）/ new（`/products/new` 空表单 + 必填校验 → `createDraft` 接线 → 转 draft 模式）；OnSale 行「编辑商品」入口（409/404 专用提示）。

### Feat(F3 — 生图内嵌）

- **`ImageStudioEmbed` 抽取**（T11）：原图选择/卖点/图配置/生成/新旧对比全封装，props 驱动（mode/draftId/taskId/initialOriginals/initialSelling/onGenerated/onClose）零路由依赖；`ImageStudio.tsx` 重构为 63 行薄壳（独立页行为不变）。
- **编辑页内嵌**（T12）：ProductEditor 新增「商品套图」区块——draft/online 模式 `initialOriginals=编辑中图片 + initialSelling=标题+属性前8条`，`onGenerated` 回填 form.images（随全量重传）；new 模式禁用提示；online 因 T6 响应无 task_id 暂按草稿渲染（未来加 task_id 一行激活实时生成）。

### Test

- worker 新增：test_mxou_platform（20，T1）/ test_mxou_login_api（21，T2+T4）/ test_product_edit（8，T6）/ test_index_backfill（7，T9）/ test_drafts_api 更新模式（8，T7）/ test_update_product_marker（10，T8）。
- 全量：**worker 969 passed**（基线 894 + 75 新）/ **skill 493 passed** / webui build + tokens:validate 绿。
- 探测脚本：`worker/scripts/probe_mxou_login.py`（T0，用户运行验证 MXOU 真实形态；响应不匹配 → 降级「仅 API Key 直登」）。

## [0.42.0] - 2026-08-16

> WebUI 运营工作台（hyperplan 对抗规划 14 任务 5 Wave 全量交付）：M0 数据脊柱（draft_submissions.status 写回复活 + running 删除守卫 + skill fail-hard + 直连任务也写 submission 行）→ M1 失败闭环 + 决策列（task→draft 解析端点 + estimate 共享定价端点）→ M2 在售货架 + 提交历史 + 首页工作台 + 设计系统（tokens.json 单一真相源）。worker 894 passed / skill 493 passed / 前端 build + tokens:validate 绿。

### Feat(M0 数据脊柱 — 生命周期单一事实来源)

- **M0.1 数据模型迁移**：`draft_submissions.draft_id` 改 nullable（直连任务行 draft_id=NULL，FK CASCADE 不作用于 NULL 行）+ `error_message` 列 + `status` 含 rejected；`product_task_index` 加 `draft_id` 列 + idx；init_data 幂等迁移（ADD COLUMN IF NOT EXISTS + DROP NOT NULL 解除存量约束）。
- **M0.2/M0.3 status 写回**：`utils/draft_status_writeback.py`（map_worker_status：completed→published/failed→failed/rejected→rejected/pending_moderation→uploading）+ task_processor 4 终态点接入（failed/rejected/completed/handle_task_failure），写回在 conn.commit() 之后（不扩事务，失败绝不回滚任务终态）。**采集箱「上架状态」列从此真实流转**（此前死字段恒 pending）。
- **M0.4 running 删除守卫**：DELETE /drafts/{id} 前查 draft_submissions ⋈ ozon_product_tasks status IN (pending,running) → 409；租户归属预查先于守卫（跨租户 404 不泄漏 active 任务存在性）。
- **M0.5 skill fail-hard**：`cloud_probe.submit_draft` 删除静默降级（_fallback）——worker 不可达/404 一律 {ok:False} 明确报错，绝不代用户直接上架（入箱语义不被背叛）。
- **M0.6/M0.7 draft_id 全覆盖（用户拍板方案）**：product_task_index 传播 draft_id（approved 路径解析）+ **直连任务也写 submission 行**（draft_id=NULL，main.py submit_task 端点，失败容忍不阻断入队）——所有任务都有 submission 行，生命周期视图完全统一，无「无草稿来源」特例。

### Feat(M1 失败闭环 + 决策列)

- **M1.1 task→draft 解析**：`GET /api/v1/tasks/{task_id}/draft`（归属校验→draft_submissions.submitted_task_id→product_task_index.task_id→None）+ 重上不变式锁定（同一 draft 重复提交 offer_id 一致 + per-store 409 防双卡）；Tasks 页「回采集箱改」按钮（无草稿禁用 + 草稿已删由编辑页 404 兜底）。
- **M1.2 estimate 共享定价**：`utils/pricing_estimate.compute_price`（pricing_node 同源公式**单处定义**，消除第三次漂移）+ `POST /api/v1/drafts/{id}/estimate`（读草稿 envelope + logistics_quote + 共享公式，纯读不落库）；采集箱三决策列（预估售价/利润/利润率，懒加载 Promise 缓存去重 + 并发≤4 + 失败静默）——采集箱从「暂存」变「决策台」。

### Feat(M2 在售货架 + 提交历史 + 首页 + 设计系统)

- **M2.1 在售货架**：`GET /api/v1/products`（product_task_index + LEFT JOIN 任务 result 提取 moderation_status，不实时调 Ozon）+ OnSale 页面（审核状态/草稿来源/改图弹窗复用 T14 update_images/Ozon 链接/分页/空态）。
- **M2.2 提交历史**：`GET /api/v1/drafts/{id}/submissions` 时间线（每草稿×每店铺提交记录 + 终态 + 错误原因）+ SubmissionHistory 弹窗（采集箱行「提交历史」入口）。
- **M2.3 首页工作台**：坏消息优先五分组（被拒/失败/待处理草稿/进行中/已上架）+ 一键重上/回采集箱改/去上架 + estimate 决策注入 + 空态引导；**条件侧边栏**（在售货架有数据才显示，加载中显示全部防闪烁，路由变化静默重拉）。
- **M2.4 设计系统 Figma-ready**：`src/tokens/tokens.json`（Tokens Studio 格式 67 token）单一真相源 → `scripts/sync-tokens.mjs` 生成 :root CSS 变量（tokens:sync/validate）+ 硬编码 hex 归零 + 补 font-weight/line-height/breakpoint/duration/easing/z-index token 类目 + `components/ui/`（Button/Badge/EmptyState/Skeleton）+ Playwright 视觉回归基线。未来 Figma：Tokens Studio 导出同名 JSON → 替换 → sync → 全局生效。

### Test

- worker 新增：test_draft_status_writeback（M0.2）/ test_task_processor_writeback（M0.3）/ test_delete_draft_guard（M0.4）/ test_image_service_index_draft（M0.6）/ test_submit_task_direct_writes_submission（M0.7）/ test_task_draft_resolver + test_resubmit_offer_id_invariant（M1.1）/ test_estimate_endpoint（M1.2）/ test_shelf（M2.1）/ test_submissions_timeline（M2.2）。
- skill 更新：test_to_box 改 fail-hard 断言（删降级测试）。
- 全量：**worker 894 passed**（基线 822 + 72 新）/ **skill 493 passed** / webui build + tokens:validate 绿。
- 修复：test_task_draft_resolver test_route_404_passthrough 漏 stopall 导致的 mock 泄漏（跨文件测试污染 test_step5，全量 887→894）。

## [0.41.0] - 2026-08-15

> WebUI v1 + 双向互通首批交付（`docs/PLAN-webui-v1.md` 18 任务全部验收通过）：worker 新增 routes/services 分层（凭证/草稿/任务/生图/在线商品 5 组端点）+ 4 新表 + 生图缓存 version/params 版本化 + image_gen_plan 类型选择 + 更新在线商品全量重传；webui 新增 React SPA（登录 + 采集箱/商品编辑/店铺管理/任务进度/生图工作台 5 页，静态托管 /app）；skill graph/follow 新增 `--to-box` 入采集箱；凭证三层防御（AES-256-GCM 列级加密 + 掩码 + 轮换）。worker 822 passed / skill 493 passed / 前端 build 成功。

### Feat(worker WebUI 数据层与端点)

- **T1 数据层迁移**（`storage/database/shared/model.py` + `init_data.py` 幂等建表）：新增 `product_drafts`（永久草稿，envelope-only，无凭证）/ `draft_submissions`（每次提交一行，换店铺新行 draft.id 不变）/ `credentials`（三层防御凭证表）/ `product_task_index`（商品↔任务定位，product_id 归档后可回溯）；`task_generated_images` ALTER 新增 `version`/`params`/`image_parent_task_id`，PK 改 `(task_id, slot, version)`；`migrate_webui_v1.py` 存量迁移。
- **T2 credential_cipher**（`utils/credential_cipher.py`）：AES-256-GCM 列级加密（env `CREDENTIAL_MASTER_KEY`，随机 nonce/值，AAD=tenant:client_id）+ `mask` 掩码（仅后 4 位）+ 轮换提醒字段。
- **T3 鉴权门**：`/run` `/stream_run` `/node_run` `/v1/chat/completions` 补齐 `_authenticate_token` + `rate_limiter.check()`，无/空 token → 401、超限 → 429（nginx deny 兜底）。
- **T5 凭证 CRUD + validate**（`routes/credentials_routes.py` + `services/credential_service.py`）：GET 列表仅掩码回显（明文 key 永不出现于响应/DB 明文列）；POST 加密创建；PATCH 轮换（旧行 revoked + 新行 active，`:revoked:` 后缀释放唯一槽）；DELETE 吊销；validate 解密 → Ozon probe → valid/reason。**租户隔离**：列表按 tenant_id 过滤。
- **T6 草稿 CRUD + submit**（`routes/drafts_routes.py` + `services/draft_service.py`）：POST /drafts 收 GraphInput → **剥离凭证**（payload 无 api_key，存 credential_id）；GET/PATCH（version 乐观锁 stale→409）；submit → C5 两层校验（per-store 409「重复商品」fail-open + 跨店 confirm 标记）→ 解密凭证重建 GraphInput → 入队 + submission 行；warehouse_id/stock 透传进 extensions 快照。
- **T8 任务列表端点**（`routes/tasks_routes.py` + `services/task_service.py`）：GET /tasks 租户隔离 + 分页，返回 status/progress/product_summary。
- **T14b 草稿 AI 单字段端点**（`services/ai_field_service.py`）：POST /drafts/{id}/ai/{field} 复用 `call_mxou_chat_api` + 翻译路径，返回 RU 只读（前端决定 PATCH 保存），未知 field → 400。

### Feat(worker 生图缓存版本化 + image_gen_plan)

- **T7a 缓存 version++ 显式重生成**（`task_image_cache.py`）：get/save 支持 version/params 快照；regen 端点 `force_regen` → 新行 version+1 新 URL，**无静默缓存命中**；resubmit 血缘回溯 `image_parent_task_id`（存原 task_id，与任务级 `payload.parent_task_id` 区分）→ 复用父图**不烧额度**。
- **T7b image_gen_plan 受限映射**（C3b 冻结）：`image_gen_plan`（type→count）只控制现有 10 slot 子集执行/跳过，不新增 slot、不改 graph 层；plan 校验：必须含 Phase1（white_bg 或 multi_angle），仅选 Phase2 类型 → 拒绝；默认全 10 张向后兼容。
- **T14 更新在线商品端点**（`routes/products_routes.py` + `services/image_service.py`）：POST /products/{id}/update_images：`product_task_index` 定位 → URL 存活检查（GET+Range）→ `/v3/product/import` 全量重传（product_id + offer_id + 新 images）→ status → `pending_moderation`「重新审核中」→ 索引行回填（upload 成功 + approved 路径挂钩）。

### Feat(webui 五页 SPA)

- **T4 前端脚手架**（`webui/` Vite React TS）：登录（token → Bearer 持久化）、路由、Axios 拦截器；`src/api/client.ts` 由 worker openapi.json 用 openapi-typescript 生成（单一真相源）；FastAPI `/app` StaticFiles + SPA fallback（未构建 dist 跳过挂载不阻断）。
- **T10 采集箱页面**：区间采集价 / SKU 数 / 来源 / 上架状态列（draft_submissions）/ 批量删除 / 清空级联。
- **T10b 商品编辑页**（上品帮 editGoods 版）：三区块锚点导航（主要信息/产品属性/变体设置）；上架店铺下拉 / 标题 3 AI 按钮 / 重量尺寸🤖 / 变体表格同首行填充 / 选择仓库 + 库存 / 定时上架 stub（persist scheduled_at）/ 立即上架（跨店 confirm 弹框闭环）。
- **T11 店铺管理页面**：绑定弹窗（shop_name/currency/is_default radio）/ 仅掩码列表 / 轮换/吊销/立即校验 / 轮换提醒 banner。
- **T12 任务进度页**：上架记录列（售价/划线价/货源/竞品/方式/时间）+ 筛选 + 异常重上（→ resubmit_task）+ 今日上架数。
- **T13 生图工作台**：原图 ≤3 / 卖点 AI 帮写 / 现有 slot 类型 ±1（材质/尺寸置灰）→ image_gen_plan / **生成前余额 + 预计消耗（N 张 = N 次）确认弹窗** / 余额 ≤0 阻止生成 / 分类型预览。

### Feat(skill --to-box)

- **T9 graph/follow `--to-box`**（`cli.py` + `cloud_probe.py`）：替代 `submit_envelope` → POST `/api/v1/drafts`（worker 剥离凭证），打印 `draft_id` + 「已入采集箱，请到 WebUI 认领」；老 skill 冷启动降级直接 submit + WebUI 横幅提示。

### Test

- worker 新增 13 文件：`test_webui_migrations`（T1）/ `test_credential_cipher`（T2）/ `test_auth_gates`（T3）/ `test_credentials_api`（T5，含租户隔离 + 明文 grep）/ `test_drafts_api`（T6，含 409 重复商品 + fail-open + 跨店 confirm + 隔离）/ `test_image_cache_version`（T7a，regen 新行新 URL + resubmit 复用父图不调 API）/ `test_image_gen_plan`（T7b，跳过断言 + Phase1 校验）/ `test_tasks_api`（T8）/ `test_draft_ai_endpoint`（T14b）/ `test_update_product`（T14）/ `test_webui_e2e`（T15）。
- skill 新增 `test_to_box`（T9，含 404 fallback）+ `test_compile_lists.py` 回归（cloud_probe 仍明文 COPY_FILES）。
- 全量验证：worker 822 passed、skill 493 passed（+1 预存）、前端 `npm run build` 成功、T15 架构评审门通过（新端点全部走 routes/services，main.py 仅注册路由）。

## [0.40.1] - 2026-08-13

> 生图合规 + 跟卖图片污染 + 类型属性 value 空 三连修复（v0.40 实测批量上架根因）：成人用品不注入标题（违禁词触发生图 violation）；跟卖参考图不再混入上传图片；8229 类型属性中文 value 置空后补 RU 文本；skill 端部分尺寸缺失按比例补齐。

### Fix(worker 生图合规)

- **成人用品生图不注入标题**（`utils/prompt_assembler.py`）：新增 `is_adult_product` 检测（成人/情趣/肛塞/后庭/性用品等中英俄关键词），`assemble_prompt` 检测到成人品类时清空 `title`/`category` + `product`/`appearance`/俄文文案等产品描述变量，保留场景/配色（靠参考图传达外观）。根因：肛塞等 1688 标题含违禁词，gpt-image-2/nano-banana-fast 内容审核直接 violation，实测 10 个生图节点大部分失败 → images count 仅 4。新增 7 单测。

### Fix(worker 跟卖参考图泄漏)

- **`_assemble_follow_sell` 不再把竞品参考图放进上传 payload**（`assemble_ozon_product_node.py`）：`images` 从 `draft.images`（Ozon 竞品图）改为 `[]`，AI 图由 prepare 阶段注入。
- **E1 兜底过滤扩展**（`utils/cos_uploader.py`）：`salvage_original_images` 新增 `_is_reference_image`——过滤 `ozonstatic`/`ir-20.`/`_460x460`/`.webp` 缩略图（原只查 `ir.ozone.ru`，漏掉 ir-20.ozonstatic.cn 竞品图 + 1688 图搜缩略图，被当成产品图转存上传）。
- 实测：园艺手套跟卖上传 9 张图全为 AI 生成 COS 图，0 参考图混入（`907172129`/`7786491361`/`ozonstatic`/`alicdn`/`460x460`/`.webp` 全绿）。

### Fix(worker 8229 类型属性 value 空)

- **8229(类型) 中文 value 置空后补 RU 文本**（`utils/ozon_dict_values.py` + `prepare_ozon_upload_node.py` + `assemble_ozon_product_node.py`）：新增 `fetch_ru_dict_value`（按 dictionary_value_id 用 /values(RU) 列表精确查俄语文本）。根因：dict_lookup 是 ZH_HANS 中文缓存，8229 value 中文「手套」置空 → 上传 `value=""` → Ozon 报「Фото товара не соответствует его типу」（照片与类型不符）。修复后 value=`Перчатки`，错误推进到类目层（`description_category_has_no_description_type`，防护手套应匹配「建筑和装修>防护和消防设备>防护手套」而非「服装>配饰>手套」）。新增 3 单测。

### Fix(skill 部分尺寸缺失)

- **部分维度缺失按比例补齐**（`scripts/cloud_probe.py`）：`length=250,width=90,height=0` 这类只缺一边时按 长:宽:高=2:1.5:1 补齐缺失边（原只处理全 0，导致 draft_sanity 拦截含 0 信封）。

### Test

- worker 全量 691 passed、skill 全量 487 passed（`test_8229_follow` 8→11、`test_prompt_assembler` 48→55）。

## [0.38.0] - 2026-08-12

> 第二批全量交付（P1a-P6 + D1-D5 + N1-N9）：discover 中国站模式 + 采集/匹配/裂变全链路并行化 + what_to_sell 批量畅销榜指标 + 源码保护扩至 14 模块；裂变两阶段 widget API、agent-in-loop 决策审计（review_log + --review + AK403 自动刷新）、缓存 VERSION 指纹一键失效、护栏阈值 settings 化；worker 端 SKU 去重防重复上架、moderation 拒绝显性化（rejected 终态 + resubmit）、余额不足拦截复活、webhook 终态通知；skill 端 cleanup 清扫、物流兜底硬化、fx_rate 三级解析、多店铺校验。

### Feat(skill discover 选品增强)

- **D1 discover 默认中国站**（`ozon_discovery.py`）：discover 默认 Ozon 中国站（cn）选品（`--local` 切回主站，`--china` 参数保留兼容）——中国站价格/库存/物流与俄语卖家视角一致，选品数据更可操作。
- **P1a discover 采集/匹配并行化**（`ozon_discovery.py`）：采集→分析→匹配→裂变各阶段线程级并行（`concurrent.futures` + CDP 连接复用），中国站大批量 URL 耗时显著下降；`--auto-submit` 支持并行提交。
- **P1b/P1c what_to_sell 批量畅销榜指标**（`ozon_seller_analytics.py`）：候选产品批量拉取月销/增长/跟卖数/drr 等运营指标，`candidate.ozon_category` 注入 what_to_sell 权威类目（category2_id/3_id），提交链路优先采用。
- **P4 follow success 判据移到提交后**（`cloud_probe.py`）：task_id 为空不再报成功——提交失败如实返回错误，不误导用户。

### Feat(skill 裂变 + agent-in-loop)

- **D2 裂变两阶段 widget API**（`ozon_fission.py`）：fission 第一阶段用 widget API 批量拉卖家全部产品浅层评分，第二阶段对高分候选逐产品深挖——卖家产品量大时深度抓取成本大幅下降；`_expand_seller` 逐产品并行化（线程级 CDP 连接）。
- **D3-L1 匹配决策元数据透传**（`ozon_discovery.py`/`ak_1688_client.py`）：confidence/badge_eff/score/reject_reason 贯通采集→匹配→组装全链路，决策依据不再黑盒。
- **D3-L2 review_log 决策审计模块**（新增 `scripts/lib/review_log.py`）：`data/review_log.jsonl` 追加式记录每次候选匹配决策（置信度/图搜 badge/拒绝原因/人工确认结果），审计可回溯。
- **D3-L3 `--review` 低置信人工确认**（`discover`/`follow`）：低于 `match_min_conf`/`match_badge_eff_min` 阈值的候选进入交互式确认（展示候选 + 依据，用户接受/跳过），低置信不再静默上架。
- **D3-L4 1688 AK 403 自动刷新重试**（`ak_1688_client.py`）：AK 403（配额/过期）自动刷新 token 重试一次；follow 不再吞 `AkAuthError`（此前 403 被静默吞掉导致候选为空、用户看到空结果）。

### Feat(skill 缓存指纹 + settings 参数化)

- **D4 缓存 VERSION 指纹**（`cache.py`）：缓存 key 前缀并入 `_cache_version()`（读 skill/VERSION）——发版即一键失效全部命名空间（probe1688/slug_cn/follow/ak_search/ak_img_search/…），旧版缓存数据不污染新版逻辑。
- **D5 护栏阈值 settings 化**：`probe_interval_seconds`（1688 探针节流间隔）、`match_min_conf`/`match_badge_eff_min`（匹配置信度/图搜 badge 门槛）从硬编码改为 `settings.json` 可配——调松紧无需重新发版。
- **P5 CDP 连接复用**（`enrich_product_with_cdp`）：支持外部传入 `CdpTab` 复用，批量富化不再 N 次重建连接。
- **P6 源码保护扩至 14 模块**（`compile.py`）：ozon_discovery/ozon_seller_analytics/analytics_upload/ozon_fission/ozon_seller/cdp_client 从明文复制晋升 Cython 编译（8→14），`_find_missing_imports` 兜底 + dist 完整性断言；`test_compile_lists.py` 锁定模块清单。

### Feat(worker 提交防护 + 审核闭环)

- **N1 SKU 级重复提交防护**（`supabase_task_processor` + DB）：`sku_key`（item_id + 变体维度）唯一索引 + `DUPLICATE_SUBMIT` 错误码——同一 SKU 重复提交直接返回已存在任务，防重复上架。
- **N2 moderation 拒绝显性化**（`main.py` + recheck 节点）：`rejected` 终态 + `POST /api/v1/resubmit_task/{id}` 重提交端点——被 Ozon 审核拒绝的任务不再模糊标记，修正后一键重提。
- **N4-w 任务终态 webhook 通知**（`task_processor.py`）：配置 `TASK_NOTIFY_URL` 后任务成功/失败自动 POST 终态通知。
- **余额不足拦截修复**（`main.py` auth）：MXOU 余额实查复活 + auth_node 对齐 + 统一错误码——`users.quota` 耗尽不再靠僵尸 `remain_quota` 误判。

### Feat(skill 运维与配置)

- **N3+N7 cleanup 命令**（`cli.py`）：`cleanup --profile-cache`（Chrome profile 可再生缓存）/ `--cache`（磁盘缓存）/ `--temp`（孤儿 .json.tmp）/ `--old-results --days N`（过期结果），`--dry-run` 预演，登录态保留。
- **N4-s query --watch + --notify**（`cli.py`）：`query --watch` 轮询任务进度至终态；`graph/follow/discover/batch --notify` 命令结束时输出任务通知。
- **N5 物流估算兜底硬化**（`cloud_probe.py`）：`fallback_chain` 透传 + last-good 费率缓存——Worker 物流报价失败时复用上次成功费率，不再退化到默认虚高兜底。
- **N6 fx_rate 三级解析**（`config_store.py`）：汇率按 店铺 `stores.json` → `settings.json` → 内置 0.075 三级回退，不再硬编码。
- **N8 多店铺校验**（`cli.py`/`config_store.py`）：default 名冲突守卫（禁止覆盖既有 default 店铺）+ 双店铺透传测试。

### Docs

- **N9 references 增强**（`skill/references/`）：stale 纠偏 + 命令全覆盖 + settings 参数表——command-reference/error-codes/env-setup 补全新命令（cleanup/query --watch/--notify/--review）与参数。

### Test

- skill 新增 36 文件：`test_china_mode`/`test_cli_china_flag`（D1）、`test_fission_shallow`/`test_fission_e2e_mock`/`test_fission_parallel`（D2）、`test_review_log`/`test_cli_review`/`test_match_decision_metadata`/`test_ak_403`（D3）、`test_cache_versioning`（D4）、`test_service_probe_interval`/`test_settings_guardrail`（D5）、`test_cleanup`（N3+N7）、`test_query_watch`（N4-s）、`test_logistics_fallback`（N5）、`test_fx_rate_config`（N6）、`test_multi_store`（N8）、`test_compile_lists`（P6，锁 14 模块清单）、`test_collect_analyze_parallel`/`test_match_selected_parallel`/`test_cli_auto_submit_parallel`/`test_bestseller_metrics_map`/`test_blue_ocean_live_queries`/`test_ozon_category_populate`/`test_follow_success_after_submit`/`test_enrich_reuse_conn`/`test_probe_reuse_cdp` 等（P1-P5）。
- worker 新增 7 文件：`test_submit_dedup`（N1）、`test_moderation_rejected`（N2）、`test_task_notify`（N4-w）、`test_auth_key_column`/`test_auth_node_balance_align`/`test_auth_node_failopen_mxou`/`test_submit_insufficient_balance`（余额拦截）。
- 全量验证：skill 本机 + Docker 3.12 双环境 pytest、worker pytest、ruff worker/src + skill scripts 全绿（详见 release commit 验证记录）。

## [0.37.0] - 2026-08-11

> 重量/尺寸兜底根治批次：废除「<10g×1000 轻物误伤」「密度÷1000 改写」等 8 处改写真实值的启发式，改为「只对缺失兜底、对已有值仅标记放行 + Sentry 留痕 + 原始信封保留」；skill 端修 parseWeightGrams 源头读错 + 高密度保留商家重量；Sentry DSN 内置硬编码（用户零配置）。

### Feat(worker 重量尺寸根治)

- **统一归一化模块**（新增 `worker/src/utils/weight_dimension_normalizer.py`）：收敛 prepare/pricing/validate/retry 四处各自实现的密度/单位启发式为单一裁决点。核心原则——数据缺失（weight≤0/dim≤0）才兜底（draft → 竞品 → 100g/300×200×50mm）；数据已有（非零真实值）默认信任，密度/单位异常仅打 `marks` 标疑，绝不改写。`weight_source`（draft/competitor/default）、`weight_estimated`、`dimensions_suspected`、`reasons` 贯通 pricing_info.wd_audit + payload._wd_audit + Sentry 上报。
- **废除 `<10g×1000` 轻物误伤**（prepare `_resolve_weight_dimensions` + pricing_node 两处）：旧启发式把真实轻物（3g 薄膜/5g 垫片/8g 塑料件）当"kg 误写 g"×1000 → 物流费 40× 爆炸（实测 3g→3000g，运费 3.23→132 CNY，售价 22→201 CNY）。改为标记 `light_weight_suspect` 放行不修正；字符串带小数点的明确 kg 证据仍允许转换。
- **废除密度÷1000 改写**（prepare A8 + skill A3）：密度异常只标记 `dimensions_suspected`/`weight_estimated`，保留商家重量（300g 铅坠不再被体积×1.0 砍成 24g）。
- **收敛 retry 子图**（`validation_retry_loop.repair_dimensions_node`）：删除「<40g 抬升到 40g」「无条件按密度重算三边」「体积重比值超阈值重算尺寸」——真实尺寸一律保留（比值判据对低密度真实商品天然不成立），仅尺寸全缺失才按重量推算。
- **draft_sanity 轻物放行**：`check_weight_suspect` 对 `<10g+尺寸>50mm` 仅标记不拦截；`validate_draft_sanity` 只拦物理超限（>50kg/单边>5m），真实轻物正常入队。
- **Sentry 标疑上报**：pricing 标疑时 `capture_task_error` 上报 `[WEIGHT_DIM_SUSPECT]`（放行但留痕）；route_after_pricing 消费 wd_audit 告警。原始信封天然保留在 PG `ozon_product_tasks.payload`（含原始 draft.weight/dimensions，可回溯分析）。

### Feat(skill 源头修复 + Sentry 零配置)

- **B4 parseWeightGrams 修复**（`service.py`）：旧 `parseInteger` 用 `replace(/[^\d]/g,'')` 剥离小数点——`'61.8g'→618`（放大10倍）、`'1.2kg'→12`（应为1200）、`'0.5kg'→5`。新增 `parseWeightGrams`（parseFloat 保留小数 + kg→g 换算），packaging 表重量解析改用它；minOrderQty 仍用 parseInteger（纯整数）。
- **A3 高密度保留商家重量**（`cloud_probe._validate_and_fix_product_data`）：密度>10 g/cm³ 且体积≥10cm³ 时不再 `weight=体积×1.0` 覆盖真实重量，改为保留 + 标记 `weight_estimated`（信封 `draft.weight_estimated` 透传 worker 审计）。
- **Sentry DSN 内置硬编码**（`config_store.DEFAULT_SENTRY_DSN` + `cli._init_sentry` 统一）：skill 用户零配置即可上报错误（与 worker 同 org/project pouding_ozon，`environment="skill"` 区分）；`settings.json.sentry_dsn` 可覆盖（自建 Sentry）。上报仅异常堆栈 + 非敏感 tags，绝不含 token/ak/api_key 凭证。

### Test

- 新增 `worker/tests/test_weight_dimension_normalizer.py`（12 断言：轻物不放大/缺失兜底/竞品优先/密度标疑/字符串kg转换）、`skill/tests/test_parse_weight_grams.py`（5 断言：61.8g 不放大/kg 换算/None 容错）。
- 更新 `test_wave2_fixes.py`（retry 真实尺寸保留语义）、`test_resolve_weight_dimensions.py`（密度保留+标疑）、`test_draft_sanity.py`（轻物放行 5 断言）、`test_wave1_fixes.py`/`test_dimension_units.py`/`test_envelope_fields.py`（5 元组解包 + A3 新语义）。
- 全量验证：worker 545 passed（2 既有失败与本改动无关）、skill 相关 38 passed、编译态 dist 8 .so + import 校验通过。

## [0.36.0] - 2026-08-11

> Wave 4 稳定性批次：浏览器链路修复（CHROME_PATH / PEP 668 / 登录误判区分 / api_only 降级）+ 1688 配额缓存（AK 搜索/图搜）+ 批量断点续传 + 打包安全（dist 断言 / config 原子写 / Chrome profile 并发锁）+ SKILL.md 瘦身与 references 迁移。

### Feat(skill 浏览器链路)

- **Q1 `CHROME_PATH` 环境变量**（`service.py:883 find_browser_executable` Phase 0）：显式指定浏览器可执行文件（服务器/CI 无默认浏览器时强覆盖），优先级在 explicit 参数之后、候选路径扫描之前；路径不存在 → `ConfigError` 明确报错（显式配置错误不静默忽略），报错提示同时指引 CHROME_PATH 兜底。
- **Q6 PEP 668 自动安装**（`service.py:2078 _auto_install_browser`）：两处 `pip install playwright`（镜像源 + 兜底分支）加 `--break-system-packages`——uv 托管 Python（Debian/Ubuntu externally-managed-environment）此前直接拒绝安装，自动装浏览器被静默阻断。
- **Q3 登录结果结构化**（`service.py _wait_for_login_session`）：返回 `{ok, session, reason}`——`ok=True` 兼容旧调用方（session 含 cdp_url/login_detected），`ok=False` 时 `reason ∈ {no_cdp, timeout, cdp_error}`，失败原因不再混为一谈。
- **Q4 登录误判区分**（`ak_1688_client.py enrich_product_with_cdp` 消费侧）：按 reason 区分三种失败并给出各自可执行提示——浏览器不存在（→ 安装 Chromium 或设 CHROME_PATH）/ 登录超时（→ 超时窗口内重新扫码）/ 浏览器启动失败（→ 手动启动 Chrome 或删除 data/browser/profiles/1688 下损坏 profile 缓存）。此前「登录超时 vs 浏览器启动失败」混淆，用户被误导反复扫码。

### Feat(skill 1688 配额缓存)

- **Q9 AK 搜索缓存**（`ak_1688_client.py search_products`）：磁盘缓存 `ak_search`（key = 查询词 + 规范化请求参数 JSON，`sort_keys` 保证同参同 key），TTL 24h——follow/discover 重复同 key 不再耗 1688 AK 配额；**仅缓存非空结果**（空列表/瞬时失败不写缓存，下次重试真实请求）。
- **Q9 图搜缓存**（`ak_1688_client.py` 图搜接口）：磁盘缓存 `ak_img_search`（key = imageUrl），TTL 6h——仅 imageUrl 模式可缓存（本地文件 base64 无法稳定复用），防 follow 重复图搜耗配额。
- **Q7 follow 链路三级缓存**：① `service.py probe_1688_page` 标准 cache.py 命名空间缓存（`probe1688`, key=url, TTL 24h）优先，原 `_find_cached_probe` 工件扫描保留作二级兜底；② `cloud_probe.py _translate_slug_to_cn` LLM 翻译缓存（`slug_cn`, TTL 30d——LLM 成本高，同 slug 不重复调用）；③ `follow_sell_cloud` envelope 级缓存（`follow`, key=`{product_id}:{store_id}`, TTL 6h——命中且有 images/1688_matches 直接复用，auto_submit 照常提交，不重复 CDP 抓取/图搜/LLM）。

### Feat(skill 批量断点续传)

- **Q8 `batch_test --resume`**（`batch_test.py`）：新参数 `--resume`（自动找最新 `batch_*.json`，排除 `*_summary.json`）+ `--resume-from FILE`（显式指定）；只跳过上次 `success=true` 项（1688 offer_id / Ozon product_id），失败项自动重试（防漏上架），成功项不重跑（防重复上架）；结果合并写回原结果文件（不拆文件），summary 记录 `resume_from`、`stats.skipped` 计入；URL 解析 + resume 判定前置到 Chrome 启动之前——无历史快速退出 rc=1、全部完成 rc=0，不再白启 Chrome。未传 `--resume` 行为完全不变。

### Feat(skill 打包安全)

- **Q15 config 原子写**（`config_store.py`）：stores.json/settings.json/auth_cache 三处 `write_text` → `_atomic_write_json`（临时文件 + os.replace 同目录原子替换，Windows 文件锁失败短等待重试一次，仿 cache.py v0.14 E3）——并发 CLI 进程（check/set_token/set_store 同开）不再读到半截 JSON / 丢凭证。
- **Q15 Chrome profile 并发锁**（`chrome_launcher.py`）：原 tempdir 全局锁 + flock LOCK_NB（非阻塞，第二个并发进程直接抛未捕获 BlockingIOError）→ per-profile 锁（`data/browser/.profile-{name}.lock`，仿 updater.py .update.lock 模式，fcntl/msvcrt 双分支）+ 阻塞等待 30s 超时 + 超时优雅降级；不同 profile 不再无谓串行。
- **Q12 dist 完整性断言**（`compile.py _assert_dist_safety`）：追加 `dist/data/browser`（Chrome profile 登录态）禁止打包断言——违反即 SystemExit 阻断打包分发，防登录态泄露；原有 runtime_probe.py 必须明文 + `data/.venv` 排除断言保留。

### Refactor(skill SKILL.md 瘦身 + references 迁移)

- **Q13**：SKILL.md 150→100 行——完整意图路由决策树细纲迁至 `references/command-reference.md`（SKILL.md 保留要点速记 + 链接）；新增 `references/anti-patterns.md` / `references/discover-fission.md` / `references/trend-selection.md` 三个专题文档。
- **Q10 分发版本覆写**（`compile.py`）：新增 `_rewrite_skill_frontmatter_version`——打包时用 `skill/VERSION` 覆写 dist 内 SKILL.md frontmatter 的 `version` 字段（count=1 仅改第一处，正文不动）；3 个新 references 文件纳入 `DOC_FILES` 随包分发。

### Feat(skill 版本四源统一)

- **Q11**：`VERSION` 四源统一为 0.36.0——root `VERSION` / `skill/VERSION` / `deploy/skill/VERSION`（此前滞留 0.34.0）/ `SKILL.md` frontmatter `version`（此前滞留 0.30.0）；`build-skill.yml` 新增 frontmatter 校验 step——从 `dist/SKILL.md` frontmatter 提取 version 断言 == 发布 tag（与既有包内 VERSION 硬校验并列），防 `_rewrite_skill_frontmatter_version` 覆写失效或源码 frontmatter 漂移。

### 测试

- 新增 9 文件 66 断言全绿：`test_batch_test_resume.py`（13：最新文件查找排除 summary / 损坏容错 / 成功 ID 提取 / main 集成跳过+重试+合并写回）、`test_follow_cache.py`（10：probe 缓存命中跳过浏览器 / 工件兜底 / slug LLM 缓存 / envelope 级命中复用）、`test_login_misjudge.py`、`test_service_chrome_path_autoinstall.py`（Q1/Q6）、`test_api_only_degraded.py`（Q2）、`test_config_atomic_write.py` + `test_chrome_profile_lock.py`（Q15）、`test_compile_frontmatter.py`（Q10/Q13）、`test_compile_no_profile.py`（Q12）。
- 全量 skill pytest 251 passed（排除网络类 6 文件）；batch_test 既有 URL 解析回归 16 断言全过。

### 待补

- **Q5 safe_unlink**（`utils.py`）：Windows 沙箱 fail-open 安全删除——`safe_unlink`（Path.unlink → 降级 os.remove → warning 返回 False）+ `safe_rmtree`（ignore_errors=True + warning）；替换 7 处裸删除（cache.py 过期清理 / task_paths.py / updater.py 回滚 / bootstrap_update.py / chrome_launcher.py PID 文件）；新增 `test_safe_unlink.py`（7 断言：成功 / PermissionError 降级 / 双败返回 False / missing_ok / rmtree 双态）。

## [0.35.0] - 2026-08-10

> SKILL.md 精简为纯操作手册 + discover 选品结构性分析文档（MD+JSON）+ Skill 端 Sentry 错误上报（复用 pouding_ozon，environment=skill）。规划：参考毛子ERP/上品帮选品逻辑调研（月销/增长/跟卖数/drr 四指标已覆盖，广告位 DOM 解析不引入——主流工具均用转化率替代）。

### Feat(skill discover 结构性分析文档)

- **`export_analysis_report()`**（`ozon_discovery.py:829`）：match_selected（1688 货源分析）完成后自动生成 `data/discovery/analysis_{ts}.md` + `analysis_{ts}.json`（同 ts 配对，无需 `--export`）——MD 头部汇总（总数/状态分布/蓝海 Top-N/利润分布）+ 每产品详情块（标题/价格/月销/增长%/广告%/跟卖数/上架天/评分/蓝海分/利润率%/1688 货源/审核状态），密度足够 Agent 直接据此向用户汇报，无需再读原始 `discovery_*.json` 缓存；JSON 为 `{generated_at, summary{total,status_distribution,blue_ocean{max,avg},profit{max,median,profitable_count}}, candidates, top_blue_ocean}`。
- **cmd_discover 接线**（`cli.py`）：货源分析后打印两行文档路径；fail-open（生成失败仅 warning，不影响选品主流程，rc 仍 0）。
- **文档**：SKILL.md discover 行 + `references/output-schema.md` 新增「discover 选品分析文档」章节（schema + Agent 汇报模板）。

### Feat(skill Sentry 错误上报)

- **`_init_sentry()` / `_capture_exception()`**（`cli.py:1167/1193`）：main() 入口初始化 `sentry_sdk`（`environment="skill"`、`release=VERSION`、`traces_sample_rate=0.0`）；命令异常捕获后上报再 re-raise（保留 traceback + 退出码 1，不吞异常）。tags 仅非敏感字段（command/skill_version/os/platform），**凭证零上传**。
- **安全降级**：`SENTRY_DSN` 未设置 / sentry-sdk 未安装 / 测试进程 → 全链路静默 no-op，不阻塞任何命令；lazy import 保证缺依赖时行为不变。
- **依赖 3→4**：`requirements.txt` 正式加入 `sentry-sdk>=2.0.0`。

### Refactor(skill SKILL.md 精简)

- **§6「更新与旧包升级」（45 行）压缩为「常见问题与升级」（4 行）**：只保留缺依赖 → `pip install -r requirements.txt`、`graph`/`follow` 缺模块 → bootstrap 升级两条排错指引；删除自动更新机制/venv 自动发现/ABI 绑定/profile 迁移叙述（维护者内容，非 Agent 操作手册该有的）。
- **全文版本号注记清除**：8 处 `v0.xx`/`v3`/`v4` 内嵌注记改写为无版本表述（`check`/`update`/`migrate_profile`/`seller`/`discover`/`queries`/fission 规则），功能描述不变。192 行 → 150 行。

### 测试

- 新增 `test_discovery_analysis_report.py`（10 断言：双文件生成/JSON schema/状态分布/top 排序/非 ASCII/空列表/同 ts 配对/利润指标）、`test_sentry_skill.py`（5 断言：无 DSN no-op/init 参数/ImportError 降级/无凭证 tags/测试进程跳过）。
- 全量 skill 测试 24 文件全绿；`compile.py` 9 模块编译成功 + import 完整性校验通过（ozon_discovery.py cythonize 安全）；`ci.sh --quick` 通过。

## [0.31.0] - 2026-08-08

> discover v3 裂变选品（同行卖家 = 选品引擎）+ trend 管线移除（agent 自带 LLM/web_search）+ 真实 CLI 链路修复。规划文件：`.omo/plans/discover-v3-fission-selection.md`。

### Feat(skill 裂变选品 — discover --fission)

- **裂变选品引擎 `ozon_fission.py`**：二分图 BFS（商品↔卖家）——种子商品 → 跟卖卖家（widget API 前 20）→ 卖家店铺产品 → 再发现。双 visited 集合（product_id / normalized seller_id）截断环路 + 三重预算（`--max-depth 2` / `--max-total-products 300` / `--time-budget 600`）任一触顶即停，**不会无界扩散**。
- **CLI 接线**：`discover --keyword <词> --fission`（种子采集 → 裂变 → 表格挑选 → 批量货源，下游全复用）；新参数 `--max-depth` / `--allow-depth-3`（>3 显式）/ `--max-total-products` / `--time-budget` / `--max-sellers-per-product` / `--max-products-per-seller` / `--non-interactive`；阶段展示（每层候选数 + 已展开卖家数）。
- **证据链**：裂变候选带 `source_chain`（种子→卖家→产品，选中时展示）+ `chain_depth`（深度越浅评分越高 10/7/4/0）。
- **蓝海评分扩展**：`calculate_blue_ocean_score` 加 `chain_depth`（10 分）+ `category_consistency`（10 分，同类目 10/跨类目 3/无数据 0）因子；权重再平衡（competing_sellers 30→20、profit_margin 30→20），两模式总和均 ≤100。`apply_analytics_to_candidate` 从 `category2_id`（Seller 权威类目）写 `candidate.category`；`run_fission` 种子类目透传裂变候选。
- **`_analyze_product` 保留 `competing_seller_list`**（完整 sellers[]，之前只取 count/min_price 丢弃——裂变种子零成本）。
- **`fetch_seller_analysis` 双 bug 修复**：签名错（`cdp_url` 非合法参数 + list 当 cdp 传）→ 正确 `(cdp, skus)`；camelCase（soldCount）→ snake_case（sold_count）——seller 命令从此返回真实运营数据。`fetch_seller_products`/`fetch_seller_analysis` 加 `cdp=` 连接级复用参数（cmd_seller 兼容）。

### Refactor(skill trend 移除)

- **砍 trend 管线**：删 `trend_selection.py`(161 行) + `mxou_chat.py`(38 行) + `cmd_trend`/`_export_trend`/`_attach_skus_via_cdp` + `test_trend_selection.py`。理由：agent 自带 LLM + web_search（能力替代）+ 与 discover 重复实现。SKILL.md 管线 E 重写为「agent web_search + LLM 提炼关键词 → discover --keyword」；command-reference/AGENTS/README 同步。worker 侧 `mxou_api.py` 保留（云端无 agent）。

### Fix(skill 真实链路)

- **跟卖卖家字段名修正**（maozi 插件实证）：webSellerList 真实字段是 `id`/`link`/`name`（非 sellerId/sellerUrl）——之前 seller_name/seller_url 恒空，裂变 normalize 拿不到卖家 ID。
- **stale tab 存活校验**：`_ensure_ozon_tab` 复用前 evaluate ping，失败新建（防「No such target id」500）。
- **DataDome 页面校验**：`_ensure_ozon_tab(cdp, target_url)` 复用 tab 后导航到目标商品页再 fetch（学 ozon_scraper；在无关页 fetch 被 DataDome 拦）。
- **`normalize_seller_id` 前缀剥离顺序修正**：先剥 `/seller/` 再 rstrip（防 `/seller/` 误留）。
- **preflight 源码模式跳过版本门**：无 `.so` 时任何 ≥3.12 解释器可运行（开发友好；dist 分发版本门照常）。

### 测试

- 新增 `test_fission_budget.py`（9 断言：预算触顶/双 visited/seller_id 归一化/checkpoint/共识排名）、`test_fission_e2e_mock.py`（4 断言：3 种子 depth=1 <5s + source_chain + visited 截断 + category 透传）、`test_blue_ocean_extension.py`（6 断言：chain_depth 四档/category 三态/两模式 ≤100）、`test_seller_analysis_fix.py`（7 断言：签名/字段名/cdp 复用）。
- 真实 CLI 验证：`discover --keyword 宠物用品 --fission` → 3 种子 → 45 真实跟卖卖家 → 6 候选（含 3 裂变产品：拉力器/宠物梳/削皮器）→ 表格 → EXIT=0。

## [0.32.0] - 2026-08-09

> 生图提示词视觉变量体系（Wave 1-C/2 占位符 + color_preset + 模型路由）+ 四修复（生图类目补链 / 类目 sim 接受门槛 / retry moderation 字段 / 属性同义词匹配）。规划来源：线上 E2E 实测驱动（5371047 店铺 3 产品，2 approved + 1 validation_failed）。

### Feat(worker 生图 v0.32)

- **生图模板占位符体系**：`image_prompts.json` 10 图位加入确定性视觉变量占位符（`{{material}}/{{size}}/{{weight}}/{{category}}` + LLM 扩展 `{{lighting}}/{{background}}/{{effects}}/{{atmosphere}}`，`{% if %}` 守卫缺省整句省略）。
- **visual_vars_llm 节点**（Wave 2）：deepseek-v4-flash 推断 19 个视觉变量（2 层容错 JSON 解析，失败回退确定性提取 + 品类默认，绝不阻断生图）；10 生图节点接线消费 + `color_preset` 配色预设路由。
- **生图移除 color 变量**：参考图承担产品颜色（1688 多选逗号串脏值防误导）；GIFT 禁编造 + 1688 属性值清洗（多选串取首项 + 30 字符截断）。
- 文档：`docs/IMAGE-PROMPT-GUIDE.md`（10 占位符 + 图位组合 + 模型路由表）。

### Fix(worker 四修复)

- **生图 `{{category}}` 恒空修复**（实测日志：prompt 渲染「突出品类产品的优势」= category 空）：
  - skill `cloud_probe.py`：`draft.category` 兜底 1688 面包屑末级（`category_name` 为空时），不再空/俄语。
  - worker `assemble` 类目匹配后回填 `category_name`（ZH 末级类目名）→ `GlobalState`。
  - `visual_vars_llm` 兜底输出补 `category/weight` + 类目解析接 `state.category_name`。
  - `merge_visual_vars` 排除确定性 key（`material/size/weight/category`）——LLM 英文 `Plastic` 不再覆盖中文「ABS塑料」；SP 放宽：确定性变量保留源语言，创意变量英文。
  - 10 生图节点 `merge_visual_vars` 传 `state.category_name`。
- **类目匹配 sim 接受门槛**（实测：笔筒 sim=0.200 错配「儿童多功能学习挂图」被直接采用 → 属性映射 0）：
  - `MIN_SIM_BY_MATCHER`（jieba 0.5 / pg_trgm 0.3 / ili 0.5，三路标尺不可共用）；低分候选不直接采用 → overlap 验证 → LLM fallback → 最终采纳点硬阻断（`match_confidence=0.0` + 阻断上架）。
  - `match_confidence` 从硬编码 0.5 改为挂钩真实 sim（`graph.py` `<0.3` 路由阻断从此真正生效）。
  - `ozon_category_query` pg_trgm 显式 `>=0.3` 过滤（GUC 无关，消除 `db.py` SET 连接级失效的非确定性）。
- **retry 审核轮询超时修复**（实测日志：`"ValidationRetryLoopState" object has no field "moderation_status"` → 60×5s 轮询超时未决）：
  - `ValidationRetryLoopState` 补 `moderation_status`/`failed_stage` 字段（Pydantic v2 严格禁止未定义字段赋值）；透出链：子图 → `ValidationRetryLoopOutput` → wrapper → `GlobalState`。
  - 附带修复 `repair_pricing_node` PRICING_FAILED 分支 `state.failed_stage` 无 try/except 崩溃风险。
- **属性名同义词匹配**（实测：1688 笔筒 15 属性映射 0——`_match_product_attr` 纯字符串匹配对无空格中文失效 + 同义词表不进 assemble）：
  - `_match_product_attr` 改四层：精确 → 包含 → jieba 分词子串重叠 → 同义词组（`match_attr_name_synonym` 同组双向包含，防错误值）。
  - 共享加载器 `load_attr_synonyms()`（prepare `_fill_optional_dict_attrs` 改用同一来源）；扩充 `attr_synonyms.json`（主要材质/适用季节/款式 + color/type/quantity 3 新组）。

### 测试

- 新增 `test_category_match_threshold.py`（11 断言：三路门槛/L0-Skill 豁免/sim 挂钩）、`test_attr_synonym_match.py`（13 断言：词汇分歧/负例/jieba 重叠/管道）、`test_envelope_category_fallback.py`（7 断言：面包屑末级兜底）。
- 扩展 `test_prompt_assembler.py`（+9：merge 防英文覆盖/category/weight 携带/state 兜底）、`test_visual_vars_llm_node.py`（+3）、`test_attribute_fill_v016.py`（+1：分歧属性入输出）、`test_retry_attr_snapshot.py`（+5：moderation 字段/approved/declined/failed_stage）。
- 全量 worker pytest 428 passed / 2 基线失败（`learning_record_gate`、`full_pipeline_context`，git worktree 基线 d73472a 同败，非本次引入）；mock 管线 12/12；skill 测试全过；`ci.sh --quick` 通过。
- 真实链路验证（本地 Docker）：笔筒 sim=0.200 拒绝（门槛 0.5）✅、正常 0.667 接受 ✅、pg_trgm 0.31 接受 ✅；同义词「季节↔适用季节」「材质↔主要材质」「款式↔风格」匹配 ✅、跨组负例 None ✅。

## [0.33.0] - 2026-08-09

> 生图 v6 单阶段模板落地——俄文文字 AI 直出（不再两阶段 PS 叠加）。用户提供 `prompt-template-v6.html` 模板系统，按「全英文模板 + visual_vars_llm 扩展 + 10 图位全升级」三决策落地。

### Feat(worker v6 单阶段俄文生图)

- **v6 核心变化**：从 v5 两阶段（AI 干净图 → PS 叠加俄文）→ v6 单阶段（俄文文字直接写进 prompt，AI 一步生成含俄文的完整图）。
- **visual_vars_llm 25 key**：ALL_KEYS 19→25——`headline_style`（必填，6 风格 EXCLAIM/PROMISE/NUMBER/CONTRAST/QUESTION/TWO_LINE_TWO_COLOR）+ `product_ru`/`cta_ru`/`selling_points_ru`/`effect_data_ru`/`target_ru`（俄文内容，Cyrillic 生成）；确定性产出 `brand_primary`/`accent`（HEX，get_preset_colors，LLM 不可覆盖）；max_tokens 2048→4096。
- **俄文回退安全**：5 个 RU key 回退空串 → LLM 失败时干净无文字图（绝不用中文 title 顶替——中文进图被 Ozon 拒）。
- **10 图位英文 v6 模板**（image_prompts.json + _DEFAULT_PROMPTS 逐字一致）：
  - 首句 `Product: {{title}}`（title 接地，保住标题注入护栏）。
  - 后缀 A（7 图 AI 直出俄文）：main/scene_1/2/3/comparison/detail/social_proof 内嵌 `{{product_ru}}`/`{{cta_ru}}`/`{{selling_points_ru}}`/`{{effect_data_ru}}`/`{{target_ru}}` + `{{headline_style}}`/`{{brand_primary}}`/`{{accent}}`；负面禁中文/英文/水印/价格（**不禁俄文**）。
  - 后缀 B（3 图禁一切文字）：white_bg/multi_angle/variant_white_bg 零 RU 占位符 + 严格负面（`no text of any kind`）。
  - 全部 RU/LLM/风格变量 `{% if %}` 守卫（空值→干净图，无 `{{` 残留/None）。
- **color 确定性**：`_DETERMINISTIC_KEYS` +color——LLM 英文 color 不覆盖（参考图承担颜色，防 1688 脏多选串）。
- **10 个生图节点零改动**（模板+变量层全在配置/工具层，`git diff --stat` 确认）。

### 测试

- 扩展 `test_visual_vars_llm_node.py`（18 断言：25 key/RU 回退/HEX/防 color_preset 碰撞/SP 俄文指令）、`test_image_prompts_config.py`（18：Product 前缀/RU 占位符渲染/白底禁文字/负面内嵌）、`test_prompt_assembler.py`（32：RU 变量渲染/空值无残留）、`test_image_gen_title_injection.py`（18：material 断言迁移 white_bg/color 不注入）。
- 全量 worker pytest **442 passed**（排除 2 个已知基线失败 + 2 个需 PG 文件）。
- 真实渲染验证（本地 Docker）：main prompt 含俄文标题「НАДЁЖНЫЙ ЗВУК」+ 品名 + 卖点 + HEX #F59E0B + `Product:` 前缀 ✅；LLM 失败回退干净无文字 ✅；white_bg 禁一切文字 + 材质渲染 ✅。

## [0.33.1] - 2026-08-09

> v8 中文模板修正（用户迭代：v6 英文模板 → v8 中文文案 + SCENE 独立变量 + multi_angle 禁文字纠正）。

### Refactor(worker 生图模板 v8 中文)

- **v6 英文模板 → v8 中文**：`image_prompts.json` 10 图位改中文文案（用户提供 `prompt-template-v8.html`），首句「产品：{{title}}」，`_DEFAULT_PROMPTS` 逐字同步。
- **SCENE_1/2/3 独立变量**：`visual_vars_llm` 透传 `scene_context_1/2/3` → 输出 `scene_1/scene_2/scene_3`（中文场景文案，确定性透传，LLM 不可覆盖，空值回退 `""`）；scene 节点模板改用 `{{scene_N}}` 替代 `{{scene_context}}`。
- **文字规则（最终确认）**：允许俄文 7 图（main/scene×3/comparison/detail/social_proof，含 `{{product_ru}}` 等 AI 渲染）+ 禁一切文字 3 图（white_bg/multi_angle/variant_white_bg）。
- **multi_angle 禁文字纠正**：用户口头修正 v8 HTML 的「俄文角标」设计——multi_angle 不允许任何文字（无 ВИД 角标，与 v6 一致）。
- 视觉变量保持英文（SP 不动）；所有 RU/场景/风格变量 `{% if %}` 守卫（空值→干净图无残留）。

### 测试

- 适配 3 个测试文件（v6 英文断言 → v8 中文语义：`Product:`→`产品：`、`no text`→`禁止任何文字`、scene_context→scene_N）。
- 全量 worker pytest **454 passed**（排除 2 个基线失败 + 2 个需 PG 文件）。
- 真实渲染验证（本地 Docker）：main 中文 prompt 含「夏日户外野餐」场景 + 「НАДЁЖНЫЙ ЗВУК」俄文标题 + HEX + 中文负面 ✅；white_bg/multi_angle 中文禁文字 ✅；scene_1 用 `{{scene_1}}` 渲染 ✅。

## [0.34.0] - 2026-08-10

> Sentry 生产错误修复（品牌/类目/描述/富文本）+ 选品数据回传（C5）+ 店铺埋点（C6）+ 类目匹配四重优化 + Sentry 用户上下文。规划文件：`.omo/plans/sentry-attribute-fixes.md`。

### Fix(worker Wave1 Sentry 四修复)

- **C1 必填属性兜底链**：attr_defaults 补 8229/9163/10096/4295/31/8292/23487/4389 安全默认分支，assemble/prepare/retry 三处一致（Sentry 967+ 次缺失归因）。
- **C3 FB_INSTA**：`_sanitize_description` 加西里尔社交词（词边界匹配，防误杀 телеграмма/одноклассники）+ `_sanitize_rich_description`/`_append_spec_table` 同步；FB_INSTA 路由 FIX_TYPE_UNFIXABLE + REPAIR_STRATEGY + ERROR_NOTICE_MAP。
- **C2 竞品尺寸重量兜底**：`PrepareOzonUploadInput` 加 `extensions` 字段；`draft_sanity` 竞品放行（weight=0 + competitor_weight_g>0 通过）；`_resolve_weight_dimensions` 抽独立函数——draft → 竞品 → 100g/300×200×50mm 三级兜底（修复 v0.9 死代码：main.py 原传 `payload.extensions` 恒空）。
- **C8 富文本 4191**：`title_ru` 空时最小 HTML 追加 + 每属性翻译跳过 4191 HTML 值（`_looks_like_html` 守卫，保留 `<p>/<b>/<ul>/<li>` 结构）。
- **其他**：leaf_name 默认空串（信封无 source_category 时防 UnboundLocalError，v0.21 引入）；品牌 85/31/5076 直写「无品牌」跳过字典兜底（消除误导性「无法获取字典值」ERROR + 无谓 API 拉取）；`_append_spec_table` 属性名中文净化（Sentry C2：schema ZH_HANS 中文属性名进规格表 → 描述含中文）。

### Feat(worker C5 选品数据回传 + C6 埋点)

- **C5 三张 PG 表**（ORM）：`blue_ocean_queries` / `ozon_bestsellers` / `market_bestsellers`（唯一键含 contributed_by_token_id，`INSERT ON CONFLICT UPDATE` 去重）；`/api/v1/analytics/{queries,ozon-bestsellers,market-bestsellers}` 三端点接收 skill 上报（token 鉴权，Supabase 未配置本地放行）。
- **C5 skill 侧**：`queries` 命令（CDP 探测 3 个 what-to-sell 真实端点 + CSV/JSON 导出）；`analytics_upload.py` daemon thread fire-and-forget 上报（无 token 跳过/失败降级）；discover `--blue-ocean-source` 从 all_queries CSV 反哺蓝海评分（competitor_keyword_density 因子）。
- **C6 埋点**：`shop_usage_stats` 表（ozon_client_id+stat_date 唯一）+ task_processor 3 处终态钩子（成功/异常/重试耗尽）增量写入（task_count=执行次数含重试；common_errors 当日 top-5 JSONB；成功路径不增）。
- 竞品插件（maozi）调研落地：明文 JSON 上报，不复制 gzip+AES 加密链路；不上传 cookie/PII。

### Fix(worker+skill 类目匹配四重优化)

- **末级词搜索**：`specific_terms = cat_terms[-2:] → [-1:]`——「科教玩具 其他益智玩具」分词后「玩具」token 稀释末级词信号，sim 0.5→0.333 错配甜品套装；只留末级词整体 sim=0.5 命中益智游戏。
- **LLM max_tokens 10→4096**：deepseek-v4-flash 推理模型 reasoning_tokens 吃光 max_tokens 配额 → 输出恒空 → LLM fallback 恒失败（用户观察「LLM 查询成功率很低」的直接根因）；`_llm_rank_categories` 结构化 JSON 输出（candidate_index + suggest_keywords），候选都不合适时建议词二次搜索。
- **merge 保高 sim**：`_merge_candidates` 同 dc/tp 保留 similarity 高版本——源搜索 sim=0.80 正确类目被全标题 sim=0.455 覆盖致 `_acceptable_match` 误拒。
- **同义词表 +10**：益智玩具→益智游戏/教育游戏/教学玩具、封口夹→密封夹、洗碗海绵→清洁海绵等高频映射。
- **skill search_text 末级词**：cloud_probe 优先用 1688 类目末级词（非长标题）查 ZH_HANS 树 + 提前提取 source_category_path 修复 UnboundLocalError。
- **实测**：竹知了玩具从类目阻断 → approved（product_id 5895655339）。

### Feat(worker Sentry 用户上下文)

- `_token_fingerprint` 脱敏（前 8 位 + sha1 前 6，不泄露明文）；`capture_task_error` 加 token 参数 → `scope.set_user({id: tenant_id, username: token_fp})` + `mxou_token_fp` tag；mxou_api chat/image 403/429/5xx 错误分支设 token 上下文——Sentry 从此可按用户筛选错误（此前 user 全 null，无法定位「哪个账号余额不足」）。

### 测试

- 新增 25+ 测试：test_attr_defaults_wave1（20）/test_fb_insta_handling（15）/test_resolve_weight_dimensions（6）/test_rich_desc_4191（5）/test_shop_usage_stats（12）/test_analytics_endpoints（9）/test_llm_suggest_rerank（4）等。
- 全量 worker pytest **537 passed**（0 失败；原 3 个基线失败为本地缺 pytest-asyncio/psycopg2 + PG URL，环境补齐后全绿）；skill pytest **174 passed**（1 个 pre-existing 环境基线 test_runtime_probe）。
- 真实链路：竹知了 1688 产品类目阻断→approved（5895655339）；queries 命令 51 行真实数据→上报→PG 51 行；C5/C6 端点 + 埋点实测。

## [0.30.0] - 2026-08-08


> hyperplan 对抗规划落地：worker 属性匹配修复（retry 止血 + fetch-back 回读闭环 + 学习 provenance）+ skill runtime 稳定化（顶层 preflight + CDP 统一）。规划文件：`.omo/plans/attribute-matching-runtime-stability.md`。

### Fixed(worker retry 止血 — 上品成功率核心)

- **删 retry 盲补字典首值**（`validation_retry_loop.py` Step 2.5）：字典属性语义解析+API 搜索未命中 → 直接跳过，绝不取 `_dict_vals[0]`。首值=语义随机：8229 可能取同大类其他小类（「套娃」错配实证）、9782 可能取「Класс 1 爆炸物」→ BR_hazard_class1。与 assemble/prepare 已统一的「宁缺毋滥」纪律对齐。
- **revalidate 加危险品守卫**（对称 prepare_ozon_upload_node.py:1877）：9782 在 retry 阶段只放行「非危险」安全值，其余跳过重传。
- **revalidate 加 is_aspect 守卫**（`attribute_utils.is_aspect_attr` 新增，A4 首次落地）：schema `is_aspect=true` 属性（交付出仓后不可改）retry 跳过，避免 attributes/update 被拒白烧一轮。
- **recheck 对 rejected_unfixable 早退**：不再带旧 task_id 空轮询（省 API 调用）。
- **`/values` limit 5000→2000**（官方 max）；**`/values/search` ≥2 字符守卫**（官方 value 最少 2 字符）。
- **prepare post-fill 中文清零**（L952 必填 + L1083 可选）：主转换循环（L1982-1990）之后 append 的字典属性 value 含中文直接置空（dict_id 权威），堵住「双保险」的第三处漏修。
- **8292 移出 `_KNOWN_DEFAULTS_RETRY`** → 统一走 `attr_defaults.resolve_merge_card_default` 字典解析路径。
- **stale 注释清理**：9 处 `/v1/product/prices/update` → `/v1/product/import/prices`（旧端点不存在）。
- **assemble 8229 type_id 匹配中文清零（Sentry POUDING_OZON-AA/9Y/9T 等实证）**：`_validate_and_enrich_items` 的 8229 type_id 分支从 ZH_HANS dict_lookup 取值未过 `_clean_dict_value` → 「垂钓诱饵/桑拿香薰/儿童泡泡机」等中文 value 直达 payload（ozon_validate 拦截上报）。修复：type_id 命中时 value 含中文置空（dict_id 权威），与 prepare post-fill 一致。
- **zombie recovery 安全开关（Sentry 实证触发真实上架）**：启动清理会复活 `retry_count < max_retries` 的 failed 任务——本地/测试环境会用真实凭证重新上架旧任务（实测 4 个任务被激活真实上传）。新增 `SKIP_ZOMBIE_RECOVERY=1` 环境变量跳过全部复活；本地测试必开（`deploy/.env` 已加，容器内验证「任务保持 failed 不复活」）。

### Fixed(worker 学习闭环 — 切断 Goodhart 棘轮)

- **fetch-back 回读闭环（P0）**：`fetch_back_node` 在 approved 后调 `/v4/product/info/attributes` 回读 Ozon 真实存储值 → diff 发送 vs 存储 → 检出 dict_id 漂移/被擦除/Ozon 自动填默认（`attributes_with_defaults`）→ `attr.outcome` 结构化遥测。graph 路由：`成功 → fetch_back → learning_record`。
- **学习门收紧（R6）**：被 Ozon 擦除 + 自动填默认的属性不写入学习（「Ozon 没查这个字段」不再被学习成「这个值是对的」）。
- **9782 erase 事件入遥测（R7）**：IGNORABLE_CODES 保留（防 retry 死循环）但擦除不再静默。
- **学习 provenance（PR-6）**：`ozon_attribute_mappings.source` 列（learned_approved/default_fallback/retry_recovered/fetch_back_corrected）+ 幂等迁移；default_fallback 复用不增长 success_count（切棘轮）；prepare 按置信消费（retry_recovered 隔离、fabricated `[{name}]` 跳过）；`scripts/backfill_mapping_source.py` 历史回填（dry-run 默认）。

### Fixed(skill runtime 稳定化 — 链路体验)

- **顶层 `_preflight_runtime`**：Python≥3.12 + requests/websocket/PIL 探测，缺依赖立即精确提示 + return 1（不再链路深处炸 traceback）。
- **4 处 ModuleNotFoundError 精确归因**：缺依赖 → pip 指引；缺模块 → 升级指引（不再误导「版本过旧」）。
- **cmd_check 不 early return**：无浏览器也继续探测 Worker/MXOU/凭证，一次性全环境诊断。
- **graph/follow/image_search(cdp) 入口前置 ensure_chrome_cdp**：缺 Chrome 立即报错（不再等 enrich 60s）。
- **updater 跨进程锁**（fcntl/msvcrt on data/.update.lock）：并发 CLI auto-update 不再竞态破坏安装。
- **RATE_LIMIT_PER_MINUTE 默认 10→300**：与 AGENTS.md/.env.example 对齐（批量提交不再 429）。
- **CDP 统一**：profile 双轨消除（全部 `profiles/1688/default`）、删 legacy 无锁 Popen、find_tab 复用用户 tab 后立即 release（不再误关用户标签页）、`_check_1688_login_live` cookie 优先零 tab 检测（降级导航 5 分钟缓存）、`scripts/migrate_profile.py` 迁移脚本（dry-run 默认）。
- **follow 信封透传 `ozon_attributes_category`**（与 graph --ozon-ref-url 一致）：worker 类目一致性校验防跨类目属性错配。

### Refactored

- **retry schema 查询 PG 缓存优先**（`_get_attribute_schema` 先查 attribute_cache 再 Ozon 直查，与主路径共享缓存）；`/values/search` body 去掉 language 字段（官方无此参数，语言仅控制 fallback 链）。

### 测试

- worker 257 passed（新增 15：retry 盲填删除/hazard/aspect 守卫/unfixable 早退/8292/retry_count/fetch_back 7 用例）；skill 87 passed（新增 6：preflight 4 + updater 锁 2）。
- 本地 Docker 实测：graph 26 节点编译（含 fetch_back）、PR-1 守卫源码断言、fetch_back diff 端到端（mock /v4）、provenance 消费门全部通过。
- 遗留 14 个环境失败（PGDATABASE_URL/uvicorn 缺失）与本次改动无关（git stash 验证）。

### 已知边界（显式 scope-out）

- PR-2 assemble 大函数物理移动 → `docs/TODO-attribute-util-extraction.md`
- PR-5 attr_defaults 扩消费（9554/18270/9160 无实证不盲加）→ 计划文件
- D1 类目错路径 category-repair 节点 → `docs/TODO-category-repair.md`
- zombie recovery 复活 failed 任务在本地环境会误激活旧任务（真实上架）→ 本地测试需先清空任务表

## [0.29.3] - 2026-08-07

> 用户电脑 CDP 全崩根因修复(REALISTIC_UA 混装) + follow 前置 ensure Chrome + MXOU 余额本地直查 + 工具 Chrome 常驻。

### Fixed(REALISTIC_UA 混装, 今天所有抓取故障源头)

- **stealth.py 极简版删了 REALISTIC_UA, service.py 4 处裸引用未删**(1619/1800/1836/1923):
  一调即崩 `cannot import name 'REALISTIC_UA'` → CDP 图搜/探针/graph 抓取全挂 →
  降级 API → 匹配垃圾结果/无货源。修复: 4 处引用删除(真实 Chrome 用真实 UA,
  伪造 UA 反而增加检测风险, 与 v0.28.7 极简设计一致), 仅保留 STEALTH_JS 注入。
- **follow 未前置 ensure Chrome**: 命令出口关闭 Chrome 后, 下次 follow 抓 Ozon
  连不上 9222 → 空数据 → 图搜用错图/文字搜索兜底(错配货源根因之一)。
  修复: `follow_sell_cloud` Step 2 前 `ensure_chrome_cdp(port=9222)`。

### Changed(工具 Chrome 生命周期, 用户反馈体验一致)

- **独立 profile + 常驻**(去掉命令出口 close_tool_chrome):
  v0.28.6「用完即关」被反馈体验不一致——每次命令 Chrome 被关, 用户手动开的
  浏览器却能保持。常驻后登录态复用, 下次命令 CDP 可用即直接用;
  v0.28.4 常驻的坑(撞用户 Chrome 默认 profile 单实例锁)已被独立 profile 消除。
  close_tool_chrome() 保留但不自动调用。

### Feat

- **MXOU 余额本地直查**(config_store.fetch_mxou_balance): check 命令显示余额/
  欠费标红预警, 与 Worker 鉴权同源(欠费 key 提前暴露, 不再白跑生图)。

## [0.29.2] - 2026-08-07

> 描述拉丁字符修复(Sentry POUDING_OZON-60 实证) + Sentry 任务重跑监控 + 生图提示词线上配置入库。

### Fixed(描述含拉丁字符, Sentry 实证三层根因)

- **validate 误报主因**: `ozon_validate_node` 的 `[a-zA-Z]` 直接检测整个 description,
  规格表 HTML `<table class="ozon-spec"><caption>` 标签本身是拉丁 → 凡有规格表的
  描述必报「描述含拉丁字母」。修复: 检测前剔除 `<[^>]+>` 标签, 只查正文。
- **富文本描述(4191)漏清拉丁**: `_sanitize_rich_description` 注释说清拉丁但代码
  漏了(HTML 标签含拉丁, 当初跳过) → 补 `[a-zA-Z]{2,}` 正文清理。
- **规格表属性值污染**: `_append_spec_table` 在净化后追加, 属性值(Black/USB/One Size)
  不经净化直接进描述 → 追加前清理, 清空跳过该行。

### Added(监控)

- **Sentry 任务重跑监控**: `capture_task_event` 通用事件上报; 三监控点——
  启动 zombie 恢复(`zombie_reset`) / 超时 stale 重置(`stale_running_reset`) /
  重跑任务 `retry_count>0`(`task_rerun`)。Sentry 搜 `task_event:*` 可查
  「是否偷偷跑任务/异常重跑」。

### Changed

- **生图提示词以线上配置为准入库**: main/scene_1/scene_2 无「均禁止」后缀(按线上),
  scene_2/3 修「任何、水印」笔误; JSON + `_DEFAULT_PROMPTS` 同步(漂移测试过)。
- Sentry MCP OAuth token 已配入本机 `~/.reasonix/config.toml`(1h 过期, 需刷新)。

## [0.29.1] - 2026-08-07

> 服务器 v0.29.0 部署实测 3 修复 + gha 构建缓存 + tests 进部署包。

### Fixed(服务器实测)

- **① deploy.sh latest 标签**: build 后显式 `docker tag v{VERSION} latest` + `up -d` 带
  `VERSION` —— 原 up 无 VERSION 落到旧 latest 镜像(服务器跑旧 0.27.0)。
- **② 多阶段镜像 PYTHONPATH**: compose 的 `PYTHONPATH=/app/src` 覆盖镜像
  `/opt/venv/...` 导致 uvicorn/依赖找不到容器重启 → 改为
  `/opt/venv/lib/python3.12/site-packages:/app/src`。
- **③ 部署包含 tests**: cd.yml 打包不再排除 `worker/tests`(服务器可跑 pytest)。
- **④ cos-deploy manifest url 空字段**: manifest 生成 step 补 `COS_BUCKET/REGION` env。

### Changed

- **worker 镜像 gha 构建缓存**: ci.yml/cd.yml `docker build` → `build-push-action` +
  `cache-from/to: type=gha`(pip/基础镜像层跨发版复用, 5-10min → 1-2min)。
- 宝塔环境 `stop_grace_period` 若被面板丢弃属环境限制, 原生 Docker 生效
  (Docker 28 + compose 2.32 实测支持, 见 DEPLOY-CHECKLIST.md)。

## [0.29.0] - 2026-08-07

> CI/CD 规范化 + Worker 稳定性(PRD-cicd-stability 第一批 7 项)+ cos-update.sh 一键升级。

### Worker 稳定性

- **P0 并发竞态修复**: `_current_task_id` 模块级 global → `contextvars.ContextVar`
  (asyncio 多任务并发不再串号; 新增 4 断言并发测试)。
- **优雅关闭**: `SHUTDOWN_FLAG` + lifespan drain(轮询 PG running 数, 最多 5 分钟)
  + docker-compose `stop_grace_period: 5m` —— `--force-recreate` 不再强杀运行中任务。
- **Dockerfile 多阶段构建**: builder(编译链) → runtime(libpq5+curl),
  镜像 ~800MB → ~400MB。

### CI/CD

- **CI 合并**: 单一 ci.yml(worker-ci.yml 删除, push 不再双跑); 去 `|| true` —
  worker ruff 全规则阻断(修 1 错), skill ruff `--select F --ignore F821` 阻断
  (ruff --fix 修 skill 160 处 + F841 下划线)。
- **gitleaks 密钥扫描**(近 3 个月, 避开历史已泄露基线)。
- **pip-audit 依赖漏洞**(警告模式)。
- **cd.yml cos-deploy**: tag push 自动打包 deploy/+worker/ 源码 →
  COS `/ozon-worker/` + manifest.json(sha256; tar 排除 .env/旧包/凭证)。
- **deploy/cos-update.sh 一键升级**: 读 manifest → sha256 校验 → 备份 →
  覆盖(保留 .env) → 优雅重建 → 健康检查失败自动回滚。

## [0.28.6] - 2026-08-07

> Chrome 独立 profile 根治「无限重启」+ 登录态持久化 + env-setup 文档更新。

### Fixed

- **Chrome 无限重启根治**(用户电脑 Windows/mac x86 实测复现):
  - 根因: check/discover 直接 ensure 用**默认 profile**(用户 Chrome), 用户 Chrome
    无 CDP 占用 profile → 杀不掉(只杀带 --remote-debugging-port 的实例)→ 启动
    新实例撞单实例锁 → 反复杀/重启失败; service 探针(graph/follow)早已用独立
    profile, 两条路径不一致。
  - 修复: `_default_profile_dir` → skill `data/browser/profile`(独立, 不碰用户
    Chrome, 避开 macOS TCC; data/ 随更新保留登录态)。**用户 Chrome 永不被杀/重启**。
  - 进程活着检查改 `Popen.poll()`(不依赖 ps/PowerShell 命令行解析, Windows/mac
    命令行截断/沙箱禁 ps 都不再误判)。
  - cli.py/batch_test 出口 finally `close_tool_chrome`(用完即关, 不常驻不累积;
    v0.28.4 常驻方案在「用户 Chrome 常开」场景死锁)。
  - 启动命令加 `--disable-crash-reporter`/`--disable-breakpad`(Crashpad 固定写
    用户 Chrome 目录, 权限受限环境导致启动崩溃)。
  - **登录态持久化**: cookies 存磁盘 profile, 关浏览器不丢, 重启自动恢复(已实测)。
- chrome_launcher 移回明文(编译态无法热迭代, cloud_probe 同款先例)。
- B1 补提交: 8 辅助生图节点 max_retries 2→1(a128249 遗漏, v0.28.5)。

## [0.28.5] - 2026-08-07

> Worker 八项优化(基于 Ozon 三店审计 317 商品/65 问题/20.5% 问题率, 本地 wave5 实测 5/5 上架成功)。

### Added

- **A1 错误码映射补全**: REPAIR_STRATEGY 补 9 个未映射错误码(审计 50 次错误不再 LLM 盲修):
  - `VALUE_MUST_BE_INTEGER`/`VALUE_MUST_BE_DECIMAL`/`ATTRIBUTE_VALUE_COUNT_EXCEEDED` → repair_prepare
  - `EMPTY_REQUIRED_AFTER_WARNING_DELETING`/`warning_attribute_values_empty`/`erased_attribute_value`/`CONDITIONAL_ATTRIBUTE_ERROR` → error_repair_llm
  - `SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT`/`all_image_failed` → unfixable(不浪费重试)
  - `marking_auto_corrected` 明确化 → error_repair_llm
- **C1 skill query 命令**: `cli.py query <任务ID>` 查状态/耗时/产品明细(agent 不再盲等)
- **C2 失败中文 notice**: ERROR_NOTICE_MAP 17 码 → 中文可读失败说明, task_status error_message 直接可读
- **E1 原始图转存 COS**: `utils/cos_uploader.py`(boto3 S3 兼容 COS), AI 生图全失败时下载原始图转存 COS 补位(未配置 COS 优雅降级)

### Changed

- **B1 生图重试 2→1**: 8 个辅助生图节点(comparison/detail/multi_angle/scene×3/social_proof/white_bg)max_retries 2→1, 主图保留 2(单商品省 8 次潜在 API 调用)
- **B2 提示词物流排除**: 10 条生图提示词统一加「物流信息/退换货说明/地址/电话/联系方式/店铺名称」排除(JSON + 模块默认同步, 热加载)
- **D1 draft_sanity**: 入队防线补 weight<=0/尺寸缺失或含 0 拦截(防 0 重量打爆定价)
- **A3 回归测试**: 核实 prepare 主路径已有标题/描述/属性值净化链(sanitize_title + 中文清除), 补 6 断言确认零拉丁/中文

### 部署注意

- deploy/.env 新增 COS 凭证启用 E1: `COS_SECRET_ID`/`COS_SECRET_KEY`/`COS_BUCKET`/`COS_REGION`(可选 `COS_PUBLIC_DOMAIN`)
- 新增测试: test_repair_strategy_mapping(7) / test_notice_build(7) / test_title_sanitizer(6) / test_cos_uploader(6)

## [0.28.4] - 2026-08-06

> Chrome 跨平台重复开启修复 + 常驻复用 + 文档增强(0.28.2 后 4 个 commit 合并发布)。

### Fixed

- **Chrome 跨平台重复开启**(用户反馈"一直重复开启浏览器", Windows/Mac):
  - 根因1: ensure_chrome_cdp 持锁复查依赖 ps/PowerShell 命令行解析判
    --remote-allow-origins, 解析失败/截断 → 误判缺参 → 杀 Chrome → 重启 → 循环。
    → 删除两处"缺 allow-origins 杀重启", CDP 可用即信任(连接 403 由调用方报错, 绝不杀浏览器)。
  - 根因2: 工具 Chrome 用完不关, 用户手动关后下次又新开。
    → v0.28.3 PID 追踪用完即关 → **v0.28.4 改为常驻复用**(更优):
    check 启动后引导登录 1688 + Ozon Seller, 浏览器保持常驻, 后续命令 CDP 可用即复用,
    不再每次弹新窗口; 用户手动关闭后下次命令自动重启。
  - check 新增登录引导: 未登录 1688 自动打开 login.1688.com + seller.ozon.ru
    (discover 运营指标需要卖家登录), 交互等待登录后 Enter; 非交互提示重跑。
- 用户反馈 6 项: env-setup pip3.12 修正 / output-schema 补 product_summary 字段 /
  error-codes check_task_status 同步提示 / C-D 澄清(--auto-submit 显式参数)。

### Added

- command-reference 并发限制表(Chrome 单实例串行/1688 配额/batch_test 内置 delay)+ 批量操作规则。
- error-codes 错误恢复决策表(每错误码下一步 + 自动重试最多 1 次)。
- output-schema submit_result/check_task_status JSON 示例 + status 取值/终态判定。
- SKILL.md 管线 E 市场信息三级降级(web_search → SEARXNG_URL → 问用户手动提供/退回 C)。

### Tests

- skill 81 passed; 行号引用(5 处)与代码逐一核对无误。


## [0.28.0] - 2026-08-06

> Skill 架构重构版(执行 PRD Phase A-F + 两轮 agent 实测修复)。Worker 代码零改动。

### Added

- **references/ 渐进式披露**:SKILL.md 455→150 行骨架(意图路由/速查表/边界/越界/索引/更新),
  拆 4 个专题文件:command-reference/error-codes/output-schema/env-setup;compile.py DOC_FILES
  含 references/(复制支持子目录)。
- **frontmatter 合规**:agent_created + description 第三人称 + version(与 VERSION 一致)。
- **口语化意图路由**:真实用户表达全覆盖——「帮我上产品/选50个/上点/有什么好卖的/昨天那个再上一遍/
  整一批夏季爆款」;口语动词归一、无对象追问(③a)、数量词规则、复合意图消歧、重上已上商品防重复、
  图搜→上架确认衔接、URL+弱化词先 --no-submit。
- **跨 Agent 兼容**:python3.12→python3(38处)+ §0 定位 Skill 目录(SKILL_DIR/当前/上级)。

### Fixed

- 蓝海触发词冲突(command-reference vs 决策表相反)→ 统一「蓝海→C,蓝海趋势/市场分析→E」。
- 「C/D 区别只在 follow_type」不实声明删除(discover 无此参数,跟卖标记内部注入)。
- frontmatter description 与决策表语义不一致;batch_test 凭证来源说明;声称数量与链接数不符追问;
  check 故障表;审核被拒 vs 提交失败区分;管线 E 无搜索兜底。

### Tests

- agent 实测两轮:工程化 9 场景(修复 9 项)+ 真实口语 10 场景(修复后 10/10 可路由)。
- skill 全量 81 passed;deploy/skill 同步一致。


## [0.27.0] - 2026-08-06

> 合并 v0.26(生图额度/队列/帽类属性)+ v0.27(信封治理/类目修复/出参审核状态)。
> 本地 Docker 真实上架验证(挂脖风扇/风扇帽/蚊香盘 3 个 approved),wave4 实测三修复。

### Added

- **信封治理标准(ENVELOPE-STANDARD.md)**:skill→agent→worker 三方数据边界——🅰 判定层
  (运营数据)不进信封、🅱 worker 执行层必须全消费、🅳 出参标准;逐字段核实(删除前
  验证 worker 实际消费)。
- **出参审核状态(v0.27)**:product_summary 补 `ozon_status`(approved/pending/declined)+
  `ozon_error`;OzonStatusInput + GraphOutput 补 moderation_status(output_schema 过滤
  致终态丢审核状态的根因)。
- **学习表治理(v0.27)**:删污染(甩脂机品牌ID 101029485/手串错配,66% 污染率实证)、
  写入前 dc/type 树存在性校验、查询门槛 success_count≥3。
- **方案B 直采类目(v0.27)**:skill poll_category=True + search_categories 语言自动选择
  (中文→ZH_HANS);worker `_resolve_skill_category` 校验采用 Seller 空间类目(跳过 pg_trgm 猜)。
- **性别双值兜底通用化(v0.27)**:9163 或属性名含 пол/性别/gender 的必填属性一律
  「中性词→男+女双值」兜底;dictionary_id=0 也强制进 required_dict(帽子类实证)。
- **品牌 31 补全**:BRAND_ATTRIBUTE_IDS=[85,5076] 缺 31(服装和鞋类品牌,帽子类必填)。
- **v0.26**:生图额度暴烧修复(P0-1 图内 pending 死循环/P0-2 队列无限重跑有界化/
  P0-3 生图失败分类不重POST/P0-4 task_generated_images 生图幂等)、帽类 9163 男+女双值、
  数字属性类型校验(8205/11650/4497/7444)、字典属性全量填满、工具链(offline_validate/
  analyze_category_mismatch/audit_products/Sentry 全局监控)、wave1/2 实测修复
  (seller analytics 借道/premium 解锁/退货率/图搜徽标降级/空壳跟卖拦截/定价统一公式/
  假成功透出/尺寸方向反转)。

### Fixed

- wave4 实测三修复(本地 Docker 真实上架):9163 dict_id=0 被筛出 required_dict →
  性别兜底失效 → 缺 9163 被拒;品牌 31 缺失;GraphOutput 缺 moderation_status →
  ozon_status 出参恒空。
- test_mxou_polling 同步 v0.26 超时抛异常(v0.25 断言过期)。

### 信封清理(v0.27)

- 删除无用字段:stock×5、max_skus/dropped_skus/drop_reason/filtered_skus;
- 10 个竞品运营字段移出信封(属判定层,skill 采集/展示保留);
- 保留 worker 兜底物理字段:competitor_weight_g/competitor_dimensions_mm;
- shipping/price/original_price 标记废弃保留(展示/折叠内部用)。

### Tests

- 新增 test_skill_category_direct(5 用例)/test_ozon_status_mapping(5 断言)/
  test_gender_attr_with_dict_id_zero(1 用例);全量 211 passed。


## [0.25.0] - 2026-08-05

> wave3/wave4 本地真机测试 + 修复：跟卖/直采 12 个产品 11 个 approved（浴刷已救活），
> 全链路根因修复 + 尺码表入库 + COS 全球加速抓图。

### Added

- **尺码表入库（F1a）**：儿童/男性/女性服装 + 鞋子四张 CSV 随镜像分发，
  `init_data.py` 部署时自动导入 PG `size_mappings`（children 127 / female 104 /
  male 88 / shoes 34 行实证），`size_mapper` 查询优先（PG → 本地兜底）。
- **必填字典属性语义解析（F1b/F1c）**：`attr_defaults` 品牌/性别/尺码/8292/型号
  语义默认值，prepare/retry 统一接入；无颜色来源 → 中性默认色
  （прозрачный→белый）；性别无来源 → Унисекс/Универсальный（列表模式关键词兜底）。
- **1688→Ozon 类目映射学习闭环（T1）**：skill 信封补 `source_category_id`，
  worker follow/直采优先查学习表，approved 后回写。
- **必填字典属性 live search 兜底（T2/T3）**：`/values/search` 公共封装，
  解析失败必走 live search；非必填字典属性 attr_synonyms 跨语言同义词填满。
- **商品描述规格参数表（T4）**：4191 富文本追加 Характеристики 表格。
- **AK 搜索结构化字段 + 趋势选品（S1/S2/S3）**：moq/48H发货率/销量/发货地/标签 +
  筛选参数；信封补 1688 类目数字 ID；`trend` 命令（web 趋势→AI 细分关键词→
  AK 搜索满 3 停）。
- **Ozon 竞品属性优先填充（v0.25 wave3）**：follow 信封透传 `ozon_attributes`，
  worker 按竞品俄语值搜索 → 1688 推断 → 兜底；8229 短词搜索；标题颜色推断；
  裤袜/女性专属品类词；One-size 尺码兜底；8292 自由文本「Нет」最后兜底。
- **生图模型路由（v0.25）**：main/social_proof 用 gpt-image-2，其余 banana；
  grsai 轮询前 30s 不轮询 + 每 5s 一次；提示词 v0.25 修订热加载。
- **Sentry 错误监测（v0.23）**：SENTRY_DSN 可配，任务异常/超时自动上报。

### Fixed

- **跟卖上传禁竞品图（wave4 浴刷 0 图下架根因）**：AI 图不足 10 张不再用
  竞品 ir.ozone.ru 补位（Ozon 抓竞品 CDN 图失败 → 整卡 0 图被下架）。
- **审核通过后路由死循环（wave4 实证）**：条件边收到 OzonStatusInput 强转 state，
  moderation_status 被剥 → approved 永远看不到 → 每秒重跑 ozon_status；
  兜底 imported+success+product_id → 成功。
- **ozon_status 404 回退**：import/info 任务不存在（product_id 当 task_id 轮询）
  → 回退 info/list 查真实状态，不误判失败；404 回退查无商品立即失败。
- **OzonStatusInput 补 ozon_task_id（d897efd）**：修复 import/info 阶段被入参过滤
  剥掉导致的 product-id 误用；校验失败返回真实 product_id。
- **制造商 23487 中文零容忍**：中文供应商名必须俄语化（LLM 翻译，失败兜底
  「Китайская компания」）——中文供应商整单被 Ozon 拒（BR_chinese_hieroglyphs）。
- **COS 全球加速域名抓图**：payload 统一改写 `cos.accelerate.myqcloud.com`
  （Ozon 跨境抓图更稳，wave4 图抓取失败频发实证）。
- **offer_id 统一裸竞品 ID**：import-by-sku/assemble/prepare 三处一致，
  防 api 复制模式双卡；import-by-sku 超时不 fallback CREATE（P2a）。
- **店铺路由（F4）**：`_get_ozon_credentials` 显式 OZON_CLIENT_ID/API_KEY 环境
  变量优先——batch --client-id 被 stores.json 默认店覆盖问题。
- **竞品兜底安全（P3）**：int 安全转换 + 部分尺寸缺失兜底；repair_pricing 无定价
  阻断（删 999 兜底，P2b）；api 兜底信封校验放宽（P1）。
- **Docker pytest-asyncio**：容器全量回归可运行（170+ 测试全绿）。

## [0.22.0] - 2026-08-04

> 经营数据闭环：worker 拒绝原因可行动化 + 完成结果返回产品经营明细，
> skill 提交后可选轮询展示（--wait）。

### Added

- **完成结果产品明细（v0.22）**：worker 任务完成后，`task_status` 的 result 增加
  `product_summary` 数组，每个产品一条：1688 采购链接、利润率（margin_rate）、
  售价、采购价、运费预估（logistics_cost）、净利润率、Ozon 商品ID；
  多 SKU 变体每变体一条。GraphOutput 契约同步新增 `product_summary` 字段。
- **提交拒绝原因可行动化**：skill `submit_envelope` 不再吞服务端错误——
  解析统一错误格式（error_code/message/detail）与 FastAPI detail，
  返回结构化原因，agent 可直接看到「token 无效/余额不足/配额不足/信封异常」及解法。
- **batch_test --wait**：批量提交后轮询任务终态，逐产品打印
  1688链接/利润率/售价/采购价/运费预估；新增 `--wait-timeout`。
- **worker 自修复升级（经验固化）**：
  - repair_prepare 尺寸单位改 cm/mm 交叉判定（旧"密度<1.0 无差别/10"会把
    修车躺板 1100mm 错砍成 110mm）；
  - `price_out_of_range` 映射到 repair_pricing（原走 LLM）+ 取价修正
    （pricing_info 实际键是 price，旧代码取 final_price 永远落空）；
  - 字典值搜索语言链 ZH_HANS→RU→EN（Ozon 字典值是俄语，旧 ZH→EN 搜不到
    8229「вентилятор→Hand Fan」）。
- **Windows 体验**：chrome_launcher 进程检测 wmic→PowerShell（Win11 弃用 wmic
  导致频繁启动新实例）+ profile 目录自动创建（缺失会开全新浏览器无登录态）。
- **图搜**：输入框选择器兼容新版页面 `.ali-search-input`（旧 `#alisearch-input`
  不存在 → 输入失败 → 空输入点搜索误触上传图片按钮）；点击前校验 URL 已填入；
  3 次分段滚动合并候选；RU→ZH 产品词映射扩充（套筒/撬棍/水平仪/风扇/套装等）
  + 标题相关性多词加权；无徽章降级阈值 conf 0.4→0.3。
- **discover 品牌过滤**（参考 maozi 插件 brand_option）：用 Ozon widget API
  （product id 查询）返回的 brand 字段（英/俄文）布尔判断——`без бренда`/空 =
  无品牌，其它（含白牌）算品牌；`--brand-filter` 三档：nobrand=只要无品牌/白牌
  （默认，规避品牌侵权）、known=只过滤知名品牌黑名单（Nike/Apple/博世 等 60+）、
  all=不过滤。命中直接 filtered 跳过，不浪费 1688 匹配/图搜/生图资源。
- **image_search --source cdp**：CLI 支持 CDP 网页版图搜（默认 ak=1688 AK API；
  需要 Chrome 登录 1688 时用 cdp，准确率更高）。
- **seller.ozon.ru 登录态检查**：check 命令区分两个登录态——www.ozon.ru
  （选品/DataDome）与 seller.ozon.ru（卖家后台，运营数据依赖）；seller 未登录
  时明确提示登录卖家后台；discover 运营数据全部缺失时同样提示
  （agent 需要月销量/销售额/增长率判断选品）。
- **token 引导**：set_token 输出提示访问 https://api.mxou.cn 注册获取。
- **竞品数据闭环（参考 maozi）**：follow 跟卖时借道 seller.ozon.ru
  what_to_sell 获取竞品**重量(4497)/尺寸(9454/9455/9456)/月销/GMV/上架天数**
  透传信封；worker assemble 在 1688 重量缺失或尺寸全 0 时用竞品值兜底
  （`apply_competitor_fallback`），降低 INCORRECT_DIMENSION/价格失真。
- **跟卖双模式（参考 maozi follow_type）**：`extensions.follow_type` 二选一——
  `hand` 防侵权跟卖（**默认**）：跳过 import-by-sku 1:1 复制，走 CREATE 重建
  （我们管线重做类目/属性/生图，天然防同款/侵权检测）；`api` 强制跟卖：
  import-by-sku 复制竞品卡片（快但可能报错/被下架）。
  **触发规则**：有 1688 货源匹配 → hand 重建（默认）；图搜无匹配（无货源）
  → skill 自动组装 api 信封（import-by-sku 复制竞品，不丢单，result 标记
  api_fallback 供 agent 知晓）；worker 兜底——hand 信封缺货源数据
  （无 purchase_url/purchase_cost）自动降级 api。
- **跟卖防双卡（offer_id 统一）**：import-by-sku 的 offer_id 从 `竞品ID` 改为
  `follow_{竞品ID}`，与后续 upload 一致——旧代码两者不一致导致 api 模式
  import-by-sku 建一张卡、ozon_upload 又 CREATE 一张（双卡 bug）；import-by-sku
  轮询 30s→60s 降超时 fallback 双卡概率。
- **余额判定根因修复**：`_check_mxou_balance` 统一查**用户级 users.quota**——
  unlimited 分支不再返回 key 级 tokens.remain_quota（僵尸字段，同账户两 key
  一正一负 +4.4亿/-5808万误导）；users 查询失败/无记录不再降级 remain_quota
  （负数会误报「有余额却余额不足」），unlimited 放行、非 unlimited 拒绝（数据
  异常暴露，宁缺毋滥）。测试 10/10。

## [0.21.0] - 2026-08-04

> 48 商品端到端实测暴露的三类根因修复：类目错配（13/16 declined）、
> 假成功/学习缓存固化（declined 被当 success 写进 category_mapping）、
> 9782 危险品等级被兜底填成"爆炸物 Category 1"（BR_hazard_class1）。

### Fixed

- **尺寸/重量根因修复（P2，2026-08-04 实证）**：1688 抓取尺寸单位误判 +
  density 兜底放大，导致挂脖风扇 300g→30.4kg、工具套装 400g→364kg、
  修车躺板 5200g→82.5kg，价格分别炸到 2134/25290/5837 CNY。
  - skill probe fallback 容器补 `module-od-product-attributes` /
    `module-od-product-description`，body 行正则补「规格/体积/外观尺寸/包装体积」，
    带单位候选（如「规格 8.5*6.5*11cm」）优先于无单位值；
  - `cloud_probe` 新增 `extract_dimensions_from_texts`（带单位优先、mm 不乘 10、
    前缀/后缀单位都认、单边 >5m 拒绝）与 `resolve_packaging_dimensions`
    （cm/mm 交叉密度判定：按 cm 密度 <0.1 且按 mm 在合理区间 → 切 mm）；
  - density 兜底：商家已提供真实重量时**不再用体积×0.5 覆盖**；
    无商家重量才估算且封顶 30kg。
- **worker 入队防线（P2）**：新增 `utils/draft_sanity.py`，weight>50kg 或
  单边>5m 的信封在 submit 直接 INVALID_REQUEST；pricing_node 对超限重量打
  `weight_suspect` 标记并告警，防脏数据再打爆定价。
- **跟卖类目缺失可观测（P3）**：import-by-sku 成功但类目解析失败时不再静默，
  返回 `category_missing=true` 标记（不阻断，类目由官方复制带出）；
  follow_sell_v5 测试同步到 v0.14/v0.19.1 行为（competitor_price 字段、
  import 失败才报类目错误）。

- **成功判据收紧（P0-1）**：learning_record 只认 `moderate_status=="approved"`；
  删除 `upload_status=="success"` / imported / active / processed 强制成功分支；
  retry 循环"imported 即 success"、"pending+product_id 视为成功"、"不可修复标 success"
  三处假成功路径改为 pending_moderation / rejected_unfixable；
  新增 `scripts/clean_category_mapping.py` 一键清理旧污染学习缓存。
- **9782 危险品等级安全兜底（P0-2）**：删除必填字典属性"取第一个字典值"兜底；
  危险属性只挑「非危险」安全默认（get_safe_hazard_default），取不到则跳过；
  普通属性仅字典值唯一时才兜底；prepare 层加防御纵深。
- **类目匹配修复（P0-3）**：外置同义词表 `config/category_synonyms.json`
  （震动棒→振动器、后视镜→摩托车后视镜、折叠椅→户外折叠椅 等）；
  末级类目词（含同义词）命中节点名 +0.5 打破 tie；
  L0 学习缓存命中必须与 L1 top5 候选一致（防旧脏数据固化）；
  jieba top1 全泛化词命中返回空触发 L3；
  skill 信封改传完整 1688 类目路径（不再截断末两级）。
- **skill 信封数据完整性（P1-1）**：尺寸缺失时标记 `dimensions_estimated`，
  worker assemble 显式告警（不再静默估算硬传）。
- **生图禁文字（P1-2）**：10 个生图 prompt 统一追加"严禁任何文字/水印/价格/促销字样"，
  默认提示词同步（防 4195 图片含配送信息被拒）。
- **batch 429 退避（P1-3）**：batch_test 提交遇到 429 指数退避重试 3 次（30/60/120s）。

### Tests

- 新增 `test_learning_record_gate.py`（5 用例）、`test_hazard_attr_fallback.py`（7 用例）、
  `test_category_match_v021.py`（5 用例）、`skill/tests/test_envelope_fields.py`（2 用例）。

## [0.20.0] - 2026-08-04

> 跟卖 0 图根因修复（A）：真实测试发现甩脂机类商品「图生成成功但卡片 0 图」——
> 根因是类目类型无效（品牌页被当类目）导致 Ozon 整包拒绝 import，图片根本没机会应用。

### Fixed

- **Skill 类目路径净化**：`category_path` 只拼 `/category/` 类目 crumb（排除品牌页
  Luxhommè 类），worker 的 pg_trgm 提示词不再取到品牌段
- **Worker 跟卖类目全链路修复**：
  - `follow_sell_import_node`：类目解析失败**绝不保留原始值**（品牌页 ID 不再被当有效类目）
  - `assemble_ozon_product_node`：数字类目必须通过类目树校验才采用；类目不可用时
    **直接走跟卖组装（省略类目，UPDATE 由 Ozon 保留原卡片类目）**，不再掉进 1688
    类目匹配（曾匹配出无效 dc/type 对 17028706/971301594 被整包拒）
  - `prepare_ozon_upload_node`：跟卖 UPDATE 类目为空时**省略字段**（不传 0）
- 单测：`test_ozon_category_fix.py` 新增品牌排除/路径净化用例（7/7 通过）

### Pending（后续版本）

- B：`warning_all_image_failed` 自动重传一次（直上偶发拉图失败自愈）
- C：ozon_status 用真实 import task_id 轮询 + pending 超时上限
- D：Ozon 风控限速 + 跟卖 import-by-sku 真假成功判定

## [0.19.2] - 2026-08-03

### Fixed

- **task_statistics v1 路由恒 0（Worker）**：`/api/v1/task_statistics` 声明了
  `TaskStatisticsResponse` 响应模型却把 `{status, statistics}` 整个返回，字段对不上被
  Pydantic 填默认值 → 统计接口全 0（旧路径 `/task_statistics` 正常、v0.19.0 的字段
  映射修复被这个解包 bug 挡在路由层）。v1 改为解包 `statistics` 后返回。
- **COS 上传无限挂死（CI）**：coscli 无请求超时，跨境上传 TCP 黑洞会无限阻塞
  （v0.19.1 的 build-skill 在 Upload 步骤挂 8 小时被手动取消）。build-skill 与
  skill-distribute 上传均加单次 600s GNU timeout，3 次重试有界。

## [0.19.1] - 2026-08-03

> 真实测试暴露的跟卖断链修复（P0+P1）：竞品类目缺失/错取导致跟卖失败；
> 参考上品帮/maozi 插件逆向结论，修类目解析 + 复用 1688 来源类目兜底 +
> 竞品信息透传。P2（销量/上架时间，卖家后台接口）探针验证中，随 v0.20。

### Fixed

- **Skill 类目解析 Bug1（品牌页当类目）**：面包屑挑类目改为只认链接含 `/category/` 的 crumb，
  品牌页（`/brand/`，crumbType 同为 CRUMB_TYPE_FULL_LINK）一律排除——甩脂机此前把
  品牌 Luxhommè(101029485) 当类目，现在正确取 Мини-тренажеры
- **Skill 类目解析 Bug2（breadCrumbs 缺失零兜底）**：entrypoint API 改为**纯数字 ID 优先**请求
  （插件实证稳定），缺失时自动回退 slug 版本；评分/评论/卖家/提问/跟卖信息一并解析（P1）
- **Worker 掐死官方通道（Bug3）**：`follow_sell_import_node` 中 import-by-sku 成功（拿到
  product_id）→ 不再强制要求类目（Ozon 官方复制自动带出）；Fallback CREATE 才需要类目，
  缺失时用 1688 来源类目/标题 pg_trgm 兜底（复用 direct 管线引擎）——本地实测棘轮扳手
  「棘轮扳手」→ Ozon 类目 Трещотка(17028653/92147) 成功过类目关

### Changed

- 竞品信息透传进信封 extensions（可选字段，契约兼容）：跟卖数/最低价/评分/评论数/提问数/卖家

### Pending（v0.20）

- P2 销量/上架时间：seller.ozon.ru 卖家后台接口（search-variant-model 等）实探为 403/404，
  需从插件请求报文反向精确报文后接入（seller 登录态已确认可用）

## [0.19.0] - 2026-08-03

> 真实上架 E2E 测试（2026-08-03，7 链接）暴露的问题修复：1688 直接上架 4/4 成功；
> Ozon 跟卖 0/3 全部被图搜护栏误拒（根因：matchBadgeFull 徽标静态文本为空 + 只取前 5 张卡
> + 五金词映射缺失）。本版一并修复生图频繁降级 banana 与上架统计不可用。

### Fixed

- **图搜护栏误拒（Skill）**：全匹配徽标 `matchBadgeFull` 静态 `textContent` 为空（hover 才显示属性级原因）→ 改为按 class 识别为「全部符合」（最高分 100/1.0，直接放行，不再被标题相关性否决）；`page_size` 5→20 + 结果页多段滚动（实测 60 张卡此前只取前 5 张，1/3、2/3、FULL 卡全被忽略）；无徽标（未登录/未渲染）时按标题相关性降级（conf≥0.4 放行，用户确认可接受牺牲准确度）；补 RU→ZH 词映射（棘轮/扳手/活动头/两用/梅花/螺丝刀/钳/锤/电钻 + 甩脂机/抖抖机/减脂/音乐）；CDP 图搜空结果原地重试 1 次再降级 AK
- **生图频繁降级 banana（Worker）**：主模型 `gpt-image-2` 超时 90s→180s（9 个生图节点统一），主模型重试 4 次→3 次；主模型真失败才降级 `nano-banana-fast`；每次生图记录 model + 耗时日志（可审计降级率）
- **上架统计接口恒返回 0（Worker）**：`get_task_statistics` 字段名（`total_tasks` 等）与 `TaskStatisticsResponse`（`total` 等）不匹配，Pydantic 全填默认值 → 新增 `statistics_payload` 统一映射，`avg_duration_seconds`（上架耗时）恢复可查
- **task_status progress 陈旧（Worker）**：completed/failed 终态优先返回 100%，不再显示内存残留的中间阶段（如 0%/social_proof_gen）

### Changed

- Ozon 商品页 CDP 抓取补多段滚动（触发图片画廊/描述/评价懒加载）

## [0.18.0] - 2026-08-03

### Changed

- **Skill 自动更新升级为默认自动应用**：每次命令检测到新版本即自动备份 → 覆盖 → 失败回滚（`data/` 全程保留）；`SKILL_AUTO_UPDATE=0` 可退回「提示 + 手动 `skill update`」；源码开发目录（存在 compile.py）仍拒绝自动更新
- **分发链路重构（build-skill 直传 COS）**：打 tag 后 build-skill 产包 → 直传 COS + manifest → sha256/公网一致性校验，不再依赖 release 事件与 40 分钟轮询（实测旧链路 4 次运行全失败、release 事件零运行记录）；skill-distribute.yml 降级为手动兜底（`gh workflow run skill-distribute.yml -f tag=<ver>`）
- **仓库治理**：从 git 跟踪中移除运行时数据（skill/data 297 个文件）、部署包（4 个 tar.gz/zip）、Cython 中间产物（`*.c`）；补 `.gitignore` + pre-commit 阻塞规则 + CI repo-hygiene 检查防回归

### Added

- **旧包一键升级 bootstrap**：`scripts/bootstrap_update.py`（随包分发 + Release 附加资产），解决 v0.12.0 之前旧包无 updater、永远收不到更新的问题；cli 在缺 `scripts.cloud_probe`（旧包）时给出明确升级提示
- **updater 单测**：`skill/tests/test_updater.py`（11 断言，mock 网络 + 临时目录，无 pytest 环境也可独立运行）

### Fixed

- v0.17.0 COS 分发失败（上传 15 分钟超时）导致 v0.13~v0.17 修复未触达用户——v0.17.0 已补发到 COS，本版本起发布自动完成

## [0.17.0] - 2026-08-03

> v0.12.0 之后首个 skill 统一发版：补发 v0.12.0 遗漏的 skill 修复，并验证「tag → build-skill → release → COS 分发 → 自动更新」全链路。worker 侧 v0.13~v0.16 改动均已含在本次 tag（详见下方各自条目）。

### Skill 修复（v0.12.0 后累积，本次随包发布）

- **E4 裸 CDP 统一封装**（`b78fe64`）：4 处手写 websocket/CDP 全部收敛到 `cdp_client.py`，后续不再允许裸 `websocket.create_connection`
- **图搜弹窗双保险**（`400ce69`/`8231639`）：Chrome 启动加 `--disable-popup-blocking` + JS 层 `window.open` 覆盖，1688 图搜不再需要手动放行弹窗；另加多重新搜机制
- **图搜标题相关性护栏**（`93ddd1a`）：badge/标题相关性弱匹配不再组装信封（防不同产品跟卖错款）
- **COS 分发竞态修复**（`b9a0310`）：skill-distribute 轮询等待 build-skill 包就位，tag 推送不再出现「Release 已发但包没传上去」

### 发版链路（本次验证）

- 端到端验证自动更新：v0.12.0 老包 → `skill update` → v0.17.0（COS manifest 指向最新包，sha256 校验）

## [0.16.0] - 2026-08-03

> 属性填充增强：类目属性尽可能填掉 + 中文零容忍（标准俄语）+ 海关编码跳过。随 v0.15.0（生图提示词外置）一并部署。

### 属性填满

- **必填自由文本无默认值 → 跳过不写空串**（assemble `_validate_and_enrich_items`）：空串上传触发 `error_attribute_values_empty`，宁缺毋滥交给 retry 靶向修
- **可选字典属性补充增强**：多值属性不再一律跳过——① 本地产品标题词对 ZH_HANS 字典值包含匹配（仅唯一命中才补）② Ozon `/values/search`（RU）官方匹配兜底；匹配不到仍跳过（v0.13 关闭盲补首值的原则保留，避免"属性值不正确"）
- 海关编码属性（ТН ВЭД 等）从可选补充排除

### 中文零容忍（标准俄语）

- **`_russian_required_attrs` 翻译结果校验**（prepare L1241）：4191/4180/9048/4384/4389/23171 俄语翻译结果必须含西里尔且无中文，否则跳过该属性——修复拉丁值翻译失败仍直接上传的泄漏路径（「请用俄文填写该字段」）
- **9024(SKU) 不再豁免中文检查**：只豁免拉丁/数字直传，含中文一律翻译/跳过
- **`_generate_rich_description_fallback`**：1688 中文属性名原样拼 HTML 的泄漏（且结果不过 sanitize）→ 属性名/值含中文跳过该 `<li>`

### 海关编码（ТН ВЭД）跳过

- 新建 `worker/src/utils/attribute_utils.py`：`is_customs_attr(attr_id, attr_name)`（ID=22604 + 名称关键词 RU/ZH/EN）
- assemble 三处：1688 匹配不填 / 必填补全跳过（绝不标题搜索乱填 HS code）/ 可选补充排除
- prepare：`_skip_attrs` 按 ID 防御纵深
- validation_retry_loop：`SKIP_ATTR_IDS` 并入海关 ID（revalidate 重传也跳过）

## [0.15.0] - 2026-08-03

> 生图提示词外置配置 + 热加载：调提示词不再需要重新部署 Worker，只改配置文件即可。

### 生图提示词配置文件化（热加载）

- **新增 `worker/config/image_prompts.json`**：10 个生图节点（main/white_bg/multi_angle/scene×3/comparison/detail/social_proof/variant_white_bg）的中文提示词全部外置，与 v0.14 硬编码**逐字一致**（保持中文版，不换英文）
- **新增 `worker/src/utils/image_prompts.py`**：`get_image_prompt(key, **kwargs)` — 每次现读磁盘（无缓存）→ 改文件下一次生图即生效；文件缺失/JSON 损坏/渲染失败 → 回退模块级默认提示词，绝不抛异常阻断生图节点
- **10 个生图节点改造**：删硬编码 prompt 字符串，改调 `get_image_prompt`（Jinja2 模板，占位符 `{{title}}`/`{{scene_context}}`），其余逻辑零改动
- **`deploy/docker-compose.yml`**：worker 服务新增 `../worker/config:/app/config:ro` bind mount → 宿主机改任何 config JSON（含 LLM cfg）无需重建镜像/重启容器，下一次调用自动生效

### 运维方式变化

- **调生图提示词**：`vim ../worker/config/image_prompts.json` → 保存即生效（无需任何操作）
- 注：config 目录 bind mount 后，`docker compose build` 的 `COPY config/` 层不再影响运行时（宿主机文件覆盖）

## [0.14.0] - 2026-08-03

> 8·26 审计遗留修复四批全量实施（PRD: `docs/PRD-audit-fixes-20260803.md`，31 项验证 27 属实 + 4 部分属实）。

### 批次 A：上架正确性（P0/P1，每单必现）

- **P0-2 跟卖属性链路接通**：`_assemble_follow_sell` 消费 follow_sell_import 输出的 final_attributes（统一 attribute_id 键）；删除硬编码 `{"id": 126745801}`（字典值ID被当属性ID）假条目；兜底最小属性集。修复跟卖商品属性全部丢失、品牌/原产国不生效
- **P0-4 单SKU/跟卖/发现漏运费**：`cloud_probe.py` 删 `if len(variants)>1` 守卫，`_collapse_variants_to_single` 无条件调用（内部兼容 0/1/N），单SKU 采购成本含国内运费 freightCny
- **P0-3 GlobalState 补字段**：加 `dictionary_values` / `match_confidence`（动态字典选色 + 类目低置信度阻断复活）
- **P0-6 竞品价链路**：Skill 抓 Ozon 竞品售价（scraper price 字段）→ `draft.competitor_price` → worker `follow_sell_import` 优先读（不再误用 1688 采购价当竞品价）
- **P1-1 quantity 变体定价**：改用 `pricing_info.variant_prices`（含利润/佣金/物流），不再用 1688 裸采购价上架
- **P1-4 定价失败阻断**：pricing 异常返回 `[PRICING_FAILED]` 标记；删 assemble ¥1000/¥1500 兜底；graph 加 `route_after_pricing` 条件边阻断
- **P1-5 parse_error 合并读 validation_errors**：本地校验错误转结构化错误 + 关键词分类，不再退化为 UNKNOWN 通用 LLM 修复
- **P1-6 登录预检条件化**：service.py `_check_1688_login_live` 加 `login_detected` 守卫
- **P1-7 佣金死代码删除**：移除用 1688 item_id 查 Ozon `/v5/product/info/prices` 的恒空块

### 批次 B：成本优化（LLM / 生图）

- **B1 属性合并批量翻译**：含中文/拉丁属性值一次 LLM 调用批量翻译（分隔符拆回，失败逐条兜底），省 40-60% LLM 调用
- **B2 call_mxou_chat_api 重试退避**：4xx（除429）不重试、429 指数退避、5xx/timeout 退避重试 2 次
- **B3 MXOU 限流接入**：`mxou_rate_limiter`（450 RPM 滑窗 + 429 退避）接入 chat/image 两入口（原死代码）
- **B4 变体主图并发生成**：`variant_primary_loop` ThreadPoolExecutor(4) 并行（配 B3 限流），39 变体小时级→分钟级
- **B5 空参考图跳过生图**：7 个 Phase2 节点空 ref 直接返回 None（detail/scene×3/comparison/social/main 连原始图都没有时）

### 批次 C：性能

- **E1 进度写 PG 节流**：每任务 2s 合并窗口（旧每节点异步写一次）
- **E2 cloud_probe import 惰性**：discovery 网络请求移出模块顶层（旧每次命令 +10s），惰性 + 进程级缓存
- **E3 cache 原子写**：临时文件 + os.replace（并发 CLI 不再写坏 JSON）
- **C1 类目树 TTL 缓存**：ozon_api `_query_category_tree` 24h 命名空间缓存（旧每次搜索重拉整树 ~2-5s）
- **E6 discover CDP 复用**：`search_by_image_cdp` 加 `conn` 参数；`match_selected` 批量图搜共享单连接（旧每候选新建）
- **E9 并发上限**：num_workers 联动 `MAX_CONCURRENT`（默认 **30**，4核4G I/O 密集安全值；旧硬编码 10）
- **C4 ProgressLogger 去重读**：进度配置模块级缓存只读一次 + `config_path` 参数生效 + 修复重复 getenv

### 批次 D：健壮性 / 代码质量

- **D2 ozon_post 共享 session**：改用 `utils.http_session` 连接池（旧裸 requests.post 每次新建 TCP）
- **D3 config 错位对齐**：graph.py metadata llm_cfg 对齐节点实际读取（assemble→category_match_v2_cfg、prepare→attributes_llm_cfg）
- **E8 cfg 键名统一**：`scene_generation_llm_cfg.json` `max_completion_tokens` → `max_tokens`（旧键被忽略，改 cfg 不生效）
- **D5 chrome_launcher 端口过滤**：仅杀带 `--remote-debugging-port` 的实例，不误杀用户日常 Chrome
- **E7 batch_test**：finally `follow_result/matches` 用 `locals().get()` 防 NameError 掩盖原异常；进度文件每 5 条增量写 + 循环后全量写（旧 O(n²)）
- **D4 死代码清理**：删除 6 个废弃节点（category_lookup/attributes_fetch/attributes_llm/attributes_learning/error_handler/multi_info_gen）+ `loop_graph.py` + `image_gen_factory.py`
- **D4 死代码清理**：删除 6 个废弃节点（category_lookup/attributes_fetch/attributes_llm/attributes_learning/error_handler/multi_info_gen）+ `loop_graph.py` + `image_gen_factory.py`
- **C4 NODE_ORDER 同步**：progress_logger 节点顺序字典同步真实图节点集
- **E4 裸 CDP 统一封装**：4 处手写 websocket/CDP → `cdp_client`（`scrape_ozon_product_via_cdp` 全量重构 / `cli.py check` 1688+Ozon 检查 / `batch_test.py` 前置检查）；`CdpTab.close(close_remote=)` + `CdpConnection.release()` 新增（复用用户已有 tab 不误关远程）
- **E5 follow_sell_cloud 连接共享**：Step2（抓 Ozon）+ Step3a（1688 图搜）共享一个 `CdpConnection`（省 2-3 个冗余 WS）；envelope 链路（probe_1688_page 会话引导）保持独立更安全
- **图搜弹窗拦截修复（真实冒烟发现）**：1688 图搜点按钮后 `window.open` 弹窗被 Chrome 拦截 → 注入覆盖为当前 tab 延迟导航 + 结果页未打开自动重试 1 次
- **图搜多重新搜机制**：badge 评分 ≤ 1 时自动重新图搜（`force_refresh` 绕过缓存）最多 2 次取最佳——1688 算法偶发匹配差，实测 badge 0→2（符合 2/3 条件）
- **Chrome 启动禁用弹窗拦截**：`chrome_launcher` 加 `--disable-popup-blocking`（专用抓取实例，不影响用户日常 Chrome）——1688 图搜/登录跳转的 `window.open` 弹窗无需手动放行站点，与 JS 层覆盖双保险
- **图搜标题相关性护栏（follow 管线）**：旧逻辑只按 badge 排序取第一，图搜误匹配不同产品也组装信封 → 复用 discover 的 `_pick_best_match`（badge "符合0/N" 跳过 + RU→ZH 标题重叠打分）；增强：badge 轻微匹配（<0.5 如"符合1/3"）但标题相关性弱（conf<0.3）也拒绝（实测"水龙头"被误标符合1/3 的教训）。拒绝时 `no_relevant_match` 不组装信封，宁缺毋滥

### 验证

- py_compile 全量 ✅；`test_attribute_fill_v013.py` 8/8 ✅；`test_audit_a_fixes.py` 5/5 ✅（P1-4 阻断路由 + P0-2 跟卖属性消费/兜底）；集成验证 ✅；mock 全流程 13/13 ✅；graph 模块导入 ✅（删死代码无残留）

## [0.13.0] - 2026-08-03

### Fixed
- **字典属性手填文本兜底移除（Ozon 上传报错根因）**：字典值未匹配时不再写 `dictionary_value_id=0 + 中文文本`（Ozon 只接受列表中的 dict_id，手填触发「属性值不正确，请从列表中选择一个属性值」——用途/商品颜色/风格报错来源）。三处统一为「未匹配 → 跳过属性，由 `/values/search` 修正或补默认字典值」：
  - `assemble_ozon_product_node.py`：`_build_items_deterministically` 字典未匹配跳过 + `_validate_and_enrich_items` 校验跳过
  - `prepare_ozon_upload_node.py`：字典属性无有效 dict_id → 跳过，绝不文本兜底
  - `validation_retry_loop.py`：`error_repair_llm` 字典修复改走「取字典第一个有效 dict_id」，绝不塞文本默认值
- **可选字典属性盲补移除**（assemble）：不再「取字典第一个值」盲补（语义随机 → 填错值被拒）；仅当字典**唯一值**时才补充，多值一律跳过
- **自由文本属性中文翻译失败防上传**：LLM 翻译失败/仍含中文 → **跳过该属性**，不再回退中文原文或写空值（修复「颜色名称 - 请用俄文填写该字段」）
- **retry 重传防御**（validation_retry_loop `revalidate_node`）：字典属性 + dict_id=0 的文本值不再重传（防死循环）；非翻译名单属性的中文值翻译失败 → 跳过重传
- **品牌属性 dict_id 保留**（prepare，集成测试发现）：品牌 85/5076 强制标记为字典属性，`"Нет бренда"(126745801)` 不再因 schema 缺失被当自由文本归零（否则 Ozon 报「请从列表中选择」）
- **生图提示词回退中文版**：main/scene/comparison/detail/social_proof/white_bg/multi_angle 恢复为 v2 英文 prompt 之前的中文版本（英文版出图质量问题，后续再调）

### Changed
- 颜色属性字典匹配强化：字典值分页拉全（limit 5000），避免大字典（颜色 1494 条）截断导致匹配不到 → 文本兜底 → 报错

## [0.12.0] - 2026-08-01

### Added
- **Discover v2 四阶段重构**（Skill）：先全量采集 → 表格分析 → 挑完再找货源（1688 配额只花在选中产品）；`--rules` 自动筛选、`--min-price/--max-price` 价格区间、无关键词中国站懒加载
- **蓝海评分增强**：sales_growth（需求上升）+ drr 广告占比（低竞争）因子
- **seller.ozon.ru 运营指标借道**（月销量/增长率/广告占比/上架天数，未登录自动降级）
- **Skill 自动更新机制**：COS manifest 检测 → `skill update` 下载/sha256 校验/备份/回滚/保留 data/；每次命令静默检查
- **CDP 图搜匹配修复**：badge 过滤 + RU-ZH 产品词映射 + 相关性护栏 + 重试机制（37/37 匹配率实测）

### Fixed
- 属性缓存预热崩溃根因：全量内存 OOM + 单事务卡死 PG + 429 无限递归 → 逐节点小事务 + 指数退避
- chrome_launcher 误杀 Electron 进程（裸 chrome 匹配）
- Chrome 130+ 默认 profile 禁止远程调试 → 独立 profile
- 采集选择器 :is() 拼接 bug、widget webPrice/评分 key 错误、缓存污染
- compile.py 遗漏 ozon_seller_analytics、__pycache__ 污染 dist
- CI：PR 到 dev 不触发（pull_request 只匹配 main）

### Changed
- service.py 移回明文（探针改动最频繁，需快速迭代）；stealth.py 保留编译
- 统一包机制：一个包全平台（_native/{darwin-arm64,darwin-x86_64,linux,win32}）

## [0.2.0] - 2026-07-18

### Added
- API v1 router (`/api/v1/`) with OpenAPI auto-docs
- Unified error codes (12 `WorkerErrorCode` values)
- Pydantic request/response schemas
- Structured JSON logging with trace_id chain tracing
- Node execution audit (duration, output, errors)
- Ozon API call logging
- Task lifecycle audit (submitted/started/completed/failed/retried)
- Deployment package (`deploy/`): docker-compose, deploy.sh, update.sh
- Auto-init DB on first deploy (category tree + logistics rates)
- CI script (`scripts/ci.sh`)
- API rate limiting (10 req/min/token)
- `MAX_CONCURRENT` env var for concurrency control
- `WORKER_URL` env var for skill-to-worker connection
- `.dockerignore` for smaller images
- Pre-commit hooks (ruff lint)
- LOGGING.md documentation
- CONTRACT.md v3.0

### Fixed
- Multi-SKU variant merge: 9048 now uses `item_id` (deterministic, traceable)
- `double_without_merger_offer` now auto-repairable (appends suffix)
- Variant image fallback uses marketing main_image instead of 1688 alicdn URL
- Deep copy base_item for variant items (prevent shared reference mutation)
- Token prefix handling unified (`replace("sk-", "", 1)`)
- Removed hardcoded Supabase service_role key from 3 files

### Changed
- Dependencies: 27 → 15 (removed opencv, Pillow, langsmith, coze-*, etc.)
- `langchain` → `langchain-core` (only RunnableConfig used)
- `coze-coding-utils` → local `runtime/context.py` stubs
- `memory_saver.py`: psycopg/PostgresSaver lazy-loaded
- Skill `check_task_status()` now queries Worker directly (not n8n)
- Skill `submit_envelope()` now POSTs to Worker directly
- Auth: balance check kept, no deduction (MXOU handles billing)
- Dockerfile: clean pyproject.toml install, HEALTHCHECK on /api/v1/health

## [0.1.0] - 2026-07-01

### Added
- Initial release
- LangGraph 22-node pipeline
- 1688 CDP data extraction
- Ozon product upload
- Multi-SKU variant support
- Self-repair retry loop
- Category/attribute learning
