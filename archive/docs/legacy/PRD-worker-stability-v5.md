# PRD: Worker 稳定性改进计划 v5 — 上传前预检

> **版本**: v5.0
> **日期**: 2026-07-21
> **核心思路**: 把所有问题发现提前到上传之前，不再依赖上传后的 retry

---

## 1. 问题根源

**当前流程**：组装 → 生图(耗时) → 上传 → Ozon报错 → retry修复(3次) → 仍失败=浪费

**根本问题**：Ozon 提供了 `/v1/product/validate` 预检 API，但我们**只在上传失败后的 retry 循环里调用**，从来没有在上传前调用过。

---

## 2. 关键发现：`/v1/product/validate`

**代码位置**：`validation_retry_loop.py` line 1426, `revalidate_node()`

这个 API 的作用：
- 接收完整 payload，返回 Ozon 会发现的所有错误
- **不创建产品**，不消耗上传配额
- 返回的错误格式与 `/v3/product/import` 完全一致

**当前调用时机**：上传 → Ozon报错 → retry loop → 修复 → 调用 validate 验证修复 → 重新上传

**应该的调用时机**：组装完成 → **调用 validate 预检** → 通过才上传

---

## 3. 预检方案：增强 `ozon_validate_node`

`ozon_validate_node` 位于 `prepare_ozon_upload` 和 `ozon_upload` 之间，是天然的预检门。

### Check 1: 调用 Ozon `/v1/product/validate` [P0 - 关键]

**现状**：只在 retry 循环中调用
**改进**：在 `ozon_validate_node` 中首次调用

```python
# 在现有本地检查之后，调用 Ozon 预检 API
validate_resp = ozon_post(
    client_id, api_key,
    "/v1/product/validate",
    ozon_payload
)
# 解析错误，与本地检查合并
```

**效果**：捕获 ~60% 的上传失败（schema 错误、缺失属性、字典值问题）

### Check 2: 图片 URL 可达性 [P0]

**现状**：COS URL 直接传给 Ozon，部分失败
**改进**：HEAD 请求检查每个 URL（取样前3张+主图）

```python
for url in sample_urls:
    resp = requests.head(url, timeout=5)
    if resp.status_code >= 400:
        failed_urls.append(url)
```

**效果**：提前发现所有 pics_http_error

### Check 3: 危化品关键词扫描 [P1]

**现状**：打火机上传后才发现
**改进**：扫描产品名+描述中的关键词（зажигалка/打火机/火柴 等）

```python
FIRE_HAZARD_KEYWORDS = [
    "зажигалка", "спички", "打火机", "火柴", "点火器"
]
```

**效果**：100% 拦截管制品

### Check 4: 字典值预验证 [P1]

**现状**：`dictionary_value_id > 0` 就通过，但可能是错的
**改进**：调用 `/v1/description-category/attribute/values/search` 验证

**效果**：减少 `warning_attribute_values_out_of_range`

### Check 5: 类目维度限制 [P1]

**现状**：不知道类目有 min/max weight 要求
**改进**：从 Ozon 属性 schema 或经验表中查询类目限制

**效果**：提前发现 INCORRECT_DIMENSION

### Check 6: 中文字符全量扫描 [P2]

**现状**：`ozon_validate_node` 已扫描但只记录
**改进**：发现中文 → 自动翻译 → 翻译失败则阻断

**效果**：100% 拦截 BR_chinese_hieroglyphs

---

## 4. 预检分类

### 阻断上传（is_valid=False）

| 阻断条件 | Check |
|---------|-------|
| Ozon validate API 返回 error 级别错误 | Check 1 |
| 所有图片 URL 不可访问 | Check 2 |
| 检测到危化品关键词 | Check 3 |
| 中文翻译失败且无法清除 | Check 6 |
| 必填属性缺失且无默认值 | 现有 |
| 类目一致性严重失败 | Fix 1(v4) |

### 警告放行（允许上传，记录日志）

| 警告条件 | Check |
|---------|-------|
| 部分图片 URL 不可访问 | Check 2 |
| 类目一致性弱匹配 | 现有 |
| 密度稍低 | 现有 |
| 字典值不在推荐范围 | Check 4 |

---

## 5. 改动量

| 改动 | 文件 | 行数 | 复杂度 |
|------|------|------|--------|
| 调用 `/v1/product/validate` | `ozon_validate_node.py` | ~50行 | 低 |
| 图片 URL 可达性检查 | `ozon_validate_node.py` | ~40行 | 低 |
| 危化品关键词扫描 | `ozon_validate_node.py` | ~30行 | 低 |
| 字典值预验证 | `assemble_ozon_product_node.py` | ~30行 | 中 |
| 类目维度限制表 | `prepare_ozon_upload_node.py` | ~60行 | 中 |
| 中文全量扫描增强 | `ozon_validate_node.py` | ~20行 | 低 |
| **合计** | | **~230行** | |

---

## 6. 预期效果

| 指标 | v4（当前） | v5（预期） |
|------|----------|-----------|
| 成功率 | ~30-55% | **~80-90%** |
| Ozon 预检捕获 | 0%（未用） | ~60% |
| 图片问题提前发现 | 0% | ~95% |
| 管制品拦截 | 0% | 100% |
| retry 循环触发 | 频繁 | 极少 |

---

## 7. 验收标准

- [ ] `ozon_validate_node` 在上传前调用 `/v1/product/validate`
- [ ] Ozon validate 返回的错误被解析并加入 `validation_errors`
- [ ] 图片 URL 不可达时阻断上传
- [ ] 危化品关键词检测到后阻断上传
- [ ] 字典值在调用 validate 前预验证
- [ ] 类目维度限制在组装前检查
- [ ] 修复后重新测试 10+ 产品，成功率 > 80%
