---
title: Worker 拓扑与错误处理手册
purpose: 13 阶段节点拓扑、错误映射与数据流（改代码快速参考）
applies-version: ">=v0.74.0"
last-updated: 2026-09-11
owner: worker-pipeline
depends: [API-OVERVIEW, DB-SCHEMA-AUDIT]
status: active
---

# Worker 拓扑与错误处理手册

> **用途**：快速定位错误根因、知道改哪个文件、理解数据流向
> **更新日期**：2026-09-11 / 增量摘要覆盖至 v0.74.0（正文按 v0.27 口径，增量为准）

---

## v0.28–v0.74 拓扑变更摘要（增量，勿重写正文）

> 下方正文按 v0.27 口径撰写，仍可用；本节列 v0.28 以来影响拓扑/错误面的关键增量（符号均已在代码中核对）。
> ⚠️ **规矩**：增量条目 ≥8 条（或跨 ≥3 个 minor）时必须重写正文并清空增量节（docs/audit/2026-09-11-repo-gov/A1-doc-governance.md §7）。

- **v0.64 视觉模型切换**：`call_mxou_chat_api` 加 `image_urls` 参数（`worker/src/utils/mxou_api.py:129`，Vision ≤4 张）；类目 LLM 匹配 / 属性多候选消歧 / `_infer_attrs_from_vision`（`prepare_ozon_upload_node.py:1319`）带图，assemble/prepare 等节点已接入。
- **v0.65 promo_price → min_price**：CREATE 单确认新建后经 `ozon_status_node` 轮询 import/info 到手真实 product_id，`try_set_min_price_floor`（`ozon_upload_node.py:80`，`ozon_status_node.py:260` 调用）补送 `/v1/product/import/prices`（防御 ≥售价50% 且 ≤售价）。
- **v0.66 L0 学习表复活**：assemble 输出 `category_match_meta`（match_layer/confidence/dc/tp）主图↔子图双向透传（`state.py:90/858/890`）；`LearningRecordInput` 补 source/envelope/product_id/user_id/ozon_client_id/ozon_api_key/pricing_info（`state.py:900`）——**langgraph 按节点 Input model 过滤 channel，节点/路由要读的字段必须声明进 Input**。
- **v0.68 `decline_errors` 累积器**：retry 子图任何消费 `state.errors` 的节点先 `_accumulate_decline_errors`（`validation_retry_loop.py:197`，append-only cap50）；R2b 仲裁池 `_build_r2b_confirm_pool`（`assemble_ozon_product_node.py:732`，top10+跨大类 overlap 必进 cap12）；GraphOutput 透传 `description_category_id/type_id/category_match_meta/final_weight_g/final_dims_mm`（`state.py:259-263`，output_schema 按名过滤）。
- **v0.69 终态口径与上传前防线**：completed 必须过 `_has_real_product_evidence`（`utils/task_processor.py:73`，product_id 空/等于 import task_id → failed）；`route_after_assemble` 改消费 `failed_stage` 通道（`graph.py:207`，修复 `or 1.0` 吞 0.0 置信度缺陷）；数值属性清洗 `utils/attr_numeric_sanitize`（prepare/validate/retry 三处唯一入口）+ 尺寸 `OZON_DIM_BOUNDS_MM` clamp（`utils/weight_dimension_normalizer.py:47`，42-400/25-400/5-200）；CREATE 前 `find_product_by_offer`（`utils/ozon_client.py:227`，`ozon_upload_node.py:53`）查到尸体 offer 转 UPDATE（UPSERT_BY_OFFER）。
- **v0.63.1 凭证端点校验失败 500→422**（REST HTTP 层，不在下方错误映射表内）：`routes/credentials_routes.py:65-77` 捕获 pydantic.ValidationError → 可读 detail。
- **v0.70 门禁与取证**：manual 树校验失败显式阻断 `_blocked_exit`（`assemble_ozon_product_node.py:802`，统一 failed_stage=category_match，`route_after_assemble` 据此终止）；任务取证只读端点 `GET /api/v1/forensics/task/{task_id}`（`main.py:2564` + `services/forensics_service.py`，任务快照+listing_result_log+双审计一站式）。
- **v0.71 值数出口闸 + 类目真值 + 采集箱懒加载**（随 v0.72.0 发版）：`utils/attr_value_sanitize.py` 唯一入口 `cap_attribute_values` 按 Ozon schema `max_value_count` 截断属性 values（8229 多值拒单根治；prepare 载荷出口/retry 合并与重发等四处接线，`ATTRIBUTE_VALUE_COUNT_EXCEEDED` 不再误判走 R4）；discover 类目真值（面包屑 web_category_id）进信封；采集箱类目/属性表单改交互版懒加载（`services/category_schema_service.py`：缓存优先→未命中回源一次→回写 30d→失败降级）。
- **v0.72 字典值缓存三桶策略**：`utils/dict_value_cache.py` 唯一入口——global（哨兵键 (attr,0,0,language) 一份）/ scoped（类目绑定）/ ephemeral（首页巨型字典不物化，运行时 /values/search）；读侧 scoped 未命中自动回退全局桶；warm 首页探测不翻页 + df<5G 守卫中止（40G 盘撑爆事故根治）。
- **v0.73 四链修复**（生产取证驱动）：错误报告/取证/类目四端点租户判定改 `resolve_tenant`（生产任务租户是 Supabase user_id，勿回哈希租户）；本地预检独立错误码 `LOCAL_TITLE_CATEGORY_MISMATCH`（拦截即入箱不重传，勿按关键词并回中文分支）；体积重量守卫 `utils/volume_weight_guard.py` **只兜底不拒绝**（MIN_DENSITY_G_CC=0.40）；价差守卫 ≥10× block（仅 discovery_meta 有锚时生效）。
- **v0.74 数据池贡献闭环 + sku_metrics_pool**：用户贡献式销量数据池（选品卡片数据完整度对标上品帮）+ `sku_metrics_pool` 表（sku 池写入 SAVEPOINT 原子化）；同车 shopbang-parity 三批（采集箱 notes / 选品 4 键 discovery_meta / `ozon_sessions` 会话代管 AES-GCM）与 W1-W8 部署修复（缓存导出换 `--export-from-pg` + `warm_dead_nodes` 永久跳过）。

---

## 1. 节点拓扑（主图，v0.27 与 `graph.py` 逐行核对）

> ⚠️ 更新日期：2026-08-05。相比 v0.11 旧版：新增 `auth → check_quota` 早期配额检查、
> 跟卖/直采双分支、`follow_sell_import → pricing` 汇合、删除 `multi_info_gen`。

```
ENTRY
  │
  ▼
auth ──→(失败)→ END
  │通过
  ▼
check_quota ──(quota blocked)→ END
  │通过
  ├──(follow_sell=true)──▶ follow_sell_import ──(ozon_product_id为空/类目解析失败)→ END / validation_retry_wrapper
  │                         │正常
  │                         ▼
  └──(1688 直采)─────────▶ ingest
                            │
                            ▼
                        pricing ──([PRICING_FAILED])→ END
                            │成功
                            ▼
                  assemble_ozon_product ──(类目匹配失败 / conf<0.3)→ END
                            │成功
                            ▼
                  scene_generation_llm
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
       white_bg_gen                 multi_angle_gen        ← Phase1（并行）
              │                           │
              └─────────────┬─────────────┘
                            ▼
        ┌──────────┬────────┼─────────┬─────────┬─────────┬─────────┐
        ▼          ▼        ▼         ▼         ▼         ▼         ▼
  detail_gen  social_  comparison scene_1  scene_2  scene_3  variant_primary_loop
              proof_gen            gen      gen      gen     (多SKU)  main_image_gen(单SKU)
        └──────────┴────────┴─────────┴─────────┴─────────┴─────────┘   ← Phase2（并行）
                            │
                            ▼
                  prepare_ozon_upload
                            │
                            ▼
                     ozon_validate ──(失败)→ validation_retry_wrapper
                            │通过
                            ▼
                      ozon_upload
                            │
                            ▼
                      ozon_status ──(pending, ≤3次)──▶ 自身重试
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
        (approved)                 (error / 未知)
              │                           │
              ▼                           ▼
      learning_record          validation_retry_wrapper
              │                           │(success/pending)
              ▼                           ▼
             END                    learning_record → END
                                     (失败 → END)
```

### 条件分支

| 分支点 | 条件 | 成功路径 | 失败路径 |
|--------|------|---------|---------|
| `route_after_auth` | `error_code == AUTH_SUCCESS` | `check_quota` | `END` |
| `route_after_early_quota` | 无 `[QUOTA_BLOCKED]` | 按 sell_type → `follow_sell_import` / `ingest` | `END` |
| `route_after_follow_sell_import` | `ozon_product_id` 非空且类目解析成功 | `pricing` | `END` / `validation_retry_wrapper` |
| `route_after_pricing` | 无 `[PRICING_FAILED]` | `assemble_ozon_product` | `END` |
| `route_after_assemble` | 类目匹配成功且 `conf ≥ 0.3` | `scene_generation_llm` | `END` |
| `should_upload_after_validate` | `is_valid=True` 且无错误 | `ozon_upload` | `validation_retry_wrapper` |
| `should_handle_error` | `moderation_status=approved`（或 imported+success+product_id 齐备兜底） | `learning_record` | `validation_retry_wrapper`（pending ≤3 次回自身） |
| `should_learn_after_repair` | `upload_status in (success, pending)` | `learning_record` | `END` |

---

## 2. 修复循环子图

```
ENTRY: parse_error
  │
  ▼
parse_error ──→ classify_error ──→ repair_node_selector（条件分支）
                                       │
                    ┌──────────────────┼──────────────────┬──────────────────┐
                    ▼                  ▼                  ▼                  ▼
            error_repair_llm    repair_prepare     repair_pricing    repair_dimensions
                    │                  │                  │                  │
                    └──────────────────┴──────────────────┴──────────────────┘
                                          │
                                          ▼
                                      revalidate
                                          │
                              ┌───────────┼───────────┐
                              ▼           ▼           ▼
                          [success]   [重试]       [退出]
                              │           │           │
                              ▼           ▼           ▼
                          reupload    parse_error   final_result → END
                              │       (回到循环)
                              ▼
                         recheck_status
                              │
                  ┌───────────┼───────────┐
                  ▼           ▼           ▼
              [success]    [重试]       [退出]
                  │           │           │
                  ▼           ▼           ▼
              final_result  parse_error  final_result → END
```

最大重试次数：3

---

## 3. 错误处理映射表

| 错误码 | 检测节点 | 处理节点 | 修复策略 | 关键代码 |
|--------|---------|---------|---------|---------|
| `DESCRIPTION_DECLINE` | ozon_status | classify_error → error_repair_llm | LLM重写描述。attr=8229: 换type_id；attr=4194/4195: 标记warning不阻断 | `validation_retry_loop.py:400` |
| `error_attribute_values_empty` | ozon_status | classify_error → error_repair_llm | 用产品名搜索字典值，LLM兜底 | `validation_retry_loop.py:450` |
| `BR_chinese_hieroglyphs_in_attribute` | ozon_status | classify_error → error_repair_llm | 批量扫描翻译所有含中文的属性值 | `validation_retry_loop.py:507` |
| `BR_warning_wrong_country` | ozon_status | classify_error → error_repair_llm | LLM修正原产国 | `validation_retry_loop.py:450` |
| `ML_INCORRECT_VOLUME_WEIGHT` | ozon_status | classify_error → repair_dimensions | 自适应密度重算尺寸（0.8/0.3/0.1） | `validation_retry_loop.py:1008` |
| `INCORRECT_DIMENSION` | ozon_status | classify_error → repair_dimensions | 同上 | `validation_retry_loop.py:1008` |
| `warning_attribute_values_out_of_range` | ozon_status | classify_error → error_repair_llm | 强制刷新字典缓存 + API搜索 | `validation_retry_loop.py:626` |
| `BR_hashtag_validation` | ozon_status | classify_error → error_repair_llm | LLM生成合规hashtag | `validation_retry_loop.py:450` |
| `BR_hashtag_brand` | ozon_status | classify_error → error_repair_llm | LLM修复品牌相关hashtag | `validation_retry_loop.py:450` |
| `double_without_merger_offer` | ozon_status | classify_error → repair_prepare | 给9048追加`_v{count}`后缀 | `validation_retry_loop.py:916` |
| `INVALID_PRICE` | ozon_status | classify_error → repair_pricing | 从pricing_info读取价格 | `validation_retry_loop.py:974` |
| `WEIGHT_DIMENSION_ERROR` | ozon_status | classify_error → repair_prepare | 确保重量/尺寸>0 | `validation_retry_loop.py:908` |
| `VARIANT_NOT_MERGED` | ozon_status | classify_error → repair_prepare | 重建payload确保颜色/9048正确 | `validation_retry_loop.py:908` |
| `PRODUCT_ALREADY_EXISTS` | ozon_status | classify_error | **不可修复**，终止循环 | `validation_retry_loop.py:408` |

### 未显式映射的错误（走默认 error_repair_llm）

- `warning_all_image_failed`
- `marking_auto_corrected`

---

## 4. 数据流映射

### title（中文 → 俄语）

| 阶段 | 位置 | 动作 |
|------|------|------|
| 创建 | `auth_node` 从 `envelope.draft.title` | 原始中文标题 |
| 转换 | `assemble_ozon_product_node` | LLM翻译中文→俄语，生成≤50字符标题 |
| 校验 | `ozon_validate_node:179` | 检查拉丁/中文字符 |
| 净化 | `_sanitize_title()` | 强制≤50字符、加标点、防关键词堆砌 |
| 修复 | `error_repair_llm_node:806` | LLM返回`corrected_title` |

### description（中文 → 俄语）

| 阶段 | 位置 | 动作 |
|------|------|------|
| 创建 | `auth_node` 从 `envelope.draft.description` | 原始中文描述 |
| 转换 | `prepare_ozon_upload_node` | LLM翻译 + `_sanitize_description()`净化 |
| 校验 | `ozon_validate_node:192` | 检查拉丁/中文字符 |
| 修复 | `error_repair_llm_node:848` | LLM返回`corrected_description` |

### attributes（1688 → Ozon格式）

| 阶段 | 位置 | 动作 |
|------|------|------|
| 创建 | `auth_node` 从 `envelope.draft.attributes` | 中文属性名→值 |
| Schema | `assemble_ozon_product_node` | 查询Ozon API获取类目属性schema + 字典值 |
| 转换 | `assemble_ozon_product_node` | LLM映射中文属性→Ozon属性ID + dictionary_value_id |
| 存储 | `GlobalState.final_attributes` | `[{id, value, dictionary_value_id}]` |
| 校验 | `ozon_validate_node:131` | 检查字典属性的dictionary_value_id |
| 转换 | `revalidate_node:1158` | 转为Ozon API格式 `{complex_id, id, values: [...]}` |
| 修复 | `error_repair_llm_node` | API搜索 + LLM兜底 |

**关键属性ID**：
| ID | 含义 | 类型 | 默认值 |
|----|------|------|--------|
| 85/5076 | 品牌 | 字典(28732849) | "Нет бренда"(126745801) |
| 4389 | 原产国 | 字典(1935) | "Китай"(90296) |
| 8229 | 类型 | 字典(1960) | — |
| 9048 | 变体绑定名 | 自由文本 | offer_id |
| 4191 | 描述 | 自由文本 | — |
| 4180 | 关键词 | 自由文本 | — |
| 23171 | hashtag | 自由文本 | 自动生成 |
| 23487 | 制造商 | 自由文本 | draft.supplier |
| 23536 | 标记码 | — | Ozon自动设置（跳过） |
| 9782 | 危险品等级 | 字典(26026952) | API搜索 |
| 10096-10099 | 颜色 | 字典(1494) | 变体特定 |

### images（原始 → AI生成）

| 阶段 | 位置 | 动作 |
|------|------|------|
| 创建 | `auth_node` 从 `envelope.draft.images[]` | 1688原始图片URL |
| Phase1 | `white_bg_gen` + `multi_angle_gen`（并行） | 白底图 + 多角度图 |
| Phase2 | 7个并行节点 | 营销图（场景/详情/对比/社交证明等） |
| 排序 | `prepare_ozon_upload_node` | 按IMG_ORDER排列 |
| 校验 | `ozon_upload_node` | 上传到Ozon |

**图片顺序**：main_image → detail → scene_1/2/3 → comparison → social_proof → multi_angle → white_bg

### dimensions/weight

| 阶段 | 位置 | 动作 |
|------|------|------|
| 创建 | `envelope.draft.dimensions{length,width,height}`(mm), `draft.weight`(克) | Skill层已转换 |
| 校验 | `ozon_validate_node:114` | 自动设置weight_unit=g, dimension_unit=mm |
| 修复 | `repair_prepare_node:908` | 默认值: weight=500g, 尺寸=200mm |
| 重算 | `repair_dimensions_node:1008` | 自适应密度重算 |

### price

| 阶段 | 位置 | 动作 |
|------|------|------|
| 创建 | `envelope.draft.purchase_cost`(CNY) | 1688采购成本 |
| 计算 | `pricing_node` | 查物流费率 + 加价 + 汇率转换 |
| 存储 | `GlobalState.pricing_info["final_price"]` | 最终价格(RUB) |
| 修复 | `repair_pricing_node:974` | price=final_price, old_price=1.2x, min_price=0.9x |

### category（类目匹配）

| 阶段 | 位置 | 动作 |
|------|------|------|
| 搜索 | `assemble_ozon_product_node` | pg_trgm搜索 → top-15候选 → LLM选择 |
| 校验 | `assemble_ozon_product_node` | `_check_category_consistency()` 一致性检查 |
| 重匹配 | `assemble_ozon_product_node` | 一致性失败时用俄语标题重新搜索 |
| 修复 | `error_repair_llm_node:476` | attr=8229时换type_id |

---

## 5. 改代码时的快速参考

### 遇到某类错误，改哪里？

| 想改什么 | 改哪个文件 | 改哪个函数/区域 |
|---------|-----------|---------------|
| 标题翻译规则 | `prepare_ozon_upload_node.py` | `_translate_to_russian_llm()` |
| 标题净化规则 | `prepare_ozon_upload_node.py` | `_sanitize_title()` |
| 描述翻译规则 | `prepare_ozon_upload_node.py` | `_translate_to_russian_llm()` text_type="description" |
| 描述净化规则 | `prepare_ozon_upload_node.py` | `_sanitize_description()` |
| 品牌默认值 | `assemble_ozon_product_node.py` | `KNOWN_DEFAULTS` + 品牌修正逻辑 |
| 制造商默认值 | `assemble_ozon_product_node.py` | attr=23487 特殊处理 |
| 类目匹配逻辑 | `assemble_ozon_product_node.py` | `_llm_match_category()` + `_extract_keywords()` |
| 类目匹配prompt | `config/category_match_v2_cfg.json` | sp字段 |
| 同义词映射 | `assemble_ozon_product_node.py` | `_CN_SYNONYMS` dict |
| 字典值搜索 | `validation_retry_loop.py` | `_search_dictionary_values()` |
| 属性翻译 | `validation_retry_loop.py` | `BR_chinese_hieroglyphs` handler |
| 体积重量修复 | `validation_retry_loop.py` | `repair_dimensions_node()` |
| 价格修复 | `validation_retry_loop.py` | `repair_pricing_node()` |
| 图片生成prompt | `white_bg_gen_node.py` / `scene_*_gen_node.py` | system_prompt |
| 新增错误处理 | `validation_retry_loop.py` | `REPAIR_STRATEGY` dict + 新handler |
| 验证规则 | `ozon_validate_node.py` | `ozon_validate_node()` |

### 新增错误处理的步骤

1. 在 `REPAIR_STRATEGY` 中添加错误码→修复节点映射
2. 如果需要新的修复逻辑，在 `error_repair_llm_node` 中添加特殊处理分支
3. 如果需要新的修复节点，创建函数并注册到 `create_validation_retry_loop()`
4. 在本手册的错误映射表中添加记录

---

## 6. 已知问题与待改进

| 问题 | 影响 | 状态 |
|------|------|------|
| `warning_all_image_failed` 未显式映射 | 走默认LLM修复，可能无效 | 待改进 |
| `marking_auto_corrected` 未显式映射 | 走默认LLM修复 | 待改进 |
| LLM翻译对专业术语（3D打印、儿童用品）失败率高 | 导致标题翻译三连失败 | 已改进兜底机制 |
| pg_trgm阈值0.05可能引入噪声候选 | 类目匹配可能选错 | 已加LLM领域消歧规则 |
| 图片生成模型不能100%保证无文字 | DESCRIPTION_DECLINE(attr=4194/4195) | 已标记为warning不阻断 |

---

## 7. 任务终态与重提交（v0.38）

### 7.1 状态机

```
pending → running → completed          （审核通过，成功）
                   → rejected          （Ozon 审核拒绝，终态，可重提）
                   → failed            （执行失败，终态，可重提）
                   → cancelled         （用户取消，终态，不可重提）
```

- **`rejected`**（v0.38 N2）：`ozon_status` 判定 `moderate_status` 被拒 / 不可修复
  时，`task_processor.py:453-483` 置 `status='rejected'` + `completed_at`。
  `task_status` 端点对 rejected 返回归位进度（stage=rejected, 100%）并提示重提。
- **`failed`**：重试耗尽或不可恢复错误（`task_processor.py:403-451`）。
- **成功判据**（v0.21）：仅 `moderate_status=="approved"` 记成功；`pending`+product_id
  不算成功（防假成功）。

### 7.2 重提交端点（N2）

`POST /api/v1/resubmit_task/{task_id}`（旧路径同）：仅 `rejected`/`failed` 可重提，
复制原载荷 + `parent_task_id` + `image_regen=True` 重新入队。
**v0.38.1 起需鉴权**：请求体 `token` 必须与任务 `tenant_id` 归属一致（跨租户返回 404）。

### 7.3 SKU 去重（N1）

- `sku_key = {user_id}[:{ozon_client_id}]:{product_id}`（v0.38.1 起含店铺维度，
  修复同用户两店铺同款误拦）。
- 提交层 `_find_existing_task` 只拦活跃任务（`pending`/`running`）；
  终态行（`rejected`/`failed`/`completed`/`cancelled`）不拦截。
- DB 唯一索引 `uq_ozon_product_tasks_tenant_sku` 谓词
  `WHERE sku_key IS NOT NULL AND status IN ('pending','running')`——
  **v0.38.1 修复**（旧版无状态过滤，resubmit 同 sku_key 插新行撞唯一索引 → 500）。
  ⚠️ 升级需在 PG 执行 DROP 旧索引 + 重建（`init_data.py` 已幂等处理）。

### 7.4 终态 webhook（N4-w）

Worker 配置 `TASK_NOTIFY_URL`（Server酱等）后，终态 POST
`{task_id, status, product_summary, error_message, product_id, ozon_client_id}`。
skill `--notify` 传 `payload.notify=true`。实现：`task_processor._send_task_notify` +
`_send_task_notify_async`（v0.38.1 起 `asyncio.to_thread` 包装，防阻塞事件循环）。

### 7.5 改代码快速参考

| 需求 | 文件 |
|------|------|
| 改去重逻辑 | `main.py:_find_existing_task` / `submit_task` / `resubmit_task` |
| 改终态判定 | `task_processor.py` 终态分支（failed/rejected/completed） |
| 改重提载荷 | `main.py:http_resubmit_task` |
| 改 webhook | `task_processor.py:_send_task_notify` |
