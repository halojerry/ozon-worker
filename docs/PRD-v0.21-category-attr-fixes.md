# PRD：v0.21 类目匹配 / 属性兜底 / 成功判据修复

> 目标版本 v0.21.0（skill + worker 同发）。本 PRD 只做修复，不改 GraphInput/GraphOutput 契约、不恢复 Cython 编译。
> 实现时建议按 Task 粒度走 TDD（每个 Task 先写失败单测再实现），提交粒度：一个 Task 一个 commit。

## 1. 摘要

**背景（48 商品端到端实测，全部有 Ozon 后台实证）**

- 16 条 declined：13 条类目/类型错配（震动棒→残疾人辅助器具、BDSM分腿带→钻石画、折叠椅→折叠手推车、后视镜→单车裤、手机架/气嘴灯→鞋类装饰、匀蛋器→野营厨房 等）、2 条纯图片带文字、1 条属性类型错误。
- 2 条工具卡（汽修工具、修车躺板）被 Ozon 判 `BR_hazard_class1`：worker 把必填字典属性 9782（危险品等级）兜底填成了 `Категория 1. Взрывчатые вещества`（dictionary_value_id=970593901，Ozon 后台已实证）。
- 学习闭环把 declined 当成功：`learning_record_node` 在 `upload_status=="success"`（含 retry 循环 `imported` 即 success、不可修复标 success、pending 带 product_id 视为 success）时写入 `category_mapping` → L0 高置信复用错误类目，越用越偏。

**成功标准**

- S1：16 条 declined 中至少 10 条重跑后 approved（排除 2 条危化品打火机、纯图片类视修复范围）。
- S2：`category_mapping` 学习表只增 `moderate_status=approved` 的记录；存量脏数据可一键清理。
- S3：9782 永不被填成爆炸物/危险等级；漏必填走温和错误而非判死。
- S4：`bash scripts/ci.sh` + 全部既有单测（含 `test_ozon_category_fix.py`）通过。

## 2. 问题排序与处置

| 优先级 | 问题 | 现象/证据 | 处置 |
|---|---|---|---|
| P0-1 | 成功判据错误 → 学习闭环固化错误类目 | 皮鞭任务 13 阶段全过但无卡；declined 卡片仍写 category_mapping | 学习记录只认 `moderate_status=="approved"`；retry 循环去掉 3 处假 success |
| P0-2 | 9782「取第一个字典值」兜底 | 工具卡被填成爆炸物（Ozon 后台实证 970593901） | 危险属性安全默认/跳过；全局兜底改为「唯一值才填」 |
| P0-3 | 类目匹配字面匹配 + tie 无门槛 | 13 条类目错配，同批手链 7 对 1 错 | 同义词映射外置 + 置信门槛 + 用完整 source_category_path |
| P1-1 | skill 信封数据 | `draft.source_category` 只传末两级；尺寸缺失按重量估算 | 传完整路径；尺寸缺失标记 estimate 不硬传 |
| P1-2 | 图片带文字 | 5 张卡 4195 拒（2 条纯图片） | 生图 prompt 禁文字；原图过滤（可选） |
| P1-3 | 批量提交 429 | 23/49 首提失败 | batch_test 自动退避重试 |

## 3. 关键变更

### Task 1（P0-1）：学习记录/重试循环只认 approved

**文件**

- 修改：`worker/src/graphs/nodes/learning_record_node.py`（成功判据，约 L104-116）
- 修改：`worker/src/graphs/validation_retry_loop.py`（约 L2204、L2074、`should_reupload` pending 分支）
- 测试：新增 `worker/tests/test_learning_record_gate.py`

**行为**

1. `learning_record_node`：`ozon_upload_success` 只允许 `moderate_status == "approved"`；删除 `upload_status == "success"` 强制成功分支；`imported/active/processed` 不再视为成功。
2. `validation_retry_loop`：
   - `imported` 后 300s 审核轮询未到 `approved/declined` → `upload_status = "pending_moderation"`（不是 success），`should_reupload` 对 `pending_moderation` 返回 `exit` 且不写学习。
   - 不可修复路径不再设 `upload_status="success"`，改 `"rejected_unfixable"`，`is_valid` 仍置 True（不重试）但 `final_result` 保留错误消息。
3. 新增脏数据清理脚本：`worker/scripts/clean_category_mapping.py`（`--dry-run` 列出 success_count>0 但对应商品非 approved 的记录；`--apply` 置 is_active=false）。

**深挖补充（subagent/本地复现确认的完整假成功路径，全部要改）**

- `worker/src/graphs/nodes/learning_record_node.py` L72：`ozon_status in ("imported","active","approved","processed")` 一律算成功 → 收紧为仅 `approved`。
- 同文件 L74-82：`upload_status=="success"` 与 `state.ozon_upload_success` 两个强制成功分支 → 删除。
- `worker/src/graphs/validation_retry_loop.py` L2199：imported 即 `upload_status="success"`（300s 审核轮询未到 approved/declined 时保持 success）→ 未 approved 改 `pending_moderation`。
- 同文件 L2276：`should_reupload` 对 pending+有 product_id「视为成功」→ 改 `pending_moderation` 退出且不写学习。
- 同文件 L2073：unfixable 错误标 `upload_status="success"` → 改 `rejected_unfixable`。
- `worker/src/graphs/graph.py` L317-323：`should_handle_error` 对 pending/有 product_id 重试审核 3 次后「视为成功（后台继续审核）」→ 改为保留 `pending_moderation` 终态，任务完结但 learning 不写。
- `final_result`（validation_retry_loop.py L2305 附近）：`upload_status=="success"` 时清空 error_message → `rejected_unfixable/pending_moderation` 不清空。

**测试用例**

- mock moderation=`declined`、status=`variant_wait`、upload_status=`success` → 断言不写 learning_record。
- mock moderation=`approved` → 断言写入。
- mock imported+审核超时 → 断言 upload_status=`pending_moderation` 且 learning 不写。

### Task 2（P0-2）：9782 危险属性安全兜底 + 全局「唯一值才填」

**文件**

- 修改：`worker/src/graphs/nodes/assemble_ozon_product_node.py`（`_validate_and_enrich_items` 兜底段，L1795-1870；`KNOWN_DEFAULTS` L1740 附近）
- 修改：`worker/src/graphs/nodes/prepare_ozon_upload_node.py`（9782 校验同步）
- 修改：`worker/src/utils/attribute_utils.py`（新增 `is_hazard_attr()` + `get_safe_hazard_default()`）
- 测试：新增 `worker/tests/test_hazard_attr_fallback.py`

**行为**

1. `attribute_utils.py` 新增：
   - `HAZARD_DICT_ATTR_IDS = {9782}`
   - `HAZARD_SAFE_VALUE_KEYWORDS = ("не опас", "неопас", "без класса", "нет класса")`
   - `get_safe_hazard_default(dict_vals)`：在字典值里按关键词挑「非危险」值返回；找不到返回 `None`。
2. assemble 必填字典兜底顺序改为：标题搜 → 属性名搜 → **唯一值**（`len(dict_vals)==1` 且非危险属性）→ 跳过。
   - 危险属性（9782）：优先 `get_safe_hazard_default`，取不到则跳过（不写任何值），打 warning。
   - 删除「回退2：取第一个可用字典值」（对全部属性生效）。
3. prepare 对 9782 只透传已填安全值，禁止补首值。

**测试用例**

- 9782 字典首位是爆炸物、存在「не опасный груз」→ 断言选中安全值。
- 9782 字典无安全值 → 断言跳过、无 9782 属性。
- 普通属性多值无匹配 → 断言跳过（不取第一个）。

### Task 3（P0-3）：类目匹配语义化 + 置信门槛 + 完整路径

**文件**

- 修改：`worker/src/utils/ozon_category_query.py`（`_search_jieba_like` 评分/tie、新增阈值过滤）
- 修改：`worker/src/graphs/nodes/assemble_ozon_product_node.py`（`source_category` 读取优先 `source.source_category_path`；L1 候选置信判断）
- 新增：`worker/config/category_synonyms.json`（1688 词 → Ozon ZH 词映射表）
- 修改：`skill/scripts/cloud_probe.py`（`draft["source_category"]` 改传完整路径）
- 测试：新增 `worker/tests/test_category_match_v021.py`

**行为**

1. skill 信封：`draft.source_category = source_category_path`（完整，不再 `names[-2:]`）；`source.source_category_path` 保持。
2. worker 读取顺序：`source.source_category_path` → `draft.source_category` → 标题。
3. `category_synonyms.json` 首批条目（键=1688 词，值=Ozon ZH 词列表）：
   `震动棒→振动器`、`点烟器→打火机配件 点火器`、`折叠椅→折叠椅 户外椅`、`后视镜→后视镜 汽车后视镜 摩托车后视镜`、`手机架→手机支架`、`香薰石→香薰 家居香薰`、`匀蛋器→打蛋器 搅拌器`、`气嘴灯→自行车配件 轮胎灯`、`清洁套装→清洁套装 电脑清洁`、`拔取钳→拔取器 拆卸工具`、`分腿带→BDSM情趣设备 束缚`。
4. 置信门槛：top1 与 top2 分差 < 0.3，或 top1 命中 token 全为泛化词 → 不选（返回空触发 L3 LLM，L3 仍无把握则返回「需人工确认类目」错误，不硬猜）。
5. L0 学习缓存联动 Task 1：只读 approved 写入的记录；`get_category_mapping_by_leaf` 增加 `verified_success` 过滤。

**深挖补充（本地复现结论：错配主因是 L0 学习缓存，不是 L1 搜索）**

- 复现证据：按 `_search_jieba_like` 评分逻辑对真实类目树打分，`后视镜`案例 L1 第一名是「摩托车后视镜(2.3)」、`折叠椅`案例前几名是「户外折叠椅配件/折叠凳(2.3)」——L1 能选出正确类目；但实际选中「单车裤/折叠手推车（1.3）」→ **错配来自 L0 或 L3**。
- L0 门槛过松：`add_category_mapping` 默认 confidence=0.7、首次写入 success_count=1（local_db_manager.py L429-458）；`_match_category_layered` 门槛 success_count≥1 且 confidence≥0.6 → **一条错误记录即可让下一件同款商品高置信命中错类目**，且无 approved 校验、无 fail_count 惩罚。
- 修复补强：
  - L0 命中时与 L1 高分候选做一致性校验：若 L0 映射的 dc/tp 不在 L1 top-5（分差>0.3），降级用 L1 结果并给该映射 `fail_count+1`、confidence×0.5。
  - `add_category_mapping` 增加 `verified` 字段（仅 approved 写入时 true）；L0 只读 `verified=true`。
  - `fail_count>=3` 自动 `is_active=false`。
  - 首次写入 confidence 从 0.7 降到 0.5（需 L1 一致性确认才升 0.7）。

**测试用例**

- 震动棒信封（source=成人用品>女用器具>震动棒）→ 断言 dc/tp = 17028959/96513。
- 折叠椅信封 → 断言不是 94319（折叠手推车）。
- 后视镜信封 → 断言不是 785353054（单车裤）。
- 同义词表缺失词 → 触发 L3/人工分支而非硬猜。

### Task 4（P1-1）：skill 信封数据完整性

**文件**

- 修改：`skill/scripts/cloud_probe.py`（source_category 完整路径；尺寸估算标记）
- 测试：`skill/tests/test_envelope_fields.py`（首建 skill/tests）

**行为**

1. 尺寸缺失时不再静默估算：`draft["dimensions_estimated"]=true` + 保留估算值；worker 端对 estimated 尺寸在 pricing 前校验（超差拒绝，写明确错误）。
2. `draft.source_category` 全路径（与 Task 3 一致）。

### Task 5（P1-2）：生图禁文字

**文件**

- 修改：`worker/config/image_prompts.json`（10 个节点 prompt 统一追加「画面不得包含任何文字、水印、运费/价格/促销字样」）
- 测试：`worker/tests/test_image_prompts_config.py` 补断言

### Task 6（P1-3）：batch_test 429 退避

**文件**

- 修改：`skill/scripts/batch_test.py`（submit 失败 429 → 指数退避重试 3 次，间隔 30/60/120s）

## 4. 验收

- 单测：新增约 12-15 个用例全绿；`PYTHONPATH=src python3 -m pytest worker/tests/ -v` 全绿；`bash scripts/ci.sh` 绿。
- 端到端：本地 worker 重跑 8 个错配商品信封（震动棒×2、折叠椅、后视镜、气嘴灯、手机架、拔取钳、匀蛋器）→ 类目断言正确；云端重跑 8 条 → approved 率记录。
- 学习缓存：重跑后 `category_mapping` 无 declined 记录新增；`clean_category_mapping.py --dry-run` 列出旧脏数据。
- skill 包：`python3.12 compile.py` 成功；COS manifest 更新；`cli.py update` 自动更新链路正常。

## 5. 发版节奏

1. Task 1 → Task 2 → Task 3 → Task 4（P0 三件 + skill 信封）合入 dev，bump VERSION/skill/VERSION 到 0.21.0，更新 CHANGELOG。
2. 本地 worker 全量回归 + 8 商品类目断言通过后，打 `v0.21.0` tag → build-skill 自动构建/传 COS/Release。
3. 用户部署 worker tarball `ozon-worker-deploy-v0.21.0.tar.gz` → 云端重跑 8 商品验证 approved。
4. Task 5/6（P1）随 v0.21.x 或 v0.22 跟进，不阻塞 0.21.0。

## 6. 待 deep-dive 挂账（subagent 输出后回填）

- 类目错配中 L0/L1/L2/L3 各层占比（需 category_match_log 或本地复现）。
- `category_mapping` 存量脏数据量级（需云端 PG 查询或本地 DB 模拟）。
- `/v1/description-category/attribute/values` 对 9782 的返回排序规律（是否固定爆炸物在首位）。
- 9782 在多少类目为必填（可离线扫 category_cache JSON）。

**已确认（2026-08-04 本地复现 + 代码审计）**

- 类目错配主因：L0 学习缓存固化（不是 L1 字面搜索）；L1 对震动棒/折叠椅/后视镜案例都能给出合理 top 候选，但 L0 以 confidence=0.7/success_count=1 直接覆盖。仍需云端 `category_match_log` 的 match_layer 字段做最终占比确认。
- 假成功路径 6 处（见 Task 1 深挖补充），learning_record 只认 approved 是收敛点。
- 9782 爆炸物：`_validate_and_enrich_items` 回退2「取第一个可用字典值」（assemble_ozon_product_node.py L1854-1869）触发；`/values` 返回顺序待云端实测确认是否固定首位。
