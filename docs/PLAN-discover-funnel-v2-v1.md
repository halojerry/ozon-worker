# PLAN — discover 选品漏斗 v2：对标上品帮（shopbang）优化 v1

> 日期：2026-09-07 · 分支：`feat/discover-funnel-v2` · 状态：已批准（用户四项全选，两期交付）
> 竞品素材：`shopbang/`（上品帮 v3.2.0 Electron 编译产物 + 两份分析文档）
> 本文是实施主文档；竞品逆向细节以 `shopbang/上品帮客户端逻辑与清单.md`、`shopbang/上品帮选品筛选SOP与源码对照.md` 为准，不重复。

## 一、调研结论（方案依据）

| 维度 | 上品帮 | 我们 | 结论 |
|---|---|---|---|
| 入口与滚动 | 可见窗口开任意入口 URL（highlight/搜索/类目/店铺通用，无特判），3000ms easeInOutQuad 缓动滚动 | 同一 highlight 入口（ozon_discovery.py:28 `CHINA_HIGHLIGHT_URL`）；`_EASE_SCROLL_JS` 注释标明照抄其节奏 | **流程层无差距** |
| 详情静默获取 | 同窗口页面内 `fetch()` widget API（entrypoint-api.bx，credentials include 复用登录态） | `_analyze_product` → `fetch_product_info` 同款路径 | 无差距 |
| 1688 匹配 | 隐藏窗拍立淘 H5 自动化，并发 3，**取第一个非广告结果**，仅回填 3 字段 | 四级降级链 + 置信度护栏 + LLM 救援 + 类目对齐 | **我们强得多，不学** |
| 粗筛 | 两段式：BASE 20 项区间 → 匹配 → DETAIL 4 项；AI 预设 5 规则含**销量价格阶梯** | `_BASE_FILTER_RULES` 18 项**全 `(None,None)` 空架**（ozon_discovery.py:1006-1025）；`--rules ai` 已实装同款阶梯（:975） | **骨架已建没通电** |
| 运营指标 | 63 列，来自**自家服务端商品库**（getOzonSaleDataByIds）——护城河，无法复制 | what_to_sell 子集（月销/增长/drr/上架天数） | 以实测可扩多少为准 |
| 采集箱 | 每商品 63 列全量上云 | drafts 只存信封 JSONB，选品元数据全丢；discovery_runs 归档与 drafts 零关联 | **最便宜的优化点** |
| 自动化 | 无人值守任务制（配置→自动跑完→入库） | 交互式（滚动→表格→人工挑选→匹配） | 产品形态差距，做任务模式 |

**利润核算**：竞品放云端 goodsFilter；我们本地 compute_price + worker commission_resolver 同源更强，不动。
**利润口径纪律**：运费/佣金估算继续走唯一入口（`estimate_shipping_cny`/`commission_resolver`），本计划不改定价。

## 二、方案（用户四项全选）

1. **① BASE 粗筛实装 + `--filter-profile`**：激活空架 18 项 + ai 预设档；`--auto-submit` 默认 ai，交互流程缺省 off 零变化。
2. **② 采集箱元数据留存**：信封注入 `extensions.discovery_meta`（worker 零迁移——payload 整存 JSONB）+ webui 采集箱列 + CSV 导出补列。
3. **③ 指标扩容 + cookie 直调**：what_to_sell 实测可扩字段进 ProductCandidate（命名对齐粗筛规则键）；seller 富化接 `fetch_*_direct`，CDP 兜底保留（ToS 灰区 I-13 纪律）。
4. **④ discover-task 任务式全自动**：URL+筛选+数量 → 自动滚动→粗筛→自动匹配（限流）→利润精筛→自动入采集箱，支持 `--resume`。

## 三、Task 分解

### Wave 1（基建）

| Task | 内容 | 主要文件 | 测试 |
|---|---|---|---|
| 1 | 注入 `extensions.discovery_meta`（~18 键白名单，缺省省略，≤2KB，沿用 match_evidence 风格 cloud_probe.py:2507-2522） | `skill/scripts/cloud_probe.py` `build_envelope_from_discovery`(:2434) | skill 单测：有/无指标两形态 |
| 2 | webui 采集箱加「蓝海分/月销/利润率」列（payload.envelope.extensions.discovery_meta，缺失 —）；worker CSV 导出 `_extract`(:285-318) 补 4 列 | `webui/src/components/CollectionPanel.tsx`、`webui/src/api/hooks.ts`、`worker/src/services/draft_service.py` | worker 单测锁列；`tsc -b`+build |
| 3 | 契约联动：worker extensions 整包透传单测 + CONTRACT-v4/AGENTS.md 契约节补 discovery_meta 说明 | `worker/tests/`、`docs/CONTRACT-v4.md`、AGENTS.md | worker 单测：带 discovery_meta 信封入队原样 |
| 4 | what_to_sell 字段实测探针（5-10 SKU dump 原始键；**需本机 Chrome 登录 seller.ozon.ru**） | `skill/scripts/probe_what_to_sell_fields.py`（一次性） | 结果写本文附录 A |
| 5 | ProductCandidate 指标扩容（按 Task 4 实测；字段命名对齐 `_BASE_FILTER_RULES` 键）+ apply_analytics_to_candidate 解析；REPORT_FIELDS 谨慎增量（~500B/条纪律） | `skill/scripts/lib/ozon_discovery.py:126-209`、`skill/scripts/lib/ozon_seller_analytics.py:1042-1112` | fixture 单测；缺字段行为逐字一致 |
| 6 | discover 指标富化（ozon_discovery.py:697-742）优先 cookie 直调（复用 `_fetch_seller_session_cookies`/`_seller_direct_post`，queries 先例 cli.py:2316-2340），CDP 兜底；失败 warning 出声；成功跳过 seller 登录导航 | 同上 | mock 直调成功/降级两分支 |
| 7 | `_passes_base_filter(candidate, profile)`：ai 档复用 `_check_ai_preset`(:988)；区间规则支持 settings/CLI `--base-filter "k>=v,k<=v"` 覆盖；ai 主判定挂 ②b 富化后 re-check（filtered+reason）；**无 analytics 月销阶梯降级放行+warning**；CLI `--filter-profile off|ai` 默认 off，`--auto-submit` 未显式指定 → ai | `ozon_discovery.py`、`cli.py` | off 逐字不变回归；ai 命中/不命中；auto-submit 默认；降级 warning |

### Wave 2（大件）

| Task | 内容 | 主要文件 | 测试 |
|---|---|---|---|
| 8 | `discover-task` 无人值守命令：`--url/--keyword/--target-count/--filter-profile(默认 ai)/--min-margin/--match-limit(30)/--match-concurrency(aibuy 2, CDP 强制 1)/--to-box/--dry-run/--resume`；复用 collect_and_analyze→粗筛→match_selected（bounded+进度回调）→利润精筛→build_envelope_from_discovery→submit_draft（单条 fail 不中断）；任务状态 `data/discovery/tasks/{id}.json`；限流：aibuy 间隔 ≥2s 抖动、连续 5 次 no_match 提前终止。**pounding-mcp 联动**：server.py 参数映射 + router.py 意图词表 | `skill/scripts/cli.py`、`skill/scripts/lib/ozon_discovery.py`、`pounding-mcp/pounding_mcp/{server,router}.py` | dry-run 全链 mock；resume 跳过已处理；限流断言；pounding-mcp 自身 venv 测试 |
| 9 | 文档收尾（command-reference.md 补 `--filter-profile`/discover-task；CHANGELOG 草稿）+ 全量回归 + 发版 gate 清单（发版另行走 v0.69 实机纪律，不在本批） | `skill/references/command-reference.md`、CHANGELOG.md | worker 全量/skill 全量/webui build/pounding-mcp 全绿 |

## 四、验收标准

1. `--filter-profile` 缺省时现有交互 discover 行为逐字不变（skill 全量绿，基线 597+）。
2. 采集箱条目可见蓝海分/月销/利润率（webui + CSV）；带 discovery_meta 信封 worker 入队原样（worker 单测）。
3. ai 档过滤前后计数可观测（日志）。
4. discover-task `--dry-run` ≥10 条真实链接通过；`--to-box` 本地 Docker worker 真实入箱。
5. worker 全量 / skill 全量 / webui build / pounding-mcp 测试全绿。

## 五、边界与风险

- 不学：竞品浅匹配、云端利润核算、服务端指标库（无法复制）。
- what_to_sell 可扩字段以实测为准，拿不到的粗筛规则恒放行（不编造数据）。
- cookie 直调保留 CDP 兜底；不做纯 HTTP 硬依赖。
- 不触碰 `mcp_server.py`（另一会话遗留纪律）；不碰 `skill/skill/` 遗留产物。
- 真实测试走本地 Docker（禁 worker.mxou.cn）。
- 基于 dev@a5f614a7（v0.69 discover 真值复用已修内容为基线）。

## 附录 A · what_to_sell 字段实测结果（Task 4 填写）

> 待实测。脚本：`skill/scripts/probe_what_to_sell_fields.py`

## 附录 B · 变更记录

- 2026-09-07 v1：创建文档，四项全选两期交付。
