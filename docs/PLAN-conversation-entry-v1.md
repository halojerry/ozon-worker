# 专家 tab 对话入口方案 v1

> 日期: 2026-08-21
> 范围: pounding-sidebar 专家 tab → 本地 skill 能力入口（Q3 答复）
> 依据: 探索结论（pounding-sidebar / pounding-mcp / pounding-harness / skill / worker 交叉核实）+ 本仓库代码审计
> 状态: 方案文档，只写不实现

---

## 1. 问题定义

### 1.1 用户诉求

用户决策（Q3 答复）：**对话入口优先**。目标是让用户在不进 dsh Agent 对话、不写 CLI 命令的前提下，在专家 tab 里用自然语言触发 skill 能力（采集、图搜、选品、上架、类目、店铺配置）。

### 1.2 现状盘点（已核实事实）

| 维度 | 位置 | 现状 |
|---|---|---|
| 专家 tab | `pounding-sidebar/src/client/index.tsx:48-57`（注册）+ `ExpertsPanel` L335-368 | **纯静态骨架**：6 张工具卡片无 `onClick` 无 `fetch`，底部文字「触发：本地 pounding-mcp HTTP 网关（8901）」 |
| registerTab 契约 | `pounding-sidebar/README.md`「验证结论」 | 三步：`import type` → `export inject=['betterSidebar']` → `ctx.effect` 包 `registerTab`（HMR/禁用自动撤销，否则报 already registered） |
| 能力边界 | dsh-better-sidebar 是 **client half 服务**（仅浏览器侧） | 浏览器侧无法直接 spawn 本地进程，触发 skill 必须经 HTTP 网关 |
| 对话驱动已存在 | `pounding-mcp/pounding_mcp/server.py`（19 工具）+ `http_server.py:8901` | **对话入口已存在于 pounding-mcp**：stdio 给 dsh agent 对话，HTTP 8901（streamable-http）给 GUI 手动驱动；`skill_runner.py` 子进程黑盒调 skill CLI |
| 同源网关 | pounding-harness 8766（`docs/ui-structure/app.js:212-214`「RPC APIs live on the local gateway (8766)」） | 页面与网关同源，可由网关转发到 8901 |
| skill CLI | `skill/scripts/cli.py:1926-2140` | 19 个子命令（set_store/list_stores/set_token/set_ak/check/search/category/probe/graph/image_search/get_ak/follow/discover/discover-multi/update/query/seller/queries/cleanup），**无 ask 命令** |
| 意图路由 | `skill/SKILL.md:24-39` + `references/command-reference.md:24-53` | A/B/C/D/E/F 决策树，**是文档不是代码**（由 Agent LLM 消费） |
| worker chat | `worker/src/main.py:917` → `runtime/openai_handler.py` | `/v1/chat/completions` 是 **stub**（`handle()` 抛 `NotImplementedError`），无业务对话 |

**结论：对话驱动所需的地基（本地 MCP 网关、skill CLI、意图决策树文档）全部已存在，缺的只是「把自然语言目标路由到具体 skill 命令」的那一层。**

---

## 2. 现状细节

### 2.1 专家 tab 现状

- 注册点 `index.tsx:48-57`：`ctx.effect(() => betterSidebar.registerTab({ id: 'pounding:experts', ... component: ExpertsPanel }))`，已包 effect，契约合规。
- 面板 `ExpertsPanel` L335-368：`tools` 数组 6 张卡片（采集/图搜/选品/上架/类目/店铺），`button` 只写了 `style`，**无 `onClick`**；底部卡片提示「也可以在对话里让 Agent 直接调用」+ 页脚「触发：本地 pounding-mcp HTTP 网关（8901）」。
- 结论：设计意图已经写明「点卡片 → 走 8901」，只是接线没做。

### 2.2 pounding-mcp 已具备的对话驱动能力

- `server.py`：FastMCP 工厂，19 个工具（`@mcp.tool()`，L23-176），薄封装：参数映射 CLI flag → `skill_runner.run_skill_command` → subprocess → 解析 JSON。
- `http_server.py:16-24`：`mcp.run_http_async(transport="streamable-http", host="127.0.0.1", port=8901)`，README 明说「HTTP 模式入口：让 webui（手动 GUI）能通过 HTTP 调 skill」。
- `skill_runner.py`：`run_skill_command` 构造 `[SKILL_PYTHON, cli.py, cmd, args...]`，`cwd=SKILL_DIR`，调用前 POST `127.0.0.1:9224/show` 唤醒浏览器宿主、结束后 `/done`（L54-73）。输出解析兼容「进度文本 + 尾部 JSON」混合格式（L132-158）。
- 安全分级（README）：server 本身不做审批，三级门控（read/write/destructive）由 dsh 侧 `tools/pre-execute` 钩子实现（`docs/ozonharness/MCP-TOOLS.md` §七 SAFETY_MAP）。
- 结论：**8901 已是功能完整的对话能力出口**，只是协议是 MCP（JSON-RPC over streamable-http），且没有「自然语言 → 工具选择」的路由层。

### 2.3 skill CLI 与意图路由

- `cli.py:1926-2140` 19 个子命令，均为独立可执行能力，输出 JSON（自动脱敏）。
- 意图路由：`SKILL.md:24-39`（速记三条：有 URL 先判类型 A/B/C/F；无 URL 按意图词趋势→E/跟卖→C/上架→D/蓝海→C；指代不清必须追问）+ `command-reference.md:24-53` 完整决策树。**这套逻辑从未被代码化**，只存在于文档里给 Agent LLM 消费。

### 2.4 worker chat stub

- `main.py:917-939`：鉴权后调 `openai_handler.handle(payload, ctx)`。
- `runtime/openai_handler.py:8-15`：`chat()` 直接 `raise NotImplementedError`，纯 API 兼容占位。
- 结论：worker 不是业务对话后端，本方案**不依赖 worker chat 端点**（worker 的 LLM 调用走 `utils/mxou_api.py`，见 worker/AGENTS.md）。

---

## 3. 方案选项

### 方案 A：专家 tab 升级为「引导式入口」（推荐二期）

用户输入目标 → 前端调本地网关 → 路由层按 SKILL.md 决策树选管线 → 调 skill CLI → 结果回显。

```
专家 tab 输入框（自然语言目标）
  → 调本地网关
  → 路由层：按 SKILL.md 决策树选管线（LLM 判定 + 追问兜底）
  → 调 skill CLI（skill_runner 黑盒，19 工具复用）
  → 返回结构化结果 → 前端回显（任务 id / 链接 / 利润预估 / 审核状态）
```

网关形态二选一：

| 形态 | 说明 | 优点 | 缺点 |
|---|---|---|---|
| **A1 直调 8901** | 前端直接 POST MCP JSON-RPC（`tools/call` + `tools/list`，streamable-http） | 复用现有 19 工具，零新端点 | 跨源需 FastMCP 配 CORS；SSE/会话对前端复杂；MCP 无内置鉴权（依赖 127.0.0.1 绑定） |
| **A2 8766 同源代理 + REST 薄壳** | 网关加一个同源 REST 端点 `/ask`（页面与网关同源无 CORS），内部转发或直接调 skill | 同源无 CORS；可把路由层做在服务端；前端只 `fetch` 普通 JSON | 需改 pounding-harness（或独立本地小服务），多一个端点 |

推荐 **A2 为主、A1 为备**：同源代理免掉 CORS 和 SSE 复杂度，路由层（LLM 决策）放服务端，前端退化为纯表单 + 结果渲染。

路由层设计（关键，决定方案成败）：
- 输入：自由中文目标；输出 schema：`{ pipeline: A|B|C|D|E|F|unknown, command, args, needs_clarification, questions[] }`。
- 判定：把 `SKILL.md §1` 决策树 + 追问纪律固化为 LLM system prompt（模型复用 deepseek-v4-flash，调用方可用用户 token 走 mxou，与系统其余 LLM 一致）；也可以先做规则表（URL 类型/意图词 → 管线）再让 LLM 只处理歧义，对应 SKILL.md ③「指代不清必须追问」。
- `unknown` / 歧义命中：返回 `questions[]` 追问，**绝不猜测直接执行**（对齐 SKILL.md 纪律）。
- 写类命令（graph 提交、discover --auto-submit、cleanup）：回显后仍需用户显式确认，复用现有「提交前必须用户确认」边界。

### 方案 B：skill ask 命令（替代）

新增 `ask` 子命令：关键词 → 管线映射表（`{url类型/意图词 → pipeline}`）+ LLM 消歧（歧义时追问）。专家 tab 只 shell 一个命令。

- 优点：决策树固化为代码，任何调用方（agent / GUI / webui）都能复用；CLI 能力平铺。
- 缺点：skill 是 Cython 编译分发（`cli.py` 属 COPY_FILES 明文，但新增逻辑要随版本走编译清单 + `test_compile_lists.py` + 4 平台验证）；pounding-mcp 已存在且 skill_runner 就是黑盒，重复造轮子；追问交互天然适合对话/HTTP 环境，不适合纯 CLI 一次性调用。

### 方案 C：dsh 原生 Agent tab（推荐先行，零新代码）

- dsh 侧边栏 **Agent（对话）为 dsh 原生**，pounding-mcp 已挂载（stdio，`cordis.patch.yml`），dsh 侧已有 read/write/destructive 三级审批钩子（SAFETY_MAP）。
- 用户今天就能在 Agent tab 输入「帮我把这个 1688 链接采集下来」，dsh 的 Agent LLM 看到 19 个 MCP 工具 + SKILL.md 决策树即完成整个对话闭环。
- 专家 tab 只需加一个引导按钮：`openTab`（better-sidebar 已有 `openTab/activateTab` 能力）跳去 Agent tab，或复制一段提示词。**后端零改动。**

---

## 4. 推荐组合

**C 先行（零成本，立即可用）+ A 二期（专家 tab 对话 UI）**。

| 阶段 | 内容 | 成本 |
|---|---|---|
| Phase 0 | C：专家 tab 加「去 Agent 对话」引导按钮（openTab + 预填提示词） | 半天，纯前端 |
| Phase 1 | A 的地基：pounding-mcp 加 `ask` 工具（或 8766 同源 `/ask` REST 薄壳），决策树固化为 LLM prompt + 输出 schema | 1-2 天 |
| Phase 2 | A 的 UI：专家 tab 输入框 → 结果回显（任务 id/链接/进度），接 ask | 1-2 天 |

理由：
1. C 在 30 分钟内可用，覆盖「对话入口优先」的用户诉求本体。
2. A 的价值在「不切进 Agent tab、点一下就出结果」的手动 GUI 场景，值得二期做，但它的路由层（方案关键）需要打磨，放一期一起做会拖慢落地。
3. B 是 A 的路由层的另一种摆放位置，选了 A2 就不需要 B（路由层已服务端化，ask 语义等价）。

---

## 5. 方案 A 详细设计（二期）

### 5.1 交互流

```
输入框（自然语言）
  → GET/POST {host:8766}/ask   （A2，同源）
      → 路由层：LLM 按决策树 → {pipeline, command, args}
      → skill_runner 执行（唤醒浏览器宿主 → subprocess → JSON）
      → 返回 {ok, result, needs_confirmation}
  → 前端渲染：结果卡片 / 追问问题列表 / 「确认执行」按钮（写类命令）
```

### 5.2 路由层 prompt 要点（直接引用现有文档，防漂移）

- 决策树正文取 `SKILL.md:24-39` 速记 + `command-reference.md:24-53` 全文，嵌入 prompt 时不重写语义。
- 追问纪律原文进 prompt：「有 URL 先判类型」「指代不清/数量不符/重上 → 必须追问核对，禁止猜测」「趋势选品先 web_search + LLM 提炼」「提交前必须用户确认」。
- 输出约束：只输出 `{pipeline, command, args, needs_clarification, questions[]}` JSON，`unknown` 必须带 `questions[]`。

### 5.3 鉴权与安全

- 8901 保持 `127.0.0.1` 绑定，不暴露公网；若走 A1 直调，FastMCP `run_http_async` 需显式配 `allow_origins`（仅本机页面源）。
- 写类命令（graph 提交/cleanup/update/auto-submit）经 `/ask` 返回 `needs_confirmation=true`，前端二次确认后才真正执行。
- 与 dsh 的 SAFETY_MAP 审批不冲突：GUI 手动路径是用户本人点击，Agent 路径才走 dsh 审批。

### 5.4 长时命令

- discover 采集可达分钟级：`skill_runner` 是同步 subprocess，HTTP 层需流式或轮询（先返回 `accepted` + 后台跑，前端轮询结果），对齐现有任务查询口径（worker task_id 或 skill query）。

---

## 6. 实施步骤

| 步骤 | 内容 | 验收 |
|---|---|---|
| P0-1 | 专家 tab 加引导按钮：`openTab` 到 dsh Agent tab + 复制「帮我把这个 1688 链接采集下来」提示词 | 点击后落到 Agent tab，能正常对话 |
| P1-1 | pounding-mcp 新增 `ask` 工具（或 8766 `/ask` REST 薄壳）：决策树 prompt + schema 输出 + 追问兜底 | `ask` 命中 A/B/C 管线正确；歧义输入返回 questions |
| P1-2 | `ask` 后端复用 `run_skill_command` 执行并返回结构化结果；写类命令标记 `needs_confirmation` | 长时命令返回 accepted + 可轮询 |
| P2-1 | 专家 tab 输入框 + 结果回显组件（接 ask） | 输入目标 → 结果卡片/追问列表/确认按钮全链路可用 |
| P2-2 | 回退开关：专家 tab 对话 UI 异常时提示「去 Agent 对话」，不进死路 | 8901 未启动时降级提示而非白屏 |

---

## 7. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| **LLM 路由可靠性** | 误选管线 → 跑错流程/白烧采集 | `unknown` + 追问兜底（SKILL.md ③ 纪律）；路由结果先展示，写类命令需用户确认；规则表优先、LLM 只消歧 |
| **8901 CORS / 鉴权** | 跨源被拒；MCP 无内置鉴权被滥用 | A2 同源代理为主免 CORS；8901 保持 127.0.0.1 绑定；A1 直调需显式 allow_origins |
| **与 dsh Agent tab 功能重叠** | 用户困惑走哪个 | 明确定位差异：Agent tab = 全 Agent 对话（有审批），专家 tab = 定向工具入口（手动 GUI 场景） |
| skill CLI 长时命令/子进程 | HTTP 请求挂死 / 浏览器唤醒失败 | `accepted + 轮询` 模式；`_wake_browser` 失败静默（skill_runner 已处理） |
| skill 编译分发 | 若走方案 B 新增 ask 子命令，需编译清单/4 平台验证 | 本方案走 A（pounding-mcp 侧，不碰 skill 编译产物），B 仅作备选 |
| worker chat stub 被误用 | 有人以为 worker 是对话后端 | 方案不依赖 worker chat；文档标注 stub 现状 |

---

## 8. 验收标准

1. Phase 0：专家 tab 引导按钮可跳 Agent tab 且能完成一次真实对话（如「跟卖一个 Ozon 链接」）。
2. Phase 1：`ask` 对典型输入（1688 链接→A、Ozon 链接→B、图片 URL→D1、关键词→C）返回正确 pipeline；对「选品」等歧义词返回追问问题而非直接执行。
3. Phase 2：专家 tab 输入自然语言 → 结果回显（任务 id/商品链接/利润预估），写类命令带二次确认。
4. 8901 未启动 / skill 目录缺失时前端降级提示，不白屏不崩溃。
5. 无新增对 worker 云端 chat 端点的依赖。
