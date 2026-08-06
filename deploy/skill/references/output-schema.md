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

**product_summary[] 字段详解**（batch_test --wait 轮询后 task_status 返回）：

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
