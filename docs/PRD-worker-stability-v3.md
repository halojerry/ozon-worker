# PRD: Worker 稳定性改进计划 v3

> **版本**: v3.0
> **日期**: 2026-07-20
> **背景**: 第二轮改进后成功率从44%提升到58%，仍有5个产品失败

---

## 1. 问题诊断

### 1.1 测试结果

| 阶段 | 产品数 | 成功 | 成功率 |
|------|--------|------|--------|
| 第一批（改动前） | 16 | 7 | 44% |
| 第二批（重新提交） | 12 | 7 | 58% |
| **总计** | **28** | **14** | **50%** |

### 1.2 失败产品分析

| # | 产品 | 错误码 | 根因 | 类型 |
|---|------|--------|------|------|
| 1 | Туристическая лампа | error_attribute_values_empty (9782) | 9782在SKIP_ATTR_IDS中被跳过 | 代码bug |
| 2 | Бейсболка с колонками | INCORRECT_DIMENSION | 类目不匹配(要求2kg) + REPAIR_STRATEGY缺映射 | 数据+代码 |
| 3 | Ручная тележка | ML_INCORRECT_VOLUME_WEIGHT | weight=1被当作1g而非1kg + REPAIR_STRATEGY缺映射 | 数据+代码 |
| 4 | Питьевая система | DESCRIPTION_DECLINE (4195) | AI图片含物流/配送文字 | 图片质量 |
| 5 | Ватные подушечки | BR_warning_wrong_country (22508) | attr=22508未处理(只硬编码4389) | 代码缺失 |

---

## 2. 修复方案

### Fix 1: 移除9782从SKIP_ATTR_IDS [P0]

**问题**: attr=9782（危险品等级）是Ozon必填属性，但代码在两处将其跳过：
- `prepare_ozon_upload_node.py:971` — `_skip_attrs = (9782, 23536)`
- `validation_retry_loop.py:841` — `SKIP_ATTR_IDS: set = {9782, 23536}`

**修复**:
```python
# prepare_ozon_upload_node.py:971
_skip_attrs = (23536,)  # 移除9782

# validation_retry_loop.py:841
SKIP_ATTR_IDS: set = {23536}  # 移除9782
```

**预期效果**: 修复1个产品（Туристическая лампа），预防所有需要危险品等级的类目

**文件**: `prepare_ozon_upload_node.py`, `validation_retry_loop.py`

---

### Fix 2: 添加REPAIR_STRATEGY缺失映射 [P0]

**问题**: 两个错误码没有在REPAIR_STRATEGY中映射：
- `INCORRECT_DIMENSION` — 应该映射到 `repair_prepare`
- `ML_INCORRECT_VOLUME_WEIGHT` — 已映射到 `repair_dimensions`，但代码中有一处写成了 `repair_prepare`

**修复**:
```python
# validation_retry_loop.py REPAIR_STRATEGY
"INCORRECT_DIMENSION": "repair_prepare",  # 新增
"ML_INCORRECT_VOLUME_WEIGHT": "repair_dimensions",  # 确认正确
```

**预期效果**: retry loop能正确处理这两个错误码

**文件**: `validation_retry_loop.py`

---

### Fix 3: 添加attr=22508（品牌注册国）处理 [P1]

**问题**: 代码只硬编码了attr=4389（原产国）为"Китай"，但attr=22508（品牌注册国）没有处理。

**修复**: 在`prepare_ozon_upload_node.py`中添加22508的处理：
```python
# 在4389硬编码附近添加
BRAND_COUNTRY_ATTR_ID = 22508
# 如果final_attributes中没有22508，添加默认值"Китай"
```

**预期效果**: 修复1个产品（Ватные подушечки）

**文件**: `prepare_ozon_upload_node.py`

---

### Fix 4: 改进重量单位检测 [P1]

**问题**: `weight_raw=1`（无小数点）被当作1g，但折叠手推车应该是3-5kg。

**当前逻辑**:
```python
if weight_raw and isinstance(weight_raw, str) and '.' in str(weight_raw):
    weight_g = int(float(weight_raw) * 1000)  # kg -> g
else:
    weight_g = int(float(weight_raw)) if weight_raw else 0
```

**修复**: 添加启发式规则：
```python
# 如果weight_raw <= 5 且产品名含"车/cart/推车/拉杆/折叠"，假设单位是kg
if weight_g <= 5 and any(kw in title_cn for kw in ['车', 'cart', '推车', '拉杆', '折叠', '折叠']):
    weight_g = weight_g * 1000
    logger.warning(f"⚠️ 重量{weight_raw}过小，假设单位为kg，转换为{weight_g}g")
```

**预期效果**: 修复1个产品（Ручная тележка），预防类似的大物品重量问题

**文件**: `prepare_ozon_upload_node.py`

---

### Fix 5: 图片问题特殊处理 [P2]

**问题**: `DESCRIPTION_DECLINE + attr=4195`（图片含物流/配送文字）无法通过retry修复。

**修复**: 在`error_repair_llm_node`中添加特殊处理：
```python
if error_code == "DESCRIPTION_DECLINE" and attr_id == 4195:
    # 图片问题无法通过retry修复，标记为warning
    state.is_valid = True
    state.upload_status = "success_with_warning"
    return state
```

**预期效果**: 图片问题不再阻断产品上架（标记为warning）

**文件**: `validation_retry_loop.py`

---

### Fix 6: 图片生成prompt添加反物流文字规则 [P2]

**问题**: AI生成的图片包含物流/配送/退货文字，导致DESCRIPTION_DECLINE。

**修复**: 在所有图片生成prompt中添加：
```
严格禁止包含以下内容：
- 物流/配送信息（发货时间、运费、快递方式）
- 退货/退款政策
- 促销/优惠信息
- 联系方式（电话、微信、网址）
```

**预期效果**: 预防未来图片质量问题

**文件**: `main_image_gen_node.py`, `scene_*_gen_node.py`, `white_bg_gen_node.py`

---

## 3. 执行计划

| 顺序 | Fix | 影响 | 改动量 | 风险 |
|------|-----|------|--------|------|
| 1 | Fix 1: 移除9782从SKIP_ATTR_IDS | 1个产品 + 预防 | 2行 | 低 |
| 2 | Fix 2: 添加REPAIR_STRATEGY映射 | 2个产品 | 3行 | 低 |
| 3 | Fix 3: 添加attr=22508处理 | 1个产品 | 15行 | 低 |
| 4 | Fix 4: 改进重量单位检测 | 1个产品 + 预防 | 20行 | 中 |
| 5 | Fix 5: 图片问题特殊处理 | 1个产品 | 10行 | 低 |
| 6 | Fix 6: 图片生成反物流规则 | 预防 | 30行 | 低 |

**总计预期修复**: 5个产品（从14/28提升到19/28 = 68%）

---

## 4. 验收标准

- [ ] attr=9782不再被SKIP_ATTR_IDS过滤
- [ ] INCORRECT_DIMENSION在REPAIR_STRATEGY中有映射
- [ ] attr=22508（品牌注册国）被正确处理
- [ ] 大物品(如推车)的重量单位检测正确
- [ ] 图片问题不阻断产品上架
- [ ] 图片生成prompt包含反物流文字规则
- [ ] 新产品成功率 > 65%
