# 上架类命令（graph / follow / image_search / search / batch_test）

> v0.79 拆分自 command-reference.md（PLAN-agent-ergonomics-v1 D3）。选管线见 routing.md；
> 选品类命令见 commands-discovery.md；运维类见 commands-ops.md。
> 提交确认口径：routing.md「提交确认二分法」——明确意图直提（可 `--wait`），弱意图先展示。

## 目录
- [管线 A：1688 上架（graph）](#管线-a1688-上架graph)
- [管线 B：Ozon 跟卖（follow）](#管线-bozon-跟卖follow)
- [管线 D1：以图搜款（image_search）](#管线-d1以图搜款image_search)
- [1688 关键词搜索（search）](#1688-关键词搜索search)
- [批量处理（batch_test.py）](#批量处理batch_testpy)

## 管线 A：1688 上架（graph）

**触发**：用户消息含 `1688.com` 链接，或管线 B 降级

> ⚠️ 串行闸：graph 与 discover/discover-multi/discover-task/follow/seller 跨进程互斥，
> 闸被占 exit 4（报错含占用方；`--wait` 排队 / `--force` 强制并行，详见 routing.md「并发限制」）。

```bash
python3 scripts/cli.py graph --url "https://detail.1688.com/offer/xxx.html" --store "主店铺"

# 弱意图场景（看看/评估/能不能上）：只组装信封不提交，展示等确认
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
  - `--no-submit`：只组装信封不提交 Worker（弱意图展示态）
  - `--ozon-ref-url`：Ozon 竞品参考链接（v0.29.x）——抓该竞品同类目属性复用，属性填充更准（可选）
  - `--notify`：提交时 GraphInput 顶层携带 `notify=True`，Worker 完成推送 webhook（需 Worker 配置 `TASK_NOTIFY_URL`）
  - `--min-margin <N>`：提交前预估利润率低于 N% 拦截（exit 3；预估非终价）
  - `--wait`：提交后轮询 Worker 到终态再退出（completed/failed 各打一行；failed → exit 3）
- **输出**：JSON `{summary, envelope, submit_result}`（字段解析见 output-schema.md）；
  出口末行 `👉 NEXT:` 提示下一步（v0.79）
- **自动完成**：CDP 抓取 1688 → 组装信封 → 提交 Worker
- **⚠️ SKU 去重（v0.38 N1）**：同店铺同商品已有活跃任务（pending/running）时重复提交返回 409 `DUPLICATE_SUBMIT`。去重键含店铺维度 `{user}:{store}:{product}`——**同用户不同店铺可提交同款**（互不拦截）。终态任务（completed/failed/rejected/cancelled）不占用去重名额，可重新提交。收到 `DUPLICATE_SUBMIT` 时用 `query <task_id>` 查既有任务状态，而非反复重提
- **执行后验证**：① `--no-submit` → 对照 `envelope_example.json` 检查信封字段完整性（title/images/weight/dimensions/purchase_cost 必填）再提交；② 已提交 → 记录返回的 `task_id`，带 `--wait` 或 `query <task_id> --watch` 跟踪，终态后再向用户汇报（勿让用户盲等）

**多店铺**：`--store` 指定店铺名（`data/config/stores.json` 的 key）；省略用 `default` 字段指向的默认店铺。详见 routing.md「多店铺」。

## 管线 B：Ozon 跟卖（follow）

**触发**：用户消息含 `ozon.ru` 商品链接

> ⚠️ 串行闸：follow 与 graph/discover 族/seller 跨进程互斥，闸被占 exit 4（`--wait` 排队 / `--force` 强制并行）。
> ⚠️ follow 腿当前**未接 `_source_preflight`**（反爬/源失效前置拦截，fix/arch-findings-v1 对齐中）——
> 跟卖单提交前只有 min-margin 一道闸；信封里 purchase_cost≤0 / 零图时人工核一眼再提。

```bash
# 明确跟卖意图：直接提交 + 等终态
python3 scripts/cli.py follow --ozon-url "https://www.ozon.ru/product/xxx/" --store "主店铺" --auto-submit --wait

# 弱意图（多少钱/评估）：缺省即展示 1688 候选，不提交
python3 scripts/cli.py follow --ozon-url "https://www.ozon.ru/product/xxx/" --store "主店铺"

# 人工评审：展示全部 1688 候选，人工接受/改选/拒绝后组装（不自动挑）
python3 scripts/cli.py follow --ozon-url "https://..." --store "主店铺" --auto-submit --review

# 完成时推送 webhook 通知（需 Worker 配置 TASK_NOTIFY_URL）
python3 scripts/cli.py follow --ozon-url "https://..." --store "主店铺" --auto-submit --notify
```

- **输入**：Ozon 商品 URL、店铺名
- **参数**：
  - `--ozon-url`（必填）：Ozon 商品页 URL
  - `--store`：Ozon 店铺名
  - `--auto-submit`：自动提交 Worker（不加则只组装不提交——弱意图展示态）
  - `--review`：人工评审暂停（v0.38）——展示全部 1688 候选，人工接受/改选/拒绝，决策写 review_log
  - `--notify`：提交时 GraphInput 顶层 `notify=True`，Worker 完成推 webhook
  - `--min-margin <N>` / `--wait`：语义同 graph（预估拦截 / 等终态）
- **输出**：JSON `{success, product_id, slug, images, title, 1688_matches, task_id}`；
  出口末行 `👉 NEXT:` 提示下一步（v0.79）
- **自动完成**：CDP 抓取 Ozon → 图搜 1688 同款 → 组装信封 → 提交 Worker
- **执行后验证**：① 图搜 `1688_matches` 为空且 `no_relevant_match=true` → 告知用户"1688 未找到同款"，不提交空壳，询问是否换货源或改关键词；② 已提交 → `query <task_id> --watch` 跟踪，`rejected`（审核被拒）时按 error-codes.md 引导用户看 Ozon 卖家后台拒绝原因

**跟卖双模式（`extensions.follow_type`，v0.22 起）**：

| 模式 | 说明 | 适用 |
|------|------|------|
| `hand`（默认） | 防侵权——跳过 import-by-sku 1:1 复制，走 CREATE 重建（管线重做类目/属性/生图，天然防同款/侵权检测） | Skill 找到 1688 货源 |
| `api` | import-by-sku 复制竞品卡 | Skill 无货源时由 Worker 自动降级 |

- **offer_id 约定**：统一 `follow_{竞品ID}`（import-by-sku / assemble / prepare 三处一致，防 api 模式双卡）
- **自动降级**：Skill 无 1688 货源 → Worker hand 模式自动降级 api 复制；图搜 `no_relevant_match`（相关性护栏拒绝）→ **直接拦截不组装**（v0.26 起，不提交空壳）

**降级（DataDome 拦截）**：Ozon 页面禁止复制时：
1. 用 Ozon Widget API 获取产品信息
2. 用产品图片在 1688 图搜同款
3. 走管线 A（直采重建，非跟卖复制——offer_id/定价都会变，须向用户说明）

## 管线 D1：以图搜款（image_search）

**触发**：用户发图片（无 URL）要找 1688 同款

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

## 1688 关键词搜索（search）

**触发**：用户给关键词按词找货（1688 侧）。

```bash
python3 scripts/cli.py search "宠物饮水机" --page-size 5
python3 scripts/cli.py search "宠物饮水机" --rules "ai,margin>=20" --export out.csv

# 双出口（v0.70，互斥二选一）：--auto-submit 直上 worker 管线 / --to-box 入采集箱
python3 scripts/cli.py search "宠物饮水机" --rules "ai" --to-box --store "3号店"
python3 scripts/cli.py search "宠物饮水机" --rules "ai" --auto-submit   # 必须确认后才跑
```

- 耗 1688 搜索配额；`--rules` 两段式同 discover（挑选期/匹配期，`"ai"` 一键预设）
- 出口 flag 触发逐个信封组装+提交；`--wait` 语义同 graph
- **⚠️ 门禁缺口（fix/arch-findings-v1 对齐中）**：批量提交腿（`--auto-submit`）当前**绕过
  preflight/min-margin/min-density**，且单条失败不影响出口码（恒 0）——需要逐单利润拦截时改走
  `graph` 逐条提交，跑完后逐行核对输出里的失败项

## 批量处理（batch_test.py）

**触发**：用户发多个链接 / "批量处理这些 / 整一批"。

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

> ⚠️ batch_test 进程内直调组装链，**不进 heavy 串行闸**（六命令闸拦不住它）——不要与
> graph/follow/discover 并行跑（会互踩 Chrome/缓存）；出口码 0=全部成功，1=有失败项。

参数：`--urls-file`（必填）、`--submit`（提交 Worker，默认不提交）、`--wait`（轮询到完成，含产品明细）、`--dry-run`（只组装验证）、`--start` / `--limit`（处理范围）、`--delay`（间隔秒，默认 3.0）、`--wait-timeout`（轮询超时秒，默认 900）、`--type-filter`（按类型过滤 URL：`1688`/`ozon`/`all`）、`--resume`（断点续传，跳过已成功项）、`--resume-from`（显式指定续传来源结果文件，默认自动找最新）、`--notify`（提交时 `notify=True`，Worker 完成推 webhook）。

凭证：`--store-id <店铺名>` 从 `data/config/stores.json` 取凭证（同 graph/follow 的 `--store`）；不指定时用环境变量 `OZON_CLIENT_ID` / `OZON_API_KEY`。

**执行后验证**：① `--dry-run` → 核对每个信封字段完整性（对照 envelope_example.json）；② `--wait` 完成后 → 逐产品核对明细（OzonID/利润率/审核状态），失败的标记出来单独汇报；③ 部分失败 → 可 `--resume` 断点续传只重试失败项。
