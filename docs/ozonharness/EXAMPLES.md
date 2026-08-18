# EXAMPLES — 官方 examples 整理（对照 pounding-mcp 方案）

> 官方仓库 `deepseek-ai/deepseek-harness/examples/` 的 6 个范例逐一整理，并标注与我们的 pounding-mcp 方案的对照关系。
> 目的：同事对接时，照着官方范例就能跑通，不用自己摸索。

---

## 零、官方 examples 目录总览

```
examples/
├── acp-agent/           ACP 自动化 server（程序化客户端）
├── headless-agent/      无交互 agent（单任务，机器可读输出）
├── jsonrpc-agent/       Python SDK + JSON-RPC 驱动
├── mcp-memory/          ⭐ MCP 接入范例（= 我们的 pounding-mcp 方案）
├── web-cordis/          自我引用的 agent（检查/修改 Cordis 插件树）
└── web-schedule/        可选的 Web overlay（会话级定时提醒）
```

**与我们最相关的是 `mcp-memory/`**——它就是「把外部能力通过 MCP 接进 dsh」的官方标准做法，和我们 pounding-mcp 的设计完全一致。

---

## 一、⭐ mcp-memory（MCP 接入标准范例）

### 1.1 它示范了什么

通过 `@deepseek-ai/dsh-mcp-client` 桥，连接三个第三方 memory server（Memorix / MCP Reference Memory / Engram），把它们的工具暴露成 `mcp__<serverName>__<tool>`。

**Dsh 负责的部分**（官方原文）：
- 解析 Cordis overlay，启动 stdio 命令或连接 Streamable HTTP URL
- 发现 MCP 工具，暴露成 `mcp__<serverName>__<tool>`
- stdio 模式：随 dsh 插件生命周期启动/停止子进程；HTTP 模式：上游服务须先运行
- stdio 桥会**移除凭据类环境变量和所有 `DSH_*` 变量**再启动子进程

### 1.2 启用方式（三种，选一）

```bash
# 方式 1：临时 overlay（一次运行）
dsh web --patch "$PWD/examples/mcp-memory/memorix.cordis.yml"

# 方式 2：合并到 profile patch 层（持久）
# 编辑 $DSH_HOME/profiles/<name>/cordis.patch.yml 或 $DSH_HOME/cordis.patch.yml

# 方式 3：用另一份配置（换成 mcp-reference-memory 或 engram）
dsh web --patch "$PWD/examples/mcp-memory/mcp-reference-memory.cordis.yml"
```

### 1.3 MCP 行的标准 YAML（官方模板）

```yaml
- insert:
    - id: mcp-pounding                     # 唯一 id
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: pounding               # 工具命名空间 → mcp__pounding__<tool>
        transport: stdio               # 或 streamable-http
        command: python3               # 我们的命令
        args: ['-m', 'pounding_mcp']       # 我们的参数
        env: {}                        # 额外环境变量
        cwd: !!js process.cwd()        # 工作目录
```

远程 server 则用：
```yaml
        transport: streamable-http
        url: http://localhost:3000/mcp
        headers:
          Authorization: !!js '`Bearer ${process.env.MCP_TOKEN}`'
```

### 1.4 验证步骤（官方）

1. 启动 dsh（带 `--patch`）
2. **等待 `mcp__...` 工具出现**（初始发现是异步的）
3. 再发送第一个验证 prompt（如「帮我记住 X」「查一下 Y」）

### 1.5 对照我们的 pounding-mcp

| 官方 mcp-memory | 我们的 pounding-mcp |
|---|---|
| `command: memorix` | `command: python3` + `args: ['-m','pounding_mcp']` |
| memory 工具（entity/relation/read/search）| 19 个 skill 命令（graph/search/discover/...）|
| `serverName: my-memory` | `serverName: pounding` |
| 工具 `mcp__my-memory__search` | 工具 `mcp__pounding__graph` 等 |
| stdio transport | stdio transport（同为本地子进程）|

**结论：我们 pounding-mcp 的做法 = 官方 mcp-memory 范例的原样复用，零偏差。**

---

## 二、其他范例（按需参考）

### 2.1 web-schedule（写 dsh 原生插件的范例）

示范如何写一个 **dsh 原生插件**（非 MCP）：会话级定时提醒，注册 `schedule_create`/`schedule_list`/`schedule_delete` 三个工具。

```bash
dsh web --patch examples/web-schedule/cordis.yml
```

**对我们的意义**：如果未来要把「审批策略钩子」或「电商侧栏」做成 dsh 原生插件（而非 MCP），这是最简范例。

### 2.2 headless-agent（无交互运行）

无交互 agent，接受一个任务、运行、输出机器可读结果。

**对我们的意义**：如果未来要做「命令行批量上架」「定时任务」，用 headless 模式 + 我们的 MCP 工具。

### 2.3 jsonrpc-agent（Python SDK 驱动）

通过 Python SDK + JSON-RPC 驱动一个无人值守的 coding agent。

**对我们的意义**：如果未来 skill（Python）要反向驱动 dsh 做子任务编排，这是参考。

### 2.4 web-cordis（运行时检查插件树）

自我引用的 agent，能检查/修改自己内存里的 Cordis 插件树。

**对我们的意义**：调试时用，检查我们的 MCP 插件是否正确挂载。

### 2.5 acp-agent（ACP 自动化 server）

Agent Client Protocol 自动化 server，供程序化客户端（带会话/权限/取消支持）。

**对我们的意义**：如果未来要接第三方客户端或做自动化审批，这是参考。

---

## 三、给同事的对接路径（照着做）

### 3.1 快速验证 MCP 接入（10 分钟）

```bash
# 1. 克隆官方仓库（拿 examples）
git clone https://github.com/deepseek-ai/deepseek-harness.git
cd deepseek-harness

# 2. 看官方 MCP 范例的配置长什么样
cat examples/mcp-memory/memorix.cordis.yml

# 3. 跑通官方范例（先验证 dsh 能正常连 MCP）
dsh web --patch "$PWD/examples/mcp-memory/memorix.cordis.yml"
# 等 mcp__memorix__* 工具出现，发个 prompt 验证
```

### 3.2 换成我们的 pounding-mcp（P1 开工后）

```bash
# 把官方范例的 command 换成我们的 skill 入口
dsh web --patch "$PWD/pounding-mcp/ozon.cordis.yml"
# ozon.cordis.yml 内容 = 本文 §1.3 的模板，command 指向 python3 -m pounding_mcp
```

### 3.3 写 dsh 原生插件（P2 审批钩子，如需要）

1. 研读 `docs/development.md`
2. 把 `packages/` 里的内置包当参考
3. 参考 `examples/web-schedule/` 的最小插件结构
4. 开发 → 打 `dsh-plugin` topic → `dsh plugin add` 安装

---

## 四、与本文档集的关系

| 文档 | 关系 |
|---|---|
| `REFERENCES.md` | 参考源全景（官方/社区/市场）|
| `EXAMPLES.md`（本页）| 官方 examples 逐一整理 |
| `ARCHITECTURE.md` | 我们的架构方案（examples 是它的官方印证）|
| `MCP-TOOLS.md` | 19 命令 → 工具设计（对应 mcp-memory 的 MCP 封装）|
