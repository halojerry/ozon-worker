# ARCHITECTURE — 电商版 DeepSeek Harness 完整架构（v3.3）

> 本文档是 ozonharness 方案的核心。取代旧版 `../ECOM-HARNESS-PLAN.md`（v2）。
> v3 新增：三种协议边界（§二）、插件化壳化（§六）、仓库结构（§七）。
> v3.1 修正：web_fetch 带不了 Bearer header。
> v3.2 修正：bash+curl 可带 header（沙箱不限网络）；审批须在 dsh 侧 pre-execute，非 MCP server 内。
> v3.3 修正：pounding-mcp 归入 ozon-worker 仓库（skill 薄封装，强耦合，同仓同步）；真正独立的是 harness-shell 和 dsh。
> 全部结论基于 dsh `0.1.0-rc.7` 本地源码 + ozon-worker 源码实证。

---

## 一、产品定位与目标

### 1.1 要做什么

把 ozon-worker 的「AI 自动化运营」能力，做成一个**电商版 Harness 桌面产品**：用户与 Agent 对话，即可完成 Ozon/1688 的选品、采集、上架、订单管理等全部操作。

### 1.2 参考对象与定位

| 参考对象 | 它是什么 | 我们学什么 |
|---|---|---|
| **DAY1-Clean** | dsh 套壳成 macOS 产品（Swift 壳 + Python 网关 + 自有 UI）| 「dsh 只当引擎，外面套自己的壳」的产品形态 |
| **hairyf/deepseek-harness-desktop** | dsh 的跨平台 Tauri 壳 | 跨平台壳底座 + dsh 进程生命周期管理 |
| **PCDCK/pounding-mcp** | Ozon 官方 API → MCP 工具（FastMCP + 三级安全门控）| MCP 封装范式 + 安全门控 + 知识层 |

### 1.3 核心原则

1. **对话即完成**：用户只说话，Agent 编排一切
2. **插件化壳化**：不 fork、不并入 dsh，只通过官方 patch 层挂载
3. **独立维护**：ozon 各仓库（ozon-worker / harness-shell）独立版本、独立 CI，dsh 只是被 pin 的依赖；pounding-mcp 因与 skill 强耦合，归入 ozon-worker 同仓
4. **可见性分层**：危险操作「老板眼皮底下」，低风险操作「默默干」

---

## 二、三种协议边界（v3 核心修正）

这是最容易混的认知，也是之前方案反复返工的根源。

### 2.1 三种协议，三个完全不同的「调用方」

| 协议/接口 | 是谁和谁之间 | 给谁用 | 对应资产 |
|---|---|---|---|
| **MCP** | Agent ↔ 工具 | 让 dsh Agent 调用「进程内能力」 | skill 的 19 个 CLI 命令 |
| **REST (FastAPI)** | 调用方 ↔ worker | 任何 HTTP 客户端（Agent/前端/skill） | worker 的 100+ 端点 |
| **SDK** | 开发者/代码 ↔ worker | 人写代码、前端、代码沙箱 | `generated.d.ts`（已是成品） |

**关键：MCP 和 REST 是正交的，不是二选一。**

### 2.2 每个对象该走什么协议

| 对象 | 本质 | Agent 怎么用 | 需要 MCP 吗 |
|---|---|---|---|
| **worker（FastAPI）** | 已经是 REST API（100+ 端点，`/api/v1`）| 上架主链路：skill 间接（`graph` 内部调）；读查询：MCP 工具或本地网关注入鉴权 | 读查询需要；主链路不需要 |
| **skill（Python CLI）** | 进程内能力：Chrome CDP、1688/Ozon 采集、图搜、选品引擎 | 必须工具化才能被 Agent 调用 | **需要** ✅ |

### 2.3 为什么上架主链路不需要 MCP，也不需要 SDK

- **上架主链路**：`graph` 命令内部已调 worker 提交（`submit_task`/`drafts`），Agent 只需调 `mcp__pounding__graph`，无需直接碰 worker，因此主链路不需要再包一层 worker-mcp。
- **SDK**：SDK 是给「人」和「前端代码」的。`generated.d.ts`（6970 行）**已经是 SDK 成品**。只有 Agent 需要「写代码编排 worker」时（dsh 的 `run_code` + bash 沙箱）才 import SDK——但这对「对话即完成」是反模式（写代码不如直接调工具可靠）。
- **⚠ 鉴权约束（关键修正）**：dsh 原生 `web_fetch` 只接受 `url` 一个参数、**不能带 Authorization header**（源码 `dsh-tool-web/lib/types/fetch.d.ts` 实证）。但 `bash` 工具可跑 `curl -H "Authorization: Bearer xxx"`（沙箱只限文件写、**不限网络**，见 §2.5）。所以 Agent 能直调 worker，只是 web_fetch 这条路走不通、bash+curl 这条路脆弱。

### 2.4 关键事实：skill 内部已经调了 worker

skill 的 `scripts/cloud_probe.py` 已在直连 worker 的 REST：

```
POST {WORKER_URL}/submit_task            # cloud_probe.py:468
POST {base}/api/v1/drafts                # cloud_probe.py:547
GET  /task_status/{task_id}              # cloud_probe.py:2990
GET  /api/v1/mappings/lookup             # cloud_probe.py:414
GET  /api/v1/health                      # cli.py:930（check 命令）
```

**推论**：Agent 的主链路（上架）**根本不用直接碰 worker**——它调 `mcp__pounding__graph`，skill 内部自己会去调 worker 提交。

### 2.5 worker 访问路径汇总（修正后的正确结论）

Agent 够到 worker 的路径（源码实证，按优先级）：

| 路径 | 场景 | 鉴权方式 | 状态 |
|---|---|---|---|
| **skill 间接** | 上架主链路（`graph`/`follow`/`discover` 提交）| skill 内部注入凭证 | ✅ 已实现，主链路 |
| **MCP 工具（读查询）** | 查订单/商品/任务状态等只读查询 | MCP 层注入 Bearer（推荐）| 需新建（读端点 → 只读 MCP 工具）|
| **bash + curl** | 兜底（模型自己拼 curl 命令）| 命令里带 `-H Authorization` | ⚠ 可行但脆弱（沙箱不限网络）|
| **本地网关** | 需要干净封装的受保护端点 | 网关代理 + 注入鉴权 | P3 网关一并做 |
| web_fetch | 仅免鉴权端点（`site/*`、`/health`）| ❌ 带不了 header | 有限 |

> **准确结论**：「worker 不需要 MCP」应改为「**上架主链路不需要 MCP**（skill 已间接处理）；**读查询推荐用 MCP 或网关做干净封装**」。Agent 技术上能通过 `bash + curl` 直调（沙箱只限文件写、不限网络，源码 `dsh-bash-sandbox` 实证），但这是脆弱路径（模型需手拼 curl、JSON 转义易错），不作为推荐。web_fetch 带不了 header，仅限免鉴权端点。
>
> **鉴权细节**：只读端点（products/orders/tasks/drafts 等）走 Bearer header；提交类端点（`submit_task`/`resubmit_task`）的 token 在 **body**（`main.py` 的 `_extract_token_from_body`），不在 Authorization header。所以 bash+curl 兜底主要适用于只读端点；提交类操作仍以 skill 间接为主链路。

---

## 三、skill / worker 分工原则（物理约束决定归属）

> 能力需要什么运行环境，就归属哪一层。这是铁律，不是感觉。

### 3.1 必须留 skill（本地进程，依赖本地 Chrome/CDP）

| 能力 | 命令 | 为什么必须在本地 |
|---|---|---|
| 1688 商品采集 | `probe` / `search` | CDP 操作本地 Chrome，带登录态/反爬 |
| 以图搜款 | `image_search` | 本地图片文件 + 上传 |
| 跟卖/竞品采集 | `follow` | Ozon 竞品页需登录态浏览器 |
| 选品分析 | `discover` / `seller` / `queries` | seller.ozon.ru 需浏览器 + 本地表格计算 |
| 1688 AK 获取 | `get_ak` | 浏览器自动化获取 |
| 凭证/店铺配置 | `set_store` / `set_token` / `set_ak` / `check` | 本地 Chrome profile / 本地凭证 |
| Chrome profile 迁移 | `migrate_profile`（独立脚本，非 CLI 子命令）| 本地 Chrome profile |

### 3.2 必须留 worker（后端，持久化/后台执行）

| 能力 | 端点 | 为什么必须在后端 |
|---|---|---|
| 上架执行 | `submit_task` → LangGraph 编排 | 后台长任务（采集→生成图→上传→定价），需队列/重试/配额 |
| 任务编排 | `task_status` / `cancel` / `resubmit` / `statistics` | 多店铺并发、进度跟踪 |
| 商品/订单/模板/店铺管理 | `products` / `orders` / `templates` / `credentials` | 数据库持久化 |
| 图片生成 | `images` | 长耗时 AI 生成 |
| 后台/配置 | `admin/*` / `site/*` / `analytics/*` | 管理面 |

### 3.3 唯一重叠区：Ozon 官方 API（两边都在调）

这不是 bug，是**两个不同阶段**。归属规则：

| Ozon API 调用 | 在哪 | 为什么 |
|---|---|---|
| **采集/选品阶段**：类目查询、竞品分析、产品信息查询、校验（读）| skill（`skill/scripts/lib/ozon_api.py`）| 需配合本地浏览器上下文，实时读 |
| **上架执行阶段**：import/update/upload、配额检查（写）| worker（`worker/src/utils/ozon_client.py`）| 后台编排 + 限流 + 稳定凭证 |

**一句话**：`读`（采集/选品/校验）→ skill；`写`（上架/改价/上传）→ worker。

### 3.4 结论：现有分工已对，不用重划

当前 skill/worker 分工已经符合「物理约束」原则，是合理的。方案里**skill 零改动、worker 零改动**，要新增的只有一层 `pounding-mcp`。

---

## 四、数据流拓扑

三条数据流，边界清晰：

| 数据流 | 方向 | 状态 |
|---|---|---|
| webui → worker（`/api/v1`）| 商品/订单/任务/大屏的读和管 | 已实现 |
| skill → worker（`submit_task`/`drafts`/`task_status`）| 采集完提交上架 | 已实现 |
| skill → 1688/Ozon 官方 API（本地 CDP 直连）| 采集源数据 | 已实现 |

**「数据从云端 worker 来」对经营数据成立；但 1688/Ozon 竞品采集数据是 skill 在本地抓的，不进 worker。**

部署拓扑：

```
本地（桌面 App）：dsh Agent + skill + webui + pounding-mcp
云端（Docker）：worker + Postgres + Supabase
```

---

## 五、UI 整合（三套 UI 的关系）

现在有**三套 UI**，这是方案里最易混乱处：

| UI | 是什么 | 角色 |
|---|---|---|
| **dsh 原生 web UI** | 对话/Agent/审批/会话界面 | 对话面（命令入口）|
| **我们的 webui** | React 19 + TanStack Router，20+ 页面 | 经营面（商品/订单/任务/大屏）|
| **DAY1-Clean 的 boujoy UI** | 自有 Web 前端（非 dsh UI）| 参考对象 |

### 5.1 webui 不该被 dsh 替换，而是「与 dsh 互补」

- **dsh 负责「对话即操作」**：用户说"上架这个 1688 链接"，Agent 调 skill
- **webui 负责「看结果」**：上架后的商品列表、订单、任务进度、数据大屏——这些 dsh 的对话界面做不好、也不该做

两者是**一个产品的两面**：对话面 + 经营面。

### 5.2 dsh 界面改造的两种路线

dsh 有正式的 UI 扩展机制（`dsh-client-ui-slots`：`register({name, children, store, inject}, Component)` 注册组件到 slot）。

| 路线 | 做法 | 耦合度 | 推荐 |
|---|---|---|---|
| **A. Electron 壳 + iframe 共存** | 一个壳里：主区加载 dsh UI，侧栏/标签页 iframe 加载 webui | 零耦合 | **Phase 1 推荐** |
| **B. dsh client-ui 插件** | 用 slot 系统把电商面板写进 dsh UI 本体 | 高耦合 | Phase 2 可选 |

### 5.3 为什么优先 iframe（而不是 client-ui 深集成）

1. dsh 是 developer preview（官方声明 `THERE WILL BE COMPATIBILITY-BREAKING CHANGES`），client-ui API 会变
2. webui 独立跑、iframe 嵌入，dsh 更新零影响——符合「独立维护」诉求
3. DAY1-Clean 已实证：**它没改造 dsh UI，而是自己写 web UI + Python 网关代理 dsh 本地 API**（`web/index.html + app.js + boujoy_server.py`）

---

## 六、插件化壳化（dsh 更新时我们零感知）

### 6.1 dsh 的插件挂载机制（源码实证）

dsh 每个 profile 有一个**补丁层**（`home/profiles/web/cordis.patch.yml`），官方注释明确：

> "Your patch layer for this dsh profile, applied after every bundle layer... **Edit cordis.patch.yml, not this file.**"

我们的安装动作就是往这个文件加一段 MCP 配置：

```yaml
# cordis.patch.yml（我们的补丁层，patch 层用 insert 包裹）
- insert:
    - id: mcp-pounding
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: pounding
        transport: stdio
        command: python3
        args: ['-m', 'pounding_mcp']   # ← ozon-worker 仓库内的包
```

**装我们的包 = 加这段配置；卸载 = 删这段配置。dsh 本体一行不动。**

### 6.2 为什么这是「插件化壳化」

| 接触点 | 稳定性 |
|---|---|
| dsh ↔ pounding-mcp | **MCP 协议**（稳定标准，不是 dsh 私有 API）|
| pounding-mcp ↔ skill | **CLI 接口**（稳定，skill 自己维护）|
| dsh ↔ webui | **iframe**（零耦合）|

dsh 更新时，我们唯一的动作是：
1. 改 shell 里 pin 的版本号
2. 跑一遍 pounding-mcp 回归测试（MCP 协议稳定，工具注册/调用不受影响）
3. 若 dsh 改了 patch 格式（罕见），只改我们自己的 patch 文件

### 6.3 唯一的耦合红线

**webui 走 iframe，不碰 dsh 的 client-ui 插件**（避免 dsh UI API 变更带来的破坏）。这是整个方案里唯一要主动克制的耦合点。

---

## 七、仓库结构（各自维护，互不阻塞）

> 修正（v3.3）：`pounding-mcp` 是 skill 的薄封装、与 skill **强耦合**（CLI 参数一变 MCP 签名就变），
> **归入 ozon-worker 仓库**一起维护，不单独建仓。真正独立的是 `ozon-harness-shell`（组装层）和 `dsh`（上游）。

| 仓库 | 维护者 | 版本/CI | 依赖关系 |
|---|---|---|---|
| `ozon-worker`（现有）| 你们团队 | 独立 | 含 worker + skill + webui + **pounding-mcp**（skill 薄封装，同仓同步版本）|
| `ozon-harness-shell`（新建，薄壳）| 你们团队 | 独立 | pin dsh 版本 + 引用 pounding-mcp（依赖 skill CLI 稳定接口）|
| `dsh`（上游）| DeepSeek 团队 | 不维护 | 只消费，pin 版本 |

### 7.1 为什么「不 fork dsh、不并入 dsh」（回答最初顾虑）

- 若 fork dsh 或把 ozon 业务代码并入 dsh：每次 dsh 更新都要手动 merge、解决冲突、回归，成本随 dsh 迭代线性增长
- 插件化壳化后：ozon 能力通过 MCP（稳定协议）挂载，dsh 更新我们零感知
- **但 pounding-mcp 和 ozon-worker 是同一边的**：pounding-mcp 只是 skill 的薄壳，与 skill 强耦合，理应同仓、同步更新

### 7.2 目录结构（最终形态）

```
ozon-harness-shell/           # 薄壳仓库（只做组装，独立维护）
├── src-electron/                 # 壳底座（Electron）
├── runtime/                      # 内嵌 dsh（pin 版本）+ Node
├── patches/                      # cordis.patch.yml（挂载 pounding-mcp）
└── web/                          # iframe 加载 webui + dsh UI

ozon-worker/                  # 现有仓库（含 pounding-mcp，同仓同步更新）
├── worker/                       # FastAPI 云端后端
├── skill/                        # 本地 CLI（被 pounding-mcp 调用）
├── webui/                        # 经营面（iframe 嵌入壳）
└── pounding-mcp/                     # MCP 薄封装（skill CLI → 19 工具）
    ├── pounding_mcp/
    │   ├── server.py             # FastMCP 工厂
    │   └── skill_runner.py       # run_skill_command 薄封装
    ├── cordis.patch.yml          # 挂载 dsh 的 patch 配置
    ├── pyproject.toml
    └── tests/
```

---

## 八、与既有文档的对应关系

| 本文档章节 | 对应既有文档 |
|---|---|
| §三 skill/worker 分工 | `../ARCHITECTURE-TOPOLOGY.md`（业务拓扑）|
| §四 数据流 | 本文 §四（`../WORKER-TOPOLOGY.md` 为 v0.11 旧版，仅参考）|
| worker 端点 | `../../api-integration/API-INTEGRATION-GUIDE.md` |
| webui 设计 | `../../design-deliverables/ozon-erp-design-spec.html` |

> 旧版 `../ECOM-HARNESS-PLAN.md`（v2）保留作历史，本目录为 v3 权威版本。
