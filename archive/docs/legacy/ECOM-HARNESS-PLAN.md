# 电商版 DeepSeek Harness 适配方案 v2

> 深度调研 + 修正方案 · 2026-08-18 · 基于 dsh 0.1.0-rc.6 本地运行时实证分析
> **v2 修正**：worker 是 REST API（Agent 用 `web_fetch` 直调），**不是 MCP**；MCP 只用于 skill 进程内能力。拆分「依赖服务 vs 逻辑服务」。
> 关联：`../api-integration/` · `../design-deliverables/` · `../integration-workplan/` · `../skill/`

---

## 一、结论摘要（TL;DR）

| 决策点 | 结论 |
|---|---|
| 产品形态 | 桌面 App（Win + Mac），Electron 壳 |
| **worker 接入** | **REST 直调**（dsh 原生 `web_fetch` 工具）——不需要 MCP 包装 ⭐ v2 修正 |
| **skill 接入** | **MCP server 包装**（19 命令 → 工具）——skill 是进程内浏览器/CDP，必须工具化 |
| 审批（透明/黑盒）| dsh 原生 `user-approval` + `approval/request` |
| 浏览器 | 保留自研 CDP（skill）+ 可选 dsh-browser / ego-browser 补通用浏览 |
| webui | iframe（先）+ dsh client-UI 插件（后） |

---

## 二、核心认知修正：REST vs MCP 的边界 ⭐

这是 v2 最重要的修正。之前的方案把 worker 也包成 MCP 是**概念错误**。

### 2.1 两种接入方式，适用不同对象

| 对象 | 本质 | Agent 怎么用 | 需要 MCP 吗 |
|---|---|---|---|
| **worker（FastAPI）** | 已经是 **REST API**（109 端点，`/api/v1`）| dsh 原生 `web_fetch` 工具直接调 `http://127.0.0.1:8080/api/v1/...` | **不需要** ❌ |
| **skill（Python CLI）** | **进程内能力**：Chrome CDP、1688/Ozon 采集、图搜、选品引擎 | 必须工具化（MCP/工具注册）才能被 Agent 调用 | **需要** ✅ |

### 2.2 为什么 worker 不需要 MCP

- 实测 dsh 自带 **`dsh-tool-web` → `web_fetch` 工具**：Agent 可直接发 HTTP 请求
- worker 已有完整 `openapi.json`（`../api-integration/openapi.json`）+ 对接文档——Agent 读文档 + `web_fetch` 就能调，**零包装**
- 包一层 worker-mcp 反而多一次进程/协议转换，无收益

### 2.3 Agent 调 worker 的方式（推荐）

```
方案①（推荐）：dsh 的 web_fetch 工具直接调 worker REST
  Agent → web_fetch GET http://127.0.0.1:8080/api/v1/orders  (带 Authorization)
  优点：零代码；缺点：Agent 需知道 URL/鉴权（用系统提示词给）

方案②：轻量 API 网关（可选）
  dsh 的 api/remotes + Typert 把 worker 端点注册成 dsh 的 RPC 方法
  优点：类型安全；缺点：dsh dev preview 的 api/remotes 在变，先不做
```

**结论：v2 方案里 worker 走方案①**（web_fetch 直调 REST），把 `/api/v1` 的 URL 与 Bearer 鉴权写进 dsh 系统提示词或 skill 指令。

---

## 三、服务分层：依赖服务 vs 逻辑服务 ⭐

实证拆解 skill 的 lib（这是怎么拆的核心）：

### 3.1 skill 的「逻辑服务」（skill 自己实现的，本地进程内）

| lib 模块 | 职责 | 本质 |
|---|---|---|
| `cdp_client` + `chrome_launcher` + `stealth` | **Chrome CDP 连接 + 启动 + 反检测** | 浏览器基础设施 |
| `browser_probe/service.py`（2667 行）| 1688 商品页 CDP 深度抓取 | 采集逻辑 |
| `ozon_scraper` / `ozon_widget` / `ozon_seller` | Ozon 页面/Widget/Seller 数据抓取 | 采集逻辑 |
| `ozon_discovery` / `ozon_fission` | 选品引擎（Discover v2/v3 裂变）| 业务逻辑 |
| `ozon_image_search` | 1688 以图搜款（CDP + aibuy mtop 双通道）| 采集逻辑 |
| `ozon_seller_analytics` | 卖家运营指标采集（跨 Tab 借道）| 采集逻辑 |
| `image_preprocessor` / `reference_images` | 图片处理 | 工具逻辑 |
| `config_store` / `cache` / `review_log` / `logging_utils` | 配置/缓存/审计日志 | 支撑服务 |

### 3.2 skill 的「依赖服务」（skill 调用外部）

| 依赖 | 用途 | 连接方式 |
|---|---|---|
| **worker**（`http://127.0.0.1:8080`）| 任务提交/查询/草稿/选品上报 | REST（`ozon_api.py` 部分 + `submit_task`/`task_status`/`analytics/*`）|
| **Ozon 官方 API** | 类目/商品/价格/属性（`ozon_api.py`）| REST（client_id + api_key）|
| **1688 API**（`ak_1688_client`）| 搜索/商品详情 | REST（1688 AK）|
| **Chrome**（CDP 9222）| 登录态页面采集 | WebSocket |
| **mxou** | token/账号（skill 90 处引用）| REST |

### 3.3 结论：怎么拆

```
┌────────────────────────────────────────────────────────┐
│  dsh Agent（对话即完成）                                 │
├────────────────────────────────────────────────────────┤
│  A. worker = REST 能力（web_fetch 直调，无需 MCP）        │
│     任务编排 / 持久化 / 订单 / 商品 / 模板 / 凭证          │
│     → http://127.0.0.1:8080/api/v1/*  (Bearer token)    │
├────────────────────────────────────────────────────────┤
│  B. skill = MCP 工具（进程内能力，必须工具化）            │
│     浏览器采集 / 图搜 / 选品引擎 / 跟卖 / 上架管线          │
│     → ozon-skill-mcp：19 命令 → MCP tools                │
│     内部再调 worker REST（提交上架等）                    │
├────────────────────────────────────────────────────────┤
│  C. 社区插件（补通用能力）                               │
│     dsh-browser（通用浏览）/ ego-browser（可见浏览器）     │
│     / modlens（视觉）                                   │
└────────────────────────────────────────────────────────┘
```

**一句话**：worker 是「编排与持久化服务」（REST），skill 是「采集与执行逻辑」（进程内工具），社区插件是「通用补充」。三者通过 dsh 的工具槽汇入 Agent 对话。

---

## 四、客户端骨架：Electron（实证 + 修正）

### 4.1 实证结论（与前版一致，补充关键点）

| 维度 | Electron | Tauri | 关键证据 |
|---|---|---|---|
| 内置 Chromium | ✅ | ❌ 系统 WebView | **我们 webui 的 Tailwind/现代 CSS 跨平台渲染一致** |
| Node 集成 | ✅ 天然 | Rust 桥接 | dsh 是 Node，Electron 管子进程最自然 |
| 浏览器/CDP | Chromium 环境最稳 | WebView 不一致 | skill CDP 与 dsh-browser 扩展在 Chromium 下最稳 |
| 团队技能 | JS 零门槛 | 需 Rust | 你们是 React/TS |
| 包体 | 大（可接受）| 小 | 本机工具，非关键 |

### 4.2 折中建议（v2 新增）

- **壳与核心解耦**：Electron 主进程只做「拉起 dsh 子进程 + 拉起 skill 网关 + 管理生命周期」，核心逻辑全在 dsh/网关——**未来若嫌重可换 Tauri，核心零重写**
- **浏览器不打包进 Electron**：skill 的 Chrome 是独立进程（9222），dsh-browser 是用户自己的 Chrome 扩展——Electron 只是 UI 宿主，不是浏览器运行时

---

## 五、社区插件审计（v2 更新）

### 5.1 推荐

| 插件 | 补什么 | 决策 |
|---|---|---|
| **dsh-browser**（lum1104，247★）| Agent 操控你正开的 Chrome（text-first）| ⭐ 装，补通用网页操作 |
| **ego-browser**（fisfzy，22★）| 32 工具 + **实时观察窗**（看得见可接手）| ⭐ 重点考察，老板眼皮底下的浏览器版 |
| **modlens**（liustack，715★）| 图像→JSON（OCR/布局）| ⭐ 补图搜前图像理解 |
| **dsh-browser-automation**（acosmi）| 隔离浏览器 + 每写操作审批 | 参考其安全/审批模型 |
| Apify 1688 Scraper（MCP）| 托管采集 | 备用通道 |

### 5.2 不采用

- 纯 TUI 类；无维护信号的插件

---

## 六、skill 解构映射（19 命令 → MCP 工具，v2 保持）

| 命令 | 工具 | 类别 | 审批 |
|---|---|---|---|
| check / set_store / set_token / set_ak / list_stores / migrate_profile | ozon_check 等 | 配置 | 🔓 |
| graph / follow / batch_test | ozon_graph 等 | 上架 | 🔒 审批 |
| discover（--auto-submit）| ozon_discover | 选品+上架 | 🔒 提交时审批 |
| search / discover（采集）/ image_search / seller / queries / category | 对应工具 | 采集选品 | 🔓 黑盒 |
| query（任务状态）| ozon_task_status | 任务 | 🔓 |
| cleanup / update | ozon_cleanup 等 | 维护 | 🔒 审批 |

> 审批挂点：skill MCP 工具内部，写操作前调用 dsh `approval/request`；或 MCP server 侧做确认回调。

---

## 七、目标架构（v2 修正：worker 走 REST）

```
┌─ 产品壳（Electron）────────────────────────────────────────────┐
│  Web UI（React + Vite）                                        │
│    ├─ iframe → dsh Web UI（3080）会话/工具/审批                  │
│    ├─ 电商侧栏（我们的 webui 设计稿）                           │
│    └─ 审批面板（老板眼皮底下视图）                              │
├─ Electron 主进程 ──────────────────────────────────────────────┤
│    拉起 dsh 子进程（node dsh web --port 3080）                  │
│    拉起 skill 网关 / 生命周期 / 托盘 / 更新                      │
├─ dsh 运行时（内置 Node + dsh 包，web profile）──────────────────┤
│    └─ 插件树: dsh-base + dsh-web-app + 我们的 MCP 插件          │
├─ 能力层 ────────────────────────────────────────────────────────┤
│    A. worker（REST，web_fetch 直调）                            │
│       http://127.0.0.1:8080/api/v1/*  ← Agent 直接 fetch       │
│    B. ozon-skill-mcp（19 命令→MCP 工具，含审批）                │
│       skill/scripts/cli.py ← 工具调用，内部再调 worker REST     │
│    C. 社区插件（dsh-browser / ego-browser / modlens）           │
└─ vault/（电商知识库）                                          │
```

---

## 八、分阶段落地（v2 修正）

| Phase | 内容 | 依赖 | 时长 |
|---|---|---|---|
| P0 | Electron 壳 + 内置 dsh + iframe 嵌 UI | 无 | 2 天 |
| P1 | **ozon-skill-mcp**（19 命令→MCP 工具）| P0 | 3 天 |
| P2 | **worker REST 直调**（web_fetch + 系统提示词注入 URL/鉴权）| P0 | 1 天 |
| P3 | 审批策略层（透明/黑盒映射 + 审批 UI）| P1-P2 | 2 天 |
| P4 | 电商侧栏 UI + 电商 vault | P0-P3 | 3 天 |
| P5 | 社区插件 + 跨平台打包 | P4 | 2 天 |

**合计约 13 天**，skill 零改动，worker 零改动。

---

## 九、风险

| 风险 | 对策 |
|---|---|
| dsh dev preview 接口变 | MCP 桥薄封装；锁定 dsh 0.1.0-rc.6 |
| web_fetch 直调 worker 的鉴权暴露 | 系统提示词给 token，或走网关统一注入 |
| Electron 包体大 | 壳与核心解耦，可后续换 Tauri |
| 社区插件质量 | 只选 GitHub 可验证 + dsh-plugin 话题 |

---

## 十、待确认

1. **P1 开工**：`ozon-skill-mcp` 骨架（19 命令 MCP 定义 + 审批挂点）？—— 核心且独立，建议现在做
2. **worker 直调方式**：确认走 web_fetch + 系统提示词（方案①），还是加轻量网关（方案②）？
3. **审批边界**：按 skill 决策边界默认映射？

*方案 v2 · `/Volumes/os/dev/ozon-worker/docs/ECOM-HARNESS-PLAN.md`*
