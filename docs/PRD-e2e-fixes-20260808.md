# PRD — E2E 修复 + Sentry 观测增强（v1.0 完整版，2026-08-09）

> 状态：**定稿待批准** — 经 HYPERPLAN 对抗团队（5 成员 × 3 轮交叉攻击）验证 + Ozon schema 实测 + plan agent 实施编排。
> 前置文档：v0.1 讨论稿（`docs/PRD-e2e-fixes-20260808.md` 历史）、实施蓝图（`docs/IMPLEMENTATION-PLAN-e2e-fixes.md` 已被对抗修正）。
> 本文档为唯一权威 PRD；实施执行按 plan agent 输出（T0-T6）。

---

## 一、背景与问题定义

2026-08-08 本地 Docker E2E 全流程测试（**提交 worker 10 任务**：2 1688 选品 + 4 用户 1688 上架 + 4 用户 Ozon 跟卖）：

| 类别 | 结果 |
|---|---|
| 1688 选品上架（2 个） | ✅ 全部成功 |
| 用户 4 个 1688 产品 | ✅ 4/4 成功（3 冰杯 + 1 折叠杯） |
| 用户 4 个 Ozon 跟卖 | ⚠️ **1 成功 3 失败**（全部因类目属性匹配） |
| Ozon 关键词/裂变选品 | ✅ 流程通（裂变候选质量问题见 P2） |

**3 个失败跟卖**（本地环境，未产生生产影响）：
1. 无线迷你风扇（3869426509）→ 必填属性缺失 8229（类型）
2. 驱蚊棒（4363241796）→ 必填属性缺失 8229 + 9782（危险等级）
3. 驱蚊花露水（4365093962）→ 必填属性缺失 8229 + 9782

## 二、证据链（本 PRD 全部结论的事实基础）

### 2.1 Sentry 实际数据（unresolved 100 个，实测）

| 错误类别 | 数量 | 结论 |
|---|---|---|
| 描述语言检查（中文/拉丁） | **62** | 噪音爆炸，同类问题碎成 62 个 issue |
| mxou chat 401 | 258 | 云端生产，8-04→8-08 持续，疑似 token 失效 |
| grsai 生图 failed | 297 | 生图 API 质量问题 |
| 任务 failed（按 task_id 成 issue） | 176 | 每次失败独立 issue，无法聚合 |
| 8229 类型无法获取字典值 | 81 | **类目属性匹配历史高频问题** |
| 9782 危险等级 empty | 76 | **同上，持续到今天** |
| 品牌 85 无法获取 | 111 | 同上 |
| LLM fallback 类目阻断 | 7 | 类目匹配兜底质量问题 |

### 2.2 worker Sentry 埋点审计（8 项缺口）

1. **业务失败不上报**（最大缺口）：`task_processor.py:305-324` `_is_failed` 分支只写 DB 不调 Sentry——Ozon 拒绝类失败不可见
2. 错误码三套体系（WorkerErrorCode / state.error_code / Ozon 错误码）均未接入 Sentry
3. `capture_task_error` 字段太少（仅 task_id/tenant_id，缺 failed_stage/error_code/category_id/attr_id）
4. trace_id 未注入 Sentry（日志↔Sentry 无法关联）
5. 无 FastAPI 全局异常中间件
6. 无 breadcrumb / set_user
7. LLM chat 调用无 span（仅 image_gen 有）
8. fetch_back attr.outcome 遥测仅日志

### 2.3 Ozon schema 实测（2026-08-09，本 PRD 新增）

| 属性 | 风扇类目 (17039635/91443) | 驱蚊类目 (17028747/99385) |
|---|---|---|
| **8229 Тип** | required ✅ **aspect=False** dict=1960 | required ✅ **aspect=False** dict=1960 |
| **9782 Класс опасности** | 不在 schema（不要求） | required ✅ **aspect=False** dict=26026952 |
| 85 Бренд | required, aspect=False | required, aspect=False |

**结论**：
- 8229/9782 均 `is_aspect=False` → **retry 可修复** → 「标题交叉验证 = 永久死路径」风险**不成立**，决定项 1 自动闭环
- 9782 只在驱蚊类目必填 → 与 3 个失败产品模式完全吻合（根因链证据闭环）

### 2.4 对抗团队修正（HYPERPLAN 5 成员 × 3 轮，推翻 3 个 v0.1 错误前提）

| 原 v0.1 前提 | 对抗实证修正 | 来源 |
|---|---|---|
| P0-1：final_attributes 是竞品属性 | **错误**——`follow_sell_import_node.py:304-310` 是硬编码 5 属性（品牌85/5076+产地4389+型号9048+数量8962）；真实竞品属性在 `draft.ozon_attributes`（俄语名→文本值 dict，cloud_probe.py:2877） | logic-critic C1（最强发现）|
| P0-3：9782 在 prepare 修复 | **错误**——主图 prepare 在 retry 子图不执行；真正根因是 `revalidate_node`（validation_retry_loop.py:1849）用 final_attributes **整体覆盖** payload（抹掉首填 9782）+ `repair_prepare_node`(:1347) 从不重跑字典 post-fill | senior-arch B1 + executor-junior 补证 |
| P0-2：8229 修复点在 `_match_product_attr` | **错误**——该函数仅直采路径；follow 走 `attr_defaults.py:253-256` type_id 匹配（PRD v0.1 日志 2 的证据来源） | logic-critic C2 |

**其余收敛裁定**（全部采纳）：
- 语言噪音 62 issue 走 sentry **默认 LoggingIntegration**（非 capture_task_event）→ 修复在 `before_send`（fingerprint 聚合 + level 降级 + trace_id 注入 + logger 名过滤）
- S7 修复目标是 `fetch_seller_products`（ozon_discovery.py:1275，tab 增殖 + 无存活校验），非 fetch_product_info（已覆盖）
- 快照方案 → 「revalidate 覆盖→合并」（ozon_payload 即零成本快照，免 4 处 schema wiring）
- P3 用 push_scope + before_send 组合（30 worker 并发 scope tag 泄漏）
- `error_repair_llm_node:818 search_result[0]` 盲取是独立潜伏 hazard bug（保留修复，非 9782 根因）

---

## 三、修复范围与优先级

| 优先级 | 项 | 影响 | 对应 Task |
|---|---|---|---|
| **P0-1** | 跟卖属性合并（ozon_attributes 竞品优先） | 跟卖评分低 + 属性缺失失败 | T1 |
| **P0-2** | 8229 类型匹配（attr_defaults 修复 + 判别词交叉验证） | 必填缺失/错配拒绝 | T2 |
| **P0-3** | 9782 重试保留（revalidate 覆盖→合并） | 重试清空值导致失败 | T3 |
| **P1** | retry 属性重建（post-fill 重跑 + search_result[0] 盲取） | 二次 prepare 清值 | T3 |
| **P2** | skill 工具链（S1-S10） | 用户侧易用性 | T4 |
| **P3** | Sentry 观测增强（8 项） | 问题分析能力 | T5 |
| **T0** | mxou 401 ×258 排查 | 独立诊断 | T0 |

---

## 四、详细方案

### P0-1 跟卖属性合并（T1）

**现状**：follow 组装只消费硬编码 5 属性（品牌/产地/型号/数量），完全丢弃：
- `draft.ozon_attributes`（竞品俄语属性，skill 抓取，俄语名→文本值）
- `draft.attributes`（1688 中文属性）

**方案**：合并链「`draft.ozon_attributes`（RU 名→attr_id→dict_id 字典解析）→ `draft.attributes`（中文语义匹配）→ 硬编码兜底」。
- 扩展 `attr_defaults.py:134-159` 6 类语义消费为通用解析器
- `ozon_attributes` 文本值**必须**过字典解析（values/search → dictionary_value_id），绝不裸注入 payload
- 无字典匹配 → 跳过（不注入原文）

**验收**：`test_follow_attr_merge.py` GREEN（4 场景：竞品有值/无竞品用1688/双无走兜底/文本无匹配跳过）；`test_follow_sell_v5.py` 无回归。

### P0-2 8229 类型匹配（T2）

**现状**：8229 有 3 条填充路径（`_match_product_attr` 直采 / `_validate_and_enrich_items` / `attr_defaults.py:246-289` post-fill），follow 走 attr_defaults type_id 匹配；「专利类型」属性名污染 + 按 type_id 错配（148495146 手持风扇 vs 实际桌面款）。

**方案**：
1. 修复点改到 `attr_defaults.py:246-289`（type_id 匹配为主），统一 3 路径纪律
2. 干扰属性名黑名单（专利类型/光源类型/开关类型…），**绝不排除纯「类型」本身**（test_language_routing.py:54 合法用例）
3. 判别词交叉验证（桌面/手持/挂脖/落地…判别词表，禁用 2-gram）——已实测 aspect=False，无死路径风险
4. 唯一值兜底；仍不匹配 → 跳过 + attr_id=8229 结构化上报（P3）

**验收**：`test_language_routing.py` 4 用例保持绿 + `test_8229_follow.py` GREEN；schema 探测记录（aspect=False 已实测）。

### P0-3 + P1 retry 属性保留（T3）

**根因**（对抗修正后）：`revalidate_node`（validation_retry_loop.py:1849）用 `state.final_attributes` **整体覆盖** `first_item["attributes"]`——抹掉首填 9782；`repair_prepare_node`(:1347) 从不重跑字典 post-fill。

**方案**：
1. `revalidate_node` 覆盖→**合并**：以 `first_item["attributes"]`（payload 快照）为基线，应用定向修复（翻译/dict_id 强制/9782 安全守卫/9048 复用/is_aspect 跳过/remove_attrs 显式删除）；快照有而 final_attributes 无的属性（首填 9782）保留
2. `repair_prepare_node` 对字典属性错误重跑 `resolve_missing_mandatory_dict_attr`
3. `error_repair_llm_node:810-825` 替换 `search_result[0]` 盲取为统一语义解析（type_id/判别词/唯一值 → None）
4. 变体同步 + 防重译边界（9048 模式泛化）

**验收**：`test_retry_attr_snapshot.py`（行为断言，非源码 inspect）GREEN：payload 有首填 9782 + final_attributes 缺 → revalidate 后 9782 存活；`test_retry_self_repair.py:122` inspect 断言改行为断言（现有断言对 `search_result[0]` bug 天然失明）。

### P2 skill 工具链（T4）

| # | 问题 | 方案 |
|---|---|---|
| S1 | batch_test 不认 m 站 URL（`offerId=` query） | `parse_urls_file` 兼容 + dj.1688.com 302 解析 |
| S2 | batch_test 不自动读凭证 | 复用 `get_ozon_credentials(store_id)` |
| S3 | `--worker-url` 读错变量（MXOU_API_BASE） | 改 `WORKER_URL` 优先 |
| S5 | 裂变候选无运营数据 | `run_fission` 接 `fetch_sales_analytics` + `apply_analytics_to_candidate` |
| S6 | 裂变严重偏题 | BFS 按种子类目过滤跨类目候选 |
| S7 | **fetch_seller_products tab 增殖**（fission 20 卖家=20 tab，ozon_discovery.py:1275-1322） | 复用 `_ensure_ozon_tab` + 存活校验 + release |
| S8 | dj.1688.com 推广链接无法富化 | 302 解析（并入 S1） |
| S9 | discover 匹配结果不进导出 JSON | 导出合并 match_selected |
| S10 | batch_test `return in finally` SyntaxWarning | 重构 |

**验收**：`test_fetch_seller_tab_reuse.py` + `test_batch_test_url_parse.py` GREEN；多卖家调用 ≤1 tab（mock 计数断言）。

### P3 Sentry 观测增强（T5）

1. `init_sentry` 加 `before_send`：语言噪音 fingerprint 聚合 + level 降级（error→warning）+ trace_id 注入 + logger 名过滤；`_SENTRY_ENABLED` 短路
2. `capture_task_error/Event` 加 kwargs：failed_stage/error_code/category_id/attr_id（tag）+ notice/upload_status/product_id（extra）
3. `task_processor.py:305-324` `_is_failed` 分支补 `capture_task_error`（业务失败上报）
4. `push_scope` + before_send（防 scope tag 泄漏）
5. FastAPI 全局 `@app.exception_handler(Exception)` + WorkerErrorCode 映射
6. `call_mxou_chat_api` 加 span
7. 节点流转 add_breadcrumb

**验收**：`test_sentry_setup.py` GREEN（before_send 行为断言）；`SENTRY_DSN=""` 本地零上报；Sentry project 语言噪音 issue 聚合降级。

### T0 mxou 401 ×258 排查

**已确认事实链**（PRD v0.1 §6.4）：chat API 用 `state.token`（用户 key）；401 事件走 logging 集成（无 task_id/tenant_id，无法定位用户）；本地 token 有效、云端持续 401；auth 剥离 `sk-` 前缀但 chat 不剥离。

**T0 产出**：裁定（code-side vs platform-side）+ 证据；若 code-side → RED 测试 + 最小修复；若 platform-side → 文档化上报，不写投机修复。

---

## 五、实施编排（plan agent 输出，provenance: ses_01dda1cfeffe6hq0x7S6D0aPbl）

```
Wave 0: T0 mxou 401 排查（独立，deep+debugging+sentry-cli）
Wave 1: T1 worker 属性合并 [deep+programming]   ← 3 条并行
        T4 skill tab 复用+URL 解析 [deep+programming+debugging]
        T5 Sentry before_send 指纹 [deep+programming+sentry-cli]
Wave 2: T2 8229 修复（依赖 T1）
Wave 3: T3 9782/revalidate 覆盖→合并（依赖 T1+T2，ultrabrain）
Wave 4: T6 E2E 回归（硬门禁：10/10 全过才放行）
```

关键路径 T1 → T2 → T3 → T6；并行加速 ~45-55% 墙钟时间。

---

## 六、验收总纲（T6 硬门禁）

1. worker 全量单测绿（`cd worker && PYTHONPATH=src python3 -m pytest tests/ -v`，含新测试 + 行为化重写）
2. skill 单测绿（`cd skill && python3.12 -m pytest tests/`）
3. **本地 Docker E2E 10/10 成功**，3 个失败跟卖通过且 8229（type_id dict）/9782（安全默认）在**首次组装 + retry 后**均正确（revalidate 合并保留）
4. `SENTRY_DSN=""` 本地零上报；Sentry 语言噪音聚合降级
5. **全程不碰云端**；任务表清理干净（zombie 红线：`DELETE FROM ozon_product_tasks WHERE status IN ('pending','failed','running')`）
6. 提交规范：`type(scope): 中文描述`，原子提交，pre-commit（.githooks）通过，不提交密钥

---

## 七、待用户拍板（剩余 2 项，第 1 项已自动闭环）

| # | 拍板项 | 现状 | 建议 |
|---|---|---|---|
| 1 | 8229 交叉验证 aspect 风险 | ✅ **已闭环**——schema 实测 aspect=False，无死路径 | 直接实现判别词交叉验证 |
| 2 | 失败任务重跑 | 重跑=真实上架+真实凭证，违反「不留痕迹」 | **本地 Docker 复现**（不碰云端）；若坚持生产重跑需另行授权 |
| 3 | 店铺重置范围 | 重置=本地 E2E stores.json 快照+恢复 | 不碰云端，按此范围 |

---

*PRD v1.0 — 2026-08-09，对抗验证 + schema 实测后定稿。*
