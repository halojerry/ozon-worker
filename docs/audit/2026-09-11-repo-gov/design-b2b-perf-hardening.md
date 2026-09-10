# 设计 — B2-β / BL-25：缓存三防 + PG 超时/池配比 + priority 消费 + checkpoint 清理

> 状态：待评审 ｜ 依据：A9 §1 Q9.2-Q9.4 / S9-03、S9-04、S9-05、S9-06（P2 方案包）｜ 基线 v0.74.0 @ 7a13d7ab
> 四个子项相互独立，可分拆实施；每项给最小改动点（file:line）与分期。

## 1. 背景与证据

- **缓存三防全缺**（A9 S9-04）：穿透——`OzonCategoryQuery.get_dictionary_values` miss 返回 None → 调用方回源 Ozon（ozon_category_query.py:1493-1535），不存在的 (attr,dc,tp) 每任务重复回源；击穿——miss 无 singleflight，同 key 并发 miss 全部回源；雪崩——`DICT_CACHE_TTL_SECONDS=30d` 按写入时刻定 expires_at（dict_value_cache.py:41），同次 warm 的行同日集中过期。
- **PG 无 statement_timeout + 池 15 vs 并发 30**（S9-03）：`db.py:33-44` pool_size=5 + max_overflow=10、pool_timeout=30s；MAX_CONCURRENT 默认 30（main.py:482）——池耗尽时认领/写库排队 30s 放大延迟（task_processor.py:444-446 注释自认）。慢查询无闸，一条失控 SQL 可占住连接。
- **priority 有通道无写入**（S9-05）：认领 SQL 已 `ORDER BY priority DESC, created_at ASC`（task_processor.py:460），但写入侧恒 0（main.py:1825「直到建立 VIP 体系」）——FIFO 下单租户 300/min 批量可把他租户压队尾。
- **memory checkpoint 零清理**（S9-06）：`_periodic_task_cleanup`（main.py:1158-1200）只物理删任务表（30d completed），PostgresSaver 的 `memory.checkpoints/checkpoint_blobs/checkpoint_writes`（memory_saver.py:51-56 建表）按 thread_id 累积——每任务数百次 checkpoint 写，只进不出。

## 2. 方案选项

### 2.1 三防（推荐：进程内轻量三件套，集中 dict_value_cache 唯一入口）

- **A 负缓存 60s + singleflight + TTL 抖动**：`routed_get` miss 记进程内负缓存 `{key: exp}`（60s）；同 key miss 用 per-key 锁只放一个回源（in-flight dict + threading.Lock，进程内即够——workers=1 是现部署契约）；`routed_set` 默认 TTL ×uniform(0.9,1.1)。
  ✅ 改动集中、零 DDL、不碰 Ozon 调用方。❌ 负缓存可能短窗内屏蔽「刚建的合法 key」——60s 自愈，可接受。
- **B 落 PG 层**（负缓存表/`pg_advisory_lock` 击穿闸）：跨副本正确但重；workers=1 现状下收益为零，待多副本批次（S10-04）再议。
- **C 只加抖动不动穿透/击穿**：最省但冷启动全 miss 窗口原样（TRUNCATE 重预热后首波最痛的场景恰是穿透+击穿）。

### 2.2 statement_timeout + 池配比

- **A（推荐）**：`create_engine` 加 `connect_args={"options": "-c statement_timeout=30000"}`（30s；warm 大事务脚本自建 engine 不受此限）；池改 pool_size=20 + max_overflow=20（≥MAX_CONCURRENT×1.3，A9 建议池容量与并发联动）。
- **B 只调池不加 timeout**：治排队不治慢查询；一条 10 分钟 SQL 照旧占死连接。
- **C 全局 5s 激进 timeout**：会误杀生图元数据写/取证聚合等合法慢查，30s 取「远大于正常查询、远小于 pool_timeout=30s 排队」的中间量级。

### 2.3 priority 消费

- **A（推荐两步）**：①submit_task 请求体开放 `priority`（clamp 0-100，默认 0，main.py:1825 改为读参）——认领 SQL 零改动即生效；②租户公平：认领 SQL 改窗口函数按 tenant 轮转（`ROW_NUMBER() OVER (PARTITION BY tenant_id ORDER BY priority DESC, created_at)` 取 rn=1），防高优租户独占。
- **B 只做 ②**：公平但无法表达「VIP 插队」的产品语义。
- **C 引 Redis 优先队列**：引入新单点，违背「队列层已多副本安全、瓶颈不在队列」的架构事实（A9 §0.7）。

### 2.4 checkpoint 清理

- **A（推荐）**：`_periodic_task_cleanup` 删任务前先收集将删 id，再按 thread_id 删三张 checkpoint 表（`DELETE FROM memory.checkpoints WHERE thread_id = ANY(:ids)`，bind 传数组；配套 checkpoint_blobs/checkpoint_writes）。
- **B 按时间删**（`thread_id` 无时间列，需 join 任务表）：任务行已删的老 checkpoint 永远 join 不上——恰是现在漏掉的存量，必须先一次性清一次全量（按 created_at 序数截断或直接 TRUNCATE 一次 + 通知用户在跑任务会丢恢复点）。

## 3. 推荐方案与分期

**Phase 1（止血，各一小时内）**
1. 三防-雪崩：`dict_value_cache.routed_set`/warm `_write_node_to_pg`（warm_category_cache.py:219-220）expires 加 ±10% 抖动（纯函数 `_jitter(ttl)` 可单测）。
2. checkpoint：清理循环追加三表 DELETE（main.py:1187 r2 之前收集 id）+ 一次性存量清理脚本 `scripts/cleanup_checkpoints.py --before <date>`。
3. statement_timeout：db.py connect_args 30s；pool_size 5→20 / overflow 10→20。

**Phase 2（结构性）**
4. 三防-穿透+击穿：`routed_get` 负缓存 60s + per-key singleflight（dict_value_cache.py 集中实现，导出计数器供探针）。
5. priority 写入侧开放（main.py:1825 读请求参数 + clamp）；webui/skill 默认不传=0，行为不变。

**Phase 3（公平性，独立评审）**
6. 认领 SQL 租户轮转（task_processor.py:460 改窗口函数；SKIP LOCKED 语义不变，需回归并发认领测试）。

## 4. 验收探针

- 三防：单测锁「同 key 并发 10 次 miss 只回源 1 次」「miss 后 60s 内不再回源」「同批写入 expires_at 分散（max-min > 0）」；运行时「同分钟同 key 回源次数」日志聚合 ≤1。
- PG：`pg_stat_statements` 无 >30s 查询（被 timeout 闸掉即告警）；pool checkout 等待计数（SQLAlchemy pool events）趋零；`SHOW statement_timeout` 会话级断言测试。
- priority：双租户夹具 A 提交 50 单 priority=50、B 提交 1 单 priority=0 → 断言 B 的认领等待分位不劣于无 A 场景的 2 倍。
- checkpoint：清理循环跑一轮后 `SELECT count(*) FROM memory.checkpoints WHERE thread_id IN (SELECT id FROM ozon_product_tasks WHERE status='completed' AND updated_at < now()-'30d')` = 0；memory schema 体积日探针（A9 §5 探针②）转平。
