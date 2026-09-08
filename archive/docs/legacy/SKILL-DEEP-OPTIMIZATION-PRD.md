# Skill 深层优化 PRD — A~G 七项改进

> **版本**：v1.0  
> **日期**：2026-08-06  
> **前置**：基于 `SKILL-ARCHITECTURE-PRD.md` Phase 1 已落地后的最终状态  
> **范围**：仅文档改动，零代码修改  
> **调研方法**：全量精读 SKILL.md（150 行）+ 4 个 references 文件 + cloud_probe.py / batch_test.py / cli.py 代码事实提取

---

## 0. 调研数据摘要

以下数据均从代码中提取，作为 PRD 规格的事实基础：

| 数据项 | 来源 | 关键事实 |
|--------|------|----------|
| submit_envelope 返回值 | cloud_probe.py:471-491 | `{ok, task_id, error, error_code, detail, http_status}` |
| check_task_status 返回值 | cloud_probe.py:2478-2490 | `{task_id, status, ok, terminal, error_message, result_json, retry_count, started_at, completed_at}` |
| product_summary 字段 | batch_test.py:88-93 | `purchase_url, purchase_cost, margin_rate, price, logistics_cost, profit_rate, product_id` |
| 错误码常量 | cloud_probe.py:45-48 | `ERR_CLOUD_UNAVAILABLE, ERR_CLOUD_REJECTED, ERR_CLOUD_TIMEOUT, ERR_CLOUD_FAILED` |
| Chrome CDP 单实例 | chrome_launcher.py:331-332 | file lock 防止双进程启动 Chrome |
| batch_test --delay | batch_test.py:327 | `default=3.0` 秒 |
| 重试退避 | cloud_probe.py:1862,1889 | `retry_delay=15.0`，`wait = 15*(attempt+1) + random(5,15)` |
| trend 降级路径 | cli.py:969-972 | `SEARXNG_URL` 环境变量 → `--market-info` 文件 → 无则警告但仍执行（AI 仅靠品类名） |
| envelope 结构 | envelope_example.json | 顶层 `_说明`(str) / `_单SKU选品`({token,...,envelope:{draft,source,extensions}}) / `_跟卖示例`(同) / `_关键约定`(dict) |
| SKILL.md 行数 | wc -l | 150 行 |
| references 总行数 | wc -l | 308 行（173+58+19+58） |

---

## A. References 触发索引（P1）

### 问题

SKILL.md §5（第 123-128 行）的 references 索引表只有 `| 文件 | 用途 |` 两列：

```
| references/command-reference.md | 各管线完整参数、示例、输入输出 |
| references/error-codes.md | Worker 错误码表 + 进度查询口径 + CLI 错误处理 |
| references/output-schema.md | submit_result / product_summary 字段解析 + 汇报模板 |
| references/env-setup.md | 环境准备 + 凭证 + check 故障排查 + data/ 目录语义 |
```

agent 知道这些文件"是什么"，但不知道"什么时候该去读"。WorkBuddy 渐进式披露的核心是 agent 能自主决定 deep-dive 时机——缺少触发条件，agent 要么全读（浪费上下文），要么不读（漏信息）。

### 改动规格

**文件**：`skill/SKILL.md` §5 索引表  
**改动**：将两列表改为三列表 `| 文件 | 何时读取 | 内容概要 |`

```
| 文件 | 何时读取 | 内容概要 |
|------|----------|----------|
| references/command-reference.md | 选定管线后、执行命令前 → 查完整参数和示例 | 各管线完整参数、输入输出、示例 |
| references/error-codes.md | 命令执行出错、或用户问进度时 → 查错误码和回复模板 | Worker 错误码表 + 进度查询口径 + CLI 错误处理 |
| references/output-schema.md | 命令执行成功、需要向用户汇报结果时 → 查字段解析和汇报模板 | submit_result / product_summary 字段解析 + 汇报模板 |
| references/env-setup.md | 首次使用、check 失败、或用户问凭证配置时 → 查环境准备和故障排查 | 环境准备 + 凭证 + check 故障排查 + data/ 目录语义 |
```

### 验收标准

- [ ] §5 索引表为三列（文件 / 何时读取 / 内容概要）
- [ ] 每个 reference 文件都有明确的"何时读取"触发条件
- [ ] deploy/skill/SKILL.md 同步

---

## B. output-schema.md 补真实 JSON 示例与字段表（P1）

### 问题

当前 output-schema.md 仅 19 行，只有一张字段表格 + 三行汇报模板。agent 拿到 `submit_result` 后：

1. **不知道完整 JSON 长什么样**——无法判断 `ok=false` 时该取 `error` 还是 `error_code` 还是 `detail`
2. **product_summary[] 没有字段表**——只有文字描述"提取 1688链接/利润率/售价/采购价/运费/净利润率/OzonID"，但代码里的真实字段名是 `purchase_url` / `margin_rate` / `price` / `purchase_cost` / `logistics_cost` / `profit_rate` / `product_id`

### 代码事实

**submit_envelope 成功返回**（cloud_probe.py:483）：
```python
return resp.json()  # Worker 返回 {ok: true, task_id: "...", message: "..."}
```

**submit_envelope 失败返回**（cloud_probe.py:471-491）：
```python
{
    "ok": False,
    "error": "reason string",
    "error_code": "TOKEN_INVALID",  # 可能为空字符串
    "detail": {...} or "",
    "http_status": 401,
    "task_id": "task-uuid or None"
}
```

**check_task_status 返回**（cloud_probe.py:2478-2490）：
```python
{
    "task_id": "...",
    "status": "completed|failed|cancelled|pending|running|not_found|worker_unreachable|query_error",
    "ok": bool,          # status == "completed"
    "terminal": bool,    # status in (completed, failed, cancelled)
    "error_message": str|None,
    "result_json": {...}, # Worker result dict，含 product_summary[]
    "retry_count": int,
    "started_at": str|None,
    "completed_at": str|None,
}
```

**product_summary[] 字段**（batch_test.py:88-93）：

| 代码字段名 | 类型 | 示例值 | 展示列名 |
|-----------|------|--------|----------|
| `purchase_url` | str | `https://detail.1688.com/offer/123456` | 1688链接 |
| `purchase_cost` | float | 25.8 | 采购价(¥) |
| `margin_rate` | float | 0.35 | 利润率 |
| `price` | str/float | 890.0 | 售价(RUB) |
| `logistics_cost` | float | 35.0 | 运费(¥) |
| `profit_rate` | float | 0.28 | 净利润率 |
| `product_id` | str | `"123456789"` | Ozon商品ID |

### 改动规格

**文件**：`skill/references/output-schema.md`  
**目标行数**：19 → ~60 行  
**新增内容**：

1. **submit_result 成功 JSON 示例**：
```json
{
  "ok": true,
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "task submitted"
}
```

2. **submit_result 失败 JSON 示例**：
```json
{
  "ok": false,
  "error": "Token invalid or expired",
  "error_code": "TOKEN_INVALID",
  "detail": "",
  "http_status": 401,
  "task_id": null
}
```

3. **check_task_status 返回 JSON 示例**（成功终态）：
```json
{
  "task_id": "550e8400-...",
  "status": "completed",
  "ok": true,
  "terminal": true,
  "error_message": null,
  "result_json": {
    "product_summary": [
      {
        "purchase_url": "https://detail.1688.com/offer/123456",
        "purchase_cost": 25.8,
        "margin_rate": 0.35,
        "price": "890.0",
        "logistics_cost": 35.0,
        "profit_rate": 0.28,
        "product_id": "123456789"
      }
    ]
  },
  "retry_count": 0,
  "started_at": "2026-08-06T10:00:00Z",
  "completed_at": "2026-08-06T10:15:00Z"
}
```

4. **product_summary[] 字段表**（如上表，含代码字段名/类型/示例/展示列名）

5. **成败判定规则**：
```
成功：submit_result.ok == true → 取 task_id → 向用户报告"已提交，任务ID: xxx"
失败：submit_result.ok == false → 取 error + error_code → 查 error-codes.md 对应模板
终态查询：check_task_status.terminal == true → 取 result_json.product_summary → 按 product_summary 字段表汇报
```

### 验收标准

- [ ] 包含 3 个完整 JSON 示例（submit 成功/失败/check_task_status 终态）
- [ ] product_summary[] 字段表含 7 个字段（代码字段名/类型/示例/展示列名）
- [ ] 成败判定规则明确（ok/terminal 怎么判断）
- [ ] deploy/skill/references/output-schema.md 同步

---

## C. 管线 E web_search 降级路径写入 SKILL.md（P1）

### 问题

SKILL.md 管线 E 的描述没有明确写出"agent 无 web_search 能力时怎么办"的降级路径。代码（cli.py:969-972）实际有三层降级：

1. `--market-info` 文件（agent 用 web_search 搜完传文本文件）← 最佳
2. `SEARXNG_URL` 环境变量（自建 SearXNG 实例）← 次佳
3. 都没有 → 警告但仍执行（AI 仅靠品类名生成关键词）← 质量差

error-codes.md 的 CLI 错误处理表已经有"无 web_search 且未配置 SEARXNG_URL"一行，但 SKILL.md 本体的管线 E 说明里没有——agent 可能在没有 web_search 的环境里直接跑 trend，得到低质量结果却不理解原因。

### 代码事实

cli.py:969-972：
```python
searxng = os.environ.get("SEARXNG_URL", "")
info = collect_market_info(args.category, args.market_info, searxng)
if "（未提供市场信息" in info:
    print("⚠️ 未提供市场信息（--market-info 或 SEARXNG_URL），AI 将基于品类名总结，建议先用 web_search 收集趋势结果传入。", flush=True)
```

### 改动规格

**文件**：`skill/SKILL.md` 管线 E 关键规则区  
**新增**：在管线 E 的"关键规则"末尾加一段降级路径：

```markdown
**市场信息获取优先级**：
1. agent 有 web_search 能力 → 搜索品类趋势 → 结果存文本文件 → 传 `--market-info <文件路径>`
2. agent 无 web_search 但环境配置了 `SEARXNG_URL` → trend 命令自动调用
3. 两者都没有 → 向用户说明"趋势选品需要市场信息，当前环境无法自动搜索"
   → 询问用户：(a) 手动提供品类趋势描述文本，或 (b) 退回管线 C 常规选品
   → 不跳过此步直接跑 trend（仅靠品类名生成关键词，选品质量差）
```

### 验收标准

- [ ] SKILL.md 管线 E 有 3 层降级路径
- [ ] 第 3 层明确写"询问用户"而非静默执行
- [ ] deploy/skill/SKILL.md 同步

---

## D. 错误恢复决策树（P2）

### 问题

error-codes.md 有完整的错误码→回复模板表，但没有"出错后下一步做什么"的决策树。agent 告诉用户"凭证无效"之后，是等用户重新设置就重试？还是放弃？还是换商品？

### 代码事实

cloud_probe.py 的错误码分类：

| 常量 | 含义 | retryable | terminal |
|------|------|-----------|----------|
| `ERR_CLOUD_UNAVAILABLE` | Worker 不可达 | 是 | 否 |
| `ERR_CLOUD_TIMEOUT` | Worker 超时 | 是（retryable=True） | 否 |
| `ERR_CLOUD_REJECTED` | Worker 拒绝 | 5xx→是, 4xx→否 | 5xx→否, 4xx→是 |
| `ERR_CLOUD_FAILED` | Worker 处理失败 | 否 | 是 |

Worker 端返回的 error_code（从 submit_envelope 解析）：
- `TOKEN_INVALID` / `TOKEN_MISSING` → 凭证问题
- `INSUFFICIENT_BALANCE` → 余额不足
- `RATE_LIMITED` → 限流
- `INVALID_REQUEST` → 请求参数错误
- 其他 → 看 http_status 和 detail

### 改动规格

**文件**：`skill/references/error-codes.md`  
**位置**：错误码表之后，新增一节  
**新增内容**：

```markdown
## 错误恢复决策

出错后按以下决策表执行下一步：

| 错误码/场景 | 下一步动作 | 自动重试? |
|-------------|-----------|-----------|
| TOKEN_INVALID / TOKEN_MISSING | 引导用户执行 `set_token` → 等用户确认 → 重试原命令 | 否，等用户 |
| INSUFFICIENT_BALANCE | 告知用户余额不足需充值 → 不重试 | 否 |
| RATE_LIMITED | 告知用户被限流 → 等待 60s → 可自动重试一次 | 是，1 次 |
| INVALID_REQUEST | 检查 1688 商品页是否正常 → 换商品或修正参数后重试 | 否 |
| SERVICE_UNAVAILABLE (5xx) | 告知用户服务暂不可用 → 等 5 分钟 → 可自动重试一次 | 是，1 次 |
| 网络错误 / Worker 不可达 | 检查网络 → 重试一次 → 仍失败则告知用户检查 Worker 状态 | 是，1 次 |
| 未知错误码 | 取 error + detail 字段告知用户 → 不重试 → 建议联系维护者 | 否 |

**重试规则**：
- 自动重试最多 1 次，重试前等待对应秒数（RATE_LIMITED: 60s, SERVICE_UNAVAILABLE: 300s, 网络: 即时）
- 重试仍失败 → 告知用户最终结果，不继续重试
- 用户手动重试无次数限制
```

### 验收标准

- [ ] error-codes.md 新增"错误恢复决策"节
- [ ] 覆盖至少 7 种错误场景
- [ ] 每种场景有"下一步动作"和"是否自动重试"
- [ ] 重试规则明确（最多 1 次 + 等待时间）
- [ ] deploy/skill/references/error-codes.md 同步

---

## E. 并发与限流指引（P2）

### 问题

SKILL.md 和 command-reference.md 均未说明 agent 能否同时跑多个命令。用户说"帮我上架这 3 个链接"时，agent 可能并行跑 3 个 `graph`，导致 Chrome CDP 冲突。

### 代码事实

| 限制项 | 证据 | 说明 |
|--------|------|------|
| Chrome CDP 单实例 | chrome_launcher.py:331-332 `file lock` | 防止双进程同时启动 Chrome |
| CDP 重试退避 | cloud_probe.py:1862,1889 | `retry_delay=15.0`，`wait = 15*(attempt+1) + random(5,15)` |
| batch_test 间隔 | batch_test.py:327 | `--delay default=3.0` 秒 |
| 1688 CAPTCHA | cloud_probe.py:2158 | 高频访问触发"验证码拦截" |

### 改动规格

**文件**：`skill/references/command-reference.md`  
**位置**：文件开头，命令速查表之前  
**新增内容**：

```markdown
## 并发限制

| 资源 | 限制 | 影响 |
|------|------|------|
| Chrome CDP | 单实例（file lock） | graph / follow / image_search / probe 不可并行，必须串行 |
| 1688 API | 有每分钟配额，高频调用触发 CAPTCHA | 连续快速调用会被"验证码拦截" |
| Worker 提交 | 可并行（队列消费） | 但建议间隔 2-3 秒避免突发 |
| batch_test | 已内置 --delay（默认 3s） | 无需手动控制间隔 |

**批量操作规则**：
- 用户要求批量上架多个链接时 → 使用 `batch_test`（内置串行 + 间隔），不自行并行多个 `graph`
- 用户要求批量选品时 → 使用 `discover` 一次调用（内部批量），不并行多个 `discover`
- 用户要求同时选品 + 上架时 → 先完成选品 → 再执行上架，不交叉并行
```

### 验收标准

- [ ] command-reference.md 开头有"并发限制"节
- [ ] 覆盖 4 种资源限制（CDP / 1688 API / Worker / batch_test）
- [ ] 有"批量操作规则"明确禁止自行并行
- [ ] deploy/skill/references/command-reference.md 同步

---

## F. envelope_example.json 读取说明（P2）

### 问题

SKILL.md §5 第 127 行写：
```
| envelope_example.json | 完整信封结构示例（单 SKU + 跟卖两种模式） |
```

但 envelope_example.json 的顶层 key 是 `_说明`(str)、`_单SKU选品`(dict)、`_跟卖示例`(dict)、`_关键约定`(dict)，实际信封数据嵌套在 `_单SKU选品.envelope` 和 `_跟卖示例.envelope` 下。agent 如果直接读顶层 key 会误以为 `_说明` 是信封字段。

### 代码事实

envelope_example.json 顶层结构：
```
_说明: "信封结构参考 — Agent 组装 GraphInput 时参考此文件"
_单SKU选品: {token, ozon_client_id, ozon_api_key, envelope: {draft, source, extensions}}
_跟卖示例: {token, ozon_client_id, ozon_api_key, envelope: {draft, source, extensions}}
_关键约定: {单产品上传, purchase_cost, dimensions_单位, weight_单位, 图片}
```

### 改动规格

**文件**：`skill/SKILL.md` §5 索引表  
**改动**：envelope_example.json 行的描述补充读取路径：

```
| envelope_example.json | 信封结构示例。读取路径：`_单SKU选品.envelope`（直采）或 `_跟卖示例.envelope`（跟卖）；`_说明`/`_关键约定` 是文档说明非数据字段 |
```

### 验收标准

- [ ] §5 索引表 envelope_example.json 行包含读取路径
- [ ] 明确标注 `_说明`/`_关键约定` 不是数据字段
- [ ] deploy/skill/SKILL.md 同步

---

## G. 子 Skill 拆分量化信号与路线（未来路线，不执行）

### 背景

当前 SKILL.md（150 行）+ references（308 行）= 458 行总量。PRD `SKILL-ARCHITECTURE-PRD.md` Phase 2 提出可选拆分子 Skill，但未给量化判断标准。

### 当前状态

| 指标 | 当前值 | 评估 |
|------|--------|------|
| SKILL.md 行数 | 150 行 | ✅ 远低于 200 行警戒线 |
| references 总行数 | 308 行 | ✅ 可接受 |
| 总量 | 458 行 | ⚠️ 接近 500 行关注线 |
| 选品/上架命令分界 | 意图路由 §3 有明确分界 | ✅ 清晰 |
| 跨管线依赖 | discover→graph, follow→graph(降级), batch_test 混合 | ⚠️ 高耦合 |

### 拆分触发信号（满足任意一个即值得拆）

| 信号 | 阈值 | 当前值 | 达标? |
|------|------|--------|-------|
| 上下文体积 | SKILL.md + references > 800 行 | 458 行 | ❌ 未达 |
| 路由混淆率 | 选品/上架命令在实测中混淆 > 15% | 待实测 | ❓ 未知 |
| 会话模式 | 同一会话只选品不上架比例高 → 选品 references 白占上下文 | 待实测 | ❓ 未知 |

### 拆分方案（触发后执行）

```
主 skill: pounding-ozon-probe
  ├─ graph / follow / batch_test / check / get_ak / list_stores
  ├─ references/: env-setup, output-schema, error-codes, command-reference(上架部分)
  └─ 共享: data/config/, data/logs/

配套 skill: pounding-ozon-discovery
  ├─ discover / trend / search / image_search / probe
  ├─ references/: command-reference(选品部分)
  └─ scripts/ → 软链接到主 skill 的 scripts/
```

### 当前结论

**不拆**。当前 458 行在合理范围内，且跨管线依赖高（discover 选完品调 graph 上架、follow 降级走 graph）。等 Phase 1 上线后收集实测数据（路由混淆率、会话模式），三个信号满足任意一个再启动拆分。

### 验收标准

- [ ] PRD 文档记录了 3 个量化信号和阈值
- [ ] 记录了拆分方案（主 skill + 配套 skill 的命令划分）
- [ ] 当前结论为"不拆"并说明原因

---

## 实施计划

### 优先级与依赖

```
A (references 触发索引) ─┐
B (output-schema 补全)  ─┤── P1，可并行，互不依赖
C (管线 E 降级路径)     ─┘
                          
D (错误恢复决策树) ─┐
E (并发限流指引)   ─┤── P2，可并行，互不依赖
F (envelope 读取)  ─┘

G (子 Skill 路线) ──── 未来路线，不执行
```

### 改动清单

| 序号 | 改动项 | 文件 | 新增/修改 | 行数变化 | 优先级 |
|------|--------|------|-----------|----------|--------|
| A | references 索引表加"何时读取"列 | `skill/SKILL.md` | 修改 §5 表格 | +4 行 | P1 |
| B | 补 JSON 示例 + product_summary 字段表 + 成败判定 | `skill/references/output-schema.md` | 大幅扩充 | +41 行 | P1 |
| C | 管线 E 加 web_search 降级路径 | `skill/SKILL.md` | 新增段落 | +6 行 | P1 |
| D | 加错误恢复决策表 | `skill/references/error-codes.md` | 新增节 | +15 行 | P2 |
| E | 加并发限制与批量操作规则 | `skill/references/command-reference.md` | 新增节 | +12 行 | P2 |
| F | envelope_example.json 行补读取路径 | `skill/SKILL.md` | 修改一行 | +1 行 | P2 |
| G | 记录子 Skill 拆分信号 | 本 PRD 文档 | 已完成 | - | 未来 |

### 同步要求

每次改动后必须同步到 `deploy/skill/` 对应文件：
```bash
cp skill/SKILL.md deploy/skill/SKILL.md
cp skill/references/output-schema.md deploy/skill/references/output-schema.md
cp skill/references/error-codes.md deploy/skill/references/error-codes.md
cp skill/references/command-reference.md deploy/skill/references/command-reference.md
```

### 验收 Checklist

- [ ] A: SKILL.md §5 为三列表（文件/何时读取/内容概要）
- [ ] B: output-schema.md 有 3 个 JSON 示例 + product_summary 7 字段表 + 成败判定规则
- [ ] C: SKILL.md 管线 E 有 3 层降级路径，第 3 层明确"询问用户"
- [ ] D: error-codes.md 有错误恢复决策表（7 场景 + 重试规则）
- [ ] E: command-reference.md 有并发限制节（4 资源 + 批量规则）
- [ ] F: SKILL.md §5 envelope 行含读取路径 + 标注非数据字段
- [ ] G: PRD 记录了 3 个拆分信号 + 拆分方案 + 当前结论
- [ ] 全部文件 deploy/skill/ 同步完成
