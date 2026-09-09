# 用户反馈 6+1 问题修复方案 v2（v0.73.0 批次，已按生产库取证修正）

> 2026-09-09 取证定案。生产 = v0.70.0；本批全部修复需发版部署生效。
> 取证底稿：会话记录 + `/tmp/issue_20260909/`（10 个任务 task_status 全量 JSON）+ ozon_ro 生产库只读查询（Q0-Q8）。

## 精准定位（git 考古 + 生产库只读取证，2026-09-09）

v0.70.0..HEAD 36 提交中仅「评分信号换轨」有半截修复（2831a564 skill 模块，接线在他会话 WIP），**其余问题零提交触及**。生产库实证修正三处设计假设：①Issue 3 与 5a 同根（本地预检错配分类）；②密度闸只能兜底提升不能硬拒（approved 可低至 0.015 g/cm³）；③转盘信封 discovery_meta 为空，价差守卫拦不到本例。

| 问题 | 生产实证根因 |
|---|---|
| 快照 0 条 | 任务 tenant_id=`28` vs 报告 tenant_id=`user_7f15ad…`，auto_context 原文即「不属于当前租户」；main.py:2525/2546/2587/2719 四处 `_key_user_id` 未随 v0.62.4 换 `resolve_tenant` |
| Issue1 类目误读 | 树 16552 节点为裁剪版、无「留香珠」叶子；top 候选 sim 0.125（洗衣机）；match_layer=blocked 9 行；LLM fallback 无可靠结果——仲裁 prompt 缺货源类目上下文（assemble:3918 只读旧字段 draft.source_category，新信封 source.source_category_path 没传入） |
| Issue2 低置信误拦 | exact type_name 无加分通道（多 token 子串命中即 1.0 打分制下枕套 exact 仅 0.25）；0.3/0.5 阈值两处裸字面量 |
| Issue3 「中文属性拒绝」 | **真相**：moderation_texts 全文=「标题与类目不一致」本地预检文案，被 parse_error:710 错归 BR_chinese_hieroglyphs_in_attribute → ERROR_NOTICE_MAP 套上误导文案；并非 Ozon 中文拒单 |
| Issue4 体积重量 | 管线既有 ≈0.40 g/cm³ 兜底簇（p25-p75 紧贴 0.398-0.402），出事相机 0.375 从其下漏过被 Ozon ML 拒；俄语原文无数字区间（放弃解析区间，直接反推）；数据禁硬拒（approved 密度 0.015-0.03 共 32%） |
| Issue5 假成功 | validate critical「标题与类目零交集」被错归中文码 → 修复分支不修 → revalidate 不复查 → 子图重传 → approved（graph.py:310 已删外层阻断边）；转盘信封 discovery_meta=空，3418₽ 锚只在客户本地选品记录 |
| Issue6 product_id 缺失 | import 无 task_id 时静默 pending（ozon_upload_node:239-248）；product_id 字段被装 import task_id（:320）；8a25469a 也现「未获取 Ozon task_id」 |
| 杂项 | failed_stage=operator.add+三 Output 默认值（state.py:444/642/803）每次运行必粘连；ERROR_NOTICE_MAP 文案误导；task_status 无鉴权（main.py:1816/2129，skill cloud_probe.py:3582/3635 裸调） |

**前置运营动作**：下架 Ozon 卡 6275520552；观察 6275514282。

## Global Constraints
- 本地 Docker 测试（禁生产）；worker：`cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/<file> -q`；收尾全量 tests/（基线 2113）
- langgraph 纪律：节点要读的字段必须声明进 Input model；box_reviewed 红线（中文翻译属合规修复豁免）
- 入箱唯一入口 `utils/blocked_draft_box.create_blocked_draft/format_box_notice`
- 多会话 WIP：worker/src 无 WIP 可安全改；skill 的 cloud_probe.py/cli.py/ozon_discovery.py/match_scoring.py 有他会话 WIP——涉这些文件先看 git status+mtime，冲突则只做无冲突侧并登记待办；逐文件 git add
- 留存表列名（生产实证）：`listing_result_log.task_db_id / weight_g / dims_mm(jsonb:l/w/h)`，非 task_id/final_*
- lint 双侧 ruff；commit `<type>(<scope>): 中文描述`；新测试 `test_*_v073.py`

---

### Task 1: 租户漂移根治【P0】
**Files**: Modify `worker/src/main.py:2525,2546,2587,2719`；Test `worker/tests/test_tenant_resolution_v073.py`
- 失败测试：monkeypatch `services.tenant_service.get_supabase` 假链返回 user_id `"28"`，monkeypatch `services.error_report_service.create_error_report`/`services.forensics_service.get_task_forensics` 捕获入参，直调两个端点函数（fake Request 带 Bearer）→ 断言收到的 tenant_id=="28"（修复前是 user_<hash> → FAIL）
- 实现：四处 `tenant_id = _key_user_id(clean_token)` → `resolve_tenant(token)`（传原始 token，其内部剥 sk-；本地无 Supabase 自动回退哈希，行为不变）
- 回归既有 error_reports/forensics 测试；commit `fix(worker): error_reports/forensics/categories 租户解析统一 resolve_tenant（报告快照 0 条根治）`

### Task 2: 本地预检错误码独立化——同时根治 Issue3+Issue5a【P0】
**Files**: Modify `worker/src/graphs/validation_retry_loop.py`（parse_error_node:708 前、REPAIR_STRATEGY:280、repair_node_selector:3650-3661、final_result_node:3560）；Test `worker/tests/test_local_block_codes_v073.py`
- 失败测试：①validation_errors=`["item[0]标题与类目不一致（Ozon DESCRIPTION_DECLINE 风险）…"]` 过 parse_error_node → code=="LOCAL_TITLE_CATEGORY_MISMATCH"（现状错归 BR_chinese → FAIL）；②repair_node_selector 对该码路由 final_result（不进 repair/reupload）；③final_result 遇该码：upload_status="blocked"、notice 含「已拦截」、monkeypatch create_blocked_draft 断言被调
- 实现：分类**最前**加 `elif "标题与类目不一致" in _le_msg: _code="LOCAL_TITLE_CATEGORY_MISMATCH"`；REPAIR_STRATEGY→"block_to_box"；selector 直达 final_result；final_result 新分支调 `graphs.nodes.assemble_ozon_product_node._maybe_create_blocked_draft`（draft 取 state.draft/envelope 兜底，candidates=[]）+ format_box_notice；核对 `_graph_result_is_failed` 对 blocked 判 failed
- 回归 retry 既有测试；commit `fix(worker): 标题-类目零交集独立错误码——拦截后入箱不再错配中文修复支路重传`

### Task 3: exact type_name 加分 + 阈值常量统一【P0·Issue2】
**Files**: Modify `worker/src/utils/ozon_category_query.py:614-652`（+模块级常量 `MIN_CONF_BOX=0.3`）；Modify `worker/src/graphs/nodes/assemble_ozon_product_node.py:302`、`worker/src/graphs/graph.py:229` 改引常量；Test `worker/tests/test_category_exact_boost_v073.py`
- 失败测试：ZH 树查询「装饰枕套」→ top1 similarity≥0.9（现状 0.25 → FAIL）；「内裤」exact>前缀>子串分档保持
- 实现：多 token 分支，候选 node_name/type_name 与整查询 strip 相等 → similarity=max(sim,0.95)；互为前后缀 → max(sim,0.8)；单 token `_score_token_hit` 不动
- 回归 test_category_match_v021 等；commit `fix(worker): 类目 exact 命中加分 + 低置信阈值常量唯一化`

### Task 4: LLM 仲裁带货源类目上下文【P0·Issue1】
**Files**: Modify `worker/src/graphs/nodes/assemble_ozon_product_node.py`（_llm_rank_categories:3861 签名+prompt:3918；调用点 549/1698/1718/1823/2298/2318）；Test `worker/tests/test_llm_arbitration_source_ctx_v073.py`
- 失败测试：monkeypatch call_mxou_chat_api 捕获 prompt，传 `source_category="个护/家清 > 衣物清洁护理 > 留香珠"` → prompt 含「留香珠」（现状恒空 → FAIL）；LLM 返回 `{"top_index":-1,"suggest_keywords":"кондиционер для белья"}` 时断言弃权+suggest 透传（二搜闭环吃上 context）
- 实现：签名加 `source_category: str=""`，prompt 3918 改 `{source_category or draft.get('source_category','')[:150]}`；6 个调用点传作用域已解析的 `source_category` 局部变量（assemble:1310 同名变量）
- 回归 test_llm_suggest_rerank；commit `fix(worker): LLM 类目仲裁注入 1688 货源类目路径（新信封断桥修复）`
- 边界注明：PG 树 16552 节点缺「留香珠」叶子，靠 LLM suggest_keywords 二搜桥接「柔顺剂」；扩树另立项不在本批

### Task 5: 重量 0.40 g/cm³ 兜底提升 + 拒后反推【P1·Issue4】
**Files**: Create `worker/src/utils/volume_weight_guard.py`；Modify `worker/src/graphs/nodes/prepare_ozon_upload_node.py`（normalizer 后接线）；Modify `worker/src/graphs/validation_retry_loop.py`（repair_dimensions_node:2258+ 增 ML_INCORRECT_VOLUME_WEIGHT 分支）；Test `worker/tests/test_volume_weight_guard_v073.py`
- 前置调查步：定位既有 ≈0.40 兜底簇的代码位置（99574d98 真实 56g 为何绕过），guard 与其统一为唯一入口
- 失败测试（纯函数，常量 `MIN_DENSITY_G_CC=0.40` 生产 199 行校准：0.398-0.402 簇=大量 approved 通过、0.375 被拒）：`(56, 83×62×29)→建议 max(56, ceil(0.40×149.3))=60g`；101g/64cc→None 不动；绝不下调、cap 原值×3、≥10g 硬下限
- 实现：prepare 接线 weight=max(weight, 0.40×vol) + marks `weight_adjusted_for_volume`（**只兜底不拒绝**——approved 低至 0.015，硬拒会误杀 32% 历史）；retry 拒后分支：ML_INCORRECT_VOLUME_WEIGHT（俄语原文无区间，弃解析）→ 同函数反推 0.40×vol 重发一次
- commit `feat(worker): 重量体积 0.40 兜底提升——ML_INCORRECT_VOLUME_WEIGHT 防复发`

### Task 6: 上传静默收口【P1·Issue6】
**Files**: Modify `worker/src/graphs/nodes/ozon_upload_node.py:239-248,317-328`；Test `worker/tests/test_upload_silent_v073.py`
- 失败测试：mock import POST 200 无 task_id → upload_status="failed"+error_message 含「未返回 import task_id」（现状静默 pending → FAIL）；有 task_id 行为不变
- 实现：无 task_id → 显式 failed；`:320` 停止把 import task_id 装进 product_id（置 None，轮询链核对读 ozon_task_id；T0.4 行为不变）
- commit `fix(worker): import 无 task_id 显式失败 + product_id 语义纯化`

### Task 7: ingest 必备字段闸 + 条件价差守卫【P1·Issue5b 重新设计】
**Files**: Create `worker/src/utils/price_sanity_guard.py`；Modify pricing_node（+PricingInput 补 `envelope: dict={}` 声明）与 ingest/prepare 的 title 闸；Test `worker/tests/test_price_sanity_guard_v073.py`
- **生产实证约束**：转盘信封 discovery_meta=空——本守卫拦不到 graph 流本例（真实上游防线=他会话 match_scoring+Task 11）；守卫只在锚存在时生效（保护 discover/带 meta 流），零误杀
- 失败测试：`check_price_sanity(35, {"ozon_price":3418})→block`（>10×）；`(115,{"ozon_price":3418})→warn`（>3×）；`(35,{})/(35,None)/(35,{"ozon_price":0})→ok`；另：draft.title 空 → ingest 阶段 fail-fast 测试（error_message 指向信封缺字段）
- 实现常量 BLOCK_RATIO=10/WARN_RATIO=3（env 可覆盖）；pricing_node 块终价后调用，block→failed+入箱（create_blocked_draft，reason 带两价），warn→marks
- commit `feat(worker): ingest 空标题闸 + 有锚条件价差守卫`

### Task 8: failed_stage 粘连根治【P2】
**Files**: Modify `worker/src/graphs/state.py:444,642,803`（默认值→""）+ pricing_node/ozon_upload_node（15 失败 return）/scene_generation_llm_node（3 处）失败显式带 failed_stage；Test `worker/tests/test_failed_stage_v073.py`
- 失败测试：成功路径归并后 failed_stage 不含 "ozon_upload"/"video_gen"/"pricing"（现状必粘连 → FAIL）；失败 return 显式携带
- 核对 `_graph_result_is_failed` 与 graph.py:221 `in` 匹配不破；全量回归；commit `fix(worker): failed_stage 粘连根治——Output 默认值归零+失败显式声明`

### Task 9: 文案 + 报告快照带拒绝原文【P2】
**Files**: Modify `worker/src/graphs/validation_retry_loop.py:186-204`（中文三行文案改「已自动翻译重试仍未通过，需人工检查」式）、`worker/src/services/error_report_service.py:_task_snapshots`（按生产列名 LEFT JOIN listing_result_log on task_db_id 取 moderation_texts 末条 → 快照加 last_decline）；Test 并入 Task 1 文件
- commit `fix(worker): 错误通知文案如实化 + 报告快照附带末次拒绝原文`

### Task 10: task_status 鉴权【P2·需协调】
**Files**: Modify `worker/src/main.py:1816-1856,2129-2133`（Bearer+租户比对，不符 404；env `TASK_STATUS_AUTH=0` 应急开关）、`skill/scripts/cloud_probe.py:3571-3635`（带 Authorization——⚠️ 他会话 WIP，冲突则只做 worker 侧并在 AGENTS 登记待办）；Test `worker/tests/test_task_status_auth_v073.py`
- 协调注记进 commit：pounding-harness 网关 tasks 白名单须透传 Bearer（drafts 已透传，风险低）
- commit `feat(worker,skill): task_status 补 Bearer+租户校验`

### Task 11: skill 侧 batch 提交闸【P2·Issue5 真正上游】
**Files**: Modify `skill/scripts/batch_test.py:353-367`（submit 前 draft.title 空或选品匹配弱档 → 跳过+log，不提交）；cloud_probe fallback 信封（:2847-2860）过 `_validate_and_fix_product_data`（⚠️ WIP 冲突则只做 batch_test 侧）
- 失败测试：构造空 title 候选 → batch 跳过不提交；skill 测试回归 `test_batch_test_reuse_discover.py` + 新用例
- commit `fix(skill): batch 提交闸——空标题/弱匹配候选不再进管线`

### Task 12: 中文残留加固【P2·纵深防御，非本批 reported 根因】
**Files**: Modify `worker/src/graphs/validation_retry_loop.py`（:1354 验收加 `not has_chinese()`、:2927 混合值补翻译、revalidate 增全属性中文终检、9024 含中文不豁免、中文检测统一 `utils/attribute_utils.has_chinese`）；Test `worker/tests/test_chinese_attr_purge_v073.py`
- 失败测试：LLM 返回混合「中文+西里尔」→ 不采纳置 ""（现状直通 → FAIL）；revalidate 终检含中文 → is_valid=False（数值属性豁免沿用 ozon_validate:475-478）
- commit `fix(worker): 属性中文翻译验收负检 + revalidate 全属性终检（纵深防御）`

### Task 13: 发版与部署【收口】
- VERSION 四源→0.73.0；CHANGELOG（含已知问题更新：Issue3 真相反转、A3 体积闭环、快照 0 条闭环、P1-6 触发条件待重核）；AGENTS 顶部块
- `python worker/scripts/gen_api_docs.py` + `--check`（task_status 鉴权进 OpenAPI）
- worker 全量+skill 全量+ruff 双侧
- 服务器 ops（CACHE-WARM-RUNBOOK）：cos-update → TRUNCATE dictionary_value_cache → ≤2 分片重预热 → export 上 COS
- 实机 gate：本地 Docker ≥3 单（1 低置信入箱、1 标题类目不一致拦截、1 正常 approved）+ 用 ozon_ro 生产库复查 error_reports 快照 attached>0

## 执行顺序
Task 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13。P0=Task1-4（租户+错配+类目），P1=Task5-7（体积/上传/守卫），P2=Task8-12，Task13 收口发版。skill 侧靶点二评分换轨（他会话 match_scoring 接线）不在本计划，完成后与本批 Task 3/4/11 上下游互补。
