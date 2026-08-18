# REFERENCES — 参考源清单（dsh 生态调研）

> 本文档是电商版 Harness 方案的调研附录，供同事对接时查阅「该去哪里找权威资料」。
> 调研时间：2026-08-18。dsh 是 v0.1 开发者预览版（2026-08-13 发布），**预计有 breaking changes，所有结论需 pin 版本**。

---

## 一、官方权威源（首选，一切以这里为准）

| 资源 | 地址 | 用途 |
|---|---|---|
| **官方仓库** | `github.com/deepseek-ai/deepseek-harness`（本地 clone：`~/Documents/dsh/`，当前 rc.7）| 源码、文档、examples、贡献历史 |
| **官方 examples** | `deepseek-harness/examples/` | 6 个可运行范例（详见 EXAMPLES.md）|
| **开发指南** | `deepseek-harness/docs/development.md` | 插件契约的**唯一权威**，写插件前必读 |
| **官方 packages** | `deepseek-harness/packages/` | 几十个内置包 = 按同一套约定写的真实插件，最好的参考实现 |
| **拦截扩展点（审批钩子权威）** | `.agents/notes/implemented/feature/2026-06-30-interception-extension-points.md` | `tools/pre-execute` 返回 `PreToolDecision(allow/deny/ask)` 的权威契约 |
| **审批 seam（审批机制权威）** | `.agents/notes/implemented/feature/2026-07-06-approval-seam.md` | `ctx.approval` / `dsh-user-approval` / answerer waterfall |
| **工具管线子系统** | `docs/subsystems/tools.md` | 工具执行管线全貌 |
| **官方产品页** | `deepseek.com/harness/en/` | 概述、快速开始 |
| **官方文档站** | `deepseek-harness.github.io/deepseek-harness/` | 用户指南、架构文档 |
| **Cordis 论文** | `github.com/cordiverse/paper` | 底层元框架原理 |

**关键**：`packages/` 里的 `core`、`llm`、`mcp`、`sandbox`、`context`、`plan`、`goal` 等包，就是我们要写的插件（或 MCP 封装）的现成范例——**没有特权内核，内置包和我们的包权力相同**。

---

## 二、MCP 接入（我们 pounding-mcp 方案的标准做法）

官方 `examples/mcp-memory/` 就是 MCP 接入的标准范例（详见 EXAMPLES.md）。

核心机制：
- 通过 `@deepseek-ai/dsh-mcp-client` 桥把外部 MCP server 的工具注册到 `ctx.tools`
- 工具命名：`mcp__<serverName>__<tool>`
- 两种 transport：`stdio`（dsh 管子进程生命周期）、`streamable-http`（服务需先运行）
- 官方模板：`dsh web --patch "$PWD/examples/mcp-memory/*.cordis.yml"`

---

## 三、插件生态（社区）

### 3.1 插件安装命令

```bash
dsh plugin --profile web add github:owner/repo      # GitHub 仓库
dsh plugin --profile web add dshmarket              # npm 包名
dsh plugin --profile web add ./path/to/plugin       # 本地开发
dsh --profile web --dump-config                     # 验证是否进入配置树
```

> 注意：`dsh plugin add` 底层转发给 pnpm，适用于** npm 形式的 dsh 插件**。我们的 `pounding-mcp` 是 Python 包，走 MCP 接入（patch 配置），不适用 `dsh plugin add`。

### 3.2 插件目录 / 市场（浏览找现成能力）

| 目录/市场 | 地址 | 规模 |
|---|---|---|
| 官方插件目录 | `deepseek-code.com/plugins` | — |
| DeepseekPlugin 目录 | `deepseekplugin.org` | 2894 个 |
| DSH Plugins 目录 | `deepseekharnessplugins.com` | 5926 个 |
| 可视化插件市场（npm 包）| `dshmarket` | 300+（数据源 awesome-dsh-plugin）|

### 3.3 精选列表（awesome）

| 列表 | 地址 | 规模 |
|---|---|---|
| `0xsline/awesome-deepseek-harness` | 官方推荐 | — |
| `beancookie/awesome-dsh-plugin` | 在线浏览+搜索+分类 | 270 个（11 分类）|
| `cccakeee/awesome-dsh-plugins` | 同步自 GitHub | 1776 curated |
| `Alex-Yanggg/awesome-DSH-plugin` | 中英双语 | — |

> GitHub topic：`dsh-plugin` 是官方可发现性约定——打这个标签的仓库会被以上列表索引。

### 3.4 我们方案可能用到的具体插件

| 插件 | 用途 | 关联 |
|---|---|---|
| `dsh-better-sidebar` | VSCode 风格侧栏 + **内嵌浏览器** + 终端 + Git 面板 + 服务化接口供其他插件注册标签页 | webui 嵌入的参考 |
| `dsh-theme-plugin` | 主题切换 | 电商品牌化 UI 参考 |
| `dshmarket` | 可视化插件市场 | 同事装插件用 |

---

## 四、插件开发指南（社区整理，便于快速上手）

| 资源 | 地址 | 说明 |
|---|---|---|
| 官方开发指南 | `github.com/deepseek-ai/deepseek-harness/blob/master/docs/development.md` | 权威契约 |
| 实战手册（书+PDF）| `github.com/lgiang517/deepseek-harness-plugin-guide` | 基于真实代码库的实战手册 |
| 社区开发指南 | `deepseekharness.io/zh/plugin-development/` | 四步流程（克隆→研读→开发→发布）|
| 社区学习站 | `deepseekharnessplugins.com/learn` | 4 节：run dsh / assembly / safety / ecosystem |

### 4.1 开发流程（官方四步）

1. 克隆 monorepo + `pnpm install` + `pnpm run build`，`pnpm dsh web` 起 Web UI
2. 研读 `docs/development.md` + `packages/`（把内置包当参考书）
3. 对着本地构建开发（插件运行时组合，加载/卸载无需重建）
4. 发布到 GitHub + 打 `dsh-plugin` topic

### 4.2 发布后的可发现性

给仓库打 `dsh-plugin` topic → 被 awesome 列表索引 → 用户用 `dsh plugin --profile web add github:owner/repo` 安装。

---

## 五、关键版本事实（对接时注意）

| 项 | 值 |
|---|---|
| 发布日 | 2026-08-13（v0.1 developer preview）|
| 官方仓库最新版本 | `0.1.0-rc.7`（本地 clone `~/Documents/dsh/`，2026-08-18）|
| 本机 DAY1-Clean 运行时 | `0.1.0-rc.6` |
| 运行时要求 | Node.js ^22.19.0 或 >=24.0.0；pnpm 11.7.0 |
| 许可证 | MIT |
| 四种运行模式 | 标准 / PTC（程序化工具调用）/ 极简 / 创造 |
| 稳定性 | **developer preview，官方明确警告 breaking changes** |

> **版本提醒**：rc.5 → rc.6 → rc.7 迭代很快（官方 8 月 13 日发布，几天内已到 rc.7）。我们的 pounding-mcp 通过 **MCP 协议**接入，MCP 是稳定标准，dsh 版本升级基本无感；但 dsh 侧的审批钩子插件（P2）若用到 `tools/pre-execute` / `ctx.approval` 契约，需 pin 版本并在升级时复核这些契约是否变动。

---

## 六、参考对象：DAY1-Clean（boujoy-harness）完整方案

> 源码：`github.com/2153796804qq-lab/boujoy-harness`（本机本地拷贝 `~/Documents/DAY1-Clean`，但本地拷贝只有 macOS，GitHub 上是 Win+Mac 完整版）。

### 6.1 跨平台方案（关键参考）

| 平台 | 宿主 | 载体 |
|---|---|---|
| macOS | 原生 WKWebView 壳（Swift `BoujoyHarness.swift`）| 同一套 Web UI |
| Windows | **无原生壳**——本地 PowerShell 服务（`Start-Boujoy.ps1`）+ Edge app 模式 | 同一套 Web UI |

**核心结论**：Windows 用「系统浏览器 + 本地服务宿主」，**不打包 Electron/Tauri**。同一套 Web UI 两端通用，只换宿主进程。

### 6.2 本地网关（P3 直接参考）

`web/boujoy_server.py` 用 **Python 标准库**实现（`http.server` + 手动 WebSocket 转发），无 FastAPI/Flask：
- WebSocket 转发：产品页在 `8766`，dsh 流在 `3080/3081`，网关终止浏览器升级、转发帧（透明 WS 桥）
- 双 dsh 模式：`knowledge:3080` / `clean-home:3081`

### 6.3 对我们的影响

之前锁定 **Electron 壳**，DAY1-Clean 证明还有更轻的选项：

| 方案 | 优点 | 缺点 |
|---|---|---|
| Electron（之前推荐）| 统一安装体验、托盘、自动更新、自启 | 包体大（~100MB+）|
| DAY1-Clean 式：原生 WebKit(macOS) + 浏览器宿主(Win) | 极轻（脚本级）、零 Electron 维护 | 无托盘/自启等桌面特性，体验偏「网页化」|

> 待决策：壳底座是否需要从 Electron 调整为「原生 WebKit + 浏览器宿主」。这不是推翻，是新增选项；需结合我们场景（dsh 对话 UI + webui 电商面板共存）评估。

---

## 七、参考对象：shopbang（上品帮）Electron 采集客户端

> 源码：`/Volumes/os/dev/ozon-worker/shopbang`（从 `resources/app.asar` v1.0.22 解包归档，归档目的即「dsh 插件化参考底稿」）。

### 7.1 是什么

Ozon 跨境电商「选品/采集/上架」自动化助手，**Electron + Vue3 + Pinia + Ant Design Vue**，后端 `plus.shopbang.cn`（独立）。

**业务闭环**：Ozon 采集 → 22 项过滤 → 1688 以图搜款 → 利润核算 → Excel 导出 → 批量上架。

### 7.2 可借鉴（能力层）

| 维度 | 内容 |
|---|---|
| 架构 5 层 | 渲染层(Vue3) / IPC层 / 服务层(TaskManager+6采集服务) / 执行层(BrowserWindow) / 数据层(API+Excel+store) |
| 反爬策略 | 环境伪装(真实窗口+指纹)、行为仿真(缓动滚动+随机延迟)、请求藏匿(页面fetch+后端中转)、容错(单条跳过+超时+并发3~5限流) |
| 任务调度 | TaskManager 单例 + 状态机 + 并发策略 |
| 上架引擎 | 分批(100条)、汇率换算、价格倍率、pako压缩 |

### 7.3 要避免（8 个已知问题）

| shopbang 问题 | 我们的规避 |
|---|---|
| DOM 选择器硬编码哈希类名 → 改版失效 | skill 的 CDP 同样有风险，用真实 Chrome profile + 更稳选择器 |
| 无验证码处理 | skill **已有**人工干预（"请在浏览器中手动滑动验证"）|
| 无代理 IP 池 | 需评估（skill 可能也缺）|
| session 串 cookie | skill 用真实 Chrome profile，天然隔离 |
| 无测试代码 | 我们已有 test_smoke |
| 死代码随包发布 | pounding-mcp 薄封装，不重复 |

### 7.4 核心洞察

shopbang 证明「Electron 客户端 + 采集 + 上架」产品形态**真实且成熟**。我们的增量是「**用 dsh 对话替代手动 GUI 操作**」——能力层借鉴 shopbang，交互层从「手动点按钮」改为「对话驱动」。

---

## 八、与既有交付包的关系

本目录（`docs/ozonharness/`）是第 4 个交付包，含：
- `README.md` — 入口 + 结论速览
- `ARCHITECTURE.md` — 完整架构（v3.3）
- `MCP-TOOLS.md` — 19 命令 → 工具设计
- `ROADMAP.md` — 分阶段计划
- `REFERENCES.md`（本页）— 参考源清单
- `EXAMPLES.md` — 官方 examples 整理
