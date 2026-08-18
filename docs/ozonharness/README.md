# Ozon Harness — 电商版 DeepSeek Harness 架构方案

> 把 ozon-worker 的 AI 自动化运营能力，做成一个「对话即完成」的电商版 Harness 桌面产品。
> 文档集最后更新：2026-08-18 · 基于 dsh `0.1.0-rc.6` 本地运行时**实证**分析（非文档猜测）。

---

## 这份文档集解决什么问题

把「我们的 skill + worker + webui 如何适配 deepseek-harness」从讨论变成一份可评审、可落地、可交给同事的完整方案。核心结论都经过了源码级核实：

- dsh 的插件挂载机制（`cordis.patch.yml` + `mcp-client` 桥）——**读的 dsh 本地运行时真实文件**
- skill 与 worker 的真实分工（`cloud_probe.py` 里的 REST 调用、`scripts/lib/` 分层）——**读的 ozon-worker 真实代码**
- 参考对象（DAY1-Clean / hairyf / PCDCK/pounding-mcp）的适配范式——**读的真实源码**

---

## 文档地图

| 文档 | 内容 | 何时读 |
|---|---|---|
| [`README.md`](./README.md)（本页） | 结论速览 + 一图流 | 先读这个 |
| [`PRD.md`](./PRD.md) | **产品需求文档**：定位/双向驱动/功能需求/验收标准 | 实施前必读 |
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | 完整架构：分层、数据流、插件化壳化、仓库结构、UI 整合 | 评审方案时 |
| [`MCP-TOOLS.md`](./MCP-TOOLS.md) | 19 命令 → MCP 工具 + 三级安全分类 + 知识层 | 动手写 MCP 层时 |
| [`SKILL-INVENTORY.md`](./SKILL-INVENTORY.md) | skill 功能清单 + vault 落盘方案 + 黑盒透明化 | 落盘/透明化时 |
| [`ROADMAP.md`](./ROADMAP.md) | 分阶段落地计划 + 待决策点 | 排期时 |
| [`REFERENCES.md`](./REFERENCES.md) | 参考源清单（dsh 生态 / DAY1-Clean / shopbang）| 查权威资料时 |
| [`EXAMPLES.md`](./EXAMPLES.md) | 官方 examples 整理 + 对照 pounding-mcp | 同事对接/跑通范例时 |

---

## 一句话结论

**不改 skill、不改 worker、不改 webui，只在 ozon-worker 仓库里新增一个薄薄的 `pounding-mcp` 包，通过 dsh 官方的 patch 层挂载，做成「插件化壳化」的形态。**

---

## 三个核心认知（整个方案的地基）

### 1. 三种协议，三个完全不同的「调用方」

| 协议/接口 | 是谁和谁之间 | 给谁用 | 对应资产 |
|---|---|---|---|
| **MCP** | Agent ↔ 工具 | 让 dsh Agent 调用「进程内能力」 | skill 的 19 个 CLI 命令 |
| **REST (FastAPI)** | 调用方 ↔ worker | 前端 / skill / 经 MCP 或网关的 Agent | worker 的 100+ 端点 |
| **SDK** | 开发者/代码 ↔ worker | 人写代码、前端、代码沙箱 | `generated.d.ts`（已是成品） |

**关键**：MCP 和 REST 是**正交的**，不是二选一。worker 永远是 FastAPI，不需要改造成 MCP；skill 的进程内能力才需要 MCP 工具化。

> ⚠ 鉴权约束（源码实证）：dsh 原生 `web_fetch` 只收 `url`、**带不了 Authorization header**；但 `bash` 工具可跑 `curl -H "Authorization: Bearer xxx"`（沙箱只限文件写、不限网络）。Agent 访问 worker 的推荐路径：**上架主链路走 skill 间接**（`graph` 内部已调 worker）、**读查询走 MCP 工具或本地网关**（注入 Bearer）；bash+curl 只作脆弱兜底。详见 `ARCHITECTURE.md` §2.5。

### 2. 分工由「物理约束」决定，不是拍脑袋

> 能力需要什么运行环境，就归属哪一层。

- 需要**本地浏览器（CDP）** → 只能 skill（操作本机 Chrome）
- 需要**持久化 / 后台长任务 / 并发队列** → 只能 worker（后端）

详见 `ARCHITECTURE.md` §三。

### 3. 「插件化壳化」—— 不 fork、不并入，只挂载

dsh 官方给每个 profile 留了补丁层（`cordis.patch.yml`），我们往里面加一段 MCP 配置即可，**dsh 本体一行不动**。我们的能力封装成 `pounding-mcp` 包（归入 ozon-worker 仓库，与 skill 强耦合同仓同步），dsh 更新时我们零感知（MCP 是稳定协议）。

详见 `ARCHITECTURE.md` §六。

---

## 一图流（完整拓扑）

```
┌─ 本地（桌面 App，Electron 壳）────────────────────────────────────┐
│                                                                  │
│  ┌─ dsh Agent（对话即操作，上游引擎，pin 版本）──────────────┐     │
│  │   工具槽：mcp__pounding__*  +  web_fetch  +  bash/run_code   │     │
│  └───────────────┬──────────────────────────────────────────┘     │
│                  │ MCP 协议（稳定标准）                             │
│  ┌───────────────▼──────────────┐                                │
│  │  pounding-mcp 包（ozon-worker 内新增）│ 19 命令 → 19 工具 + 三级安全门控 │
│  └───────────────┬──────────────┘                                │
│                  │ 调 skill CLI（稳定接口）                         │
│  ┌───────────────▼──────────────┐                                │
│  │  skill（现有，零改动）        │  CDP 浏览器 / 1688采集 / 选品引擎 │
│  └───────────────┬──────────────┘                                │
│                  │ REST /api/v1（skill 内部自动调 worker 提交上架）   │
│  ┌───────────────▼──────────────┐                                │
│  │  webui（现有，零改动）        │  经营数据面板，iframe 嵌入壳       │
│  └──────────────────────────────┘                                │
└──────────────────────────────┬───────────────────────────────────┘
                               │ REST /api/v1（Bearer 鉴权）
┌──────────────────────────────▼───────────────────────────────────┐
│  云端 worker（现有，零改动，Docker + Postgres + Supabase）          │
│  上架执行 / 商品 / 订单 / 任务 / 模板 / 凭证 / 图片生成 / 管理后台   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 与已有交付包的关系

本方案是**新增的第四个交付包**，与前三个互补：

```
ozon-worker/
├── docs/ozonharness/        ← 本方案（电商版 Harness 架构）
├── design-deliverables/      设计资产（规范 HTML + tokens + 原型图）
├── api-integration/          API 对接材料（文档 + openapi + TS 类型）
└── integration-workplan/     工作清单（PRD/PLAN/TASKS/TEST/ISSUES/TODO）
```

> 旧版 `docs/ECOM-HARNESS-PLAN.md`（v2）已由本目录的 `ARCHITECTURE.md`（v3.3）取代；v3 新增了「三种协议边界」「插件化壳化」「仓库结构」三个 v2 缺失的关键结论。

---

## 待拍板的决策（详见 ROADMAP.md）

1. **壳底座**：Electron（推荐，Node 生态 + 零 Rust 成本）vs Tauri（hairyf 现成、包体小）
2. **webui 整合**：iframe 共存（推荐，零耦合）vs dsh client-ui 插件（深耦合）
3. **worker 部署**：云端（团队共享）vs 本地自托管（单人私有）
4. **社区插件**：择 1–2（`dsh-better-sidebar` / `dshmarket`）vs 暂不装
5. **P1 开工**：`pounding-mcp` 的 19 命令 → 工具设计稿（见 MCP-TOOLS.md）
