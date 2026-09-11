# A9 — 性能/稳定性/耦合/扩展/工程闭环/隔离 体系化风险登记册

- 基线：v0.74.0 @ `7a13d7ab`（2026-09-11）；审计范围 = worker 运行时 + 部署链 + 测试/CI + 多租户面。
- 方法：全量读码取证（main.py / task_processor / 两个 rate_limiter / ozon_client / mxou_api / dict_value_cache / memory_saver / instance_lock / tenant_service / credential_service / forensics / CI workflow / cos-update.sh / tests 分层统计），路径均为仓库相对路径。
- 排除（不重复立案）：竞态/重复类 26 条见 `docs/audit/2026-09-09-race-duplication/findings.md`；汇率断链/备份/alembic 见 A3（G-01~G-03）；variant 断链与死代码见 A5；文档漂移明细归 A1 波（本文只在门禁可行性处引用）。
- 等级口径：P0=正在造成不可逆损失；P1=架构性缺口、必然复发或防线失效；P2=高概率触发/影响可量化；P3=现状可接受但需登记。

## 0. 执行摘要

1. **共 22 条风险：P1×2、P2×12、P3×8**，无 P0。
2. 两条 P1：①Ozon 限流器 acquire 超时后**放行**（返回值被忽略）+ 全局桶无租户公平——「防封禁」是尽力而为；②租户过滤无集中执行层，靠端点手工带 `tenant_id`（v0.73 四端点漂移已实证此模式复发）。
3. 超时/重试矩阵整体健康（六依赖全有界、永久错误快败、zombie 有界），但**全线无熔断器**，依赖持续故障时退化为重试风暴。
4. 缓存三防（穿透/击穿/雪崩）全缺：无负缓存、无 singleflight、30d TTL 按预热时刻对齐。
5. 多租户资源隔离只有「提交限流 300/min + MXOU 计费」；队列 priority 恒 0、无 per-tenant 并发/生图配额，单租户批量可饿死他租户。
6. 单实例内存态 8 处（session store/3 个 limiter/2 个缓存/running_tasks/MemorySaver 兜底）——`workers=1` 约束只存在于注释与部署约定，无启动断言。
7. 任务队列层已多副本安全（SKIP LOCKED + advisory lock），是扩展性最好的一层；瓶颈在进程内存态而非队列。
8. 测试 272 文件/~2375 用例分层良好；5 个图节点近零直测；发布有健康检查+自动回滚但无灰度、健康探针不探业务链路。
9. 文档同步唯一自动门禁是 `gen_api_docs --check`；AGENTS/CONTRACT/TOPOLOGY 靠人工+审计纠偏——可仿 `test_compile_lists.py` 先例加断言脚本进 CI，成本低。
10. PG 连接池 15 上限 vs 30 并发任务 + API + 同步作业，是容量上最紧的一根弦。

---

## 1. 模块 9：性能与稳定性

### Q9.1 并发与限流——一句话结论
**并发=asyncio.Semaphore(MAX_CONCURRENT 默认 30)（main.py:482，与文档 50 漂移）+ PG 队列 `FOR UPDATE SKIP LOCKED` 认领；限流三层齐备但 Ozon 桶是全进程共享且 acquire 失败 fail-open——防线现状=尽力而为，非硬保证。**
- 执行位置：`worker/src/main.py:482`（读 env 建处理器）、`main.py:598`（`start_workers(num_workers=max_concurrent)`）、`worker/src/utils/task_processor.py:358`（Semaphore）、`task_processor.py:460-462`（`ORDER BY priority DESC, created_at ASC ... FOR UPDATE SKIP LOCKED`）。
- Ozon 桶：`utils/ozon_rate_limiter.py:22`（seller 1000 / finance 100 / premium 60 per min）——**per-section 全局单例，非 per-credential**：多租户共享同一桶，单租户批量可挤占全桶；Ozon 平台侧限额本按凭证计，全局桶偏保守安全但牺牲吞吐与公平。
- **fail-open 实证**：`utils/ozon_client.py:91` `_rate_limiter.acquire(endpoint)` **返回值被忽略**——`acquire` 等待超时返回 False（`ozon_rate_limiter.py:72-74`）后调用照发。饱和场景=限流器静默失效。
- 429 处理：`ozon_client.py:99-104` tenacity 仅对 429/5xx 重试 3 次，429 优先 `Retry-After`（`ozon_client.py:46-52`）；MXOU 侧 450 RPM/token 滑窗 + 429 指数退避（`utils/mxou_rate_limiter.py:23,93-106`）。
- 「调用过频被封」防线现状：自限流 + Retry-After 尊重 = 有；per-credential 隔离/公平调度/全局熔断 = 无。

### Q9.2 超时/重试/熔断/降级矩阵——一句话结论
**六依赖全部有界（无无限重试/无无限轮询），永久错误快败与优雅降级散点存在，但无一处熔断器、PG 无 statement_timeout、Supabase 未显式配超时。**

| 依赖 | 超时 | 重试 | 熔断 | 降级/兜底 |
|---|---|---|---|---|
| Ozon Seller API | 单调用 10–60s（默认 60） | tenacity 3 次，仅 429(Retry-After 优先)/5xx，指数抖动 ≤20s（ozon_client.py:41-52,99） | ❌ | 查询类 fail-open（配额 :219-222、offer 存在性 :262-267 失败按可继续）；写类 typed error 入 retry 子图 |
| Ozon 限流器 | acquire 阻塞 ≤30s | — | ❌ | **超时放行（返回值被忽略，ozon_client.py:91）** |
| MXOU LLM chat | 90s（mxou_api.py:130） | 1+2 次退避 2^n；429 专道 ≤2 次（mxou_api.py:193,258-259） | ❌（永久错误 401/quota 快败 :243） | 无备用模型自动路由（config 热加载可手切） |
| MXOU 生图(grsai) | 180s 含轮询（mxou_api.py:406,642） | 有界 max_retries（:538） | 内容违规 `MxouContentViolationError` 不重试不降级（:44,688） | b64→COS 转存（:603-617）；生图全败→COS 原图兜底（cos_uploader） |
| PG 业务库 | pool_timeout=30s；建连 15s×2（db.py:38-44, memory_saver.py:14） | ❌（语句不重试） | ❌ | pre_ping + recycle 1800s；**无 statement_timeout**；池 5+10 |
| Supabase | 未显式配置（supabase_client.py 无 timeout 项） | ❌ | ❌ | resolve_tenant 查询失败 fail-closed 503（tenant_service.py:68-70）；未配置 fail-open（本地） |
| COS | connect 10 / read 60 / retries 2；下载原图 15s（cos_uploader.py:75-77,150） | SDK 2 次 | ❌ | 未配置优雅降级返回 None 不阻断 |
| Checkpointer | 建连 15s×2（memory_saver.py:14-15） | ❌ | ❌ | 失败退化 **MemorySaver**（:66-71，重启丢 state） |

### Q9.3 大任务处理——一句话结论
**三条大任务链均有分批/有界设计，但 warm 断点续传是人工 `--offset` 而非自动 checkpoint；内存风险集中在「任务 payload 无大小上限 + memory schema checkpoint 无清理」两处。**
- warm：分片 ≤2 写死 + `--offset` 人工断点 + `warm_dead_nodes` 永久跳过（`worker/scripts/warm_category_cache.py:13-14,491-518`）——中断后靠人记 offset，无自动续跑。
- 订单批量拉取：v4 cursor/has_next 分页（`services/order_service.py:293`、`store_sync_service.py:58-72`，AGENTS 登记）。
- 重试有界：retry_count<max_retries 才回炉（main.py:551-560,1105-1110）；生图有 `task_image_cache` 防重跑重烧（`utils/task_image_cache.py` 模块注释）。
- 内存风险：①submit 无 payload 大小上限（main.py:1759 附近仅读 max_retries，无 len 校验）——大 JSONB 全量入库、认领时全量 `json.loads` 进内存（task_processor.py:477）；②checkpoint 表（PG `memory` schema）只在任务表清理（`DELETE FROM ozon_product_tasks ... 30 days`，main.py 清理段），**checkpoint 行零清理**。
- 优先级消费：`priority DESC, created_at ASC` 只在认领 SQL（task_processor.py:460）；**写入侧恒 0**（main.py:1757「直到建立 VIP 体系」）——字段有通道无策略，低优不阻塞高优只因大家都是 0（严格 FIFO）。

### Q9.4 缓存三防——一句话结论
**穿透/击穿/雪崩三防全缺，当前靠「全局令牌桶 + 30d 长 TTL」稀释风险；冷启动（清表重预热前）是全 miss 回源窗口。**
- 穿透：`get_dictionary_values` miss 返回 None → 调用方回源 Ozon（`utils/ozon_category_query.py:1493-1535`），**无负缓存**——不存在的 (attr,dc,tp) 每任务重复回源。
- 击穿：miss 无 singleflight/进程锁（同 key 并发 miss 全部回源；对比：类目树同步有 advisory xact lock，`ozon_category_query.py:1577-1688`，字典读侧无同款）。
- 雪崩：`expires_in=DICT_CACHE_TTL_SECONDS=30d` 按写入时刻定 expires_at（`dict_value_cache.py:41,79-105`）——**同一次 warm 预热的行 30 天后同日集中过期**，无 jitter。
- 冷启动：TRUNCATE 后首波任务全 miss → 回源 `/values`，被全局 seller 桶 1000/min 压顶，任务变慢但不雪崩（桶反而成了唯一的意外防波堤）。

### 模块 9 风险登记

| 编号 | 风险 | 等级 | 证据 | 建议缓解 | 探针/监控 |
|---|---|---|---|---|---|
| S9-01 | Ozon 限流 acquire 超时 fail-open + 全局桶无 per-credential 公平——饱和时限流静默失效、单租户挤占全桶，防封禁无硬保证 | **P1** | ozon_client.py:91；ozon_rate_limiter.py:22,72-74 | acquire 失败时显式 warning+Sentry 计数（或直接抛可重试异常）；按 credential 维度加公平轮转队列 | limiter 等待时长分布、acquire 超时次数、per-credential 调用计数（复用 log_ozon_api_call 聚合） |
| S9-02 | 全线无熔断器：外部依赖持续故障时 30 任务×重试继续烧额度/时间（永久错误快败是唯一缓解） | P2 | 矩阵见 Q9.2；全仓无 circuit 关键字 | 引入轻量断路器（连续 N 次 5xx/超时 → 冷却期快败），或最低限度给 MXOU/Ozon 加「滑动错误率」告警 | /health 扩展依赖健康分；Sentry 按依赖聚合 5xx 率 |
| S9-03 | PG 池 15 连接（5+10）对 30 并发任务+REST+同步作业，无 statement_timeout——池耗尽→30s 排队放大延迟 | P2 | db.py:37-44；task_processor.py:444-446 注释自认 | 池容量与 MAX_CONCURRENT 联动（≥并发×1）；关键会话 `SET statement_timeout` | pool checkout 超时计数（SQLAlchemy pool events）；pg_stat_activity 连接数探针 |
| S9-04 | 字典缓存穿透（无负缓存）/击穿（无 singleflight）/雪崩（30d TTL 对齐预热时刻）三防全缺 | P2 | ozon_category_query.py:1493-1535；dict_value_cache.py:41 | expires_at 加 ±10% jitter；miss 计数进 attr_match_log；热 key 回源加进程内单飞 | cache hit/miss 率按 (attr,dc,tp) 聚合；「同分钟同 key 回源次数」探针 |
| S9-05 | priority 恒 0 + 无 per-tenant 队列配额——FIFO 下单租户批量（≤300 提交/min）可把他租户任务压队尾 | P2 | main.py:1757；task_processor.py:460 | 短期：按 tenant 轮转认领（claim SQL 按 tenant 公平取）；长期：启用 priority 列定义租户套餐 | 队列等待时长按 tenant 分位（认领时 created_at-now 可算） |
| S9-06 | 无界增长三处：memory schema checkpoint 零清理；submit 无 payload 上限；进程内 limiter/租户缓存 dict 键无淘汰 | P2 | main.py 清理段（只删任务表）；task_processor.py:477；main.py:233-252；tenant_service.py:26 | 清理循环追加 `DELETE FROM memory.checkpoints`（按 thread_id=已完成任务）；submit 校验 payload ≤N MB；缓存键 LRU 化 | memory schema 行数/体积日探针；`_requests`/`_tenant_cache` len 打点 |
| S9-07 | warm 断点续传靠人工 --offset，16h 全量中断即人工重算 | P3 | warm_category_cache.py:13-14,440 | 跑前把 offset 写入 state 文件，重跑自动续 | coverage 端点（已有 --coverage）+ 完成率日志 |
| S9-08 | MAX_CONCURRENT 默认 30 与文档 50 漂移；1000/min 桶 × 30 并发的容量上限从未测算 | P3 | main.py:482 vs AGENTS「MAX_CONCURRENT=50」 | 压测标定吞吐拐点，写入 DEPLOY 容量表 | 已含 S9-01/S9-03 探针 |

## 2. 模块 10：耦合与扩展

### Q10.1 Worker↔Skill↔MCP 契约耦合——一句话结论
**契约三处同步（CONTRACT-v4/skill/worker state.py）目前靠人工纪律；「新增类目」大部分走数据可扩展，「新增平台」必须重写核心，「新增 MCP 工具/服务」必改代码。**
- 正例 1（数据驱动）：敏感词表/同义词/生图计划/prompt 全部 config JSON 热加载（`worker/config/*.json`、`utils/restricted_keywords.py`），新增类目敏感词零发版。
- 正例 2（链式解析）：佣金解析 `explicit > 缓存表 > extensions segments > 0.10`（`utils/commission_resolver.py`）——新增佣金数据源不改定价核心。
- 正例 3（零业务逻辑 MCP）：`mcp_server.py` 只回调本进程 REST（`_call`，:113），REST 加字段 MCP 自动生效。
- 反例 1（新平台）：上传链硬编码 Ozon——`ozon_client.py:39` BASE_URL、`graphs/nodes/ozon_*` 8 个节点、draft schema 的 `description_category_id/type_id`、`WorkerErrorCode` 14 枚举；接 WB=分叉整条管线。
- 反例 2（新增 MCP 工具）：`_call` 内 19 处硬编码 REST 路径字符串（mcp_server.py:204-402）+ pounding-mcp `server.py` 参数映射 + `router.py` 意图词表——三处人工同步，AGENTS 有规则、无门禁。
- 反例 3（数据在代码）：`_SENSITIVE_SOURCE_SIGNALS`（ozon_category_query.py:99）、`_INFER_KW`、retry `_KNOWN_DEFAULTS_RETRY`（validation_retry_loop.py:1722）按词/attr_id 硬编码进 py——新增敏感类目/属性默认值要发版。

### Q10.2 跨模块直连与循环依赖——一句话结论
**调用图是干净的单向星型（webui/skill/harness → worker REST；MCP 进程内回调），无模块级循环依赖；真正的耦合熵中心是 3185 行的 main.py 巨石。**
- webui：同进程静态伺服 + SPA fallback（main.py:3089-3102），零 RPC 循环；skill/harness：纯 HTTP 消费，零 import。
- `mcp_server.py` 对 `main` 的依赖是**函数内延迟 import**（:77,103）——无模块级循环，但形成「同进程自调用」的隐式依赖（改路由必须同步 `_call`，AGENTS 红线）。
- main.py 同时承载：FastAPI 路由 + CLI 入口 + 限流器 + 僵尸清理 + 定时循环编排 + newapi 代理（:3081+）——改动热点冲突面。

### Q10.3 水平扩展——一句话结论
**任务队列层已多副本安全（SKIP LOCKED + store_sync_jobs + instance_lock advisory 锁 + 类目树 xact 锁），但 8 处进程内存态使 `workers=1` 成为隐式部署契约，且无启动断言防误配。**
- 单实例清单：`MxouSessionStore`（mxou_login_service.py:27 docstring 自认「单进程 workers=1 安全」）、main.RateLimiter（main.py:233）、mxou TokenRateLimiter（mxou_rate_limiter.py:77）、ozon RateLimiter（ozon_rate_limiter.py:77）、`_BALANCE_CACHE` 单条（mxou_api.py:59）、`_tenant_cache`（tenant_service.py:26）、GraphService.running_tasks（main.py:247）、MemorySaver 兜底（memory_saver.py:66）。
- 部署现状：单进程（`worker/Dockerfile:72` `CMD python -m src.main -m http -p 5000`，非 gunicorn 多 worker）——与内存态自洽。
- 已多副本安全：任务认领（task_processor.py:462）、同步 jobs（main.py:583 注释）、后台循环锁（instance_lock.py 全文，fail-open 口径）、全树同步锁（ozon_category_query.py:1577+）。
- 单点：PG（一切状态）、COS（图床，生命周期删图=历史卡全无图，已知事故）、MXOU（LLM/生图唯一渠道）、Supabase（auth/租户源，未配置 fail-open）。

### 模块 10 风险登记

| 编号 | 风险 | 等级 | 证据 | 建议缓解 | 探针/监控 |
|---|---|---|---|---|---|
| S10-01 | 新平台（WB 类）不可插拔：客户端/节点/schema/错误码全 Ozon 硬编码，扩平台=重写管线 | P2 | ozon_client.py:39；graphs/nodes/ozon_*；api/errors.py | 中期抽 `PlatformClient` 协议 + upload 节点按 platform 分发；短期只登记不动 | —（架构演进项） |
| S10-02 | 路由路径字符串三处人工同步（mcp `_call` 19 处 + pounding-mcp 映射 + webui client.ts），无门禁 | P2 | mcp_server.py:204-402；AGENTS 更新联动表 | 写一个断言测试：枚举 REST 路由表 → 断言 `_call`/pounding-mcp/server.py 中引用路径均存在（双向） | CI 现有 drift 思路扩展 |
| S10-03 | 数据在代码：敏感信号词/视觉推断词/retry 默认值按 attr_id 硬编码，新增类目属性需发版 | P3 | ozon_category_query.py:99；validation_retry_loop.py:1722 | 迁 config JSON 热加载（同 restricted_keywords 先例） | 变更时 grep 审计 |
| S10-04 | 8 处单实例内存态 + `workers=1` 无启动断言——误开多副本时限流/会话/缓存语义静默失效（队列不坏更隐蔽） | P2 | mxou_login_service.py:27；Dockerfile:72；instance_lock.py | 启动时探测 `WORKERS>1`/同库双实例即 fail-fast 或告警；内存态逐步下沉 PG/Redis | 实例指纹心跳表（同 lock key 第二持有者检测） |
| S10-05 | main.py 3185 行巨石（路由+CLI+限流+清理+代理）是耦合熵中心，改动热点冲突 | P3 | main.py 全文 | 按 services/ 既有惯性继续抽离（router 分文件），不紧急 | — |
| S10-06 | 单点无冗余：PG/COS/MXOU/Supabase 任一不可用即对应能力全停；MXOU 无自动备用路由 | P3 | Q10.3 单点清单；mxou_api.py 单 base_url | 备份链已 A3 立案（G-02）；MXOU 备用模型走 config 兜底自动切换 | 依赖健康分进 /health（同 S9-02） |

## 3. 模块 11：工程闭环

### Q11.1 测试体系——一句话结论
**三层齐备且量大（272 文件/~2375 用例；194 文件 mock / 106 触 PG / conftest 有 PG skip 守卫），但 5 个图节点近零直测、E2E 闸有已知隔离问题、PG 集成有「连错库假绿」的历史坑。**
- 分层统计（grep 实测）：worker/tests 272 文件、`def test_` 2375 个；含 mock 194 文件；触 PG（get_engine/psycopg/PGDATABASE_URL）106 文件；conftest.py:36-96 有 PG 探测 skip 守卫。
- 低覆盖节点（tests/ 引用计数，含弱信号）：check_quota_node=1、fetch_back_node=1、ingest_node=1、scene_generation_llm_node=2、visual_vars_llm_node=2（对照 assemble=39、ozon_upload=33、prepare=29）。
- MCP 独立测试：有——worker `tests/test_mcp_server.py`（工具整形/Bearer/挂载面）+ pounding-mcp 自身 venv 26 用例（含 29 工具注册 smoke）。
- E2E：`deploy/docker-compose.e2e.yml` 存在；已知 `test_webui_e2e` 无 boto3 环境被镜像闸 422（AGENTS 高频坑）；PG 集成曾「5433 被临时 PG 占位→连错库假绿」（AGENTS 红线）。

### Q11.2 CI/CD 与发布——一句话结论
**CI 侧完整（ci.yml 9 jobs：hygiene/syntax/deploy-scripts/gitleaks/ruff×2/test-worker 含 API 漂移门禁/test-skill/构建）；CD 侧无灰度/蓝绿，回滚链=cos-update.sh 健康检查失败自动回滚（备份轮转 3 份）是唯一防线，且健康探针只打 /health 不探业务链路；发布 checklist 无机器可执行版本。**
- cd.yml：3 jobs（Version Four-Source Check :14 / Docker Build & Push :37 / Create Release :77），tag `v*` 触发。
- 回滚：`deploy/cos-update.sh:7-16,117-145`——失败回滚到备份 + 回滚后健康检查 + 按旧版本号重打镜像 tag（v0.73 修正）。
- 无灰度：无 canary/双版本并行/流量切分概念；升级=整容器重建。
- 发布 gate（实机 ≥3 单）写在 AGENTS 纪律里，靠人执行，无 checklist 文件（DEPLOY.md 无「回滚/checklist」小节，grep 零命中）。

### Q11.3 版本与需求追溯——一句话结论
**版本四源有 CI 硬断言（好）；需求→影响评估→审批的追溯链=14 篇 PLAN-* 文档纪律，无 issue/PR 强制关联，实际执行靠多会话纪律+审计纠偏。**
- cd.yml:14-31 断言 VERSION 四源一致；CHANGELOG + AGENTS 顶部块纪律在 AGENTS「纪律」节。
- `docs/PLAN-*.py` 计 14 篇（PLAN-repo-gov-v1、PLAN-shopbang-parity-v1 等）——「需求来源」有底稿，但无机制强制「每个行为变更必有 PLAN」。

### Q11.4 文档同步机制——一句话结论
**唯一自动门禁是 `gen_api_docs.py --check`（ci.yml:159 + ci.sh Step 5d）；AGENTS/WORKER-TOPOLOGY/CONTRACT 同步靠人工，A1 波已取证多处漂移——加 CI 断言门禁可行且成本低（仓库已有两个先例）。**
- 先例：`worker/tests/test_compile_lists.py`（锁 14 模块不变式）与 `webui/scripts/verify-design-tokens.mjs`（token 防漂移）证明「文档即断言」模式在仓内已跑通。
- 可行方案：新脚本 `scripts/doc_drift_check.py` 断言——①AGENTS 错误码数=errors.py 枚举数；②STAGE_ORDER 数=AGENTS 数字；③MCP 工具数=mcp_server 注册数；④AGENTS API 表行数 ⊆ openapi paths。进 ci.yml repo-hygiene 即可。

### 模块 11 风险登记

| 编号 | 风险 | 等级 | 证据 | 建议缓解 | 探针/监控 |
|---|---|---|---|---|---|
| S11-01 | 5 个图节点近零直测：check_quota/fetch_back/ingest/scene_generation_llm/visual_vars_llm | P2 | tests/ 引用计数 1/1/1/2/2（详见 Q11.1） | 按 mock 先例补纯单测（ingest 是信封入口优先） | coverage 报告按节点维度入 CI 产物 |
| S11-02 | 发布无灰度/蓝绿；健康检查只探 /health，业务链路（PG 队列认领/一次 dry Ozon 调用）不进升级 gate；checklist 无机器版本 | P2 | cos-update.sh:7-16,145；DEPLOY.md grep 零命中 | 升级后自动跑 1 单本地 dry-run（或至少 /task_statistics+队列认领探活）；docs/RELEASE-CHECKLIST.md 落 AGENTS gate 条目 | 升级完成后自动 POST 一条合成探针任务 |
| S11-03 | 需求→PLAN→实现追溯靠纪律，无机制强制；「多会话撞车」依赖人工看 git status | P3 | docs/PLAN-* 14 篇；AGENTS 纪律节 | PR 模板加「PLAN 链接」字段（低成本） | PR lint 检查描述含 PLAN 路径 |
| S11-04 | 文档同步唯一自动门禁是 API-REFERENCE；AGENTS/CONTRACT/TOPOLOGY 漂移多次实证，靠人工+审计纠偏 | P2 | ci.yml:159；A1 波取证 | 落 `doc_drift_check.py` 断言（错误码数/阶段数/MCP 工具数/API 表行数），仿 test_compile_lists.py 先例进 CI | 同左（门禁即探针） |
| S11-05 | 测试假绿面：PG 集成连错库历史假绿 + test_webui_e2e 已知隔离问题——「全绿」不等于「对库」 | P3 | AGENTS 高频坑节；conftest.py:36-96 | conftest 探针断言库名/表特征（category_tree_nodes 行数>0）而非仅端口连通 | CI 内 init_data 后断言行数 |

## 4. 模块 12：多租户隔离

### Q12.1 数据隔离——一句话结论
**执行层是「路由级 resolve_tenant（60s LRU）+ 各 service 手工带 tenant_id」，无集中 middleware/guard——v0.73 四端点租户漂移已实证该模式必然复发；已知防线（get_decrypted 404 / 绑定 409 / forensics 404）有效，绕过面在无租户列的审计表。**
- 解析：`services/tenant_service.py:39-72`（Supabase→user_id，fail-closed 503；未配置回退哈希派生）。
- 防线实证：`credential_service.py:324-342`（get_decrypted `WHERE tenant_id=:tenant` 跨租户 404）；`_assert_client_not_bound_elsewhere` 409；`main.py:2670-2700` forensics 任务行租户比对 404。
- 绕过面 1：`category_match_log`/`attr_match_log` **无 tenant_id 列**（model.py:369-431）——读保护完全依赖经 ozon_product_tasks 的 join；未来任何直读审计表的端点（报表/导出）即跨租户泄漏。
- 绕过面 2：task_status 老数据无租户列→「宽容读」放行（main.py:1879-1896 注释自认）。
- 设计内共享（非泄漏）：category_mapping（W11 全局知识）、蓝海/榜单读面（v0.57 拍板全局共享）。

### Q12.2 资源隔离——一句话结论
**只有提交端 per-token 300/min 滑窗 + MXOU 按 key 计费隔离；并发槽/队列/生图线程池均全局共享无 per-tenant 配额——大租户可挤占（与 S9-05 同根，租户视角单列）。**
- 限流覆盖：提交（main.py:1726）+ 多数读端点 429（:2367 等 7 处）；**task_status 有意不限**（:1882-1884 注释：高频轮询不误伤）。
- 并发：Semaphore 全局一个（task_processor.py:358）；生图并发=30 任务×8 节点抢 128 线程池（main.py:487-496），无租户维度。
- 额度：MXOU RPM limiter per-token（450 RPM，mxou_rate_limiter.py:23）+ 余额/配额按 user key——计费隔离良好；但限流器内存 dict per-token 无淘汰（见 S9-06）。

### Q12.3 能力隔离——一句话结论
**能力差异现仅二元：is_admin（role≥10，New API 角色）挡 admin 读面；无套餐/分层/功能开关，用户间差异实质=余额与 unlimited_quota——产品化分层时会撞「无 per-tenant 配额基建」。**
- `services/admin_service.py:21-38`（role≥10 统一判定，兼容 int/str）；`resolve_analytics_scope` 角色查询失败按非 admin（fail-safe，tenant_service.py:74+）。
- 无 plan/tier 字段消费（mcp__ozon 的订阅分层是 Ozon 侧概念，与本系统租户分层无关）。

### 模块 12 风险登记

| 编号 | 风险 | 等级 | 证据 | 建议缓解 | 探针/监控 |
|---|---|---|---|---|---|
| S12-01 | 租户过滤无集中执行层：每查询手工带 tenant_id，漂移必然复发（v0.73 四端点实证）；审计表无租户列，读保护全系于 join | **P1** | tenant_service.py:39；model.py:369-431；v0.73 修复记录（AGENTS） | ①给两张审计表补 tenant_id 列（写入侧同事务，历史行回填 task join）；②写「租户断言」测试基类：遍历读端点断言 SQL 含 tenant 条件或经 task join | 审计 SQL 日志抽查「SELECT ... FROM category_match_log 无 join」即告警；跨租户 404 率归零断言 |
| S12-02 | task_status 老数据无租户列宽容放行——持任意有效 token 可读无主任务终态（存在性泄漏） | P3 | main.py:1879-1896 | 一次性回填历史行 tenant_id（task join payload），随后把宽容读改 404 | 回填计数 + 宽容读命中次数打点 |
| S12-03 | 并发/队列/生图线程池零 per-tenant 配额，大租户批量挤占他租户吞吐 | P2 | task_processor.py:358；main.py:487-496 | 与 S9-05 合并治理：tenant 轮转认领 + 生图槽按 tenant 上限 | 线程池队列深度 + 各 tenant 在跑任务数打点 |
| S12-04 | 能力隔离仅 admin 二元，无套餐/配额基建——商业化分层时缺地基 | P3 | admin_service.py:21-38 | 登记为演进项：users 加 plan 字段 + 配额表（复用 priority 列作槽位权重） | — |

## 5. 汇总

**P1（建议随下个修复批）**
- S9-01 限流 fail-open + 无租户公平（一行修：尊重 acquire 返回值 + 告警）。
- S12-01 租户过滤执行层缺口 + 审计表补列（写入侧小改，读保护从「约定」变「结构」）。

**P2 中位（排入 B 批）**：S9-02 熔断缺失、S9-03 PG 池容量、S9-04 缓存三防、S9-05+S12-03 队列公平性（同一治理）、S9-06 无界增长、S10-02 路由路径断言测试、S10-04 workers=1 启动断言、S11-01 节点测试补缺、S11-02 发布探活+checklist、S11-04 文档断言门禁。

**探针落地优先级**（只读、低成本、可进 /health）：①限流器 acquire 超时计数（S9-01）；②memory.checkpoints 行数与 PG 连接池等待（S9-06/S9-03）；③字典缓存 miss 率（S9-04）；④各 tenant 队列等待分位（S9-05/S12-03）；⑤依赖 5xx 滑动错误率（S9-02/S10-06）。

**总体评价**：该代码库在「有界性」（超时/重试/重试封顶/僵尸有界）上纪律出色，矩阵无一处无限循环；但体系化的四块地基——熔断、缓存三防、租户执行层、多副本内存态——仍是空白或约定态。当前用户规模下均未爆雷，随租户数与批量用量增长的边际风险陡增；上述 P1 两项均为「小改动消大隐患」型，建议优先落地。
