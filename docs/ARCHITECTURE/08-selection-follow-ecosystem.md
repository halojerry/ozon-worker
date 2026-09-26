# 08 · 生态线：discover / follow / 数据池 / 会话 / 店铺 / MCP / webui

> skill 内部细节见 `01-skill-line.md`；本文讲**跨端时序**与 worker 消费侧。

## 1. discover 跨端时序

```
CLI discover → readiness(chrome/seller/aibuy 预热)
  → collect_and_analyze（搜索/类目页滚动采集 + widget 全量）
  → 粗筛档位（BASE→ai 档→完整档）
  → 运营富化（数据池→cookie 直调→CDP→逐 SKU）→ 池回馈 _giveback_metrics
  → 蓝海评分 → 表格/挑选（规则两段式）→ 1688 匹配（match_selected，四级货源）
  → 利润计算 → 导出 CSV/XLSX
  → --auto-submit: build_envelope_from_discovery → submit_envelope(POST /submit_task)
  → --to-box:     submit_draft(POST /api/v1/drafts, notes 只落列)
worker: create_draft（凭证剥离→credentials；只存 envelope 进 product_drafts.payload）
```

- **数据源三页**（ozon_seller_analytics.py）：all-queries=`/api/site/searchteam/Stats/queries/search/v2`；ozon-bestsellers 与 market-bestsellers=`what_to_sell/data/v3`（weekly + session_count_search_desc / PLATFORM_ALL+价格段）。CDP 版与直调版双实现。
- **DataDome 阻断现状**（平台侧实证）：requests 直调 TLS 指纹级 403 终态；页内 fetch 被 SPA 轮换 Bearer 401——静态 cookie 快照不可持续。现役通道 = cookie 直调（`_fetch_seller_session_cookies` CHIPS 双读 + DataDome 挑战短路窗 600s + 负缓存窗）+ CDP 兜底。roadmap = CDP 捕获 SPA Authorization（需 cdp_client 内建事件循环）。
- **榜单沉淀旁路**：queries 三页 → `analytics_upload` fire-and-forget → worker `/api/v1/analytics/queries|ozon-bestsellers|market-bestsellers`（两趟 upsert 落 blue_ocean_queries/ozon_bestsellers/market_bestsellers）。

## 2. follow 跨端时序

```
CLI follow → readiness → 6h 信封缓存(product_id:store_id)
  → scrape_ozon_product_via_cdp（webShort/全表 DOM 兜底 bring_to_front/webAspects/面包屑/webSellerList）
  → Step2.5 what_to_sell（cookie 直调→CDP；Seller 空间权威类目覆盖面包屑）
  → 1688 找货源四级（aibuy→CDP 图搜→AK 图搜→LLM 翻译文字搜）+ 护栏 + --review
  → follow 信封（images=竞品主图[:1]、follow_type=hand、无 9048 前缀、竞品图只进 competitor_ref_images）
  → 预估+min-margin → 提交
worker: route_by_sell_type → follow_sell_import_node
  hand=CREATE 重建 / api=import-by-sku 复制（现无出口）/ discover=类目置空续走
  权威 dc/tp schema 自校验直信；全链失败→门控仲裁；UPDATE 项必填 dc/tp 硬闸
  → prepare：UPDATE 模式 offer=竞品 id + product_id；9048 裸 item_id（刻意并卡）
  → upload：UPDATE 项 0 图豁免；防洗卡合并；learning 真跟卖压 0.6
```

- **learning 区分**：`_is_true_follow_envelope`（LR:60-86）——follow_type=hand/api 才是真跟卖（压 0.6）；discover 变体正常分档（曾因 truthy 判定被误压 0.6 的实证在注释）。
- **A7 双缓存**：`ozon_cdp/` ns 与 `follow/` ns 均 6h——重测必须同清（cleanup 只有全清，无按 ns 精清）。

## 3. 数据池（sku_metrics_pool）

- 表：sku 唯一 / `sales_payload`/`variant_payload` JSONB **只存指标永不存 cookie** / 归因 cap10 / `needs_*_sync` 不落列（读侧按 updated_at 算）。
- 服务：`SKU_SYNC_MAX_ITEMS=12` / `SKU_QUERY_MAX=50` / `STALE_DAYS=14` / `_norm_sku` 剥 `_0` / SAVEPOINT 原子 upsert。
- 端点：`POST /api/v1/analytics/seller-sync`（≤12/批）与 `GET /api/v1/analytics/sku-metrics`（≤50/查，**全局共享 W11 无租户过滤**——任意有效 token 可枚举全池）。
- skill：`_giveback_metrics`（kill-switch `METRICS_POOL_REPORT=0`，绝不 raise，两处挂点）+ `_apply_pool_metrics`（池命中可整段免 CDP；**stale 行只填值不计命中，不抑制真实补采**）。
- ⚠️ **variant_v2 三段断链仍在接口上**：worker 每次读返回 `needs_variant_sync`，skill 无生产消费/回填方——死字段掩护下的「看起来可用」（已知 defer）。

## 4. 会话代管（ozon_sessions）

- 表：AES-256-GCM、aad 冻结 `tenant:credential`（复用 CREDENTIAL_MASTER_KEY）、只打名单不落值、`(tenant,credential)` 唯一。
- 服务：`store_session`（JSONB 绑定必须 json.dumps——裸 list 被 psycopg2 适配 text[] 的实机 500 根因）；`load_cookies` 换 key 后 GCM 认证失败→标 expired 返 None 绝不返静默垃圾。
- 端点三件：POST/GET/DELETE `/api/v1/credentials/{id}/session`；`GET /api/v1/analytics/what-to-sell` 直调（无会话 404/expired 409 fast-fail/err=session_expired 联动 mark）。
- worker 直调客户端契约：`x-o3-app-name: seller-ui` 头（缺它 403）；307→`?__rr=1`+nonce 必须 Session jar+follow_redirects；判废只看终态。
- **结论**：`__Secure-access_token` 分钟级寿命/用后轮换——静态快照活不过一次消费；三候选（refresh_token 续期 / 同步后秒级消费已实证 / 数据面留 skill 浏览器上下文）待拍板。

## 5. 店铺线（store_sync_service）

- 同步域：orders（游标续传）/products/returns/actions/rating(+warehouse/analytics)。
- **窗口唯一出口 `_as_utc/_fmt_utc`**（:673-689）：timestamptz 取回带会话时区（生产 Asia/Shanghai）不是 UTC，裸 strftime 会把 +08 挂钟标成 UTC → since 落到未来 8h → Ozon 400（S1 事故根因）；防御 clamp since<to。
- `/v1/actions` 是 **GET-only**（ozon_get，POST 永久 405）；rating `localization_index` 是数组（`_extract_localization_index:375`）。
- 快照：`store_metrics_history` append-only（profit 无成本写 NULL 绝不编造，90 天保留）；`/stores/{id}/analysis`（summary/profit_trend/low_margin/out_of_stock/promo_ready；成本链 product_task_index→payload.envelope，无成本不填利润率）。
- 执行：`/stores/{id}/actions` operation ∈ {bulk_update_prices/stocks/archive, actions_register, seller_action_discount}；**每个 operation 成败都写 `store_operation_log`**（唯一写入口 `_write_operation_log`）。
- 防线：`_sync_products` 失败绝不 `_archive_missing`（空集=全店软删，已拆雷）；跨租户绑定拦截 `_assert_client_not_bound_elsewhere`（`pg_advisory_xact_lock(hashtext(cid))` 关并发双绑窗口）。

## 6. MCP 双面

**worker `/mcp` 远程**（22 工具，Bearer ASGI 中间件，进程内回调 REST 同源）——映射表见 `02-worker-graph.md` §7。

**pounding-mcp 本地**（stdio）：
- 21 CLI 封装（check/set_*/get_ak/search/probe/image_search/category/follow/discover/discover_multi/discover_task/seller/queries/graph/query/update/cleanup/session_sync…；白名单映射 skill_runner.py:56，exit 2 语义透传）。
- 5 worker HTTP 直调（analyze_store/run_store_action/report_issue/list_error_reports/get_task_forensics；token=env WORKER_TOKEN > settings.json）。
- 4 job_*（job_list/status/result/cancel；running 态附 `next_poll_s=20`+`next_action` 节奏字段）。
- tasks_server（:8902，Bearer `POUNDING_TASKS_TOKEN`，CORS 全删）：/tasks CRUD + `POST /ask`（弱意图→questions；needs_confirmation→只回显不执行；check/category/search 同步；graph/follow/discover* 后台任务）。
- `router.py` 意图路由九类：A=1688 URL→graph / B=Ozon URL→follow / C=跟卖蓝海选品→discover / C2=自动选品→discover_task / D=上架→--auto-submit / D1=图搜→image_search / E=趋势→追问 / F=≥2 URL→batch / +弱意图词（看看/值不值得→展示态 `--no-submit`）。

## 7. webui 交互面（简要）

- 主要消费：/drafts CRUD+PATCH（version 乐观锁 409）+/ai/{field}+/assemble+/estimate+/submit(支持 scheduled_at)+/batch-submit；/stores/*（stats/analysis/sync）；/orders、/products、/analytics/*、/seo/keywords、/mappings/lookup、admin/templates/mxou/site。
- **EditDraftDrawer**（CollectionPanel.tsx）：类目选择器 `GET /categories/search` → 选中写 `source="manual"` 权威直通；属性表单 `GET /categories/attributes?dc=&tp=`，字典下拉 `&attr_id=` 懒加载（回写缓存 30 天）；改配值并入 draft.attributes；notes PATCH（空串=清、不传=不改）。
- ⚠️ 提交错误归因过粗：400/422 一律提示「请先选择有效店铺凭证」，吞掉 sanity 拒单等真实原因（09）。

## 8. 疑点（并入 09-findings §生态）

1. 直调头契约漂移：worker 带 `x-o3-app-name: seller-ui`，skill `_seller_direct_post` 没有——skill 侧 403 可能部分是缺头。
2. bot-403 误判 session_expired（worker ozon_session_client:82 把 403 一律判废联动 mark expired，DataDome 拦截时误杀活会话）。
3. 畅销榜 map 双缓存口径分裂（直调 cache_key 用 cookie company_id、CDP 版恒 None——互不命中各存一份，可读到相距 6h 的两份快照）。
4. follow 6h 缓存复用窗口（旧竞品价提交，无新鲜度告警；重测须双 ns 同清）。
5. 30min cookie 快照 vs 分钟级 token 寿命错配（快照命中率高=直调 401 率高）。
6. 池 stale 语义与 has_analytics 的真实 0 歧义（池路径与 CDP 路径展示语义不完全一致）。
7. sku-metrics 全局可枚举（W11 有意设计，但读侧无法感知归因边界）。
8. MCP 限流双计（有效配额减半）。
9. `_sync_products` total 兜底估算（上游字段改名→循环提前结束，漏同步静默）。
10. market-bestsellers 无直调版（DataDome 收紧时唯一必开 seller 页的分支）。
11. `--wait`×`--to-box` 组合静默忽略（draft_id 非 task_id，无显式警告）。
