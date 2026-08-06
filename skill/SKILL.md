---
name: pounding-ozon-probe
version: "0.27.1"
description: >
  Ozon 上架工具。当用户发送 1688 链接时直接上架，发送 Ozon 链接时直接跟卖。
  当用户说"帮我找蓝海产品""帮我选品"且没有给链接时，去 Ozon 中国站自动选品。
  支持批量上架、以图搜款。
---

# pounding-ozon-probe — 工具手册

## 1. 概述

pounding-ozon-probe 是跨境电商上架工具，覆盖从选品到上架 Ozon 的完整流程。

**你的角色**：操作员。你用以下命令完成工作。每条命令封装了完整的业务逻辑，你只需按场景选择并执行。

**所有命令在 `skill/` 目录下执行，使用 `python3.12 scripts/cli.py`。**

---

## 2. 环境准备（首次使用）

### 2.1 安装依赖

```bash
cd skill && pip3.12 install -r requirements.txt
```

### 2.2 获取凭证

| 凭证 | 用途 | 获取方式 |
|------|------|----------|
| MXOU_TOKEN | 云端 AI 服务密钥 | 自动从 `~/.pounding/config.json` 读取（pounding 桌面端用户无需手动设置）。没有则向用户索取。 |
| 1688 AK | 1688 商品搜索 | 浏览器打开 https://clawhub.1688.com 登录后复制 |
| Ozon Client ID + API Key | Ozon API | Ozon 卖家后台 → 设置 → API 密钥 |

三个凭证一次性问完用户。MXOU_TOKEN 自动读到了就跳过。

```bash
python3.12 scripts/cli.py set_token --token <MXOU_TOKEN>
python3.12 scripts/cli.py set_ak --ak <1688_AK>
python3.12 scripts/cli.py set_store --name "主店铺" --client-id <CLIENT_ID> --api-key <API_KEY>
```

### 2.3 验证配置

```bash
python3.12 scripts/cli.py check
```

全部 ✅ 后方可执行业务操作。如有 ❌，按提示修复。

**check 失败排查表**：

| ❌ 项 | 原因 | 修复 |
|---|---|---|
| Chrome 未安装 | 系统无 Google Chrome | 安装 Google Chrome（工具自动启动，无需手动配置） |
| Chrome 版本过旧 | Chrome < 100 | 升级 Chrome 到最新版 |
| 1688 AK 无效 | AK 过期或未配置 | `python3.12 scripts/cli.py get_ak`（自动获取）或 `set_ak` 手动设置 |
| 1688 未登录 | Chrome 中未登录 1688 | 在 Chrome 打开 1688.com 登录（工具会提示） |
| Ozon 店铺未配置 | `data/config/stores.json` 无店铺 | `python3.12 scripts/cli.py set_store --name 主店铺 --client-id xxx --api-key xxx` |
| MXOU_TOKEN 无效 | token 过期或未配置 | 向用户索取新 token：`python3.12 scripts/cli.py set_token --token <token>` |
| Worker 不可达 | 网络问题或 Worker 宕机 | 检查网络；`curl -s https://worker.mxou.cn/health` 确认服务状态 |

### 2.4 环境要求

- Python 3.12（必须）
- Google Chrome（工具自动启动，用户无需手动打开）

---

## 3. 意图路由

**先判断用户意图，再选管线。每次操作前重新判断，不因上下文而惯性选择。**

### 决策表（按优先级从上到下）

```
用户输入
 ├─ ① 有 URL？ → 先判 URL 类型：
 │    商品页 detail.1688.com/offer/…  → 【管线 A】1688 直接上架
 │    商品页 ozon.ru/product/…        → 【管线 B】Ozon 跟卖
 │    搜索页/类目页（ozon.ru/search 或 /category/）→ 【管线 C】discover --url 采集该页
 │    多 URL / 混合（1688+Ozon）      → 追问用户按顺序处理（一次只处理一个任务）
 ├─ ② 有图片（无 URL）？             → 【管线 D1】image_search 以图搜款
 ├─ ③ 无 URL → 按意图词优先级：
 │    "趋势/热卖/新品风向" + 品类     → 【管线 E】趋势选品（先 web_search，见下）
 │    "跟卖/找能跟卖的"              → 【管线 C】discover 跟卖选品
 │    "选品上架/直接上架"            → 【管线 D】discover 选品上架
 │    "蓝海" → 默认 【管线 C】（蓝海评分体系在 C）；用户明确说"蓝海趋势/市场分析"才走 E
 │    "选品" 无任何修饰              → 追问（跟卖 or 上架 or 趋势）
 ├─ ④ 问店铺商品状态/被拒原因        → 引导用户在 Ozon 卖家后台查看（工具不直接查询）
 └─ ⑤ 指代不清（"类似的""这个""它"） → 必须追问确认，禁止猜测
```

### 关键规则

- **有 URL 时先判类型**：搜索页/类目页 URL 走 C（discover --url），**绝不去 B 跟卖单商品**（解析会失败）
- 无 URL = 按意图词优先级选 C / D / E
- **蓝海评分只在管线 C 中使用**；「蓝海」默认路由 C，避免与 E 的「趋势」触发词冲突
- 管线 C（跟卖选品）和管线 D（选品上架）命令相同（discover），**区别只在提交给 Worker 的 follow_type**，采集流程一致
- ⚠️ **管线 E 必须先用 web_search 收集趋势**：调用 `trend` 命令前，先搜索
  `"{品类} Ozon 热门趋势 蓝海 细分品类 2025"`（可加俄语 `Ozon тренды 2025`、平台
  变体 `ozon.ru trends` 多角度搜），收集 3-5 条结果存文件，用 `--market-info` 传入。
  **不传 `--market-info` = 半残模式**（AI 只凭品类名总结，选品质量明显下降），
  除非用户明确表示不用搜索。
- ⚠️ **管线 B 降级语义变化**：Ozon 页面禁止复制时降级走管线 A（直采重建）——这是
  **重建直采卡，不是跟卖复制**（offer_id/定价都会变），必须向用户说明
- **指代不清必须追问**：用户说"类似的/这个/它"时，禁止根据上下文惯性猜测，先确认指代对象

---

## 4. 命令参考

> ⚠️ **速查表**：所有命令 + 副作用一览。完整参数见下方各管线小节（或 `--help`）。

| 命令 | 用途 | 关键参数 | 副作用 | 适用场景 |
|---|---|---|---|---|
| `check` | 验证环境（Chrome/凭证/Worker） | 无 | 无 | 首次使用 / 排错 |
| `set_store` | 配置 Ozon 店铺 | `--name --client-id --api-key` | 写 `data/config/` | 首次配置 |
| `set_token` | 配置 MXOU_TOKEN | `--token` | 写 `data/config/` | 首次配置 |
| `set_ak` | 配置 1688 AK | `--ak` | 写 `data/config/` | 首次配置 / AK 过期 |
| `update` | 检查并应用自动更新 | 无 | **覆盖 skill 文件**（备份 + 保留 data/） | 版本升级 |
| `get_ak` | 浏览器自动获取 1688 AK | `--timeout` | 无 | AK 过期时刷新 |
| `list_stores` | 列出已配置店铺 | 无 | 无 | 查看配置 |
| `graph` | 1688 上架（组装信封） | `--url/--item-id --store [--no-submit] [--category-query] [--retries]` | 提交 Worker（除非 `--no-submit`） | 用户发 1688 商品链接 |
| `follow` | Ozon 跟卖 | `--ozon-url --store [--auto-submit]` | 提交 Worker（加 `--auto-submit`） | 用户发 Ozon 商品链接 |
| `image_search` | 以图搜款 | `--image [--source cdp] [--sort] [--limit]` | 耗 1688 图搜配额 | 用户发图片 / 找同款 |
| `discover` | Ozon 选品（采集→分析→挑选→货源） | `--keyword/--url/--max-products [--rules] [--export] [--auto-submit]` | 查 seller.ozon.ru 运营指标 | 找蓝海 / 跟卖选品 |
| `trend` | 趋势驱动选品 | `--category [--market-info] [--max-price] [--export] [--with-skus]` | 耗 1688 配额 | 品类趋势 / 蓝海细分 |
| `search` | 1688 关键词搜索 | `query [--page-size]` | 耗 1688 搜索配额 | 按词找货 |
| `probe` | CDP 探针抓取单个 1688 商品 | `--url [--timeout]` | 无 | 调试单个商品 |
| `batch_test.py` | 批量处理 URL 列表 | `--urls-file [--submit] [--wait] [--dry-run] [--start] [--limit] [--delay]` | 提交 Worker（加 `--submit`） | 批量上架 / 回归 |

### 管线 A：1688 上架

**触发**：用户消息含 `1688.com` 链接，或管线 B 降级

```bash
python3.12 scripts/cli.py graph --url "https://detail.1688.com/offer/xxx.html" --store "主店铺"

# 只组装信封不提交（调试/确认场景，不会上架）
python3.12 scripts/cli.py graph --url "https://..." --store "主店铺" --no-submit

# 用商品 ID（无 URL 时）+ 指定 Ozon 类目俄语关键词
python3.12 scripts/cli.py graph --item-id "980815374096" --category-query "поилка" --store "主店铺"
```

- **输入**：1688 商品 URL、店铺名
- **参数**：
  - `--url` / `--item-id`（二选一）：1688 商品链接 或 商品 ID
  - `--store`：Ozon 店铺名（定价/凭证来源）
  - `--category-query`：Ozon 类目俄语关键词（帮助类目匹配，可选）
  - `--retries`：CDP 抓取重试次数（默认 3）
  - `--no-submit`：只组装信封不提交 Worker（调试）
- **输出**：JSON `{summary, envelope, submit_result}`
  - `summary`：商品摘要（标题、价格、重量、尺寸、图片数、属性数、供应商）
  - `envelope`：完整的 GraphInput 信封（发给 Worker 的数据）
  - `submit_result`：Worker 提交结果（见 §5）
- **自动完成**：CDP 抓取 1688 → 组装信封 → 提交 Worker

### 管线 B：Ozon 跟卖

**触发**：用户消息含 `ozon.ru` 链接

```bash
python3.12 scripts/cli.py follow --ozon-url "https://www.ozon.ru/product/xxx/" --store "主店铺" --auto-submit
```

- **输入**：Ozon 商品 URL、店铺名
- **输出**：JSON `{summary, envelope, submit_result}`
- **自动完成**：CDP 抓取 Ozon → 图搜 1688 同款 → 组装信封 → 提交 Worker

**降级**：Ozon 页面禁止复制（DataDome 拦截）时：
1. 用 Ozon Widget API 获取产品信息
2. 用产品图片在 1688 图搜同款
3. 走管线 A（直接上架，不走跟卖）

### 管线 C：跟卖选品（Discover v2）

**触发**：用户说"有什么好产品可以跟卖"、"帮我找可以跟卖的"（无 URL）

```bash
# ① 有关键词：搜索 → 全量采集 → 表格展示 → 交互挑选 → 批量找货源
python3.12 scripts/cli.py discover --keyword "宠物用品"

# ② 无关键词：直接打开 Ozon 中国站（highlight 页）滚动懒加载采集
python3.12 scripts/cli.py discover --max-products 30

# ③ 自动筛选规则（跳过交互）：月销量≥200 且 广告占比≤30% 且 跟卖≤20
python3.12 scripts/cli.py discover --keyword "宠物用品" --rules "monthly_sales>=200,drr<=30,seller_count<=20"

# ④ 价格区间过滤（RUB）：区间外产品标记 ⏭️价区间外，不参与挑选/运营指标查询
python3.12 scripts/cli.py discover --keyword "收纳" --min-price 300 --max-price 2000

# ⑤ 指定页面 URL 直接采集（搜索页/类目页）
python3.12 scripts/cli.py discover --url "https://www.ozon.ru/search/?text=собака"

# ⑥ 挑选 + 货源后确认提交 Worker
python3.12 scripts/cli.py discover --keyword "宠物用品" --auto-submit

# ⑦ 不查 seller.ozon.ru 运营指标（未登录卖家后台时自动降级，无需手动加）
python3.12 scripts/cli.py discover --keyword "宠物用品" --no-analytics
```

- **输入**：搜索关键词 或 Ozon 页面 URL 或 无（→ 中国站懒加载）
- **流程（v2，先采集后分析）**：
  1. **采集**：有 `--keyword` → 真实搜索页 `/search/?text=`；有 `--url` → 直接采集该页；都无 → 中国站 highlight 页滚动懒加载。结果容器限定（`.tile-root`），滚动到底部触发懒加载 + 等待渲染 + 翻页 + 去重
  2. **全量数据**：widget API（价格/标题/图/品牌/评分/评论数）+ 跟卖数/最低价 + **seller.ozon.ru 运营指标**（月销量/增长率/广告占比/上架天数——需卖家后台已登录，未登录自动降级，表格运营列显示 `—`）
  3. **表格分析挑选**：全量表格展示（含拒绝原因/状态）→ 人工按序号挑选 或 `--rules` 自动筛选 —— **此时不花 1688 配额**
  4. **批量货源**：只对选中的产品 1688 识图（CDP 图搜 → AK 图搜 → AK 关键词三级，含重试）→ 利润计算（真实重量/佣金）→ 蓝海评分 → 确认 → 提交
- **输出**：候选产品列表（全量落盘 `data/discovery/`，CSV 可导出）
- **规则字段**：`monthly_sales / gmv / drr / seller_count / margin / price / create_days / sales_growth / rating`
- **其他参数**：
  - `--rules`：自动筛选规则（跳过交互），逗号分隔，如 `"monthly_sales>=200,drr<=30,seller_count<=20"`
  - `--export csv|json|both` + `--output <路径>`：导出全量+选中结果
  - `--brand-filter`：`nobrand`（默认，只要无品牌/白牌）/ `known`（只过滤知名品牌黑名单）/ `all`（不过滤）
- **表格符号**：`✅可挑` 待分析 · `⚠️夹带?` 标题不含关键词 · `⏭️价区间外` 超价格区间 · `💰有利` 符合条件 · `⚠️利润低` 利润不足 · `❌无货源` 1688 没匹配到 · `—` 运营列无数据（卖家后台未登录）

**展示候选列表后，等用户确认再提交。不替用户选择。**

### 管线 D：选品上架

**触发**：用户说"帮我选品上架"、给关键词但没给 URL，意图是"上架"而非"跟卖"

**子路径 D1：1688 图搜**
```bash
python3.12 scripts/cli.py image_search --image "https://example.com/image.jpg"

# 用 CDP 图搜（比默认 AK 更准，准确率~100%）+ 按价格排序 + 限制条数
python3.12 scripts/cli.py image_search --image "https://..." --source cdp --sort price_asc --limit 5
```
- **输入**：图片 URL 或本地路径
- **参数**：
  - `--source`：`ak`（默认，1688 AK API）或 `cdp`（浏览器图搜，更准）
  - `--sort`：`price_asc` / `price_desc` / `sold_desc` / `yx_desc`
  - `--limit`：返回条数（默认 10）
- **输出**：JSON `{success, results: [{offer_id, title, price, image, shop_name}]}`

**子路径 D2：Ozon 选品**
```bash
python3.12 scripts/cli.py discover --keyword "宠物用品"
# 无关键词 → 直接采集中国站（highlight 页懒加载）
python3.12 scripts/cli.py discover --max-products 30
```
Discover v2 四阶段：采集（搜索/中国站懒加载）→ 全量数据（含运营指标）→ 表格挑选 → 批量 1688 货源 → 确认提交（详见管线 C）。

### 批量处理

```bash
python3.12 scripts/batch_test.py --urls-file urls.txt --submit

# 提交并轮询结果（完成后打印每个产品的 1688链接/利润率/售价/采购价/运费/净利润率/OzonID）
python3.12 scripts/batch_test.py --urls-file urls.txt --submit --wait

# 只组装信封不提交（验证信封，不花上架额度）
python3.12 scripts/batch_test.py --urls-file urls.txt --dry-run

# 从第 5 个开始处理 10 个，间隔 5 秒
python3.12 scripts/batch_test.py --urls-file urls.txt --submit --start 5 --limit 10 --delay 5
```

URL 文件混合 1688/Ozon 链接，自动识别管线。

参数：`--urls-file`（必填）、`--submit`（提交 Worker，默认不提交）、`--wait`（轮询到完成，含产品明细）、`--dry-run`（只组装验证）、`--start` / `--limit`（处理范围）、`--delay`（间隔秒）、`--wait-timeout`（轮询超时秒，默认 900）、`--type-filter`（按类型过滤 URL）。

### 其他命令（辅助）

```bash
# 1688 关键词搜索（按词找货，耗 1688 配额）
python3.12 scripts/cli.py search "宠物饮水机" --page-size 5

# 查看已配置店铺
python3.12 scripts/cli.py list_stores

# 浏览器自动获取 1688 AK（登录后自动复制）
python3.12 scripts/cli.py get_ak --timeout 300

# CDP 探针抓取单个 1688 商品（调试用）
python3.12 scripts/cli.py probe --url "https://detail.1688.com/offer/xxx.html" --timeout 30
```

### 管线 E：趋势驱动选品（v0.25）

**触发**：用户说"帮我找 {品类} 的蓝海/热卖/趋势商品"

```bash
python3.12 scripts/cli.py trend --category "玩具" --market-info trend_info.txt --max-price 50 --max-moq 10 --min-ship-rate-48h 80 --min-sales 100
```

**流程**：
1. **收集市场信息（强制第 0 步）**：用 web_search（或 SearXNG）搜索，建议多角度各搜 1 次：
   - 中文：`"{品类} Ozon 热门趋势 蓝海 细分品类 2025"`
   - 俄语：`"{品类} Ozon тренды 2025 ниша"`
   - 平台：`"ozon.ru {品类} bestsellers"`
   把 3-5 条结果内容保存为文本文件，用 `--market-info <文件>` 传入；配置了 `SEARXNG_URL` 时也可自动抓取。**不要跳过此步直接跑 trend**——没有市场信息的 AI 总结质量明显下降。
2. **AI 提炼关键词**：复用 mxou LLM，输出 5-8 个可直接搜 1688 的中文细分关键词（严格 JSON，含潜力原因）；解析失败 → 明确报错，不脑补。
3. **AK 搜索**：并发搜索前 3 个关键词（≤3 在飞），每个取 Top1；**累计满 3 个有效商品立即停止**；某关键词无结果则补位下一个。
4. **展示**：细分市场卡片（图片/价格/起批量/48H 揽收率/销量/供应商）+ 全 SKU 明细表（3 倍建议价）+ 汇总表；`--with-skus` 用 CDP 拉 SKU，`--export json|csv` 导出。

筛选参数：`--max-price`（元）、`--max-moq`（件）、`--min-ship-rate-48h`（%）、`--min-sales`（件）。
其他参数：`--with-skus`（CDP 拉 Top 商品 SKU 明细）、`--export json|csv|both` + `--output <路径>`（导出结果）。

### 4.1 输出字段解析

所有业务命令（graph / follow / batch_test）输出 JSON，关键字段：

| 字段 | 类型 | 含义 | agent 取值方式 |
|---|---|---|---|
| `summary` | dict | 商品摘要 | 提取标题、价格、重量、图片数 → 汇报给用户 |
| `envelope` | dict | 完整 GraphInput 信封 | 内部数据，不需解析 |
| `submit_result.ok` | bool | 提交是否成功 | `true` → 按 §5.1 回复；`false` → 按 §5.2 回复 |
| `submit_result.task_id` | str | 任务 ID | 提取后告知用户，用于后续查询 |
| `submit_result.error_code` | str | 错误码 | 按 §5.2 错误码表回复 |
| `product_summary[]` | array | 产品明细（`--wait` 轮询后） | 提取 1688链接/利润率/售价/采购价/运费/净利润率/OzonID → 表格展示 |

**成败判定**：`submit_result.ok == true` → 成功；否则按 `error_code` 查 §5.2 表。

**汇报模板**：
- 成功：`✅ 任务已提交，任务 ID: {task_id}。预计 10–20 分钟完成。`
- 失败：按 §5.2 错误码表回复（含修复指引）。
- 轮询完成（batch_test --wait）：`✅ 任务完成。产品明细：[表格]`。

---

## 5. Worker 响应处理

CLI 命令输出中的 `submit_result` 字段包含 Worker 的响应。按以下模板回复用户。

### 5.1 提交成功

Worker 返回：
```json
{"ok": true, "task_id": "550e8400-...", "message": "Task submitted to queue"}
```

回复用户：
> ✅ 任务已提交到云端处理
> - 任务 ID：`{task_id}`
> - 预计耗时：10–20 分钟（类目匹配 → AI 生图 → Ozon 上架 → 审核）
> - 你可以在 Ozon 卖家后台查看上架结果，或稍后用 `batch_test --wait` 查询。如有问题 Worker 会自动重试修复。

### 5.2 提交失败

| Worker 错误码 | 原因 | 回复用户 |
|--------------|------|----------|
| `TOKEN_INVALID` / `TOKEN_MISSING` | MXOU_TOKEN 无效或缺失 | "凭证无效，请重新设置 MXOU_TOKEN：`python3.12 scripts/cli.py set_token --token <你的token>`" |
| `TOKEN_DISABLED` / `TOKEN_EXPIRED` | 账户被禁用或过期 | "账户已被禁用或过期，请联系管理员。" |
| `INSUFFICIENT_BALANCE` | 余额不足 | "账户余额不足（{detail.remain_quota}），请充值后重试。" |
| `RATE_LIMITED` | 请求太频繁 | "请求太频繁，请稍后再试（每分钟限制 {limit} 次）。" |
| `INVALID_REQUEST` | 信封数据不完整 | "产品数据不完整：{message}。请检查 1688 商品页是否正常加载，或重试。" |
| `TASK_SUBMIT_FAILED` | 队列写入失败 | "任务入队失败，Worker 内部错误。请稍后重试。" |
| `SERVICE_UNAVAILABLE` | 服务不可用 | "云端服务暂时不可用，请稍后重试。" |
| `INTERNAL_ERROR` | 未知内部错误 | "Worker 内部错误：{message}。请稍后重试，如持续出现请联系技术支持。" |
| 网络错误（ConnectionError） | Worker 不可达 | "无法连接云端服务。请检查网络连接和 WORKER_URL 配置。" |
| 网络错误（Timeout） | 请求超时 | "云端服务响应超时，请稍后重试。" |

### 5.3 查询进度

用户问"进度"、"完成了没"时：

- **批量提交**：用 `batch_test.py --wait` 自动轮询（每 5s 查一次），完成后打印每个产品的明细（1688链接/利润率/售价/采购价/运费/净利润率/OzonID）。
- **单任务查询**：CLI 未暴露单任务查询子命令。如用户追问单个任务进度：
  1. 告知任务正在云端处理中（类目匹配 → AI 生图 → Ozon 上传 → 审核），预计 10–20 分钟
  2. 建议用户等待后用 `batch_test.py --wait` 查看结果，或在 Ozon 卖家后台查看商品状态
  3. 不要自行调 Worker API 轮询（skill 无此命令）

---

## 6. 决策边界

| 操作 | 策略 | 说明 |
|------|------|------|
| `check`、`pip install`、`set_store`、`set_token`、`set_ak` | 自动执行 | 环境准备类操作，无需确认 |
| `graph`、`follow`（含 `--auto-submit`） | 自动执行 | 用户给了明确 URL，直接上架 |
| `discover` 选品后的最终提交 | 必须确认 | 展示候选列表，等用户说"提交" |
| 批量处理 | 必须确认 | 影响面大，需用户明确确认 |
| 利润率高低、候选产品优劣 | 展示不表态 | 陈列数据，不替用户判断 |

---

## 7. 错误处理

| 错误 | 回复用户 |
|------|----------|
| 1688 验证码拦截 | "1688 出现验证码，请在 Chrome 浏览器中滑动验证后按 Enter 继续。" |
| 1688 未登录 | "1688 未登录，请在 Chrome 中打开 1688.com 登录后告诉我。" |
| Ozon DataDome 拦截 | "Ozon 页面被反爬拦截，请在 Chrome 中访问一次 Ozon 后告诉我。" |
| 1688 AK 缺失 | "缺少 1688 AK。请执行：`python3.12 scripts/cli.py set_ak --ak <你的AK>`" |
| Ozon 店铺未配置 | "店铺未配置。请执行：`python3.12 scripts/cli.py set_store --name '店铺名' --client-id <ID> --api-key <KEY>`" |
| 图搜无结果 | "1688 上未找到同款产品。要不要试试用关键词搜索？" |
| Worker 返回错误 | 按 §5.2 错误码表回复用户 |
| AI 关键词输出非法 JSON | 明确报错「关键词总结失败：JSON 解析错误」，不猜测关键词继续 |
| 市场信息缺失 | 提示用 --market-info 传入 web_search 结果或配置 SEARXNG_URL；不凭空编造趋势 |

**遇到任何错误，描述问题并引导用户修复。不自己修代码、不自己探索项目结构。**

---

## 8. 常见越界行为

| 错误行为 | 后果 | 正确做法 |
|---------|------|---------|
| 自己写 Python 代码调 API | 逻辑不完整、缺错误处理 | 用 `cli.py` 命令 |
| 自己探索项目目录结构 | 浪费时间、可能改错文件 | 看本文档 |
| 给 Ozon URL 还去算蓝海评分 | 逻辑混乱 | 有 URL 直接处理 |
| 给 1688 URL 还去 Ozon 搜索 | 多余操作 | 有 URL 直接处理 |
| 替用户决定"这个利润太低不上了" | 用户失去控制权 | 展示数据让用户决定 |
| 在用户没说"提交"时就提交 Worker | 用户没确认就上架 | 等用户明确说"提交" |
| 对话长了就忘记意图路由规则 | 管线混乱 | 每次操作前重读 §3 |
| 把蓝海逻辑混入跟卖流程 | 数据错误 | 蓝海只在管线 C |

---

## 9. 参考文件

| 文件 | 用途 |
|------|------|
| `envelope_example.json` | 完整信封结构示例（单 SKU + 跟卖两种模式） |
| `field_mapping.md` | 1688/Ozon 字段 → 信封字段的映射规则 |

---

## 10. 更新与旧包升级

**自动更新（v0.18.0 起，默认开启）**：每次运行命令时，若 COS 上有新版本，
会自动备份旧文件 → 覆盖升级 → 失败自动回滚（`data/` 凭证/登录态/缓存全程保留），
升级成功后提示重启终端。

- 关闭自动更新：`export SKILL_AUTO_UPDATE=0`，退回「提示 + 手动 `skill update`」模式。
- 手动更新：`python3.12 scripts/cli.py update`

**旧包升级（v0.12.0 之前的包没有 updater，不会自动提示）**：

```bash
# 1. 从最新 GitHub Release 下载 bootstrap_update.py 到 skill 包目录
#    https://github.com/halojerry/ozon-worker/releases
# 2. 运行（会下载最新包 → sha256 校验 → 覆盖升级 → 失败回滚）
python3.12 bootstrap_update.py
```

如果运行 `graph`/`follow` 提示「未找到 scripts.cloud_probe（版本过旧）」，
按上面 bootstrap 升级即可。手动确认当前版本：`python3.12 scripts/cli.py update`
（显示「已是最新」即正常）。

## 11. data/ 目录语义（防误删）

| 路径 | 用途 | 可否删除 |
|---|---|---|
| `data/config/` | 凭证（stores.json / token / ak） | ❌ **绝对不能删**（删了要重新配置全部凭证） |
| `data/discovery/` | discover 选品结果落盘 | 可清理旧文件 |
| `data/logs/` | 运行日志 | 可清理旧文件 |
| `data/cache/` | 磁盘缓存（TTL 自动过期） | 可清理 |
| `wave*.txt` / `urls_*.txt` / `test_run_*` | 测试遗留文件 | 可删除 |
