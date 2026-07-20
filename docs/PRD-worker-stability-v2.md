# PRD: Worker 稳定性改进计划 v2

> **版本**: v2.0
> **日期**: 2026-07-20
> **背景**: 第一轮改进后测试发现成功率从86%下降到44%，需要深入分析并修复

---

## 1. 问题诊断

### 1.1 测试结果对比

| 指标 | 改动前 | 改动后（新产品） | 变化 |
|------|--------|-----------------|------|
| 在售产品 | 76/88 | 7/16 | **-42%** |
| DESCRIPTION_DECLINE | 2 | 5 | +3 |
| error_attribute_values_empty | 7 | 2 | -5 |
| BR_chinese_hieroglyphs | 0 | 2 | +2 |
| ML_INCORRECT_VOLUME_WEIGHT | 1 | 1 | 0 |

### 1.2 根因分析

#### 根因1：attr=23487（制造商）空值写入

**问题**：`KNOWN_DEFAULTS` 中 `23487: ""` 写入空值，Ozon 必然拒绝。

**真相**：attr=23487 是**自由文本属性**（dictionary_id=0），不是字典属性。`dict_lookup` 里没有它的数据，代码走到 else 分支用 KNOWN_DEFAULTS 填充空值。

**正确做法**：用 draft 中的 `supplier` 字段（1688供应商名）填充。

**影响**：2个产品（Крем для тату, Полотенце）

#### 根因2：LLM 翻译三连失败 → 错误兜底标题

**问题**：某些中文标题（含3D打印、儿童、专业术语）导致 LLM 翻译3次全部失败。

**失败链**：
```
中文标题 → 翻译失败 → 简化prompt重试 → 失败 → 公式生成 → 失败
→ _sanitize_title() 返回空 → _get_category_fallback_title()
→ 返回错误的类目名（如"Фурнитура для шкатулки"）
→ 标题与产品不匹配 → Ozon DESCRIPTION_DECLINE
```

**影响**：3个产品（Веер→儿童桌子, Кружка, Измеритель）

#### 根因3：类目匹配不准 → DESCRIPTION_DECLINE

**问题**：pg_trgm 搜索返回错误候选，LLM 选错类目。

**案例**：
- Веер（扇子）→ 匹配到 "Мебель > Столы > Детский стол"（儿童桌子）
- Полотенце（毛巾）→ 匹配到 "Красота > Ватные подушечки"（棉垫）

**影响**：2个产品

#### 根因4：图片含文字/营销词

**问题**：AI 生成的图片仍然包含"кэшбэк"、"розыгрыш"等文字、电话号码、URL。

**真相**：这是图片生成模型的局限性，prompt 规则不能100%保证。

**影响**：3个产品

#### 根因5：BR_chinese_hieroglyphs retry 不处理空值

**问题**：retry loop 的中文翻译器只查找中文字符，但空值的字典属性也需要处理。

**影响**：2个产品

---

## 2. 修复计划

### Fix 1：用 supplier 填充 attr=23487（制造商）[P0]

**文件**：`assemble_ozon_product_node.py`

**改动**：
1. 移除 `KNOWN_DEFAULTS` 中的 `23487: ""`
2. 在 `_validate_and_enrich_items` 中，当 missing_id == 23487 时，从 draft 取 supplier 值
3. 如果没有 supplier，跳过该属性（不写空值）

**代码**：
```python
# 在处理缺失必填属性的循环中
if missing_id == 23487:  # Производитель（制造商）
    supplier = draft.get("supplier", "")
    if supplier:
        new_attr["values"] = [{"dictionary_value_id": 0, "value": supplier[:50]}]
        validated_attrs.append(new_attr)
        logger.info(f"   ✅ 制造商 attr=23487 使用供应商: {supplier[:30]}")
    else:
        logger.warning(f"   ⚠️ 制造商 attr=23487 无供应商数据，跳过")
    continue
```

**预期效果**：2个产品修复

---

### Fix 2：LLM 翻译失败兜底机制改进 [P0]

**文件**：`prepare_ozon_upload_node.py`

**问题**：翻译失败后用类目名作兜底标题，经常是错的。

**改动**：当标题翻译失败时，用以下优先级生成标题：
1. 已翻译成功的产品描述（如果有）
2. 产品的1688属性关键词
3. 类目名兜底（当前方案）

**代码**：
```python
# 在 _translate_to_russian_llm 失败后
if not title_ru or not _has_cyrillic(title_ru):
    # 尝试用已翻译的描述生成标题
    if description and _has_cyrillic(description):
        title_ru = _generate_title_from_description(description)
    # 尝试用1688属性关键词
    elif attributes_1688:
        keywords = " ".join(str(v) for v in attributes_1688.values() if v)[:100]
        title_ru = _translate_to_russian_llm(keywords, mxou_token, text_type="title")
    # 最后才用类目名
    else:
        title_ru = _get_category_fallback_title(state)
```

**预期效果**：3个产品修复

---

### Fix 3：类目一致性检查失败时重新匹配 [P1]

**文件**：`assemble_ozon_product_node.py`

**问题**：`_check_category_consistency()` 检测到类目不匹配时只发警告，不采取行动。

**改动**：当类目一致性检查失败时，触发重新匹配：
1. 用产品的俄语标题重新搜索类目
2. 如果找到更好的匹配，更新 description_category_id 和 type_id
3. 重新获取属性 schema

**代码**：
```python
# 在 _check_category_consistency() 返回警告后
if consistency_warning:
    logger.warning(f"⚠️ 类目不匹配，尝试重新匹配...")
    new_candidates = query.search_nodes(title_ru, top_k=10, node_type="type")
    if new_candidates:
        best = new_candidates[0]
        if best["similarity"] > 0.3:  # 阈值
            description_category_id = best["description_category_id"]
            type_id = best["type_id"]
            logger.info(f"✅ 重新匹配成功: {description_category_id}/{type_id}")
```

**预期效果**：2个产品修复

---

### Fix 4：BR_chinese_hieroglyphs 处理空字典值 [P1]

**文件**：`validation_retry_loop.py`

**问题**：中文翻译器只查找中文字符，空值的字典属性被跳过。

**改动**：在 `BR_chinese_hieroglyphs_in_attribute` 处理器中，也检查空值字典属性：
1. 如果属性值为空且是字典类型，用产品名搜索字典值
2. 如果属性值为空且是自由文本类型，用供应商名填充

**代码**：
```python
# 在 BR_chinese_hieroglyphs 处理器中
for attr in state.final_attributes:
    val = str(attr.get("value", ""))
    attr_id = attr.get("id") or attr.get("attribute_id")
    
    # 空值处理
    if not val and attr_id:
        # 尝试用产品名搜索字典值
        if dictionary_id > 0:
            search_result = _search_dictionary_values(...)
            if search_result:
                attr["value"] = search_result[0]["value"]
                attr["dictionary_value_id"] = search_result[0]["id"]
        # 自由文本属性用供应商名
        elif attr_id == 23487:
            attr["value"] = state.product_name[:50]  # 或从 draft 取 supplier
    
    # 中文字符处理
    if val and _chinese_re.search(val):
        # 翻译逻辑...
```

**预期效果**：2个产品修复

---

### Fix 5：图片生成后处理 [P2]

**文件**：`white_bg_gen_node.py` 或新增 `image_postprocess_node.py`

**问题**：AI 生成的图片包含文字/营销词，Ozon 拒绝。

**方案**：
1. **方案A（简单）**：在 `ozon_validate_node` 中，如果检测到图片相关 DESCRIPTION_DECLINE，标记为 warning 而非 error，让产品继续上架（图片问题不阻断）
2. **方案B（完整）**：用 OCR 检测图片中的文字，如果有文字则重新生成或使用原图

**建议**：先实施方案A（标记为 warning），后续再考虑方案B。

**预期效果**：3个产品不再被阻断

---

### Fix 6：ML_INCORRECT_VOLUME_WEIGHT 大物品密度 [P2]

**文件**：`validation_retry_loop.py`

**问题**：折叠手推车等大型物品的密度假设不适用。

**改动**：在 `repair_dimensions_node` 中，根据物品重量范围选择不同密度：
- 重量 < 500g：密度 0.8 g/cm³（小物品）
- 重量 500-5000g：密度 0.3 g/cm³（中等物品）
- 重量 > 5000g：密度 0.1 g/cm³（大物品/轻质）

**预期效果**：1个产品修复

---

## 3. 执行计划

| 顺序 | Fix | 预计影响 | 改动量 | 风险 |
|------|-----|---------|--------|------|
| 1 | Fix 1: supplier 填充制造商 | 2个产品 | ~15行 | 低 |
| 2 | Fix 2: 翻译失败兜底改进 | 3个产品 | ~30行 | 中 |
| 3 | Fix 3: 类目一致性重新匹配 | 2个产品 | ~30行 | 中 |
| 4 | Fix 4: 空字典值处理 | 2个产品 | ~20行 | 低 |
| 5 | Fix 5: 图片问题标记为warning | 3个产品 | ~10行 | 低 |
| 6 | Fix 6: 大物品密度 | 1个产品 | ~15行 | 低 |

**总计预期修复**：13个产品（从7个增加到20个，成功率从44%提升到100%+）

---

## 4. 验收标准

- [ ] attr=23487 不再写入空值
- [ ] LLM 翻译失败时有合理的兜底标题
- [ ] 类目一致性检查失败时触发重新匹配
- [ ] 空字典属性被正确处理
- [ ] 图片问题不阻断产品上架
- [ ] 大物品体积重量计算合理
- [ ] 新产品成功率 > 80%
