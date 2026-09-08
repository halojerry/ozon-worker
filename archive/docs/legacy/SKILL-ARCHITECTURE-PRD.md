# PRD: pounding-ozon-probe Skill 架构升级

> 版本: v1.0 | 日期: 2026-08-06 | 状态: 待评审

---

## 1. 背景与问题

### 1.1 现状

pounding-ozon-probe 是跨境电商 Ozon 上架工具包，通过 COS 云端自动更新分发。AI Agent（WorkBuddy / Claude Code / ZCode）在客户本地运行此包，依据 SKILL.md 操作 14 个 CLI 命令 + 1 个批量脚本完成选品、跟卖、上架全流程。

当前 SKILL.md 为 345 行单文件，包含环境准备、意图路由、命令参考（5 条管线）、错误处理、决策边界等全部内容。

### 1.2 核心问题

| # | 问题 | 影响 | 证据 |
|---|------|------|------|
| P1 | **Frontmatter 不合规** | WorkBuddy SkillManage 无法管理；不同 agent 触发命中率低 | 缺 `agent_created: true`；description 用条件句而非第三人称；缺 version；触发词漏"图片/批量/趋势/跟卖" |
| P2 | **写作风格不兼容** | 部分 agent（system prompt 强的）混淆"你在对谁说话" | 全文用第二人称"你"/"你的角色"，WorkBuddy skill-creator 要求祈使句/客观描述 |
| P3 | **渐进式披露缺失** | agent 触发时加载 345 行全量上下文，浪费 token、降低路由准确性 | 无 `references/` 目录；SKILL.md 一文件塞了路由+速查+管线详解+错误码+环境准备+越界清单 |
| P4 | **跨 Agent 环境不兼容** | WorkBuddy managed Python 路径不同；其他 agent 工作目录不确定 | 硬编码 `python3.12 scripts/cli.py`；硬编码 `cd skill` |
| P5 | **命令覆盖不全** | agent 不知道有命令或不知道有参数 | 14 命令中 6 个完整(43%)、7 个参数不全、4 个完全未入册（search/probe/list_stores/get_ak） |
| P6 | **内部矛盾** | agent 行为不确定 | §5.3 说"不提供实时进度查询"但 `batch_test --wait` 轮询 + `check_task_status()` 都存在；§5.1 "流程完成后我会通知你"无推送机制 |
| P7 | **VERSION 死循环** | 线上用户每次运行命令都重复下载 10MB | 包内 VERSION=0.25.0 ≠ manifest=0.27.0，updater 毸远判定"有更新" |

### 1.3 不改动什么

- **不改任何 .py 代码**（cli.py / cloud_probe.py / compile.py 的编译逻辑）
- **不改 Worker 端**（worker/src/ 下任何文件）
- **不改信封契约**（GraphInput / GraphOutput 结构不变）
- **不改 COS 发布机制**（只改打包内容和 VERSION 生成方式）

---

## 2. 目标

### 2.1 核心目标

> **让任何主流 Agent（WorkBuddy / Claude Code / ZCode）拿到发布包后，仅凭文档就能正确、完整地操作全部命令，不猜、不漏、不矛盾。**

### 2.2 量化指标

| 指标 | 当前 | 目标 |
|------|------|------|
| 命令完整覆盖率 | 43% (6/14) | 100% (14/14 + batch_test) |
| SKILL.md 行数 | 345 行 | ≤ 160 行（核心骨架） |
| references/ 文件 | 0 | 4 个专题文件 |
| Frontmatter 合规项 | 1/4 (name only) | 4/4 (name + description + agent_created + version) |
| 文档内部矛盾 | ≥ 3 处 | 0 |
| Agent 触发场景覆盖 | 3/7 | 7/7 |

### 2.3 非目标

- 不优化 Worker 端的上架成功率/生图质量
- 不增加新的 CLI 命令
- 不改 cli.py 的参数定义
- 不做 audit_products.py 的打包（保持开发者工具定位）

---

## 3. 目标架构

### 3.1 架构选型：单 Skill + references/ 渐进式披露

对三种"多 Skill"方案做了对比分析：

| 方案 | 描述 | 优点 | 缺点 | 适用性 |
|------|------|------|------|--------|
| **A. 单 Skill + references/** | 一个 skill，SKILL.md 精简骨架，详细内容拆到 references/ | 零代码改动；符合 WorkBuddy 标准；agent 按需加载 | 仍是单包，触发时加载骨架 | ✅ **推荐 Phase 1** |
| **B. 多独立 Skill 共享 scripts/** | 拆 3 个 skill（env/listing/discovery），各自 SKILL.md，共享 scripts/ 目录 | 触发更精准；上下文更小 | 打包复杂；共享状态管理难；代码耦合高 | ⚠️ 可选 Phase 2 |
| **C. Expert 包 (Team 型)** | 创建 Ozon 专家团，多 agent 各带 skill | WorkBuddy 原生体验最佳；有头像/人设/快捷提问 | 生命周期管理复杂；仅 WorkBuddy 平台可用 | ⚠️ 可选 Phase 3 |

**选 A 的理由**：

1. **代码现实**：14 命令共享 `data/config/`（凭证）、`data/discovery/`（选品缓存）、`data/logs/`（日志），拆成独立 skill 后共享状态管理极其复杂；
2. **命令耦合**：`discover` 选完品后调 `graph` 上架，`follow` 降级走 `graph`，`batch_test` 混合调 `graph`+`follow`——拆开后跨 skill 调用无机制；
3. **用户心智**：用户记一个 skill 名比记三个简单；
4. **渐进式披露已足够**：WorkBuddy 的 references/ 机制正是为"大 skill 按需加载"设计的，agent 触发时只加载 160 行骨架，需要细节时才读 references/。

### 3.2 未来子 Skill 拆分路线（Phase 2 可选）

如果 Phase 1 后仍发现以下信号，再考虑拆分：

- agent 频繁在"选品"场景误触发"上架"命令（路由混淆）
- 选品场景的 references/ 文件被频繁加载但上架场景不需要
- 不同用户只使用部分功能（只选品不上架 / 只上架不选品）

**拆分方案（如需）**：

```
# 主 skill（核心上架流程）
pounding-ozon-probe/
├── SKILL.md          # graph / follow / batch_test + 环境准备
├── references/
│   ├── error-codes.md
│   ├── output-schema.md
│   └── env-setup.md
└── scripts/          # 共享代码

# 配套 skill（选品发现）
pounding-ozon-discovery/
├── SKILL.md          # discover / trend / search / image_search / probe
├── references/
│   └── discovery-guide.md
└── scripts/          # → 软链接到主 skill 的 scripts/
```

**触发词分离**：
- `pounding-ozon-probe`：用户发 1688/Ozon URL、说"上架""跟卖""批量"
- `pounding-ozon-discovery`：用户说"选品""找蓝海""找趋势""搜一下""以图搜款"

### 3.3 Expert 包路线（Phase 3 可选）

用 expert-manager 创建 "Ozon 上架专家"：

```
ozon-listing-expert/
├── .codebuddy-plugin/
│   └── plugin.json
├── agents/
│   └── ozon-operator.md    # 专家人设：跨境电商上架操作员
├── skills/
│   └── pounding-ozon-probe/  # 现有 skill 嵌入
│       ├── SKILL.md
│       ├── references/
│       └── scripts/
└── avatars/
    └── ozon-operator.png
```

**收益**：
- WorkBuddy 专家中心一键启用
- `defaultInitPrompt` 引导用户正确使用
- `quickPrompts` 预设推荐问题
- 专家人设 + Skill 操作流程 = 完整体验

### 3.4 目标目录结构（Phase 1 落地）

```
skill/
├── SKILL.md                    # ≤160 行精简骨架
├── references/                 # 新建目录
│   ├── command-reference.md    # 全命令速查表 + 参数详解 + 示例
│   ├── error-codes.md          # Worker 错误码表 + 进度查询口径 + 汇报模板
│   ├── output-schema.md        # submit_result / product_summary 字段解析
│   └── env-setup.md            # 环境准备 + 凭证获取 + check 故障排查
├── envelope_example.json       # 不变
├── field_mapping.md            # 不变
├── requirements.txt            # 不变
├── VERSION                     # 修正为跟随发布版本
├── scripts/                    # 不变（代码零改动）
│   ├── cli.py
│   ├── batch_test.py
│   ├── cloud_probe.py
│   ├── ...
│   └── lib/
└── compile.py                  # DOC_FILES 加入 references/ 路径
```

---

## 4. 详细规格

### 4.1 Frontmatter 规范

**当前**：
```yaml
---
name: pounding-ozon-probe
description: >
  Ozon 上架工具。当用户发送 1688 链接时直接上架，发送 Ozon 链接时直接跟卖。
  当用户说"帮我找蓝海产品""帮我选品"且没有给链接时，去 Ozon 中国站自动选品。
  支持批量上架、以图搜款。
---
```

**目标**：
```yaml
---
name: pounding-ozon-probe
version: "0.27.0"
agent_created: true
description: >
  Ozon 跨境电商上架工具。此技能在以下场景触发：
  用户发送 1688 商品链接时直接上架到 Ozon；用户发送 Ozon 商品链接时跟卖；
  用户发送图片时以图搜款找 1688 同款；用户说"选品""找蓝海""找趋势商品"时
  自动搜索 Ozon 中国站并匹配 1688 货源；用户发送多个链接时批量处理。
  覆盖选品、跟卖、上架、以图搜款、趋势选品全流程。
---
```

**改动点**：

| 字段 | 改动 | 原因 |
|------|------|------|
| `version` | 新增 `"0.27.0"` | 版本追踪；与 VERSION 文件一致 |
| `agent_created` | 新增 `true` | WorkBuddy SkillManage 可管理（修改/删除） |
| `description` | 条件句 → 第三人称客观描述 | WorkBuddy skill-creator 硬性要求："This skill should be used when..." |
| 触发词 | 扩充覆盖 7 场景 | 不同 agent 匹配逻辑不同，覆盖越全越不漏触发 |

### 4.2 写作风格规范

| 规则 | 当前（错误） | 目标（正确） |
|------|-------------|-------------|
| 人称 | 第二人称"你" | 祈使句/客观描述 |
| 角色定义 | "你的角色：操作员。你用以下命令完成工作。" | "Role: operator. Execute CLI commands to complete tasks." |
| 指令 | "你只需按场景选择并执行" | "Select the pipeline based on user intent and execute." |
| 禁止 | "你" / "你的" / "你会" | 全部改为祈使句或被动语态 |

**全文替换规则**（在 SKILL.md 和 references/ 中一致执行）：

| 原文模式 | 替换为 |
|----------|--------|
| "你的角色" | "Role" |
| "你用以下命令" | "Execute CLI commands" |
| "你只需" | 删除，直接写指令 |
| "你发给" | "User sends" |
| "告诉用户" | 保持（这是对 agent 的指令，不是对用户的称呼） |
| "等用户说" | "Wait for user to say" |

### 4.3 SKILL.md 骨架（≤160 行）

**目标结构**：

```
SKILL.md
├── frontmatter (8 行)
├── §0 概述 + 定位 Skill 目录 (15 行)
├── §1 意图路由 (30 行)          ← 核心中的核心，必须全量在 SKILL.md
├── §2 命令速查表 (30 行)        ← 一张表，14 命令 + batch_test
├── §3 决策边界 (10 行)          ← 精简版
├── §4 越界行为 (10 行)          ← 精简版
├── §5 参考文件索引 (15 行)      ← 指向 references/ 各文件
├── §6 更新机制 (10 行)
└── (合计 ~128 行，余量留给格式)
```

**从当前 SKILL.md 拆出到 references/ 的内容**：

| 当前章节 | 目标位置 | 拆出内容 |
|----------|----------|----------|
| §2 环境准备（全部） | `references/env-setup.md` | 安装依赖、获取凭证、验证配置、环境要求、check 故障排查表 |
| §4 管线 A-E 详细说明 | `references/command-reference.md` | 每个管线的触发条件、完整参数、示例、输入输出 |
| §4 命令速查表 | **保留在 SKILL.md** | 精简为一张表 |
| §5 Worker 响应处理（全部） | `references/error-codes.md` | 错误码表、回复模板、进度查询口径 |
| §5.1 提交成功回复模板 | `references/output-schema.md` | submit_result 字段解析、product_summary 字段解析、汇报模板 |
| §7 错误处理 | `references/error-codes.md` | 合并到错误码文件 |
| §6 决策边界 | **保留在 SKILL.md** | 精简版 |
| §8 越界行为 | **保留在 SKILL.md** | 精简版 |
| §9 参考文件 | **保留在 SKILL.md** | 扩充为 references/ 索引 |
| §10 更新机制 | **保留在 SKILL.md** | 精简版 |

### 4.4 意图路由（§1）补全

**当前路由**（5 分支）：
```
1688 URL → A | Ozon URL → B | "好跟卖的" → C | "选品上架" → D | "蓝海/趋势" → E
```

**目标路由**（7 分支）：
```
用户输入
  ├─ 有 1688 URL？                    → 【管线 A】1688 直接上架
  ├─ 有 Ozon URL？                    → 【管线 B】Ozon 跟卖
  ├─ 有图片（非 URL）？                → 【管线 D1】以图搜款
  ├─ "有什么好跟卖的"？无 URL          → 【管线 C】Ozon 选品发现
  ├─ "帮我选品上架"？无 URL            → 【管线 D】1688 搜索/图搜 → 上架
  ├─ "找蓝海/热卖/趋势"+品类           → 【管线 E】趋势驱动选品
  ├─ 多个 URL / "批量处理这些"         → 【管线 F】batch_test 批量
  └─ "店铺商品状态/为什么被拒"          → 引导用户查看 Ozon 卖家后台
                                        （本工具不提供店铺巡检能力）
```

### 4.5 命令速查表（§2）规范

每行格式：

| 列 | 说明 |
|----|------|
| 命令 | CLI 子命令名 |
| 用途 | 一句话 |
| 关键参数 | 最常用的 2-4 个（完整参数见 references/command-reference.md） |
| 副作用 | 提交 Worker / 耗 1688 配额 / 写 data/ / 无 |
| 需确认 | 自动执行 / 必须确认 |
| 详见 | references/command-reference.md §X |

**14 命令 + batch_test 完整覆盖**：

| 命令 | 用途 | 关键参数 | 副作用 | 需确认 |
|---|---|---|---|---|
| `check` | 验证环境 | 无 | 无 | 自动 |
| `set_store` | 配置 Ozon 店铺 | `--name --client-id --api-key` | 写 data/config/ | 自动 |
| `set_token` | 配置 MXOU_TOKEN | `--token` | 写 data/config/ | 自动 |
| `set_ak` | 配置 1688 AK | `--ak` | 写 data/config/ | 自动 |
| `update` | 检查并应用自动更新 | 无 | 覆盖 skill 文件 | 自动 |
| `get_ak` | 浏览器自动获取 1688 AK | `--timeout` | 无 | 自动 |
| `list_stores` | 列出已配置店铺 | 无 | 无 | 自动 |
| `graph` | 1688 上架 | `--url/--item-id --store [--no-submit] [--category-query] [--retries]` | 提交 Worker（除非 --no-submit） | 自动 |
| `follow` | Ozon 跟卖 | `--ozon-url --store [--auto-submit]` | 提交 Worker（加 --auto-submit） | 自动 |
| `image_search` | 以图搜款 | `--image [--source cdp] [--sort] [--limit]` | 耗 1688 图搜配额 | 自动 |
| `discover` | Ozon 选品 | `--keyword/--url/--max-products [--rules] [--export] [--auto-submit] [--brand-filter] [--min-price] [--max-price] [--no-analytics]` | 查 seller.ozon.ru | 展示后确认 |
| `trend` | 趋势选品 | `--category [--market-info] [--max-price] [--max-moq] [--min-ship-rate-48h] [--min-sales] [--with-skus] [--export] [--output]` | 耗 1688 配额 | 展示后确认 |
| `search` | 1688 关键词搜索 | `query [--page-size]` | 耗 1688 配额 | 自动 |
| `probe` | CDP 探针抓取单个 1688 商品 | `--url [--timeout]` | 无 | 自动 |
| `batch_test.py` | 批量处理 URL 列表 | `--urls-file [--submit] [--wait] [--dry-run] [--start] [--limit] [--delay] [--wait-timeout] [--type-filter]` | 提交 Worker（加 --submit） | 必须确认 |

### 4.6 references/ 文件规格

#### 4.6.1 references/command-reference.md

**内容**：14 命令 + batch_test 的完整参数表 + 使用示例 + 输入输出说明

**结构**：
```markdown
# 全命令参考

## check
- 用途：验证环境（Chrome/凭证/Worker）
- 参数：无
- 输出：逐项 ✅/❌
- 示例：`python3 scripts/cli.py check`
- ❌ 常见问题：Chrome 未安装 / 凭证无效 / Worker 不可达 → 修复步骤

## set_store
...

## graph
- 参数：--url, --item-id, --store, --no-submit, --category-query, --retries
- --no-submit：只组装信封不提交 Worker（调试/确认场景）
- --category-query：指定 Ozon 类目关键词（俄语）
- 输出：JSON {summary, envelope, submit_result}
  - summary: 商品摘要（标题/价格/重量/尺寸/图片数/属性数/供应商）
  - envelope: 完整 GraphInput 信封
  - submit_result: Worker 提交结果（见 output-schema.md）
...
```

#### 4.6.2 references/error-codes.md

**内容**：Worker 错误码表 + CLI 错误处理 + 进度查询口径

**关键修正**：
- §5.3 "CLI 不提供实时进度查询" → 改为：
  - 批量任务：`batch_test.py --wait` 轮询并展示 product_summary
  - 单任务：当前无 CLI 子命令查询（`check_task_status` 存在于 cloud_probe.py 但未注册为子命令）
  - 建议回复用户：任务已提交，预计 10-20 分钟，可用 `batch_test --wait` 跟踪批量任务
- §5.1 "流程完成后我会通知你" → 删除（无推送机制）

#### 4.6.3 references/output-schema.md

**内容**：submit_result + product_summary 字段解析 + 成败判定 + 汇报模板

**submit_result 字段**：

| 字段 | 类型 | 含义 | 成败判定 |
|------|------|------|----------|
| `ok` | bool | 提交是否成功 | true=成功 |
| `task_id` | string | Worker 任务 ID | 有值=已入队 |
| `message` | string | 状态描述 | 展示给用户 |
| `error_code` | string | 错误码（失败时） | 见 error-codes.md |
| `detail` | object | 错误详情 | 按错误码解释 |

**product_summary 字段**（batch_test --wait 输出）：

| 字段 | 类型 | 含义 |
|------|------|------|
| `purchase_url` | string | 1688 采购链接 |
| `purchase_cost` | float | 采购价（CNY） |
| `margin_rate` | float | 利润率 |
| `price` | string | Ozon 售价（RUB） |
| `logistics_cost` | float | 运费预估（CNY） |
| `profit_rate` | float | 净利润率 |
| `product_id` | string | Ozon 商品 ID |

**汇报模板**：
```
✅ 任务已提交到云端处理
- 任务 ID：{task_id}
- 预计耗时：10-20 分钟（类目匹配 → AI 生图 → Ozon 上架 → 审核）
- 可用 batch_test --wait 跟踪批量任务进度

或（batch_test --wait 完成后）：
✅ 批量任务完成（{成功数}/{总数}）
- 1688: {purchase_url}
- 采购价: ¥{purchase_cost} | 售价: {price}
- 利润率: {margin_rate} | 净利润率: {profit_rate}
- Ozon 商品 ID: {product_id}
```

#### 4.6.4 references/env-setup.md

**内容**：安装依赖 + 凭证获取 + check 故障排查表 + data/ 目录语义

**check 故障排查表**：

| ❌ 项 | 原因 | 修复 |
|-------|------|------|
| Chrome 未检测到 | 未安装或路径异常 | 安装 Google Chrome；或设置 CHROME_PATH 环境变量 |
| MXOU_TOKEN 无效 | 未设置或已过期 | `python3 scripts/cli.py set_token --token <TOKEN>` |
| 1688 AK 缺失 | 未设置或已过期 | `python3 scripts/cli.py set_ak --ak <AK>` 或 `python3 scripts/cli.py get_ak` |
| Ozon 店铺未配置 | 未设置 | `python3 scripts/cli.py set_store --name "店铺" --client-id <ID> --api-key <KEY>` |
| Worker 不可达 | 网络问题或服务宕机 | 检查网络；确认 WORKER_URL 正确 |

**data/ 目录语义**：

| 路径 | 内容 | 操作限制 |
|------|------|----------|
| `data/config/` | 凭证（stores.json / token / ak） | ❌ 禁止删除 |
| `data/discovery/` | 选品采集结果 | 可清理，不影响运行 |
| `data/logs/` | 运行日志 | 可清理 |
| `data/cache/` | 磁盘缓存（TTL 自动过期） | 可清理 |
| `wave*.txt` / `urls_*.txt` | 测试遗留文件 | 可清理，勿误认为配置 |

### 4.7 跨 Agent 环境兼容

**当前硬编码**：
```bash
cd skill && python3.12 scripts/cli.py graph --url "..."
```

**目标写法**：
```bash
python3 scripts/cli.py graph --url "..."
```

**SKILL.md §0 新增"定位 Skill 目录"说明**：

```markdown
## 0. 定位 Skill 目录

所有命令在 skill 根目录下执行。Skill 根目录是包含 `scripts/cli.py` 的目录。

确定方式（按优先级）：
1. 若环境变量 SKILL_DIR 已设置，使用 $SKILL_DIR
2. 若当前目录存在 scripts/cli.py，使用当前目录
3. 否则，查找上级目录直到找到 scripts/cli.py

Python 要求 ≥ 3.12。使用环境中可用的版本：`python3` 或 `python3.12`。
```

**注意**：这是文档层面的指导，不修改 cli.py 代码。cli.py 本身已经能在任何 Python 3.12+ 环境运行。

### 4.8 compile.py 打包清单

**当前 DOC_FILES**：
```python
DOC_FILES = [
    "SKILL.md",
    "envelope_example.json",
    "field_mapping.md",
    "requirements.txt",
    "VERSION",
]
```

**目标 DOC_FILES**：
```python
DOC_FILES = [
    "SKILL.md",
    "references/command-reference.md",
    "references/error-codes.md",
    "references/output-schema.md",
    "references/env-setup.md",
    "envelope_example.json",
    "field_mapping.md",
    "requirements.txt",
    "VERSION",
]
```

**注意**：compile.py 的 DOC_FILES 是纯文件列表，加入 `references/` 路径即可，不需要改编译逻辑。

### 4.9 VERSION 修正

**当前问题**：
- 仓库 `skill/VERSION` = 0.25.0（手动维护，经常忘记递增）
- COS manifest = 0.27.0（GitHub Actions 自动从 git tag 生成）
- 包内 VERSION 0.25.0 < manifest 0.27.0 → updater 每次都判定"有更新"

**修复方案**（CI 层面，不改代码）：
1. GitHub Actions 打包步骤中，用 git tag 覆写 VERSION 文件：
   ```yaml
   - name: Write VERSION from tag
     run: echo "${GITHUB_REF_NAME#v}" > skill/VERSION
   ```
2. 打包后校验：`包内 VERSION == manifest version`，不一致则 fail CI
3. 仓库 `skill/VERSION` 可保留手动维护值，CI 打包时强制覆写

---

## 5. 实施计划

### Phase 1：文档重构（本次执行，零代码改动）

| # | 动作 | 涉及文件 | 预估 |
|---|------|----------|------|
| 1.1 | 新建 `references/` 目录 | `skill/references/` | - |
| 1.2 | 从 SKILL.md 拆出 4 个 references 文件 | `skill/references/*.md` | 1h |
| 1.3 | 重写 SKILL.md 骨架（≤160 行） | `skill/SKILL.md` | 30min |
| 1.4 | 改 frontmatter | `skill/SKILL.md` | 5min |
| 1.5 | 全文第二人称 → 祈使句 | `skill/SKILL.md` + `references/*.md` | 20min |
| 1.6 | 意图路由补 2 分支（图片/批量） | `skill/SKILL.md` | 10min |
| 1.7 | 命令速查表补全 14 命令 | `skill/SKILL.md` | 15min |
| 1.8 | 修正 §5.3 矛盾（进度查询口径） | `references/error-codes.md` | 10min |
| 1.9 | compile.py DOC_FILES 加 references/ | `skill/compile.py` | 5min |
| 1.10 | python3.12 → python3 + §0 目录定位 | `skill/SKILL.md` | 10min |
| 1.11 | VERSION 修正（CI 方案文档） | 记录在 PRD 中 | - |

**验收标准**：
- [ ] SKILL.md ≤ 160 行
- [ ] references/ 含 4 个 .md 文件
- [ ] frontmatter 含 name + description + version + agent_created
- [ ] 14 命令 + batch_test 全部出现在速查表
- [ ] 无"你"/"你的"（除引号内用户原文）
- [ ] 无内部矛盾（§5.3 进度查询口径统一）
- [ ] compile.py DOC_FILES 含 references/ 路径
- [ ] `python3 scripts/cli.py --help` 输出的命令与速查表一致

### Phase 2：子 Skill 拆分（可选，Phase 1 后评估）

| # | 动作 | 触发条件 |
|---|------|----------|
| 2.1 | 评估是否拆分（路由混淆 / 上下文浪费） | Phase 1 上线后 2 周 |
| 2.2 | 如需拆分：创建 pounding-ozon-discovery skill | 选品场景独立触发 |
| 2.3 | 主 skill 保留上架 + 环境管理 | - |

### Phase 3：Expert 包（可选，WorkBuddy 专属）

| # | 动作 | 触发条件 |
|---|------|----------|
| 3.1 | 用 expert-manager 创建 Ozon 上架专家 | 用户主要在 WorkBuddy 平台使用 |
| 3.2 | 嵌入 pounding-ozon-probe skill | - |
| 3.3 | 配置 defaultInitPrompt + quickPrompts | - |

---

## 6. 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| references/ 拆分后 agent 不主动加载 | 中 | agent 缺少详细参数信息 | SKILL.md 速查表必须自包含核心参数；references/ 只放补充细节；在 SKILL.md 明确标注"完整参数见 references/command-reference.md" |
| 祈使句改写后语感变化 | 低 | 无功能影响 | 保持指令清晰即可 |
| python3 替代 python3.12 后环境无 3.12 | 低 | cli.py 要求 3.12+ | §0 已说明"Python ≥ 3.12"；cli.py shebang 已是 `#!/usr/bin/env python3` |
| references/ 未进打包清单 | 中 | 发布包缺文件 | compile.py DOC_FILES 同步修改；打包后 CI 校验文件完整性 |
| VERSION 修正方案需要改 CI | 中 | 如不改 CI 则 VERSION 仍不一致 | PRD 中已给出 CI 修改方案；可先手动改 VERSION 文件作为临时措施 |

---

## 7. 附录

### 7.1 当前命令 vs 文档覆盖审计

| 命令 | SKILL.md 有无 | 参数完整度 | 缺失参数 | 有无示例 |
|------|-------------|-----------|----------|---------|
| check | ✅ §2.3 | 完整 | - | ✅ |
| set_store | ✅ §2.2 | 完整 | - | ✅ |
| set_token | ✅ §2.2 | 完整 | - | ✅ |
| set_ak | ✅ §2.2 | 完整 | - | ✅ |
| update | ✅ §10 | 完整 | - | ✅ |
| get_ak | ✅ 速查表 | 完整 | - | ❌ 无示例 |
| list_stores | ✅ 速查表 | 完整 | - | ❌ 无示例 |
| graph | ✅ §4 管线A | 不全 | --no-submit, --category-query, --retries, --item-id | ✅ |
| follow | ✅ §4 管线B | 完整 | - | ✅ |
| image_search | ✅ §4 D1 | 不全 | --source, --sort, --limit | ✅ |
| discover | ✅ §4 管线C | 不全 | --brand-filter, --min-price, --max-price, --output, --no-analytics | ✅ |
| trend | ✅ §4 管线E | 不全 | --with-skus, --export, --output | ✅ |
| search | ✅ 速查表 | 完整 | - | ❌ 无示例 |
| probe | ✅ 速查表 | 完整 | - | ❌ 无示例 |
| batch_test.py | ✅ §4 | 不全 | --wait, --dry-run, --start, --limit, --delay, --wait-timeout, --type-filter | ✅ |

### 7.2 内部矛盾清单

| # | 位置 | 矛盾 | 修正方向 |
|---|------|------|----------|
| 1 | §5.3 vs batch_test --wait | 文档说"不提供实时进度查询"，实际 --wait 可轮询 | 改为：批量可用 --wait 轮询；单任务无 CLI 查询 |
| 2 | §5.1 vs 实际能力 | "流程完成后我会通知你"，实际无推送机制 | 删除此承诺 |
| 3 | §2 vs §0 | §2 写 `python3.12`，但不同 agent 环境不同 | 统一为 `python3` + §0 环境自适应说明 |
| 4 | 速查表 vs cli.py | 速查表缺 --no-submit 等参数 | 补全 |

### 7.3 WorkBuddy Skill 标准对照

| 标准项 | 要求 | 当前 | 目标 |
|--------|------|------|------|
| name | 必填，kebab-case | ✅ pounding-ozon-probe | ✅ 不变 |
| description | 必填，第三人称 | ❌ 条件句 | ✅ 改为第三人称 |
| agent_created | SkillManage 可管理 | ❌ 缺失 | ✅ true |
| version | 版本追踪 | ❌ 缺失 | ✅ "0.27.0" |
| 写作风格 | 祈使句 | ❌ 第二人称 | ✅ 祈使句 |
| references/ | 按需加载 | ❌ 不存在 | ✅ 4 个文件 |
| scripts/ | 可执行代码 | ✅ 已有 | ✅ 不变 |
| 渐进式披露 | 三层加载 | ❌ 单文件 | ✅ 三层 |

### 7.4 子 Skill 拆分可行性分析

**支持拆分的因素**：
- 选品（discover/trend/search/image_search）和上架（graph/follow/batch_test）在使用场景上可分离
- 不同用户可能只用部分功能

**反对拆分的因素**：
- 14 命令共享 `data/config/`、`data/discovery/`、`data/logs/` → 拆包后共享状态管理复杂
- `discover` 选完品后调 `graph` 上架 → 跨 skill 调用无机制
- `follow` 降级走 `graph` → 跨 skill 依赖
- `batch_test` 混合调 `graph`+`follow` → 跨 skill 编排
- 用户心智成本：记一个 skill 名 vs 记三个

**结论**：Phase 1 不拆分，用 references/ 渐进式披露解决上下文体积问题。Phase 2 根据实际使用反馈再评估。
