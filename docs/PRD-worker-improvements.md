# PRD: Worker Pipeline 质量改进计划

> **版本**: v1.0
> **日期**: 2026-07-20
> **目标**: 修复当前10个阻断性产品错误，提升未来上架成功率

---

## 1. 背景与现状

### 1.1 当前产品状态

| 状态 | 数量 | 说明 |
|------|------|------|
| ✅ 正常在售 | 66 | 无错误 |
| ⚠️ 在售但有 warning | 7 | 非阻断，可选修复 |
| 🔴 不出售 | 10 | 阻断性错误，必须修复 |
| 🟡 审核中 | 1 | 等待审核 |
| ❌ 从未上传 | 40 | 本次不处理 |

### 1.2 错误分布

| 错误码 | 数量 | 影响 | 根因 |
|--------|------|------|------|
| `error_attribute_values_empty` | 7 | 阻断 | 变量名 bug + 字典值空值写入 |
| `DESCRIPTION_DECLINE` | 2 | 阻断 | 描述翻译无内容净化 |
| `ML_INCORRECT_VOLUME_WEIGHT` | 1 | 阻断 | repair 只调尺寸不调重量 |
| `BR_hashtag_validation` | 2 | 在售 | hashtag 格式问题 |
| `warning_attribute_values_out_of_range` | 3 | 在售 | 属性值超范围 |
| 品牌错误 | 全局 | 潜在风险 | 品牌未强制为"无品牌" |
| 类目匹配不准 | 全局 | 潜在风险 | pg_trgm 阈值 + ILIKE 兜底 + LLM 偏差 |

---

## 2. 问题详细分析

### 2.1 [P0] 字典属性空值写入 — `error_attribute_values_empty`

**影响产品**: 7个火柴类产品（类目 17027930/431787897）

**根因链**:
1. `assemble_ozon_product_node.py:1034` 把 `description_category_id` 写成 `category_id`
2. 标题搜索字典值时 `NameError`，被 `except Exception` 静默吞掉
3. 降级到取字典第一个值或写入 `{"dictionary_value_id": 0, "value": ""}` 空值
4. Ozon 拒绝空值属性 → `error_attribute_values_empty`

**修复方案**:
- Fix 变量名 `category_id` → `description_category_id`
- 字典值三级回退：标题搜索 → 属性名搜索 → 取第一条 → 仍无值则跳过（不写空值）

**验证**: 重新提交7个火柴类产品，检查是否通过审核

---

### 2.2 [P0] 描述翻译无内容净化 — `DESCRIPTION_DECLINE`

**影响产品**: 2个（狗牵引绳 + 首饰配件）

**根因链**:
1. `prepare_ozon_upload_node.py:263-268` 描述翻译 prompt 无任何内容规则
2. 标题翻译有详细规则（禁止营销词、拉丁文、中文），描述翻译没有
3. 营销词、URL、联系方式、品牌名被忠实翻译到俄语
4. `ozon_validate_node.py:188` 拉丁文检测只查"纯拉丁文"，混合内容漏检

**修复方案**:
- 描述翻译 prompt 加规则（与标题翻译对齐）
- 加 `_sanitize_description()` 函数：翻译后二次净化
- 验证节点加强：描述中出现拉丁文即报错（不再要求"纯拉丁"才报）

**验证**: 检查修复后的描述是否通过 Ozon 审核

---

### 2.3 [P0] 体积重量不一致 — `ML_INCORRECT_VOLUME_WEIGHT`

**影响产品**: 1个（马桶药片架）

**根因链**:
1. `validation_retry_loop.py` 的 `repair_dimensions_node` 用固定密度 0.5 g/cm³ 从重量反算尺寸
2. **从不调整重量** — 如果源数据重量本身就是包装重量（含盒子），修复后体积重量仍不匹配
3. 密度 0.5 对塑料/金属制品（实际 ~0.8-1.2）严重偏低

**修复方案**:
- repair 后加一致性校验：`volume_weight = d*w*h/5000`，与实际重量比值 > 3x 时自动调整重量
- 密度从固定 0.5 改为按类目选择（至少提高到 0.8）

**验证**: 重新提交该产品，检查 ML_INCORRECT_VOLUME_WEIGHT 是否消失

---

### 2.4 [P0] 品牌未强制为"无品牌"

**影响范围**: 所有产品

**根因链**:
1. 品牌（attr=85）在 `_build_items_deterministically` 中被跳过（第805行）
2. 作为"缺失必填属性"进入字典搜索流程
3. 标题中如有"Lego"等品牌词，字典搜索会匹配到真实品牌
4. 品牌修正逻辑（第1123行）只修正 `dictionary_value_id == 0` 的情况
5. 有真实品牌 `dictionary_value_id > 0` 的不会被修正

**修复方案**:
- 品牌修正改为无条件强制为 `Нет бренда`（dictionary_value_id=126745801）
- 不管 `dictionary_value_id` 是什么，都覆盖

**验证**: 检查所有产品的品牌是否都为"Нет бренда"

---

### 2.5 [P1] 类目匹配不准

**影响范围**: 多个产品（已确认：狗牵引绳→儿童安全带、首饰配件→电气面板等）

**根因链（三层问题叠加）**:

| 层级 | 问题 | 文件 | 影响 |
|------|------|------|------|
| pg_trgm | 阈值 0.3 太高，中文多关键词查询相似度只有 0.06~0.11 | `db.py:89`, `init_data.py:34` | 返回 0 条结果，被迫走 ILIKE 兜底 |
| ILIKE 兜底 | 按单词 OR 匹配，太宽松 | `ozon_category_query.py:124-230` | "牵引绳"同时匹配"宠物牵绳"和"儿童牵引绳"，分数相同 |
| LLM 选择 | 位置偏差（锚定效应） | `category_match_v2_cfg.json` | 排在前面的候选被优先选中，无论是否正确 |

**修复方案**:
1. **pg_trgm 阈值**：从 0.3 降到 0.05（在 `init_data.py` 和 `db.py` 中 `SET pg_trgm.similarity_threshold = 0.05`）
2. **ILIKE 排序**：按关键词匹配率（匹配数/查询词总数）排序，而非原始计数
3. **同义词映射**：`_CN_SYNONYMS` 补充宠物类（牵引绳→宠物牵绳，狗绳→宠物牵绳等）
4. **LLM prompt**：加规则"适用对象为狗/猫/宠物时，优先选宠物用品类目"

**验证**: 用已知错误案例测试类目匹配准确率

---

### 2.6 [P2] BR_hashtag_validation（在售）

**影响产品**: 2个（水枪、迷你风扇）

**说明**: 产品仍在售，非阻断。需要进一步确认具体错误内容（可能是 hashtag 中含品牌名或格式问题）。

**修复方案**: 待确认具体错误后决定

---

### 2.7 [P2] warning_attribute_values_out_of_range（在售）

**影响产品**: 3个（书包、园艺手套、装饰花盆）

**说明**: 已有 retry 策略（强制刷新字典缓存），但未完全解决。产品仍在售。

**修复方案**: 待 P0/P1 完成后，分析具体属性值问题

---

## 3. 执行计划

### Phase 1: P0 修复（阻断性错误）

| # | 任务 | 文件 | 改动量 | 预计效果 |
|---|------|------|--------|---------|
| 1.1 | Fix `category_id` 变量名 | `assemble_ozon_product_node.py:1034` | 1行 | 7个火柴产品字典搜索恢复 |
| 1.2 | 字典值空值 fallback | `assemble_ozon_product_node.py:1053-1070` | ~30行 | 不再写入空值 |
| 1.3 | 品牌强制无品牌 | `assemble_ozon_product_node.py:1117-1126` | ~5行 | 所有产品品牌统一 |
| 1.4 | 描述翻译净化 | `prepare_ozon_upload_node.py` | ~60行 | 2个被拒产品修复 |
| 1.5 | 验证节点加强 | `ozon_validate_node.py` | ~15行 | 预防未来描述问题 |
| 1.6 | 体积重量一致性 | `validation_retry_loop.py` | ~25行 | 1个重量问题修复 |
| 1.7 | REPAIR_STRATEGY 补充 | `validation_retry_loop.py` | ~5行 | retry loop 覆盖更多错误 |

### Phase 2: P1 修复（类目匹配）

| # | 任务 | 文件 | 改动量 | 预计效果 |
|---|------|------|--------|---------|
| 2.1 | pg_trgm 阈值降到 0.05 | `init_data.py`, `db.py` | ~5行 | 正确类目进入候选列表 |
| 2.2 | ILIKE 排序优化 | `ozon_category_query.py` | ~20行 | 匹配率高的类目排前面 |
| 2.3 | 宠物类同义词补充 | `assemble_ozon_product_node.py` | ~15行 | 搜索信号增强 |
| 2.4 | LLM prompt 加规则 | `category_match_v2_cfg.json` | ~10行 | 消除位置偏差 |

### Phase 3: 重新提交 + 验证

| # | 任务 | 说明 |
|---|------|------|
| 3.1 | 重新部署 Worker | Docker rebuild + restart |
| 3.2 | 重新提交10个问题产品 | 通过 API 提交到 Worker |
| 3.3 | 检查修复结果 | 确认所有产品通过审核 |
| 3.4 | 抽查类目匹配 | 随机检查10个产品的类目是否合理 |

---

## 4. 技术细节

### 4.1 字典值回退策略（Fix 1.2）

```
缺失必填字典属性 →
  1. 标题搜索字典值（Ozon API /values/search, value=标题[:50]）
  2. 属性名搜索字典值（value=属性名[:30]）
  3. 取字典缓存第一条
  4. 仍无值 → 跳过该属性（不写空值，避免触发 error_attribute_values_empty）
```

### 4.2 描述净化规则（Fix 1.4）

翻译后执行以下净化：
- 移除拉丁文（`[a-zA-Z]{2,}`）
- 移除中文字符（`[\u4e00-\u9fff]`）
- 移除 URL（`https?://\S+`）
- 移除联系方式（电话、邮箱模式）
- 移除营销词（爆款、热销、新品、促销、跨境、亚马逊等）
- 长度限制：500~2000 字符

### 4.3 体积重量一致性校验（Fix 1.6）

```python
# repair_dimensions_node 修复后
recalc_vw = (depth * width * height) / 5000.0
ratio = weight_g / recalc_vw if recalc_vw > 0 else 0
if ratio > 3.0 or ratio < 0.33:
    # 重量与体积严重不匹配，调整重量
    item["weight"] = str(int(recalc_vw))
```

### 4.4 pg_trgm 阈值调整（Fix 2.1）

```sql
-- init_data.py 和 db.py 中添加
ALTER DATABASE ozon SET pg_trgm.similarity_threshold = 0.05;
-- 或在连接级别
SET pg_trgm.similarity_threshold = 0.05;
```

### 4.5 ILIKE 排序优化（Fix 2.2）

当前排序：关键词匹配数 + 深度加分
改为：关键词匹配率（匹配数 / 查询词总数）+ 深度加分

```python
# 当前
score = match_count + depth_bonus

# 改为
score = (match_count / total_keywords) * 2.0 + depth_bonus
```

---

## 5. 风险评估

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|---------|
| pg_trgm 阈值过低导致噪声候选增多 | 中 | LLM 选择负担增加 | 设置 top_k=15 限制候选数 |
| 描述净化过度删除有用内容 | 低 | 描述信息不足 | 只删明确的违规内容，保留产品描述 |
| 品牌强制无品牌影响品牌类产品 | 低 | 品牌信息丢失 | 当前所有产品都是无品牌白牌，不影响 |
| 重量调整后仍被 ML 标记 | 低 | 单个产品失败 | 加密度分类映射，提高精度 |

---

## 6. 验收标准

- [ ] 7个火柴类产品状态从"Не продается"变为"Продается"
- [ ] 2个 DESCRIPTION_DECLINE 产品通过审核
- [ ] 1个 ML_INCORRECT_VOLUME_WEIGHT 产品通过审核
- [ ] 所有产品品牌显示为"Нет бrenда"
- [ ] 类目匹配准确率提升（用10个已知错误案例验证）
- [ ] 新上架产品无 `error_attribute_values_empty` 错误
- [ ] 新上架产品无 DESCRIPTION_DECLINE 错误
