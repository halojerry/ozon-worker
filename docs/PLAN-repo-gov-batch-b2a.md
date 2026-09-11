---
title: PLAN—repo-gov 止血修复批 B2-α
purpose: BL-01~23 九项止血修复（已合 dev PR #13，待发版）
applies-version: ">=v0.74.0"
last-updated: 2026-09-11
owner: worker-pipeline
depends: []
status: active
---

# PLAN-repo-gov-batch-b2a — 止血修复批（BL-01/02/03/04/05/07/14/22/23）

> 状态: completed-dev（B2-α 九项已随 PR #13 合 dev，2026-09-11，在 v0.74.0 之后 → 发版后可升级 shipped；原状态行「执行中（2026-09-11 用户拍板：四批全跑，B2-α 先行；variant_v2=标记实验性）」）
> 分支：fix/repo-gov-b2a（自 origin/dev 044b0c61）。执行：3 个并行 subagent（文件组不相交），主会话 review+测试+PR。
> 探针记录：主会话已亲核六处改动点现状（见各任务「现状」），全部 P1 断言 grep 实锤。

## 任务组 G1（存储层 + main.py）

| ID | 现状（已亲核） | 改法 |
|---|---|---|
| BL-14 | import_logistics.py:155-156 DELETE 后 interim commit，:162 再 commit | 删 :156 中间 commit，单事务 |
| BL-23 | local_db_manager.set_attribute_cache type_id 裸传（None→NULL→ON CONFLICT 永不匹配→裂行） | 入口 `type_id=int(type_id or 0)` 归一（对齐 v0.72 字典侧修法） |
| BL-05 | sku_metrics_pool_service.upsert_seller_sync_items SELECT-then-INSERT，并发 IntegrityError 整批丢+analytics_routes.py:349-352 兜底 except 误标 422「invalid item」 | 每条 item begin_nested() savepoint + IntegrityError 回滚后 re-select 合并重试一次；路由 except 收窄为 (ValueError,KeyError,TypeError)，IntegrityError 单独 500 诚实文案 |
| BL-07 | data_erasure_service.STORE_SCOPED_TABLES 16 表无 ozon_sessions | 清单补 ozon_sessions |
| BL-22 | 过期缓存行永不物理删 | main.py _periodic_task_cleanup 加每日分支：ctid IN (…LIMIT 5000) 批量 DELETE dictionary_value_cache/attribute_cache 过期行（expires_at 为 int 秒，与 int(time.time()) 比），迭代 cap20，非致命 |
| BL-01b | main.py 无 fx 刷新 | 同 cleanup 循环加每日 lazy import `utils.fx_rate_service.refresh_cny_rub_if_due`（try/except 非致命，与 G2 解耦） |

## 任务组 G2（外呼层）

| ID | 现状 | 改法 |
|---|---|---|
| BL-01a | exchange_rates 表零写者（set_exchange_rate 全仓无调用），pricing_node._get_exchange_rate 兜底 12.0 仅 warning | 新 utils/fx_rate_service.py：`fetch_cny_rub_live()`（open.er-api.com/v6/latest/CNY，timeout10，失败 None）/ `resolve_cny_rub_rate() -> (rate, source)`（pg_cache→live_fetch 持久化→fallback_12）/ `refresh_cny_rub_if_due()`（24h 节流吞错）。pricing_node 集成：fallback_12 时 pricing_info 加 `exchange_rate_source`/`exchange_rate_fallback: True` marks，warning 保留 |
| BL-02 | ozon_client.py:91 `_rate_limiter.acquire(endpoint)` 返回值被忽略（acquire 阻塞式，超时返 False=fail-open 静默） | 超时时 logger.warning 带端点（可见性），per-credential 桶评估留给 B2-β 设计 |
| BL-04 | repair_cards.py:34-35 硬编码 CLIENT_ID/API_KEY | argparse --client-id/--api-key 必填（env 回退），缺失 exit 2；头部注释注明轮换义务；git 历史清除另立决策 |

## 任务组 G3（文档）

| ID | 改法 |
|---|---|
| BL-03 | AGENTS.md v0.74 块 + CHANGELOG 0.74.0 + PLAN-data-pool-parity-v1.md 三处标注 variant_v2 真值链「实验性未生效」（fetch_variant_truth 无生产调用/needs_variant_sync 零消费，接线计划在数据池批 8 P2） |

## 验收

- 新增/扩展测试全绿（pytest 单文件，绝对 venv 路径 /Volumes/os/dev/ozon-worker/skill/.venv314）
- ruff check src/ --select E,F,W --ignore E501 零新红
- 不触 API 路由签名（无需 gen_api_docs）；sku 路由仅改错误分支文案不改 schema
- 主会话 diff review 后逐文件提交
