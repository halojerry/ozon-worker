# Worker 优化 PRD — 基于 Ozon 三店审计数据

> **版本**：v1.0  
> **日期**：2026-08-07  
> **数据来源**：Ozon Seller API 三店商品审计（317 商品 / 65 问题 / 20.5% 问题率）+ validation_retry_loop.py REPAIR_STRATEGY 映射表 + 生图重试逻辑  
> **原则**：只 ADD 不 CUT——不降低任何体验前提下，让流程更可靠、更透明、更可控

---

## 0. 数据基础

### 三店审计结果

| 店铺 | 总商品 | 问题数 | 问题率 | 主要错误 |
|------|--------|--------|--------|----------|
| 主店铺 (4718259) | 236 | 48 | 20.3% | DESCRIPTION_DECLINE(27), BR_hazard_class1(8), VALUE_MUST_BE_DECIMAL(8) |
| 测试5381204 | 25 | 1 | 4.0% | double_without_merger_offer(1) |
| 测试5371047 | 56 | 16 | 28.6% | DESCRIPTION_DECLINE(8), ATTRIBUTE_VALUE_COUNT_EXCEEDED(6) |
| **合计** | **317** | **65** | **20.5%** | |

### 错误频次 TOP（三家店汇总）

| 错误码 | 次数 | REPAIR_STRATEGY 映射 | 修复类型 |
|--------|------|---------------------|----------|
| `DESCRIPTION_DECLINE` | 35 | ✅ 已映射 | product_import |
| `VALUE_MUST_BE_INTEGER` | 10 | ❌ **未映射** | ❌ 无 |
| `marking_auto_corrected` | 10 | ❌ 未映射（但 FIX_TYPE_ATTRIBUTES 有） | attributes |
| `BR_hazard_class1` | 8 | ✅ 已映射（unfixable） | unfixable |
| `VALUE_MUST_BE_DECIMAL` | 8 | ❌ **未映射** | ❌ 无 |
| `error_attribute_values_empty` | 8 | ✅ 已映射 | attributes |
| `ATTRIBUTE_VALUE_COUNT_EXCEEDED` | 8 | ❌ **未映射** | ❌ 无 |
| `EMPTY_REQUIRED_AFTER_WARNING_DELETING` | 4 | ❌ **未映射** | ❌ 无 |
| `BR_hashtag_validation` | 4 | ✅ 已映射 | attributes |
| `warning_attribute_values_out_of_range` | 4 | ✅ 已映射 | attributes |
| `SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT` | 4 | ❌ **未映射** | ❌ 无 |
| `ML_INCORRECT_VOLUME_WEIGHT` | 2 | ✅ 已映射 | product_import |
| `warning_attribute_values_empty` | 2 | ❌ 未映射 | ❌ 无 |
| `erased_attribute_value` | 2 | ❌ 未映射 | ❌ 无 |
| `CONDITIONAL_ATTRIBUTE_ERROR` | 1 | ❌ 未映射 | ❌ 无 |
| `double_without_merger_offer` | 1 | ✅ 已映射 | product_import |
| `primary_image_load_failed` | 1 | ✅ 已映射（unfixable） | unfixable |
| `pics_http_error` | 1 | ✅ 已映射（unfixable） | unfixable |
| `all_image_failed` | 1 | ❌ **未映射** | ❌ 无 |
| `warning_all_image_failed` | 1 | ✅ 已映射（unfixable） | unfixable |

**10 个未映射错误码，合计 50 次错误**——全部走默认 `error_repair_llm`（LLM 盲修），其中大部分 LLM 修不了。

### DESCRIPTION_DECLINE 细分（35 次，占比最高）

| attr_id | 错误原因 | 次数（估） | 根因 |
|---------|----------|-----------|------|
| 4180 | 标题含拉丁字母（"Название на латинице"） | ~12 | LLM 翻译漏了拉丁字符未清干净 |
| 22508 | 含中文/非法符号（"недопустимые символы и/или иероглифы"） | ~8 | 属性值含中文未清 |
| 4195 | 图片含物流/退换信息 | ~3 | AI 生图提示词含物流文案 |
| 8229 | 图片与类型不匹配 | ~2 | 类目+图不一致 |

---

## Phase A — 错误修复策略补全（P0，直接降低问题率）

### A1. 补全 REPAIR_STRATEGY 未映射错误码

**问题**：10 个错误码（合计 50 次错误）未在 `REPAIR_STRATEGY` 显式映射，全部走默认 `error_repair_llm`（LLM 盲修）。其中大部分 LLM 修不了，浪费 3 轮重试。

**改动**：在 `validation_retry_loop.py` 的 `REPAIR_STRATEGY` 和 `FIX_TYPE_*` 集合中补全：

| 错误码 | 建议映射 | 修复类型 | 理由 |
|--------|----------|----------|------|
| `VALUE_MUST_BE_INTEGER` | `repair_prepare` | attributes | 强制 `int(value)` 转换，LLM 改不了数据类型 |
| `VALUE_MUST_BE_DECIMAL` | `repair_prepare` | attributes | 强制 `float(value)` 转换 |
| `ATTRIBUTE_VALUE_COUNT_EXCEEDED` | `repair_prepare` | attributes | 删除多值只保留第一个，LLM 不知道删哪个 |
| `EMPTY_REQUIRED_AFTER_WARNING_DELETING` | `error_repair_llm` | attributes | 需要补值，LLM 可以搜索字典值 |
| `SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT` | `unfixable` | unfixable | 商品已在其他账号，不可修复，不浪费重试 |
| `all_image_failed` | `unfixable` | unfixable | 所有图片失败，需重新生图而非 LLM 修 |
| `warning_attribute_values_empty` | `error_repair_llm` | attributes | 同 error_attribute_values_empty |
| `erased_attribute_value` | `error_repair_llm` | attributes | 属性值被擦除，需补值 |
| `CONDITIONAL_ATTRIBUTE_ERROR` | `error_repair_llm` | attributes | 条件属性错误，LLM 可尝试 |

**涉及文件**：`worker/src/graphs/validation_retry_loop.py`  
**验收标准**：`REPAIR_STRATEGY` 覆盖审计发现的全部 20 个错误码；`VALUE_MUST_BE_INTEGER/DECIMAL` 走 `repair_prepare` 强制类型转换

### A2. DESCRIPTION_DECLINE 针对性修复

**问题**：35 次 DESCRIPTION_DECLINE 是最大错误来源。当前统一走 `error_repair_llm`，但 4 个 attr_id 根因完全不同，需要针对性修复。

**改动**：在 `error_repair_llm_node` 的 DESCRIPTION_DECLINE 处理分支中，按 attr_id 分策略：

| attr_id | 当前处理 | 建议改进 |
|---------|----------|----------|
| 4180（标题拉丁字母） | LLM 重写标题 | 增加 `_strip_latin()` 后处理——LLM 返回后强制正则清除 `[a-zA-Z]` 字符（保留数字和标点） |
| 22508（中文/非法符号） | LLM 重写 | 增加 `_strip_chinese()` 后处理——强制正则清除 `[\u4e00-\u9fff]` 字符 |
| 4195（图片含物流信息） | LLM 重写描述 | 这个修不了描述——需要标记为"重新生图"（当前无此策略，见 A3） |
| 8229（图与类型不匹配） | 触发类目重匹配 | ✅ 当前已处理（validation_retry_loop.py:601） |

**涉及文件**：`worker/src/graphs/validation_retry_loop.py`（error_repair_llm 函数）  
**验收标准**：attr=4180/22508 的 DESCRIPTION_DECLINE 修复后必须通过 `_strip_latin()`/`_strip_chinese()` 后处理，不依赖 LLM 自觉

### A3. 标题/属性拉丁字符和中文的源头拦截

**问题**：DESCRIPTION_DECLINE attr=4180/22508 的根因是 LLM 翻译后残留拉丁字符或中文。当前在 `prepare_ozon_upload_node.py` 的 `_sanitize_title()` 和 `_sanitize_description()` 中有净化，但显然有漏网。

**改动**：在 `prepare_ozon_upload_node.py` 的 `_sanitize_title()` 和 `_sanitize_description()` 中加强正则：
- 标题：强制清除所有 `[a-zA-Z]`（除非是品牌名白名单）
- 描述：强制清除所有 `[\u4e00-\u9fff]`（中文）
- 属性值：在 `assemble_ozon_product_node.py` 的属性映射后，强制扫描所有值清除中文

**涉及文件**：`worker/src/graphs/nodes/prepare_ozon_upload_node.py`、`worker/src/graphs/nodes/assemble_ozon_product_node.py`  
**验收标准**：提交到 Ozon 的标题/描述/属性值中，拉丁字符（标题）和中文（全字段）出现率为 0

---

## Phase B — 生图重试策略调整（P0，费用控制）

### B1. 辅助生图节点 max_retries 2→1

**问题**：所有 9 个生图节点 max_retries=2。但业务策略是主图+白底图可多次重试，其他辅助图只重试 1 次。

**改动**：

| 节点 | 当前 max_retries | 改为 | 理由 |
|------|-----------------|------|------|
| `main_image_gen_node.py:86` | 2 | 2（不变） | 主图，多次重试 |
| `white_bg_gen_node.py:107` | 2 | 2（不变） | 白底图，多次重试 |
| `scene_1_gen_node.py:76` | 2 | **1** | 辅助图，只重试 1 次 |
| `scene_2_gen_node.py:76` | 2 | **1** | 辅助图，只重试 1 次 |
| `scene_3_gen_node.py:76` | 2 | **1** | 辅助图，只重试 1 次 |
| `detail_gen_node.py:75` | 2 | **1** | 辅助图，只重试 1 次 |
| `comparison_gen_node.py:77` | 2 | **1** | 辅助图，只重试 1 次 |
| `social_proof_gen_node.py:77` | 2 | **1** | 辅助图，只重试 1 次 |
| `multi_angle_gen_node.py:97` | 2 | **1** | 辅助图，只重试 1 次 |

**涉及文件**：6 个 `*_gen_node.py`  
**验收标准**：6 个辅助节点 `max_retries=1`；单商品最坏生图调用从 45 次降到 34 次

### B2. 生图提示词排除物流/退换文案（配合 A2）

**问题**：DESCRIPTION_DECLINE attr=4195 是因为 AI 生成的图片含物流/退换信息。`config/image_prompts.json` 的提示词需要明确排除这类内容。

**改动**：检查 `config/image_prompts.json` 各节点提示词，在 scene/comparison/social_proof 等节点追加"禁止出现物流、配送、退换货、快递相关文字和图标"的约束。

**涉及文件**：`worker/config/image_prompts.json`  
**验收标准**：生图提示词含明确的物流/退换文案排除指令

---

## Phase C — 进度可见性 + 失败通知（P1，透明性）

### C1. Skill 端暴露进度查询 CLI

**问题**：Worker `/task_status/{task_id}` 返回 13 阶段 progress，但 skill 的 `check_task_status()` 未注册为 CLI 子命令。用户提交后 10-20 分钟黑盒。

**改动**：
- `skill/scripts/cli.py` 加 `query --task-id <id>` 子命令，调用 `cloud_probe.check_task_status(task_id)`
- 返回 `{status, progress: {stage, percent, stages_completed, stages_remaining}, result_json, error_message}`
- skill 文档（error-codes.md 进度查询口径 + output-schema.md）同步更新

**涉及文件**：`skill/scripts/cli.py`、`skill/references/error-codes.md`、`skill/references/output-schema.md`  
**验收标准**：agent 可执行 `python3 scripts/cli.py query --task-id <id>` 查询单任务进度，返回 stage/percent/stages_remaining

### C2. 失败商品通知

**问题**：`validation_retry_loop.py` 的 `final_result` 在 3 次重试失败后只返回 `{upload_status: "failed", error_message: "..."}`。单任务提交时 agent 不知道失败。

**改动**：
- Worker 端：`final_result` 在 `upload_status=failed` 时，在 result 的 `notice` 字段写入人类可读的失败原因摘要
- Skill 端：`check_task_status` 返回值已有 `error_message`，在 output-schema.md 明确写"agent 检查到 failed/declined 时主动告知用户"

**涉及文件**：`worker/src/graphs/validation_retry_loop.py`、`skill/references/output-schema.md`  
**验收标准**：failed 任务的 task_status 含 `notice` 字段；skill 文档明确 agent 主动检查失败并通知用户

---

## Phase D — 提交预校验增强（P1，fail-fast）

### D1. submit_task 校验 weight=0 / dimensions 全零

**问题**：`draft_sanity.py` 只校验 `weight > 50kg` 和 `dim > 5m`，不拦 `weight = 0` 或 `dimensions = {0,0,0}`。到 pricing 节点才报错，用户白等 5-10 分钟。

**改动**：在 `validate_draft_sanity()` 中增加：
```python
if weight == 0:
    reasons.append("weight=0g（重量不能为 0）")
for key in ("length", "width", "height"):
    if (dimensions or {}).get(key, 0) == 0:
        all_zero = True  # 标记
if all_zero:
    reasons.append("dimensions 全为 0（尺寸不能为 0）")
```

**涉及文件**：`worker/src/utils/draft_sanity.py`  
**验收标准**：`weight=0` 或 `dimensions={0,0,0}` 的信封在 submit_task 阶段被拦截，返回 INVALID_REQUEST

---

## Phase E — 原始图转存兜底（P2，可靠性）

### E1. AI 生图全失败时用原始图转存补位

**问题**：`prepare_ozon_upload_node.py:2305` 写"所有 AI 生成图均失败，不使用 alicdn 原始图（Ozon 无法下载）"。极端情况下 0 图被 Ozon 拒。

**改动**：在 `prepare_ozon_upload_node.py` 加 `_salvage_original_images()` 函数：
- 当 AI 生图 < 3 张时触发
- 下载 `original_images` 中的 alicdn 图 → 上传到 COS → 用 COS URL 补位
- 不替代 AI 图，只补到最低可上架数量

**涉及文件**：`worker/src/graphs/nodes/prepare_ozon_upload_node.py`  
**验收标准**：AI 生图 < 3 张时触发原始图转存兜底；转存后的 COS URL 可被 Ozon 访问

---

## 实施优先级

| 优先级 | 项 | 预期收益 | 改动量 |
|--------|---|----------|--------|
| **P0** | A1. 补全 REPAIR_STRATEGY（10 个未映射错误码） | 50 次错误不再走 LLM 盲修 | 小 |
| **P0** | A2. DESCRIPTION_DECLINE 针对性修复 | 35 次最大错误源针对性处理 | 中 |
| **P0** | A3. 标题/属性拉丁+中文源头拦截 | 从源头减少 DESCRIPTION_DECLINE | 小 |
| **P0** | B1. 辅助生图 max_retries 2→1 | 单商品省 11 次 API 调用 | 小 |
| **P0** | B2. 生图提示词排除物流文案 | 减少 attr=4195 被拒 | 小 |
| **P1** | C1. Skill 暴露进度查询 CLI | 打破 10-20min 黑盒 | 小 |
| **P1** | C2. 失败商品通知 | agent 主动告知用户失败 | 小 |
| **P1** | D1. submit 预校验 weight=0 | fail-fast，省 5-10min 白等 | 小 |
| **P2** | E1. 原始图转存兜底 | 防 0 图被拒 | 中 |

### 依赖关系

```
A1 ─┐
A2 ─┤── 互相独立，可并行
A3 ─┘
B1 ── 独立
B2 ── 独立（配合 A2 的 attr=4195）
C1 ── 依赖 skill 端改动
C2 ── 依赖 worker + skill 协同
D1 ── 独立
E1 ── 独立
```

### 验收 Checklist

- [ ] A1: REPAIR_STRATEGY 覆盖全部 20 个审计错误码
- [ ] A2: DESCRIPTION_DECLINE 按 attr_id 分策略（4180/22508 有后处理，4195 标记重新生图）
- [ ] A3: 标题无拉丁字符、属性无中文（正则后处理强制）
- [ ] B1: 6 个辅助生图节点 max_retries=1
- [ ] B2: image_prompts.json 含物流排除指令
- [ ] C1: `python3 scripts/cli.py query --task-id <id>` 可用
- [ ] C2: failed 任务含 notice 字段；skill 文档写明 agent 主动检查
- [ ] D1: weight=0/dimensions=0 在 submit_task 被拦截
- [ ] E1: AI 图 <3 张时原始图转存触发
