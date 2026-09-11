---
title: worker 远程 MCP 服务接入指南
purpose: /mcp 端点接入、Bearer 鉴权、工具清单与客户端配置
applies-version: ">=v0.73.0"
last-updated: 2026-09-11
owner: mcp
depends: [API-OVERVIEW, CONTRACT-v4]
status: active
---

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

## 工具清单（22 个）

| 工具 | 类型 | 说明 |
|---|---|---|
| `submit_task(envelope, ozon_client_id, ozon_api_key)` | 写 | 提交上架任务（信封 {draft, source, extensions}） |
| `get_task_status(task_id)` | 读 | 任务状态与进度 |
| `cancel_task(task_id)` | 写 | 取消任务（仅 pending） |
| `get_task_statistics()` | 读 | 本租户任务统计 |
| `list_drafts()` | 读 | 采集箱草稿列表（精简字段，不含 envelope 大字段） |
| `get_draft(draft_id)` | 读 | 草稿全文（payload 信封 + version；v0.71）。改配类目/填属性前先取 |
| `patch_draft(draft_id, version, payload)` | 写 | 更新草稿（乐观锁，payload=完整 envelope；v0.71）。典型：ozon_category{dc,tp,source:"manual"} + attributes |
| `assemble_draft(draft_id)` | 写 | 一键 AI 预组装（RU 标题/描述/属性写回，幂等；v0.71） |
| `search_categories(q, limit?)` | 读 | 类目树搜索 ZH_HANS（dc/tp/路径；v0.71） |
| `get_category_attributes(dc, tp, attr_id?)` | 读 | 类目特征属性 schema（缓存优先，未命中自动按需拉 Ozon 回写；attr_id=单属性字典值；v0.71） |
| `submit_draft(draft_id, credential_id, template_id?)` | 写 | 提交草稿上架 |
| `batch_submit_drafts(ids, credential_id)` | 写 | 批量提交（≤50） |
| `list_stores()` | 读 | 店铺凭证列表（api_key 掩码） |
| `analyze_store(credential_id)` | 读 | 整店分析（利润/库存/低利润/缺货清单） |
| `run_store_action(credential_id, operation, params)` | ⚠️写 | 批量改价/改库存/归档/活动报名/自建促销 |
| `lookup_commission(category_id)` | 读 | 类目佣金分段（FBS/FBO × 价格段） |
| `quote_logistics(weight_g, depth_cm, width_cm, height_cm, creds?)` | 读 | 物流运费报价（CNY） |
| `lookup_mapping(keyword)` | 读 | 1688 中文类目 → 已学习 Ozon 类目映射 |
| `get_seo_keywords(q, limit)` | 读 | Ozon 蓝海 SEO 流量关键词（标题/hashtag 用）。只读。limit ≤50 |
| `report_issue(title, severity?, category?, description?, reproduction?, evidence?)` | 写 | 用户问题反馈 → 错误报告入 worker 跟踪队列（v0.70）。按 `evidence.task_ids` 自动附加本租户任务快照（状态/错误/时间线/product_id）；提交成功返回 `report_id`。模板契约见 `docs/ERROR-REPORT-TEMPLATE.md` |
| `list_error_reports(status?, limit?, report_id?)` | 读 | 查本租户错误报告（只读）。status ∈ {new,triaging,fixed,wontfix} 可筛；`report_id` 非空返回单条详情（含自动附加的任务快照 auto_context） |
| `get_task_forensics(task_id)` | 读 | 任务取证一站式只读聚合（v0.70）：任务快照 + 上架留存(listing_result_log) + 类目/属性匹配审计。排查「为什么失败/为什么这么上架」首选——先取证再报 issue |

**agent 改配工作流（v0.71，与 webui 表单同链）**：`list_drafts` → `get_draft` →
`search_categories` 选定 dc/tp → `get_category_attributes` 拉 schema → 按 schema
填 `draft.attributes`（字典属性取 values 的 id/value；is_collection=false 恒单值）→
`patch_draft` 回写（source=manual 即权威直通）→ `assemble_draft` 预检 → `submit_draft`。

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

## 附：参数差分核查表（v0.75）— 本地 pounding-mcp 30 工具

> **范围注记**：本表核查的是**本地 pounding-mcp（stdio，`pounding_mcp/server.py`，30 工具）**
> 与 `skill/scripts/cli.py` argparse 全集的参数差分（v0.75 收口批 C10），与上文 worker 远程
> MCP（22 工具）是两套服务。事实源：argparse 定义（cli.py `build_parser`）；映射机制
> `_build_argv`（下划线→连字符，None/False/"" 跳过，True→裸 flag）。方法论与 B1 先例一致
> （graph 补 category/type/min-density，commit 6d0d4561）。回归
> `pounding-mcp/tests/test_param_parity_c10.py`（8 用例）+ `test_smoke.py`（30 工具注册）。

### 核查结论

**①类（MCP 缺 CLI 参数）真漂移 4 工具 7 参，已修**（缺省不进 argv，旧行为逐字保持）：
`search`+export/threads、`image_search`+ozon_product_id、`queries`+export/output、
`session_sync`+status/cdp_url。**①类有意裁剪面 3 工具 44 参，登记不修**（discover 族
docstring 委托 `--help`）。**②类（MCP 有 CLI 无）0 处**（background/force 是 MCP 层参数，
`_run_or_background` 消费不进 argv；report_issue 字段是 worker REST 体，不走 argv）。
**③类语义漂移 2 处，登记不修**（见下）。

### 矩阵（21 个 skill CLI 封装）

| 工具 | CLI 全参数（*必填） | MCP 覆盖 | 差异 |
|---|---|---|---|
| check | （无参） | 同 | ✅ |
| list_stores | （无参） | 同 | ✅ |
| set_store | name*/client_id*/api_key*/currency | 全覆盖 | ✅ |
| set_token | token* | 同 | ✅ |
| set_ak | ak* | 同 | ✅ |
| get_ak | timeout(300) | 同 | ✅ |
| search | query(位)*/page-size(5)/sort/export/rules/store/auto-submit/to-box/threads(3) | 全覆盖（export/threads **v0.75 补**） | ①修复 |
| probe | url*/timeout(30) | 同 | ✅ |
| image_search | image*/limit(10)/sort/source(aibuy)/ozon-product-id | 全覆盖（ozon_product_id **v0.75 补**） | ①修复 |
| category | query(位)*/lang(ZH_HANS)/max(5)/store | 全覆盖 | ✅ |
| follow | ozon_url*/auto-submit/to-box/store/review/notify | 全覆盖 | ✅ |
| discover | 33 参 | 16 参 | ①裁剪 17（docstring 委托 --help） |
| discover_multi | 21 参 | 7 参 | ①裁剪 14（v0.75 补 docstring 委托声明） |
| discover_task | 27 参 | 14 参 | ①裁剪 13 + ③dry_run（见下） |
| seller | seller_id*/max-products(60)/max-skus(30) | 全覆盖 | ✅ |
| queries | type/keyword/sku/category_id/price_min/price_max/export(csv)/output | 全覆盖（export/output **v0.75 补**） | ①修复 |
| graph | item_id/url/category_query/category_id/type_id/min_density/retries(3)/store/no_submit/to_box/ozon_ref_url/template_id/notify | 全覆盖 | ✅（B1 已收口） |
| query | task_id(位)*/watch/timeout(900) | 全覆盖 | ✅ |
| update | （无参） | 同 | ✅ |
| cleanup | profile_cache/cache/temp/old_results/days(30)/dry_run/all | 硬编码 all=True+dry_run=True | ③登记 |
| session_sync | credential_id*/status/worker_url/cdp_url | 全覆盖（status/cdp_url **v0.75 补**） | ①修复 |

### 矩阵（5 个 worker REST 直调 + 4 个 job_*，无 CLI 对应不参与差分）

analyze_store(store_id) / run_store_action(store_id,operation,payload) /
report_issue(title,severity,category,description,steps,command,expect,actual,
task_ids,item_id,error_codes,extra_evidence) / list_error_reports(status,limit,report_id) /
get_task_forensics(task_id)；job_list(limit) / job_status(task_id,log_tail) /
job_result(task_id) / job_cancel(task_id)。注记：report_issue 与 CLI `report` 子命令是
同一 worker 端点的双通道（CLI 多 --step/--draft-ids/--offer-id/--ozon-product-id，
MCP 以 extra_evidence dict 承载，语义等价非漂移）。

### ③类语义漂移登记（只登记不修，均已在 docstring 声明）

1. **cleanup 硬编码 `--all --dry-run`**（CLI 默认 False/False）——有意的「只预演不真删」
   安全设计；真删走 skill CLI 人工执行。
2. **discover_task `dry_run` 默认 True**（CLI store_true 默认 False），且 CLI 侧
   `--dry-run` 压制双出口（cli.py `if args.dry_run or not (to_box or auto_submit)`）——
   即 MCP `discover_task(to_box=True)` 缺省 dry_run=True 时**入箱被干跑压制成 no-op**，
   必须显式 `dry_run=False`。docstring 已有「零副作用」声明 + v0.75 补压制警示句；
   默认值是否翻转为 None（→不传 flag，双出口按 CLI 原生语义生效）属产品决策，待拍板。

### 其他口径注记（非漂移）

- discover/discover_multi 的 `--china` 是 argparse.SUPPRESS 隐藏兼容 flag，MCP 有意不露出。
- search 的 --auto-submit/--to-box 是 CLI 互斥组，MCP schema 无法表达互斥，同传由 CLI
  argparse 报错兜底（工具 docstring 已注明）。
- image_search CLI help 首行「ak=1688 AK API（默认）」文案陈旧（default 实为 aibuy）——
  MCP 默认 aibuy 与 CLI 实际默认一致，属 CLI 侧 help nit。
- 修复未动 flag 名/默认语义：新参缺省值均等价于 CLI 默认（""/None/False 不进 argv）。
