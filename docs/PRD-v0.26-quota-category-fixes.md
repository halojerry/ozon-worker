# PRD：v0.26 — 生图额度暴烧 + 队列无限重跑 + 帽类属性修复

> 日期：2026-08-05 ｜ 状态：**🟡 实施中（代码已改未提交，PRD 待审阅）** ｜ 目标版本：v0.26.0
> 证据来源：Sentry 线上数据（org halo-fx / 项目 pouding_ozon）+ Ozon 全店审计（真实凭证只读查询）+ 代码逐行验证，全部带 `文件:行号`

---

## 一、背景与目标

用户报告三个问题：
1. **生图额度消耗异常**（"图片一直在生成"）——怀疑节点无限触发或 Worker bug
2. **改了生图提示词但线上还在用旧的**
3. **Sentry 上 v0.25 的问题**需要分析修复

另提出改进方向：Sentry 全局监控（不止看 Ozon 返回错误）、skill 抓取的类目/属性 worker 没用上、能否用 API 试填校验不真实上架。

## 二、调查发现（三重证据）

### 2.1 Sentry 线上数据（14 天，未解决 issue 按频率）

| 问题 | 次数 | 性质 |
|---|---|---|
| 必填属性 8229(类型) 缺失 | 229 | 属性填充 |
| 模型 nano-banana-fast 生图失败（无降级） | 139 | 生图失败 |
| 任务 failed（多个 task_id） | 120 | 任务失败 |
| grsai 任务 violation（提示词违规） | 126 | 生图失败 |
| 任务超时（1800秒） | 100 | 任务失败 |
| 必填属性 9163/31/10096/4295/8292 缺失 | 各 90-94 | 属性填充 |
| grsai 轮询超时 | 50 | 生图失败 |
| **GraphRecursionError（recursion limit 25）** | 3 | 🔴 图内死循环 |
| 所有 AI 生成图均失败 | 27 | 生图失败 |

⚠️ **关键事实**：全部 1899 个事件 `environment=local`、`release=dev` → **线上跑的不是 v0.25.0 镜像**（deploy.sh 会注入 VERSION，release=dev 说明构建时没传/旧镜像）——直接解释"提示词还在用旧的"。

### 2.2 Ozon 全店审计（3 店铺真实凭证只读查询）

| 店铺 | 商品 | approved | declined | fail | 带错误商品 |
|---|---|---|---|---|---|
| 主店铺 4718259 | 205 | 179 (87%) | 19 | 6 | 41 |
| 测试 5381204 | 28 | 26 | 0 | 1 | 3 |
| 测试 5371047 | 56 | 48 | 6 | 0 | 8 |

错误项 TOP：**DESCRIPTION_DECLINE ×32**（类目错配）、**VALUE_MUST_BE_INTEGER ×10**、**VALUE_MUST_BE_DECIMAL ×8**、BR_hazard_class1 ×8、ATTRIBUTE_VALUE_COUNT_EXCEEDED ×8、**9163 性别空值 ×7**（error/warning_attribute_values_empty）。

**帽类实证**：`Панама`（dc=17028959/type=96512）declined（8229/22507 类别错配）；`Панама Шапка`（dc=41777465/type=93040）pending（attr=9163 error_attribute_values_empty）；帽类目 **9163 字典值只有 4 个**（Мужской/Женский/Девочки/Мальчики），**无中性词** → 中性兜底永远匹配不到。

### 2.3 代码验证（根因）

| # | 根因 | 代码位置 |
|---|------|---------|
| P0-1 | **图内 pending 死循环**：条件边收到 `OzonStatusInput` 强转 state，该 schema **缺 `moderation_retry_count`** → 恒 0 → `ozon_status` 自环永不退出 → 击穿 recursion_limit 25（GraphRecursionError 实证） | `graph.py:337-344`、`state.py:563-581` |
| P0-2 | **队列无限重跑**：`_periodic_task_cleanup` 每 60s 把 running 且 30 分钟无更新的任务**无条件重置 pending，不检查/不递增 retry_count** → 永不封顶；每次重跑无 checkpointer 全量重烧 9+N 张生图 | `main.py:838-841`、`task_processor.py:266-284` |
| P0-3 | **单张图最多 5 次计费**：POST 失败重试重新 POST；轮询 180s 超时（任务仍计费）→ 降级 `nano-banana-fast` 再 POST | `mxou_api.py:304-366` |
| P1-1 | **帽类 9163 性别填不上**：字典值无中性词，中性兜底永不命中 | `prepare_ozon_upload_node.py:867-896` |
| P1-2 | **数字属性类型错误**：文本塞进 INTEGER/DECIMAL 属性（8205/11650/4497/7444） | `prepare_ozon_upload_node.py:1404-1435`（仅重量清洗，无通用类型转换） |
| P1-3 | **字典属性覆盖不完整**：只对必填做语义解析+搜索+列表兜底；可选字典属性只有同义词填满 | `prepare_ozon_upload_node.py:907-992` |

## 三、修复方案与实施状态

### P0 生图额度/无限重跑（Sentry 证据最硬，优先）

| # | 方案 | 状态 | 验证 |
|---|------|------|------|
| P0-1 | `OzonStatusInput` 补 `moderation_retry_count` 字段（条件边强转不再剥） | ✅ 已实施 | 路由测试 2/2（pending 3 次退出） |
| P0-2 | stale 清理有界化：`retry_count < max_retries` → pending+递增+记错误；`>= max_retries` → failed 终止；lifespan 僵尸重置同守卫；60s 心跳保活（慢任务不被误判卡死） | ✅ 已实施 | 测试含 SQL 守卫断言 |
| P0-3 | 失败分类：轮询超时 → 不重试不降级（`ImagePollTimeoutError`）；violation → 有界重试 2 次（banana 快，重试可能过）；HTTP 重试 2→1；变体 timeout 90→180 | ✅ 已实施 | 5/5（poll 超时仅 1 次 POST 等） |
| P0-4 | 生图幂等：PG 表 `task_generated_images` + `task_image_cache.py` + 10 节点接入（缓存命中直接复用），7 天自动清理 | ✅ 已实施 | mock 全流程 12/12 |

### P1 属性/类目字典修复（Ozon 审计实证）

| # | 方案 | 状态 | 验证 |
|---|------|------|------|
| P1-1 | 帽类 9163：中性词搜索失败 → 取「男+女」双值兜底（Ozon 支持性别多选） | ✅ 已实施 | 3/3 + 离线校验器真实帽类验证 0 错误 |
| P1-2 | 数字属性类型校验：按 schema type（Integer/Decimal）提取数字转换（"12 месяцев"→12、"1000 г"→1000）；无法解析 → 跳过 | ✅ 已实施 | 5/5 |
| P1-3 | 字典属性全量填满：dictionary_id>0 全部未填属性走 ①缓存精确匹配 ②`/values/search`(RU) ③`/values` 列表包含匹配；多值属性取全部匹配；匹配不到跳过 | ✅ 已实施 | 5/5 |

### 工具与监控

| 项 | 方案 | 状态 |
|---|------|------|
| 离线试填校验器 | `worker/scripts/offline_validate.py`：先匹配类目 → 拉真实 schema/字典值 → 复用 prepare 填充函数模拟填值 → 本地校验输出对齐 Ozon 错误码清单。**不真实上架**（Ozon 无 dry-run API，import 提交即创建） | ✅ 已实施，真实帽类验证 0 错误 |
| 类目透传诊断 | `worker/scripts/analyze_category_mismatch.py`：量化 declined/类目错配（DESCRIPTION_DECLINE），为下一版 Widget→Seller 映射表提供数据 | ✅ 已实施（44 个错配清单） |
| 全店审计 | `skill/scripts/audit_products.py`：3 店铺商品状态+错误项只读审计 | ✅ 已实施 |
| Sentry 全局监控 | 任务级 transaction + 节点/生图 span（trace 视图看卡在哪个节点/生图次数）+ 生图 POST 记实际 prompt 前 80 字符 | ✅ 已实施 |
| 提示词排查 | `docs/CHECK-IMAGE-PROMPTS.md`：服务器侧排查步骤（含 Sentry release=dev 铁证） | ✅ 已实施 |

## 四、剩余工作（未完成）

| # | 事项 | 说明 |
|---|------|------|
| R1 | **提交代码** | 19 个改动文件 + 8 个新文件未 commit |
| R2 | **本地 Docker 重建 + 回归** | 代码改动未进本地运行容器（当前容器跑旧镜像） |
| R3 | **真实产品完整流程验证** | 单测全绿基础上，选产品跑完整管线：①帽类（验证 9163 双值上架不再 pending）②数字属性类（验证 VALUE_MUST_BE_INTEGER 消除）③正常新品（无回归）。待用户选产品 |
| R4 | **Sentry 环境正确化** | 部署时 `SENTRY_ENV=production`，确认 `release=v0.26.0`（当前线上 release=dev） |
| R5 | **部署** | `deploy/deploy.sh` 到生产（需用户确认时机） |

## 五、验收标准

1. **死循环消失**：Sentry 不再出现 GraphRecursionError；任务超时/failed 次数显著下降
2. **额度可观测**：生图 POST 次数 = 日志数；轮询超时不再触发重新 POST/降级
3. **重跑不重烧**：任务重试/重启后生图节点命中缓存，Sentry trace 可见复用
4. **帽类修复**：帽类产品 9163 填「男+女」，模拟校验 0 错误；真实上架不再 pending/declined
5. **数字属性**：VALUE_MUST_BE_INTEGER/DECIMAL 从 Ozon 审计中消失
6. **提示词生效**：线上日志 `mxou 生图 POST` 显示新提示词（部署后验证）

## 六、范围外（下一版）

- **A 类目映射表**（Widget ID→Seller dc/type）：44 个类目错配商品修复 + declined 归档+重建；本次已产出诊断数据（`analyze_category_mismatch.py`）供下一版使用
- 变体图数量上限 + 恰好 1 变体双图修复
- 必填属性缺失系列（8229 类型等）根治——依赖类目映射表

## 七、风险与注意事项

1. **9163 男+女双值**：需确认 Ozon 该属性支持多值（帽类 schema is_multivalue 未明确）。若 Ozon 拒绝多值，回退单值 Мужской——实施时已验证（离线校验器通过）
2. **生图幂等缓存**：以 `config.configurable.thread_id`（PG 任务 ID）为 key；`state.task_id` 是 ingest 随机 UUID 不可用（已规避）
3. **stale 有界化**：健康但超 30 分钟的任务现在由心跳保活避免误重置；心跳失败（PG 抖动）仍可能触发一次有界重试
4. **Sentry span**：traces_sample_rate=0.1（默认），生产流量下采样可控
