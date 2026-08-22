# MCP-TOOLS — pounding-mcp 工具设计稿

> 本文档是 `pounding-mcp` 包（唯一新增的业务层）的设计稿，可直接作为 P1 编码依据。
> 参考对象：PCDCK/pounding-mcp 的 FastMCP + 三级安全门控 + 知识层范式（已核实其源码结构）。

---

## 一、设计原则

1. **黑盒命令即资产**：skill 的 19 个命令已是「参数 → JSON 结果」的黑盒，MCP 只做薄封装，业务逻辑留在 skill 单点维护
2. **参数 1:1 映射 CLI flag**：不发明新参数，Agent 看到的参数 = CLI 的 argparse 参数
3. **三级安全门控**：把 skill 的「决策边界」翻译成 read/write/destructive 三级
4. **透明 vs 黑盒**：危险操作「老板眼皮底下」（需审批），低风险只读「默默干」

---

## 二、工具命名约定

- MCP serverName：`ozon`
- 工具在 dsh 中可见为：`mcp__pounding__<toolName>`（由 `dsh-mcp-client` 自动加前缀）
- toolName 与 skill 命令名一致，方便对照维护

---

## 三、19 命令 → 19 工具映射表

> 安全分级：`read`（黑盒直跑）/ `write`（需确认）/ `destructive`（双重确认）

| # | 工具名 | skill 命令 | 描述（Agent 可见） | 关键参数（映射 CLI） | 安全 |
|---|---|---|---|---|---|
| 1 | `check` | check | 诊断前置条件（Chrome/凭证/Worker/Ozon API 是否就绪）| — | read |
| 2 | `list_stores` | list_stores | 列出所有已配置的 Ozon 店铺 | — | read |
| 3 | `set_store` | set_store | 配置 Ozon 店铺凭证 | `name, client_id, api_key, currency` | write |
| 4 | `set_token` | set_token | 设置 MXOU 平台 token | `token` | write |
| 5 | `set_ak` | set_ak | 手动设置 1688 Access Key | `ak` | write |
| 6 | `get_ak` | get_ak | 浏览器自动获取 1688 AK | `timeout` | write |
| 7 | `search` | search | 搜索 1688 商品 | `query, page_size, sort, rules, store, auto_submit` | read → write* |
| 8 | `probe` | probe | CDP 抓取 1688 商品详情页 | `url, timeout` | read |
| 9 | `image_search` | image_search | 以图搜款（上传图找 1688 同款）| `image, limit, sort, source` | read |
| 10 | `category` | category | 查询 Ozon 类目（关键词→候选类目）| `query, lang, max` | read |
| 11 | `follow` | follow | 跟卖 Ozon 商品（竞品→找同款→上架）| `ozon_url, store, to_box, auto_submit, review` | read → write* |
| 12 | `discover` | discover | Ozon 选品 v2（采集→分析→挑货）| `url, keyword, max_products, min_margin, store, fission, auto_submit...` | read → write* |
| 13 | `discover_multi` | discover-multi | 多关键词批量选品 | `keywords, max_each, min_margin...` | read → write* |
| 14 | `seller` | seller | 卖家店铺全产品运营分析 | `seller_id, max_products, max_skus` | read |
| 15 | `queries` | queries | what-to-sell 榜单查询 | `type, keyword, sku, category_id, price_min, price_max` | read |
| 16 | `graph` | graph | 组装并提交上架（1688→GraphInput→Worker）| `item_id, url, category_query, store, to_box, no_submit, template_id` | write* |
| 17 | `query` | query | 查询 Worker 任务状态 | `task_id, watch, timeout` | read |
| 18 | `update` | update | 检查并应用 skill 自动更新 | — | write |
| 19 | `cleanup` | cleanup | 清理缓存/临时数据 | — | destructive |

> `*` 表示该命令的安全级别随 flag 动态变化：默认「采集/组装」为 `read`（黑盒直跑），提交类 flag（`auto_submit`/`to_box`）触发 `write`（需审批）。`graph` 例外：默认即为提交（`write`），`no_submit=True` 降为只读组装（见 §四）。

---

## 四、三级安全门控设计

### 4.1 映射规则（透明 vs 黑盒）

skill 里很多命令有「采集（只读）」和「提交（写）」两种模式，靠 flag 切换（`--auto-submit` / `--to-box` / `--no-submit`）。设计如下：

| 场景 | 门控 | 效果 |
|---|---|---|
| 只读采集/查询（search/probe/discover 不提交）| `read` | 黑盒直跑，Agent 无需确认 |
| 提交上架/改凭证/改配置 | `write` | **老板眼皮底下**：执行前弹出确认，展示「将提交到哪店/什么商品」|
| 清理/删除（清理缓存、删临时数据）| `destructive` | **双重确认**：write 确认 + 显式二次确认 |

### 4.2 具体落地：把「提交类 flag」变成审批触发点

MCP 工具层不把提交 flag 当成普通布尔参数，而是与 skill CLI 的 `--no-submit` / `--to-box` / `--auto-submit` 对齐：

```
工具签名示例（graph）：
  ozon_graph(url, store, category_query, template_id, ...)   # 默认直接提交 worker 上架 → 触发审批
  ozon_graph(..., to_box=True)                               # 入采集箱（轻确认）
  ozon_graph(..., no_submit=True)                            # 只组装信封，无副作用（黑盒直跑）
```

- `no_submit=True`：只组装 GraphInput，不提交 → `read`（黑盒直跑）
- `to_box=True`：写入采集箱（`/api/v1/drafts`）→ `write` 轻确认
- 默认：直接提交 worker 上架 → **审批门控**（`ctx.approval.request`）

### 4.3 审批机制（dsh 原生，官方源码实证 rc.7）

**关键架构事实**：`ctx.approval` 是 dsh 的 Cordis 上下文，**MCP server 是独立进程，拿不到它**。所以三级安全门控**不能实现在 pounding-mcp 内部**，必须由 **dsh 侧的一个 `tools/pre-execute` 拦截点插件**（普通 Cordis 插件）来做。

**官方契约**（`dsh` 源码 `.agents/notes/implemented/feature/2026-06-30-interception-extension-points.md` + `2026-07-06-approval-seam.md`）：
- native hook = 普通 Cordis 插件订阅 canonical 事件，**不是**独立包
- 工具管线：`pre-execute → guards → execute → dispatch → post-execute → result`
- `tools/pre-execute` 是 waterfall gate，返回 **`PreToolDecision`（allow / deny / ask）**
- **`ask` 决策由 harness 的 approval seam 解析**（不是钩子自己调 `ctx.approval.request`）：`ask` → `ctx.approval` 的 answerer waterfall → `allowed-once` / `rejected` / `cancelled` / `unavailable`
- **answerer 已存在**：由 dsh-web-app 自带（`cordis.patch.yml:105-106` 以 `api-gateway` id 挂 `@deepseek-ai/dsh-host-apiproxy`），**并非缺失**。guard（`pre-execute`）与 `serviceAsk` 共享同一 `exec.callId`（dsh-tools `lib/index.js:3105-3106`），**无相关性失配**；answerer 逆向扫描（dsh-host-apiproxy `lib/index.js:1911-1913`）实测可匹配（ApprovalService.request 的 debug9 日志）。注意：**answerer 非平台职责的一部分时**才回到 `unavailable` → 自动拒绝（fail-closed）。
- **discover 默认是 read**：无 `--auto_submit`/`--to_box` 时不触发审批（不 ask）；只有 `--auto_submit`/`--to_box` 才升级 write 才 ask。若 discover 未落盘 `tasks.json`，更可能是「discover 无 auto_submit 根本没 ask」或「无 UI resolver」，**而非 answerer 缺失**。

```typescript
// 伪代码：dsh 侧的 pre-execute 拦截点插件（普通 Cordis 插件，非 MCP server）
export function apply(ctx) {
  ctx.on('tools/pre-execute', async (exec, next) => {
    const toolName = exec.name;                        // 例如 mcp__pounding__graph
    if (!toolName.startsWith('mcp__pounding__')) return next();
    const safety = resolveSafety(toolName, exec.arguments); // read / write / destructive
    if (safety === 'read') return next();              // 黑盒直跑
    // write / destructive：返回 ask 决策，harness 走 approval seam 审批
    return { ask: { reason: summarize(exec.arguments) } };
    // summarize 生成人读摘要："提交上架到『3号店』：1688商品 X"
    // 注意：ApprovalRequest 不携带工具参数，参数摘要须由我们塞进 reason 字符串
  });
}
```

**挂载审批 seam**（`cordis.yml` 或 patch 层）：

```yaml
- id: approval
  name: '@deepseek-ai/dsh-user-approval'
  # config:
  #   policy: never   # 无人值守时设为 never，所有 ask 自动拒绝
```

> 「双重确认」说明：`PreToolDecision` 只有 allow/deny/ask 三态，**没有「两次 ask」的语义**。destructive（如 `cleanup`）退化为「一次 ask（reason 标注破坏性）+ 工具自身确认参数」。真正的双重确认可在 answerer 层或工具内实现，P2 再细化。
>
> 伪代码按 `dsh` rc.7 契约；实现时以 `docs/development.md` + `.agents/notes/implemented/feature/*interception*` + `*approval-seam*` 为准。

---

## 五、知识层设计（领域知识沉淀）

参考 PCDCK/pounding-mcp 的 `knowledge/`（YAML 维护），我们把 skill 已有的领域知识沉淀成 Agent 可查的知识：

| 知识类型 | 内容 | 来源 |
|---|---|---|
| **workflows**（工作流）| 「1688 链接上架」「跟卖竞品」「蓝海选品」多步流程 | skill 的 SKILL.md 决策边界 |
| **quirks**（坑）| Ozon 类目限制、1688 反爬、图片白底要求 | skill 的 `field_mapping.md`、`references/` |
| **rate_limits**（限流）| Ozon API 配额、1688 限速 | skill 的 `cloud_probe.py` 已处理的限流逻辑 |
| **safety_overrides**（安全覆盖）| 哪些方法/参数需降级或加确认 | 本文 §四 |

这些知识以 YAML 维护，加载进 MCP server 后：
- Agent 可通过工具描述/系统提示词获取
- 后续可扩展「知识检索」工具（`ozon_search_knowledge`）让 Agent 主动查坑

---

## 六、FastMCP 代码骨架（pounding-mcp 只做薄封装，不做审批）

> 审批/安全门控在 dsh 侧 pre-execute 钩子（见 §4.3），pounding-mcp 是独立进程、不含 `ctx.approval`。因此工具函数只负责「参数映射 CLI + 返回 JSON」，不内嵌审批逻辑。

```python
# pounding_mcp/server.py
from fastmcp import FastMCP

mcp = FastMCP("pounding")

@mcp.tool()
async def search(query: str, page_size: int = 5, sort: str = "") -> dict:
    """搜索 1688 商品（只读，黑盒直跑）。"""
    return run_skill_command("search", query=query, page_size=page_size, sort=sort)

@mcp.tool()
async def graph(url: str = "", store: str = "", no_submit: bool = False,
                to_box: bool = False, category_query: str = "") -> dict:
    """组装并提交上架。默认直接提交（dsh 侧 pre-execute 会拦审批）；
    no_submit=True 只组装；to_box=True 入采集箱。"""
    return run_skill_command("graph", url=url, store=store,
                             no_submit=no_submit, to_box=to_box,
                             category_query=category_query)

def run_skill_command(cmd: str, **kwargs) -> dict:
    """统一调用 skill CLI，返回 JSON 结果（黑盒命令即资产）。"""
    # subprocess 调 python -m skill.scripts.cli <cmd> ...
    ...
```

### 挂载到 dsh（cordis.patch.yml，patch 层用 `insert` 包裹）

```yaml
- insert:
    - id: mcp-pounding
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: pounding
        transport: stdio
        command: python3
        args: ['-m', 'pounding_mcp']
```

> 注意两种写法：`cordis.yml`（bundle 层）直接 `- id:`；`cordis.patch.yml`（patch 层）用 `- insert:` 包裹。本文档统一用 patch 层写法（与官方 `examples/mcp-memory` 一致）。

---

## 七、安全分级清单（放 dsh 侧 pre-execute 钩子）

> 归属：SAFETY_MAP 放 **dsh 侧插件**（审批属于 dsh 能力，不泄漏到 MCP server）。MCP server 只通过工具描述/元数据标注「可写/可删」信号，dsh 钩子据此决定是否审批。

```typescript
// dsh 侧插件（例如 @dsh-external/dsh-pounding-guard 的 safety.ts）
export const SAFETY_MAP: Record<string, 'read' | 'write' | 'destructive'> = {
    // read —— 黑盒直跑（提交类 flag 触发时动态升级为 write，见 resolveSafety）
    "check": "read",
    "list_stores": "read",
    "search": "read",            // --auto-submit 时 → write
    "probe": "read",
    "image_search": "read",
    "category": "read",
    "follow": "read",            // --auto-submit / --to-box 时 → write
    "discover": "read",          // --auto-submit / --to-box 时 → write
    "discover_multi": "read",    // --auto-submit / --to-box 时 → write
    "seller": "read",
    "queries": "read",
    "query": "read",
    // write —— 老板眼皮底下（需审批）
    "set_store": "write",
    "set_token": "write",
    "set_ak": "write",
    "get_ak": "write",
    "graph": "write",            // 默认提交；--no-submit 时降为 read
    "update": "write",
    // destructive —— 双重确认
    "cleanup": "destructive",
};
```

> 运行时动态升级：`auto_submit`/`to_box`/`submit` 为 True 时，`read` → `write`（见 §4.3 `resolveSafety`，dsh 侧）。
