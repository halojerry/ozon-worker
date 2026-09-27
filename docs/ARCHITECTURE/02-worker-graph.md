# 02 · worker 骨架：LangGraph 图 / 节点出入参 / 路由 / 生命周期

> 范围：`worker/src/graphs/`、`worker/src/main.py`、`worker/src/utils/task_processor.py`、`worker/src/mcp_server.py`。
> 硬纪律：**langgraph 按节点 Input model 过滤 state**——节点/路由要读的字段必须声明进 Input，否则静默拿不到。

## 1. 图装配（graphs/graph.py）

- 主图：`StateGraph(GlobalState, input_schema=GraphInput, output_schema=GraphOutput)`（graph.py:52-56），编译 `:453`，**刻意不挂 langgraph retry_policy**（注释 447-452，永久错误分类交给 task_processor）。
- 入口 `auth`（:99）。节点注册 :62-95 + validation_retry_wrapper/learning_record/fetch_back（:298-300）。
- 边：assemble→scene_llm(261)→visual_vars(262)→Phase1 扇出 [white_bg,multi_angle](262-263)→Phase2 扇出 8 节点(274-284，多 SKU→variant_primary_loop、单 SKU→main_image_gen)→汇聚 prepare(287-291)→validate(295)→条件边→upload(320-327)→status(333)→条件边→fetch_back→learning_record→END / 失败→validation_retry_wrapper(407-443)。
- **STAGE_ORDER 13 阶段（main.py:91-95）是展示顺序非拓扑**：check_quota 实际在 auth 后第二跳（graph.py:121-155），却排在第 10 位。

## 2. 节点全卡（Input → Output → 核心 → 写表）

| 节点 | Input model | Output 要点 | 核心调用（文件:行号） | 写表 |
|---|---|---|---|---|
| **auth** | `AuthInput`（state.py:288-299，含 error_code/failed_stage 路由可见性补口） | user_id/balance/currency_code/draft/source/extensions/error_code/failed_stage("auth") | `auth_node.py:148`：`_verify_mxou_token:43`（GET /v1/models）→ `query_ozon_seller_info:81` → 余额 fail-open `:66` | 无 |
| **check_quota** | **无类型注解**（check_quota_node.py:22——channel 不过滤，见疑点 #10） | 复用 OzonUploadOutput | `ozon_check_quota`；不足 → `[QUOTA_BLOCKED]` + failed_stage=check_quota（:117） | 无 |
| **ingest** | `IngestInput`（state.py:334-340） | task_id(=uuid4!)/status="accepted"/draft/source/extensions/item_id | `ingest_node.py:18`：三层/扁平归一(52-66)、空标题 fail-fast(82-96)、**task_id=随机 uuid4(119) 与 DB 任务 id 双轨**（疑点 #6）；不写表(122) | 无 |
| **follow_sell_import** | **直接 GlobalState**（follow_sell_import_node.py:33，唯一无专用 Input） | product_id/competitor_price/dc/tp/follow_copied_attributes/import_* | hand/api 双模式(37-41)；api=import-by-sku(:203)；/v3+/v4 反查回填真 dc/tp(:214,:359)；失败 fallback CREATE(:634) | 读 PG 类目树 |
| **pricing** | `PricingInput`（state.py:409-427，含 error_message/pricing_info 路由补口） | pricing_info/price/old_price(str)/error_code(LOCAL_PRICE_GAP_BLOCKED)/notice | `pricing_node.py:26`：重量归一(:117)→物流(:180)→汇率(:547)→`compute_price`(:211)→佣金(:246)→**价差守卫**(:455，block→入箱 :470) | product_drafts（仅入箱） |
| **assemble_ozon_product** | **直接 GlobalState**（assemble:1225） | dc/tp/category_match_meta/attributes_schema/final_attributes/ozon_payloads/name/…；阻断出口 LOCAL_TITLE_EMPTY + failed_stage=category_match | 主函数 :1225；`_match_category_layered:4195`、`_llm_match_category:2758`(vision)、`_generate_hashtags:4014`、`_blocked_exit:819`/`_restricted_category_exit:847`、一致性 Step6.5(2264-2495) | **category_match_log**（`_log_match_attempt:4256`，正路+blocked 6 处） |
| **scene_generation_llm** | `SceneGenerationInput`（state.py:744） | scene_context_1/2/3；失败降级默认场景 | `scene_generation_llm_node.py:15`：vision call(50-58) | 无 |
| **visual_vars_llm** | `VisualVarsInput`（state.py:765） | visual_vars；失败回退确定性 extract | `visual_vars_llm_node.py:269`；scene_context 透传不可被 LLM 覆盖(336-341) | 无 |
| **10 生图节点** | `graphs/state_image_gen.py` 各 Input/Output（全部声明 image_gen_plan——但 GlobalState 无此字段，队列路径恒 DEFAULT_PLAN，疑点 #5） | 各单图字段/variant_primary_images | 同构：plan 闸→`filter_reference_images`→`task_image_cache` 缓存→`prompt_assembler`+配色→质量评估→`call_mxou_image_api`；variant 循环 ThreadPool(4)+失败原图兜底(variant_primary_loop_node.py:148，**毒化整单**，09-#1) | **task_generated_images** |
| **prepare_ozon_upload** | `PrepareOzonUploadInput`（state.py:460-507 全字段） | ozon_payload(s)/ordered_images/final_weight_g/final_dims_mm/error_code…；⚠️ **failed_stage 默认值非空**（state.py:527，疑点 #7） | 主函数 prepare:2212；属性填充链 3978-4062（详见 04）；`_derive_model_name_9048:1861`；`_resolve_weight_dimensions:2112`；`_enforce_payload_image_policy:2026`；图片排序 `_IMG_ORDER:1884` | **attr_match_log**（8 处打点）、attr_bounds_learned |
| **ozon_validate** | `OzonValidateInput`（state.py:599-622） | is_valid/validation_errors/auto_fixed；⚠️ **无 error_code/failed_stage 字段**（state.py:625-652，09-#8） | `ozon_validate_node.py:80`：20+ 校验项（详见 09 与下表）；vat/unit 自动修复(210-245) | 无 |
| **ozon_upload** | `OzonUploadInput`（state.py:544-567） | product_id(**恒 None**)/ozon_task_id/upload_status/error_code(LOCAL_IMAGES_MISSING) | `ozon_upload_node.py:142`：upsert 查尸体 offer(276)→**零图硬闸**(286-310)→防洗卡(317-322)→`/v3/product/import`(341)；v0.73 起 pid 由 status 回填(:364-377) | 无 |
| **ozon_status** | `OzonStatusInput`（state.py:651-683，含 ozon_payload/moderation_status） | moderation_status/product_id/error_code/moderation_retry_count | `ozon_status_node.py:27`：Phase1 轮询 import/info(:136)→min_price 补送(:269-292)；Phase2 moderate_status(:360)；**卡图断言**(:523-632) | 无 |
| **fetch_back** | `FetchBackInput`（state.py:936-944） | fetch_back_result{mismatches,erased,defaulted_by_ozon,…} | `fetch_back_node.py:80` → `/v4/product/info/attributes` diff(:110-140) | 无（遥测） |
| **learning_record** | `LearningRecordInput`（state.py:881-925，v0.66+v0.73 补声明历史） | recorded_count | `learning_record_node.py:381`：`_is_real_upload_success:96`→学习门 fetch_back 排除 erased(459-496)→category_mapping 分档写(562-695)→佣金/索引/面包屑回填 | category_mapping、attribute_learning、category_commission、product_task_index、product_costs、source_candidates、web_category_path |
| **validation_retry_wrapper** | `ValidationRetryWrapperInput`（state.py:786-838） | 子图 Output 映射（state.py:841-877） | `validation_retry_wrapper_node.py:18-140`：**:77 进程内 `validation_retry_loop.invoke(...)`**（非 langgraph 嵌入） | 见子图 |

**retry 子图**（validation_retry_loop.py:4228-4336 编译）：节点 = parse_error(:771)→classify_error(:901)→[error_repair_llm(:1257)/repair_prepare(:2204)/repair_pricing(:2399)/repair_dimensions(:2455)/reupload(:3706)/regen_main_image(:3631)]→revalidate(:2953)→recheck_status(:3916)→final_result(:4168)。max_retries=3；`retry_count` 跨入口不重置（wrapper :66）。

## 3. 路由函数全表

| 函数 | 读字段 | 分支 |
|---|---|---|
| `route_after_auth` graph.py:105 | error_code、failed_stage 含 "auth" | 阻断→END / check_quota |
| `route_after_early_quota` graph.py:138 | error_message 含 `[QUOTA_BLOCKED]` | END / route_by_sell_type |
| `route_by_sell_type` graph.py:128 | `extensions.follow_sell` | follow_sell_import / ingest |
| `route_after_follow_sell_import` graph.py:159 | error_message（空 pid→END/类目失败→retry） | pricing / retry / END |
| `route_after_ingest` graph.py:182 | failed_stage 含 "ingest" + error_message（operator.add 累积通道） | END / pricing |
| `route_after_pricing` graph.py:200 | `[PRICING_FAILED]`→END；wd_audit 仅告警放行 | assemble / END |
| `route_after_assemble` graph.py:226 | failed_stage 含 "category_match"（统一阻断通道）+ 错误文案魔法字兜底（双口径，09-#10-类目）+ `match_confidence`（显式 None 判断）< MIN_CONF_BOX(0.3) | scene_generation_llm / END |
| `should_upload_after_validate` graph.py:304 | is_valid、validation_errors | upload / retry |
| `should_handle_error` graph.py:337 | moderation_status/errors/product_id/upload_status/moderation_retry_count；imported+success+真 pid 兜底成功(:372-384)；pending 自环≤3 | fetch_back / retry / ozon_status 自环 |
| `should_learn_after_repair` graph.py:424 | upload_status ∈ (success,pending) | learning_record / END |
| 子图 `repair_node_selector` :971 | error_type=="unfixable"/repair_node/block_to_box/reupload_direct | 对应修复节点/final_result |
| 子图 `should_continue` :3456 | needs_recategorization（硬收敛 LOCAL_CATEGORY_RECATEGORIZE_FAILED）→exit；is_valid→success；retry_count≥max→exit | reupload / parse_error / final_result |
| 子图 `should_reupload` :4079 | upload_status=="success"→success；pending→exit；errors→parse_error | recheck / parse_error / final_result |

## 4. 任务生命周期（submit → queue → execute → terminal）

**提交** `POST /submit_task`（main.py:2081-2330）：
1. 信封解析（兼容 body.payload 包装）→ 空 draft 校验 → `_validate_draft_required_fields:2134`（api 跟卖必带 ozon_product_id/competitor_price）→ 重量非负 → `validate_draft_sanity:2159`。
2. token 剥 `sk-` → `resolve_tenant:2183` → 限流 300/min(:2189) → 余额 402(:2196)。
3. 店铺配额预检 429(:2227)；SKU 去重 `{tenant}:{store}:{ozon_product_id|item_id|sku_id}` → 409 DUPLICATE_SUBMIT（并发由部分唯一索引兜底 :2290）。
4. 建任务行（task_processor.submit_task:394，INSERT ozon_product_tasks，FOR UPDATE SKIP LOCKED 认领 :464-516）+ 直连提交写 draft_submissions（main.py:2039-2078）。

**执行**：lifespan 起 30 workers（main.py:578）→ `process_next_task:518` → `execute_graph_with_timeout:967`（60s 心跳防误判 stale :932；thread_id=任务行 id :998；**队列路径刻意无 checkpointer** :1004-1008，防 30-50 并发锁竞争，超时重跑由 task_image_cache 兜底）。节点回调 → `ProgressCallback:303`（_NODE_STAGE_MAP :289）。

**终态**（三路 + 守卫）：
- 失败判定 `_graph_result_is_failed:60`（upload_status∈(failed,blocked) / notice|error_message 以 `[` 开头 / error_message+failed_stage）。
- **T0.4 佐证闸 `_has_real_product_evidence:84`**：product_id 空/==ozon_task_id（假 pid）→ `_mark_no_real_product_failure:119`（PRODUCT_NOT_CREATED + failed_stage=final_product_evidence_check）→ failed。零假 completed 七道防线见 09。
- 唯一写入口 `_write_terminal_status:907`（`WHERE status='running'` 守卫防迟到翻盘）→ shop_usage 埋点 → draft_submissions 写回 → webhook → `listing_result_log`（failed/rejected/completed 三挂点 :686/:721/:752）。
- 异常：TimeoutError→永久 failed；`_is_permanent_task_error:37`（OutOfQuota/ContentViolation/FileNotFound…）→ retry_count 推满；否则 pending+retry+1。

**查询/重试**：GET /task_status（:2429，_task_status_guard + 跨租户 404 + payload 脱敏）；POST /cancel_task（仅 pending，409）；POST /resubmit_task（:2573，仅 rejected/failed，深拷贝注入 parent_task_id + image_regen=True）；**SKIP_FAILED_REVIVE 语义翻转**（main.py:540,601-658：部署重启默认**不**复活 failed；`SKIP_FAILED_REVIVE=0` 恢复旧行为；running 僵尸照常有界复活）。

## 5. checkpoint 与恢复

- checkpointer 工厂 memory_saver.py:88-126（AsyncPostgresSaver 优先，退化 MemorySaver）；lifespan 挂给 **sync/async `/run` 族图**（main.py:563-576）——⚠️ `set_graph` docstring 宣称 "never hit checkpoint DB" 与代码相反（09-#2-doc）。
- 队列主路径无 checkpointer；无 langgraph interrupt（全仓 0 处）。
- checkpoint 归档 `_purge_checkpoints:1403`（30 天 completed 删行前，序 checkpoints→blobs→writes）。
- stale 清理：运行期 30 分钟未更新重置（:1440）；重启恢复 running→pending+retry+1 有界（:601-658）。

## 6. GlobalState 分组（state.py:15-182）

凭证(token/ozon_*/supabase_*) · 进度(progress_counter max-reducer) · 重试(retry_count/assembly_retry_count/moderation_retry_count/regen_main_image_done/error_type) · 信封(envelope/draft/source/extensions) · 认证(user_id/token_id/balance/currency_code) · 类目(dc/tp/schema/final_attributes/category_match_meta/category_source/final_weight_g/final_dims_mm/attributes_adjusted add-reducer) · 图片(phase1/2、各单图、variants) · 价格(pricing_info) · 上传(product_id/ozon_task_id/upload_status/import_submitted/is_follow_sell) · 验证(validation_errors/is_valid/errors/decline_errors append-only/error_message **last-write-wins**/error_code/notice/failed_stage **operator.add 拼接累积**/stages)。

**GraphInput** = token/ozon_client_id/ozon_api_key/envelope 四必填（:186-194）。**GraphOutput**（:221-284）= 终态全量透出（error_code/failed_stage/upload_status/moderation_status/decline_errors/category_match_meta/final_* 等——**不在 GraphOutput 声明的字段被 channel 过滤丢弃**，wave2 实证注释 :247-251）。

## 7. MCP 远程面（/mcp，mcp_server.py）

ASGI Bearer 中间件(56-90) → 22 工具全部经进程内 httpx ASGITransport 回调 REST（`_call:113`，Bearer 透传，≥400 折叠 `{"error":...}`）——与路由天然同源，**改路由路径必须同步 `_call`**。

| 工具 | REST | 工具 | REST |
|---|---|---|---|
| submit_task:162 | POST /submit_task | search_categories:262 | GET /categories/search |
| get_task_status:179 | GET /task_status/{id} | get_category_attributes:272 | GET /categories/attributes |
| cancel_task:185 | POST /cancel_task/{id} | list_stores:304 | GET /credentials |
| get_task_statistics:191 | GET /task_statistics | analyze_store:310 | GET /stores/{id}/analysis |
| list_drafts:202 | GET /drafts | run_store_action:316 | POST /stores/{id}/actions |
| submit_draft:211 | POST /drafts/{id}/submit | lookup_commission:331 | GET /commissions/lookup |
| batch_submit_drafts:226 | POST /drafts/batch-submit | quote_logistics:338 | POST /logistics/quote |
| get_draft:239 | GET /drafts/{id} | lookup_mapping:352 | GET /mappings/lookup |
| patch_draft:248 | PATCH /drafts/{id}（乐观锁 version） | get_seo_keywords:358 | GET /seo/keywords |
| assemble_draft:288 | POST /drafts/{id}/assemble | report_issue:364 | POST /error_reports |
| — | — | list_error_reports:385 / get_task_forensics:399 | GET /error_reports · GET /forensics/task/{id} |

限流：一次 MCP 调用计 2 次（中间件+内层，mcp_server.py:11-12 自认）。

## 8. HTTP API 面与鉴权矩阵

- **main.py 直挂**：执行族 /run /stream_run /node_run（`_authenticate_token`+限流）、/progress（`_require_bearer`）、/graph_parameter（无鉴权，仅 JSON Schema）；任务族 submit_task/task_status/cancel/resubmit/task_statistics（statistics 走 `_require_bearer`+admin 判定，非 admin 跨租户 403）；/health 无鉴权；/api/v1/store/health（`_require_bearer`，上游失败 502）；分析直读 analytics/*（`_verify_analytics_token`+限流）；/logistics/quote（`_require_bearer`+专属限流键）。
- **routes/**：credentials/dashboard/drafts/estimate/images/orders/products/settings/shelf/source_candidates/store_actions/store_sync/tasks/templates/admin_* 全走 `_authenticate_token`（admin_* 再过 require_admin）；error_reports 走 `get_tenant` Depends；site_public/newapi_proxy/静态无鉴权。
- 鉴权唯一入口纪律：`_require_bearer`（main.py）；v0.79 收口后 cancel/statistics/progress/store/health/logistics-quote 无 Bearer 一律 401。

## 9. 疑点 / 坏味道（详情并入 09-findings）

1. **进度条倒退**：`_NODE_STAGE_MAP` 缺 assemble_ozon_product/scene_generation_llm/visual_vars_llm/check_quota/fetch_back/validation_retry_wrapper/follow_sell_import/variant_primary_loop → `update_progress` stage_idx=0 → **percent 归 0**（assemble 每单必现一次）。
2. **set_graph docstring 与代码相反**（main.py:280-282 vs :566-572）：/run 族实际写 checkpoint，靠清理器兜底。
3. `should_handle_error` 内 errors/product_id 变量重复声明（graph.py:351-359，v0.11 残留）。
4. follow_sell_import 无专用 Input（吃整个 GlobalState）——GlobalState 改名不会被 channel 机制暴露。
5. **image_gen_plan 通道断链**：生图 Input 都声明了，GlobalState 无此字段，队列路径恒 DEFAULT_PLAN——「预留接口」而非活通道。
6. ingest task_id（uuid4）与 DB 任务 id 双轨；assemble `_log_match_attempt` fallback 仍用 state.task_id（:4270-4276）。
7. PrepareOzonUploadOutput.failed_stage 默认值非空（state.py:527）——违反 v0.73「Output 默认值归零」纪律。
8. OzonValidateOutput 无 error_code/failed_stage——validate 阶段失败码恒空。
9. MCP 限流双计（有效配额减半，无文档提示）。
10. **定时炸弹**：check_quota 无 Input 注解 + `route_after_early_quota` 路由读 `state.envelope`——谁按纪律补 Input 注解，路由立刻 AttributeError/恒走 full 分支。
