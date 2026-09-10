---
title: 数据库 Schema 歧义审计
purpose: 34 表分类、14 歧义点、ID 词汇表与 status 取值域（建表/改列必读）
applies-version: ">=v0.70.0"
last-updated: 2026-09-10
owner: worker-db
depends: [WORKER-TOPOLOGY]
status: active
---

# 数据库 Schema 歧义审计（DB-SCHEMA-AUDIT）— v0.70

> 起因：用户问「表名/参数/列是否有冲突、歧义」。本文档是**只读审计结论 + 低风险
> 修复记录**，schema 权威定义唯一在 `worker/src/storage/database/shared/model.py`。
> 新表/新列设计前先读本文「约定」节，防歧义继续累积。

## 1. 表清单概览（35 张）

schema 无 alembic——迁移 = `create_all + init_data.py 手写幂等 ALTER`（加列/加索引
支持；改列类型/改名/数据回填无框架，需手写脚本）。每次部署/升级都跑 init_data。

| 分类 | 表 |
|---|---|
| 租户隔离（tenant_id） | ozon_product_tasks, product_drafts, draft_submissions(经 draft FK), credentials, product_task_index, listing_templates, order_notes, order_messages, discovery_runs, ozon_orders_cache, ozon_products_cache, credential_sync_state, store_metrics_history, store_operation_log, error_reports |
| 用户隔离（非 tenant_id 命名） | blue_ocean_queries / ozon_bestsellers / market_bestsellers / selection_insights（contributed_by_token_id）；image_tasks / audit_logs（user_id） |
| 全局共享（无租户列，有意设计） | attribute_cache, dictionary_value_cache, category_cache(按 client+lang), logistics_rates, size_mappings, exchange_rates, ozon_attribute_mappings, category_tree_nodes, category_mapping(W11), category_commission, attribute_synonym, domain_hint, gateway_tasks, task_generated_images, site_banners, site_announcements, data_sources, sku_metrics_pool（未发版：UGC 跨店 sku 指标池，唯一键 sku；归因数组 source_company_ids/contributed_by_token_ids 各 cap 10；**红线只存指标**，永不存 cookie/凭证；needs_*_sync 不落列按 updated_at/缺 payload 读时计算） |
| 审计（无租户列，经任务行可溯源） | category_match_log, attr_match_log, listing_result_log(有 tenant_id) |

主键三类混用：UUID(gen_random_uuid)×10 / 自然键复合键×4 / Identity 自增×其余。

## 2. 歧义/冲突点清单（修复状态标注）

| # | 位置 | 现象 | 风险 | 状态 |
|---|------|------|------|------|
| 1 | category_match_log.task_id (Text) | P1-6：v0.67 前写 ingest 随机 uuid（与任务行无关），v0.67 起优先 thread_id 对齐——**历史行无法 JOIN 任务表** | 中 | 已修写入侧；历史脏数据不可恢复，取证时按时间戳人工对齐 |
| 2 | prepare attr_match_log audit_task_id | 同款：曾「prepare 输入无 task_id → 恒空」 | 低 | v0.69 已修 |
| 3 | `WHERE id::text = :x` 全链混用 | error_report_service / task_service / tasks_routes / learning_record 都按文本查 uuid 主键 → 裸 PK 索引走不上 | 中 | **v0.70 已修**：`idx_ozon_product_tasks_id_text ((id::text))` 表达式索引 |
| 4 | draft_submissions.submitted_task_id Text 无 FK | 引用完整性靠约定，脏引用不可探知 | 中 | **v0.70 部分修**：加普通索引；FK 暂不加（存量脏引用会阻塞建 FK），待观察 |
| 5 | 「task_id」四种语义并存 | ①任务行 uuid（task_generated_images/审计表）②ingest 随机 uuid（历史审计行）③gateway_tasks.task_id String(100)（独立 ID 空间）④Ozon import 操作 id（state.ozon_task_id/import_task_id） | 高 | 约定进本文 §3 词汇表；**新代码禁止再造第五种** |
| 6 | type_id 命名不一致 | 5 张表的 type_id=Ozon 商品类型 ID；ozon_attribute_mappings.category_id 实际也是 description_category_id（同义不同名） | 低-中 | 接受（改名风险大）；新表一律用 `description_category_id`/`type_id` 全名 |
| 7 | status 六套取值域 | 任务/草稿提交/凭证/图片任务/错误报告/留存表各一套（全 varchar 无 enum）；ozon_orders_cache.status 还是中文归一化值 | 中 | 约定进本文 §4 矩阵；不建议 DB enum（迁移成本 > 收益） |
| 8 | confidence 量纲 | 全仓库统一 **0-1 浮点**（无 0-100 写法） | 低 | 无冲突；新列注释必须写明 0-1 |
| 9 | weight 单位 | 全链路**克(g)**；历史 kg 混淆由 v0.68.1 修复（(0,10)g 视缺失走兜底不 ×1000）；`draft.weight` 与 `weight_g` 两种字段名并存 | 中 | 字段名并存接受；语义恒为克 |
| 10 | ID 语义矩阵 | offer_id（Ozon 卖家编码，跟卖=follow_{竞品id}）/ sku_id（1688 规格）/ item_id（1688 商品=source_item_id）/ product_id（Ozon 商品）/ sku_key（{user}:{pid} 去重键）/ ozon_bestsellers.sku_or_id（混合） | 高 | 约定进本文 §3；新代码不得混用 |
| 11 | 任务事实四处冗余 | ozon_product_tasks.payload/result + product_drafts.payload + draft_submissions.extensions + listing_result_log（提取副本） | 中 | listing_result_log 是唯一长期留存（completed 7 天删）；改信封结构须同步 listing_result_log.py 提取器 |
| 12 | ARCHITECTURE-TOPOLOGY 文档漂移 | 「progress 表」实为 ozon_product_tasks.progress JSONB 列 | 低 | **v0.70 已修** |
| 13 | 缓存体积口径冲突 | AGENTS「JSON 全量 ~70GB / PG ~600MB」vs 脚本注释「~600MB 文件」 | 低 | v0.70 以脚本注释为准（60-70GB 是未压缩前的错误估算）；AGENTS 已对齐 |
| 14 | 唯一约束风格不统一 | size_mappings 用 Index(unique) 其余用 UniqueConstraint；logistics_rates 无唯一键（幂等靠 count 跳过） | 低 | 接受；新表统一 UniqueConstraint |

## 3. ID 词汇表（新代码必读）

| 名称 | 语义 | 产生方 |
|---|---|---|
| `ozon_product_tasks.id` | 任务行 uuid（**权威任务 ID**，= langgraph thread_id） | submit_task |
| `category_match_log.task_id` / `attr_match_log.task_id` | 应存任务行 uuid（v0.67+）；历史行为 ingest 随机 uuid | assemble/prepare |
| `gateway_tasks.task_id` | gateway 状态独立 ID 空间（String 100），**与任务表无关** | gateway |
| `state.ozon_task_id` / `import_task_id` | Ozon /v3/product/import 异步操作 id（非商品 id） | 上传节点 |
| `offer_id` | Ozon 卖家编码；跟卖=follow_{竞品id}，否则 draft.item_id/sku_id 派生 | prepare |
| `item_id` | 1688 商品 id（= listing_result_log.source_item_id） | skill 抓取 |
| `sku_id` | 1688 规格 id | skill 抓取 |
| `product_id` | Ozon 商品 id（上传成功后） | Ozon API |
| `description_category_id` / `type_id` | Ozon 类目/商品类型数字 ID（跨语言一致） | Ozon 类目树 |
| `tenant_id` | 租户 = `_key_user_id(token)` 派生（MXOU key 即用户） | worker |

## 4. status 取值域矩阵

| 表 | 取值域 |
|---|---|
| ozon_product_tasks | pending / running / completed / failed / cancelled / rejected |
| product_drafts.submission_status | （见 draft_submissions） |
| draft_submissions | pending / uploading / published / failed / rejected |
| credentials | active / revoked |
| image_tasks | pending / processing / completed / failed |
| error_reports.status | new / triaging / fixed / wontfix |
| listing_result_log.final_status | approved / declined / failed / pending |
| ozon_orders_cache.status | 中文归一化值（⚠️ 唯一非英文域） |

## 5. 约定（新表/新列设计纪律）

1. 租户数据必带 `tenant_id`（String 50）；平台级知识（类目/佣金/词典缓存）保持全局共享，勿加租户隔离（W11 先例）。
2. 主键：业务实体用 UUID + `gen_random_uuid()`；审计/日志用 Identity 自增。
3. 引用任务表用 `task_db_id`（唯一键）或带 FK 的 `task_id`；裸 Text 无 FK 时**必须加索引**。
4. 时间戳 `DateTime(timezone=True) + server_default=func.now()`；缓存过期用 `expires_at` 秒级 epoch（attribute_cache 先例）。
5. JSONB 列名后缀 `_json` / 语义命名（candidates_json/values_data）；数组优先 JSONB（ARRAY 仅历史 source_keywords）。
6. 量纲注释强制：weight=g、confidence=0-1、尺寸=mm、价格=CNY/RUB 显式标注。
7. 迁移：加列/加索引写进 `init_data.py` 幂等段（`ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`）；存量表默认值必须 `SET DEFAULT`（v0.56.3 教训）。
