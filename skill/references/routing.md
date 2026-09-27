# 意图路由与全局约束

> v0.79 拆分自 command-reference.md（PLAN-agent-ergonomics-v1 D3）：怎么选管线/命令的全局规则。
> 各命令完整参数见 commands-listing.md / commands-discovery.md / commands-ops.md。

## 目录
- [意图路由决策树](#意图路由决策树)
- [提交确认二分法（统一口径）](#提交确认二分法统一口径)
- [并发限制](#并发限制)
- [多店铺（P2-8）](#多店铺p2-8)
- [to-box vs auto-submit 出货路径](#to-box-vs-auto-submit-出货路径所有选品管线通用)

## 意图路由决策树

> 先判断用户意图，再选管线；每次操作前重新判断，不因上下文惯性选择。

```
用户输入
 ├─ ① 有 URL？ → 先判 URL 类型：
 │    商品页 detail.1688.com/offer/…  → 【管线 A】1688 直接上架
 │    商品页 ozon.ru/product/…        → 【管线 B】Ozon 跟卖
 │    搜索页/类目页（ozon.ru/search 或 /category/）→ 【管线 C】discover --url 采集该页
 │    多 URL / "批量处理这些"         → 【管线 F】batch_test 批量
 ├─ ② 有图片（无 URL）？             → 【管线 D1】image_search 以图搜款
 │    图搜出候选 → 展示让用户确认哪一款 → 再 graph 上架（不允许图搜后直接上架）
 ├─ ③ 无 URL → 按意图词优先级：
  │    "趋势/热卖/新品风向/爆款" + 品类 → 趋势选品：agent 先 web_search + LLM 提炼
  │      细分关键词 → discover --keyword <细分关键词>（见 trend-selection.md）
  │    "跟卖/找能跟卖的"              → 【管线 C】discover 跟卖选品
  │    "自动采集/无人值守/任务式/自动跑一批选品" + 品类或入口页
  │                                   → 【管线 C2】discover-task（缺省 ai 档粗筛+干跑；
  │                                     `--to-box` 入采集箱，见 commands-discovery.md）
  │    "找更多同类/挖同行货源/顺着卖家找" → 【管线 C】discover 裂变选品（`--fission`，
  │      见 discover-fission.md）
  │    "上架/上货/上点/整一批/发布/上传" → 【管线 D】discover 选品上架
  │    "蓝海" → 默认 【管线 C】（蓝海评分体系在 C）；用户明确说"蓝海趋势/市场分析"才走趋势选品
  │    "选品/选 N 个" 无修饰          → 追问（跟卖 or 上架 or 趋势选品）+ 品类
  │    "有什么好卖的/卖得动"          → 追问（趋势选品 or 跟卖推荐）+ 品类
  │    "搜索/搜一下/查一下/找货源"（无 URL）→ 1688 词搜 search（见 commands-listing.md）
  │    "查类目/类目"                 → category（只读类目查询，见 commands-discovery.md）
  │    "检查/诊断/环境/凭证"         → check（环境诊断，见 commands-ops.md）
  │    机器路由优先级以 router.py 词表顺序为准：趋势→类目→环境→上架→任务式→
  │    跟卖/蓝海/选品/采集→搜索；本树是人读指南，两者冲突时以实测 router 输出为准
 ├─ ③a 无对象：上架/选品 但无 URL/图/关键词 → 先追问：发链接或图片(→A/D1)，
 │    还是给品类让我选品(D)。禁止直接中国站兜底采集
 ├─ ④ 问店铺商品状态/被拒原因        → 引导用户在 Ozon 卖家后台查看（工具不直接查询）
 └─ ⑤ 指代不清 / 数量不符 / 重上已上商品
      （"类似的""这个""它"；声称 5 个只发 2 个；"昨天那个再上一遍"）
      → 必须追问核对 + 检查是否已上过（防重复提交），禁止猜测
```

## 提交确认二分法（统一口径）

> v0.79（PLAN-agent-ergonomics-v1 B）：取代旧的四处分裂口径。CLI 缺省行为不变——graph 缺省提交、follow 缺省展示。

| 意图强度 | 用户话术示例 | 动作 |
|---|---|---|
| **明确上架意图** | 发链接 + "上架/跟卖/整一批/发布"，或会话中已确认过提交 | `graph` / `follow --auto-submit` 直接提交，建议带 `--wait` 到终态，不追问 |
| **弱意图** | "看看这个 / 能不能上 / 多少钱 / 评估一下" | `graph --no-submit` / `follow`（不带 `--auto-submit`）展示信封+预估，等用户说提交 |
| **选品类** | discover/search/discover-task | 双出口纪律：没说 `--to-box` 还是直上就先问；`--auto-submit` 恒须确认 |

## 并发限制

> 各命令 × 各门禁（heavy 闸/preflight/min-margin/min-density/--wait）与出口码 0-4 的速查矩阵在
> SKILL.md §2「门禁速查矩阵」——此处只列并发资源限制。

| 资源 | 限制 | 影响 |
|------|------|------|
| 重命令串行闸 | `discover` / `discover-multi` / `discover-task` / `graph` / `follow` / `seller` 六命令跨进程互斥（`data/locks/heavy_cdp.lock`） | 闸被占 **exit 4**（报错含占用命令/PID/已运行时长）；`--wait` 排队（每 30s 心跳）/ `--force` 强制并行（互踩 Chrome/缓存，慎用） |
| Chrome CDP | 单实例（file lock，chrome_launcher.py）；探活三态（up/refused/busy）——繁忙（其他进程正在用）按就绪等待恢复，绝不杀；仅确认端口无人监听才杀带调试端口的实例重启 | graph / follow / image_search / probe 不可并行，必须串行 |
| 1688 API | 有每分钟配额，高频调用触发验证码拦截（cloud_probe.py:2158） | 连续快速调用会被"验证码拦截" |
| Worker 提交 | 可并行（队列消费） | 但建议间隔 2-3 秒避免突发 |
| batch_test | 已内置 `--delay`（默认 3.0s，batch_test.py:327） | 无需手动控制间隔 |

**批量操作规则**：
- 用户要求批量上架多个链接时 → 使用 `batch_test`（内置串行 + 间隔），不自行并行多个 `graph`（并行也会被串行闸 exit 4 拦下）
- 用户要求批量选品时 → 使用 `discover` 一次调用（内部批量），不并行多个 `discover`（串行闸直接拦）
- 用户要求同时选品 + 上架时 → 先完成选品 → 再执行上架，不交叉并行（串行闸会排队，`--wait` 显式排队体验更好）
- agent 见 exit 4 → 告知用户占用方信息并等它跑完（或 `--wait` 排队），**勿盲目 `--force`**

## 多店铺（P2-8）

**stores.json 格式**（`skill/data/config/stores.json`，`set_store` 自动写入）：

```json
{
  "default": "主店铺",
  "stores": {
    "主店铺": {"client_id": "4718259", "api_key": "...", "currency": "CNY", "fx_rate": 0.10},
    "副店铺": {"client_id": "5371047", "api_key": "...", "currency": "RUB"}
  }
}
```

- 顶层 `"default"` 是**指针**（声明的默认店铺名）；`"stores"` 是店铺字典。
- `set_store --name <店铺名> --client-id <ID> --api-key <KEY> [--currency CNY|RUB]` 新增/更新店铺；**第一个店铺自动成为默认**。
- `list_stores` 查看全部店铺（client_id 打码）。改默认店铺：手动编辑 stores.json 的 `"default"` 字段（当前无 CLI 命令，首个店铺自动设为默认）。

**`--store` 指定店铺**：graph / follow / discover / batch_test（`--store-id`）均支持，决定**凭证 + 定价参数**（`currency`/`margin_rate`/`commission_rate`/`fx_buffer`/`fx_rate`）来源；省略时用默认店铺。用户提到"某某店铺"时必须显式传 `--store`，不要默认用默认店铺。

**POUNDING_OZON_STORE（遗留）**：该环境变量只切换 `runtime_config.{profile}.json`（旧运行时配置），**不参与 stores.json 多店铺选择**——多店铺只认 `--store` / stores.json 的 `"default"` 指针。

**⚠️ 名为 "default" 的店铺歧义**：若某店铺恰好叫 `"default"`，它与顶层 `"default"` 指针字段同名冲突。解析规则（config_store.py `get_store`，v0.38 P2-8）：**指针字段优先**——`get_store("")` 按指针解析（指针指向谁就用谁），并在解析默认时**告警一次**提示重命名该店铺。建议店铺名避免使用 `"default"`。

**store_id 全链路透传**（已实测）：`cmd_discover` → `build_envelope_from_discovery(store_id)` → 信封 `extensions.store_id`；`cmd_follow` → `follow_sell_cloud(store_id)`（follow 缓存 key = `product_id:store_id`，不同店铺不串缓存）；`cmd_graph` → `build_graph_envelope_with_retry(store_id)`；`batch_test` → `_resolve_credentials` 按 `--store-id` 解析凭证。

## to-box vs auto-submit 出货路径（所有选品管线通用）

> v0.70 起双出口全量成立：`search`（1688 词搜）/ `discover` / `discover-multi` /
> `discover-task` / `follow` / `graph` 都同时支持 `--to-box` 与 `--auto-submit`
> （互斥，二选一）。**用户没说走哪条就先问**。

| | `--to-box`（采集箱） | `--auto-submit`（直接上架） |
|---|---|---|
| 落点 | 采集箱草稿（`POST /api/v1/drafts`） | Worker 上架任务（`submit_task`） |
| 后续 | WebUI 采集箱人工认领 → 点提交才上架 | 直接进 worker 管线真实上架 |
| 可逆性 | 可逆（草稿可删/可改后再提交） | 不可逆（真实创建商品卡） |
| 信封差异 | 带 `extensions.discovery_meta`（蓝海分/月销/利润率/匹配置信度，采集箱列表与 CSV 可见） | 同样带 discovery_meta |
| agent 策略 | 可自动执行（安全默认） | **必须确认**（等用户说"提交"） |
