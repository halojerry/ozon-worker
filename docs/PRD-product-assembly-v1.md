# Ozon Worker 商品组装优化 PRD

> 版本: v1.0 | 日期: 2026-07-16 | 分支: `feat/unified-product-assembly`

---

## 一、背景与目标

### 1.1 背景

Ozon Worker 已完成统一商品组装架构重构（`assemble_ozon_product_node` 替代原 4 节点管线），首个 E2E 测试产品（修枝剪）成功上架 Ozon（product_id: 5524655390），审核通过。

### 1.2 目标

在已验证的架构基础上，修复测试中发现的 5 个问题，确保：
- 属性值语义正确（标签、品牌、描述不混淆）
- 字典值覆盖率 100%（PG 缓存 + Ozon API 回退）
- LLM 输出被充分利用（不重复翻译/组装）
- 自学习 source_value 准确

---

## 二、当前状态分析

### 2.1 已验证通过

| 环节 | 状态 | 说明 |
|------|------|------|
| 类目搜索 | ✅ | jieba 分词 → pg_trgm/ILIKE → top-15 候选 |
| 类目匹配 | ✅ | LLM 选最佳 + 自动修正 description_category_id |
| 属性 Schema 获取 | ✅ | PG 缓存优先, Ozon API 回退 (34 attrs) |
| LLM 完整组装 | ✅ | 生成 `/v3/product/import` items JSON |
| 上传 | ✅ | 成功创建商品, product_id=5524655390 |
| 审核 | ✅ | moderate_status=approved |
| 自学习记录 | ✅ | `learning_record_node.py` bug 已修复, 6 条记录写入 PG |
| 日志输出 | ✅ | 完整的属性匹配对照表 |

### 2.2 测试产品数据

| 字段 | 值 |
|------|-----|
| 1688 ID | 671383066553 |
| 中文标题 | 不锈钢园林园艺修枝剪 修果树粗枝花艺修花专用家用修剪树枝剪刀 |
| 俄语标题 | Секатор из нержавеющей стали, для сада |
| Ozon Product ID | 5524655390 |
| 类目 | 17028746/92777 (住宅和花园 > 园艺工具 > 修枝剪) |
| 必填属性 | 3 个 (8229, 9048, 85) |
| 属性总数 | 8 个 (最终 payload) |

---

## 三、问题分析

### 问题 1: Hashtag #23171 值错误 🔴 高

**现象**:
- 当前值: `"Нет бренда"` (dict_id=126745801)
- Ozon 报错: `BR_hashtag_validation` — 每个标签应以 `#` 开头, 用空格分隔
- 正确值应为: `#секатор #садовый #инструмент #обрезка`

**根因**:
`_validate_and_enrich_items()` 中品牌修正逻辑未区分属性 ID:
```python
# 品牌（85, 5076, 23171）
for brand_id in BRAND_ATTRIBUTE_IDS:  # ← 23171 被当作品牌
    brand_attr = next((a for a in validated_attrs if int(a.get("id", 0)) == brand_id), None)
    if brand_attr:
        values = brand_attr.get("values", [])
        for v in values:
            if v.get("dictionary_value_id", 0) == 0:
                v["dictionary_value_id"] = NO_BRAND_DICT_ID  # ← 错误: 给标签赋品牌值
                v["value"] = NO_BRAND_VALUE
```

**修复方案**:
`BRAND_ATTRIBUTE_IDS` 从 `[85, 5076, 23171]` 改为 `[85, 5076]`，移除 23171。

对于 23171，添加独立的标签生成逻辑：从产品标题提取核心词，生成 3-5 个俄语 hashtag。

### 问题 2: 字典值 PG 缓存命中 0% 🔴 高

**现象**:
- 11 个字典属性, PG 缓存命中: 0 个
- `get_dictionary_values()` 首次运行时返回 None
- `summarized_dict` 传递给 LLM 时为空
- LLM 在无字典参考下"猜测" dictionary_value_id

**根因**:
`assemble_ozon_product_node` Step 3:
```python
values = query.get_dictionary_values(attr_id, description_category_id, type_id)
if values and isinstance(values, list) and len(values) > 0:
    dict_lookup[attr_id] = values
# ← 如果返回 None，不加任何处理，直接跳过
```
没有 Ozon API 回退逻辑。

**修复方案**:
当 PG 缓存未命中时，调用 Ozon API `/v1/description-category/attribute/values` 获取字典值，并写入缓存：
```python
if not values:
    # Ozon API 回退
    values = _fetch_dict_values_from_ozon(attr_id, description_category_id, type_id, ...)
    if values:
        # 写入 PG 缓存
        cache_dict_values(...)
        dict_lookup[attr_id] = values
```

### 问题 3: LLM 生成 description (4191) 被 prepare_ozon_upload 跳过 🟡 中

**现象**:
- LLM 生成: `"Профессиональный садовый секатор из нержавеющей стали. Острые лезвия обеспечивают чистый срез..."`
- `prepare_ozon_upload` 日志: `4191 ⏭️ 跳过`
- 最终产品可能缺少 description

**根因**:
`prepare_ozon_upload` 对 `dictionary_value_id <= 0` 的字典属性一律跳过:
```python
if dict_id > 0 and dictionary_value_id <= 0:
    continue  # ← 跳过，不加入 payload
```
4191 (简介) 是 `dictionary_id=0` 的自由文本属性，但 `prepare_ozon_upload` 的判断逻辑可能误将其跳过。

**修复方案**:
检查 `prepare_ozon_upload` 的属性处理逻辑，确保 `dictionary_id=0` 的自由文本属性（4191, 4180）不会被错误跳过。自由文本属性应始终保留 LLM 生成的值。

### 问题 4: LLM 完整 payload 未被下游使用 🟡 中

**现象**:
- `assemble_ozon_product` 返回 `ozon_payloads: [{"items": items}]`（完整 JSON）
- `prepare_ozon_upload` 从零重建 payload: 标题翻译、属性组装、尺寸补全等
- LLM 的标题、描述、属性格式等优秀输出被丢弃

**根因**:
架构设计遗留问题：`prepare_ozon_upload` 最初设计为"从 state 碎片组装 payload"，与新的"LLM 一次输出完整 JSON"模式不兼容。

**修复方案**（本次 PRD 范围内不处理）:
这是架构级优化，需要修改 `prepare_ozon_upload` 的 ~1500 行代码。
- 短期: 确保 `assemble_ozon_product` 正确设置 `final_attributes`, `description_category_id`, `type_id` 等 state 字段
- 中期: 在 `prepare_ozon_upload` 开头检查 `ozon_payloads`，如果非空则直接使用 LLM 输出（仅补全图片）
- 标记为 **V2 优化项**

### 问题 5: source_value 映射不准确 🟢 低

**现象**:
- 学习记录中 source_value 多为 `[属性名]` (如 `[类型]`, `[品牌]`)
- 实际 1688 数据中有真实值，但匹配逻辑未找到

**根因**:
`learning_record_node.py` 的 `source_value` 提取逻辑:
```python
if not source_value:
    source_value = f"[{attribute_name or 'unknown'}]"  # ← 兜底填充
```
1688 的属性 key 与 Ozon 属性名不匹配，导致找不到对应的中文源值。

**修复方案**（本次 PRD 范围内不处理）:
改进 `source_value` 匹配策略：
1. 优先用 attribute_name 在 draft.attributes 中查找匹配 key
2. 用 value 字符串在 draft 数据中搜索源文本
3. 标记为 **V2 优化项**

---

## 四、解决方案

### 4.1 本次修复范围（P0）

| # | 问题 | 变更文件 | 预计行数 |
|---|------|---------|---------|
| 1 | Hashtag 值错误 | `assemble_ozon_product_node.py` | ~10 行 |
| 2 | 字典值 API 回退 | `assemble_ozon_product_node.py` | ~40 行 |
| 3 | Description 4191 未保留 | `assemble_ozon_product_node.py` + 检查 `prepare_ozon_upload_node.py` | ~10 行 |

### 4.2 变更不在本次范围（V2）

| # | 问题 | 原因 |
|---|------|------|
| 4 | LLM payload 未被使用 | 需要重构 `prepare_ozon_upload` (~1500 行) |
| 5 | source_value 映射 | 需要改进 `learning_record_node` 匹配逻辑 |

### 4.3 修复后回归测试

使用相同产品 (671383066553) 重新上架，验证:
1. Hashtag 23171 值为 `#секатор #садовый #инструмент` 格式
2. 字典值 PG 缓存命中 > 0
3. Description 4191 值不为空
4. Ozon 无 `BR_hashtag_validation` 错误
5. 产品审核通过

---

## 五、实施计划

| 步骤 | 内容 | 时间估计 |
|------|------|---------|
| 1 | 修复 23171 hashtag 逻辑 | 5 min |
| 2 | 添加字典值 Ozon API 回退 | 15 min |
| 3 | 修复 4191 description 保留 | 5 min |
| 4 | Docker rebuild | 2 min |
| 5 | E2E 回归测试 | 5 min |
| 6 | 对比新旧结果, 确认修复效果 | 3 min |

---

## 六、验收标准

- [ ] Hashtag #23171 不为 "Нет бренда"，格式为 `#word1 #word2 #word3`
- [ ] Ozon 无 `BR_hashtag_validation` 错误
- [ ] 字典值 PG 缓存命中 ≥ 1
- [ ] Description 4191 在 Ozon 产品上有值
- [ ] 产品审核通过
- [ ] 自学习记录正确写入
