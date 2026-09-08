# 错误上报（Error Report）— 出错时按此模板报给 worker

> 适用场景：任务 failed / Ozon 拒审且重试无解 / 未知错误码 / 用户明确抱怨结果。
> 上报后 worker 落库并**自动附加任务快照**（按 task_ids），开发侧可追溯、可复现、可修复。
> 契约细节（字段全集、worker 自动 enrichment 机制）以仓库 `docs/ERROR-REPORT-TEMPLATE.md`
> 为单一事实源，本文档只写 agent 行为纪律与字段速查。

## 何时报（满足其一就报，不要攒）

1. 任务终态 `failed`，且按 `error-codes.md` 决策表重试仍失败。
2. Ozon 拒审（rejected）且原因不明/反复出现（同类商品第二次被拒）。
3. 遇到错误码表里没有的未知错误码。
4. 用户明确抱怨结果不对（假成功：task completed 但 Ozon 查无此品 / 卡片内容不符）。

纯用户操作问题（链接贴错、店铺没配置）**不报**，引导修复即可。

## 怎么报（三条通道，按优先级）

1. **MCP 工具（dsh 等 agent 推荐）**：`mcp__pounding__report_issue`，参数即模板字段，
   工具 docstring 有说明。
2. **CLI（无 MCP 环境用）**：

   ```bash
   python3 scripts/cli.py report \
     --title "1688 offer xxx 上架失败：尺寸越界" \
     --severity high --category upload_failed \
     --description "期望创建卡片，实际 Ozon 拒单 INCORRECT_DIMENSION" \
     --step "graph --url <1688 链接> 提交" \
     --step "query <task_id> 显示 failed" \
     --command "python3 scripts/cli.py graph --url https://detail.1688.com/xxx.html" \
     --expect "Ozon 卡片创建并过审" \
     --actual "Ozon 拒单 INCORRECT_DIMENSION" \
     --task-ids "<task_id1>,<task_id2>" \
     --error-codes "INCORRECT_DIMENSION"
   ```

3. **HTTP（兜底）**：`POST /api/v1/error_reports`，`Authorization: Bearer <mxou_token>`，
   body 见 `docs/ERROR-REPORT-TEMPLATE.md`。

## 配套查询（MCP，v0.69/v0.70）

- **`mcp__pounding__list_error_reports(status, limit, report_id)`**：查本租户已提交报告的
  处理状态（`new/triaging/fixed/wontfix`）；`report_id` 非空返回单条详情（含 worker 自动
  附加的任务快照 `auto_context`）。用户问「我报的问题怎么样了」时用。
- **`mcp__pounding__get_task_forensics(task_id)`**：任务取证四路聚合（任务快照 + 上架留存
  listing_result_log + 类目/属性匹配审计）。排查「为什么失败 / 为什么这么上架」**先取证再上报**。

## 字段速查

| 字段 | 必填 | 取值 |
|---|---|---|
| `title` | ✅ | 一句话概括（≤500 字） |
| `severity` | | `high`（主流程挂/资金损失）、`medium`（缺省）、`low` |
| `category` | | `upload_failed` / `category_wrong` / `attribute_error` / `image_error` / `pricing` / `cli_bug` / `other` |
| `description` | | 现象：期望 vs 实际、何时开始、影响面 |
| `--step`（可多次） | | 复现步骤，按顺序 |
| `--command` / `--expect` / `--actual` | | 复现命令 / 期望结果 / 实际结果 |
| `--task-ids` | ✅（有任务号必给） | 逗号分隔；**worker 按此自动附任务快照**，没有快照=排查靠猜 |
| `--error-codes` | | Ozon 拒单码**原样**（VALUE_MAX_LIMIT 等），不要意译 |
| `--item-id` / `--offer-id` / `--ozon-product-id` / `--draft-ids` | | 1688 商品 / Ozon offer / Ozon 卡片 / 采集箱草稿 |

响应：`{"status":"ok","report_id":"<uuid>","tasks_attached":N}`。

## 纪律（红线）

1. **先收证据再报**：先 `query <task_id>`（或 `check_task_status` / MCP `get_task_forensics`）拿终态/错误/进度/留存事实，不要凭用户口述直接报。
2. `error_codes` 取任务终态里的 Ozon 原码，不意译。
3. `task_ids` 有就必给；纯 CLI 崩溃（无任务号）可只给 `--command`+`--actual`，category 用 `cli_bug`。
4. **任何字段都不写 api_key / token**（凭证零明文）。
5. 提交成功后把 `report_id` 回给用户：「已入跟踪队列，report_id=…」。
6. 上报不代替给用户的解释——先按 `error-codes.md` 回复用户，再补报。
