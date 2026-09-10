# PLAN-discover-cross-source-v1 — discover 跨平台静默货源匹配 v1（对标 1688 静默匹配）

> 2026-09-10。用户拍板：「做成跟 1688 一样，静默匹配」——discover（Ozon 选品）产出候选时
> **自动**完成三源（1688/淘宝/拼多多）货源匹配与选优，用户无感；采集箱保留三源对比可见可改。
> 前置已落地：PR #11（feat/cross-platform-sourcing，适配器/信封/worker 兼容层）已合 dev，
> 实机 gate 全过（含淘宝/天猫/拼多多真实商品入箱）。SDD 执行，分支
> `feat/discover-cross-source-v1`。

## 0. 一句话

discover 候选定稿前：1688 图搜同款照旧（**定款主通道**）→ 副通道用 1688 命中标题的关键词在
淘宝/拼多多**后台共享 tab 静默搜索**比价 → 同款确认过阈值且**显著更便宜才换源** →
`purchase_url/purchase_cost` 用胜者 → `discovery_meta.source_comparison` 带三源快照（采集箱
可见可改）；任一环节失败/未登录**静默跳过该平台**，绝不阻塞、绝不出声。

## 1. 决策记录（用户拍板）

- **模式**：静默自动选优（同 1688 现状），**不做**「三源全列让用户选」交互模式；对比快照仅作
  采集箱透明度兜底（可见可改，非必经决策点）。
- **定款权威**：1688 图搜不动摇——跨平台关键词命中 ≠ 同款，换源必须过同款确认阈值，否则
  宁用 1688 原匹配（安全底线：静默自动模式下错换款的代价高于少换款）。
- **换源阈值**：到手价（价+运费）显著更低才换（默认 ≥10%，常量 + env 可调）——防抖动、
  防关键词噪音引起的假便宜。
- **节流**：只对利润过闸的 top-N 候选比价（默认 N=5，`--compare-sources N` 可调，0=关闭）。

## 2. 现状取证（本仓已实证）

| 依赖 | 现状 | 证据 |
|---|---|---|
| 1688 静默匹配 | 图搜三通道（aibuy mtop 纯 HTTP 静默 / AK / CDP 回退）+ `_match_and_score` | `ozon_discovery.py:894-967`、`ozon_image_search.py`（aibuy 静默主通道） |
| 静默 CDP 先例 | 后台共享 tab（纯 HTTP 不可行——DataDome；tab 复用+释放纪律） | `ozon_widget._tab_for_variant_truth`、discover 静默化取证（v0.70 前后） |
| 淘宝搜索页 | 登录态下 CDP 可挖商品 id（DOM innerHTML 正则） | 2026-09-10 实机（feat/cross-platform-sourcing gate） |
| 拼多多搜索页 | 登录态下 CDP 可挖（DOM 属性扫描；rawData 顶层 `stores`，ssrListData 仅筛选配置，商品列表客户端渲染） | 同上 |
| 详情适配器 | `taobao_client.fetch_product` / `pdd_client.fetch_product` 实机通过 | PR #11 gate 记录 |
| 登录态 | 工具 Chrome 已常驻淘宝+拼多多会话（用户扫码）；过期 → 本计划静默降级 | 同上 |
| discovery_meta 透传 | worker 零消费整包透传，采集箱展示 | CONTRACT-v4 §1.1.1 |

**明确不做**：拼多多 web 图搜（平台没有）；淘宝拍立淘自动化（反爬重、非必要——关键词副通道
够用）；worker 侧任何改动（信封透传）；follow/graph 流改动（graph 本来就是用户给 URL）。

## 3. 全局约束（红线，全程有效）

- Tier A：本分支 + PR；worktree `/Volumes/os/dev/ozon-worker-xmatch`（凭证已从主仓拷贝）。
- **静默纪律**：跨源匹配任何失败（未登录/风控/超时/解析空）→ 该平台静默跳过（debug 日志），
  候选照常产出；**绝不让 discover 整体失败或变慢到不可用**（每候选副通道预算 ≤25s，超时放弃）。
- 登录态探测每 run 一次（非每候选）；cookie/凭证绝不落日志。
- worker 零改动；CONTRACT discovery_meta 新键（source_comparison）登记一处。
- 测试纯 mock（CDP evaluate 返回录制夹具）；实机 gate 用真实 discover run。
- 逐文件 git add；避开危险品类目（实机 gate 延续保温杯等安全品类）。

## 4. 批次（TDD，SDD 逐批 implement→review→fix）

### 批1 跨平台关键词搜索通道（lib/cross_source_search.py）
1. `search_taobao(cdp, keyword, *, timeout) -> list[SourceOffer]`：后台共享 tab 开
   `s.taobao.com/search?q=` → 等渲染 → 解析商品卡（标题/价格/销量/链接/主图）——DOM 解析 +
   失败降级（innerHTML 正则挖 item id，实机先例）；**未登录探测**（`_m_h5_tk` 缺失 →
   `NotLoggedIn` 单例缓存）。
2. `search_pdd(cdp, keyword, *, timeout) -> list[SourceOffer]`：
   `mobile.yangkeduo.com/search_result.html?search_key=` → DOM 属性扫描 + innerHTML 正则
   （实机先例）拿 goods_id 列表 → 从渲染 DOM 提标题/价格（拉不到的字段允许 None）→
   登录探测（login.html 重定向 → `NotLoggedIn`）。
3. `SourceOffer` dataclass：`platform/title/price/freight(None 允许)/sold/url/image`；
   tab 生命周期全走「用户 tab 不动、自建 tab 用完即 close」纪律。
4. 测试：录制夹具（今日实机采的 DOM 形态手工脱敏重建）mock evaluate；登录降级/超时/解析空
   分支；tab 释放断言。
5. compile.py COPY_FILES 收编 + `test_compile_lists.py` 绿。

### 批2 同款确认与选优（lib/source_matcher.py）
1. `confirm_same_product(candidate_title_zh, offer_title) -> float`：jieba 分词 overlap +
   规格词匹配（容量/材质/型号），阈值常量 `SAME_PRODUCT_MIN`（默认 0.45，env 可调）。
2. `pick_best_source(offers_by_platform, baseline_1688_cost) -> Decision`：到手价 =
   price + freight(None→0 并标记 freight_unknown) ；换源门槛 `SWITCH_MIN_RATIO`（默认 0.90
   即便宜 ≥10%）；平手保 1688；销量作平级 tie-break。
3. 关键词提取：从 1688 命中标题（已有中文字段 match_1688_title）剥修饰词取核心词
   （复用 `_MODIFIER_WORDS` 先例——勿改原表，新增平台无关的瘦身逻辑）。
4. 测试：纯函数全覆盖（确认阈值两侧/换源门槛两侧/平手/None freight/空列表）。

### 批3 discover 接线 + 快照
1. `ozon_discovery.py` map 定稿钩子（`_giveback_metrics` 同位置）：利润过闸 top-N 候选 →
   跨源匹配（批1+2）→ 胜者写 `candidate.match_1688_url/price` **同槽位换值**（字段名不动，
   避免六出口联动——语义升格为「matched_source」；REPORT_FIELDS/CSV/webui 零改动，值里带
   平台前缀 URL 自证）；`discovery_meta.source_comparison = {platform: {url,price,freight,
   sold,matched,score,switched}}` 快照（缺失平台省略键，契约登记一行）。
2. `cli.py discover` 加 `--compare-sources N`（默认 5，0=关）；配置热加载键同义。
3. 每候选预算 ≤25s（两平台串行、各 ≤12s 超时）；run 级登录探测一次。
4. 测试：接线矩阵（换源/不换/未登录静默/超时静默/N 限制/开关关闭零行为差异）+ 快照键纪律
   + 现有 discover 测试全绿（1688-only 路径回归）。

### 批4 实机 gate（真实 discover run）
- 工具 Chrome 淘宝+拼多多登录态已常驻（2026-09-10 扫码）。
- `discover --keyword 保温杯 --max-products 20 --compare-sources 5 --export csv`：
  验收 ≥1 候选出现跨源快照、胜者换源有依据（快照内可见价差）、未登录/风控路径静默、
  学习表/discovery_runs 上报行为不变、全 run 时长增幅 ≤ top-N×25s。
- 结果回填本文末尾。

## 5. 验收（PR 合并门槛）

- skill 全量 + ruff 绿；worker 零 diff；`gen_api_docs --check` 零漂移（无 API 变更时自动过）。
- 批1-3 SDD review 通过；批4 gate 记录回填。

## 实机 Gate 结果记录

> 2026-09-10，工具 Chrome（淘宝+拼多多登录态常驻）+ 真实 discover run（保温杯，10 采集/3 profitable
> 进跨源比价）。经四轮迭代收敛——每轮都是实机暴露静态审查测不出的问题：

**最终结果：三源同台比价全通 ✓**
- 三候选快照全字段：baseline 1688（¥22/¥9.5/¥15）+ 淘宝（¥56 confirm 0.48 / ¥88 confirm 0.41 /
  **¥17.52 confirm 0.8571**）+ 拼多多（¥70.9 / ¥199 / ¥24.65 confirm 0.4615，销量 69000/22000 实值）；
- 判决引擎经济行为全对：最接近的一单淘宝 ¥17.52 vs 1688 ¥15（换源线 13.5）→ 正确保 1688；
  三单零误换源——1688 是批发源头，比价后保持即正确结论；换源路径由批2/批3 单测覆盖（真实
  更便宜时按 0.90 线切换）；
- 学习表/discovery_runs 上报行为不变 ✓；run 时长增幅在预算内（top-N×25s）✓。

**四轮迭代记录（gate 的价值实证）**
1. 第1轮：`extract_search_keyword` 在无空格长 CJK 标题（1688 标题常态形态）返回 None → 跨源
   整段 skipped「无参照标题」——夹具形态偏差（brief 示例全带空格）。修：CJK 连写回退截断
   （7bf6e277），顺带修修饰词剥离两缺陷。
2. 第2轮：淘宝 context 守卫误杀——URL 参数规范化（`page=1` 前置）致前缀比对恒 False；
   pdd 只出 goods_id 无标题价格。修：解析后比对 + pdd 改页内 script-JSON 挖掘（56984f33，
   实机 40/20 offers 全字段）。
3. 第2.5轮（实现者主动揪出）：淘宝搜索页**没有** `_m_h5_tk`（那是详情页 mtop token），批1
   cookie 登录门会毒化每次 run → 改登录判据=登录页重定向；连带修 5 处单测未隔离副钩真触网
   （全量 485s→69s，flake 根治）。
4. 第3轮：淘宝自身把 q 截断（半转义 `%E6%` 结尾）+ 会话软限流时双重编码——q 守卫语义从
   「逐字节相等」改「解码前缀放行」（fe81cdf6），精度仍由同款确认把关。

**多类目矩阵（2026-09-10 晚，5 类目 × 10 采集 × top-3 比价）**
- 手机壳 0 profitable（利润闸正确拦——俄区手机壳红海）；袜子 3/3、收纳盒 3/5、宠物碗 3/3、
  数据线 3/4 带快照（top-N 帽生效）；双平台 12/12 出价零跳过（登录态 6 run 稳定）。
- **3 次真实自动换源**：收纳盒路由器架→pdd ¥6.13、宠物碗→taobao ¥9.0、数据线→taobao ¥13.08
  （1688 基线 ¥28 的 Thunderbolt5 线，taobao 同款 0.46 确认、pdd ¥16.32/12.5w 销量同台，价优者胜，
  判决理由人话可读 + freight_unknown 如实标记 + thresholds 回显 + 写回带胜者真实标题）。
- confirm 分布健康：≥0.6 ×5 / 0.45-0.6 ×10 / <0.45 ×9（闸在甄别）；判决分布 6 未达线 +
  6 无同款 + 3 换源；单 run 时长 2.5-4 分钟（预算内）。
- **观察项（后续批候选）**：快照未带 offer title 字段——采集箱看不到备选货源标题、需点 URL；
  快照 title 补全 + webui 货源对比块展示是顺理成章的下一个 UI 批。

**遗留观察项（不阻塞）**
- pdd 推荐位混排（非同款高价品）由批2 质量闸兜底（confirm 0.2857/0.3846 正确拒）；
- 标题截尾标记集/区间价取低档为保守口径；
- 淘宝会话软限流时该平台诚实 empty（快照记零命中非异常）；
- 1688 freight 字段当前实跑为 None（freight_unknown 旗标在快照可见）——1688 运费并入
  purchase_cost 的既有链路不受影响。
