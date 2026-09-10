# A3 — 数据质量 / 一致性 / 备份恢复审计（v0.74.0 基线）

> 审计代理：glm-5.3-flash 调研执行层 · 2026-09-11 · 三主题：数据质量校验 / 数据一致性保障 / 备份与恢复

**总体结论**：上架写入链的数据质量校验**成熟度高**（出口闸唯一入口纪律 + 本地预检 + retry 自愈三层），脏数据防御强；一致性保障**局部好、全局无**——任务↔submission、订单增量同步有真实对账修复（F-D04 / 1h 重叠窗），但**六类「本地 vs Ozon」副本对中无一有计数级对账探针**，汇率缓存更是「有读无写」的断链（P1）；备份恢复是**最薄弱一环**——唯一脚本 `backup-pg.sh` 依赖宿主机手配 crontab，无异地副本强制、无恢复演练、RTO/RPO 只存在于 PRD 目标未运营化、schema 无 alembic 无 down migration。

---

## 1. 事实基线

### 1.1 数据质量校验机制清单

| 机制 | 位置 | 覆盖范围 | 缺口 |
|---|---|---|---|
| 属性值数出口闸 | `worker/src/utils/attr_value_sanitize.py:76`（`cap_attribute_values`，8229 恒 1，`max_value_count`/`is_collection` 驱动） | prepare 载荷出口、retry flat 合并、retry attributes/update 重发、validate 预检第二道 | 无；schema 未收录属性不设限（有意宁勿误伤） |
| 数值属性清洗 | `worker/src/utils/attr_numeric_sanitize.py:47`（RU 逗号/单位剥离/取整/越界夹取） | prepare 主循环 + 8962 兜底 + retry repair 三处唯一入口；bounds 白名单仅 `8962:(1,10000)`（:32） | bounds 表仅 1 个属性，其余属性越界靠 Ozon 事后拒 |
| 体积-重量密度兜底 | `worker/src/utils/volume_weight_guard.py:66`（0.40 g/cm³ 只抬不拒，cap×3） | prepare `_resolve_weight_dimensions` + retry `repair_dimensions_node` 两接线 | 生产 199 行校准依赖历史分布，新品类漂移无监控 |
| 重量/尺寸归一 | `worker/src/utils/weight_dimension_normalizer.py`（`OZON_DIM_BOUNDS_MM`:47 = 长(42,400)/宽(25,400)/高(5,200)，`OZON_MIN_WEIGHT_G`=10g:43，真实值只标疑不改写） | normalizer 源头 clamp + validate 第二道同源 import | 无 |
| 上传前预检（大闸） | `worker/src/graphs/nodes/ozon_validate_node.py`（必填缺失:357、字典 id>0:307、值数闸:325、数值坏值:340、标题-类目零交集:384、拉丁/中文:402-483、变体颜色:491、危化品:534、图片可达抽样:566、COS 镜像闸:599-612；critical 关键词表:624 一次列全不 fail-first） | Ozon 无 dry-run API（:615 注释），全部本地预检 | 图片仅探测可达性，无分辨率/宽高比/格式规格校验；预检异常整体 except 降级（:661） |
| 信封物理合理性 | `worker/src/utils/draft_sanity.py`（MAX_WEIGHT_G=50000 / MAX_DIM_MM=5000，轻物<10g 只标疑） | pricing_node:122 入口拦截 | 仅 pricing 消费，draft 入库口不查 |
| 价差守卫 | `worker/src/utils/price_sanity_guard.py`（锚价/final ≥10× block 入箱，env 可覆盖） | 仅 discovery_meta 带锚流；graph 直传无锚零拦截（docstring 自认） | 无锚流靠 skill 侧匹配（跨仓库） |
| 标题规范 | `worker/src/utils/title_sanitizer.py`（营销词黑名单、80 字符截断:126、关键词堆砌）+ `title_formula.py:19`（`TITLE_MAX_LENGTH=80`） | prepare/retry 共享 | 80 是内部口径（Ozon 实际上限 200/255 更宽），无平台契约核对记录 |
| 受限品类闸 | `worker/src/utils/restricted_keywords.py` + `assemble_ozon_product_node.py:1475`（config 热加载，双命中拦，manual/page 豁免） | assemble 出口 | 词表靠人工维护 |
| 图 URL 白名单 | `worker/src/utils/image_url_guard.py`（货源图床白名单 + 拒缩略后缀） | E1 转存/生图参考出口 | 不校验图片内容规格 |
| 离线试填校验 | `worker/scripts/offline_validate.py`（拉真实 schema+字典→复用 prepare 填充函数→模拟 Ozon 错误码，零 import） | 开发期改填充逻辑后的回归 | 需真实凭证；无 CI 定时跑 |
| 缺口量化 | `worker/scripts/gap_report.py`（应填 vs 已填 attempted_fill_rate + Top 缺口属性） | 离线读 PG 最近任务 | 手动运行，无告警阈值 |
| 缓存覆盖率审计 | `worker/scripts/warm_category_cache.py:445`（`--coverage` + `warm_dead_nodes` 永久跳过表:491，coverage 分母剔除死节点） | ZH_HANS type 节点 schema/字典覆盖率 | 同上 |
| 写入口归一 | `worker/src/services/draft_service.py:34`（`_norm_notes` None→空/strip/cap2000）、`:40`（`_norm_source_batch` >64 字符 400 拒绝）、`ingest_node.py:82`（空 title fail-fast 出清不入箱） | drafts create/import/PATCH 三口 + 图管线入口 | 其余草稿字段（weight/price）无同款集中归一 |

### 1.2 一致性保障机制清单

| 机制 | 位置 | 覆盖范围 | 缺口 |
|---|---|---|---|
| 任务↔submission 对账归位（F-D04） | `worker/src/main.py:1093-1170`（`_periodic_task_cleanup`，r3 段把「任务已终态而 submission 仍 pending/uploading」按终态归位，60s 一轮幂等） | draft_submissions 卡死修复 | 只修 submission→task 方向；task 行被 30 天物理删后（r2）对应 submission 成悬空引用 |
| 僵尸任务有界复活 | 同上 r1/r1f（stale running 30min → retry_count+1 / 耗尽 failed） | 防无限重跑烧额度 | 无 |
| 订单增量同步 | `worker/src/services/store_sync_service.py:467`（since=上次−1h 重叠，首扫 90 天；cursor/has_next 续传，incomplete 标志:481）+ upsert 覆盖（:520 `ON CONFLICT DO UPDATE`） | 订单状态最终一致 | 无总量比对探针（本地行数 vs 平台返回 total） |
| 商品全量同步 | 同上：4（全量分页，本次未出现 → `archived=True` 不硬删） | 下架归档语义 | archived 行永不清理 |
| 终态写回 | `worker/src/utils/draft_status_writeback.py`（worker 终态→submission 状态映射，非致命） | 正常路径 | 崩溃窗口靠 F-D04 兜底（已闭环） |
| 佣金缓存守卫 | `worker/src/utils/commission_resolver.py:66-92`（`parse_prices_commissions` 段值 ≤0 → None 拒绝 0% 污染；`resolve_commission_rate` 优先级链 explicit > 缓存表 > extensions > 0.10） | category_commission 表读写 | 表全局共享无租户，错行影响全租户 |
| 字典缓存三桶 | `worker/src/utils/dict_value_cache.py`（global(0,0) 哨兵/scoped/ephemeral 不物化；TTL 30d:41；读侧 `expires_at > now` 过滤在 `local_db_manager.py:106`） | 防字典缓存撑爆盘 | 过期行只被读侧跳过、**永不物理删除**（靠 runbook 手动 TRUNCATE） |
| schema 懒加载回写 | `worker/src/services/category_schema_service.py:47`（缓存优先→按需拉一次→回写 30d→失败降级 found=false） | 交互面 99.8% 未预热类目 | 回写失败仅 debug 日志 |
| 余额缓存 | `worker/src/utils/mxou_api.py:56-61`（30s TTL + token 指纹绑定）+ `main.py:1377`（0.0 二次直查防缓存污染） | 防 402 误报 | 余额无本地账本，全信 MXOU（设计如此，非缺陷） |
| 数据擦除 | `worker/src/services/data_erasure_service.py:27`（STORE_SCOPED_TABLES 16 表硬删 + store_operation_log 审计）+ `routes/credentials_routes.py:88`（吊销=软删 revoked） | 店铺级 GDPR 式擦除 | `ozon_sessions`（cookie 密文，credential 级敏感数据）**不在** 16 表清单 |
| 迁移数据修复先例 | `worker/scripts/migrate_key_tenant_to_user.py`（dry-run 默认、`_mig_backup_<table>` 自动备份表:160、孤儿告警不删） | 租户迁移 | 一次性脚本，无框架化 |

### 1.3 备份恢复机制清单

| 机制 | 位置 | 覆盖范围 | 缺口 |
|---|---|---|---|
| PG 备份脚本 | `deploy/backup-pg.sh`（pg_dump + 可选 gpg 对称加密 + 14 天保留:8 + `--restore`:11） | 单库全量 | 依赖宿主机手配 crontab（`docs/DEPLOY.md:328`），compose 无备份 sidecar |
| 异地副本 | `docs/DEPLOY.md:342-350`（**建议项**：coscli 上传 .gpg，注意避开图片生命周期规则） | 无强制、无落地验证 | 未接 cron，脚本本身不含上传 |
| 恢复演练 | `docs/DEPLOY.md:348`（「并定期演练 restore」一句话） | 无 runbook、无记录 | 未定义 RTO/RPO 于运维文档（仅 PRD 目标 `docs/PRD-store-sync-erp-v1.md:605`「RPO 24h / RTO ≤1h」） |
| 升级前文件备份 | `deploy/cos-update.sh:178`（备份 deploy/ worker/ VERSION → backups/，排除 .env） | 代码/部署包 | .env（凭证/env 全集）**不备份** |
| 表结构变更 | `Base.metadata.create_all`（`worker/src/storage/database/db.py:92`，只建新表不加列）+ 手写幂等 ALTER（`scripts/migrate_webui_v1.py` / `migrate_sync_erp_v1.py` / `migrate_drafts_batch_v1.py`，由 `init_data.py:129-136` 统一调用，`cos-update.sh:275` 升级后必跑） | 加列/加索引 | **无 alembic、无 down migration、无 schema 版本表**；改列类型/改名无框架（`docs/DB-SCHEMA-AUDIT.md` 明文承认） |
| 配置备份 | compose bind mount `../worker/config:/app/config:rw`（`deploy/docker-compose.yml:66`），admin /config 写时带 backup（:65 注释） | LLM prompt 热加载配置 | 无版本化/异地 |
| 用户资产（生图） | COS bucket + `task_generated_images` 表（7 天缓存清理 `main.py:1158`） | 元数据可溯 | COS 图片删除=卡片无图（AGENTS 已知红线），无图片资产备份 |

---

## 2. 数据质量校验审计

**结论：上传链「出门前拦截」体系完整且分层（清洗归 prepare / 拦截归 validate / 自愈归 retry），是全仓最强一环；但校验维度集中在「属性+文本+重量尺寸」，图片规格与标题平台契约长度是盲区；脏/重复数据有真实防线，孤儿数据是欠账。**

### 2.1 校验类型矩阵

| 校验类型 | 状态 | 证据 |
|---|---|---|
| 枚举值（字典 dictionary_value_id>0） | ✅ 双道 | prepare 字典命中归 `attr_value_matcher`；validate `ozon_validate_node.py:307-321` |
| 值数上限（max_value_count） | ✅ 双道唯一入口 | `attr_value_sanitize.py` + validate:325 |
| 数值格式/类型/区间 | ✅ 半覆盖 | 格式类型全量（`attr_numeric_sanitize`），**区间只白名单 8962 一个属性** |
| 尺寸/重量平台契约 | ✅ 同源双道 | `OZON_DIM_BOUNDS_MM`（normalizer clamp + validate:267 报错）+ 密度兜底 `volume_weight_guard` |
| 多语言（俄语必含/中文禁/拉丁禁） | ✅ | validate:402-483（9048 型号豁免防误报:456） |
| 必填项（schema is_required） | ✅ 批量列全 | validate:357-378 + revalidate 终检（v0.73 混合值直通根治） |
| 标题-类目一致性 | ✅ 词面预检 | `common_cyr_words`（validate:36，≥4 字符西里尔公共词）+ `LOCAL_TITLE_CATEGORY_MISMATCH` 独立码 |
| **字符长度（标题/描述/属性值）** | ⚠️ 部分 | 标题 80 截断是**内部口径**（Ozon 契约上限更宽，未按平台文档核对）；描述/属性值无长度闸——超长靠 Ozon 事后拒 |
| **图片规格（分辨率/宽高比/格式/张数上限）** | ❌ 无 | 仅 URL 可达抽样（validate:566）+ 全外链镜像闸（:599）；无最小像素校验（Ozon 建议 ≥700px），低分辨率图靠 Ozon 事后降权/拒 |
| 类目枚举闭环 | ✅ | 树 16552 节点 + `MIN_CONF_BOX` 阻断入箱 + LLM 仲裁（v0.73）；`warm_dead_nodes` 死节点剔除防覆盖率虚高 |
| 价格合理性 | ✅ 半覆盖 | 价差守卫仅带锚流；无「价格为 0/负」类通用闸（pricing 层 [PRICING_FAILED] 兜） |

### 2.2 脏 / 孤儿 / 重复三类数据现状

| 类别 | 现状 | 证据与风险场景 |
|---|---|---|
| **脏数据** | ✅ **有机制（强）** | 入口：ingest 空 title fail-fast（`ingest_node.py:82-105`，9048 裸 item_id 根治）、draft_sanity 物理上限、价差守卫；出口：五套 sanitize 唯一入口；自愈：retry 子图按错误码分流（ML_INCORRECT_VOLUME_WEIGHT 反推重发、DESCRIPTION_DECLINE 重生成、R4 换类目）。**残留风险**：审核拒绝原文 `decline_errors` cap50 截断、`category_match_log` 历史 task_id 脏行（见下）。 |
| **孤儿数据** | ⚠️ **部分有机制** | ①已修：F-D04 submission 卡死归位（`main.py` r3 段）；②已知+文档化：`draft_submissions.submitted_task_id` Text 无 FK（`model.py:707`，DB-SCHEMA-AUDIT #4：存量脏引用阻塞建 FK，暂以普通索引过渡）；③**无机制**：credential 吊销/硬删后，`ozon_sessions` cookie 密文行残留（不在 `STORE_SCOPED_TABLES`，`data_erasure_service.py:27-44`）、`task_generated_images`/`category_match_log`/`attr_match_log` 在任务 30 天物理删后成悬空审计行（取证只能按时间戳）；④历史债：v0.67 前 match_log 写 ingest 随机 uuid，历史行无法 JOIN 任务表（DB-SCHEMA-AUDIT #1）。 |
| **重复数据** | ✅ **有机制（针对性强）** | ①任务级：`uq_ozon_product_tasks_tenant_sku` 部分唯一索引（`model.py:69-80`，仅 pending/running 参与，终态可重提——v0.38.1 修复 IntegrityError）；②卡片级：`_derive_model_name_9048`（item_id~sha1(supplier|标题)，跨卖家不并卡、跟卖刻意并卡）；③submission 级：`has_active_submission` 409 防重提（`draft_service.py`）；④映射行：`add_category_mapping` 同 (cid,dc,tp) 异措辞归并不裂行；⑤佣金 0% 污染行：resolver 双侧拒 0（v0.67）。**残留风险**：`store_metrics_history` 无业务唯一键（快照语义，有意）；CSV 导入 item_id 空时生成 `csv_` 随机 id（`draft_service.py:538`）——重复导入同一 CSV 产生重复草稿，无内容级幂等键。 |

---

## 3. 一致性审计（本地 ↔ Ozon 平台副本对）

**结论：六个副本对的更新触发与 TTL 各自成体系，但「对账」只有订单同步一处（且是增量窗而非计数比对）；不一致的修复路径多数是「等下轮同步覆盖」或「人工 runbook」，无自动探针、无漂移告警。汇率缓存是唯一彻底断链的副本对。**

| # | 副本对 | 更新触发 | 过期/TTL | 对账能力 | 不一致修复路径现状 |
|---|---|---|---|---|---|
| 1 | `ozon_orders_cache` ↔ Ozon FBS 订单 | 手动 sync 端点 + 同步 job（默认 15min） | `synced_at` 记录 | ⚠️ 半：增量 since−1h 重叠窗 + cursor 续传 + incomplete 标志（`store_sync_service.py:467-510`）；**无本地/平台计数比对** | 下轮 upsert 覆盖终态；无差异清单输出 |
| 2 | `ozon_products_cache` ↔ Ozon 在售商品 | 同步 job（默认 30min）+ 全量分页 | `synced_at` | ⚠️ 半：全量语义 + archived 归档（不硬删）可发现下架；**无价格/库存逐行 diff 告警** | 覆盖更新；改价/改库存写操作走 `store_actions` 且 `store_operation_log` 记 before/after（**全仓最好的追溯机制**） |
| 3 | `attribute_cache` ↔ Ozon 类目 schema | warm 预热 + assemble/retry 懒加载回写 + 交互懒加载（`category_schema_service.py:89`） | 30d（读侧 expires_at 过滤） | ❌ 无（coverage 只管预热覆盖率，不管 schema 内容漂移） | 过期后读侧跳过 → 触发懒加载回源；Ozon 改 schema（加必填）在 TTL 内不可见 |
| 4 | `dictionary_value_cache` ↔ Ozon 字典 | warm（三桶路由）+ init_data 导入 + assemble 按需 | 30d 读侧过滤；**过期行不物理删** | ❌ 无 | runbook 手动 TRUNCATE + 重预热（docs/CACHE-WARM-RUNBOOK.md，分片 ≤2）；ephemeral 桶走 /values/search 实时 |
| 5 | `category_commission` ↔ Ozon 佣金 | 上架成功回填（prices_api 源）+ what_to_sell 分段 | **无 TTL**（佣金随价格段变） | ❌ 无 | ≤0 拒绝守卫防脏行；无新鲜度重查机制——旧费率可能长期用于定价 |
| 6 | `exchange_rates` ↔ 实际汇率 | **❌ 零写者**：`set_exchange_rate`（`local_db_manager.py:426`）全仓无调用方 | 读侧 24h 新鲜度检查（:186） | ❌ | 过期/未命中 → `pricing_node.py:493` **静默回落硬编码 12.0**。CNY/RUB 实际汇率偏离 12.0 时所有定价系统性偏差，且无告警——**P1，详见 G-01** |

**不一致时的修复/回滚/追溯总评**：
- **订单**：无修复动作（只读镜像），状态靠覆盖收敛；
- **余额**：纯外部（MXOU），本地零副本，30s 缓存 + 0.0 二次直查（`main.py:1377`）已把误报修到可用；
- **库存/价格**：`store_actions` 写操作有 before/after 审计（可追溯），但**无回滚端点**——改错价只能反向再调一次；
- **任务/上架**：追溯链最全（`listing_result_log` append-only 永久留存 + `decline_errors` 审核原文 + marks 留痕 + Sentry 上报 + `GET /api/v1/forensics/task/{id}` 一站式聚合）；
- **卡片区**：`repair_cards.py` 提供归档→删除→重建修复流（`--dry-run` 设计良好，凭证硬编码见 G-05）；
- **类目学习**：负反馈闭环（declined → fail+1 → 3 次下线，curated 恒 active）。

---

## 4. 备份恢复审计

**结论：有脚本、无保障。备份可达性依赖「运维记得配 crontab」这一单一人为环节；恢复从未被演练验证；表结构变更单向不可逆。以 PRD 自定目标（RPO 24h/RTO ≤1h）衡量，当前 RPO 实际=「上次手跑备份」（可能无穷大），RTO 未测。**

| 维度 | 现状 | 缺口 |
|---|---|---|
| DB 备份 | `backup-pg.sh`：pg_dump 明文→可选 gpg→14 天保留→`--restore` 通道 | ①cron 靠手配，compose 无备份 sidecar；②备份落本机与库同盘同命运，异地副本是文档「建议项」未强制；③pg_dump 非 WAL 归档——两次备份间的写入 RPO=24h 且无 point-in-time |
| 恢复验证 | `--restore` 直接灌生产库（无 `--dry-run`/临时库验证） | 无恢复演练 runbook、无恢复时间实测记录；恢复会**覆盖**而非并行比对 |
| RTO/RPO | 仅 PRD 目标值，DEPLOY.md 运维手册未承接 | 未定义、未测、未监控（备份失败无告警——cron 静默失败只有 log 文件） |
| 配置/凭证备份 | config/ bind mount；cos-update 备份 deploy/ 时 **--exclude .env** | .env（Supabase/MXOU/COS/CREDENTIAL_MASTER_KEY 全集）零备份——盘损后 `credentials.ozon_api_key_enc` 密文永久不可解 |
| 用户资产 | 生图落 COS + 元数据 7 天清理；listing_result_log 长期事实留存（设计正确） | COS 图片资产无独立备份策略（生命周期误删事故史） |
| 表结构变更回滚 | `create_all`（只加表）+ 幂等 ALTER（只加列/索引）；`_mig_backup_<table>` 表级备份先例（migrate_key_tenant_to_user.py:160） | **无 alembic/无 down migration/无 schema 版本表**：改列类型、删列、改约束无框架支撑，回滚=restore 整库备份 |
| 授权信息备份 | tokens 在外部 Supabase；`ozon_sessions`/`credentials` 在 PG 内随库备份（密文态，口令 `CREDENTIAL_MASTER_KEY` 在 .env） | 主密钥丢失=全部店铺凭证作废，无密钥托管/恢复方案文档 |

---

## 5. 发现清单（P0-P3）

### P0（无）
未发现「会造成数据不可逆丢失且正在发生」的问题——最接近的是 G-01（汇率断链，影响定价正确性而非丢失）。

### P1

**G-01 汇率缓存有读无写，定价静默退化为硬编码 12.0**
- 证据：`worker/src/utils/local_db_manager.py:426`（`set_exchange_rate` 全仓零调用方）+ `:186`（读侧 24h 过期即返 None）+ `worker/src/graphs/nodes/pricing_node.py:493`（`logger.warning("PG 汇率缓存未命中，使用默认汇率 12.0")`）
- 影响：exchange_rates 表无自动刷新，部署 24h 后所有 CNY→RUB 定价按 12.0 计算；实际汇率偏离 5% 即全租户售价系统性偏差，且仅 warning 日志无告警。
- 修复建议：每日定时任务拉取汇率写 `set_exchange_rate`；回落 12.0 时升 Sentry 事件并在 pricing_info marks 标 `exchange_rate_fallback`。
- 探针：只读 SQL `SELECT updated_at FROM exchange_rates WHERE from_currency='CNY' AND to_currency='RUB'` 与 NOW() 差值 >24h 即红。

**G-02 备份链路无强制执行点、无恢复演练，RTO/RPO 未运营化**
- 证据：`deploy/docker-compose.yml`（无备份 sidecar）+ `docs/DEPLOY.md:317-350`（crontab 手配）+ RTO/RPO 仅 PRD 目标
- 影响：单一人为环节失效（换机/忘配/cron 静默失败）= 零备份运行；恢复从未验证，真实 RTO 未知。
- 修复建议：①compose 加 cron sidecar 跑 backup-pg.sh；②备份成功后写心跳 + `/api/v1/health` 附 last_backup_at（超 26h 降级 unhealthy）；③写 RESTORE-RUNBOOK：临时库恢复→行数抽样比对→切换，演练记录入库。
- 探针：健康端点断言 last_backup_at < 26h。

**G-03 表结构变更无版本化框架，迁移单向不可逆**
- 证据：`worker/src/storage/database/db.py:92`（仅 create_all）+ DB-SCHEMA-AUDIT 明文承认 + `cos-update.sh:275`（升级必跑 init_data 兜底）
- 影响：任何破坏性 DDL 只能整库 restore 回滚（受 G-02 制约）；存量脏引用永久阻塞加 FK。
- 修复建议：最小改法——①建 `schema_migrations` 版本表 + 把现有三个 migrate_*.py 纳入编号管理；②破坏性变更先跑「预检 SQL」（孤儿引用计数）再放行；③沿用 `_mig_backup_<table>` 先例写入 CI 模板。
- 探针：CI 步骤比对 `information_schema.columns` 快照 vs model.py 声明（漂移即红）——gen_api_docs --check 思路的 schema 版。

### P2

**G-04 credential 吊销/硬删后 `ozon_sessions`（cookie 密文）残留**
- 证据：`data_erasure_service.py:27-44`（STORE_SCOPED_TABLES 16 表无 ozon_sessions）+ revoke 不触会话
- 修复：加入 STORE_SCOPED_TABLES；revoke 时级联。探针：`SELECT count(*) FROM ozon_sessions s LEFT JOIN credentials c ON c.id=s.credential_id WHERE c.status='revoked'`。

**G-05 `repair_cards.py` 硬编码真实店铺 API key 入库**
- 证据：`worker/scripts/repair_cards.py:34-35`（CLIENT_ID="5371047" / API_KEY="db64d282-…"）
- 影响：Ozon 凭证明文进 git 历史。修复：env 参数化 + 轮换 key + git 历史清除（filter-repo，Tier A）。探针：`grep -rn "API_KEY\s*=" worker/scripts/*.py`。

**G-06 图片规格（分辨率/宽高比/张数）零校验**
- 证据：`ozon_validate_node.py:566-612` 仅可达性。修复：像素下限检查（Pillow 已在依赖链），先 warning 观测后拦截。探针：抽样 IMAGE_ERROR 拒单任务的图片像素分布。

**G-07 缓存过期行只被读侧跳过、永不物理删除**
- 证据：`local_db_manager.py:106` + 全仓无 DELETE 自动路径（仅 runbook 手动 TRUNCATE；v0.72 事故正因此表 4GB+）。
- 修复：`_periodic_task_cleanup` 每日顺带批量 DELETE 过期行。探针：死行占比 SQL。

### P3

- **G-08 CSV 重复导入无内容级幂等**（`draft_service.py:538` csv_ 随机 id）——(tenant, title+purchase_url) 哈希幂等键。
- **G-09 标题 80 字符截断是内部口径，未锚定平台契约**（title_sanitizer.py:126）——用 ozon mcp 核对 import name 上限并锚定注释。
- **G-10 archived 缓存行与审计悬空行无生命周期**（archived>180d 清理；审计行加 retired 标记）。

---

**探针优先级建议**：先落 G-01（汇率 mtime）与 G-02（last_backup_at）两个只读探针进 `/health`——一个 SQL 就把两个 P1 变成可监控项；G-03 的 schema 漂移检查可复用 `gen_api_docs.py --check` 的 CI 门禁模式。
