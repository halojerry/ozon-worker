# PRD: Worker 稳定性改进计划 v4

> **版本**: v4.0
> **日期**: 2026-07-21
> **测试数据**: 36个产品上架，11个可售（30.6%），25个有问题

---

## 1. 测试结果总览

| 状态 | 数量 | 比例 |
|------|------|------|
| ✅ Готов к продаже + Продается | 11 | 30.6% |
| ⏳ mod=approved 待激活 | 10 | — |
| 🔴 mod=declined | 4 | — |
| 🟡 mod=空（有错误） | 11 | — |

---

## 2. 错误根因分析

### 2.1 #1 类目匹配严重跑偏 — 导致 DESCRIPTION_DECLINE

**影响**: 7个产品被分配到了完全错误的类目，Ozon拒绝。

| 产品 | 实际用途 | Worker分配的类目 |
|------|---------|---------------|
| 汽修组套工具 | 汽车修理工具 | **家具 > 搬运工具套装** |
| 电动车后视镜 | 摩托车配件 | **儿童用品 > 儿童汽车配件** |
| 修车躺板 | 汽车维修 | **家具 > 搬运工具套装** |
| 自行车震动灯 | 车载配件 | **家具 > 家具轮子** |
| 打火机 | 日用品 | **露营取暖器** |
| 铝合金方向盘塞 | 汽车改装 | **家具** |
| F7 USB | 电子产品 | **家具** |

**根因**:
1. pg_trgm 搜索返回了大量噪声候选（阈值降到 0.05 后副作用）
2. LLM 在噪声候选中选择时没有足够的领域消歧信号
3. Worker 日志已警告"跨类目一致性：无共同关键词"，但仍照常上传

**代码位置**: `assemble_ozon_product_node.py` — `_check_category_consistency()` + `_llm_match_category()`

---

### 2.2 #2 INCORRECT_DIMENSION — 尺寸/重量错误

**影响**: 7个产品。

**根因（二重叠加）**:

**a) 重量 10x 乘数过于激进**
- 密度校验发现低于 1.293 kg/m³ 时自动 ×10
- 对于手链(30g)、手机支架(15g)等轻小物品，变成 300g/150g
- Ozon ML 标记为"与同类商品严重不符"

**b) 默认尺寸 200×200×200mm/500g 不合理**
- `repair_prepare_node` 用统一默认值
- 手链实际 ~50×50×10mm/30g，被设为 200mm正方体/500g

**c) cm→mm 阈值 200 引入新问题？**
- 需要验证：推车等大物品修正后是否正常，小物品是否被误判

**代码位置**: 
- `prepare_ozon_upload_node.py` — 密度校验 + 重量乘数
- `validation_retry_loop.py` — `repair_prepare_node` 默认值

---

### 2.3 #3 COS 图片间歇性失败 — 非阻断

**影响**: 11次 `pics_http_error`（**全部 WARNING 级别，不阻断销售**）

**根因**: 跨境网络不稳定。Ozon 俄罗斯服务器从广州 COS 下载图片，部分成功，部分超时。

**关键发现**: 
- 有图片错误的产品**仍可售**（如 Браслет、Мягкая игрушка）
- 成功下载的图片被 Ozon 转存到 `ir.ozone.ru`，之后稳定
- 这是**间歇性网络问题**，不是代码 bug

**代码位置**: `prepare_ozon_upload_node.py` — 假设"COS URL Ozon可正常访问"

---

### 2.4 #4 打火机管制品 — 不修复

**影响**: 2个打火机，被 Ozon 识别为火险品。

**决定**: 不修复。Skill 层不做过滤——用户自己避免选打火机类产品。

---

## 3. 修复计划

### Fix 1: 类目匹配 — 上传前拦截不一致匹配 [P0]

**目标**: 类目一致性检查失败时拒绝上传，而非照常发出。

**方案**:
1. 修改 `_check_category_consistency()` 返回值已被调用方使用（已做）
2. 当一致性失败时，**不要回退到错误类目**，而是：
   - 用俄语标题重新 pg_trgm 搜索类目（已做，但效果有限）
   - 若仍不匹配，将类目匹配范围扩大到所有类目（不限 top-15）
   - 若仍失败，标记 `needs_manual_review` 并跳过上传（不让错误类目的产品上架）

**改动量**: ~30行
**文件**: `assemble_ozon_product_node.py`

---

### Fix 2: 移除重量 10x 乘数 [P0]

**目标**: 不再因密度低而盲目乘10。

**方案**:
1. 完全移除密度校验中的 10x 重量乘数逻辑
2. 改为：密度低于 1.0 kg/m³ 时，记录 WARNING 日志，但**保持原值不变**
3. 信任 1688 源数据——如果数据本身就是错的（如 weight=1表示1kg），用已有的单位检测修复

**改动量**: ~15行
**文件**: `prepare_ozon_upload_node.py`

---

### Fix 3: 自适应默认尺寸 [P1]

**目标**: 轻小物品不用 200mm/500g 默认值。

**方案**:
1. 根据产品重量反推合理默认尺寸（已有 `repair_dimensions_node` 的逻辑）
2. 当维度缺失时，用密度 0.8 g/cm³ 计算默认尺寸（而非硬编码 200mm）
3. 例如：30g 物品 → 默认尺寸约 35mm（而非 200mm）

**改动量**: ~20行
**文件**: `validation_retry_loop.py` — `repair_prepare_node`

---

### Fix 4: COS 图片重试机制 [P2]

**目标**: 减少 `pics_http_error` 发生概率。

**方案**:
1. 上传前检查：curl COS URL 确认可访问
2. 不可访问的图片用 S3 预签名 URL 替换
3. 或：在 prepare_ozon_upload 阶段将所有 COS URL 预转为 S3 URL

**改动量**: ~40行
**文件**: `prepare_ozon_upload_node.py` + `image_url_processor.py`

---

### Fix 5: 补充未映射错误码 [P2]

**目标**: 不浪费 retry 循环。

**方案**: 在 `REPAIR_STRATEGY` 中添加不处理或 unfixable 标记：
```python
"pics_http_error": None,  # WARNING级，不触发retry
"pics_cant_decode": None,
"primary_image_load_failed": None,
"some_image_failed": None,
"warning_all_image_failed": None,
"BR_hazard_class1": "unfixable",
"FB_fire_hazardous_goods": "unfixable",
"FB_LIGHTER": "unfixable",
"INCORRECT_DENSITY": "repair_prepare",
```

**改动量**: ~10行
**文件**: `validation_retry_loop.py`

---

## 4. 执行优先级

| 顺序 | Fix | 影响 | 预期效果 | 改动量 |
|------|-----|------|---------|--------|
| **1** | Fix 1: 类目一致性拦截 | 7个产品 | 不再将工具上传到家具类目 | ~30行 |
| **2** | Fix 2: 移除10x乘数 | 7个产品 | 轻小物品不再300g/200mm | ~15行 |
| **3** | Fix 3: 自适应默认尺寸 | 辅助 | 减少 INCORRECT_DIMENSION | ~20行 |
| **4** | Fix 5: 补充错误码映射 | 辅助 | 不浪费retry循环 | ~10行 |
| **5** | Fix 4: COS图片重试 | 减少warning | 减少 pics_http_error | ~40行 |

**总预期效果**: 成功率从 30.6% → 55%+

---

## 5. 验收标准

- [ ] 类目一致性检查失败时跳过上传（不发送到 Ozon）
- [ ] 不合理的密度校验不再自动乘10
- [ ] 错误码映射表覆盖今天所有出现的错误类型
- [ ] 修复后重新测试 10+ 产品，成功率 > 50%
