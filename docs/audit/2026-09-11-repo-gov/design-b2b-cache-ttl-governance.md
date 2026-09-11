# 设计 — B2-β / BL-24：category_cache 10 年 TTL 与 category_commission 无 TTL 治理

> 状态：待评审 ｜ 依据：A7 §2.2 / §3 缓存映射总表 / P2 两项 ｜ 基线 v0.74.0 @ 7a13d7ab
> 范围：两张全局缓存的时效语义；不动字典/schema 30d 三桶体系（v0.72 已定案）。

## 1. 背景与证据

- **category_cache 名义 10 年 TTL**：`local_db_manager.set_category_cache` 默认参 `expires_in=315360000`（:330），本地实查过期日 2036-09-06。真实失效 = 手动 `refresh_category_tree.py`（月级类目变化全靠人记得跑），TTL 字段是谎言——仪表/排查时 `expires_at` 给出虚假安全感。
- **category_commission 无任何时效**：`model.py:332-344` 只有 `updated_at` 供观察；`commission_resolver.resolve_commission_rate`（:97-134）对缓存行**无条件采信**（仅拒 ≤0 段值，v0.67 防污染）。Ozon 佣金是「类目×发货模式×价格段」三维矩阵且平台侧会调整——一年前的行照常参与定价。
- **失效代价不对称**：类目树过期 = 搜索落空/类目缺失（显性，易发现）；佣金过期 = 价格系统性偏差（隐性，直接烧利润，无人报警）。
- 既有先例可复用：佣金 source 链（`explicit > cache:{band} > segments:{band} > fallback`）已经是「逐级降信」结构，加一层时效降信是同构扩展。

## 2. 方案选项

### 选项 1：显式刷新戳 + runbook（诚实化，不改行为）

TTL 注释标注「真实失效 = 手动 refresh」；refresh 纳入 cron/周检；佣金表加 `last_verified_at` 语义约定 + runbook 月度核对 SQL。
- ✅ 零行为风险、零迁移。❌ 两个问题都只从「说谎」变「坦白」，佣金隐性偏差照旧，依赖人的纪律（v0.73 前后多次证明纪律会漏）。

### 选项 2：TTL 真降（category_cache → 90d）+ 读侧过期回源

TTL 改 90d；过期后读侧像 schema 缓存一样触发懒加载重拉（或直接走既有 assemble 拉树兜底通道）。
- ✅ 时效语义归真。❌ 树全量拉取是分钟级 + 需店铺凭证 + 写两倍行（双语），懒加载点在请求热路径上不可接受；且类目树月级变化配 90d 已足够，懒回源收益极低、复杂度（凭证传递/并发防击穿）高。

### 选项 3：佣金新鲜度权重（resolve 时降信）

`resolve_commission_rate` 缓存分支加时效闸：行 `updated_at` 距今 >180 天 → 视同未命中，继续走 segments/fallback（0.10），并在定价 marks 记 `commission_stale_fallback=true` + 日志打点。
- ✅ 改动集中在 resolver 一个函数（唯一入口纪律，三处调用方自动生效）；0.10 fallback 是保守方向（宁可利润算保守不可虚高）；有 marks 可观测。
- ❌ 需要给 `get_category_commission` 返回的 dict 带上 updated_at（现为 6 段值 + source）；180d 阈值是拍的（Ozon 佣金无公开变更频率数据，取保守半年）。

**取舍结论**：类目树选 1（诚实化，月级变化 + 手动 refresh 是合理运维面）；佣金选 3（隐性资损必须自动化防）。组合拳，互不阻塞。

## 3. 推荐方案与分期

**Phase 1（佣金时效闸，核心收益，~半天）**
1. `commission_resolver.get_category_commission` 返回 dict 增加 `updated_at`（epoch）。
2. `resolve_commission_rate` 增 keyword-only `stale_after_days=180`：缓存行超龄 → 不采信，source 落 `fallback:stale`（与既有 source 枚举风格一致，排查链 grep 兼容）。
3. 定价侧（pricing_node 调用点）在 marks/diagnostics 记 `commission_stale_fallback`；Sentry 按类目聚合超龄命中率（持续 >50% 说明该类目该回填了）。
4. 单测：新鲜行采信 / 超龄行走 fallback / 无 updated_at 行为兼容（历史行 updated_at 为空 → 视为超龄，保守方向）。

**Phase 2（category_cache 诚实化，~半天）**
5. `set_category_cache` 默认参 315360000 → 7776000（90d）并注释「真实失效 = refresh，TTL 仅为名义上限」；本地/生产存量行一次性 UPDATE 续期或留着过期也无碍（读侧本不看它拦人）。
6. refresh_category_tree 周检 cron 化（deploy compose 已有定时面可挂）+ `--coverage` 风格的 `--staleness` 输出（树行最新 created_at 距今天数）。

**Phase 3（可选演进）**
7. 佣金行回填自动化：what_to_sell 分段 upsert 时顺带刷 updated_at（已有通道），让超龄行随使用自然续期——超龄告警自然归零，无需人工清洗。

## 4. 验收探针

- 单测探针：`resolve_commission_rate` 对 200d 行返回 `(0.10, "fallback:stale")`、对 1d 行返回 `(pct/100, "cache:{band}")`——锁定时效闸语义。
- 运行时：日志 grep `fallback:stale` 计数按周环比；`SELECT count(*) FROM category_commission WHERE updated_at < now()-'180d'`（应单调下降）。
- 类目树：`SELECT language, to_timestamp(expires_at)::date FROM category_cache;` 显示 90d 内 + runbook 周检输出树龄 ≤7d。
- 定价对账：任取一单三档价，marks 无 `commission_stale_fallback` 时佣金来源必为显式/新鲜缓存/信封 segments 三者之一。
