# PLAN — v0.83 质量战役（预估统一 / 描述重造 / 类目边界 / 后台化 / Session 落盘 / 回执真值 / 收口）

> 状态：待拍板 → 开工。日期：2026-09-28。动因：客户四抱怨（价格算不对 / 属性描述 Rich 没写清楚 /
> 类目没匹配对 / agent 平台一直等+discover 数据没法分析）+ 两轮 subagent 审计（五域根因审计 + 实施级遗漏挖掘）。
> 本文是主方案；各批开工时按需另立细案。**改下述任何链路前先读本文件对应批节。**

---

## 0. 一页结论

1. 三条质量抱怨的共同根因不是「修复无效」，是**修复与抱怨错位**：v0.81 修的是单卡正确性，客户抱怨的是
   预估一致性、内容信息量、证据送达——三个维度此前从未立项。
2. 遗漏挖掘挖出**六个改变方案的实锤**（§2），其中最重的一个：**顶层 `description` /
   `description_json` 根本不在 `/v3/product/import` 契约里，Ozon 静默忽略**——整条「1688 详情翻译
   → description」链写的都是死字段，卡面唯一载体是属性 4191（MCP + 本地 swagger 双源实锤）。
3. 七批（§3），依赖拓扑定序（§4）：①②并行 → ③ → ⑤ → ④⑥并行 → ⑦收尾，同车 v0.83.0。
4. 所有工程级决策已定案（各批「已定案」标注），真正的产品拍板收敛为 §7 的 6 条。

---

## 1. 动因与审计来源

- 客户使用反馈：价格计算有问题；特征属性/描述/Rich 没写清楚；类目没匹配对。
- agent 平台反馈：重命令同步阻塞，agent 全程干等；discover 数据不落盘没法分析；字段多出口漂移。
- 审计一（五域根因）：价格 4 条独立算价链不同源；描述=翻译链+4191 prompt 看不到图+11254 纯图；
  类目 #86 语义闸在权威来源/旧 skill 包下空转；后台化基建已存在缺默认值；落盘 6 处 4 种格式无 run_id。
- 审计二（实施级遗漏挖掘，五路并行）：§2 实锤 + 各批设计定案。

---

## 2. 遗漏挖掘的六个改变方案的实锤

### 实锤 A：顶层 description 是死字段（批②靶位重定义）
`/v3/product/import` 的 `items[]` 无 `description` / `description_json` 字段（ProductAPI_ImportProductsV3
request schema，MCP `ozon_describe_method` 与 `docs/refs/ozon-mcp/data/seller_swagger.json` 双源一致）。
卡面描述唯一载体 = **属性 4191（Аннотация）**；`prepare_ozon_upload_node.py:3415`（description）与
`:3486-3500`（description_json）写的都是契约外字段。评级条件：4191 >100 字符 +25、>500 字符 +25、
11254 Rich 有值 +100。**批②的全部火力对准 4191；description_json 作废；description 顶层键保留发送
（无害）但 validate/retry 的等价检查重心迁 4191。**（开工第 0 步：对测试店一张现卡跑
`/v1/product/info/description` 读取端点确认死字段结论。）

### 实锤 B：「同链」没有现成函数——必须先抽 pricing_core（批①最大隐藏工作量）
`estimate_from_envelope` 与 `pricing_node` 差异远不止 reconcile：包装费常量、店铺 3PL 探测、fx 三级链、
dc 来源、weight_suspect/purchase_cost_suspect/wd_audit/价差守卫/变体循环。**唯一正解：从 pricing_node
抽纯计算核 `worker/src/utils/pricing_core.py`，pricing_node 与 batch estimate 共调；带副作用的部分
（Sentry 留痕、价差守卫 block、Ozon API 兜底查汇率）留在 pricing_node。** 顺带回归两个隐藏消费者：
`ai_field_service._estimate_pricing`（采集箱展示价）与 `store_analysis_service`（店铺分析利润率）。

### 实锤 C：直接切 worker 算价会让佣金「变错」——两个 resolver 侧缺口
①`commission_resolver` 只读 `fbs` 前缀忽略 `fbo`（`commission_resolver.py:24,186-191`），fbo-only 候选
会从 skill 的真实 fbo 段掉到 fallback；②worker fallback 恒 0.10 vs skill 兜底 12–18%，无 dc 无
segments 的候选切过来后**利润被系统性高估**，可能让本被 `--min-margin` 拦掉的候选变 profitable。
定案：resolver 补 fbo 回退；无 dc 无 segments 候选标 `commission_source=fallback` 且**不参与
profitable 判定**（宁缺毋滥）。

### 实锤 D：discover 匹配期拿不到国内运费、也拿不到代表档
三条匹配路径（aibuy/CDP/AK）返回的 match dict 都不含 `freightCny`（`ozon_discovery.py:1338` 唯一写入点
恒空）；代表档逻辑 `_pick_representative_qty_tier` 在 cloud_probe 侧消费 1688 详情页 variants，匹配期
无此数据。定案：**国内运费加进 purchase_cost（对齐 graph 路径 `cloud_probe.py:784-790`），不新增独立
键**（pricing_node 不认，开了就是第二个出口）；**H6 代表档降级为提交期口径**（该路径已生效），匹配期
mini-envelope 标 `weight_source=ozon_competitor_card` + `freight_unknown` 如实留痕。

### 实锤 E：R4 自证链闭环——`validation_retry_loop.py:1229` 把没跑过 R2b 的自选结果贴成
`match_layer="R2b", confidence=0.7`，连带豁免 Step 6.5、以 0.7 档写学习表。另 follow 链
`cloud_probe.py:4699` 竞品标题为空时 `matches[0]` 直通绕过整道语义闸。两处是权威边界收紧后被绕开的
后门，批③必堵。

### 实锤 F：后台化的前置缺口——普通 discover 无结构化尾 JSON
`cmd_discover` 出口只有人话行（`cli.py:2315-2329`），pounding-mcp `_summarize` 读不到 `candidates` →
后台收割摘要恒 0、孤儿任务误判。且 skill 无法 import pounding-mcp（独立编译发布），注册表会裂成两套。
定案：skill 生成 `disc_*` run_id、尾 JSON 带 run_id+计数、注册表单落 `skill/data/jobs/`（pounding-mcp
job_* 读同路径）；worker 侧列名用 `session_run_id`（run_id/task_id 语义已被 worker 占用——
category_match_log.task_id 历史坑勿重演）。

---

## 3. 批次定义

### 批① 预估统一（discover/graph/follow 算价收口 worker，P0）
**目标**：全系统唯一算价出口 = worker `compute_price` 链；skill 零公式。
**已定案**：
- 抽 `pricing_core`（实锤 B）；`estimate_service` 补 reconcile+体积兜底+店铺 3PL（凭证解密走
  credential_service，batch 请求带 credential_id）+ fx 三级链；estimate 回吐审计 marks
  （weight_suspect/exchange_rate_source/commission_source）。
- 新端点 `POST /api/v1/estimate/batch`（≤50/批，Pydantic+`openapi_extra`+`_examples`，先例
  analytics_routes seller-sync）：请求 `{items:[{purchase_cost(含国内运费), weight_g, dims_mm,
  attributes, currency_code, commission_segments, dc?}], credential_id?}`；响应 200+逐项明细
  `{items:[{index, ok, price/old_price/promo_price/profit_cny/profit_rate/commission_rate/
  commission_source/estimate_source}], failed:[{index,reason}]}`（先例 drafts_routes，不用 207）。
  不支持 variants（多 SKU 交回终价链）。鉴权 Bearer+独立限流桶 `estimate_batch:{token}`。
- skill：`_calculate_profit`/`estimate_shipping_cny`/`_estimate_and_print`/`cmd_search` 内联公式
  （cli.py:377-390）/cloud_probe 魔数 1.44375（cloud_probe.py:3937）五处退役为消费方；discover 匹配
  按分块（与 workers/早停语义兼容）批量调用回填；`ozon_currency` 死键清理。
- 降级：worker 不可达/404（try/except，不做版本探测）→ **无预估**（字段省略 + estimate_source=
  unavailable），**不回落 legacy 公式**；`--min-margin` 闸对无预估不拦（现状已正确）。
- 黄金对账：新增 `test_estimate_batch_parity_v083.py`（骨架复用 test_pricing_alignment_v080），
  断言 pricing_node ≡ estimate ≡ batch 三处逐字段相等。
**改动面**：worker（pricing_core/estimate_service/routes/2 个隐藏消费者回归）+ skill（5 出口）+
CONTRACT-v4 + gen_api_docs。估 3-4 人日。

### 批② 描述/Rich 重造（P0，与①并行）
**目标**：4191 从「通用句」变「撰写的事实锚定俄语描述」。
**已定案**：
- **撰写=替换**：新撰写链成为 4191 唯一来源（删除/旁路 `_generate_rich_description`）；11254 保留
  确定性生成器。撰写目标 500–1500 字符（对齐 +25/+25 评级点）。
- 输入：`draft.attributes`（≤40）+ 1688 详情文本 + 竞品 A7 全表（确认进信封）+ **vision 真图**
  （`draft.images` 1688 原图经 COS 镜像保证可达，不喂 AI 生成图——喂 AI 图=二次编造）；prompt 传
  `image_urls=` 参数（现状 URL 当纯文本，vision 模型实际瞎的）。
- **数字事实锚定硬闸**：出口抽取 4191 全文数字，逐个回查属性/重量/尺寸/1688 原文，未命中即删该数字
  或整段回落；prompt 硬约束「不得引入证据外数字」。撰写≠编造规格。
- 降级链：撰写失败 → 用户/1688 文本翻译 → `build_annotation` 确定性句 → 通用句最后兜底并打
  `marks.description_fallback=True`。失败=warning 非阻断；`MxouContentViolationError` 显式 catch
  不重试；额度/鉴权走永久错误。
- **顺带修三个存量缺陷**：①box_reviewed 闸补到 4191 链（现状用户在采集箱写的描述会被 LLM 覆写——
  `_box_reviewed` 只 gate 标题链）；②retry 的 DESCRIPTION_DECLINE 修复改写 4191（现状写死字段
  description）；③`fetch_back_node` 与 `card_audit_service` 的 enrich 补填加 `is_follow_sell` 豁免
  （现状会给跟卖竞品卡写 4191/11254——A6 红线漏洞）。
- **11254 文字块前置探针**：契约查不到块 schema（swagger 零记载）——真店测试店 import 一张带文字块
  的 11254，确认 (a) 不 400 (b) rating text_rich fulfilled (c) PDP 渲染，再定块类型。**不许盲写。**
- description_json 作废；description 顶层键保留但 validate（ozon_validate_node.py:573-587）与 retry
  等价检查重心迁 4191；rating<90 且 4191 过短允许重生成（content_enrich 加可替换分支）。
- 存量卡：`content_rating_sweep` 复用同一撰写构造器（同源纪律），是否全量扫随批⑦定。
**改动面**：worker 为主（prepare/content_enrich/fetch_back/card_audit/validate/retry）；素材加抓
（如需）走 skill+信封键三处同步。估 4-5 人日。

### 批③ 类目权威边界重定义（P0）
**目标**：让 #86 语义闸在主场景真正生效；堵两条自证换类目后门。
**已定案**：
- **skill 出证 + worker 消费**（worker 不新增 pairwise LLM——成本与语料都在 skill 侧，有进程内缓存）。
- worker 降级阶梯（替换 `_divergent_match_block_reason` 的权威二值豁免）：divergent+权威+**两侧语料
  齐备**（1688 类目名+面包屑均在信封）→ 降级为非权威走既有全闸链（R2b/Step6.5 域守卫），过闸放行、
  不过 `_blocked_exit` 入箱；语料缺失 → 留证不拦（unknown）；非权威 → 现状硬拦。**不 fail-closed 硬拒
  权威**（what_to_sell 在 seller 登录时覆盖大多数候选，硬拒=停摆）。
- manual 豁免 = `source=="manual" 且 dc/tp 数字且在树中`（不按 source 字面，防绕树校验）；R1 对
  manual 仍硬。
- **堵后门**：①R4 不再伪造 R2b 标记（实锤 E）——只在真过 `_r2b_confirm_adoption` 时置 R2b；换类目
  候选过 `_non_generic_overlap_words`（源词=signal `validation_retry_loop.py:1164`），零 overlap →
  再走 R2b/入箱，不静默保留旧类目。②Step 6.5 同款守卫（assemble:2656-2730/2745-2824 两处采纳点）。
  ③follow 空标题不走 `matches[0]` 直通（cloud_probe:4699），改标 unknown。
- follow 三值判定统一：拒 / 出证放行（unknown 或 divergent）/ 放行——修掉 follow unknown fail-closed
  与 discover unknown 放行的政策冲突。
- graph 链：声明「无闸」写进 CONTRACT/文档（无 Ozon 语料）；可选增强 `--ozon-ref-url` 补
  category_path 出证（拍板项 §7-5）。
- semantic_unknown：**留证不拦**（硬拦会让 CDP-only/无 AK 环境大面积停摆——缺 1688 类目名是常态）；
  `_assemble_match_evidence` 与 `_apply_discover_page_truth` 加占比埋点，跑一周定量后再议升级。
- L0 清洗：一次性脚本 `worker/scripts/audit_category_mapping.py`（--dry-run/--apply），三档判据——
  dc/tp 不在树 → is_active=False；leaf 与 cat_zh 零 overlap → 降权 succ=1 走弱档（不下线）；
  cat_zh 空 → 只报告；curated 恒不动。**不做 card_audit 第 5 不变量**（全局表 vs 卡片维度不匹配）。
- 测试：`test_semantic_gate_divergent_v082` 三条权威放行断言按新阶梯重写；follow 闸测试族同步。
估 4-5 人日。

### 批④ agent 后台化（P1）
**已定案**：
- 前置（实锤 F）：discover/discover-multi/seller/queries 补结构化尾 JSON（run_id+候选计数）；
  skill 新 `lib/detach.py`，注册表单落 `skill/data/jobs/{job_id}.json`，pounding-mcp job_* 读同路径
  （env 指路）——单事实源，不出第二套 id。
- pounding-mcp 重工具（discover 族/follow/seller/queries/graph）`background` 默认翻 True（保留
  `background=false` opt-out）；`job_status` 加 run_id/session_path/next_poll_s（已有）；
  `job_result` 保证含 run_id。
- skill CLI：六重命令加 `--detach`（fork detached + 写 job 注册表 + 打印句柄行 + NEXT）+ `jobs`/
  `job-status` 子命令；`--detach` 与 `--wait` 互斥；锁跟子进程走，查询侧永不持 heavy_cdp.lock。
- NEXT 行：新增出口全部接 `_print_next`；`test_next_hints_v079` 既有 needle 文案不破坏，新出口追加。
- 文档：SKILL.md §1/§2 + references/commands-discovery/commands-ops 口径翻转（「默认即后台，拿 job
  句柄轮询；同步结果显式 background=false」）；`graph --no-submit` 展示态在后台化后经 job_result
  获取——行为变更写发版说明。
**改动面**：skill + pounding-mcp + 文档；worker 零改。估 3 人日。

### 批⑤ discover Session 落盘 canonical（P1，前置④⑥身份层）
**已定案**：
- 一次 discover = 自包含 session 文档 `discover.session.v1`：`{schema_version, session_run_id(disc_*),
  created_at, entry, params, env, candidates[{id/ozon/metrics/competition/match_1688/pricing/status/
  status_reason/discovery_meta/provenance(raw 裁剪)}], summary}`。
- 本地 `data/discovery/sessions/{run_id}.json` + `index.jsonl`；`analysis_*.json/md` 停默认生成
  （`--report` 显式开关）；旧 2391 文件不回填，`load_latest_discovery` 兼容读旧。
- worker `discovery_runs` 表加 `session_run_id`（可空+部分唯一）/`schema_version`/`session_json` 列
  （init_data 幂等迁移）；上报改幂等 upsert（现状裸 insert 会撞 500）；**请求体上限 4MB**（现状无闸）
  ；raw 证据按候选 cap（图 N 张/文本截长）；daemon 上报改可重试 + skill `--sync-sessions` 补传。
- **引用化红线**：`extensions.discovery_meta.ozon_price/min_competing_price` 恒 materialize
  （price_sanity_guard 提交闸输入，引用化=守卫依赖网络）；其余 meta 键可投影引用；run_id 只放
  `extensions.discovery_meta.run_id`（禁 drafts 顶层列——CSV 导入无 discovery 来源）；信封业务键
  （purchase_url/weight/attributes/ozon_category 等）恒自包含。
- 读取：新端点 `GET /api/v1/discovery/runs/{session_run_id}`（**跨租户 404**，不可枚举 id）+ list
  保持全局共享；worker MCP 新增 `get_discovery_run`（22→23，同步 `test_mcp_server.py:64` 断言与
  `_call`）；pounding-mcp discover 返回带 run_id。
- CSV：skill/drafts 列名对齐 canonical（新增列+旧列保留一个版本，列序契约测试两侧同步）。
估 5-6 人日（Tier A）。

### 批⑥ 回执真值化（P1，与④并行）
**已定案**：
- **零新增 Ozon API**：`learning_record._backfill_category_commission` 已打 `/v5 prices`（只取佣金丢
  其余）→ 同响应补算实盘利润，经 GraphOutput 新字段透传（必须声明进 GraphOutput，channel 纪律）→
  task_processor 落 listing_result_log 新列。
- card_audit 第 5 不变量 `profit_reality`：`_fetch_price_map` 补留 commissions（日级批量零增量）；
  预估利润（listing_result_log.pricing_info.profit_estimation）vs 实盘利润差超阈值开 finding
  （部分唯一索引已就绪）。
- **实盘口径**（防噪声把不变量做废）：实盘 = `marketing_seller_price`（买家实付）− 采购 − 国际运费
  − （售价×真实佣金率 + acquiring + fbs 物流费折算），**未建模费项单列**；多 SKU 逐 product_id；
  非 RUB 店汇率走 worker FX 链（现状非 RUB 汇率断链）；阈值双门（PCT+ABS）+ 最低价门槛；
  `commission_source=fallback` 命中的卡降级为报告不自动开 finding（设计内差异）。
- 跟卖卡回执口径：确认 follow 链是否真设价后写死（拍板项 §7-6）。
估 3 人日。

### 批⑦ 杂项收口（P2，字段定型后收尾）
采集箱双币统一（H2：assemble RUB vs estimate CNY，两端显式 currency_code）；webui PricingPanel/
TemplatesPanel margin 三档语义对齐 `is_dual_margin`；hooks.ts 类型补全（DraftDiscoveryMeta 18→33 键、
EstimateStandaloneRequest 补三档键、DiscoveryRun 加 run_id）；min_price 扩面（单档派生底线，评估
UPDATE/多 SKU）；佣金 resolver fbs/fbo 前缀回退（批①顺带则此处销账）；`estimate_service.py:26-27` 与
`pricing_node.py:165,230` 常量去重。

---

## 4. 依赖拓扑与实施顺序

```
①预估统一 ──强前置──► ⑤session canonical ──弱前置(身份)──► ④后台化
                        │                                    │
②描述/Rich（独立，与①并行，共用 payload 出口）              ▼
③类目权威边界 ──────────────────────────────► ⑥回执真值化（依赖 card_audit 已上线）
                        ▼
                  ⑦杂项收口（最后）
```

推荐顺序：**①+②并行开工 → ③ → ⑤ → ④+⑥并行 → ⑦**。会话资源充足时 ③ 可与 ①② 的后半并行
（不同文件面）。
每批验收闸：

| 批 | 验收闸 |
|---|---|
| ① | gen_api_docs --check 绿；三处对账测试逐字段相等；skill 信封不含 margin 自算键断言；实机 ≥3 单预估 vs 卡价一致 |
| ② | 死字段确认（步骤 0）；4191 恒填+锚定闸回归；11254 探针结论落档；实机卡 rating 复检 |
| ③ | `test_semantic_gate_divergent_v082` 新阶梯全绿；R4 伪 R2b 标记杜绝断言；实机错配拦截 |
| ④ | test_smoke(30)/test_param_parity/test_next_hints 绿；旧同步路径兼容；实机 2 调用直达业务 |
| ⑤ | run_id 迁移幂等；体上限+upsert 断言；MCP 工具注册数更新；gitleaks 无 cookie |
| ⑥ | card_audit 用例绿；profit_reality 幂等；实机 1 单实盘回填 |
| ⑦ | bunx tsc -b + build 绿；双币列序测试两侧绿；VERSION 四源 |

---

## 5. 发版与兼容矩阵

- **同车 v0.83.0**：worker 先合 dev（端点/迁移/类型就位）→ skill 跟进（算价退役/jobs/尾 JSON）→
  gen_api_docs + webui 快照刷新 → VERSION 四源 → 实机 gate（本地 Docker + 测试店 5381204）。
- 新 worker + 旧 skill：旧 skill 仍塞 margin_rate 单档键 → worker 键存在判定旧行为逐字保持，安全。
- 旧 worker + 新 skill：新 skill 停算价后信封缺 margin 键 → 旧 worker 三档默认 → **上架价会变**
  （v0.65 拍板口径），发版说明必写；estimate/batch 404 → skill 无预估降级。
- MCP 默认后台翻转为显式行为变更；旧 harness 显式传 background=False 不受影响。
- ruff 全规则集（TRY 组）与 CI 口径自查；新端点全带 examples；MCP 工具数断言同步。

---

## 6. 生产遗留处置（时机挂钩）

- **5 张错源卡**：等批③落地后，开 `CARD_AUDIT_LLM_TOKEN` 跑 C 不变量（source_mismatch）清单化 →
  人工批归档+重匹配（重跑语义闸对新信封生效，对存量卡无回溯通道——必须走 finding 人工）。
- **41 张 legacy declined**：card_audit B 不变量首轮全量发现（已上线），人工归档+重上，不进自动修。
- 生产 init_data 随 cos-update 自动跑（批⑤新列迁移就位后）。

---

## 7. 拍板清单（等用户，其余均按本文「已定案」执行）

1. **描述撰写口径**：撰写为主+数字锚定+宁短不编（推荐）——确认后批②开工。
2. **跟卖卡内容红线**：跟卖卡我方不写 4191/11254（含 fetch_back/card_audit 豁免）——确认维持。
3. **11254 文字块探针**：批②开工时对测试店 5381204 发一张探针卡（真实 import 写操作）——授权。
4. **worker 不可达时预估显示**：「无预估（不回落旧公式）」——确认。
5. **graph 链类目**：声明无闸（推荐）还是 `--ozon-ref-url` 补面包屑出证（+工作量）。
6. **跟卖回执口径**：follow 卡是否我方设价（决定实盘利润公式形态）——需一句确认。

---

## 8. 明确不做（non-goals）

- 不做 worker 侧 pairwise LLM 权威复核（成本/语料/cache 三输）。
- 不做 semantic_unknown 硬拦升级（先埋点一周定量）。
- 不做 discovery_sessions 并存新表（演进 discovery_runs）。
- 不做 session 证据全量入云（raw 按候选 cap，大 blob 不拆 COS——体积观察后再议）。
- 不做 L0 清洗常驻巡检（一次性脚本+定期手动）。
- 不动跟卖竞品卡面（A6 红线，本战役反而堵三个相关漏洞）。

## 9. 工程纪律检查表

- 五处精确锁断言先改后动代码：`test_mcp_server.py:64`(22 工具)、`test_smoke.py:66`(30 工具)、
  `test_discovery_export_csv.py:138`(列尾)、`test_next_hints_v079.py`(NEXT 前缀)、
  `test_semantic_gate_divergent_v082`(三态)。
- 每批 Tier 判定：①③⑤⑥ Tier A（worktree+PR+CI 绿）；②④⑦ 视改动面，>3 文件一律 Tier A。
- 新端点 Bearer+限流+租户矩阵对照 v0.76 收口；Mimosa 关注 IDOR（session 明细）/体量 DoS/CSV 中和。
- langgraph 纪律：新节点 Input 声明读字段；GraphOutput 新字段显式声明否则 channel 静默吞。
- 信封键三处同步（skill/worker state.py/CONTRACT-v4）。
