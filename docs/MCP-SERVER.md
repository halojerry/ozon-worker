# MCP-SERVER.md — worker 远程 MCP 服务接入指南（v0.67.0 批次 1）

> 任何支持远程 MCP 的平台（dsh / Claude Code / Cursor / Cherry Studio / ChatGPT…）
> 用下面的配置即可调用 worker 云端能力。对标竞品 linkfox 的 `streamableHttp + JWT header`
> 交付形式，但鉴权直接复用现有 **mxou key**，不引入新账号概念。

## 服务信息

| 项 | 值 |
|---|---|
| 端点 | `https://worker.mxou.cn/mcp`（本地环境 `http://localhost:8080/mcp`） |
| Transport | Streamable HTTP（MCP 规范 2025-03-26+，单端点，POST JSON-RPC） |
| 鉴权 | `Authorization: Bearer <mxou key>`（与 worker REST / webui 同一 token 体系） |
| 多租户 | 按 key 自动隔离（复用 `_authenticate_token`：限流 300/min、吊销、租户解析） |
| 开关 | 服务端 `MCP_ENABLED=0` 可整体关闭（默认开） |

**获取 key**：webui 设置页 / MXOU 控制台复制 API Key（`sk-` 开头）。key 拥有你账户的完整
操作权限（含真实上架与改价），**仅填入可信客户端**。

## 双 MCP 分工（重要）

| | 本服务（远程，worker 云端） | 本地 pounding MCP（stdio，用户本机） |
|---|---|---|
| 职责 | 上架任务/采集箱/店铺运营/查询 | 1688+Ozon 页面采集（依赖本机 Chrome 登录态） |
| 典型工具 | `submit_task` `submit_draft` `analyze_store` | `graph` `follow` `discover` `image_search` `check` |

典型编排：本地 `discover` 选品 → 结果入采集箱 → 本服务 `submit_draft` 上架；
或本地组装信封 → 本服务 `submit_task` 直提。

## 工具清单（14 个）

| 工具 | 类型 | 说明 |
|---|---|---|
| `submit_task(envelope, ozon_client_id, ozon_api_key)` | 写 | 提交上架任务（信封 {draft, source, extensions}） |
| `get_task_status(task_id)` | 读 | 任务状态与进度 |
| `cancel_task(task_id)` | 写 | 取消任务（仅 pending） |
| `get_task_statistics()` | 读 | 本租户任务统计 |
| `list_drafts()` | 读 | 采集箱草稿列表（精简字段，不含 envelope 大字段） |
| `submit_draft(draft_id, credential_id, template_id?)` | 写 | 提交草稿上架 |
| `batch_submit_drafts(ids, credential_id)` | 写 | 批量提交（≤50） |
| `list_stores()` | 读 | 店铺凭证列表（api_key 掩码） |
| `analyze_store(credential_id)` | 读 | 整店分析（利润/库存/低利润/缺货清单） |
| `run_store_action(credential_id, operation, params)` | ⚠️写 | 批量改价/改库存/归档/活动报名/自建促销 |
| `lookup_commission(category_id)` | 读 | 类目佣金分段（FBS/FBO × 价格段） |
| `quote_logistics(weight_g, depth_cm, width_cm, height_cm, creds?)` | 读 | 物流运费报价（CNY） |
| `lookup_mapping(keyword)` | 读 | 1688 中文类目 → 已学习 Ozon 类目映射 |
| `get_seo_keywords(q, limit)` | 读 | Ozon 蓝海流量关键词 |

安全分级：`run_store_action` / `submit_task` / `submit_*` 为真实写操作。dsh 侧已有
`tools/pre-execute` 审批钩子（read/write/destructive 分级）；其它平台建议开启工具调用确认。

## 各平台接入配置（复制即用）

### dsh（cordis patch / harness 设置页生成）

```json
{
  "id": "mcp-pounding-worker",
  "name": "@deepseek-ai/dsh-mcp-client",
  "transport": "streamable-http",
  "url": "https://worker.mxou.cn/mcp",
  "headers": { "Authorization": "Bearer <你的 mxou key>" }
}
```

### Claude Code / ZCode（`.mcp.json` / mcp 配置）

```json
{
  "mcpServers": {
    "pounding-worker": {
      "type": "http",
      "url": "https://worker.mxou.cn/mcp",
      "headers": { "Authorization": "Bearer <你的 mxou key>" }
    }
  }
}
```

### Cursor（`~/.cursor/mcp.json`）

```json
{
  "mcpServers": {
    "pounding-worker": {
      "url": "https://worker.mxou.cn/mcp",
      "headers": { "Authorization": "Bearer <你的 mxou key>" }
    }
  }
}
```

### Cherry Studio（v1.4.8+，类型选 Streamable Http）

```json
{
  "mcpServers": {
    "pounding-worker": {
      "type": "streamableHttp",
      "url": "https://worker.mxou.cn/mcp",
      "headers": { "Authorization": "Bearer <你的 mxou key>" }
    }
  }
}
```

## 验证

```bash
# 匿名应 401
curl -i -X POST https://worker.mxou.cn/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'
# 带 key 应 200（initialize 握手）
curl -i -X POST https://worker.mxou.cn/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'Authorization: Bearer <你的 mxou key>' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

## 实现与运维备注（改代码前必读）

- 实现在 `worker/src/mcp_server.py`；`main.py` 顶层挂载（`MCP_ENABLED` 守卫 + lifespan 合并
  `_root_lifespan` + 精确 `/mcp` 无尾斜杠内部转交 `_McpNoSlash`——Mount 对裸路径会在鉴权前
  307，勿删）。
- **零业务逻辑**：工具经进程内 httpx ASGITransport 回调现有 REST 路由，鉴权/租户/校验/错误码
  与 REST 同源。给 REST 加字段自动对 MCP 生效；改路由路径要同步 `mcp_server.py` 的 `_call` 调用。
- 限流计数：一次工具调用记 2 次（MCP 中间件 + 内层路由各一次），比 REST 更保守。
- 回归：`worker/tests/test_mcp_server.py`（19 用例：工具整形/鉴权中间件/挂载面）。
- harness 侧对接（dsh 挂载、网关瘦身、8902 退役）见 `docs/PLAN-harness-mcp-adoption-v1.md`。
