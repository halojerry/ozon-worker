# 执行 PRD: Worker 优化(核实修正版)

> 版本: v1.0 | 日期: 2026-08-07 | 状态: **可执行**
> 来源: Codex `WORKER-OPTIMIZATION-PRD.md` 核实修正 —— 9 项假设逐一代码实证,2 项不实剔除,7 项保留。

---

## 0. 核实结论(9 项代码实证)

| 项 | PRD 声称 | 核实结果(文件:行) | 判定 |
|---|---|---|---|
| A1 | REPAIR_STRATEGY 缺 10 个错误码 | 缺 **9 个**(VALUE_MUST_BE_INTEGER/DECIMAL、ATTRIBUTE_VALUE_COUNT_EXCEEDED、EMPTY_REQUIRED_AFTER_WARNING_DELETING、SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT、warning_attribute_values_empty、erased_attribute_value、CONDITIONAL_ATTRIBUTE_ERROR、all_image_failed);现有 14 键 | ✅ **属实,执行** |
| A2 | DESCRIPTION_DECLINE 4 attr 需分策略 | **4180 已有**(retry_loop:1131 sanitize_title + 拉丁/西里尔检查 + 强制俄语翻译)、**4194/4195 已有**(489-497 warning 不阻断)、**8229 已有**(81/601 类目重匹配)、22508 已有中文处理(_chinese_re:694) | ❌ **不实,已实现** |
| A3 | 净化有漏网 | 描述已清拉丁/中文(prepare:370-380);**prepare 主路径无 _sanitize_title**(净化只在 retry_loop 修复路径) | ⚠️ **部分,执行 prepare 标题净化** |
| B1 | 辅助生图 max_retries 2→1 | 9 节点全 max_retries=2;辅助实为 **7 个**(PRD 写 6 是笔误) | ✅ **属实,执行** |
| B2 | 提示词无物流排除 | image_prompts.json grep 物流/退换/配送=0 | ✅ **属实,执行** |
| C1 | check_task_status 未注册 CLI | cli.py 无 query/task-status 命令 | ✅ **属实,执行** |
| C2 | final_result 无 notice | failed 时仅 upload_status+error_message(2168-2214) | ✅ **属实,执行** |
| D1 | draft_sanity 不拦 weight=0 | 只拦 >50kg(MAX_WEIGHT_G=50000)/单边>5m(MAX_DIM_MM=5000) | ✅ **属实,执行** |
| E1 | 无原始图兜底 | prepare:2305 明确不用 alicdn,无 _salvage_original_images | ✅ **属实,执行** |

**剔除**:A2(已实现)。**修正**:B1 辅助节点为 7 个;A3 范围收窄为「prepare 主路径标题净化」。

---

## 1. 可执行项(7 项,按优先级)

### P0-A1 补全 REPAIR_STRATEGY 未映射错误码(9 个)

**文件**: `worker/src/graphs/validation_retry_loop.py`

| 错误码 | 映射 | 修复类型 | 理由 |
|--------|------|----------|------|
| `VALUE_MUST_BE_INTEGER` | repair_prepare | attributes | 强制 int(value) 转换,LLM 改不了类型 |
| `VALUE_MUST_BE_DECIMAL` | repair_prepare | attributes | 强制 float(value) 转换 |
| `ATTRIBUTE_VALUE_COUNT_EXCEEDED` | repair_prepare | attributes | 删多值保留首个 |
| `EMPTY_REQUIRED_AFTER_WARNING_DELETING` | error_repair_llm | attributes | 需补值,LLM 可搜字典值 |
| `SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT` | unfixable | unfixable | 商品已在其他账号,不浪费重试 |
| `all_image_failed` | unfixable | unfixable | 需重新生图非 LLM 修 |
| `warning_attribute_values_empty` | error_repair_llm | attributes | 同 error_attribute_values_empty |
| `erased_attribute_value` | error_repair_llm | attributes | 属性值被擦除,需补值 |
| `CONDITIONAL_ATTRIBUTE_ERROR` | error_repair_llm | attributes | 条件属性,LLM 可尝试 |

**验收**: REPAIR_STRATEGY 覆盖 23 键(14+9);INTEGER/DECIMAL 走 repair_prepare 类型转换;unfixable 类不再 3 轮重试。

### P0-A3 prepare 主路径标题净化

**文件**: `worker/src/graphs/nodes/prepare_ozon_upload_node.py`

- 新增 `_sanitize_title()`(对齐 retry_loop/utils/title_sanitizer 逻辑):清 `[a-zA-Z]{1,}` 残留拉丁(单字母也清)、清 `[\u4e00-\u9fff]` 中文、清营销词;保留西里尔/数字/标点;≤80 字符
- prepare 组装 final_title 处调用,确保**主上传路径**(非修复路径)标题零拉丁/中文

**验收**: prepare 输出的 title 无拉丁/中文(正则断言);81+ 用例回归。

### P0-B1 辅助生图 max_retries 2→1(7 个)

**文件**: scene_1/2/3、detail、comparison、social_proof、multi_angle 共 7 个 `*_gen_node.py`

- 7 个辅助节点 `max_retries=2 → 1`;main/white_bg 保持 2
- 单商品最坏生图调用:45 → 34 次

**验收**: 7 节点 max_retries=1,main/white_bg=2(脚本断言)。

### P0-B2 生图提示词加物流排除

**文件**: `worker/config/image_prompts.json`

- scene/comparison/social_proof/detail 节点提示词追加:「禁止出现物流、配送、快递、退换货相关文字和图标」

**验收**: 4+ 节点提示词含物流排除指令。

### P1-C1 skill 暴露 query 子命令

**文件**: `skill/scripts/cli.py`、`skill/references/error-codes.md`、`output-schema.md`

- `cli.py` 加 `query --task-id <id>` → 调 `cloud_probe.check_task_status(task_id)` → 输出 `{status, progress:{stage,percent,stages_completed,stages_remaining}, result_json, error_message}`
- error-codes.md 进度查询口径同步(删「CLI 未暴露单任务查询」+ 维护者提示);output-schema.md 补 query 输出字段

**验收**: `python3 scripts/cli.py query --task-id <id>` 返回 13 阶段 progress;文档同步。

### P1-C2 失败任务 notice 字段

**文件**: `worker/src/graphs/validation_retry_loop.py`、`skill/references/output-schema.md`

- final_result 在 upload_status=failed/rejected_unfixable 时,result 补 `notice`(人类可读失败原因摘要,如「类目错配,已尝试修复 3 次失败:DESCRIPTION_DECLINE」)
- output-schema.md 写明 agent 检查到 failed/declined 主动告知用户

**验收**: failed 任务 task_status.result.notice 非空;文档明确 agent 主动通知。

### P1-D1 draft_sanity 拦 weight=0/dim 全零

**文件**: `worker/src/utils/draft_sanity.py`

- `weight == 0` → reasons.append("weight=0g(重量不能为 0)")
- `dimensions 全 0` → reasons.append("dimensions 全为 0(尺寸不能为 0)")
- 返回 INVALID_REQUEST 拦截

**验收**: weight=0/dim={0,0,0} 信封 submit_task 被拦截;正常值不误伤。

### P2-E1 原始图转存兜底(评估后决定)

**文件**: `worker/src/graphs/nodes/prepare_ozon_upload_node.py`

- AI 生图 < 3 张时触发 `_salvage_original_images()`:下载 alicdn 原始图 → 转存 COS → COS URL 补位(不替代 AI 图)
- ⚠️ 前置条件:Worker 需 COS 凭证(当前无!v0.25 禁竞品图是因为 Ozon 抓不到外部 URL;**COS 转存需要 S3 凭证配置**,worker 环境无)→ **本项需先决策 COS 凭证来源,否则暂缓**

---

## 2. 实施顺序与依赖

```
A1(独立) ──┐
A3(独立) ──┤ 可并行
B1(独立) ──┤
B2(独立) ──┤
C1(独立) ──┼── 全部独立, 按 P0→P1→P2 顺序执行
C2(独立) ──┤
D1(独立) ──┤
E1(需 COS 凭证决策) ── 暂缓
```

## 3. 验收总清单

- [ ] A1: REPAIR_STRATEGY 23 键;INTEGER/DECIMAL → repair_prepare
- [ ] A3: prepare 输出标题零拉丁/中文
- [ ] B1: 7 辅助节点 max_retries=1
- [ ] B2: 提示词含物流排除
- [ ] C1: query 命令可用 + 文档同步
- [ ] C2: failed 含 notice + 文档
- [ ] D1: weight=0/dim 全零拦截
- [ ] E1: 待 COS 凭证决策(暂缓)

## 4. 不做

- A2(DESCRIPTION_DECLINE 分策略)——已实现,不再重复
- 改动审计数据本身(317 商品/65 问题数据来自 Seller API 外部审计,非代码问题)
