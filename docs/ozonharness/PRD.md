# PRD — 电商版 Harness 客户端（对话 + 手动双向驱动）

> 本文档是 ozonharness 方案的**产品需求文档**，整合此前所有分析结论，作为「逐一击破」的实施依据。
> 关联：`ARCHITECTURE.md`（架构 v3.3）· `MCP-TOOLS.md`（19 工具设计）· `SKILL-INVENTORY.md`（skill 功能清单 + 落盘）· `ROADMAP.md`（分阶段计划）· `REFERENCES.md` / `EXAMPLES.md`（参考对象）。

---

## 一、产品概述

### 1.1 定位

**Ozon 跨境电商的「对话 + 手动」双向操作客户端**：把 ozon-worker 的 skill 能力（1688 采集 / Ozon 选品 / 上架），用「对话」和「手动按钮」两种方式操作。

### 1.2 目标用户与场景

| 用户 | 场景 |
|---|---|
| Ozon 卖家（客户）| 日常选品、采集、上架、看订单 |

### 1.3 核心价值

1. **双向驱动**：复杂任务（选品，需综合判断）用对话省心；确定任务（上架指定 URL）用手动直接。两种方式操作同一套 skill 能力，结果一致、互为补充。
2. **黑盒透明化**：skill 原本是「黑盒脚本」，agent 用起来不顺畅。本产品把过程透明化（进度/浏览器/组装/错误可见）。
3. **对话即完成**：接 dsh，用户说话 agent 编排一切。

### 1.4 参考对象（各取所长）

| 参考 | 借鉴 |
|---|---|
| shopbang（上品帮）| 手动 GUI 调本地采集的成熟实现（Electron IPC + BrowserWindow）|
| DAY1-Clean | dsh 套壳 + vault 知识库 + 本地网关统一暴露 |
| PCDCK/pounding-mcp | FastMCP 封装 + 三级安全门控 |

---

## 二、产品形态

### 2.1 客户端 vs 官网

| 形态 | 内容 | 定位 |
|---|---|---|
| **客户端（Electron）** | dsh 对话面 + webui 经营面（**6 个核心页**）| 客户日常用 |
| **官网** | 完整 webui（15 页）| 完整管理 |

### 2.2 客户端核心页（6 个）

主仪表盘 · 采集箱 · 任务中心 · 订单中心 · 店铺管理 · 上架工作台

> 其余（管理员/订阅/钱包/密钥/系统设置/个人中心/热销榜/模板/图片工坊/智能定价/数据大屏）走官网。

### 2.3 双向驱动架构

```
┌─ 对话面（dsh）──→ MCP 工具 ──→ skill CLI ──┐
│                                          ├─→ 本地能力（采集/选品/图搜）
└─ 经营面（webui）──→ 本地网关/IPC ──→ skill CLI ──┘   ← 新增通道

┌─ 对话面（dsh）──→ web_fetch/MCP ──→ worker REST ──┐
└─ 经营面（webui）──→ worker REST ───────────────────┘
                                    （上架/商品/订单，已通）
```

**关键新增**：webui 目前只调 worker REST，调不了 skill 本地能力。需新增「本地能力暴露」通道（Electron 主进程 IPC 或本地网关）。

---

## 三、核心功能需求

### F1 对话驱动（dsh agent 调 skill）

- 19 个 skill 命令 → 19 个 MCP 工具（`mcp__pounding__*`），见 `MCP-TOOLS.md`
- 审批：read 黑盒直跑 / write 老板眼皮底下 / destructive 双重确认（dsh 侧 `tools/pre-execute` 钩子）

### F2 手动驱动（GUI 调 skill + worker）

- webui 核心页提供手动操作按钮（采集/选品/上架），调本地 skill 能力 + worker REST
- 实现：Electron 主进程 IPC（shopbang 模式）或本地网关（DAY1-Clean 模式）

### F3 vault 落盘（配置自动 + 结果卡片化）

- **目录**：`vault/`（平台无关命名），结构见 `SKILL-INVENTORY.md` §2.2
- **分层**：配置类（店铺/凭证/类目）skill 命令自动落盘（状态必须同步）；结果类（采集/选品/上架）agent 工作流落盘
- **结果卡片化**：采集结果落盘成「商品卡片 Markdown」（图片 URL + 采购价/运费/利润），对齐原型图
- **脱敏**：凭证只存摘要，明文 key 绝不落盘

### F4 黑盒透明化

| 黑盒 | 透明化手段 |
|---|---|
| 进度黑盒（长任务无反馈）| 落盘 `Active-Context.md` 实时进度 |
| 浏览器黑盒（CDP 爬取不可见）| 落盘爬取日志（抓了哪些页、验证码提醒）|
| 组装黑盒（字段映射不可见）| 落盘组装映射（1688字段→Ozon字段可视化）|
| 错误黑盒（出错难纠正）| 落盘错误 + 可操作建议 |

### F5 平台无关（未来扩展）

- 命名不带 ozon 前缀（`stores/sourcing/selection/listing`），Ozon 专属集中 `05-ozon/`
- 未来加 Amazon → `06-amazon/`，通用能力零改动

---

## 四、技术架构（详见 ARCHITECTURE.md v3.3）

### 4.1 三个协议边界

| 协议 | 用途 |
|---|---|
| MCP | agent 调 skill 进程内能力（19 命令）|
| REST | worker 保持 FastAPI（100+ 端点）|
| SDK | 给前端/开发者（`generated.d.ts` 已是成品）|

### 4.2 仓库结构

- `ozon-worker`（现有）：worker + skill + webui + **pounding-mcp**（skill 薄封装，同仓同步）
- `ozon-harness-shell`（新建）：Electron 壳，pin dsh + 引用 pounding-mcp
- `dsh`（上游）：pin 版本，不维护

### 4.3 插件化壳化

- dsh 更新零感知：只通过 MCP（稳定协议）+ `cordis.patch.yml` 补丁层接入
- 唯一耦合红线：webui 走 iframe，不碰 dsh client-ui 插件

---

## 五、分阶段实施（详见 ROADMAP.md）

| Phase | 内容 | 产出 |
|---|---|---|
| **P1** | `pounding-mcp`（19 命令 → 工具）✅ 骨架已完成并验证 | 可被 dsh 调用的 MCP server |
| **P2** | 审批策略层（dsh 侧 pre-execute 钩子）| 三级安全门控 |
| **P0** | Electron 壳 + dsh 运行时 | 能启动 dsh 的桌面壳 |
| **P3** | 本地网关 + webui iframe + 本地能力暴露 | 双向驱动（手动 GUI 调 skill）|
| **P4** | vault 落盘（配置自动 + 结果卡片化）+ 黑盒透明化 | 电商知识库 + 过程可见 |
| **P5** | 社区插件 + 跨平台打包 | Win/Mac 安装包 |

---

## 六、验收标准

### 6.1 对话驱动

- [ ] 用户在 dsh 说「搜索 1688 的收纳盒」→ agent 调 `mcp__pounding__search` → 返回结果
- [ ] 上架类操作（graph 提交）触发审批，用户确认后才执行
- [ ] 只读操作（search/probe）无审批直接跑

### 6.2 手动驱动

- [ ] 客户端 webui 采集箱页有「采集」按钮，点击后调本地 skill 采集，结果显示为商品卡片
- [ ] 手动操作和对话操作结果一致（都落盘同一 vault）

### 6.3 落盘与卡片化

- [ ] `set_store` 后 `vault/01-Stores/stores.md` 更新（脱敏）
- [ ] 采集结果落盘成商品卡片（图片 URL + 采购价 + 运费 + 利润）
- [ ] agent 能通过读 vault 自动获取店铺/采集/选品信息（无需重跑脚本）

### 6.4 黑盒透明化

- [ ] 长任务（discover）执行时，`Active-Context.md` 实时更新进度
- [ ] 浏览器爬取日志可查（抓了哪些页、是否遇验证码）
- [ ] 上架组装映射可查（1688字段→Ozon字段）

### 6.5 平台无关

- [ ] 命名无 ozon 前缀（除 `05-ozon/` 专属）
- [ ] 未来加 Amazon 平台，通用能力（采集/选品/上架/落盘）零改动

---

## 七、待确认（仅剩）

1. **本地能力暴露方式**：Electron 主进程 IPC（shopbang 模式）还是本地网关（DAY1-Clean 模式）？——影响 P3 实现
2. 其余决策已锁定（Electron 壳 / iframe 整合 / 云端 worker / 暂不装社区插件）
