# 错误报告模板（Error Report Template）— v0.69

> 目的：用户问题反馈结构化。出现错误时，**agent 按本模板填写**（含复现方式与证据）
> → 调 MCP 工具 `report_issue`（或直接 POST worker）→ worker 落库并**自动附加任务
> 快照** → 开发侧按 `status` 流转（new → triaging → fixed/wontfix）闭环。
> 动机实例：2026-09-07 泡脚包三连失败 + 17 条官方清单——此前靠对话人工粘贴，
> 报告落库后问题发现/复现/修复全程可追溯。

## 通道

| 层 | 入口 |
|---|---|
| agent（推荐） | MCP 工具 `mcp__pounding__report_issue`（pounding-mcp，参数即模板字段） |
| HTTP | `POST /api/v1/error_reports`（Bearer = mxou key；鉴权/限流与 analytics 同源） |
| 查询 | `GET /api/v1/error_reports`（列表，`?status=&limit=&offset=`）；`?report_id=` 单条详情 |

## 模板字段

```jsonc
POST /api/v1/error_reports
{
  "title": "必填。一句话问题概括（≤500 字）",
  "severity": "high | medium | low（缺省 medium）",
  "category": "upload_failed | category_wrong | attribute_error | image_error
             | pricing | cli_bug | other（缺省 other）",
  "description": "现象描述：期望 vs 实际，何时开始、影响面",
  "reproduction": {                    // 复现方式（agent 按用户口述+日志整理）
    "steps": ["1. 打开 dsh → 输入『上架 …』", "2. skill graph --url <1688 链接>", "..."],
    "command": "python scripts/cli.py graph --url https://detail.1688.com/xxx",
    "expect": "Ozon 卡片创建并过审",
    "actual": "task completed 但 Ozon 侧查无此品"
  },
  "evidence": {                        // 证据（agent 用现有工具收集）
    "task_ids": ["3170fd33-…", "64e5e6c6-…"],   // ← worker 按此自动附任务快照
    "draft_ids": ["ff7d0346-…"],
    "item_id": "623236101606",          // 1688 offer
    "offer_id": "e2e-xxx",              // Ozon offer（如有）
    "ozon_product_id": "…",
    "error_codes": ["VALUE_MAX_LIMIT", "INCORRECT_DIMENSION"],
    "skill_version": "0.69.0",
    "platform": "win32",
    "worker_url": "https://worker.mxou.cn"
  }
  // 扁平兼容：task_ids / item_id / error_codes 等可直接放顶层，worker 自动归入 evidence
}
```

**响应**：`{"status":"ok","report_id":"<uuid>","created_at":"…","tasks_attached":N}`
`tasks_attached` = worker 成功附加的本租户任务快照数；`task_ids` 全部查不到/不属本租户时
`auto_context.note` 会说明（报告仍落库）。

**worker 自动 enrichment（`auto_context.tasks[]`）**：每任务一条
`{task_id, status, error_message(≤500), created_at, completed_at, product_id}`。
取证实证：假成功单（17s completed、product_id 空）有快照即可秒判，无需再问用户。

## agent 使用纪律（pounding agent 按 SKILL/本节执行）

1. 用户报问题 → 先用现有工具收集证据：`check_task_status`（任务终态/错误/进度）、
   `lookup_*`/信封（draft payload），不要凭口述直接报。
2. `error_codes` 优先取任务终态里的 Ozon 拒单码（VALUE_MAX_LIMIT 等），不要意译。
3. `task_ids` 必填（有任务号才有自动快照）；纯 CLI 崩溃（无任务）可只给
   `command`+`actual`，category 用 `cli_bug`。
4. 凭证安全：**不要**把 api_key/token 写进任何字段（worker 只存模板字段）。
5. 提交后把 `report_id` 回给用户，告知「已入跟踪队列」。

## 实例（泡脚包，已按模板回填）

```jsonc
{
  "title": "1688 offer 623236101606（福丫丫艾草泡脚包）主店三次上架失败",
  "severity": "high",
  "category": "upload_failed",
  "description": "三次提交均失败且形态不同：假成功/Ozon 属性越界/尺寸越界",
  "reproduction": {
    "steps": ["graph 提交 623236101606 → task 3170fd33 17s completed 但 OzonID 空且查无此品",
              "重提 → 64e5e6c6 VALUE_MAX_LIMIT 属性 8962 越界（信封无此属性，worker 生成）",
              "再提 → 35082a80 INCORRECT_DIMENSION 宽 430 超上限 400"],
    "command": "cli.py graph --url https://detail.1688.com/offer/623236101606.html",
    "expect": "主店创建泡脚包卡片",
    "actual": "三单三种失败形态"
  },
  "evidence": {
    "task_ids": ["3170fd33", "64e5e6c6", "35082a80"],
    "item_id": "623236101606",
    "error_codes": ["VALUE_MAX_LIMIT", "INCORRECT_DIMENSION"],
    "platform": "win32"
  }
}
```

（该实例即 v0.69 批次的直接输入——clamp/8962 清洗/假 completed 收口三条修复的出处。）
