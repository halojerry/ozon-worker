# PLAN — 用户 11 问修复方案：skill 静默化/可观测性 + worker 守卫精化 v1

> 2026-09-19。动因：0.77.3 gate 实机跑批（graph 5/5 / follow 6/6 / discover 6/5 approved）后用户提出 11 个问题。
> 取证方式：三路并行源码探查（skill CDP/静默链、aibuy 图搜+日志、worker 守卫/图片/定价）+ gate 运行日志（/tmp/dw7_skill.log 等）交叉验证；
> 关键结论均做二次抽查（`new_tab` 默认前台、readiness 预热无负缓存已亲读源码确认）。
> 状态：**方案待拍板，未开工**。skill侧行号基于 main（与 dev 差异仅 P7 一处）；worker 侧行号基于 origin/dev（含 PR #42）。

## 0. 十一问快速结论表

| # | 问题 | 一行结论 | 状态 |
|---|---|---|---|
| 1 | 静默匹配却前台开 Chrome | `new_tab()` 默认 foreground（`/json/new` 激活 tab）+ readiness 预热失败不缓存（每命令前台开 1688 首页） | **本批修（批A）** |
| 2 | seller.ozon.ru/ch/ 与 1688 主页反复开 | seller 根路径重定向到 /ch/；失败结果永不缓存 + readiness 每命令探测 + tab 被关后重建 | **本批修（批A）** |
| 3 | logger 黑盒 | 全 skill 仅 cloud_probe import 副作用配了一次 logging；discover/check/image_search 的 INFO 全被丢弃；无计时/无终局报告 | **本批修（批B）** |
| 4 | agent 重复步骤绕路，是否拆 skill | 根因是命令编排碎片（提交→手工 query→重提），不是 SKILL.md 结构（仅 138 行已渐进披露）。**建议不拆**，改一次性命令 `--wait` | 拍板（建议不拆） |
| 5 | 直链是否直接上架不算利润拦截 | 是——graph 默认自动提交（`--no-submit` 才跳过），预估只打印不拦截；follow 连预估都不打印 | 已是设计；可选增强拍板 |
| 6 | aibuy 图搜可用图片 URL | **已经支持**——搜索参数就是 `imageAddress`(URL)，upload 失败自动降级原始 URL 直搜；真缺口是 upload 腿 413 无压缩 | **本批修（批A）** |
| 7 | 零交集闸应认面包屑 | validate 侧预检与 assemble 侧豁免不一致：assemble 认 page/what_to_sell/manual 权威，validate 预检不认（"前门豁免后门杀"） | **本批修（批C）** |
| 8 | 价差守卫跨币种假阳性 | 止血已合 dev（currency_mismatch skip）；增强=用 pricing 的 exchange_rate 换算后同比 | 止血在 dev；增强本批（批C） |
| 9 | Supabase 未配置白烧 4s | 已修 dev（URL 缺失/非法→零重试零 sleep）。角色澄清：生产仍靠 Supabase tokens 表做用户鉴权，非死代码 | **已修 dev (0.77.3)** |
| 10 | config 挂空重试 4 轮 40s 根因 | 根因=compose 从已删除 worktree 目录起栈→bind 源路径不存在→Docker 静默挂空目录。已修双层（永久错误分类+启动守卫）；追加固化 | 主体已修 dev；追加批D |
| 11 | 上架的是 AI 图还是 1688 原图 | **默认全 AI 生图**（5 槽位）；1688 原图仅作生成参考；仅当全部生成失败才 E1 兜底转存 COS 上原图 | 已是设计；原图模式可拍板 |

---

## 1. Q1+Q2：静默契约被前台弹窗破坏（生产 bug）

### 现象
约定 1688/淘宝/pxx 货源匹配全程静默（aibuy mtop 直调免浏览器），实跑中 Chrome 反复弹前台：1688 首页、`seller.ozon.ru/ch/`、about:blank 空白 tab 闪动。

### 根因（按贡献频率排序，全部源码实锤）

**R1 — `new_tab()` 默认前台激活**。`skill/scripts/lib/cdp_client.py:306`：
`def new_tab(self, url="about:blank", background: bool = False)` — docstring 自述"可见 tab，激活到前台"。`background=False` 走 `PUT /json/new`（Chrome 会创建**并激活** tab）。全 skill 只有 2 处用了 `background=True`（CDP 图搜 `ozon_image_search.py:326`、跨源搜索 `cross_source_search.py:544`），其余十几个建 tab 点全部前台。

**R2 — readiness aibuy 预热失败不缓存，每命令前台开 1688 首页**。`readiness.py:251-262`：`aibuy_token` 探针失败 + `prewarm=True` → `_fetch_aibuy_cookies_from_chrome()` → `ozon_image_search.py:671` 前台 `new_tab()` + `:673` 导航 `https://www.1688.com/`。探针**只缓存成功**（`readiness.py:180-196`，TTL 600s），失败不落负缓存 → 1688 cookie 失效的机器上**每条命令必弹一次**。discover/discover-multi/discover-task/follow 管线都含该探针；5 个 CLI 调用点都没传 `prewarm=False`（cli.py:549/1291/1465/2073/2706）。

**R3 — seller 页失败永不缓存 + readiness 每命令探测**。`/ch/` 不是代码写死的——是 `https://seller.ozon.ru/` 根路径的地域重定向。`_tab_for_seller`（`ozon_seller_analytics.py:445-476`）：复用已有 seller tab，未命中才前台 `new_tab()` + 导航根路径。tab 本有常驻复用（`_close_seller_tab` :494-507 不远程关闭），但三个放大器：
- `fetch_sales_analytics`（:692-792）与 `fetch_bestseller_metrics_map`（:1324-1348）**只缓存非空成功**（:784-785/:1346-1347）——未登录/失败时每次重跑都重开 seller 页；
- follow Step 2.5 缓存 key=单 SKU（cloud_probe.py:4375）→ 每个新商品首次必 miss；
- readiness `seller_login` 探针失败 → 交互路径直接 `wait_for_seller_login` 开页（readiness.py:289-290）。

**R4 — "静默读"探针全在闪前台空白 tab**。`_read_1688_cookies_silent`（:725）、`_read_seller_cookies_silent`（:521）、`_cdp_get_cookies_sequence`（:971）、`probe_alibaba_login`（readiness.py:80）全部用默认 foreground `new_tab("about:blank")`；登录等待轮询期间每 5s 闪一次。`get_seller_session_cookies`（:1048-1072）每次先活读（每命令/每商品一次空白 tab），30min 快照仅活读失败才用。

**R5 — aibuy 空结果不缓存，每候选降级**。`search_by_image_aibuy` 只有非空才 cache（:1047-1048）；aibuy 持续失败的批次里**每个候选**都烧一遍 aibuy→CDP 降级链（`ozon_discovery.py:3319 _search_1688_source` 串行循环）。CDP 图搜本身已是后台 tab，但高频创建/销毁 + 每次 ≤3s 重试（:3412-3416）拖慢匹配阶段。

**R6 — 冷启动 Popen 拉起 GUI Chrome 被 OS 激活**（chrome_launcher.py:527-539）。仅 Chrome 未以 CDP 运行时发生一次，属可接受。

（chrome_launcher/ensure_chrome_cdp 本身无 activate/bringToFront/杀重启行为——v0.76 三态探活后 CDP up 即返回 :412-413，已排除嫌疑。）

### 修复设计（批A `fix/skill-silent-cdp-v1`）

**A1 静默场景全部改 `background=True`**（改调用点，接口不动）：
- 预热/导航类：`_fetch_aibuy_cookies_from_chrome` :671、`_tab_for_seller` :467、discover 阶段①开搜索页（ozon_discovery.py:879）、readiness ozon 探针 :118；
- 静默读类：`_read_1688_cookies_silent` :725、`_read_seller_cookies_silent` :521、`_cdp_get_cookies_sequence` :971、`probe_alibaba_login` readiness:80；
- 三平台匹配契约：`taobao_client.py:562/653`、`pdd_client.py:644/740` 全改后台；
- 保留前台的白名单：验证码人工介入重试（ozon_scraper.py:566）、check 诊断命令的 `_open_tab`（cli.py:901-910，用户主动触发想看见）、首次 `wait_for_seller_login` 的登录引导页（人工要登录，但轮询读改后台）。
- 风险评估：后台 tab 的 rAF 节流可能影响滚动提取——但 CDP 图搜与 cross_source_search 已在后台跑滚动提取且生产正常，先例充分；测试覆盖一轮 discover 实机。

**A2 readiness 失败负缓存**：`readiness.py` 探针失败也写 600s 负缓存（带"负缓存命中"日志），杜绝每命令重复预热导航；非交互管线调用点传 `prewarm=False`（interactive 分支保留首跑预热一次）。

**A3 seller 失败负缓存窗**：`fetch_sales_analytics`/`fetch_bestseller_metrics_map` 失败/未登录 → 600s 负缓存（对齐既有 DataDome 短路窗先例 :1075-1102），防每命令/每商品重开 seller 页。

**A4 aibuy run 级熔断**：批内连续 3 次 aibuy 异常/空结果 → 本进程剩余候选直接走 CDP 后台通道并打一行汇总日志（"aibuy 连续失败 3 次，本批熔断"）。token 级 600s claim 闸已有（:581-630），这是批级补充。

### 测试
- 单测：mock CdpConnection 断言各静默调用点 `new_tab(background=True)`；负缓存 TTL 语义；熔断计数。
- 实机 gate：discover 一轮全程**零前台 tab 弹出**（macOS 用 NSWorkspace frontmost 断言或人工观察录屏）+ 图搜命中率与改前持平。

---

## 2. Q3：运行日志黑盒

### 根因
- **全 skill 只有一处 logging 配置**且是 import 副作用：`cloud_probe.py:59-66` basicConfig(INFO, stderr)。`discover`（提交腿才 import cloud_probe，cli.py:1814）、`discover-task`（:2914）、`image_search`、`check` 全程 INFO 被 Python lastResort 丢弃——`ozon_discovery` 丰富的阶段日志（:739/:863/:887/:910/:1501/:1760）在这些命令下**全部不可见**。
- 无任何 per-run 日志文件；无阶段耗时；follow 直提模式 task_id 只埋在 `_out` JSON 不打行（graph 有 :732）；`poll_task_status`（cloud_probe.py:4092-4122）轮询静默，worker 断连 900s 零输出。
- discover 终局只有计数（cli.py:1692），失败明细一闪而过；AuditLogger JSONL 写后无人读（`read_task_log` 全仓零调用，docstring 承诺的 `check --logs` 不存在），且图搜审计 `task_id="unknown"`（:470）。

### 修复设计（批B `feat/skill-run-logging-v1`）
1. `logging_utils.setup_run_logging(cmd)`：cli `main()` 顶部统一调用——stderr(INFO 现格式) + 文件 sink `data/logs/run_{ts}_{cmd}.log`(DEBUG)，**启动即打印日志路径**；cloud_probe 的副作用配置降级为兜底（root 无 handler 时才生效）。
2. `log_stage()` contextmanager：enter/exit INFO + 耗时秒数，替换 cli.py:1494/2770/2829/2885 与 follow 各步的阶段 print。
3. follow 提交腿补 task_id stdout 行（对齐 graph :732-733）；`--wait` 模式轮询每 poll DEBUG、状态变化 INFO、连续 3 次 worker_unreachable WARNING。
4. discover/discover-task 终局落 run 报告 JSON（成功/失败清单 + task_id/draft_id/失败原因首行），路径打印。
5. `check --logs [task_id]` 实现（消费 read_task_log）；图搜审计接真实 task_id。
6. aibuy 降级 warning 带连续失败计数（配合 A4 熔断日志）。

---

## 3. Q4：agent 绕路——拆 skill 还是保持渐进披露？

### 结论：**不拆，保持单 skill + 渐进披露**，把力气花在"一次性命令"上。

依据：
- SKILL.md 仅 **138 行**，结构已是速查表+意图路由+references/ 渐进披露——agent 绕路不是文档问题。
- 真正的绕路在命令编排碎片：`graph` 提交后 fire-and-forget（graph/follow/discover 提交腿从不轮询，agent 只能手工 `query --watch` 或自写脚本），加上 discover 交互确认断腿（P7 已修 dev）。
- 拆多 skill 的代价：Chrome/锁/凭证/stores.json 四套共享状态要跨 skill 传递，agent 上下文反而更重；运维面（编译/发版/ pounding-mcp 映射）翻倍。

### 修复设计（并入批B）
1. graph/follow/discover(提交腿) 增 `--wait`：提交后自动 `poll_task_status` 到终态，打印终态 + product_id/失败原因（poll_task_status 已有，只差接线）。
2. SKILL.md 命令表补 `--wait`/`--yes`（P7 非交互确认）两列 + 顶部加「最短路径」三条 recipe（一条 1688 链接→上架；一条 Ozon 链接→跟卖；一条关键词→选品）。
3. 长任务继续走 pounding-mcp `job_*` 后台四件套（job_status/job_result/job_list/job_cancel 已有）——这正是它的设计用途，SKILL.md 引导 agent 用它替代手工轮询。

---

## 4. Q5：直链是否直接上架、不算利润拦截？

### 现状（源码实锤，与你的预期一致）
- **graph（1688 链接）**：组装信封 → 打印预估（cli.py:632-668：采购+运费→预估售价/利润/利润率，复用 worker 公式参数同源）→ **默认自动提交**（:671 注释"默认自动提交到 Worker，--no-submit 跳过"）。预估**只打印不拦截**；真正的拦截闸只有 source_preflight（反爬/源失效，:694-703）和可选 `--min-density`（:706-712）。
- **follow（Ozon 链接）**：连预估打印都没有，直接提交；定价全部 worker 侧实算。
- discover 是唯一有利润筛选（profitable 状态）+ 人工挑的管线。

### 可选增强（拍板项）
follow/graph 补预估打印（follow 腿数据已在信封里，一行计算）+ `--min-margin`（默认 0=不拦截，行为零变化）对齐 discover 语义。默认仍直上——直链场景用户意图明确，拦截反而违背"给链接就上"的预期。

---

## 5. Q6：aibuy 图搜用图片 URL

### 结论：**已经支持，且早就在用**
- aibuy 搜索参数本身就是 URL：`searchParam.imageAddress`（ozon_image_search.py:906-910）；上传步 `_aibuy_image_upload`（:868-896）返回 1688 托管 URL 只是为了搜索更稳。
- **upload 失败自动降级原始 URL 直搜**：`:1007-1008` `search_img = uploaded_url or image_url`——413 时 Ozon 原图 URL 直接进 `imageAddress`。模块 docstring（:13-17）与 I-9 实测（aibuy 服务器可直接抓 ir.ozone.ru，无需 COS 转存）都确认这条路。
- AK 通道同款先例：`ak_1688_client.py:707-714` 优先 `imageUrl` 直传，"1688服务器直接抓取，无质量损失"。

### 真缺口与修复（并入批A）
gate 实跑的"返回空降级 CDP"是**两条腿都断**：upload 腿 413（原图 base64 膨胀 1.33× POST，无任何压缩——:875-891 只有一个 100 字节下限检查）+ URL 腿偶发空。修复：
1. upload 前压缩：downscale ≤1024px + JPEG q80 后再 base64（image_preprocessor 已有 `_convert_to_jpeg`，AK 通道 :765-779 已有"URL 空结果→下载→预处理→重试"先例，移植到 aibuy）；
2. 压缩后 upload 成功率恢复 → `uploaded_url` 优先，URL 直搜继续兜底，CDP 只作第三线（配合 A4 熔断）。

---

## 6. Q7：标题-类目零交集闸应认面包屑

### 根因（"前门豁免、后门杀"的不一致，源码实锤）
- 零交集检查在 **validate 侧**：`ozon_validate_node.py:380-400` 预检——RU 标题与 RU 类目路径（`_fetch_ru_category_path` :59-75 查 PG category_tree_nodes，**这就是 Ozon 官方面包屑**）无 ≥4 字符共同西里尔词根（`common_cyr_words` :26-58）→ 追加"标题与类目不一致"。
- retry 侧映射 `LOCAL_TITLE_CATEGORY_MISMATCH → block_to_box`（validation_retry_loop.py:366, :747-753）——拦即入箱不重传。
- **豁免不一致**：assemble 侧一致性检查认权威来源（`_is_skill_authoritative`：page/what_to_sell/manual/mapping，assemble:579-585 + `_step65_consistency_exempt` :2258-2273），但 **validate 侧预检只豁免 UPDATE（带 product_id）和缺 RU 路径，从不看类目来源**——类目明明来自 page 面包屑（竞品在售真实类目，最高信任源），RU 标题词面没对上照样被拦。盆/篮/筛家族 8+ 卡误伤即此。

### 修复设计（批C `fix/guard-precision-v1`）
1. `OzonValidateInput`（state.py:572-596）增加 `category_source: str` 字段——**langgraph 纪律：节点要读的字段必须声明进 Input**，prepare 写入（payload 侧 dc/tp 定稿时同步来源）。
2. validate 预检 :384-392：`category_source ∈ {page, what_to_sell, manual, mapping}`（对齐 assemble 白名单）→ 零交集**降级为 warning 留痕，不进 item_errors**；非权威来源维持现状拦截。
3. 不动 retry 侧映射（拦截语义本身正确，错的触发面）。
- 测试：权威来源+零交集→无 LOCAL_TITLE_CATEGORY_MISMATCH；search_kw 猜测+零交集→仍拦；Input 声明完整性（防 channel 过滤吞字段）。

---

## 7. Q8：价差守卫跨币种

### 现状
- **止血已在 dev**（0.77.3 PR #42 P4）：`price_sanity_guard.py:92-100` `final_currency != "RUB"` → skip（`{"skipped": "currency_mismatch"}`）。608₽÷30¥=20× 假杀已消除。
- 三层价口径现状正确：守卫只比日常价 price；old_price 划线价本就强制 ≥1.2×、promo 是底线价，不参与比值。

### 增强（批C）
你说的对——我们有汇率，应该换算后真比而不是干脆跳过：
1. `pricing_node.py:194` 的 `exchange_rate`（CNY→RUB，来源 pg_cache/live_fetch/fallback_12）已在守卫调用点（:436-439）作用域内——把它传进 `check_price_sanity`。
2. guard 内：`final_currency != "RUB"` 且 `exchange_rate > 0` → `anchor_cmp = anchor_rub / exchange_rate`（如 608₽÷13.3≈45.6¥ vs 30¥=1.52× 通过）；换算后 ratio≥10 仍拦（真异常不放过）；`exchange_rate` 缺失/≤0 → 维持 skip（宁缺勿假）。
- 测试：CNY 店真异常价（换算后 ≥10×）仍拦；正常价通过；rate 缺失 skip。

---

## 8. Q9：Supabase 白烧 4s

**已修 dev（0.77.3 P1）**：`auth_node.py:224-231` URL 缺失/非 http(s) → 直接降级**零重试零 sleep**；`MissingSchema/InvalidURL` 确定性异常即 break（:235-241）。

**角色澄清**（回答"我们已经不依赖这个了"）：不对——生产环境 token→用户鉴权**仍然走 Supabase tokens 表**（`GET {url}/rest/v1/tokens?key=eq...` :214，即 MXOU 用户库）。"不依赖"只成立于本地未配置环境——修复后这类环境瞬时降级（fail-open 走 MXOU 余额），不再每任务白烧 4s。若未来要彻底去 Supabase 化，那是独立的架构议题。

---

## 9. Q10：config 挂空重试 4 轮白烧 40s 根因

### 根因复盘（已在 0.77.3 CHANGELOG 登记）
1. compose 栈曾从 `../ozon-worker-release`（后删除的 worktree）目录 `up`；
2. bind mount 源路径被删 → **Docker 静默挂一个空目录**盖住镜像内 /app/config（容器 health 显示一切正常）；
3. 场景配置 FileNotFoundError 被 retry 分类当临时错误 → 4 轮 × ~11s 重试烧完才 failed。

### 已修（dev 0.77.3）
- P2：`task_processor.py:53` FileNotFoundError/PermissionError/ImportError/ModuleNotFoundError → permanent，直接终态不重试；
- P5：`main.py:475-513` `_assert_critical_configs` 启动守卫——8 个关键配置缺失即 ERROR + Sentry `critical_configs_missing`（report-not-block），lifespan 顶部调用。

### 追加固化（批D，Tier B 小改）
1. `deploy/docker-compose.yml` worker healthcheck 追加 `test -f /app/config/imagegen.json`——config 挂空时容器直接 unhealthy，不再"健康地空跑"；
2. `docs/DEPLOY.md` 红线：禁止从临时 worktree 目录起 compose 栈（bind 源路径必须是主仓持久路径）。

---

## 10. Q11：上架的是 AI 生图还是 1688 原图？

### 结论：**默认全部 AI 生图**（gate 17 张卡无一例外）
- payload 图廊只从 AI 生成槽位构建：`prepare_ozon_upload_node.py:1564-1603`（main + white_bg/multi_angle/detail/scene 等 `_IMG_ORDER`），策略行 :1868-1871 明确"仅使用AI生成的图片，不使用原始产品图片（alicdn已失效404）"。
- 1688 原图的两个角色：①生图**参考**（不进 payload）；②**E1 全失败兜底**——所有 AI 图都失败时 `salvage_original_images` 把原图转存 COS 后顶上（:3144-3157），连这也没有才 IMAGE_ERROR 失败。
- 竞品参考图（competitor_ref_images）任何场景不进上传数组（:1569-1575 fix/image-ref-pollution 红线）。
- 默认 5 槽位：`image_gen_plan.py:58-65`（white_bg/multi_angle/main/detail/scene_1 + 变体主图循环）。

### 可选（拍板项）
"原图直上"目前**没有**开关——payload 明确排除 alicdn 原图（404 失效风险）。若产品上要这个选项，需走 E1 式 COS 转存路径立项（新 `image_gen_plan: originals` 模式 + 转存），不是配置翻转就能开。

---

## 11. 批次划分与门槛

| 批 | 分支 | 内容 | 门槛 |
|---|---|---|---|
| A（P0） | `fix/skill-silent-cdp-v1`（Tier A） | A1 后台化白名单 + A2/A3 负缓存 + A4 aibuy 熔断 + Q6 upload 压缩 | skill 全量测试绿 + 实机 discover 一轮零前台弹窗 + 图搜命中率持平 |
| B | `feat/skill-run-logging-v1`（Tier A） | Q3 日志六件套 + Q4 `--wait` 一次性命令 + SKILL.md recipe + Q5 follow 预估（若拍板） | 测试绿 + 实机跑一轮日志可复盘（文件路径/计时/终局报告可见） |
| C | `fix/guard-precision-v1`（Tier A） | Q7 validate 侧权威来源豁免 + Q8 换算后真比 | worker 全量测试绿（PG 5433）+ 构造用例双向验证 |
| D | 直提 dev（Tier B） | Q10 healthcheck + DEPLOY.md 红线 | compose 卫生测试不红，当日 push |

依赖关系：A/B/C 相互独立可并行；A 先行（生产 bug 止血优先）。全部完成后并入下个发版（0.77.3 尚未发版，可考虑同车 0.78.0）。

### 拍板结果（2026-09-19 用户拍板）
1. Q4：**同意不拆 skill、改一次性命令**（批B `--wait`）。
2. Q5：**follow/graph 加预估打印 + `--min-margin`**（默认 0=不拦截，行为零变化）→ 批B。
3. 执行方式：**subagent 并行执行**——批A/C/D 文件面互斥（skill / worker / deploy）并行；批B 与批A 同触 cli.py，排序在批A 合并后。

---

## 12. 测试矩阵与验证方式（评审补充）

> TDD 纪律：所有行为变更**先写失败测试（RED）→ 最小实现（GREEN）→ 重构**；每个新测试必须先观察到因功能缺失而失败。
> 单测全部纯 mock，**禁止实机 Chrome/CDP/网络**（实机验证串行由控制器执行，防 CDP 争抢）。

### 批A（skill 静默化）测试
| 修复 | 单元测试 | 通过判据 | 验证方式 |
|---|---|---|---|
| A1 后台 tab | `test_silent_cdp_background_v078.py` | monkeypatch `CdpConnection.new_tab` 抓 kwargs，逐调用点断言 `background=True`（预热/cookie 静默读/_tab_for_seller/readiness 探针/taobao/pdd/discover 阶段①）；白名单（ozon_scraper/check/_open_tab/登录引导页）断言仍前台 | 实机 discover 一轮零前台弹出 + 图搜命中率持平 |
| A2 readiness 负缓存 | `test_readiness_negative_cache_v078.py` | 探针失败写 600s 负缓存→TTL 内第二次不再触发探针与预热导航；成功缓存语义不回归 | 实机第二条命令零 1688 首页 |
| A3 seller 失败负缓存 | `test_seller_negcache_v078.py` | fetch 失败/空→600s 窗内二次调用不建 tab 直接返回失败形状 | 同上（seller 页） |
| A4 aibuy 熔断 | `test_aibuy_breaker_v078.py` | 3 连败开闸→`search_by_image_aibuy` 快速返回 []；成功复位；600s 自动过期 | 实机日志一行熔断汇总 |
| Q6 upload 压缩 | `test_aibuy_upload_compress_v078.py` | PIL 构造 2000px PNG→`_aibuy_image_upload` 捕获 payload→解出 ≤1024px JPEG；PIL 失败回退原始字节不 raise | 413 复现率归零（实机采样） |

回归底线：`cd skill && .venv314/bin/python -m pytest tests/ -q` 全绿（基线 ~1496，报实数）。

### 批B（日志+一次性命令+预估）测试
| 修复 | 单元测试 | 通过判据 |
|---|---|---|
| setup_run_logging | `test_run_logging_v078.py` | `main()` 后 root 有 stderr+file 双 handler、日志路径已打印、cloud_probe 副作用配置不覆盖 |
| log_stage 计时 | 同文件 | contextmanager enter/exit 各一条 INFO、含耗时秒 |
| `--wait` | `test_wait_flag_v078.py` | mock `poll_task_status`：graph/follow/discover `--wait` 轮询到终态并打印 task_id+终态+product_id/失败原因 |
| 终局 run 报告 | `test_run_report_v078.py` | discover/discover-task 落 JSON 报告（逐条 task_id/draft_id/失败首行），路径打印 |
| `check --logs` | `test_check_logs_v078.py` | 消费 read_task_log 输出 JSONL 事件 |
| 预估打印 + `--min-margin` | `test_estimate_min_margin_v078.py` | follow/graph 打印预估行；利润率 < 阈值拦截 exit 3；默认 0 不拦截零行为变化 |

验证方式：实机 graph 一条 `--wait`，从打印的日志路径复盘全程计时。

### 批C（worker 守卫精化）测试
| 修复 | 单元测试 | 通过判据 |
|---|---|---|
| Q7 权威来源豁免 | `test_validate_category_source_v078.py` | mock `_fetch_ru_category_path`：权威来源（page/what_to_sell/manual/mapping/widget 或 match_layer=Skill）+ 零交集 → **不进 item_errors**（warning 留痕）；空来源/search_kw → 仍拦 LOCAL_TITLE_CATEGORY_MISMATCH；UPDATE 豁免不回归；`"category_source" in OzonValidateInput.model_fields`（防 langgraph channel 过滤吞字段） |
| Q8 汇率换算 | `test_price_guard_fx_v078.py` | CNY 店+rate：608₽/13.3≈45.6¥ vs 30¥→ok；换算后 ≥10×（真异常）→block；rate 缺失/≤0→skip；RUB 路径回归不变 |

回归底线：worker 全量绿（`PGDATABASE_URL=postgresql://postgres:localdev123@localhost:5433/ozon`，先 `lsof -iTCP:5433 -sTCP:LISTEN` 核实；PG 不可用则无 PG 模式跑，新测试必须纯 mock 双模式都绿；基线 ~2660，报实数）。不涉及 `api/schemas.py` 则无需 gen_api_docs。

### 批D（deploy healthcheck）测试
| 修复 | 单元测试 | 通过判据 |
|---|---|---|
| healthcheck 含 config 存在性 | `test_deploy_compose_hygiene.py` 扩断言 | worker healthcheck 断言含 `test -f /app/config/imagegen.json`（或等价）；既有不变式（日志封顶/端口禁 5433/mem_limit）不回归 |

验证方式：单测试文件绿（纯文件读取无需 PG）。

### 实机验证（控制器串行执行，禁并行 CDP）
1. 批A 合并后：worktree 跑 `discover --non-interactive --auto-submit` 一轮——全程 macOS 前台观察**零弹出**，匹配命中与改前持平。
2. 批B 合并后：`graph <1688链接> --wait` 一条——日志文件路径打印、阶段计时、终态回显齐全。
3. 批C 构造用例已由单测覆盖；生产 gate 属发版流另行拍板。
