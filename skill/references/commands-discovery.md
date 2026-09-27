# 选品类命令（discover / discover-task / discover-multi / seller / queries / category）

> v0.79 拆分自 command-reference.md（PLAN-agent-ergonomics-v1 D3）。选管线见 routing.md；
> 上架类见 commands-listing.md；运维类见 commands-ops.md。
> 双出口纪律：`--to-box`（入箱，可自动）与 `--auto-submit`（直上，须确认）二选一，
> 用户没说走哪条就先问（routing.md「to-box vs auto-submit」）。

## 目录
- [管线 C：跟卖选品（discover，Discover v2）](#管线-c跟卖选品discoverdiscover-v2)
- [管线 C 增强：裂变选品（--fission）](#管线-c-增强裂变选品discover---fissionv031)
- [管线 C 增强：任务式全自动选品（discover-task）](#管线-c-增强任务式全自动选品discover-task漏斗-v2)
- [管线 C 增强：多关键词批量选品（discover-multi）](#管线-c-增强多关键词批量选品discover-multi)
- [长任务后台化（MCP background + job_*）](#长任务后台化mcp-background--job_v070)
- [管线 D2：Ozon 选品上架](#管线-d2ozon-选品上架discover)
- [管线 E：趋势选品](#管线-e趋势选品agent-自主分析--discover-执行v031-起)
- [卖家店铺分析（seller）](#卖家店铺分析seller)
- [what-to-sell 蓝海/榜单查询（queries）](#what-to-sell-蓝海榜单查询queries)
- [Ozon 类目查询（category）](#ozon-类目查询category)

## 管线 C：跟卖选品（discover，Discover v2）

**触发**：用户说"有什么好产品可以跟卖"、"帮我找可以跟卖的"（无 URL）

> ⚠️ 串行闸：本节全部命令（discover/--fission/discover-task/discover-multi）与 graph/follow/seller
> 跨进程互斥，闸被占 exit 4（`--wait` 排队 / `--force` 强制并行）。

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
- **输出**：候选产品列表（全量落盘 `data/discovery/`，CSV 可导出）；结束写 `data/logs/report_*.json` 运行报告（v0.78）
- **规则字段**（两段式，v0.69 起）：`monthly_sales / gmv / drr / seller_count / price / create_days / sales_growth / rating` 为**挑选期**字段（1688 匹配前判定）；`margin` 为**匹配期**字段（匹配后二次筛选——匹配前恒 0.0 无从判定）
- **其他参数**：
  - `--rules`：自动筛选规则（跳过交互），逗号分隔，如 `"monthly_sales>=200,drr<=30,seller_count<=20"`；**两段式**：挑选期规则在前、匹配期规则在后（如 `"ai,margin>=20"`）；**`"ai"` = 上品帮 AI 预设**（上架≤365d/跟卖≤30/月动态>0/DRR≤15 + 价格分档月销下限），可作逗号项与其他规则混写
  - `--export csv|json|both` + `--output <路径>`：导出全量+选中结果
  - `--brand-filter`：`nobrand`（默认，只要无品牌/白牌）/ `known`（只过滤知名品牌黑名单）/ `all`（不过滤）
  - `--fx-rate`：RUB→CNY 汇率，显式指定时优先；缺省按 店铺 `stores.json` 的 `fx_rate` → `settings.json` 的 `fx_rate` → 0.075 解析
  - `--local`：主站搜索（默认中国站 highlight 页内搜索）；`--china`（隐藏别名，`argparse.SUPPRESS`，等价默认中国站）
  - `--blue-ocean-source csv|queries` + `--blue-ocean-csv <path>`：蓝海增强数据源（v0.33 C4）
  - `--review`：人工评审暂停（v0.38）——弱匹配候选逐个确认（`y`/`N`/`a`=全部/`s`=跳过），决策写入 review_log；settings.json `visual_review: true` 可全局开启
  - `--notify`：提交时 GraphInput 顶层 `notify=True`，Worker 完成推 webhook
  - `--filter-profile off|ai`（漏斗 v2）：粗筛档位——`off`（缺省，行为同旧）；`ai` = 上品帮 AI 预设档。**完整判定需 seller 运营指标**（`--auto-submit` 未显式指定时默认 `ai`，交互流程缺省不变）；无指标候选降级只判跟卖数
  - `--base-filter "monthly_sales>=50,drr<=15"`（漏斗 v2）：自定义区间粗筛（仅挑选期字段——**`margin` 不支持，会显式报错**指向 `--rules`；含加购率/促销/退货等 22 个），与 `--filter-profile` 叠加；非法表达式报错退出
- **表格符号**：`✅可挑` 待分析 · `⚠️夹带?` 标题不含关键词 · `⏭️价区间外` 超价格区间 · `💰有利` 符合条件 · `⚠️利润低` 利润不足 · `❌无货源` 1688 没匹配到 · `—` 运营列无数据（卖家后台未登录）
- **执行后验证**：① 采集完成 → 检查 `data/discovery/` 落盘 + 候选数量非零；② 货源分析后 → 读 `data/discovery/analysis_*.md` 核对候选状态分布（profitable/rejected/no_match）；③ 表格挑选/`--rules` 筛选后 → 向用户展示候选清单等确认，确认后才提交

## 管线 C 增强：裂变选品（discover --fission，v0.31）

**触发**：用户要"找更多同类产品 / 挖同行货源"（在种子选品基础上再深挖一层）。

> 完整细则（流程/预算限制/参数/数据字段/注意事项）见 `discover-fission.md`。

**展示候选列表后，等用户确认再提交。不替用户选择。**

## 管线 C 增强：任务式全自动选品（discover-task，漏斗 v2）

**触发**：用户说"自动采集/自动选品一批/无人值守跑"。全程免人工挑选，缺省干跑零副作用。

> **v0.70 目标驱动**：`--target-count` = **达标数**（最终 profitable 出口数），不再是页面采集数；采集上限独立由 `--max-scan`（默认 300）控制。匹配池按达标可能性降序（月销高→跟卖少），**profitable 达到目标即停止发起新图搜**（护配额）。`--match-limit` 缺省 = 目标×3（显式传参尊重）。粗筛池耗尽仍未达标 → 如实报告缺口（加大 `--max-scan` / 换关键词 `--resume` 续采，已匹配 pid 不重烧图搜）。**用户没说数量先问「要多少个符合要求的产品」**。

```bash
# ① 干跑（缺省）：采集(--max-scan 上限) → ai 粗筛 → 自动匹配 → 达标 50 即停，不入箱
python3 scripts/cli.py discover-task --keyword "宠物饮水机" --target-count 50

# ② 真实入采集箱（POST /api/v1/drafts，WebUI 认领后上架）
python3 scripts/cli.py discover-task --keyword "宠物饮水机" --to-box

# ②b 直接走管线上架（submit_task，真实创建商品——agent 侧必须确认后才跑）
python3 scripts/cli.py discover-task --keyword "宠物饮水机" --auto-submit

# ③ 自定义入口页（highlight/搜索/类目/店铺页均可）+ 采集上限/并发
python3 scripts/cli.py discover-task --url "https://www.ozon.ru/highlight/xxx/" \
    --max-scan 200 --match-concurrency 2 --min-margin 20

# ④ 中断/未达标续跑（跳过已匹配 pid，不重烧图搜——可加大 --max-scan 补采）
python3 scripts/cli.py discover-task --keyword "宠物饮水机" --to-box --resume
```

- **参数补充**：`--filters <JSON规则文件>`（区间/品牌/价格/发货模式，FBS 含 rFBS，子串匹配，同名键覆盖 ai 默认）、`--min-price/--max-price/--brand-filter`、`--expend-shop N` 拓店（`--url` 须商品页种子，竞品卖家评级优先，预算 max(N×4,60)，与 `--keyword` 互斥）、`--dry-run`（强制干跑）、`--wait`（auto-submit 出口逐个等终态；**任一任务终态 failed → exit 3**，全成功 → 0——fix/arch-findings-v1 已对齐 graph/follow 单腿语义；入箱出口是 draft_id 无任务句柄，`--to-box`×`--wait` 组合不轮询、打 ⚠️ 一行后按入箱出口码返回）
- **流程**：`collect_and_analyze`（粗筛档位**缺省 `ai`**，与交互 discover 相反）→ `rank_match_pool` 排序 → `match_selected`（**`target_profitable` 达标即停** + `--match-limit` 限额 + 连续 `--no-match-streak-stop`（默认 5）次 no_match 早停、请求间 2s 节奏抖动、并发 ≤2）→ profitable 逐条 `build_envelope_from_discovery` → 双出口
- **进度输出**：`目标 N（达标）｜扫描上限 M` 开场、`[k/N] 达标进度` 行、结尾 `🎯 已达标` 或 `⚠️ 未达标` + 续采提示；出口末行 `👉 NEXT:`（v0.79）
- **结构化出口（v0.70）**：结尾输出尾部 JSON（`task_id` / `summary` / `state_path`）——MCP 后台任务收割与 agent 机读都靠它；`--export <路径.csv>` 落盘全量候选供人工复核
- **任务状态**：`data/discovery/tasks/task_{ts}.json`，`--resume` 找同入口最近任务续跑
- **安全边界**：不带 `--to-box` 即干跑（不出信封不入箱）；MCP 侧 `discover_task` 工具 dry_run 缺省 True，to_box=True 触发 dsh 审批
- **执行后验证**：任务状态 JSON 的 `summary.candidates` 状态分布 + `summary.target`（goal/prior/total）+ `summary.submitted/skipped/failed`

## 管线 C 增强：多关键词批量选品（discover-multi）

**触发**：用户给多个关键词要横向对比选品。

```bash
python3 scripts/cli.py discover-multi --keywords "宠物饮水机,猫爬架,逗猫棒" --max-each 30
```

- 逐词跑 discover 漏斗（`--max-each` 每词采集上限），结果合并展示；`--auto-submit`/`--to-box` 语义与 discover 一致；`--min-margin`（匹配期筛选门槛，与 graph/follow 的预估拦截语义不同）
- MCP 侧 `discover_multi` 工具同参；minutes 级任务建议 `background=true`

## 长任务后台化（MCP background + job_*，v0.70）

MCP 工具 `discover`/`discover_multi`/`discover_task`/`follow`/`seller`/`queries`/`graph` 均有 `background`（默认 false）与 `force` 参数：

```json
discover_task({"keyword": "手套", "target_count": 30, "background": true})
→ {"id": "a1b2c3", "status": "running", ...}          // <1s 返回
job_status({"task_id": "a1b2c3"})                      // 进度/阶段/日志尾/worker_task_ids
                                                       // + next_poll_s/next_action（v0.79 轮询节奏）
job_result({"task_id": "a1b2c3"})                      // 完成后取完整结果
job_cancel({"task_id": "a1b2c3"})                      // 需要时取消
```

- **会话关闭任务照跑**（CLI 进程脱离会话、输出落盘）；重开会话 `job_list` 找回
- **单飞闸**：同一时刻 1 个 heavy 任务（discover 族/follow/seller/graph），再提交返回 error dict，`force=true` 强制并行
- **轮询节奏（v0.79）**：`job_status` 返回 `next_poll_s`（20s）——分钟级任务按此间隔查，勿秒级轮询；终态即停
- 纪律：agent 跑分钟级任务**必用** background=true，别阻塞对话；期间可答复用户/干别的

## 管线 D2：Ozon 选品上架（discover）

**触发**：用户说"帮我选品上架"、给关键词但没给 URL，意图是"上架"而非"跟卖"

```bash
python3 scripts/cli.py discover --keyword "宠物用品"
# 无关键词 → 直接采集中国站（highlight 页懒加载）
python3 scripts/cli.py discover --max-products 30
```

> **C 与 D 命令相同（都是 discover）**：区别仅在 agent 按用户意图处理——C（跟卖）与 D（上架）都不带 `--auto-submit` 时展示候选等用户确认；`--auto-submit` 是**显式参数**，不存在「D 默认自动提交」。

## 管线 E：趋势选品（agent 自主分析 + discover 执行，v0.31 起）

**触发**：用户说"帮我找 {品类} 的**热卖/趋势/新品风向**"商品。
注意：只说"蓝海"默认走**管线 C**（discover 跟卖选品，蓝海评分体系在 C）。

> 完整流程（web_search 多角度 → LLM 提炼 → discover 执行）与纪律见 `trend-selection.md`。

## 卖家店铺分析（seller）

**触发**：跟卖选品时发现某卖家店铺整体强（竞品多/销量好），要"挖这个卖家整店"。

> ⚠️ 串行闸：seller 与 graph/follow/discover 族跨进程互斥，闸被占 exit 4。

```bash
# 采集店铺产品 + 逐 SKU 拉运营指标（默认前 60 个产品、前 30 个 SKU 分析）
python3 scripts/cli.py seller --seller-id 472316509
```

- **输入**：`--seller-id`（必填，Ozon 卖家 ID，跟卖列表透传的 seller_id）
- **参数**：`--max-products`（采集店铺产品上限，默认 60）、`--max-skus`（运营分析 SKU 上限，默认 30）
- **输出**：JSON `{seller_id, product_count, analyzed_count, products: [{product_id, monthly_sales, monthly_revenue, sales_growth, drr, create_days, category}]}`
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

## Ozon 类目查询（category）

**触发**：确认类目 / 排查类目匹配问题。

```bash
python3 scripts/cli.py category "поилка для кошек" --lang ZH_HANS --max 5
```

- **参数**：`--lang ZH_HANS|EN|RU`（默认 ZH_HANS 中文直查）、`--max N`（候选数）
- **输出**：候选类目列表（只读）
