# 09 · 问题暴露清单（本轮全链路梳理产出）

> 来源：2026-09-25 九路并行深读（skill 链 / graph 骨架 / 类目 / 属性 / 定价 / 图片 / 校验重试 / 模型矩阵 / 生态）+ 关键锚点人工抽查复核。
> 分级：🔴 行为级 bug（影响线上结果）· 🟠 一致性/门禁缺口（双源分叉、防线绕过）· 🟡 质量/成本 · 📚 文档与口径漂移 · ✅ 已知 defer（确认仍在，不算新发现）。
> 每条带锚点；修前先读对应分域文档的上下文。

---

## Top 10 先修（按 影响×确定性 排序）

| # | 级 | 问题 | 锚点 | 一句话修法 |
|---|---|---|---|---|
| 1 | 🔴 | **变体生图失败回退 1688 原图 → 撞出口硬闸 → 整单 IMAGE_GEN_ALL_FAILED**。variant_primary_loop 失败时把 `variant.image`（alicdn）当产物返回（v0.60 决策）；v0.78 批A 后 `_enforce_payload_image_policy` 判它 external → 即使其余 5 张 AI 图全成功也整单失败重试，兜底已从「救缺图」变质为「毒化整单」 | variant_primary_loop_node.py:148-158,166-176 + prepare:4136 | 失败置 None 走缺图语义（单变体缺图靠主图顶），删原图兜底；同步改注释 |
| 2 | 🔴 | **进度条从高位跳回 0%**。`_NODE_STAGE_MAP` 缺 assemble_ozon_product/scene_generation_llm/visual_vars_llm/check_quota/fetch_back/validation_retry_wrapper/follow_sell_import/variant_primary_loop → `update_progress` stage_idx=0。assemble 恰在管线中段，**每单必现一次倒退** | task_processor.py:289-300（已抽查实锤）+ main.py:91-95 | 补全 map（assemble→category_match、follow_sell_import→ingest、variant→image_generation…）；stage 缺失时保持上一阶段而非归 0 |
| 3 | 🔴 | **UnboundLocalError**：`items_title` 只在 `if repaired_title:` 块内赋值（:2053），:2054 兄弟行直接引用。强制翻译失败/box_reviewed 清空 repaired_title 后必炸；是否静默取决于外层 try——标题修复支路可被打断 | validation_retry_loop.py:2051-2054（grep 全文件仅 3 处，无更早绑定，已实锤） | `items_title` 取值移到 `if repaired_title:` 外，或把 2054 并进该块 |
| 4 | 🟠 | **OutOfQuota 吞异常回归口 ×5**（v0.63.1 修复被新代码重新引入）：A5 整段（prepare:4043）、_translate_ru（attr_fill_extras:639）、revalidate 两处翻译（retry:3167/3193）、ai_field_service（无特判→500）。余额耗尽时静默降级而非明确失败 | 各锚点 | 统一 `except MxouOutOfQuotaError: raise` 前置（仓库已有 30 处先例） |
| 5 | 🟠 | **信封注入口径三裂**：graph/跨平台走 `_merge_config_tiers`（含模板 9048 前缀/三档键），follow 腿手工只注 7 数值键，discover 降级腿只注 3 键；且 follow 腿**无 `_source_preflight`**、search 批量 `_submit_one` 绕过全部门禁（preflight/min-margin/density）且失败不影响 exit 0、batch_test 进程内直调不进 heavy 闸 | cloud_probe.py:4479-4484/3096-3099 vs 1490；cli.py:455-476；batch_test.py:238/384 | `_merge_config_tiers` 提为唯一注入入口（三腿同源）；follow 接 preflight；search/batch_test 对齐 graph 门禁语义 |
| 6 | 🟠 | **retry 补 9048 与 prepare 形状不一致**：prepare 用 `item_id~sha1(supplier|标题)[:8]`，revalidate 兜底用 `name[:50]/sku_id`（:3217-3245）——快照丢 9048 时重传值与首传不同，**变体拆卡/并卡风险**（与「防重翻译不一致」自设目标相悖） | retry:3217-3245 vs prepare:1861-1881 | revalidate 复用 `_derive_model_name_9048` 同一函数 |
| 7 | 🟠 | **OzonValidateOutput 无 error_code/failed_stage**（state.py:625-652）——validate 阶段失败在留存表 error_code 恒空，归因只能靠文本；与 v0.77.2「终态必须带码」方向不一致 | state.py:625-652 | Output 补 error_code/failed_stage 声明 + validate 失败出口赋值 |
| 8 | 🟠 | **`_CONTENT_VIOLATION_KEYWORDS` 含宽泛 `"content"` 子串**——任何含 content 的普通错误文案（如 invalid content type）被误判内容违规 → 不重试不降级 + 任务判永久失败 | mxou_api.py:86-89,548-557 | 关键词精确化（violation/policy/sensitive…），去掉裸 content |
| 9 | 🟡 | **A5 schema-LLM 默认开**：`getenv("LLM_SCHEMA_FILL","1")!="0"`——新环境未设变量即生效。作为 kill-switch 语义没错，但发版说明/部署清单须明示（AGENTS 已写，但部署侧无哨兵提醒） | prepare:4021 | 保持语义；`_assert_critical_configs` 或启动日志加一行显式提示当前开关态 |
| 10 | 🟡 | **定价双实现微差**：三档判定键存在 vs 值非 None（graph 三档而 /estimate 单档的可能）；provisional 选档 kwargs 传/不传；`pick_price_band(None)=leq_1500` vs 手工 leq_5000；compute_price 裸调全缺省=单档 vs 外层=三档（语义相反陷阱） | pricing_node.py:160-176 / estimate_service.py:93-124 / pricing_estimate.py:92 | 判定与临时价口径收敛进 commission_resolver/pricing_estimate 共享函数 |

---

## 分域全清单

### 类目链
- 🟠 **match_layer="R2b" 只在 retry 产生**；assemble 内 R2b 确认后仍是 "L1"（`_r2b_confirmed` 内存旗标），R2b 采纳率无法按列统计（A:1962/1975）。
- 🟠 **web 面包屑映射直通复用 `_resolved_by_path=True` → 自动获得 R1 唯一豁免**（A:1372-1383）——学习表错行被 approved 固化后，会把错配升级成 R1 豁免通道。
- 🟡 权威档 L0/Skill 完全跳过 R2b——权威错配无 LLM 复核，第一次错配由 Ozon declined 买单（对冲只有事后负反馈）。
- 🟡 confidence 只升不降（conflict 侧 greatest，L:704）——lookup 的 MIN_CONFIDENCE=0.6 门槛形同虚设。
- 🟡 blocked 入箱幂等：已提交过（哪怕 failed）再阻断就新建行——长尾商品可堆积多张同 item 草稿（B:72-95 有意设计，建议加窗口上限）。
- 🟡 curated 种子 succ=5 直升权威 + 负反馈不下线：错误种子要 5 次 declined 才沉底，期间跳过所有闸。
- 🟡 learning source_value 推断过宽：`in` 双向 + `startswith("[")` 判 default_fallback——"[官方旗舰店]" 类真值被误标不涨 succ（LR:504-519）。
- 🟡 route_after_assemble 双口径（failed_stage 通道 + 错误文案魔法字兜底 G:243-245）——改文案漂移即漏拦。
- 🟡 `_log_match_attempt` fallback 仍用 state.task_id（随机 uuid4，ingest_node:119）——config 缺失路径审计写歪（A:4270-4276）。
- 🟡 0 候选（A:1596）与 type_id 无效（A:2035）两个阻断出口不写 blocked 审计行。

### 属性链
- 🟠 **top-1 盲采残留三处**：assemble 可选补齐 `_results[0]`（:3743）、retry `_search_dictionary_values_chain` `result[0]`（:575，**且写回 final_attributes 重发**）、prepare 主循环 search 首个合法（:3018）——与「绝不盲补首值」红线不一致（v0.71 只修了校验回填一处）。
- 📚 「5379 宁缺毋滥」无对应代码——全 worker/src 无属性 5379；实际是 9379/22232（海关族）。口径笔误待澄清。
- 🟡 `fetch_ru_dict_value` 仍 `limit=5000`（官方钳 2000）——分页仍工作但首页有效数据比声称少（ozon_dict_values.py:124）。
- 🟡 retry `_get_attribute_schema` 回写 TTL=1d（:723）vs 全库 30d——重试场景反复回源 Ozon。
- 🟡 A6 豁免 A4 有风险窗口：竞品卡自带箱规数量类值（8513>200）原样照抄，只有「对方已过审」隐式担保（prepare:1688）。
- 🟡 A6 UPDATE 就地读回（prepare:4051）+ 三出口各读一次 /v4，跨调用无去重缓存。
- 🟡 `merge_copied_card_attributes` 只对带 product_id 的 item 生效——CREATE 场景 C-⑨ 恒空转，「合并+N」日志误导。
- 🟡 中文直搜旁路触发宽（共享任意 ≥1 中文字符）——多打 /values/search（prepare:1265-1272）。
- 🟡 vision 推断失败/多候选放弃无审计打点（缺口榜统计不到）。
- 🟡 **prepare 直搜链未接 `get_or_fetch`**——大字典×5 页×多属性放大请求数（缓存读穿只在 assemble/schema_service/retry 三处）。
- 🟡 attr_bounds_learned **只紧不松**：Ozon 放宽限额时旧学习值永久过夹，无过期/重置机制（:166 注释认账）。

### 图片链（Top10 #1 外）
- 🟠 social_proof 降级循环缺 MxouModelConfigError break（对比 main:138-145 有）——配置错模型重复 POST。
- 🟡 main 节点双层降级链可能重复 POST 同一坏模型（API 层从 fast 下一级起步、节点层又从 fast 开始）。
- 🟠 `is_cos_url` 域判定子串过松（`"cos." in host`）——`mycos.evil.com` 误判本方 COS；上卡闸靠 key 前缀兜住，但「唯一事实源」第一层名不副实（image_url_guard.py:68）。
- 🟡 validate_plan 死代码——纯 Phase2 plan 静默连锁跳过全部生图。
- 🟡 多 SKU 主图优先级注释与代码矛盾（注释称 variant 优先，代码 main 优先，prepare:3467 vs :3477）。
- 📚 LOCAL_IMAGES_MISSING / CARD_IMAGE_MISMATCH / VARIANT_NOT_MERGED 自由字符串码未收进 errors.py 枚举（自称统一错误码体系）。
- 🟡 card_image_assert 只查 items[0]；混合载荷只查数量不查 3:4。
- 🟡 regen 产物不落任务缓存（整任务重试时主图二次烧额度面）。
- 🟡 validate 空图 critical 判定靠中文文案子串（「缺失」）——文案改动即静默失效（ozon_validate:658）。
- 🟡 跟卖 regen 参考退化（filter_product_images 不放行竞品图，与批I「竞品图=参考」口径不完全一致）。

### 模型调用（Top10 #4/#8/#9 外）
- 📚 生图模型三源漂移（PRIMARY=gpt-image-2.5 / imagegen.main=gpt-image-2.5 / DEFAULT_NODE=gpt-image-2）——回滚只改 imagegen 但 PRIMARY 常量不同步。
- 📚 LLM 模型名双轨：worker vision-exp 约 12 处硬编码绕 config；skill 用 flash 4 处——上游更名要改 ~16 处。
- 🟡 温度漂移：config 全 0，但 scene/visual 代码默认 0.7、`_call_mxou_llm` 默认 0.3、描述重试 0.3；键名 max_tokens vs max_completion_tokens 不统一（读错键静默落默认）。
- 🟡 翻译验收负检不一致：retry/attr_fill/ai_field 是「含西里尔**且**无中文」，prepare 主路径只查正向 `_has_cyrillic`——中俄混合标题可漏过（靠二次兜底）。
- 🟡 attr_disambiguation_cfg 假热加载（模块级缓存无 TTL——改文件需重启）。
- 📚 category_synonyms.json 末尾 4 个值是空格字符串而非数组。
- 🟠 **skill 直调无治理**：4 处 deepseek-v4-flash 直连（无限流/无台账/无余额闸/无 Sentry 指纹；cloud_probe.py:3569 还未禁 thinking）——`mxou_call_ledger` 对账缺这 4 类（见 07§7）。

### worker 骨架（Top10 #2/#3/#7 外）
- 🟠 image_gen_plan 通道断链：Input 全声明、GlobalState 无字段、队列不注入——生产恒 DEFAULT_PLAN（预留接口非活通道，读代码易误判）。
- 🟡 ingest task_id（uuid4）与 DB 任务 id 双轨（部分位置可能误用第二真相源）。
- 🟡 PrepareOzonUploadOutput.failed_stage 默认值非空（state.py:527，唯一违反「默认值归零」纪律的 Output）——异常路径留非空 error_message 时会放大成 failed。
- 🟠 **check_quota 定时炸弹**：无 Input 注解 + `route_after_early_quota` 路由读 `state.envelope`——谁按纪律补 Input 注解，路由立刻 AttributeError/恒走 full 分支。
- 📚 set_graph docstring 与代码相反（/run 族实际写 checkpoint）。
- 🟡 MCP 限流双计（一次调用计 2 次，有效配额减半，无文档提示）。
- 🟡 `should_handle_error` 内变量重复声明（graph.py:351-359 残留）。
- 🟡 has_pending 把 status="skipped" 当处理中（最坏空转 10 分钟，ozon_status:224）。
- 🟡 PRICE_ERROR 分类无策略（parse_error:800 造码但 REPAIR_STRATEGY 未收录→价格错被全量重传而非 prices 靶向）。
- 🟡 REPAIR_STRATEGY/ERROR_NOTICE_MAP/FIX_TYPE 三表无一致性约束（新增码漏登记只能靠人眼；ERROR_NOTICE_MAP 注释仍写 18 条实际 22 条）。
- 🟡 validate 密度 ÷10 自修复可能二次破坏（修复后不复查契约上界，ozon_validate:236-251）。
- 🟡 moderation_texts cap50 vs 报表只取前 2——长尾原文无人消费。
- 🟡 UPSERT_BY_OFFER 纯常量死开关（注释称可置 False 但无 env 通道）。
- 🟡 follow-sell 9048 裸 item_id 对存量误写 hash 形状的卡无迁移路径（UPDATE 永不纠正）。

### 定价（Top10 #10 外）
- 🟠 upsert_category_commission 无入参守卫（0 段值可落库；当前单写方受控）。
- 🟡 stale 标记误标：缓存超龄但 segments 命中 → source=segments 却 stale=True → commission_source="stale_fallback" 审计带偏。
- 🟡 pricing_node 算出的 band 只用于日志（resolve 内部重算，逻辑重复易漂移）。
- 🟡 assemble 变体价格初值是 CNY 采购价（依赖 prepare 覆盖；旁路直传=CNY 冒充 RUB，v0.14 历史事故）。
- 🟡 汇率 fallback_12 静默价差 + /estimate 不接汇率（UI 对比价与实际价不同币值）。

### skill 线（Top10 #5 外）
- 🔴 `check` 条件表达式运算符优先级 bug：`if session_ok and alibaba_cdp_ok or session_ok:` 等价 `if session_ok`——`alibaba_cdp_ok` 恒无效果，疑为括号笔误（cli.py:1284）。
- 🟠 `--no-submit` 连 preflight 也跳过——展示态信封可能带反爬页数据而用户无感知（cli.py:859-861）。
- 🟠 `--wait`×`--to-box` 组合静默忽略（draft_id 非 task_id）；且 discover/discover-task 批量 `--wait` failed 只打印不回传失败码——同为「提交后失败」不同命令退出码不一致（cli.py:2203/3309 vs graph 925）。
- 🟡 DEFAULT_WEIGHT_G 注释与实现漂移：选品运费缺重按 500g 分段、信封缺重兜底 50g——同一商品选品期估 ¥6、上架按 50g 进 worker，两链口径不同（ozon_discovery.py:45 vs cloud_probe.py:1050）。
- 🟡 match_1688_freight_cny 占位键：唯一读者读 `match["freightCny"]`，全库无通道产出——CSV「1688 国内运费」列恒空（ozon_discovery.py:1332）。
- 🟡 ai 档阈值双份维护（cli.py:2745 `_AI_DEFAULT_RULES` vs ozon_discovery.py:1577 `AI_PRESET` 字面重复）——改阈值需双改。
- 📚 死代码：publish_product_new / build_variant_envelope（cloud_probe:3377/3291）；ozon_seller.py 三导出无生产调用。
- 📚 envelope_example.json 仍示例 `draft.stock:100`（stock 已退役）。

### 生态线
- 🟠 直调头契约漂移：worker 带 `x-o3-app-name: seller-ui`，skill `_seller_direct_post` 没有——skill 侧部分 403 可能是缺头（ozon_seller_analytics.py:1240 vs ozon_session_client.py:62）。
- 🟠 bot-403 误判 session_expired：403 一律判废联动 mark expired——DataDome 拦截时误杀活会话（ozon_session_client:82；改进已在 PLAN 登记）。
- 🟡 畅销榜 map 双缓存口径分裂（直调/CDP 互不命中各存一份，可读相距 6h 的两份快照）。
- 🟡 30min cookie 快照 vs 分钟级 token 寿命错配（快照命中率高=直调 401 率高）。
- 🟡 池 stale 语义与 has_analytics 真实 0 歧义。
- 🟡 `_sync_products` total 兜底估算——上游字段改名→循环提前结束漏同步静默。
- 🟡 market-bestsellers 无直调版（DataDome 收紧时唯一必开 seller 页分支）。
- 🟡 webui 提交错误归因过粗（400/422 一律「请先选择有效店铺凭证」，吞 sanity 拒单真实原因）。

### 文档与口径漂移（集中清）
- 📚 WORKER-TOPOLOGY.md:239 图片顺序与 `_IMG_ORDER` 不一致（social_proof/detail 对调）；头部更新日期 2026-08-05 落后多版路由变更。
- 📚 CONTRACT-v4.md：auth 字段清单缺 failed_stage、失败示例 progress_counter=0（代码 1）；check_quota「复用 OzonUploadInput」与代码不符；ingest status=running（代码 accepted）且缺 error_message/failed_stage。
- 📚 AGENTS「category_cache 90d」vs 代码 TTL≈10 年（L:330，读侧当持久化）。
- 📚 ARCHITECTURE-TOPOLOGY.md 自认 v0.27 口径——本目录文档集可作为其 v0.80 替代素材。

### ✅ 已知 defer 确认（非新发现，现状核实仍在）
- variant_v2 真值链三段断链（fetch_variant_truth 零生产调用/variant_payloads 无人传/needs_variant_sync 零消费）。
- 抬重后定价不重算（同带影响趋零）。
- follow 6h 信封缓存复用窗口（重测须双 ns 同清；cleanup 无按 ns 精清）。
- DataDome 数据面平台侧阻断（requests 403 终态/SPA Bearer 401；roadmap=CDP 捕获 SPA Authorization）。
- `__Secure-access_token` 分钟级寿命三候选待拍板。
- box_reviewed 只看布尔不带「类目是否被用户改过」粒度（所见即所得契约的已知取舍）。
- MCP 限流双计（代码注释自认）。

---

## skill 优化建议（可用 skill-creator 落地）

上一轮 v0.79 已把 SKILL.md 重构成 Quick Task Reference + 自由度档 + Red Flags 形态，骨架是对的。本轮梳理暴露的 skill 侧改进，适合下一轮用 `skill-creator` 走一遍结构化修订：

1. **信封注入唯一入口化**：SKILL.md/专家模板里「配置了才注入」的口径与代码三腿裂口（#5）对齐——把 `_merge_config_tiers` 写成 SKILL.md 里的硬约束（「任何新提交腿必须走它」），并在 references/commands-*.md 相应命令节标注当前缺口（follow/discover 降级腿）。
2. **门禁矩阵进 Red Flags**：把 exit 0-4 × 各命令 × 各门禁（heavy/preflight/min-margin/density/wait）做成一张速查表——本轮发现 search/batch_test/--no-submit/--wait×--to-box 四处语义不对齐，agent 手册里没有对应警示。
3. **check 命令语义修正**：cli.py:1284 优先级 bug 修掉后，SKILL.md「check 全绿=可跑单」的口径才成立；补「cookie 在 ≠ 会话活（probe_seller_session_alive）」的判定说明。
4. **references 更新**：envelope_example.json 删 stock；新增 follow/discover 降级信封与主链信封的键差异对照（正好用 01§2 的表）；legacy 死命令（publish_product_new 等）从文档面摘除。
5. **路由口径核对**：pounding-mcp router.py 九类意图与 SKILL.md §1 话术表逐行对齐（本轮未逐行核对 router 与 SKILL.md 漂移，可作为 skill-creator 任务的一部分）。
