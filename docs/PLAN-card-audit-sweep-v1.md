# PLAN: 店铺卡不变量巡检（card_audit 域）v1

> 动因：2026-09-26 上架质量战役 4 路根因审计的架构级结论——全部 8 项终态守卫
> 钉死在「提交时点/任务终态时点」，而 Ozon 的真实损害（评分每日重算、审核数小时
> 后才 declined、错货、卡片被平台改写）几乎都发生在终态之后。当前架构下下一批
> 坏卡的发现路径 = 用户肉眼发现 → 找开发者跑一次性脚本。本方案把「人工发现+脚本
> 救」变成「系统持续发现 + 白名单内自动修 + 其余报告待人」。
>
> 审计取证底稿：三份 subagent 审计报告（错货语义链/属性链/价格重量链/终态守卫，
> 2026-09-26 会话留痕）；修复侧 P0/P1 由 PR #84/#85/#86 承接，本方案只做「事后
> 巡检层」，不重复上传前闸。

## 0. 一句话

把「店铺卡健康」做成 store_sync_jobs 的一个**日级域**（`card_audit`），复用现有
调度水位、content_enrich 唯一修复构造器、listing_result_log 源-卡映射，产出
`card_audit_finding` 发现表；rating/媒体缺口白名单内自动修，moderation declined
/ 内容-源不一致只报告。

## 1. 为什么是现在（审计实锤的三个结构性缺口）

1. **零上传后对账**：fetch_back 只 diff「我发送的 vs Ozon 实存」且只在本任务
   窗口内跑一次（`fetch_back_node._content_rating_enhance`，v0.81.0）；评分是
   Ozon 每日重算的动态值，任务窗口外的卡再无人看。
2. **pending 耗尽即「视为成功」**：`graphs/graph.py:394-400` pending 重试 3 次
   耗尽后按成功收尾——Ozon 在终态之后才给出的 declined 永远没有任何组件再去看。
   41 张存量 declined 卡由此产生且不可发现。
3. **moderate_status 被主动丢弃**：`store_sync_service._sync_products` 拉
   /v3/product/info/list 后 `_upsert_products`（:983-985）只映射
   archived/error/visible 三态，`statuses.moderate_status` 整段不落库——declined
   在 `ozon_products_cache` 里不可查询，这是存量 declined 卡「数据模型里不存在」
   的直接原因。

## 2. 自愈地基盘点（全部已存在，缺的只是接线）

| 地基 | 现状 | 本方案用法 |
|---|---|---|
| 调度 | `store_sync_jobs` PG 队列域水位（`services/store_sync_jobs.py:243-246` 域注册表 + `due_credentials` 每域 interval） | 域元组加 `("card_audit", 日级 interval)`，零新调度器 |
| 并发 | `store_sync_scheduler._run_job` 3 并发 worker 池 + zombie 重置 | `_run_job` 加 card_audit 分支 |
| 修复逻辑 | `content_enrich.build_enrich_update_body` 唯一构造器（fetch_back/sweep/改图三消费方同源，#84 后第四消费方） | 巡检自动修走同一构造器，sweep 脚本退役为手动兜底 |
| 源-卡映射 | `listing_result_log`（source_url/source_item_id/source_title_cn/source_category_path ↔ ozon_product_id/dc/tp）+ `product_task_index` | 内容-源一致性巡检的 join 底座 |
| 通知 | `task_processor._send_task_notify`（#80 SSRF 校验后） | 发现汇总推送 |
| 报告范式 | `error_reports.status` 状态机（new/triaging/fixed/wontfix） | finding 表照抄该范式 |

## 3. 巡检不变量与动作分级

每店每日一轮，按 product 批量拉 /v3/product/info/list（**补存 moderate_status**）
+ /v1/product/rating-by-sku（100/批，sweep 已验证）+ /v4/product/info/attributes（按需）：

| 不变量 | 判定 | 动作 |
|---|---|---|
| A 内容完整度 | rating < 90 且缺口 ⊆ 保守白名单（4191 Аннотация / 11254 Rich / 其余 free-text 可填项） | **自动修**（build_enrich_update_body 全量回显 UPDATE；失败留 finding 不重试熔断） |
| B 审核状态 | moderate_status == declined | **只报告**（API 改不了类目；修复=归档+重上，破坏性，人工确认） |
| C 内容-源一致性 | 现卡名称/类目 vs listing_result_log 的 source_title_cn/source_category_path，LLM 比对（cap N/日，成本闸） | **只报告**（错货卡唯一系统内检测通道） |
| D 价格 sanity | old_price ≥ price、差价规则（#84 唯一入口 enforce_old_price_rule）、min_price ≥ 售价 50%、毛利率异常 | 阈值内**自动修**（单字段 UPDATE），超阈**只报告** |

纪律：**自动修只允许走 content_enrich 家族构造器**；任何新自动修出口必须过
A6 防洗卡红线（全量回显）——与上传链同一纪律，不设例外。

## 4. 数据模型

新 append-only 表 `card_audit_finding`（范式抄 error_reports）：
`{id, tenant_id, credential_id, ozon_product_id, invariant ∈ {rating_gap, declined, source_mismatch, price_sanity}, severity, detail JSONB, status ∈ {open, auto_fixed, triaging, fixed, wontfix}, created_at, updated_at}`。
唯一键 `(ozon_product_id, invariant)` + status=open——同一张卡同一不变量不重复
开单，状态流转后才可再开。

`ozon_products_cache` 补 `moderate_status` 列（`_upsert_products` 顺手写），存量
41 张 declined 卡由此一网打尽（B 不变量首轮全量发现）。

## 5. 改动面（预估）

- `services/store_sync_jobs.py`：域注册 + interval（~5 行）
- `services/store_sync_scheduler.py`：`_run_job` 加 card_audit 分支（~20 行）
- **新** `services/card_audit_service.py`：四不变量巡检 + 动作分级（主体，~400 行）
- `services/store_sync_service.py`：`_upsert_products` 补 moderate_status（~10 行）
- `storage/database/shared/model.py`：新表（首启自动建，幂等迁移）
- 通知/汇总：复用 `_send_task_notify` + `capture_task_event`
- `main.py` **零改动**（循环已在）
- `worker/scripts/content_rating_sweep.py`：保留为手动兜底，docstring 指向巡检域

env 开关：`CARD_AUDIT_ENABLED`（默认 1）/ `CARD_AUDIT_LLM_CAP_DAILY`（C 不变量
成本闸，默认 50）/ `CARD_AUDIT_AUTOFIX`（默认 1，A/D 白名单自动修总闸）。

## 6. 验收口径

1. 实机（本地 Docker + 测试店）：造一张缺 4191 的卡 → 一轮巡检后自动补填且
   finding=auto_fixed；造一张 declined 卡 → finding=open 且零写操作。
2. 存量对账：测试店首轮巡检 finding 数与手工 audit 脚本一致。
3. 防洗卡：自动修 UPDATE 载荷过 `_enforce_payload_image_policy` 同款断言（复用
   #84 测试风格锁行为）。
4. 调度水位：card_audit 域 due 计算与 orders 域互不挤兑（3 并发池内排队）。

## 7. 不做什么（明确出圈）

- 不做上传前闸的任何改动（#84/#85/#86 已收口）。
- 不做 declined 卡自动归档/重上（破坏性，永远人工）。
- 不做跨店 LLM 全量一致性比对（成本不可控，cap + 抽样）。
- 不迁 content_rating_sweep 的 argv 交互（保留原样当逃生门）。
