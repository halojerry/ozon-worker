# PLUGIN-ASSEMBLY — 插件装配清单 + 配合关系

> 本文档是「电商版 dsh 客户端」的插件装配清单：哪些插件、各司什么职、怎么互相配合成一个完整电商工作流。
> 原则：**我们的核心增量只造「电商业务」，其余通用能力全部从社区装配，零重复造轮。**

---

## 一、总览（三类来源）

| 类 | 内容 | 状态 |
|---|---|---|
| **我们的核心**（自研）| pounding-mcp（采集/选品/上架）+ pounding-guard（审批）+ vault（落盘）| ✅ 已做 |
| **dsh 原生** | 审批、ask-user（选择项）、web_fetch、bash、run_code | ✅ 已带 |
| **社区插件**（装配）| 浏览器/视觉/搜索/Excel/图表/记忆/品牌化/手机远程 | ⏳ 装即可 |

---

## 二、完整插件清单（按环节）

### A. 基础设施（先装）

| 插件 | 用途 | 安装 |
|---|---|---|
| `dsh-market` | 插件市场（后续一键装）| `dsh plugin --profile web add dshmarket` |
| **`DSH-better-sidebar`** | 侧栏工作台：文件/终端/Git/子代理 + **三方插件注册新 Tab** | `dsh plugin --profile web add dsh-better-sidebar` |

> ⚠ 关于 `ccq1/dsh-side-panel`：**已归档停维护**，官方推荐 DSH-better-sidebar。侧栏面板这个能力**很重要**（下面 §四 详说它的关键作用）。

### B. 电商核心（我们的，已做）

| 插件 | 用途 |
|---|---|
| `pounding-mcp` | skill 19 命令 → MCP 工具（`mcp__pounding__*`）|
| `pounding-guard` | 三级安全门控（read 直跑 / write 审批 / destructive）|
| `vault` | 落盘（配置自动 + 结果卡片 + 进度透明化）|

### C. 浏览器（电商采集辅助）

| 插件 | 用途 | 配合关系 |
|---|---|---|
| `dsh-builtin-browser` | 可见浏览器 + 人可接手 + CDP 驱动 | **通用浏览**；skill 自己的 CDP 是 **1688/Ozon 专用**，互补 |
| skill CDP（我们的）| 1688/Ozon 采集 | 核心，无人替代 |

### D. 视觉 / 搜索

| 插件 | 用途 |
|---|---|
| `modlens`（2.3k⭐）| 视觉桥（OCR/版面/语义）—— 图搜前看图 |
| `dsh-vision-router` | 看图问答/OCR/截图 |
| `modsearch` / `argo` | 联网搜索（argo 含**购物**搜索，选品趋势用）|

### E. 办公汇报（用户要的 xlsx/图表/选择项）

| 插件 | 用途 |
|---|---|
| `dsh-excel-chat` | 对话完成 Excel（建表/编辑/修公式/图表校验）|
| `dsh-genui` | 交互式 UI：**图表/表单/测验**/mermaid/3D → **可视化选择项** |
| `dsh-visualize` | 生成式 UI（交互式 HTML 卡片）|
| `dsh-openpencil` / `dsh-aigc-canvas` | 设计预览 / 画布（导图/绘图）|

### F. 记忆

| 插件 | 用途 |
|---|---|
| `dsh-mnemon` | 跨 Agent 持久记忆 + 语义召回 + 知识图谱 |
| `unified-agent-memory` | 多 Agent 共享 Obsidian vault（跟我们的 `vault/` 思路一致）|

### G. 品牌化

| 插件 | 用途 |
|---|---|
| `dsh-thought-buddy` | 思考时的动态小机器人头像（SVG 动画）→ **改成 Pounding 品牌形象** |

### H. 手机远程（出门用手机）—— 已定：Quick Tunnel 免账号，开箱即用

**决策**：用 Cloudflare **Quick Tunnel（免账号）**，把 `cloudflared` 单文件打包进安装包，开箱即用。

| 项 | 说明 |
|---|---|
| 方案 | Cloudflare Quick Tunnel（免账号，`cloudflared` 单文件）|
| 打包 | `cloudflared` 打进安装包 resources（~30-40MB）|
| 启动 | 客户端主进程自动 `spawn cloudflared tunnel --url http://127.0.0.1:3080` |
| 展示 | UI 自动显示二维码 + 6 位密码，手机扫码即用 |
| 用户操作 | **零操作**（无需装 Tailscale/配置 cf 账号）|

**约束（已接受）**：
- 随机 URL 每次启动变 → 用户每次重新扫码（可接受）
- 依赖 Cloudflare 网络 → 海外客户 OK，国内可能慢（后续可选自建 frp）
- 安全 = 随机 URL + 6 位密码（够用，非强认证）

> 参考实现：`DeepSeekHarnessRemoteGateway`（社区已有，自动生成 URL + 密码 + 二维码）。后续可选 Phase 2：用户填自己 cf 域名走命名 Tunnel 得固定地址。

---

## 三、配合关系（完整电商工作流）

```
用户（桌面 / 手机远程扫码）
    │
    ▼
dsh agent（总调度，按需「装配」插件）
    │
    ├─ 电商核心（我们）
    │   ├─ pounding-mcp：采集 1688 / 选品 / 上架
    │   ├─ pounding-guard：危险操作审批（老板眼皮底下）
    │   └─ vault：结果落盘（配置/商品卡片/进度）
    │
    ├─ 通用能力（社区）
    │   ├─ dsh-builtin-browser：通用浏览（人可接手）+ skill CDP 专用采集
    │   ├─ modlens：看图（图搜前理解图片）
    │   ├─ argo/modsearch：选品趋势搜索
    │   ├─ dsh-excel-chat：选品结果导出 Excel 汇报
    │   ├─ dsh-genui：图表 + 可视化选择项（"这几个产品选哪个"）
    │   └─ dsh-mnemon：跨会话记忆（选品偏好）
    │
    └─ 品牌化 + UI
        ├─ dsh-thought-buddy：Pounding 品牌思考指示
        └─ DSH-better-sidebar：侧栏（文件/终端 + 电商面板 Tab）
```

**示例完整对话流**：

```
用户："帮我找蓝海产品，导出 Excel 给我选"
  → agent 调 pounding-mcp discover（选品）
  → 调 argo 搜趋势（补充）
  → 结果落盘 vault + 用 dsh-excel-chat 生成 Excel
  → 用 dsh-genui 生成可视化选择卡片
  → 用户点选 → agent 调 graph 上架（pounding-guard 审批）
```

---

## 四、关键决策

### 1. 侧栏面板（DSH-better-sidebar）—— 8 板块的容器

`DSH-better-sidebar` 支持**三方插件注册新 Tab**（`registerTab`）。这是客户端「侧边栏 8 板块」的容器：

```
DSH-better-sidebar 侧栏
├── Agent（dsh 原生对话）
├── 文件/终端/Git（better-sidebar 内置）
├── 【采集箱】（我们 registerTab 注册）
├── 【任务中心】（我们 registerTab 注册）
├── 【专家】（我们 registerTab 注册）
├── 【知识库】（我们 registerTab 注册）
├── 【爆品新闻】（我们 registerTab 注册）
├── 【计算器】（我们 registerTab 注册，脚本直算）
└── 【用量】（dsh token-meter / 社区 balance）
```

> 8 板块的具体内容 + 能力归属（worker/skill/脚本/agent）见 `PRD.md` §2.2/2.3。

这样「对话面（Agent）+ 7 个业务板块」在一个窗口里，双向驱动（对话 + 手动）都有了。

### 2. 手机远程 —— Quick Tunnel 免账号（已定，开箱即用）

`cloudflared` 单文件打包进安装包 → 主进程自动拉起 → UI 显示二维码 + 6 位密码 → 手机扫码浏览器访问。用户零操作，开箱即用。

---

## 五、待办

- [ ] 装 DSH-better-sidebar，验证「三方注册 Tab」机制（电商面板嵌入的关键）
- [ ] 参考 dsh-thought-buddy，改成 Pounding 品牌形象
- [ ] 打包 cloudflared（Quick Tunnel 免账号）进安装包 + 主进程自动拉起 + 二维码（P5）
- [ ] 装 modlens / dsh-genui / dsh-excel-chat 做端到端配合验证

对应文档：`PRD.md`（产品需求）· `ARCHITECTURE.md`（架构）· `REFERENCES.md`（参考源）
