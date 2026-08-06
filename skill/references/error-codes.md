# Worker 响应处理与错误码

## 提交成功（submit_result.ok == true）

Worker 返回：
```json
{"ok": true, "task_id": "550e8400-...", "message": "Task submitted to queue"}
```

按以下模板回复用户：
> ✅ 任务已提交到云端处理
> - 任务 ID：`{task_id}`
> - 预计耗时：10–20 分钟（类目匹配 → AI 生图 → Ozon 上架 → 审核）
> - 用户可在 Ozon 卖家后台查看上架结果，或稍后用 `batch_test --wait` 查询。如有问题 Worker 会自动重试修复。

## 提交失败错误码表

| Worker 错误码 | 原因 | 回复用户 |
|--------------|------|----------|
| `TOKEN_INVALID` / `TOKEN_MISSING` | MXOU_TOKEN 无效或缺失 | "凭证无效，请重新设置 MXOU_TOKEN：`python3 scripts/cli.py set_token --token <你的token>`" |
| `TOKEN_DISABLED` / `TOKEN_EXPIRED` | 账户被禁用或过期 | "账户已被禁用或过期，请联系管理员。" |
| `INSUFFICIENT_BALANCE` | 余额不足 | "账户余额不足（{detail.remain_quota}），请充值后重试。" |
| `RATE_LIMITED` | 请求太频繁 | "请求太频繁，请稍后再试（每分钟限制 {limit} 次）。" |
| `INVALID_REQUEST` | 信封数据不完整 | "产品数据不完整：{message}。请检查 1688 商品页是否正常加载，或重试。" |
| `TASK_SUBMIT_FAILED` | 队列写入失败 | "任务入队失败，Worker 内部错误。请稍后重试。" |
| `SERVICE_UNAVAILABLE` | 服务不可用 | "云端服务暂时不可用，请稍后重试。" |
| `INTERNAL_ERROR` | 未知内部错误 | "Worker 内部错误：{message}。请稍后重试，如持续出现请联系技术支持。" |
| 网络错误（ConnectionError） | Worker 不可达 | "无法连接云端服务。请检查网络连接和 WORKER_URL 配置。" |
| 网络错误（Timeout） | 请求超时 | "云端服务响应超时，请稍后重试。" |

## 进度查询口径

> ⚠️ **审核被拒 ≠ Worker 提交失败**：上架后 Ozon 审核被拒（卖家后台显示拒绝原因）与提交时的 Worker 错误码是两类问题。审核被拒引导用户在 Ozon 卖家后台查看具体原因（类目/属性/图片/侵权），**不套用上文提交错误码表**。

用户问"进度"、"完成了没"时：

- **批量提交**：用 `batch_test.py --wait` 自动轮询（每 5s 查一次），完成后打印每个产品的明细（1688链接/利润率/售价/采购价/运费/净利润率/OzonID）。
- **单任务查询**：CLI 未暴露单任务查询子命令。用户追问单个任务进度时：
  1. 告知任务正在云端处理中（类目匹配 → AI 生图 → Ozon 上传 → 审核），预计 10–20 分钟
  2. 建议用户等待后用 `batch_test.py --wait` 查看结果，或在 Ozon 卖家后台查看商品状态
  3. 不要自行调 Worker API 轮询（skill 无此命令）

## CLI 错误处理

| 错误 | 回复用户 |
|------|----------|
| 1688 验证码拦截 | "1688 出现验证码，请在 Chrome 浏览器中滑动验证后按 Enter 继续。" |
| 1688 未登录 | "1688 未登录，请在 Chrome 中打开 1688.com 登录后告诉我。" |
| Ozon DataDome 拦截 | "Ozon 页面被反爬拦截，请在 Chrome 中访问一次 Ozon 后告诉我。" |
| 1688 AK 缺失 | "缺少 1688 AK。请执行：`python3 scripts/cli.py set_ak --ak <你的AK>`" |
| Ozon 店铺未配置 | "店铺未配置。请执行：`python3 scripts/cli.py set_store --name '店铺名' --client-id <ID> --api-key <KEY>`" |
| 图搜无结果 | "1688 上未找到同款产品。要不要试试用关键词搜索？" |
| Worker 返回错误 | 按上文错误码表回复用户 |
| AI 关键词输出非法 JSON | 明确报错「关键词总结失败：JSON 解析错误」，不猜测关键词继续 |
| 市场信息缺失 | 提示用 --market-info 传入 web_search 结果或配置 SEARXNG_URL；不凭空编造趋势 |
| 无 web_search 且未配置 SEARXNG_URL | 明确告知用户需要市场信息才能做趋势选品，询问是否退回管线 C 常规选品 |

**遇到任何错误，描述问题并引导用户修复。不自己修代码、不自己探索项目结构。**
