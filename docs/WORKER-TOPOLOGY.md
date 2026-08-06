# Worker 拓扑与错误处理手册

> **用途**：快速定位错误根因、知道改哪个文件、理解数据流向
> **更新日期**：2026-07-20

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
