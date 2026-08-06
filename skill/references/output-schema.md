# 输出字段解析

所有业务命令（graph / follow / batch_test）输出 JSON，关键字段：

| 字段 | 类型 | 含义 | 取值方式 |
|---|---|---|---|
| `summary` | dict | 商品摘要 | 提取标题、价格、重量、图片数 → 汇报给用户 |
| `envelope` | dict | 完整 GraphInput 信封 | 内部数据，不需解析 |
| `submit_result.ok` | bool | 提交是否成功 | `true` → 按 error-codes.md 成功模板回复；`false` → 按错误码表回复 |
| `submit_result.task_id` | str | 任务 ID | 提取后告知用户，用于后续查询 |
| `submit_result.error_code` | str | 错误码 | 按 error-codes.md 错误码表回复 |
| `product_summary[]` | array | 产品明细（`--wait` 轮询后） | 提取 1688链接/利润率/售价/采购价/运费/净利润率/OzonID → 表格展示 |

**成败判定**：`submit_result.ok == true` → 成功；否则按 `error_code` 查错误码表。

## submit_result JSON 示例

**成功**（cloud_probe.py:483，Worker 返回）：
```json
{
  "ok": true,
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Task submitted to queue"
}
```

**失败**（cloud_probe.py:471-491）：
```json
{
  "ok": false,
  "error": "Token invalid or expired",
  "error_code": "TOKEN_INVALID",
  "detail": "",
  "http_status": 401,
  "task_id": null
}
```

> 失败时 `error_code` 可能为空字符串——此时取 `error` 字段描述问题，按 error-codes.md CLI 错误处理表回复。

## check_task_status 返回结构

`check_task_status(task_id)` 返回（cloud_probe.py:2478-2490）：

```json
{
  "task_id": "550e8400-...",
  "status": "completed",
  "ok": true,
  "terminal": true,
  "error_message": null,
  "result_json": {
    "product_summary": [
      {
        "purchase_url": "https://detail.1688.com/offer/980815374096.html",
        "purchase_cost": 33.5,
        "margin_rate": 0.25,
        "price": "69",
        "logistics_cost": 14.04,
        "profit_rate": 0.39,
        "product_id": "5840335148",
        "ozon_status": "approved",
        "ozon_error": ""
      }
    ]
  },
  "retry_count": 0,
  "started_at": "2026-08-06T10:00:00Z",
  "completed_at": "2026-08-06T10:15:00Z"
}
```

**status 取值**：`completed`（成功终态）/ `failed`（失败终态）/ `cancelled`（取消终态）/ `pending` / `running`（非终态）/ `not_found` / `worker_unreachable` / `query_error`

**终态判定**：`terminal == true` → 任务结束（completed/failed/cancelled）；`terminal == false` → 仍在处理中。

> CLI 未暴露单任务查询子命令。批量查询用 `batch_test.py --wait` 自动轮询。详见 error-codes.md 进度查询口径。

## product_summary[] 字段详解

| 字段 | 类型 | 示例值 | 含义 | 展示列 |
|---|---|---|---|---|
| `purchase_url` | string | `https://detail.1688.com/offer/980815374096.html` | 1688 采购链接 | 链接 |
| `purchase_cost` | float | `33.5` | 采购价（CNY） | 采购价 ¥ |
| `margin_rate` | float | `0.25` | 利润率 | 利润率 % |
| `price` | string | `69` | Ozon 售价（RUB） | 售价 |
| `logistics_cost` | float | `14.04` | 运费预估（CNY） | 运费 ¥ |
| `profit_rate` | float | `0.39` | 净利润率 | 净利润率 % |
| `product_id` | string | `5840335148` | Ozon 商品 ID | OzonID |
| `ozon_status` | string | `approved` / `pending` / `declined` | 审核状态（v0.27+） | 状态 |
| `ozon_error` | string | `""` / `DESCRIPTION_DECLINE` | 拒绝原因（declined 时） | 备注 |

汇报时以表格展示：`1688 链接 | 采购价 | 利润率 | 售价 | 运费 | 净利润率 | OzonID | 状态`。

**汇报模板**：
- 成功：`✅ 任务已提交，任务 ID: {task_id}。预计 10–20 分钟完成。`
- 失败：按 error-codes.md 错误码表回复（含修复指引）。
- 轮询完成（batch_test --wait）：`✅ 任务完成。产品明细：[表格]`。
