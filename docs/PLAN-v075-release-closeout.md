---
status: 已完成（2026-09-11，PR #21 已合 dev；实机 gate 待用户）
scope: v0.75.0 收口批——repo-gov 战役（PR #13-#19）全部余量 + 发版物料 + 验证矩阵
branch: feat/v075-closeout（worktree ../ozon-worker-v075）
---

# v0.75.0 收口批实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.
> 执行模型：主会话（GLM-5.3）派发/两阶段 review/提交；执行 subagent 按 glm-5.3-flash 规格（自包含提示词、窄文件组、无 git 操作权）。

**Goal:** 把 repo-gov 审计（docs/audit/2026-09-11-repo-gov/）与六批修复（PR #13/#15/#16/#17/#18/#19）的全部代码余量收口，补齐 API 示例面，跑全量验证矩阵，产出 v0.75.0 发版物料。

**Architecture:** 三波 subagent 实施（文件组互斥）→ 主会话 review + 逐波 commit 到单一分支 feat/v075-closeout → 一个 PR 合 dev → 发版物料在主 worktree dev 直提（发版豁免）→ 用户实机 gate → dev→main PR + tag。

**Tech Stack:** FastAPI + LangGraph + SQLAlchemy（幂等 DDL 迁移走 init_data.py 先例）、pytest（skill/.venv314）、gen_api_docs.py（示例门禁）。

## Global Constraints（每个任务隐含遵守）

- worker 测试命令：`cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/<file> -q`；全量前必 `lsof -iTCP:5433 -sTCP:LISTEN` 核实是 compose PG。
- 纯 mock 单文件可省 PGDATABASE_URL；**裸 SQL 绑 JSONB/数组列必须 `json.dumps` / `CAST(:x AS 实际类型)`**（记忆 sqlalchemy-jsonb-cast-trap）。
- **密钥纪律**：测试夹具里的假 key 用非关键词常量名 + f-string 占位（gitleaks generic-api-key 会命中 `token: <10+小写串>` 形态）；真实凭证绝不进任何文件。
- langgraph 纪律：节点/路由要读的 state 字段必须声明进该节点 Input model。
- 改 API 面（含 schema examples）后由主会话统一跑 `python worker/scripts/gen_api_docs.py`（快照重生成），subagent 不跑全量重生成（防并行踩快照）。
- subagent 不执行任何 git 写操作（add/commit/push 由主会话做）。
- ruff CI 口径：worker `ruff check src/ --select E,F,W --ignore E501`。

## 文件结构总览（任务↔文件互斥表）

| 任务 | 独占/主要文件 |
|---|---|
| C1 单飞锁 | worker/src/utils/dict_value_cache.py、tests/test_dict_cache_singleflight.py |
| C4 bounds 闭环 | worker/src/utils/attr_numeric_sanitize.py、worker/src/storage/database/shared/model.py（新表）、worker/src/graphs/nodes/validation_retry_loop.py（回填点）、worker/scripts/init_data.py（登记）、tests/test_attr_bounds_learned.py |
| C5 is_aspect 收窄 | worker/src/utils/attribute_utils.py、tests/test_aspect_fallback_v075.py |
| C6 佣金续期 | worker/src/utils/commission_resolver.py、tests/test_commission_stale_v075.py（扩） |
| C8 绑店原子化 | worker/src/services/credential_service.py、tests/test_credential_bind_atomic_v075.py |
| C2 checkpoint 清理 | worker/src/main.py（_periodic_task_cleanup）、worker/scripts/cleanup_checkpoints.py（新）、tests/test_checkpoint_cleanup_v075.py |
| C3 租户 guard 收尾 | worker/src/main.py（categories/attributes 端点）、model.py（审计表 tenant_id）、写入点（ozon_category_query/prepare 审计写点）、init_data.py、tests/test_tenant_guard_phase3.py |
| C7 priority 开放 | worker/src/main.py（submit_task）、worker/src/api/schemas.py、tests/test_submit_priority_v075.py |
| C9 示例二期 | api/schemas.py、routes/*.py、main.py inline 路由、gen_api_docs.py（strict）、.github/workflows/ci.yml、scripts/ci.sh |
| C10 mcp 对齐 | pounding-mcp/pounding_mcp/server.py（如发现漂移）、docs/MCP-SERVER.md |

---

## Wave 1（4 任务并行，文件组互斥）

### Task C1: 字典缓存单飞锁（BL-25 Phase 2-4 防击穿）

**Files:**
- Modify: `worker/src/utils/dict_value_cache.py`（模块注释 :53「留二期」处）
- Test: `worker/tests/test_dict_cache_singleflight.py`（新建）

**Interfaces:**
- Produces: `dict_value_cache.singleflight_stats() -> dict`（导出计数器 `{"inflight_hits": int, "acquired": int}`，探针面）；内部 `_SF_LOCK: threading.Lock` + `_INFLIGHT: set[tuple]`。
- 消费方不变：`routed_get`/调用方零改动——singleflight 包在「miss 后回源」的外层调用处（模块内提供 `acquire_singleflight(key) -> _Lease` 上下文管理器，miss 回源路径必须经它）。

**背景**：B4 已落负缓存 60s + TTL 抖动 ±10%（`record_negative` :97）；唯一余量是同 key 并发 miss 全部回源（击穿）。workers=1 是现部署契约 → 进程内锁即够（设计文档 design-b2b-perf-hardening.md §2.1-A）。

- [ ] **Step 1: 写失败测试**（并发语义用线程 + 屏障）

```python
def test_singleflight_dedup_concurrent_miss():
    """同 key 并发 10 次 miss 回源，底层 fetch 只执行 1 次，其余等结果。"""
    import threading
    from worker_src.utils import dict_value_cache as dvc  # 按 PYTHONPATH=src 实际 import 路径调整
    calls = {"n": 0}
    barrier = threading.Barrier(10)
    def slow_fetch():
        barrier.wait() if False else None
        with dvc.acquire_singleflight(("attr", 1, 2, "RU")):
            calls["n"] += 1
            time.sleep(0.05)  # 模拟回源耗时，放大并发窗口
    threads = [threading.Thread(target=slow_fetch) for _ in range(10)]
    # 断言：10 线程全部 acquire 成功（串行化），但若换成「计数器只增一次」的
    # fetch-封装语义则 fetch_calls == 1 —— 按下方 Step 3 的封装函数测
```

最终形态测两个函数：①`acquire_singleflight(key)` per-key 互斥（同 key 并发只有一个线程持锁，另一个阻塞等待）；②`run_exclusive(key, fn)` 封装——同 key 并发 10 线程调 `fn` 只执行 1 次且全部拿到同一返回值；③`singleflight_stats()` 计数递增；④不同 key 不互斥（并行度不退化）。

- [ ] **Step 2: 跑测试确认失败**（`ModuleNotFoundError: acquire_singleflight`）
- [ ] **Step 3: 最小实现**：模块级 `_SF_LOCK = threading.Lock()`、`_INFLIGHT: set = set()`、`_SF_STATS = {"inflight_hits": 0, "acquired": 0}`；`acquire_singleflight(key)` 上下文管理器（进 set 前 loop：key 在 set 中 → 计 inflight_hits + `_SF_COND.wait(timeout=30)`，防死锁；拿到后 acquired+1）；`run_exclusive(key, fn)` 用它包 fn 执行并返回结果；`singleflight_stats()` 返回计数副本。在 miss 回源调用链（`routed_get` miss 分支的回源处/或调用方 `OzonCategoryQuery` 的 fetch 包装——以模块注释指引的「miss 后回源」唯一热路径为准）接 `run_exclusive`。**不许把锁加到 set/get PG 路径上（那是共享读，不能串行化）。**
- [ ] **Step 4: 跑测试通过** + 既有 `tests/test_dict_cache_three_bucket.py` 全绿（零回归）。

### Task C4: 数值 bounds 拒单学习闭环（BL-30 / A4 F-P1-1）

**Files:**
- Modify: `worker/src/utils/attr_numeric_sanitize.py`（NUMERIC_ATTR_BOUNDS :32-34 白名单仅 8962）
- Modify: `worker/src/storage/database/shared/model.py`（新表 AttrBoundLearned，紧跟 AttrMatchLog 附近）
- Modify: `worker/src/graphs/nodes/validation_retry_loop.py`（decline 消费点回填）
- Modify: `worker/scripts/init_data.py`（migrate_repo_gov_v075：建表由 create_all 覆盖，无需 ALTER；仅 `register_schema_migration(engine, "repo_gov_v075_bounds", ...)` 登记行并入既有调用序列）
- Test: `worker/tests/test_attr_bounds_learned.py`（新建）

**Interfaces:**
- Produces:
  - `attr_numeric_sanitize.parse_numeric_bounds_from_decline(text: str) -> list[tuple[int, float, float]]`——从 decline 原文抽 `(attr_id, lo, hi)`；解析不 confident 返回 []（宁可不学）。
  - `attr_numeric_sanitize.learn_bounds_from_decline(text: str) -> int`——parse → upsert `attr_bounds_learned`，返回学习条数（DB 不可用静默 0，绝不 raise 进 retry 主链）。
  - `attr_numeric_sanitize.get_learned_bounds(attr_id: int) -> tuple[float, float] | None`——进程内 60s TTL 缓存（模块级 dict，容量 cap 1000 简单清空即可）。
  - 读侧优先级：静态 `NUMERIC_ATTR_BOUNDS`（人工，恒赢）> 学习表 > None（现状不夹取）。`sanitize_numeric_attr_value` 行为不变签名不变（内部查学习表）。

- [ ] **Step 1: 失败测试**（纯 mock，PG 用 monkeypatch 引擎或 sqlite/建表于临时 PG——按 tests/test_commission_resolver.py 先例选纯函数 + mock get_engine）：

```python
def test_parse_max_limit_ru_text():
    text = "Значение характеристики 8962 не должно превышать 10000 (Термос…)"
    assert parse_numeric_bounds_from_decline(text) == []  # 若无法同时置信抽到 attr_id+界 → 不学

def test_parse_confident_pair():
    # 格式以 v0.69 生产三店扫描 42 例的 VALUE_MAX_LIMIT/MIN_LIMIT 原文为蓝本写两条正例
    # （执行者：在 validation_retry_loop/错误码表中找已有 VALUE_MAX_LIMIT 处理先例取真实文案形态）
    ...

def test_static_whitelist_wins_over_learned():
    # 8962 静态 [1,10000] 存在时，学习表即便有别的值，读侧仍返回静态值
    ...

def test_sanitize_uses_learned_bounds(monkeypatch):
    # 学习表 22333 → (0, 5000)；值 9999 被夹取到 5000
    ...
```

- [ ] **Step 2: 确认失败** → **Step 3: 实现**：
  - 新表 `AttrBoundLearned`：`attr_id BigInteger PK`、`min_value Float nullable`、`max_value Float nullable`、`source String(32) default 'decline_learned'`、`sample Text`（原文片段 cap 200，人工复核面）、`updated_at` epoch。**新表 = 全局共享（对齐 category_commission W11 先例），无 tenant_id。**
  - parser：正则抽 `attr_id`（decline 文案中「характеристик[аи]? (\d+)」或「id[: ](\d+)」形态）+ 界值（«не должн[о|а] превышать (\d+)» → max；«(не менее|минимум) (\d+)» → min）。**只当单条消息能同时定位 attr_id 与至少一个界才产出**；同一 attr 多条历史由 upsert 合并（已学界与新界取并集区间：min 取 max(min)，max 取 min(max)——收紧不放松）。
  - 回填点：validation_retry_loop 消费 moderation_texts/decline 原文处（`_accumulate_decline_errors` 之后、parse_error 分支内）调 `learn_bounds_from_decline(text)`，包 try/except 静默（学习失败不影响重试主链）。
  - sanitize 读侧接 `get_learned_bounds`。
- [ ] **Step 4: 测试通过** + `tests/test_audit_a_fixes.py`、既有 attr numeric 相关测试全绿。

### Task C5: is_aspect 名称关键词兜底收窄（A4 F-P1-2）

**Files:**
- Modify: `worker/src/utils/attribute_utils.py`（`is_aspect_attr` :78、`ASPECT_ATTR_NAME_KEYWORDS` :28）
- Test: `worker/tests/test_aspect_fallback_v075.py`（新建）

**Interfaces:** `is_aspect_attr(attr_id, attr_name, schema_entries)` 签名不变，语义收窄：
1. schema 行存在且**显式含** `is_aspect` 键 → 用显式值（true/false 都尊重，**false 不再落关键词兜底**）。
2. schema 行存在但**缺** `is_aspect` 键 → 关键词兜底，且要求该行 `dictionary_id > 0`（aspect 是字典形态属性；«вид деятельности» 之类自由文本不再误伤）。
3. 无 schema 行 → 关键词兜底（现状保持，revalidate 安全优先）。

- [ ] **Step 1: 失败测试**：

```python
def test_explicit_false_wins():
    assert is_aspect_attr(1234, "Цвет", schema_entries=[{"id": 1234, "is_aspect": False, "dictionary_id": 1}]) is False

def test_missing_key_requires_dictionary():
    assert is_aspect_attr(1235, "Вид деятельности", schema_entries=[{"id": 1235, "dictionary_id": 0}]) is False
    assert is_aspect_attr(1236, "Тип", schema_entries=[{"id": 1236, "dictionary_id": 1960}]) is True

def test_no_schema_falls_back_to_keywords():
    assert is_aspect_attr(None, "颜色", schema_entries=None) is True
```

- [ ] **Step 2: 失败确认** → **Step 3: 实现**（改 `is_aspect_attr` 的分支序 + `match_attr_name` 处调用点同步——grep 该函数全部调用方确认无第二处内联关键词判断）→ **Step 4: 通过 + 既有 `test_hazard_attr_fallback.py` / attr_value_matcher 相关测试全绿**。行为变更（构建期更多含 тип/цвет 词的可选特征参与填充）写进任务输出说明，主会话汇入 CHANGELOG。

### Task C6: 佣金缓存随用续期（BL-24 Phase 3-7）

**Files:**
- Modify: `worker/src/utils/commission_resolver.py`（`upsert_category_commission`）
- Test: `worker/tests/test_commission_stale_v075.py`（扩既有文件）

**Interfaces:** `upsert_category_commission` 不变签名；语义补：what_to_sell 分段 upsert 路径（source 非 prices_api 的回填）**必须刷新 `updated_at`**——确认列是否 `onupdate` 自动刷；不是则在 UPDATE 分支显式 `updated_at = extract(epoch from now())`。

- [ ] **Step 1: 失败测试**：行 updated_at=200d 前 → 再跑一次 what_to_sell upsert（mock 同既有测试）→ `get_category_commission` 返回的 updated_at 为当前（即 180d 新鲜度闸自然解除，超龄告警随使用归零）。
- [ ] **Step 2/3/4**：确认失败 → 实现 → 通过（含既有 stale 闸测试不回归）。

### Task C8: 跨租户绑店原子化（BL-26 代码半 / A8 F3）

**Files:**
- Modify: `worker/src/services/credential_service.py`（`_assert_client_not_bound_elsewhere` :57 注释自认「无 DB 级锁」）
- Test: `worker/tests/test_credential_bind_atomic_v075.py`（新建）

**Interfaces:** 预检+INSERT 同事务首行执行 `SELECT pg_advisory_xact_lock(hashtext(:ozon_client_id))`——并发双绑窗口关闭；既有 409 拦截测试保持绿。

- [ ] **Step 1: 失败测试**：捕获 SQL 序列断言（既有 mock conn 先例）——创建凭证事务内第一条语句是 advisory lock 且与预检 SELECT/INSERT 同一事务（`conn.in_transaction()` 为真 / begin 上下文同一 conn）。
- [ ] **Step 2/3/4**：失败 → 实现（注意：get_session 每次新 session；把 lock 放进创建凭证的同一 `with get_session() as s:` 事务体首行，hashtext 需要 `text("SELECT pg_advisory_xact_lock(hashtext(:cid))")` 绑参）→ 通过 + 既有 `test_credentials_validation_422.py` 与凭证 CRUD 测试全绿。
- **范围外（defer）**：历史双绑清查（需 S3 SSH 探针）、哈希租户迁移守卫（A8 F2，运行时告警噪音风险 → 与 SSH 半一起 defer，登记 BACKLOG）。

---

## Wave 2（2 任务并行）

### Task C2: memory checkpoint 三表清理（BL-25 Phase 1-2 漏项）

**Files:**
- Modify: `worker/src/main.py`（`_periodic_task_cleanup` :1181——删任务行**前**收集 id）
- Create: `worker/scripts/cleanup_checkpoints.py`（存量孤儿一次性清理：thread_id NOT IN (SELECT id FROM ozon_product_tasks)，ctid 批量 5000）
- Test: `worker/tests/test_checkpoint_cleanup_v075.py`（新建）

**探针先行**：执行者先连本地 PG（5433）`SELECT table_name FROM information_schema.tables WHERE table_schema='memory'` 确认三表实名（langgraph PostgresSaver 惯例 checkpoints/checkpoint_blobs/checkpoint_writes）+ `SELECT thread_id FROM memory.checkpoints LIMIT 3` 对拍 task_id 形态（uuid 一致即 thread_id==task_id；若不等，先找 task_processor 的 config thread_id 赋值点再写清理键）。**清理键必须以探针实证为准，不许按惯例名盲写。**

**Interfaces:**
- `_periodic_task_cleanup` 内新私有函数 `_purge_checkpoints(conn, task_ids: list[str]) -> int`——`DELETE FROM memory.checkpoints WHERE thread_id = ANY(:ids)`（bind 传 Python list of str，psycopg2 自动数组化；三表依次，先 checkpoints 再 blobs/writes 或按探针的外键依赖序），返回删除行数记 debug 日志。
- 脚本 `cleanup_checkpoints.py --db-url $PGDATABASE_URL [--dry-run]`：孤儿 thread_id 全量清理 + 输出清了三表各多少行。

- [ ] **Step 1: 失败测试**：mock conn（既有 main 测试先例 monkeypatch get_engine）——删任务 DELETE 前收集 id + 三表 ANY(:ids) DELETE 各执行一次 + 顺序正确（收集先于任务 DELETE）。
- [ ] **Step 2/3/4**：失败 → 实现 → 通过（跑 worker 全量前该文件先独立绿）。

### Task C3: 租户 guard 收尾（BL-17 Phase 2/3 余量）

**Files:**
- Modify: `worker/src/main.py`（categories/attributes 端点 :2837 区域迁 `Depends(get_tenant)`）
- Modify: `worker/src/storage/database/shared/model.py`（CategoryMatchLog/AttrMatchLog 补 `tenant_id: Mapped[Optional[str]]` + 索引）
- Modify: 写入点——grep `INSERT INTO category_match_log` / `INSERT INTO attr_match_log`（assemble 链 `_write_match_row` 类函数与 prepare 审计写点，各自带上租户参数）
- Modify: `worker/scripts/init_data.py`（migrate_repo_gov_v075：两表 `ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50)` + 索引 + `register_schema_migration`；历史回填 `UPDATE category_match_log m SET tenant_id = t.tenant_id FROM ozon_product_tasks t WHERE m.task_id::text = t.id::text AND t.tenant_id IS NOT NULL`——join 不上的保持 NULL 并 SELECT count 记日志，P1-6 断裂如实）
- Test: `worker/tests/test_tenant_guard_phase3.py`（新建）

**Interfaces:**
- 端点迁移：复用 B4 先例（`routes/error_reports_routes.py` 头部四步接线注释 + `deps_tenant.get_tenant`）；categories/attributes 两端点行为不变（错误码/限流序不变——get_tenant dependency 已含同语义解析）。
- CI 断言测试（防复发闸）：import app 后遍历 `app.routes`，对租户面读端点集合（drafts/credentials/orders/forensics/error_reports/tasks 面已知清单）断言 handler 签名含 `tenant` 注入参数或路由 dependencies 含 `get_tenant`；另 grep 源码断言「FROM category_match_log / attr_match_log 的裸查询不带 tenant 条件且无任务表 join」为零（正则白名单豁免回填迁移与写点 INSERT）。
- 写入点改造：审计写点函数补 `tenant_id` 入参（值源 = state 的租户字段；节点 Input 需要的按 langgraph 纪律**必须声明进该节点 Input model**——grep 写点所在节点确认 Input 声明，漏声明 = 静默 None，专项断言测试锁一条真写入带租户）。

- [ ] **Step 1: 失败测试**（三组）：①categories/attributes 端点签名断言；②审计写入点 SQL 含 tenant_id 列；③CI 断言测试本体（红：当前端点未迁）。
- [ ] **Step 2/3/4**：失败 → 实现（先端点迁移，再列+写点，再 init_data 迁移，最后断言测试转绿）→ 通过 + `test_tenant_guard_v075.py`/`test_tenant_guard_phase2.py` 零回归。
- **范围外（defer 登记）**：task_status 老数据宽容读改 404（等 v0.62.4 前无租户行随 30d 清理滚出，约 2026-09-30 后 v0.76 做）；明文 token 列退役迁移（grace 期 + dedup key 重建，v0.76）。

### Task C7: submit_task priority 开放（BL-25 Phase 2-5）

**Files:**
- Modify: `worker/src/main.py`（:1869 `priority = 0` → 读请求参数）
- Modify: `worker/src/api/schemas.py`（SubmitTask 请求模型补 `priority: int = Field(0, ge=0, le=100)`——找到实际请求模型名）
- Test: `worker/tests/test_submit_priority_v075.py`（新建）

**Interfaces:** 请求体可选 `priority`（clamp 0-100 默认 0）；认领 SQL 零改动（`ORDER BY priority DESC, created_at ASC` 已就绪 :460）；不传 = 0 行为逐字节不变。

- [ ] **Step 1: 失败测试**：POST submit_task 带 priority=90 → 任务行 priority=90；带 150 → clamp 100（或 422，按模型 Field 语义选，与既有校验风格一致）；不带 → 0。
- [ ] **Step 2/3/4**：失败 → 实现 → 通过。**注意**：main.py submit_task 是手读 body 先例（`openapi_extra`）还是 Pydantic 模型，执行者先看现状再选接入点；schema 变更后**不要自己跑 gen_api_docs**（主会话 Wave 3 后统一跑）。

### Task C10: pounding-mcp 30 工具参数差分核查（A6 P3）

**Files:**
- 可能 Modify: `pounding-mcp/pounding_mcp/server.py`（仅当发现真漂移）
- Modify: `docs/MCP-SERVER.md`（核查结论一行/修正）
- Test: 若改 server.py → `cd pounding-mcp && .venv/bin/python -m pytest tests/ -q` 全绿（30 工具 smoke 不破）

**步骤**：对 30 个工具逐个比对 skill CLI argparse 定义（skill/scripts/cli.py subparsers）与 server.py 参数映射表——输出三列清单（工具/CLI 参数/MCP 是否覆盖）落 docs/MCP-SERVER.md 附表；漂移即修（B1 先例：graph 补 category/type/min-density）。另核 `migrate_profile` 在 SKILL.md 的注记（升级后迁移登录态章节是否引用它；缺则补一句指引）。

- [ ] 核查表落库 + （若有）漂移修复 + pounding-mcp 自身 venv 测试绿。

---

## Wave 3（示例二期，主会话先跑一次 lint 落清单再派）

### Task C9: API 示例补齐至 strict 门禁（BL-11 二期）

**Files:**
- Modify: `worker/src/api/schemas.py`（18 个无示例 schema 补 `model_config = _examples({...})`——清单以 `[example-lint] 无示例 schema：BackupItem, ConfigListItem, HTTPValidationError, …` 为准）
- Modify: `worker/src/routes/*.py` + `worker/src/main.py` inline 路由（响应示例：`responses={200: {"content": {"application/json": {"example": {...}}}}}` 先例见 B4 已补的 45 个操作）
- Modify: `worker/scripts/gen_api_docs.py`（--check 联动 strict：`args.check and args.fail_on_missing_examples` 默认在 check 模式生效）
- Modify: `.github/workflows/ci.yml` + `scripts/ci.sh`（Step 5d 命令加 `--fail-on-missing-examples`）

**完成线（Definition of Done）**：`gen_api_docs.py --check --fail-on-missing-examples` 输出——无示例 schema=0、无 requestBody POST=0（当前 5：credentials/{id}/session、products/{id}/source、admin/queries/import、image-tasks×2）、200/201 无示例操作=0（当前 139，含别名重复，实际唯一操作约 90）。

**分派**：三路并行（a：schemas.py 18 schema + api/schemas 内 requestBody；(b)：routes/*.py 响应示例按 lint 清单分片；(c)：main.py inline 路由示例 + 门禁转正 + ci.yml/ci.sh）。示例值必须真实（对照既有实现字段语义/DB 列注释；HTTPValidationError 用 FastAPI 标准形态）。
**注意**：三路都不许跑 `gen_api_docs.py` 重生成（主会话最后统一跑一次 + 提交快照）；(c) 改完 ci.yml 后本地跑 `bash scripts/ci.sh --quick` 的对应步验证门禁红绿逻辑。

- [ ] lint 清单分片 → 并行补齐 → 主会话统一 `python worker/scripts/gen_api_docs.py` → 快照零漂移提交 → strict 门禁在 CI 收口。

---

## R 批：发版物料（主 worktree dev 直提，主会话亲自，PR 合并后）

- [ ] **R-1** VERSION 四源 0.74.0 → **0.75.0**（根 `VERSION` / `skill/VERSION` / `deploy/skill/VERSION` / `skill/SKILL.md` frontmatter）。
- [ ] **R-2** CHANGELOG 0.75.0 节，必须覆盖：①六 PR 摘要（B2-α 九项止血 / B1 文档治理 / B2-β 基建三表 / B3 规范固化 / B4 清障 244 行 / B5 密钥出库+历史重写）；②**行为变更清单**：佣金 >180d 降级 fallback:stale、cancel_task 不可取消 409、FX 汇率源切换（pg_cache→live→12.0，未配 margin 店上架价会变）、shelf 3 端点删除（147→144 path）、error_reports/forensics/categories/attributes 鉴权序统一 Depends(get_tenant)、category_cache TTL 10 年→90d、字典负缓存 60s+TTL 抖动、is_aspect 兜底收窄（构建期多填）、audit 表 tenant_id、priority 参数开放；③**运维注意**：git 历史已重写——所有其他机器 clone 必须重新 clone；升级后首次启动建新表（schema_migrations/backup_heartbeat/mxou_call_ledger/attr_bounds_learned）+ init_data 幂等迁移自动跑；ofelia 备份 sidecar 上线勿与宿主 crontab 双跑；④密钥轮换提醒（19 组待用户平台侧轮换）。
- [ ] **R-3** AGENTS.md 顶部 v0.75.0 块（战役摘要 + 密钥纪律/租户口径/探针先行三条新纪律指针）。
- [ ] **R-4** `python worker/scripts/gen_api_docs.py` 重跑（bump 后必跑）+ `docs/DB-SCHEMA-AUDIT.md` 补 attr_bounds_learned + 审计表 tenant_id 列 + BACKLOG.md 勾销/defer 登记（task_status 404 化、明文列退役、认领租户轮转、单飞锁跨副本、BL-26 SSH 半、BL-18 立项）。
- [ ] **R-5** 本计划状态行更新为「已完成（gate 待用户）」。

## V 批：验证矩阵（全绿线，全过才进发版动作）

- [ ] `lsof -iTCP:5433 -sTCP:LISTEN` 核实 → worker 全量：`cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/ -q`（基线 2587 + 本批新增，预期 ≥2620；跑前 `DELETE FROM ozon_product_tasks WHERE status IN ('pending','running')` 清残留）。
- [ ] skill 全量：`cd skill && .venv314/bin/python -m pytest tests/ -q`（基线 ~999）。
- [ ] pounding-mcp：`cd pounding-mcp && .venv/bin/python -m pytest tests/ -q`（30 工具 smoke）。
- [ ] webui：`cd webui && bun install && bunx tsc -b && bun run build`。
- [ ] ruff 两口径（worker src / skill scripts）零新增。
- [ ] `python worker/scripts/gen_api_docs.py --check --fail-on-missing-examples` 零漂移零缺失。
- [ ] 本地 Docker 冒烟：`cd deploy && docker compose up -d --build` → `/api/v1/health`（last_backup_at 字段在）→ `/docs` 可开 → 提交一条 mock 任务跑通到 category 阶段（或用既有 e2e mock 用例）→ teardown。

## 发版动作（用户实机 gate 通过后）

1. 用户跑实机 ≥3 单 gate（discover/follow 真实单，approved/completed 终态核验）。
2. dev→main PR（merge commit 保留流边界）→ **main 上打 tag v0.75.0**（tag 只打 main）。
3. 服务器 cos-update（cd.yml 按 tag 触发）：先起 worker（建表+init_data 幂等迁移）→ ofelia 备份 sidecar 随 compose 起（勿与宿主 crontab 双跑）→ TRUNCATE/预热按 CACHE-WARM-RUNBOOK。
4. 发版后 ops（登记不阻塞）：SSH 通道恢复 → `worker/scripts/probe_assets.py` 七探针；RESTORE-RUNBOOK 首次演练填 RTO。

## Self-Review 记录

- 覆盖核对：BACKLOG 挂起五项（BL-30✅C4+C5 / 单飞锁✅C1 / 租户 guard Phase2-3✅C3 / 示例二期✅C9 / BL-26 代码半✅C8）+ checkpoint 清理✅C2 + priority✅C7 + 佣金续期✅C6 + mcp 核查✅C10 + 发版线✅R/V。defer 项均已登记去处。
- 类型一致性：C4 接口三函数名在 Steps 与 Interfaces 一致；C3 测试文件名与既有 phase2 命名衔接。
- 已知风险：C9 示例量大（~90 唯一操作）→ 三路分片 + 主会话抽检 10 个；C2 清理键以探针实证为准（thread_id≠task_id 时停手回报）；C5 行为变更入 CHANGELOG。
