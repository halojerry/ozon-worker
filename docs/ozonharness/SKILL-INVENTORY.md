# SKILL-INVENTORY — skill 功能清单 + 落盘方案

> 目标：把 skill 的「脚本/配置/知识」功能化，用 **skill 命令落盘**成平台无关的 Markdown 知识库，agent 通过 AGENTS.md 自动读取（借鉴 DAY1-Clean 的 vault 机制）。
> 命名原则：**平台无关能力不带 ozon 前缀**，仅 Ozon 专属的才带 `ozon/`。未来扩展 Amazon/eBay 等平台时，通用能力直接复用。

---

## 一、skill 功能全貌（5 层，源码实证）

### 1.1 配置层（`scripts/lib/config_store.py` → `data/config/`）

| 文件 | 内容 | 平台相关性 |
|---|---|---|
| `stores.json` | 多店铺凭证 `{default: "主店铺", stores: {name: {client_id, api_key, currency}}}` | **通用**（店铺概念平台无关，但当前字段是 Ozon 的 client_id/api_key）|
| `settings.json` | `mxou_token` + `ali_1688_ak` | **通用**（1688 AK 是货源平台，MXOU 是 LLM 平台）|
| `auth_cache.json` | 认证缓存 | 通用 |

> 这就是用户说的「客户端配置店铺信息」——skill 的 config，落盘后 agent 应能读（脱敏）。

### 1.2 CLI 命令层（19 个，`scripts/cli.py`）

| 类 | 命令 | 平台相关性 |
|---|---|---|
| **配置** | `set_store` `list_stores` `set_token` `set_ak` `get_ak` `check` | 通用（店铺/凭证/诊断）|
| **采集** | `search` `probe` `image_search` | 通用（1688 是货源，未来可接别的货源）|
| **选品** | `discover` `discover_multi` `seller` `queries` | 通用（选品方法论平台无关）；其中 Ozon 榜单/卖家分析有 Ozon 成分 |
| **Ozon 专属** | `category`（Ozon 类目）| **Ozon** |
| **上架** | `graph` `follow` `query` | 通用流程（未来可上架到别的平台）|
| **维护** | `update` `cleanup` | 通用 |

### 1.3 能力层（`scripts/lib/`，含编译 `.c` 产物）

| 能力 | 文件 | 平台相关性 |
|---|---|---|
| 浏览器/CDP | `cdp_client` `chrome_launcher` `stealth` | 通用（基础设施）|
| 1688 采集 | `browser_probe` `ak_1688_client` | 通用（货源）|
| 选品引擎 | `ozon_discovery` `ozon_fission` | 通用方法论（名字带 ozon 但逻辑是选品）|
| 图搜 | `ozon_image_search` | 通用 |
| 卖家分析 | `ozon_seller` `ozon_seller_analytics` | Ozon（seller.ozon.ru）|
| Ozon API | `ozon_api` | **Ozon** |
| 上报 | `analytics_upload` | 通用 |
| 配置 | `config_store` | 通用 |

### 1.4 知识层（`references/` + `field_mapping.md`）

| 文件 | 内容 | 平台相关性 |
|---|---|---|
| `command-reference.md`（30KB）| 命令完整参考 | 通用 |
| `anti-patterns.md` | 反模式 | 通用 |
| `error-codes.md` | 错误码 | 通用 |
| `output-schema.md` | 输出 schema | 通用 |
| `env-setup.md` | 环境设置 | 通用 |
| `discover-fission.md` `trend-selection.md` | 选品方法论 | 通用 |
| `field_mapping.md` | 1688/Ozon → 信封字段映射 | 混合（1688 通用，Ozon 专属段）|

### 1.5 数据层（`data/`）

| 内容 | 说明 |
|---|---|
| URL 列表（wave/urls 系列）| 采集任务输入 |
| `batch_results` `discovery` `logs` `review_log.jsonl` | 运行结果/日志 |
| `cache/` | 缓存（slug/类目/搜索）|

---

## 二、落盘方案

### 2.1 原则

1. **产品视角**：这是「对话即完成」的产品，用户只对话、不操作 skill 命令。落盘是 **agent 的记忆**，不是用户的功能——因此**不设 sync 命令**。
2. **分层落盘**：
   - **配置类**（店铺/规则/类目）→ skill 命令**自动落盘（副作用）**：这是「状态」，改了必须同步，agent 读到的永远最新
   - **结果类**（采集/选品/上架记录）→ **agent 在工作流中落盘**（DAY1-Clean 自动捕获模式）：不是所有中间结果都值得留，agent 判断有价值才落盘
3. **平台无关命名**：目录按能力域组织（stores/sourcing/selection/listing），Ozon 专属集中 `05-ozon/`。知识库目录名为 **`vault/`**（不带 ozon 前缀）。
4. **脱敏**：凭证只存摘要（`client_id` 打码），明文 key 绝不落盘。
5. **与 `references/` 的分工**：`references/` = 静态说明书（命令参考/字段映射/错误码，随 skill 版本、只读）；`vault/` = 动态工作台（店铺配置/采集选品结果/上架记录，随使用更新、可写）。两者互补，agent 都读。

### 2.2 目录结构（`vault/`，dsh 工作区）

```
vault/
├── AGENTS.md              ← agent 规则（自动读，定义「读什么、怎么落盘、quiet mode」）
├── DASHBOARD.md           ← 导航（现在在做什么）
├── 00-System/
│   ├── Boot.md            ← 启动读取清单
│   ├── Active-Context.md  ← 当前状态（正在上架 X）
│   └── Memory-Index.md    ← 索引
├── 01-Stores/             ← 店铺配置（平台无关）
│   └── stores.md          ← set_store/list_stores 自动落盘（脱敏摘要）
├── 02-Sourcing/           ← 1688 货源采集（平台无关）
│   ├── ak-status.md       ← 1688 AK 状态
│   └── products/          ← probe/search 结果（agent 落盘）
├── 03-Selection/          ← 选品（平台无关方法论）
│   ├── rules.md           ← 选品规则（利润率/品牌过滤）
│   └── results/           ← discover 结果（agent 落盘）
├── 04-Listing/            ← 上架（平台无关流程）
│   ├── templates.md       ← 上架模板
│   └── records.md         ← graph/follow 上架记录（task_id → 状态）
└── 05-Ozon/               ← Ozon 专属（唯一带 ozon 命名的）
    ├── categories.md      ← 类目映射（category 自动落盘）
    └── pricing.md         ← 定价参数
```

### 2.3 落盘触发映射（分层：配置自动 / 结果 agent 落盘）

| skill 命令 | 落盘方式 | 落盘到 | 内容 |
|---|---|---|---|
| `set_store` / `list_stores` | **自动（副作用）** | `01-Stores/stores.md` | 店铺列表 + 凭证摘要（脱敏）|
| `set_ak` / `get_ak` / `check` | **自动（副作用）** | `02-Sourcing/ak-status.md` | AK 状态、环境诊断 |
| `category` | **自动（副作用）** | `05-Ozon/categories.md` | Ozon 类目映射 |
| `probe` / `search` | agent 落盘 | `02-Sourcing/products/` | 采集结果（**商品卡片**：图片 URL + 采购价/运费/利润）|
| `discover` / `discover_multi` | agent 落盘 | `03-Selection/results/` | 选品结果（**商品卡片** + 蓝海评分）|
| `graph` / `follow` | agent 落盘 | `04-Listing/records.md` | 上架记录（task_id → 状态）|

> 配置类「自动副作用」的边界：只有「状态型」命令落盘（店铺/凭证/类目），且脱敏。采集/选品/上架这些「结果型」留给 agent 在工作流中判断落盘（参考 DAY1-Clean 自动捕获）。

### 2.4 落盘实现方式（skill 侧新增）

在 skill 的 `scripts/lib/` 新增一个 `vault_writer.py`（通用落盘器），各命令执行后调用：

```python
# 伪代码：set_store 命令落盘
from scripts.lib.vault_writer import write_markdown
write_markdown("01-Stores/stores.md", render_stores(stores))  # 脱敏后的摘要
```

- **不改命令返回**：落盘是副作用，命令的 JSON 输出不变（MCP 工具兼容）
- **vault 路径可配**：`VAULT_DIR` 环境变量，默认指向 dsh 工作区的 `ozon-vault/`
- **幂等**：重复落盘覆盖同一文件，不产生冗余

---

### 2.4 结果卡片化（采集/选品/上架结果 → 商品卡片，对齐原型图）

采集结果的 `product_summary[]` 字段齐全（`purchase_url`/`purchase_cost`/`logistics_cost`/`price`/`profit_rate` + `draft.images[]`/`draft.title`），**落盘成「商品卡片 Markdown」而非纯 JSON**：

```markdown
## 商品卡片：<标题>
![商品图](<draft.images[0]>)

| 字段 | 值 |
|---|---|
| 1688 链接 | <purchase_url> |
| 采购价 | ¥<purchase_cost> |
| 运费预估 | ¥<logistics_cost> |
| Ozon 售价 | ₽<price>（利润率 <profit_rate>）|
```

**两层呈现（互补，都对齐原型图）：**
- **数据卡片**（vault 落盘）：agent 读，一眼看到「哪个产品、采购价、运费、利润」
- **图片卡片**（webui 经营面）：用户看，就是 `ozon-collect-box-proto.png` / `ozon-products-proto.png` 的卡片网格

> **图片渲染的技术现实**：webui 能直接渲染图片 URL（原型图样子）；dsh 对话里 MCP 返回图片是「占位符」（`dsh-mcp-client` 实证），所以 agent 靠「图片 URL + 字段数据」理解，需要「看」图时用视觉工具（modlens / vision-toolkit）。

---

## 三、待确认

1. **落盘方式**（已定）：配置类自动副作用 + 结果类 agent 落盘，不设 sync 命令 ✅
2. **vault 位置**（已定）：独立 `vault/` 目录（平台无关命名），与 `references/` 分工清晰 ✅
3. **能力域名**：`stores/sourcing/selection/listing` + `05-ozon` 这套命名是否 OK？未来加 Amazon 时放 `06-amazon/`？

确认后，我把这份清单 + pounding-mcp 设计 + 落盘方案合并成正式 PRD。
