# ROADMAP — 分阶段落地计划 + 待决策点

> 配合 `ARCHITECTURE.md` 和 `MCP-TOOLS.md` 使用。本文定义「先做什么、后做什么、依赖什么、要拍什么板」。

---

## 一、总工期估算

**约 9–14 天（1 人全职）**，其中 skill/worker/webui 均为零改动。

| Phase | 内容 | 依赖 | 时长 | 产出 |
|---|---|---|---|---|
| **P0** | 壳底座 + dsh 运行时跑通 | 无 | 1–2 天 | 能启动 dsh 的 Electron 壳 |
| **P1** | `pounding-mcp`（19 命令 → 工具 + 三级安全）| P0 | 2–3 天 | 可被 dsh 调用的 MCP server |
| **P2** | 审批策略层（透明/黑盒）| P1 | 1–2 天 | 危险操作需确认 |
| **P3** | 本地网关 + webui iframe 整合 | P0 | 2 天 | 经营面嵌入壳 |
| **P4** | 知识层 vault + 电商侧栏 UI | P0–P3 | 2–3 天 | 领域知识 + 品牌化界面 |
| **P5** | 社区插件补充 + 跨平台打包 | P4 | 1–2 天 | Win/Mac 安装包 |

---

## 二、各阶段详解

### P0 — 壳底座 + dsh 运行时（1–2 天）

**目标**：能启动一个桌面 App，里面跑起来 dsh。

- 搭 Electron 壳（或 fork hairyf 的 Tauri 版改）
- 内嵌 dsh 运行时（`dsh --profile web --port 3080`）
- 壳拉起 dsh 进程 + iframe 加载 dsh UI

**产出**：`ozon-harness-shell` 仓库骨架，能启动 dsh。

### P1 — pounding-mcp（2–3 天）⭐ 核心

**目标**：19 个 skill 命令变成 dsh Agent 能调的工具。

- 按 `MCP-TOOLS.md` 建 `pounding-mcp` 目录（归入 ozon-worker 仓库，FastMCP + 19 工具）
- 参数 1:1 映射 CLI flag，统一 `run_skill_command()` 薄封装
- 往 `cordis.patch.yml` 加 `mcp-pounding` 配置，挂到 dsh
- 回归测试：每个工具能调通 skill CLI
- 可选：worker 读查询工具（orders/products/tasks 只读 → MCP，MCP 层注入 Bearer，见 ARCHITECTURE §2.5）

**产出**：`pounding-mcp`（ozon-worker 内），dsh 里可见 `mcp__pounding__*` 工具。

### P2 — 审批策略层（1–2 天）

**目标**：实现「老板眼皮底下 vs 默默干」。

- 按 `MCP-TOOLS.md` §四实现三级安全门控（`read`/`write`/`destructive`）
- 用 dsh 原生 `ctx.approval` + `tools/pre-execute` 挂点
- 把 skill 的「决策边界」翻译成审批策略

**产出**：危险操作需确认，只读操作黑盒直跑。

### P3 — 本地网关 + webui iframe（2 天）

**目标**：经营面（webui）嵌进壳，与对话面共存。

- webui 通过 iframe 嵌入壳的侧栏/标签页
- 本地网关统一会话/审批/状态（参考 DAY1-Clean 的 `boujoy_server.py`）+ **代理 worker 注入 Bearer 鉴权**（补 web_fetch 带不了 header 的缺口，见 ARCHITECTURE §2.5）
- 确认数据流：webui 直连 worker `/api/v1`（webui 自有 axios Bearer 拦截器，可直接调）

**产出**：对话面 + 经营面共存。

### P4 — 知识层 vault + 电商侧栏（2–3 天）

**目标**：让「对话即完成」有记忆、有品牌。

- 建 vault 知识库（商品/店铺/供应商/选品偏好）
- 用 design-deliverables 的设计语言做电商侧栏 UI
- 知识层（workflows/quirks/rate_limits/safety）落进 pounding-mcp

**产出**：品牌化壳 + 领域知识。

### P5 — 社区插件 + 跨平台打包（1–2 天）

**目标**：补通用能力 + 发安装包。

- 装 1–2 个社区插件（dsh-browser 通用浏览、ego-browser 实时观察窗、modlens 视觉）
- Electron 打包 Win/Mac 安装包

**产出**：可分发安装包。

---

## 三、决策点（已锁定 ✅ 2026-08-18）

| # | 决策 | 结果 | 说明 |
|---|---|---|---|
| 1 | **壳底座** | **Electron** | Node 生态管 dsh/skill 最自然；零 Rust 成本；内置 Chromium 保 webui 渲染一致 |
| 2 | **webui 整合** | **iframe 共存**（Phase 1）| 零耦合，dsh 更新零影响；client-ui 待 dsh 稳定后可选 |
| 3 | **worker 部署** | **云端**（现状）| 团队共享数据，已有 Docker 部署 |
| 4 | **社区插件** | **暂不装**（先跑通主链路）| P5 阶段再按需引入 dsh-better-sidebar / ego-browser / modlens |
| 5 | **P1 开工** | **已开工** | pounding-mcp 骨架见项目根 `pounding-mcp/` |

---

## 四、风险与边界（诚实清单）

| 风险 | 等级 | 应对 |
|---|---|---|
| dsh 是 developer preview，API 会变 | 高 | 只通过 MCP + patch 层 + iframe 接触，不碰 client-ui；pin 版本 |
| skill CLI 参数与 MCP schema 漂移 | 中 | MCP 参数 1:1 映射 CLI，写回归测试；skill 单点维护 |
| 浏览器/CDP 依赖本地 Chrome | 中 | skill 已含 chrome_launcher；桌面 App 内嵌或检测系统 Chrome |
| worker 云端 vs 本地网络延迟 | 低 | 已有 Docker 部署 + 重试逻辑 |
| 审批策略与 skill 决策边界不一致 | 中 | P2 阶段用 skill 的 SKILL.md 决策边界做对照翻译 |

---

## 五、下一步（建议动作）

1. **评审本文档四件套**（README / ARCHITECTURE / MCP-TOOLS / ROADMAP）
2. **拍板 §三 的 5 个决策点**
3. **P1 开工**：建 `pounding-mcp` 目录（归入 ozon-worker），先实现 2–3 个工具（`check` / `search` / `query`）验证链路，再批量补齐 19 个

> 关联文档：`../../api-integration/`（worker 对接）、`../../design-deliverables/`（webui 设计）、`../../integration-workplan/`（现有工作清单）
