---
title: PLAN—harness 对接 worker 远程 MCP
purpose: harness 侧 dsh 双 MCP 一键配置施工说明书（批次 3 跨仓执行中）
applies-version: ">=v0.67.0"
last-updated: 2026-09-08
owner: harness
depends: [MCP-SERVER]
status: active
---

# PLAN-harness-mcp-adoption-v1 — pounding-harness 对接 worker 远程 MCP 施工文档（批次 3 说明书）

> 状态: in-progress（批次 1 已落 v0.67.0；批次 3 在 harness 独立仓库施工）

> 2026-09-05 定稿。批次 1（worker `/mcp` 远程 MCP，v0.67.0）已落地，本文档是 harness 侧
> 对接的**施工说明书**——本批次 harness 一行代码未动，采集箱/任务中心/网关全部保持现状；
> 本文档供批次 3（harness 仓库）执行时照单施工。
> 背景与决策记录见 ozon-worker `docs/MCP-SERVER.md` 与 CHANGELOG v0.67.0。

## 0. 目标架构（两轮讨论拍板）

```
终端用户
  ├─ agent 会话（dsh / Claude Code / Cursor / 任何平台）
  │    ├─ 挂 pounding-mcp（本地 stdio/uvx，采集执行面：CDP 抓取/图搜/discover，依赖本机 Chrome 登录态）
  │    └─ 挂 worker /mcp（远程 streamable-http，云端运营面：上架/草稿/店铺/查询，Bearer=mxou key）
  ├─ harness 薄壳 = Tauri 拉起 dsh + 网关 + 设置页 + 只读看板（品牌壳）
  └─ webui = 云端 ERP（REST，不受影响）

维护对象：skill(+MCP) + worker(+MCP) + 薄壳
```

**边界铁律**：MCP 化的是「agent ↔ 服务」接口；UI ↔ 服务继续走 REST（网关代理），服务 ↔ 服务
继续 REST。采集必须留在本地（浏览器登录态），上架/运营在云端。

**竞品参照**：linkfox 27 个远程 MCP 服务（`streamableHttp + JWT header`，Kong 网关）只做
第三方选品数据；没有 Ozon 官方 API 上架/店铺运营 MCP——我们全链路 MCP 化是差异化。

## 1. dsh 挂载配置（批次 3 第一步）

现状：harness 的 `runtime/home/profiles/web/cordis.patch.yml` 是空数组 `[]`，dsh 零 MCP 挂载；
本地 pounding-mcp 以 stdio 挂 dev 期 `dsh web --patch`。

目标态（完整示例，壳安装时生成 / 设置页一键写入）：

```yaml
# runtime/home/profiles/web/cordis.patch.yml
- insert:
    id: mcp-pounding            # 本地采集（执行面，必须本地）
    name: "@deepseek-ai/dsh-mcp-client"
    transport: stdio
    command: uvx
    args: ["pounding-mcp"]      # 批次 2 发包后；过渡期: python -m pounding_mcp.server
- insert:
    id: mcp-pounding-worker     # 云端运营（本批次新能力）
    name: "@deepseek-ai/dsh-mcp-client"
    transport: streamable-http
    url: https://worker.mxou.cn/mcp
    headers:
      Authorization: "Bearer <mxou key>"
```

- 工具在 dsh 内可见为 `mcp__pounding__*`（采集）与 `mcp__pounding-worker__*`（云端）。
- URL 用**无尾斜杠** `/mcp`（worker 已做裸路径内部转交，307 不再出现）。
- 「一键配置」落点：壳设置页（读 `settings.json` 的 mxou key → 生成上面 patch 并写盘 →
  重启 dsh 生效）。过渡期用户手动粘贴也可用（对标 linkfox 的交付形式）。

## 2. 凭证流转（网关明文代管链的退役路径）

现状（`web/boujoy_server.py`）：网关从 skill `settings.json` 读明文 `mxou_token`（`_read_mxou_token`
:855）注入 `/api/worker/*`（:903）+ submit 类端点再注 `body.token`；前端只见 masked（:856 注释）。

目标态：
- **mxou key 只存一处**：`settings.json`（设置页写入，skill / 本地 pounding-mcp / 远程 MCP 配置三方同源）。
- 远程 MCP 的 key 在 dsh 挂载配置 headers 里（设置页生成时从 settings.json 取）。
- 批次 3 末段：网关 `/api/worker/*` 只剩看板只读路由（见 §3），`_read_mxou_token`/`_inject_mxou_token`
  /submit 端点 body 注 token 逻辑整体删除——写操作全部经 agent → MCP（鉴权在 worker 侧完成）。

## 3. harness 现有 UI 保留策略（采集箱不动）

| 页面 | 现状（批次 1 时点） | 批次 3 处置 |
|---|---|---|
| 采集箱 CollectPage（`frontend/src/App.tsx:1465`，fetchDrafts/submitDraft 经 worker.ts:115-143） | 走网关 `/api/worker/drafts*` | **保留**。网关保留只读 drafts 代理 + `drafts/{id}/submit` 一条写路由（保留现有 token 注入）；其余 `/api/worker/*` 通用透传删除 |
| 任务中心 TasksPage（App.tsx:1756） | 双源：worker tasks + 8902 本地采集任务 | **单源化**：删 8902 源与 `/api/pounding/tasks` 代理（`_tasks_proxy` :1776-1797）；保留 worker tasks 只读代理 |
| 其余 7 页（总览/执行现场/知识库/专家/监控/情报/定价器） | 走 dsh RPC 桥，不碰 worker | 不变 |
| 设置抽屉（App.tsx:2128） | 走网关 skill-config（本地文件读写） | 保留 + 增加「MCP 配置一键生成」入口（§1） |

**8902（pounding-mcp tasks_server）退役条件与步骤**：
1. 前提：dsh 已挂本地 pounding MCP，采集发起（discover/graph/follow/image_search）全部经对话 MCP 工具；
   结果经 `to_box=true` 归档 worker 草稿箱（链路已实证：docs/P1-VERIFICATION.md）。
2. 删网关 `/api/pounding/tasks*` 代理与前端 `fetchPoundingTasks/cancelPoundingTask/createPoundingTask`
   （worker.ts:124-137）+ CollectDialog 本地任务分支 → 任务中心单源。
3. pounding-mcp 仓库侧 `tasks_server.py` 保留（`/ask` 意图路由仍可独立用），仅 harness 不再代理。

## 4. 网关瘦身清单（批次 3 scope 速查）

**可删**：`/api/worker/*` 通用透传（缩成 §3 白名单 2-3 条）、`/api/pounding/tasks*` 代理、
`_read_mxou_token`/`_inject_mxou_token` 明文代管链、submit 类 body token 注入。
**必留**：静态 serve web/、dsh HTTP+WS 桥（`/api/harness/*`，Typert/cookie 认证）、skill-config 读写、
vault/news/records、`/api/app/restart`、浏览器宿主编排（过渡期）。
**旧宿主清理**（TARGET-ARCHITECTURE 已拍板）：删 `macos/`、`windows/*.ps1`、`app/`（Electron）；
`electron-browser/` → 系统 Chrome `--remote-debugging-port=9222`（skill `chrome_launcher` 自启兜底），
删除后网关 `ensure_browser_host`（:2033-2062）同步简化。
**不受影响**：skill 二进制分发缺口（PyO3/sidecar）与 MCP 无关，按原 roadmap P3 推进。

## 5. 写类工具安全分级（dsh 审批映射）

worker MCP 工具已在描述中标注读写性；dsh 侧 `tools/pre-execute` 钩子按工具名分级建议：

| 分级 | 工具 | 策略 |
|---|---|---|
| read | get_task_status / get_task_statistics / list_drafts / list_stores / analyze_store / lookup_commission / quote_logistics / lookup_mapping / get_seo_keywords | 直接放行 |
| write | submit_task / cancel_task / submit_draft / batch_submit_drafts | 用户确认（真实上架/消耗额度） |
| destructive | run_store_action（批量改价/库存/归档/促销） | 用户确认 + 展示 operation/params 摘要 |

## 6. 兼容与回滚

- **worker 端开关**：`MCP_ENABLED=0` 整体关闭 `/mcp`（HTTP API 不受影响）——线上异常时热切。
- **老 worker 兼容**：dsh 挂了远程 MCP 但 worker 未升级（无 /mcp 路由）→ 客户端连接失败，
  agent 侧表现为工具不可用；harness 应在设置页提供「测试连接」（GET /api/v1/health + MCP 握手）并提示升级。
- **fastmcp 缺失降级**：worker 镜像自带；手工环境未装 fastmcp → main.py 顶层 try/except 跳过挂载并
  warning，REST 完全不受影响。
- **回滚**：harness 侧还原 cordis.patch.yml（删 mcp-pounding-worker 段）即回纯本地模式；
  网关在批次 3 未执行前保持现状，无需回滚。

## 7. 验收清单（批次 3 完成定义）

- [ ] dsh 会话内 `mcp__pounding-worker__list_tools` 可见 17 工具；`get_seo_keywords` 实调返回数据
- [ ] 设置页「一键配置 MCP」生成 cordis.patch.yml 并重启 dsh 生效；「测试连接」通过
- [ ] 对话链路：本地 `discover` → 草稿入箱 → `submit_draft` 真实上架 → `get_task_status` 到 approved
- [ ] 审批钩子：`run_store_action` 触发 dsh 确认框；read 类直行
- [ ] 采集箱/任务中心回归：drafts 列表、submit、任务单源展示正常（8902 退役后）
- [ ] 网关瘦身回归：smoke_test 契约断言同步更新（AGENTS.md 契约门）
- [ ] 匿名 `POST /mcp` 401、无效 key 401、超限 429（worker 侧已由 test_mcp_server.py 锁定）

## 施工现状（2026-09-08 核对）

> 以下以 harness 仓库 `/Volumes/os/dev/pounding-harness/web/boujoy_server.py` 实际代码为准（已逐项 grep 核对）。

**已落地（批次 3 主体）**：

- dsh MCP 一键配置：`/api/mcp/config` GET/POST（boujoy_server.py:1769/:1922）+ `/api/mcp/test` 握手测试（:1925）。
- managed block 三段：`mcp-pounding` / `mcp-pounding-worker` / `mcp-approval-guard`（`_MCP_PATCH_ROW_IDS` :155）。
- §5 审批分级插件 `_MCP_APPROVAL_PLUGIN_JS`（:157），仅作用于 `mcp__pounding-worker__*` 前缀（read 直行，write/destructive 用户确认）。
- 网关白名单：GET 仅 `(drafts|tasks|drafts/{id})`（:1773）、POST 仅 `drafts/{id}/submit`（:1929）——§4 的「通用透传缩白名单」已执行。
- 8902 镜像与 `/api/pounding/tasks*` 代理已删（任务中心单源 worker）。

**剩余（harness 侧待办）**：

- §2 `_read_mxou_token` 明文代管链退役（:1009 定义，仍有约 5 处调用：:1036/:1057/:1098/:1134/:1194）。
- §4 旧宿主清理：`macos/`、`windows/*.ps1` 仍在仓库；`app/`（Electron）与 `electron-browser/` 已删除。

**批次 2**：pounding-mcp 发包准备已做（`pyproject.toml` version=0.70.0、description 对齐 25 工具；README.md 仍写「19 个命令」待对齐），实际 publish 待 PyPI token。
