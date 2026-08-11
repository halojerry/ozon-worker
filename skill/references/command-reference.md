# 全命令参考

> 各管线的触发条件、完整参数、示例、输入输出。速查表见 SKILL.md §2。

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

## 管线 A：1688 上架（graph）

**触发**：用户消息含 `1688.com` 链接，或管线 B 降级

```bash
python3 scripts/cli.py graph --url "https://detail.1688.com/offer/xxx.html" --store "主店铺"

# 只组装信封不提交（调试/确认场景，不会上架）
python3 scripts/cli.py graph --url "https://..." --store "主店铺" --no-submit

# 用商品 ID（无 URL 时）+ 指定 Ozon 类目俄语关键词
python3 scripts/cli.py graph --item-id "980815374096" --category-query "поилка" --store "主店铺"
```

- **输入**：1688 商品 URL、店铺名
- **参数**：
  - `--url` / `--item-id`（二选一）：1688 商品链接 或 商品 ID
  - `--store`：Ozon 店铺名（定价/凭证来源），省略时用默认店铺
  - `--category-query`：Ozon 类目俄语关键词（帮助类目匹配，可选）
  - `--retries`：CDP 抓取重试次数（默认 3）
  - `--no-submit`：只组装信封不提交 Worker（调试）
- **输出**：JSON `{summary, envelope, submit_result}`（字段解析见 output-schema.md）
- **自动完成**：CDP 抓取 1688 → 组装信封 → 提交 Worker

## 管线 B：Ozon 跟卖（follow）

**触发**：用户消息含 `ozon.ru` 商品链接

```bash
python3 scripts/cli.py follow --ozon-url "https://www.ozon.ru/product/xxx/" --store "主店铺" --auto-submit
```

- **输入**：Ozon 商品 URL、店铺名
- **输出**：JSON `{summary, envelope, submit_result}`
- **自动完成**：CDP 抓取 Ozon → 图搜 1688 同款 → 组装信封 → 提交 Worker

**降级**：Ozon 页面禁止复制（DataDome 拦截）时：
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
```

- **输入**：搜索关键词 或 Ozon 页面 URL 或 无（→ 中国站懒加载）
- **流程（v2，先采集后分析）**：
  1. **采集**：默认中国站——有 `--keyword` → 中国站 highlight 页内搜索（`CHINA_HIGHLIGHT_URL?text=`）；`--local` → 主站 `/search/?text=`；有 `--url` → 直接采集该页；都无 → 中国站 highlight 页滚动懒加载。结果容器限定（`.tile-root`），滚动到底部触发懒加载 + 等待渲染 + 翻页 + 去重
  2. **全量数据**：widget API（价格/标题/图/品牌/评分/评论数）+ 跟卖数/最低价 + **seller.ozon.ru 运营指标**（月销量/增长率/广告占比/上架天数——需卖家后台已登录，未登录自动降级，表格运营列显示 `—`）
  3. **表格分析挑选**：全量表格展示（含拒绝原因/状态）→ 人工按序号挑选 或 `--rules` 自动筛选 —— **此时不花 1688 配额**
  4. **批量货源**：只对选中的产品 1688 识图（CDP 图搜 → AK 图搜 → AK 关键词三级，含重试）→ 利润计算（真实重量/佣金）→ 蓝海评分 → 确认 → 提交
- **输出**：候选产品列表（全量落盘 `data/discovery/`，CSV 可导出）
- **规则字段**：`monthly_sales / gmv / drr / seller_count / margin / price / create_days / sales_growth / rating`
- **其他参数**：
  - `--rules`：自动筛选规则（跳过交互），逗号分隔，如 `"monthly_sales>=200,drr<=30,seller_count<=20"`
  - `--export csv|json|both` + `--output <路径>`：导出全量+选中结果
  - `--brand-filter`：`nobrand`（默认，只要无品牌/白牌）/ `known`（只过滤知名品牌黑名单）/ `all`（不过滤）
  - `--fx-rate`：RUB→CNY 汇率，显式指定时优先；缺省按 店铺 `stores.json` 的 `fx_rate` → `settings.json` 的 `fx_rate` → 0.075 解析（P2-6：卢布波动时在店铺/全局配置中调整，避免利润估算失真）
- **表格符号**：`✅可挑` 待分析 · `⚠️夹带?` 标题不含关键词 · `⏭️价区间外` 超价格区间 · `💰有利` 符合条件 · `⚠️利润低` 利润不足 · `❌无货源` 1688 没匹配到 · `—` 运营列无数据（卖家后台未登录）

### 管线 C 增强：裂变选品（discover --fission，v0.31）

**触发**：用户要"找更多同类产品 / 挖同行货源"（在种子选品基础上再深挖一层）。

> 完整细则（流程/预算限制/参数/数据字段/注意事项）见 `references/discover-fission.md`。

**展示候选列表后，等用户确认再提交。不替用户选择。**

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
```

URL 文件混合 1688/Ozon 链接，自动识别管线。

参数：`--urls-file`（必填）、`--submit`（提交 Worker，默认不提交）、`--wait`（轮询到完成，含产品明细）、`--dry-run`（只组装验证）、`--start` / `--limit`（处理范围）、`--delay`（间隔秒）、`--wait-timeout`（轮询超时秒，默认 900）、`--type-filter`（按类型过滤 URL）。

凭证：`--store-id <店铺名>` 从 `data/config/stores.json` 取凭证（同 graph/follow 的 `--store`）；不指定时用环境变量 `OZON_CLIENT_ID` / `OZON_API_KEY`。

## 其他命令（辅助）

```bash
# 1688 关键词搜索（按词找货，耗 1688 配额）
python3 scripts/cli.py search "宠物饮水机" --page-size 5

# 查看已配置店铺
python3 scripts/cli.py list_stores

# 浏览器自动获取 1688 AK（登录后自动复制）
python3 scripts/cli.py get_ak --timeout 300

# CDP 探针抓取单个 1688 商品（调试用）
python3 scripts/cli.py probe --url "https://detail.1688.com/offer/xxx.html" --timeout 30
```
