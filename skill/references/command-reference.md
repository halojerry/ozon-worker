# 全命令参考

> 各管线的触发条件、完整参数、示例、输入输出。速查表见 SKILL.md §2。
> 所有命令都是黑盒：先 `--help` 看用法，勿读 `cli.py` 源码。

## 目录
- [意图路由决策树](#意图路由决策树)
- [并发限制](#并发限制)
- [多店铺（P2-8）](#多店铺p2-8)
- [管线 A：1688 上架（graph）](#管线-a1688-上架graph)
- [管线 B：Ozon 跟卖（follow）](#管线-bozon-跟卖follow)
- [管线 C：跟卖选品（discover，Discover v2）](#管线-c跟卖选品discoverdiscover-v2)
- [管线 C 增强：任务式全自动选品（discover-task，漏斗 v2）](#管线-c-增强任务式全自动选品discover-task漏斗-v2)
- [管线 C 增强：to-box vs auto-submit 出货路径对比](#管线-c-增强to-box-vs-auto-submit-出货路径对比)
- [管线 D：选品上架](#管线-d选品上架)
- [管线 E：趋势选品](#管线-e趋势选品agent-自主分析--discover-执行v031-起)
- [批量处理（batch_test.py）](#批量处理batch_testpy)
- [任务查询（query）](#任务查询query)
- [卖家店铺分析（seller）](#卖家店铺分析seller)
- [what-to-sell 蓝海/榜单查询（queries）](#what-to-sell-蓝海榜单查询queries)
- [自动更新（update）](#自动更新update)
- [磁盘清理（cleanup）](#磁盘清理cleanup)
- [其他命令（辅助）](#其他命令辅助)
- [settings.json 可调参数](#settingsjson-可调参数)

## 意图路由决策树

> 从 SKILL.md §1 迁移。先判断用户意图，再选管线；每次操作前重新判断，不因上下文惯性选择。

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
  │      细分关键词 → discover --keyword <细分关键词>（见 references/trend-selection.md）
  │    "跟卖/找能跟卖的"              → 【管线 C】discover 跟卖选品
  │    "自动采集/无人值守/任务式/自动跑一批选品" + 品类或入口页
  │                                   → 【管线 C2】discover-task（缺省 ai 档粗筛+干跑；
  │                                     `--to-box` 入采集箱，见下方专节）
  │    "找更多同类/挖同行货源/顺着卖家找" → 【管线 C】discover 裂变选品（`--fission`，
  │      见 references/discover-fission.md）
  │    "上架/上货/上点/上产品/整一批"  → 【管线 D】discover 选品上架
  │    "蓝海" → 默认 【管线 C】（蓝海评分体系在 C）；用户明确说"蓝海趋势/市场分析"才走趋势选品（agent 分析 + discover）
  │    "选品/选 N 个" 无修饰          → 追问（跟卖 or 上架 or 趋势选品）+ 品类
  │    "有什么好卖的/卖得动"          → 追问（趋势选品 or 跟卖推荐）+ 品类
 ├─ ③a 无对象：上架/选品 但无 URL/图/关键词 → 先追问：发链接或图片(→A/D1)，
 │    还是给品类让我选品(D)。禁止直接中国站兜底采集
 ├─ ④ 问店铺商品状态/被拒原因        → 引导用户在 Ozon 卖家后台查看（工具不直接查询）
 └─ ⑤ 指代不清 / 数量不符 / 重上已上商品
      （"类似的""这个""它"；声称 5 个只发 2 个；"昨天那个再上一遍"）
      → 必须追问核对 + 检查是否已上过（防重复提交），禁止猜测
```

## 并发限制

| 资源 | 限制 | 影响 |
|------|------|------|
| Chrome CDP | 单实例（file lock，chrome_launcher.py:331） | graph / follow / image_search / probe 不可并行，必须串行 |
| 1688 API | 有每分钟配额，高频调用触发验证码拦截（cloud_probe.py:2158） | 连续快速调用会被"验证码拦截" |
| Worker 提交 | 可并行（队列消费） | 但建议间隔 2-3 秒避免突发 |
| batch_test | 已内置 `--delay`（默认 3.0s，batch_test.py:327） | 无需手动控制间隔 |

**批量操作规则**：
- 用户要求批量上架多个链接时 → 使用 `batch_test`（内置串行 + 间隔），不自行并行多个 `graph`
- 用户要求批量选品时 → 使用 `discover` 一次调用（内部批量），不并行多个 `discover`
- 用户要求同时选品 + 上架时 → 先完成选品 → 再执行上架，不交叉并行

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

## 管线 A：1688 上架（graph）

**触发**：用户消息含 `1688.com` 链接，或管线 B 降级

```bash
python3 scripts/cli.py graph --url "https://detail.1688.com/offer/xxx.html" --store "主店铺"

# 只组装信封不提交（调试/确认场景，不会上架）
python3 scripts/cli.py graph --url "https://..." --store "主店铺" --no-submit

# 用商品 ID（无 URL 时）+ 指定 Ozon 类目俄语关键词
python3 scripts/cli.py graph --item-id "980815374096" --category-query "поилка" --store "主店铺"

# 复用 Ozon 竞品参考链接（同类目属性，提升属性填充准确率）
python3 scripts/cli.py graph --url "https://..." --store "主店铺" --ozon-ref-url "https://www.ozon.ru/product/xxx/"
```

- **输入**：1688 商品 URL、店铺名
- **参数**：
  - `--url` / `--item-id`（二选一）：1688 商品链接 或 商品 ID
  - `--store`：Ozon 店铺名（定价/凭证来源），省略时用默认店铺
  - `--category-query`：Ozon 类目俄语关键词（帮助类目匹配，可选）
  - `--retries`：CDP 抓取重试次数（默认 3）
  - `--no-submit`：只组装信封不提交 Worker（调试）
  - `--ozon-ref-url`：Ozon 竞品参考链接（v0.29.x）——抓该竞品同类目属性复用，属性填充更准（可选）
  - `--notify`：提交时 GraphInput 顶层携带 `notify=True`，Worker 完成推送 webhook（需 Worker 配置 `TASK_NOTIFY_URL`）
- **输出**：JSON `{summary, envelope, submit_result}`（字段解析见 output-schema.md）
- **自动完成**：CDP 抓取 1688 → 组装信封 → 提交 Worker
- **Agent 决策**：用户给了 1688 URL → 直接执行（决策边界 §3 自动类）；`--no-submit` 用于"看看/评估/能不能上"场景，展示信封等用户确认后再提交
- **⚠️ SKU 去重（v0.38 N1）**：同店铺同商品已有活跃任务（pending/running）时重复提交返回 409 `DUPLICATE_SUBMIT`。去重键含店铺维度 `{user}:{store}:{product}`——**同用户不同店铺可提交同款**（互不拦截）。终态任务（completed/failed/rejected/cancelled）不占用去重名额，可重新提交。收到 `DUPLICATE_SUBMIT` 时用 `query <task_id>` 查既有任务状态，而非反复重提
- **执行后验证**：① `--no-submit` → 对照 `envelope_example.json` 检查信封字段完整性（title/images/weight/dimensions/purchase_cost 必填）再提交；② 已提交 → 记录返回的 `task_id`，主动 `query <task_id> --watch` 跟踪，终态后再向用户汇报（勿让用户盲等）

**多店铺**：`--store` 指定店铺名（`data/config/stores.json` 的 key）；省略用 `default` 字段指向的默认店铺。不同店铺各自持有独立凭证/定价参数（`set_store --currency` 可配 RUB/CNY 店铺），提交时按店铺取凭证与 `fx_rate`。用户提到"某某店铺"时必须显式传 `--store`，不要默认用默认店铺。

## 管线 B：Ozon 跟卖（follow）

**触发**：用户消息含 `ozon.ru` 商品链接

```bash
python3 scripts/cli.py follow --ozon-url "https://www.ozon.ru/product/xxx/" --store "主店铺" --auto-submit

# 人工评审：展示全部 1688 候选，人工接受/改选/拒绝后组装（不自动挑）
python3 scripts/cli.py follow --ozon-url "https://www.ozon.ru/product/xxx/" --store "主店铺" --auto-submit --review

# 完成时推送 webhook 通知（需 Worker 配置 TASK_NOTIFY_URL）
python3 scripts/cli.py follow --ozon-url "https://www.ozon.ru/product/xxx/" --store "主店铺" --auto-submit --notify
```

- **输入**：Ozon 商品 URL、店铺名
- **参数**：
  - `--ozon-url`（必填）：Ozon 商品页 URL
  - `--store`：Ozon 店铺名
  - `--auto-submit`：自动提交 Worker（不加则只组装不提交）
  - `--review`：人工评审暂停（v0.38）——展示全部 1688 候选，人工接受/改选/拒绝，决策写 review_log
  - `--notify`：提交时 GraphInput 顶层 `notify=True`，Worker 完成推 webhook
- **输出**：JSON `{success, product_id, slug, images, title, 1688_matches, task_id}`
- **自动完成**：CDP 抓取 Ozon → 图搜 1688 同款 → 组装信封 → 提交 Worker
- **执行后验证**：① 图搜 `1688_matches` 为空且 `no_relevant_match=true` → 告知用户"1688 未找到同款"，不提交空壳，询问是否换货源或改关键词；② 已提交 → `query <task_id> --watch` 跟踪，`rejected`（审核被拒）时按 error-codes.md 引导用户看 Ozon 卖家后台拒绝原因

**跟卖双模式（`extensions.follow_type`，v0.22 起）**：

| 模式 | 说明 | 适用 |
|------|------|------|
| `hand`（默认） | 防侵权——跳过 import-by-sku 1:1 复制，走 CREATE 重建（管线重做类目/属性/生图，天然防同款/侵权检测） | Skill 找到 1688 货源 |
| `api` | import-by-sku 复制竞品卡 | Skill 无货源时由 Worker 自动降级 |

- **offer_id 约定**：统一 `follow_{竞品ID}`（import-by-sku / assemble / prepare 三处一致，防 api 模式双卡）
- **自动降级**：Skill 无 1688 货源 → Worker hand 模式自动降级 api 复制；图搜 `no_relevant_match`（相关性护栏拒绝）→ **直接拦截不组装**（v0.26 起，不提交空壳）
- **Agent 决策**：用户给了 Ozon URL → 直接执行；跟卖走 Worker 跟卖管线（`follow_sell=true`），与管线 A 直采上架不同

**降级（DataDome 拦截）**：Ozon 页面禁止复制时：
1. 用 Ozon Widget API 获取产品信息
2. 用产品图片在 1688 图搜同款
3. 走管线 A（直采重建，非跟卖复制——offer_id/定价都会变，须向用户说明）

## 管线 C：跟卖选品（discover，Discover v2）

**触发**：用户说"有什么好产品可以跟卖"、"帮我找可以跟卖的"（无 URL）

```bash
# ① 有关键词：搜索 → 全量采集 → 表格展示 → 交互挑选 → 批量找货源
python3 scripts/cli.py discover --keyword "宠物用品"

# ② 无关键词：直接打开 Ozon 中国站（highlight 页）滚动懒加载采集
python3 scripts/cli.py discover --max-products 30

# ③ 自动筛选规则（跳过交互）：月销量≥200 且 广告占比≤30% 且 跟卖≤20
python3 scripts/cli.py discover --keyword "宠物用品" --rules "monthly_sales>=200,drr<=30,seller_count<=20"

# ④ 价格区间过滤（RUB）：区间外产品标记 ⏭️价区间外，不参与挑选/运营指标查询
python3 scripts/cli.py discover --keyword "收纳" --min-price 300 --max-price 2000

# ⑤ 指定页面 URL 直接采集（搜索页/类目页）
python3 scripts/cli.py discover --url "https://www.ozon.ru/search/?text=собака"

# ⑥ 挑选 + 货源后确认提交 Worker
python3 scripts/cli.py discover --keyword "宠物用品" --auto-submit

# ⑦ 不查 seller.ozon.ru 运营指标（未登录卖家后台时自动降级，无需手动加）
python3 scripts/cli.py discover --keyword "宠物用品" --no-analytics

# ⑧ 蓝海增强数据源（v0.33 C4）：实时 what_to_sell 查询反哺蓝海评分（需 --keyword + seller 登录；未登录自动降级 CSV）
python3 scripts/cli.py discover --keyword "宠物用品" --blue-ocean-source queries

# ⑨ 人工评审暂停（v0.38）：弱匹配候选逐个确认（y=接受 / N=拒绝 / a=全部 / s=跳过），决策写 review_log
python3 scripts/cli.py discover --keyword "宠物用品" --blue-ocean-source queries --review

# ⑩ 主站搜索（默认中国站 highlight 页；--china 是隐藏反向别名）
python3 scripts/cli.py discover --keyword "宠物用品" --local
```

- **输入**：搜索关键词 或 Ozon 页面 URL 或 无（→ 中国站懒加载）
- **流程（v2，先采集后分析）**：
  1. **采集**：默认中国站——有 `--keyword` → 中国站 highlight 页内搜索（`CHINA_HIGHLIGHT_URL?text=`）；`--local` → 主站 `/search/?text=`；有 `--url` → 直接采集该页；都无 → 中国站 highlight 页滚动懒加载。结果容器限定（`.tile-root`），滚动到底部触发懒加载 + 等待渲染 + 翻页 + 去重
  2. **全量数据**：widget API（价格/标题/图/品牌/评分/评论数）+ 跟卖数/最低价 + **seller.ozon.ru 运营指标**（月销量/增长率/广告占比/上架天数——需卖家后台已登录，未登录自动降级，表格运营列显示 `—`）
  3. **表格分析挑选**：全量表格展示（含拒绝原因/状态）→ 人工按序号挑选 或 `--rules` 自动筛选 —— **此时不花 1688 配额**
  4. **批量货源**：只对选中的产品 1688 识图（CDP 图搜 → AK 图搜 → AK 关键词三级，含重试）→ 利润计算（真实重量/佣金）→ 蓝海评分 → 确认 → 提交
- **输出**：候选产品列表（全量落盘 `data/discovery/`，CSV 可导出）
- **规则字段**（两段式，v0.69 起）：`monthly_sales / gmv / drr / seller_count / price / create_days / sales_growth / rating` 为**挑选期**字段（1688 匹配前判定）；`margin` 为**匹配期**字段（匹配后二次筛选——匹配前恒 0.0 无从判定）
- **其他参数**：
  - `--rules`：自动筛选规则（跳过交互），逗号分隔，如 `"monthly_sales>=200,drr<=30,seller_count<=20"`；**两段式**：挑选期规则在前、匹配期规则在后（如 `"ai,margin>=20"`）；**`"ai"` = 上品帮 AI 预设**（上架≤365d/跟卖≤30/月动态>0/DRR≤15 + 价格分档月销下限），可作逗号项与其他规则混写
  - `--export csv|json|both` + `--output <路径>`：导出全量+选中结果
  - `--brand-filter`：`nobrand`（默认，只要无品牌/白牌）/ `known`（只过滤知名品牌黑名单）/ `all`（不过滤）
  - `--fx-rate`：RUB→CNY 汇率，显式指定时优先；缺省按 店铺 `stores.json` 的 `fx_rate` → `settings.json` 的 `fx_rate` → 0.075 解析（P2-6：卢布波动时在店铺/全局配置中调整，避免利润估算失真）
  - `--local`：主站搜索（默认中国站 highlight 页内搜索）；`--china`（隐藏别名，`argparse.SUPPRESS`，等价默认中国站）
  - `--blue-ocean-source csv|queries` + `--blue-ocean-csv <path>`：蓝海增强数据源（v0.33 C4）——`csv` 用本地 all-queries CSV 反哺蓝海评分（默认找 `/tmp/queries_all.csv`，无数据降级原流程）；`queries` 实时 what_to_sell 查询（需 `--keyword` + seller 登录，未登录/异常静默降级 CSV）
  - `--review`：人工评审暂停（v0.38）——弱匹配候选逐个确认（`y`/`N`/`a`=全部/`s`=跳过），决策写入 review_log；settings.json `visual_review: true` 可全局开启
  - `--notify`：提交时 GraphInput 顶层 `notify=True`，Worker 完成推 webhook
  - `--filter-profile off|ai`（漏斗 v2）：粗筛档位——`off`（缺省，行为同旧）；`ai` = 上品帮 AI 预设档（上架≤365d/跟卖≤30/月动态>0/DRR≤15 + 价格分档月销下限）。**完整判定需 seller 运营指标**（`--auto-submit` 未显式指定时默认 `ai`，交互流程缺省不变）；无指标候选降级只判跟卖数
  - `--base-filter "monthly_sales>=50,drr<=15"`（漏斗 v2）：自定义区间粗筛（仅挑选期字段——**`margin` 不支持，会显式报错**指向 `--rules`；含加购率/促销/退货等 22 个），与 `--filter-profile` 叠加；非法表达式报错退出
- **表格符号**：`✅可挑` 待分析 · `⚠️夹带?` 标题不含关键词 · `⏭️价区间外` 超价格区间 · `💰有利` 符合条件 · `⚠️利润低` 利润不足 · `❌无货源` 1688 没匹配到 · `—` 运营列无数据（卖家后台未登录）
- **执行后验证**：① 采集完成 → 检查 `data/discovery/` 落盘 + 候选数量非零；② 货源分析后 → 读 `data/discovery/analysis_*.md` 核对候选状态分布（profitable/rejected/no_match）；③ 表格挑选/`--rules` 筛选后 → 向用户展示候选清单等确认，确认后才提交

### 管线 C 增强：裂变选品（discover --fission，v0.31）

**触发**：用户要"找更多同类产品 / 挖同行货源"（在种子选品基础上再深挖一层）。

> 完整细则（流程/预算限制/参数/数据字段/注意事项）见 `references/discover-fission.md`。

**展示候选列表后，等用户确认再提交。不替用户选择。**

### 管线 C 增强：任务式全自动选品（discover-task，漏斗 v2）

**触发**：用户说"自动采集/自动选品一批/无人值守跑"。对标上品帮无人值守任务制——全程免人工挑选，缺省干跑零副作用。

> **v0.70 目标驱动**：`--target-count` = **达标数**（最终 profitable 出口数），不再是页面采集数；采集上限独立由 `--max-scan`（默认 300）控制。匹配池按达标可能性降序（月销高→跟卖少），**profitable 达到目标即停止发起新图搜**（护配额）。`--match-limit` 缺省 = 目标×3（显式传参尊重）。粗筛池耗尽仍未达标 → 如实报告缺口（加大 `--max-scan` / 换关键词 `--resume` 续采，已匹配 pid 不重烧图搜）。**用户没说数量先问「要多少个符合要求的产品」**。

```bash
# ① 干跑（缺省）：采集(--max-scan 上限) → ai 粗筛 → 自动匹配 → 达标 50 即停，不入箱
python3 scripts/cli.py discover-task --keyword "宠物饮水机" --target-count 50

# ② 真实入采集箱（POST /api/v1/drafts，WebUI 认领后上架）
python3 scripts/cli.py discover-task --keyword "宠物饮水机" --to-box

# ③ 自定义入口页（highlight/搜索/类目/店铺页均可）+ 采集上限/并发
python3 scripts/cli.py discover-task --url "https://www.ozon.ru/highlight/xxx/" \
    --max-scan 200 --match-concurrency 2 --min-margin 20

# ④ 中断/未达标续跑（跳过已匹配 pid，不重烧图搜——可加大 --max-scan 补采）
python3 scripts/cli.py discover-task --keyword "宠物饮水机" --to-box --resume
```

- **流程**：`collect_and_analyze`（粗筛档位**缺省 `ai`**，与交互 discover 相反；深滚动采满 `--max-scan` 或触底）→ `rank_match_pool` 排序 → `match_selected`（**`target_profitable` 达标即停** + `--match-limit` 限额 + 连续 `--no-match-streak-stop`（默认 5）次 no_match 早停、请求间 2s 节奏抖动、并发 ≤2）→ profitable 逐条 `build_envelope_from_discovery` → `--to-box` 入采集箱（单条失败不中断批次）
- **进度输出**：`目标 N（达标）｜扫描上限 M` 开场、`[k/N] 达标进度` 行、结尾 `🎯 已达标` 或 `⚠️ 未达标: 目标 X｜累计达标 Y｜本次已采 Z（粗筛通过 P）` + 续采提示
- **任务状态**：`data/discovery/tasks/task_{ts}.json`（已处理 pid 含 profitable/rejected/no_match 终态 / 摘要含 target），`--resume` 找同入口最近任务续跑
- **安全边界**：不带 `--to-box` 即干跑（不出信封不入箱）；`--dry-run` 强制干跑；MCP 侧 `discover_task` 工具 dry_run 缺省 True，to_box=True 触发 dsh 审批
- **执行后验证**：任务状态 JSON 的 `summary.candidates` 状态分布 + `summary.target`（goal/prior/total）+ `summary.submitted/skipped/failed`；CSV（`--export`）供人工复核

### 管线 C 增强：多关键词批量选品（discover-multi）

**触发**：用户给多个关键词要横向对比选品。

```bash
python3 scripts/cli.py discover-multi --keywords "宠物饮水机,猫爬架,逗猫棒" --max-each 30
```

- 逐词跑 discover 漏斗（`--max-each` 每词采集上限），结果合并展示；`--auto-submit`/`--to-box` 语义与 discover 一致
- MCP 侧 `discover_multi` 工具同参；minutes 级任务建议 `background=true`

### 长任务后台化（MCP background + job_*，v0.70）

MCP 工具 `discover`/`discover_multi`/`discover_task`/`follow`/`seller`/`queries`/`graph` 均有 `background`（默认 false）与 `force` 参数：

```json
discover_task({"keyword": "手套", "target_count": 30, "background": true})
→ {"id": "a1b2c3", "status": "running", ...}          // <1s 返回
job_status({"task_id": "a1b2c3"})                      // 进度/阶段/日志尾/worker_task_ids
job_result({"task_id": "a1b2c3"})                      // 完成后取完整结果
job_cancel({"task_id": "a1b2c3"})                      // 需要时取消
```

- **会话关闭任务照跑**（CLI 进程脱离会话、输出落盘）；重开会话 `job_list` 找回
- **单飞闸**：同一时刻 1 个 heavy 任务（discover 族/follow/seller/graph），再提交返回 error dict，`force=true` 强制并行
- 纪律：agent 跑分钟级任务**必用** background=true，别阻塞对话；期间可答复用户/干别的，定期 job_status

### 管线 C 增强：to-box vs auto-submit 出货路径对比

| | `--to-box`（discover-task） | `--auto-submit`（discover） |
|---|---|---|
| 落点 | 采集箱草稿（`POST /api/v1/drafts`） | Worker 上架任务（`submit_task`） |
| 后续 | WebUI 采集箱人工认领 → 点提交才上架 | 直接进 worker 管线真实上架 |
| 可逆性 | 可逆（草稿可删/可改后再提交） | 不可逆（真实创建商品卡） |
| 信封差异 | 带 `extensions.discovery_meta`（蓝海分/月销/利润率/匹配置信度，采集箱列表与 CSV 可见） | 同样带 discovery_meta |
| agent 策略 | 可自动执行（安全默认） | **必须确认**（等用户说"提交"） |

## 管线 D：选品上架

**触发**：用户说"帮我选品上架"、给关键词但没给 URL，意图是"上架"而非"跟卖"

**子路径 D1：1688 图搜（image_search）**
```bash
python3 scripts/cli.py image_search --image "https://example.com/image.jpg"

# 用 CDP 图搜（比默认 AK 更准，准确率~100%）+ 按价格排序 + 限制条数
python3 scripts/cli.py image_search --image "https://..." --source cdp --sort price_asc --limit 5
```
- **输入**：图片 URL 或本地路径
- **参数**：
  - `--source`：`ak`（默认，1688 AK API）或 `cdp`（浏览器图搜，更准）
  - `--sort`：`price_asc` / `price_desc` / `sold_desc` / `yx_desc`
  - `--limit`：返回条数（默认 10）
- **输出**：JSON `{success, results: [{offer_id, title, price, image, shop_name}]}`
- **执行后验证**：`results` 为空 → 告知用户"1688 未找到同款"，询问换图/换关键词；非空 → 展示候选让用户确认哪一款，**确认后再走 graph 上架**（图搜结果不直接上架）

**子路径 D2：Ozon 选品（discover）**
```bash
python3 scripts/cli.py discover --keyword "宠物用品"
# 无关键词 → 直接采集中国站（highlight 页懒加载）
python3 scripts/cli.py discover --max-products 30
```
Discover v2 四阶段：采集（搜索/中国站懒加载）→ 全量数据（含运营指标）→ 表格挑选 → 批量 1688 货源 → 确认提交（详见管线 C）。
> **C 与 D 命令相同（都是 discover）**：区别仅在 agent 按用户意图处理——C（跟卖）与 D（上架）都不带 `--auto-submit` 时展示候选等用户确认；`--auto-submit` 是**显式参数**，不存在「D 默认自动提交」；提交时机按 SKILL.md §3 决策边界（discover 选品后的最终提交必须确认）控制。

## 管线 E：趋势选品（agent 自主分析 + discover 执行，v0.31 起）

**触发**：用户说"帮我找 {品类} 的**热卖/趋势/新品风向**"商品。
注意：只说"蓝海"默认走**管线 C**（discover 跟卖选品，蓝海评分体系在 C）。

> 完整流程（web_search 多角度 → LLM 提炼 → discover 执行）与纪律见 `references/trend-selection.md`。

## 批量处理（batch_test.py）

```bash
python3 scripts/batch_test.py --urls-file urls.txt --submit

# 提交并轮询结果（完成后打印每个产品的 1688链接/利润率/售价/采购价/运费/净利润率/OzonID）
python3 scripts/batch_test.py --urls-file urls.txt --submit --wait

# 只组装信封不提交（验证信封，不花上架额度）
python3 scripts/batch_test.py --urls-file urls.txt --dry-run

# 从第 5 个开始处理 10 个，间隔 5 秒
python3 scripts/batch_test.py --urls-file urls.txt --submit --start 5 --limit 10 --delay 5

# 断点续传（v0.36）：跳过上次已成功项，只重试失败项（自动找最新 data/batch_results/batch_*.json）
python3 scripts/batch_test.py --urls-file urls.txt --submit --resume

# 显式指定续传来源结果文件
python3 scripts/batch_test.py --urls-file urls.txt --submit --resume-from data/batch_results/batch_20260811_090000.json

# 完成时推送 webhook 通知（需 Worker 配置 TASK_NOTIFY_URL）
python3 scripts/batch_test.py --urls-file urls.txt --submit --wait --notify
```

URL 文件混合 1688/Ozon 链接，自动识别管线。

参数：`--urls-file`（必填）、`--submit`（提交 Worker，默认不提交）、`--wait`（轮询到完成，含产品明细）、`--dry-run`（只组装验证）、`--start` / `--limit`（处理范围）、`--delay`（间隔秒，默认 3.0）、`--wait-timeout`（轮询超时秒，默认 900）、`--type-filter`（按类型过滤 URL：`1688`/`ozon`/`all`）、`--resume`（断点续传，跳过已成功项）、`--resume-from`（显式指定续传来源结果文件，默认自动找最新）、`--notify`（提交时 `notify=True`，Worker 完成推 webhook）。

凭证：`--store-id <店铺名>` 从 `data/config/stores.json` 取凭证（同 graph/follow 的 `--store`）；不指定时用环境变量 `OZON_CLIENT_ID` / `OZON_API_KEY`。

**执行后验证**：① `--dry-run` → 核对每个信封字段完整性（对照 envelope_example.json）；② `--wait` 完成后 → 逐产品核对明细（OzonID/利润率/审核状态），失败的标记出来单独汇报；③ 部分失败 → 可 `--resume` 断点续传只重试失败项。

## 任务查询（query）

**触发**：用户问"任务/上架进度"、"完成了吗"、追问 `graph`/`follow`/`batch_test` 提交后返回的 task_id 状态。

```bash
# 单次查询（非终态返回当前进度，终态打印明细）
python3 scripts/cli.py query 550e8400-e29b-41d4-a716-446655440000

# 轮询直到终态（每 10s 查一次，打印进度中间态；--timeout 默认 900s 超时）
python3 scripts/cli.py query 550e8400-... --watch

# 长任务（生图/审核慢）调大轮询上限
python3 scripts/cli.py query 550e8400-... --watch --timeout 1800
```

- **输入**：`<task_id>`（位置参数，`graph`/`follow` 提交返回的 UUID；`batch_test --wait` 另走批量轮询）
- **参数**：`--watch`（轮询到终态，每 10s）、`--timeout`（watch 超时秒，默认 900）
- **输出**（非 JSON，人读格式）：`任务 {id}: {status}` + 开始/完成时间 + 重试次数；终态成功 → 产品明细行（OzonID | 售价 | 净利润率 | 审核状态 | 备注）；失败 → `❌ 错误: {error_message}`；`not_found`/`worker_unreachable`/`timeout` 各有明确提示
- **status 取值**：`completed`/`failed`/`rejected`/`cancelled`（终态，v0.38 起 `rejected`=Ozon 审核被拒）/ `pending`/`running`（非终态）/ `not_found`/`worker_unreachable`/`query_error`（查询异常）
- **Agent 决策**：提交后可直接 `query --watch` 等终态再向用户汇报，不必让用户盲等；终态字段解析见 output-schema.md
- **执行后验证**：① 终态 `completed` → 确认 `moderate_status` 后再向用户报成功；② `rejected`/`failed` → 按 error-codes.md 引导（看 Ozon 后台拒绝原因 / 可重提）；③ `pending`/`running` 非终态 → 告知预计 10-20 分钟，建议 `--watch` 或稍后重查
- **⚠️ rejected/failed 重提（v0.38 N2）**：`rejected`（审核被拒）与 `failed`（执行失败）是终态但可重试——重新提交同款会被 SKU 去重放行（终态不占用去重名额）。重提方式：调 Worker `POST /api/v1/resubmit_task/{task_id}`（请求体带 `token`，复制原载荷 + 重生成图片重新入队）。CLI 暂未内置 resubmit 命令，需 API 调用；重提后返回新 task_id，用 `query <新id> --watch` 跟踪。

## 卖家店铺分析（seller）

**触发**：跟卖选品时发现某卖家店铺整体强（竞品多/销量好），要"挖这个卖家整店"。

```bash
# 采集店铺产品 + 逐 SKU 拉运营指标（默认前 60 个产品、前 30 个 SKU 分析）
python3 scripts/cli.py seller --seller-id 472316509
```

- **输入**：`--seller-id`（必填，Ozon 卖家 ID，跟卖列表透传的 seller_id）
- **参数**：`--max-products`（采集店铺产品上限，默认 60）、`--max-skus`（运营分析 SKU 上限，默认 30）
- **输出**：JSON `{seller_id, product_count, analyzed_count, products: [{product_id, monthly_sales, monthly_revenue, sales_dynamics, drr, create_days, category}]}`
- **⚠️ 限速**：what_to_sell 逐 SKU 查询 ~1s/SKU，`--max-skus 30` 约 30s；店铺产品太多只分析前 N
- **依赖**：seller.ozon.ru 已登录（工具 Chrome 登录态）
- **Agent 决策**：拿到卖家产品清单后可从中挑选候选 → `graph`/`follow` 上架，或喂给 `discover --fission` 裂变

## what-to-sell 蓝海/榜单查询（queries）

**触发**：选品前查"哪些关键词有蓝海"（count/ca/uniq_sellers）、Ozon 畅销榜、跨平台畅销榜。

```bash
# 关键词蓝海查询（all-queries 全量，可带关键词过滤）
python3 scripts/cli.py queries --type all-queries --keyword "поилка" --export csv --output /tmp/queries_all.csv

# Ozon 畅销榜（按 SKU 过滤）
python3 scripts/cli.py queries --type ozon-bestsellers

# 跨平台畅销榜（按类目/价格过滤）
python3 scripts/cli.py queries --type market-bestsellers --category-id 17028929 --price-min 300 --price-max 2000

# 导出 JSON 到指定路径（默认打印 stdout）
python3 scripts/cli.py queries --type all-queries --export json --output data/queries.json
```

- **输入**：`--type`（必填：`all-queries`/`ozon-bestsellers`/`market-bestsellers`）
- **参数**：`--keyword`（all-queries 关键词过滤）、`--sku`（ozon-bestsellers SKU 过滤）、`--category-id`/`--price-min`/`--price-max`（market-bestsellers 过滤）、`--export csv|json`（默认 csv）、`--output <路径>`（默认打印 stdout）
- **输出**：CSV（utf-8-sig，Excel 兼容）或 JSON 行
- **⚠️ 前置**：需 seller.ozon.ru 已登录（CDP 复用已登录 tab 页内 fetch 真实端点）；未登录打印「未登录 seller.ozon.ru」
- **自动上报**：采集成功后 fire-and-forget 上报 worker PG（异步，无 token 跳过，不阻断主流程）
- **Agent 决策**：`all-queries` 结果可作蓝海关键词依据（反哺 `discover --blue-ocean-source`）；榜单数据可直接作为选品参考

## 自动更新（update）

**触发**：提示"发现新版本 vX.Y.Z"或用户要求升级 Skill。

```bash
python3 scripts/cli.py update
```

- **行为**：从 COS manifest 下载最新包 → sha256 校验 → 备份当前（`_update_backup`）→ 覆盖 `scripts/`/文档 → **保留 `data/`**（凭证/登录态/缓存/选品日志）→ 失败自动回滚
- **⚠️ 跨进程锁**：并发 CLI 同时 auto-update 会互相破坏备份/覆盖，有文件锁防竞态
- **Agent 决策**：版本升级由用户主导，不自动触发；升级提示按 SKILL.md §6 引导用户执行

## 磁盘清理（cleanup）

**触发**：用户问"占空间太大/清理一下"、磁盘满、Chrome profile 膨胀（实测可再生缓存累计可达 GB 级）。

```bash
# 预演（只打印将删除内容，不实际删）
python3 scripts/cli.py cleanup --all --dry-run

# 清理全部：Chrome profile 可再生缓存 + 磁盘缓存 + 孤儿 .json.tmp + 过期结果（--days 默认 30）
python3 scripts/cli.py cleanup --all

# 只清过期结果/日志文件（保留最近 7 天）
python3 scripts/cli.py cleanup --old-results --days 7
```

- **参数**：`--profile-cache`（Chrome profile 可再生缓存目录白名单，**登录态绝不动**）、`--cache`（磁盘缓存全部命名空间）、`--temp`（孤儿 .json.tmp + 旧任务/会话文件）、`--old-results`（过期结果/日志，配合 `--days` 默认 30）、`--dry-run`（只预览）、`--all`（全部）
- **⚠️ profile-cache 需 Chrome 关闭**：Chrome 进程运行时跳过（缓存文件被锁，硬删会损坏），返回 `skipped_chrome_running` warning
- **安全**：删除走 `safe_rmtree`（fail-open），登录态文件（Cookies/Local Storage/Login Data/Preferences）绝不在清理名单
- **Agent 决策**：环境类操作，用户要求即执行；`--dry-run` 先预览是稳妥做法

## 其他命令（辅助）

```bash
# 1688 关键词搜索（按词找货，耗 1688 配额；--rules 两段式同 discover，"ai" 一键预设）
python3 scripts/cli.py search "宠物饮水机" --page-size 5
python3 scripts/cli.py search "宠物饮水机" --rules "ai,margin>=20" --export out.csv

# 查看已配置店铺
python3 scripts/cli.py list_stores

# 浏览器自动获取 1688 AK（登录后自动复制）
python3 scripts/cli.py get_ak --timeout 300

# CDP 探针抓取单个 1688 商品（调试用）
python3 scripts/cli.py probe --url "https://detail.1688.com/offer/xxx.html" --timeout 30

# 环境检查 + 自动启动 Chrome + 凭证验证（首次使用/排错，env-setup.md）
python3 scripts/cli.py check

# 跨浏览器 cookie 导入（v0.69）：扫描本机 Chrome/Edge/Brave/Firefox 的 1688/Ozon
# 登录 cookie → 注入工具 Chrome → 验证。readiness 未登录时也会自动兜底（冷却 1h）
python3 scripts/cli.py import-cookies
python3 scripts/cli.py import-cookies --sources chrome,firefox   # 指定源
```

## settings.json 可调参数

> 位于 `data/config/settings.json`（与凭证同目录），高级用户/维护者按需调整；普通 agent 操作不涉及。

| key | 默认 | 作用 |
|-----|------|------|
| `probe_interval_seconds` | 2.5 | 1688 CDP 探针页内操作间隔秒数（T5；调大更稳防验证码，调小更快） |
| `match_min_conf` | 0.3 | 图搜匹配主护栏置信度下限（T7；无徽标路径 conf ≥ 阈值放行；调高更严格） |
| `match_badge_eff_min` | 0.5 | 图搜匹配 badge 有效性下限（T7；主护栏 badge 下限） |
| `visual_review` | false | 全局开启 discover/follow 人工评审暂停（D3-L3；等价每次加 `--review`） |
| `fx_rate` | 0.075 | RUB→CNY 全局汇率兜底（N6；CLI `--fx-rate` > 店铺 stores.json `fx_rate` > 此值 > 0.075） |
| `sentry_dsn` | 内置默认 | Sentry 错误上报 DSN 覆盖（可选） |

修改后即时生效（读取时加载，无需重启）。
