# A7 — 数据模型与缓存映射指引

> 审计日期 2026-09-11 ｜ 基线 v0.74.0（ozon-worker-gov）｜ 数据源：`worker/src/storage/database/shared/model.py` 全量静态审计 + 本地 PG（5433）只读实查 + 缓存模块源码核对。
> 定位：**可直接使用的开发/排查指引**（用户模块 5 交付物）；Phase B 提升为正式 docs。关联：A4（类目属性校验规则，本文只引用不重查）/ DB-SCHEMA-AUDIT / CACHE-WARM-RUNBOOK。

## 0. 结论摘要（先读）

1. **表数修正**：`model.py` 实际 **45 张表**（46 个 class − 1 个无表 Base；`grep -c __tablename__` = 45），任务前提「44 张」与旧审计「35 张（标题）/42 张（清单）」都不准——**权威唯一以本文 §1 清单为准**。
2. **三核心缓存表中两类风险已实机坐实**：①`logistics_rates` **无唯一约束**且导入脚本是「DELETE 提交 → 再 INSERT 提交」两段式，重导入存在运费查询真空窗；②`category_cache` TTL 默认 **10 年**（315360000s，本地实查过期日 2036-09-06），类目树月级变化实际靠手动 refresh 兜底。
3. **字典缓存三桶策略**（v0.72）是全仓库最重要的缓存架构决策：global（哨兵键 (attr,0,0)）/scoped/ephemeral（不物化走 /values/search），读侧 scoped→global 自动回退。本地实查 168 行全部 scoped 桶。
4. **两处「只读不写」的死缓存**：`exchange_rates` 表全仓库**零写入调用方**（定价恒走 12.0 硬编码兜底）；`warm_dead_nodes` 不在 model.py，由 warm 脚本 raw SQL 自建。
5. 汇率/租户/MXOU 会话/余额四类缓存是**进程内存缓存**，重启即失、多副本不同步——排查「改了配置没生效」先想到它们。

## 1. 全表清单（45 张，权威）

schema 无 alembic——迁移 = `create_all + init_data.py 手写幂等 ALTER`（见 DB-SCHEMA-AUDIT §1）。归属口径：**租户**=带 tenant_id；**用户**=contributed_by_token_id/user_id；**全局**=平台共享；**审计**=日志留痕；**运营**=店铺/站点运营态。

| # | 表名 | 归属 | 用途一句话 | 关键索引/唯一键 | 关联方式 |
|---|---|---|---|---|---|
| 1 | ozon_product_tasks | 租户 | 任务队列（worker 核心调度） | 部分唯一 (tenant,sku_key) WHERE pending/running；idx(status,priority,created_at)；`(id::text)` 表达式索引 | 被 product_task_index/listing_result_log.task_db_id/task_generated_images.task_id 裸引用 |
| 2 | attribute_cache | 全局 | 类目属性 schema 缓存 | uq(dc,tp,language) | 与 dictionary_value_cache 按 (dc,tp) 逻辑关联（无 FK） |
| 3 | dictionary_value_cache | 全局 | 属性字典值缓存（三桶） | uq(attr,dc,tp,language) | attr 行随 attribute_cache schema 语义对应 |
| 4 | category_cache | 全局 | 类目树原始 JSONB 快照 | uq(ozon_client_id,language) | category_tree_nodes 由它 sync 派生 |
| 5 | logistics_rates | 全局 | 物流费率（Excel 导入） | 仅 idx(weight_min,weight_max)；**无唯一键** | 无 |
| 6 | size_mappings | 全局 | 尺码映射（CSV 导入） | 唯一 idx(table_type,input_value) | 无 |
| 7 | exchange_rates | 全局 | 汇率缓存（**当前零写入**） | uq(from,to) | 被 pricing_node/ai_field_service 读 |
| 8 | ozon_attribute_mappings | 全局 | 属性值映射学习记录 | uq(category_id,attribute_id,source_value) | category_id ≙ description_category_id（裸引用） |
| 9 | gateway_tasks | 全局 | gateway 任务状态（独立 ID 空间） | task_id unique | **与任务表无关** |
| 10 | category_tree_nodes | 全局 | 类目树扁平节点（trgm 模糊搜索） | uq(dc,tp,language)；2×GIST trgm；idx(parent/type_id 部分) | (dc,tp) 被 mapping/log/warm 裸引用 |
| 11 | web_category_path_map | 全局 | Web 面包屑→Seller dc/tp 映射（F-B04） | breadcrumb_key unique | (dc,tp) 裸引用 category_tree_nodes |
| 12 | category_mapping | 全局 | 1688→Ozon 类目 L0 学习表 | uq(source_leaf,dc,tp) | (dc,tp) 裸引用；W11 全局共享 |
| 13 | category_commission | 全局 | 类目佣金% 三段缓存 | description_category_id unique | dc 裸引用类目树 |
| 14 | attribute_synonym | 全局 | 属性名同义词表 | uq(source_attr_name,target_attr_id) | 供 attr 匹配链查 |
| 15 | category_match_log | 审计 | 类目匹配审计（每次尝试一行） | idx(task_id)、idx(match_layer,created_at) | task_id 裸引用（历史行是随机 uuid） |
| 16 | attr_match_log | 审计 | 属性匹配审计 | idx(task_id)、idx(attr_id,created_at) | 同上 |
| 17 | domain_hint | 全局 | 领域消歧规则（关键词→顶级类目） | idx(priority) | 无 |
| 18 | task_generated_images | 全局 | 任务生图缓存（防重烧额度） | PK(task_id,slot,version) | task_id=任务表 uuid（thread_id）；image_parent_task_id 裸引用父任务 |
| 19 | blue_ocean_queries | 用户 | 蓝海关键词数据上报 | uq(query,token_id) | contributed_by_token_id≈租户（非 tenant_id 列名） |
| 20 | ozon_bestsellers | 用户 | Ozon 榜单上报 | uq(sku_or_id,token_id) | 同上 |
| 21 | market_bestsellers | 用户 | 全平台榜单上报 | uq(product_name,token_id) | 同上 |
| 22 | discovery_runs | 租户 | discover 选品结果归档 | idx(tenant_id)（无业务唯一键） | tenant_id 隔离 |
| 23 | shop_usage_stats | 运营 | 店铺任务埋点（按天聚合） | uq(ozon_client_id,stat_date) | ozon_client_id 裸关联店铺 |
| 24 | product_drafts | 租户 | 采集箱草稿（version 乐观并发） | idx(tenant_id,updated_at DESC) | payload 含信封；被 draft_submissions FK |
| 25 | draft_submissions | 租户 | 草稿提交记录（每次一行） | idx(draft_id)；**FK→product_drafts.id CASCADE** | submitted_task_id 裸 Text 引用任务表（有索引无 FK） |
| 26 | credentials | 租户 | 店铺凭证（AES-GCM 列级加密） | uq(tenant,client_id)；部分唯一 (tenant) WHERE is_default | 被 sessions/sync_state/cache 表裸引用 |
| 27 | product_task_index | 租户 | 商品↔任务索引 | PK product_id；**FK→ozon_product_tasks.id**、FK→credentials.id | draft_id 裸引用 |
| 28 | listing_templates | 租户 | 上架配置模板 | idx(tenant)；部分唯一 (tenant) WHERE is_default | 无 |
| 29 | order_notes | 租户 | 订单货源标注 | PK posting_number；idx(tenant) | posting_number 对应订单缓存 |
| 30 | order_messages | 租户 | 订单消息留痕 | idx(tenant) | posting_number 裸引用 |
| 31 | site_banners | 运营 | 站点 Banner | idx(sort,enabled) | 无 |
| 32 | site_announcements | 运营 | 站点通告 | 无 | 无 |
| 33 | ozon_orders_cache | 租户 | FBS 订单缓存（15min 同步） | uq(tenant,credential,posting_number) | credential_id 裸引用 credentials |
| 34 | ozon_products_cache | 租户 | 在售商品缓存 | uq(tenant,credential,product_id) | 同上 |
| 35 | credential_sync_state | 租户 | 店铺同步水位 | uq(tenant,credential) | 同上 |
| 36 | data_sources | 全局 | 数据源配置 | name unique | 无 |
| 37 | audit_logs | 用户 | 通用审计日志 | 无 | user_id 弱引用 |
| 38 | image_tasks | 用户 | 图片处理任务 | idx(user_id,created_at) | user_id 弱引用 |
| 39 | store_metrics_history | 租户 | 店铺指标快照（append-only） | idx(tenant,credential)、idx(store,snapshot_at) | credential_id 裸引用 |
| 40 | store_operation_log | 租户 | 店铺操作审计（append-only） | idx(tenant,credential)、idx(credential,created) | 唯一写入口 store_operation_log.py |
| 41 | selection_insights | 用户 | 选品洞察聚合 | uq(keyword,token_id) | contributed_by_token_id |
| 42 | listing_result_log | 审计 | 上架结果长期留存（每任务一行） | task_db_id unique；idx(tenant/status/created) | task_db_id=任务表 uuid（修 P1-6 对齐） |
| 43 | error_reports | 租户 | 用户错误报告 | idx(tenant/status/created)（无业务唯一键） | evidence.task_ids 裸引用 |
| 44 | ozon_sessions | 租户 | 卖家会话代管（AES-GCM） | uq(tenant,credential)；idx(tenant) | credential_id 裸引用（同租户语义） |
| 45 | sku_metrics_pool | 全局 | 跨店 SKU 指标池（只存指标） | sku unique；idx(updated_at) | 归因数组 cap10，永不存 cookie |

**⚠️ model.py 之外还有 1 张真表**：`warm_dead_nodes`（PK(dc,tp)，warm 脚本 `CREATE TABLE IF NOT EXISTS` 自建，v0.73）——Ozon 400 死类目永久跳过。**不在 model.py = 不受 create_all 管**，排查时别漏。

### 1.1 DB-SCHEMA-AUDIT 漂移与 14 歧义点现状

- 旧审计标题「35 张」、清单实际 42 张、真值 45 张（+model.py 外 1 张）。**未入旧审计的 4 张**：`web_category_path_map`（F-B04 新增）、`shop_usage_stats`（v0.34 埋点）、`ozon_sessions`（未发版批 C）、`warm_dead_nodes`（raw SQL）。
- 14 歧义点现状（逐条核对 v0.70 文档标注）：#3（id::text 表达式索引）、#4 部分（submitted_task_id 加索引不加 FK）、#12（文档漂移）已修；#1/#2 写入侧已修、历史脏数据不可恢复；其余（#5 task_id 四语义、#7 status 六套取值域、#10 ID 矩阵、#11 事实四处冗余、#14 唯一约束风格）为「接受+约定」态——**新表纪律仍以该文档 §5 为准**，本文不重复。

## 2. 三核心表深挖

### 2.1 logistics_rates（运费表）

| 字段 | 类型 | 语义 |
|---|---|---|
| scoring_group / service_level / tpl_provider / delivery_method | varchar | 评分组/服务等级/3PL/配送方式（Q1 查询四维） |
| base_cost / per_gram_rate | float | 首重（¥）+ 续重（¥/g） |
| weight_min / weight_max | int | 重量区间（g，闭区间匹配 `min<=w<=max`） |
| sum_limit_cm / longest_limit_cm | int | 三边和/最长边上限（cm） |
| charge_type / vol_weight_divisor | varchar/int | actual|volumetric；体积重除数（如 6000，0=不计） |
| created_at | **String(50)** | ⚠️ 字符串非 timestamp（schema 气味） |

- **数据来源/更新频率**：`docs/China_scoring_ENG_CN*.xlsx`「中国 rFBS」sheet → `scripts/import_logistics.py` 手动导入，142 行（本地实查 142）。费率变更 = 换 Excel 重跑。**无 TTL、无缓存层**——每次定价/报价直查 PG。
- **消费方**：`utils/logistics_quote.py query_logistics_cost`（唯一权威入口；Q1 全匹配→Q2 仅重量→Q3 跨 3PL→RETS Standard→`max(5.0, w*0.05)` 默认），供 pricing_node + `POST /api/v1/logistics/quote`。⚠️ `LocalDBManager.get_logistics_cost` 是**只按重量匹配的旧入口**（忽略 3PL/等级），仍存活勿误用。
- **已知风险**：①无唯一约束（实机 `\d` 确认仅 PK+weight 索引），重复行不报错，Q1 `order_by base_cost asc limit 1` 会静默选最便宜行；②导入是 `DELETE→commit` 后再循环 `INSERT→commit`（import_logistics.py:155-163），**两提交之间并发查询落空走默认费率**（轻小件差价可达数倍）；③weight_max=0 行（Excel 解析失败产物）会匹配任何重量。

### 2.2 category_tree_nodes（类目表）+ category_cache（原始快照）

| 字段（tree_nodes） | 语义 |
|---|---|
| description_category_id / type_id | Ozon 类目/类型数字 ID（叶子 tp 非空；复合唯一含 language） |
| node_name / node_type / parent_dc | 名称；category\|type；父类目 |
| full_path / top_level_category_name / depth | 全路径（jieba/路径精配主字段）/顶层名/深度 |
| disabled / language | 软失效标记；RU+ZH_HANS 双份（id 跨语言一致，值仅 ZH_HANS） |

- **数据来源/更新频率**：①`scripts/refresh_category_tree.py`（权威刷新：每语言 1 次 `/v1/description-category/tree` → 快照落 category_cache → 全量 upsert（advisory lock 防并发）→ 消失节点 `disabled=true` 软失效，手动执行，幂等连跑零变更）；②assemble 类目缓存为空时拉树兜底（`set_category_cache + sync_category_tree_nodes`）；③init_data 从 assets JSON 导入。本地实查 18 256 行。
- **category_cache**：uq(ozon_client_id,language)，`tree_data` JSONB 整树快照，**TTL 默认 315360000s=10 年**（实查过期日 2036-09-06）——名义缓存实际是「最近一次拉到的树」持久快照，失效靠手动 refresh，不靠过期。
- **消费方**：`OzonCategoryQuery`（jieba 搜索/路径精配/类型节点全集）、warm 预热分母（type 节点 DISTINCT (dc,tp)）、`GET /api/v1/categories/search`、L0/映射表的 (dc,tp) 校验。
- **已知风险**：①树变化只能手动 refresh 发现，无定时任务文档化；②软失效行保留（设计如此，供 L0 失效识别），统计现役要带 `disabled=false`；③language 双份意味着树刷一次写两倍行。

### 2.3 attribute_cache / dictionary_value_cache（特征属性表+字典值表）

| 表 | 唯一键 | TTL | 本地实查 |
|---|---|---|---|
| attribute_cache | (dc,tp,language)，attributes_schema JSONB | 30d | **0 行**（懒加载语义：按需才写） |
| dictionary_value_cache | (attr,dc,tp,language)，values_data JSONB | 30d | 168 行，全部 scoped 桶，过期日 +30d 吻合 |

- **数据来源（双通道，同 30d TTL）**：①**预热**：`warm_category_cache.py --all --pg-only`（全量 ~16h，分片 ≤2、磁盘 <5G 守卫、死节点跳过）；②**懒加载**：管线版（assemble/retry schema 拉取回写）+ 交互版（`services/category_schema_service.py`，webui 表单/字典下拉按需拉一次回写 30d）；③部署灌入：COS 缓存 JSON → `--import-only`（deploy.sh/cos-update.sh 自动）；④init_data 导入。三处 TTL 写点（warm/init_data/local_db_manager）已对齐 30d。
- **消费方**：OzonCategoryQuery（schema：`get_attribute_schema`；字典：`get_dictionary_values` 含 scoped→global 回退，ozon_category_query.py:1493）、属性填充/校验节点、`GET /api/v1/categories/attributes`。
- **已知风险**：①`set_attribute_cache` **未做 type_id None→0 归一**（dictionary 侧 v0.72 已修，attribute 侧同款雷仍在）——传 NULL 时 ON CONFLICT 不命中退化为纯 INSERT 裂行（现调用方恰好都传 int，属潜伏）；②expires_at 是 epoch 秒 int，人工改数易错；③本地/新环境 0 行 = 未预热，首单全靠懒加载变慢。

## 3. 缓存层映射总表

| 缓存 | 底层表/来源 | key 构成 | TTL | 写入时机 | 失效机制 | 一致性保障 | 排查命令/入口 |
|---|---|---|---|---|---|---|---|
| 字典三桶 | PG dictionary_value_cache | scoped=(attr,dc,tp,lang)；global=哨兵 (attr,0,0,lang)；ephemeral 不落库 | 30d | warm / 懒加载 / --import-only | 读时 expires_at + 周日 re-warm cron | upsert ON CONFLICT + type_id None→0 归一 + 巨字典不物化（防撑盘） | `warm_category_cache.py --coverage`；`SELECT count(*) FROM dictionary_value_cache WHERE attribute_id=<id>;` |
| 属性 schema | PG attribute_cache | (dc,tp,language) | 30d | warm / 管线+交互懒加载 / import | 同上 | upsert（⚠️ type_id 无归一） | `SELECT count(*) FROM attribute_cache WHERE description_category_id=<dc> AND type_id=<tp>;` |
| 类目树快照 | PG category_cache | (ozon_client_id,language) | **10 年** | refresh 脚本 / assemble 拉树兜底 | 手动 refresh（不靠过期） | upsert uq_category_cache | `SELECT ozon_client_id,language,to_timestamp(expires_at)::date FROM category_cache;` |
| 类目扁平表 | PG category_tree_nodes | uq(dc,tp,language) | 无 TTL | refresh 全量 upsert / init_data | refresh diff 软失效 disabled=true | advisory lock 防并发；软失效保 L0 引用 | `SELECT count(*) FROM category_tree_nodes WHERE language='ZH_HANS' AND node_type='type' AND disabled=false;` |
| 死节点表 | PG warm_dead_nodes（raw SQL） | PK(dc,tp) | 永久 | warm 遇 400 mark_dead_node | 手工 DELETE 后重预热 | ON CONFLICT DO NOTHING | `SELECT * FROM warm_dead_nodes LIMIT 20;` |
| 类目佣金 | PG category_commission | dc unique | 无 TTL | approved 回填 prices_api / what_to_sell 分段 | 无自动失效（updated_at 仅供观察） | upsert + 段值 ≤0 拒采信（防 0% 污染） | `SELECT * FROM category_commission WHERE description_category_id=<dc>;` |
| 生图缓存 | PG task_generated_images | (task_id,slot,version) | 7d 清理 | 每次生图成功 save_image | main 周期 cleanup_old(7d) | upsert + resubmit 父任务一层回溯 + force_regen 绕读 | `SELECT slot,version,url FROM task_generated_images WHERE task_id='<tid>' ORDER BY slot;` |
| MXOU 余额 | 进程内存 _BALANCE_CACHE | token 指纹（**单槽**） | 30s | 每次生图前 _check_balance_cached | 时间过期 | fp 绑定防多用户污染 + 0.0 二次直查防误固化 | 入口 `utils/mxou_api.py:305`；日志 grep「余额」 |
| 租户解析 | 进程内存 _tenant_cache | clean token | 60s | resolve_tenant 首查成功 | 读时判过期（**条目不清理只累积**） | threading.Lock；Supabase 失败 fail-closed 503 | `services/tenant_service.py` clear_cache()；等 60s |
| MXOU 登录会话 | 进程内存 MxouSessionStore | MXOU user id | 60s（monotonic） | login 成功后 put | get 时过期弹出 | 单进程 workers=1 前提（多副本不同步） | `services/mxou_login_service.py:28` |
| 汇率 | PG exchange_rates | (from,to) | 读时 24h 判 | **无写入方（零调用）** | — | 无 | `SELECT * FROM exchange_rates;`（恒空→定价走 12.0 兜底，日志「汇率缓存未命中」） |
| 部署灌入通道 | COS 两个 JSON → --import-only | 文件级 | 沿用表 TTL 30d | deploy.sh/cos-update.sh 部署后 | 每次部署覆盖 | upsert 幂等 | 容器日志 `/app/logs/warm_import.log` |

**一致性总口径**：所有 PG 缓存 = upsert 幂等 + 读时 TTL 判（过期行不删留原地）+ 主动更新（warm cron/部署灌入）+ 失效兜底（懒加载回源回写）；对账工具 = `--coverage`（只读覆盖率审计）。内存缓存 = 短 TTL + 单进程假设，**无跨进程失效广播**（改表/改配置后重启进程是最可靠失效手段）。

## 4. 类目差异化存储与读取规则（属性/特征）

**三层模型**：类目 (dc,tp) → 特征（attribute_cache schema 行，attr_id）→ 特征值（dictionary_value_id）。schema 按 (dc,tp) 物化差异：每个类目的必填/字典/值数上限各不相同。

1. **schema 差异化**：`attribute_cache` 按 (dc,tp,language) 一行一份 schema JSONB。写入三通道同 TTL（§2.3）。读侧「缓存优先→未命中按租户凭证拉一次 Ozon→回写 30d→失败降级 found=false」（category_schema_service，v0.71 修订的交互版懒加载）。
2. **字典值三桶路由**（唯一入口 `utils/dict_value_cache.py`）：
   - `category_dependent=false`（原产国/保证类，跨类目逐字节一致）→ **global 桶**：哨兵键 (attr,0,0,lang) 全局一份，零 DDL；
   - `category_dependent=true` 且 ≤2000 值（类型 8229/颜色/HS）→ **scoped 桶**：(attr,dc,tp,lang) 按类目隔离；
   - 首页（limit=2000 契约上限）即 has_next 的巨型字典（品牌 85）→ **ephemeral**：不物化不落库，下拉返回首页值，运行时 value→id 走 `/values/search`（utils/ozon_dict_values.py）；
   - 字段缺失/未知 → 保守按 scoped（旧行为兼容）。
3. **读侧回退**：`get_dictionary_values` 先查 scoped，未命中自动回退 global 桶（ozon_category_query.py:1493）——所有纯缓存读者免改自动生效。
4. **校验规则（引用 A4 结论，不重查）**：值数出口闸 cap_attribute_values（按 schema `max_value_count`，8229 恒 1）；字典值必须带 Ozon dictionary_value_id（盲填归一到 attr_value_matcher，exact-only 宁缺毋滥）；数值 bounds 白名单仅 8962 等少数 attr（attr_numeric_sanitize）；尺寸 OZON_DIM_BOUNDS_MM clamp 留痕。平台不下发 min/max/pattern，数值边界只在拒单错误里反馈（A4 §0.1）。
5. **TTL 纪律**：字典/schema 30d 三处写点一致；改任何写 SQL 前必读记忆 `sqlalchemy-jsonb-cast-trap`（CAST(:bind AS jsonb)，bind 旁裸 `::type` 会被吞成 syntax error）。

## 5. 排查手册（5 个常见故障场景）

### 5.1 「字典值查不到」（属性值没填/attr_match_log 大量 miss）

症状→定位：①查桶：`SELECT description_category_id,type_id,length(values_data::text) FROM dictionary_value_cache WHERE attribute_id=<id>;`——有 (dc,tp) 行=scoped 命中；只有 (0,0) 行=global 回退；**零行且非 ephemeral**=未预热。②确认是否 ephemeral 巨字典（品牌 85 等 >2000 值）：属预期，运行时走 /values/search，查不到看 Ozon API 日志。③查过期：`to_timestamp(expires_at) < now()` 即过期衰减回懒加载。④查 type_id 归一：写入侧已 None→0，但手工 INSERT NULL 会让 ON CONFLICT 永不命中静默裂行。
修复：缺哪补哪——单点懒加载（调一次 categories/attributes 接口）或批量预热 `warm_category_cache.py --all --pg-only`（分片 ≤2）；expired 行重跑预热即续期（upsert 幂等）。

### 5.2 「类目 schema 缺失」（webui 表单空/categories/attributes found=false）

定位：①看返回 reason：`no_credential`=请求没带店铺凭证；`fetch_failed`=Ozon API 失败；`empty_from_ozon`=类目被 Ozon 删除→查 `SELECT * FROM warm_dead_nodes WHERE description_category_id=<dc> AND type_id=<tp>;`（若在死节点表但商品在售=误标，DELETE 该行重预热自证）。②`SELECT count(*) FROM category_tree_nodes WHERE description_category_id=<dc> AND type_id=<tp> AND disabled=false;` 为 0=树里没有该叶子→先 `refresh_category_tree.py --dry-run` 看差异。③本地/新环境 attribute_cache=0 行是正常态（懒加载），不是故障。
修复：补凭证/重预热；误标死节点 DELETE；树缺失跑 refresh（需凭证，双语全量分钟级）。

### 5.3 「缓存撑盘」（磁盘满/warm 中止）

症状→定位：warm 内置守卫 `<5G 自动中止`（日志「磁盘余量不足」）；`df -h` 确认。历史根因=pre-v0.72 品牌字典 per-node 大行（5.18MB×每节点）。
修复（严格按 CACHE-WARM-RUNBOOK）：①`TRUNCATE dictionary_value_cache;`（清旧遗留）；②重预热（**分片 ≤2**，9 片并行曾顶爆 3.6G RAM 容器）；③`--export-from-pg` 导出（⚠️ 勿用旧 `--export-only` 边拉边导；未清理就导出会把遗留大行原样回灌=事故复发面）；④coscli 上 COS → 部署自动灌入。防复发三件套已就位：三桶 ephemeral 不物化 / 磁盘守卫 / 备份轮转+logging 封顶。

### 5.4 「定价佣金异常」（售价偏差/利润不符）

定位：①佣金来源链：日志找 `source=explicit|cache:<band>|segments:<band>|fallback`——fallback=0.10 说明缓存表无该类目或段值 ≤0 被拒采信；查 `SELECT fbs_leq_1500,fbs_leq_5000,fbs_gt_5000,source FROM category_commission WHERE description_category_id=<dc>;`（0/负值=历史污染行，upsert 正确段值覆盖）。②运费链：日志找 fallback_chain 标记（Q1_size_miss/Q2_tpl_miss/Q3_cross_tpl_miss/RETS_standard/default_fallback）——default_fallback=`max(5.0,w*0.05)` 说明表空或导入真空窗；查 `SELECT count(*) FROM logistics_rates;` 应 142。③汇率：日志「PG 汇率缓存未命中，使用默认汇率 12.0」=exchange_rates 表空（**结构性恒空**，见 P1-2），CNY→RUB 恒 12.0。
修复：佣金行 upsert 修正；运费表重导 import_logistics.py（注意导入窗口，见 P1-1）；汇率先人工 INSERT 一行可临时生效（24h 读时过期）。

### 5.5 「租户数据查不到」（草稿 0 条/forensics 404/报告空）

定位：①确认租户口径：生产 tenant=Supabase user_id 整数（如 "28"），本地未配 Supabase=key 哈希派生 `user_<sha256[:16]>`——**用哈希租户查生产数据必然查不到**（v0.73 已修四处端点，历史 error_reports 哈希租户行原 token 仍不可见）。②租户缓存 60s：刚改 tokens 表绑定后 60s 内旧值仍命中，重启进程立即失效。③跨租户访问被 404 吞（设计如此）：用管理员视角查表确认数据存在：`SELECT tenant_id,count(*) FROM product_drafts GROUP BY 1 ORDER BY 2 DESC LIMIT 10;` 对比当前解析出的 tenant_id。
修复：等 60s/重启；核对 Supabase tokens 表 user_id；历史脏租户行只能 SQL 侧人工改 tenant_id。

## 6. 发现清单（P0-P3）

| 级别 | 发现 | 证据 | 建议 |
|---|---|---|---|
| P1 | logistics_rates 重导入真空窗：DELETE 提交与 INSERT 提交之间表为空，并发定价/报价走 default_fallback 低估运费 | import_logistics.py:155-163 两段 commit；logistics_quote.py:200-212 兜底 | 改单事务（DELETE+INSERT 同一 commit）或临时表 swap；加 UNIQUE(tpl_provider,service_level,scoring_group,weight_min,weight_max) |
| P1 | exchange_rates 死缓存：set_exchange_rate 全仓库零调用方，表恒空，pricing_node/ai_field_service 汇率恒走硬编码 12.0——CNY→RUB 波动时三档价全链失真 | grep 零命中；pricing_node.py:479-498 兜底日志 | 接汇率源定时写入（init_data 或周期任务），或明确接受 12.0 并删除死代码与误导性日志 |
| P2 | category_cache TTL=10 年（315360000）：树月级变化但快照名义有效期 10 年，refresh 全靠手动纪律 | local_db_manager.py:318 默认参；本地实查 expires=2036-09-06 | 降为 30d 并纳入 refresh cron；至少在注释标注「真实失效=手动 refresh」 |
| P2 | set_attribute_cache 未归一 type_id（NULL≠NULL 使 ON CONFLICT 不命中→裂行），与 dictionary 侧 v0.72 修复前同款雷 | local_db_manager.py:258-279（对比 :289 的 None→0） | 复制 dictionary 侧归一逻辑；顺手给 uq_attribute_cache 改部分唯一索引或归一写入 |
| P2 | DB-SCHEMA-AUDIT 漂移：标题 35/清单 42/真值 45；未收录 web_category_path_map、shop_usage_stats、ozon_sessions、model.py 外 warm_dead_nodes | 本文 §1.1 逐表比对 | Phase B 更新旧审计（表清单+status 域矩阵补 ozon_sessions active/expired） |
| P2 | logistics_rates.created_at 为 String(50) 非 timestamp；LocalDBManager.get_logistics_cost 旧查询入口忽略 3PL/等级，与 logistics_quote 双入口并存 | model.py:153；local_db_manager.py:146-170 | 列型改 timestamp（init_data 幂等 ALTER）；旧入口删除或内部转调 query_logistics_cost |
| P3 | _tenant_cache 条目只查不删（token 维度无限累积，单条 <200B，进程生命周期内低风险） | tenant_service.py:23,69-71 | 加简单 LRU 上限即可 |
| P3 | 45 表单文件 1296 行（model.py），按域（任务/缓存/学习/运营/审计）拆分可读性更好；风险低不紧急 | 文件行数 | 择机重构，拆时保持 import 路径兼容（现有很多 `from ...shared.model import X`） |
| P3 | shop_usage_stats.common_errors 是「最近 5 条」降级实现非 top-5 聚合，使用者易误解 | model.py:624-626 注释 | 已注释声明，Phase B 文档化时保留该口径说明 |

**对账基线（本地 PG 5433 实查 2026-09-11）**：logistics_rates=142 ｜ category_tree_nodes=18 256 ｜ attribute_cache=0（懒加载语义）｜ dictionary_value_cache=168（全 scoped）｜ category_cache=2（RU/ZH，2036 过期）｜ category_commission=2 ｜ task_generated_images=75 ｜ category_mapping=7 ｜ warm_dead_nodes=0。生产数字会不同，但**量级结构与桶分布**应一致——显著偏离即优先查 §5 对应场景。
