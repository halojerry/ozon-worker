# PRD: Worker 稳定性改进计划 v6 — Ozon API 深度集成

> **版本**: v6.0
> **日期**: 2026-07-21
> **核心发现**: 12个未使用的 API + attribute schema 深度检查

---

## 1. 发现总览

### 1.1 配额管理（P0 紧急）

| API | 用途 | 状态 |
|-----|------|------|
| `/v4/product/info/limit` | 查询每日上传额度 + 总产品上限 | ⚠️ **当前863/1000，仅剩137个位置** |
| `/v1/product/archive` | 归档失败产品，释放配额 | 已有710个归档，需自动清理 |

### 1.2 增量修复（P1）

| API | 用途 | 替代方案 |
|-----|------|---------|
| `/v1/product/pictures/import` | 增量上传/替换图片 | 替代全量 `/v3/product/import` |
| `/v1/product/attributes/update` | 增量更新属性值 | 替代全量重新导入 |
| `/v1/product/info/wrong-volume` | 查询体积/重量异常产品 | 主动发现 ML 问题 |

### 1.3 属性 schema 深度检查（P1）

**当前使用**: `is_required`, `dictionary_id`
**未使用**:
| 字段 | 含义 | 预检价值 |
|------|------|---------|
| `type` | String/Decimal/Dict | 验证值类型是否正确 |
| `description` | 格式说明（如"只用整数或小数，分隔符是点"） | 验证值格式 |
| `max_value_count` | 最大可设置的值数量 | 防止超限 |
| `category_dependent` | 是否依赖类目 | 跨类目时需重新取值 |

### 1.4 图片独立管理（P1）

| API | 用途 |
|-----|------|
| `/v1/product/pictures/import` | 上传/替换图片 |
| `/v2/product/pictures/info` | 查询图片状态和错误 |

**最大改进**: 图片失败不需要重新导入整个产品。

### 1.5 产品库管理（P2）

| API | 用途 |
|-----|------|
| `/v4/product/info/stocks` | 新版库存（替代废弃的v3） |
| `/v1/product/archive` + `/unarchive` | 归档/恢复产品 |
| `/v1/product/update/offer-id` | 修改offer_id不移除产品 |

---

## 2. 类目/属性预检 — API 覆盖面分析

### 2.1 属性 schema 能告诉我们的

```
/v1/description-category/attribute 返回每个属性:
├── id: 属性ID
├── name: 名称
├── description: 说明（含格式要求）
├── type: String | Decimal | Dict
├── is_required: 是否必填
├── dictionary_id: 字典ID（0=自由文本）
├── max_value_count: 最多几个值
├── category_dependent: 是否依赖类目
└── is_aspect: 是否为特征属性
```

**可预检项**:
1. ✅ `is_required=true` 的属性必须存在 → 已做
2. ✅ `dictionary_id>0` 的属性必须有 valid dictionary_value_id → 已做
3. ⚠️ `type="Decimal"` 的值必须是数字 → **未做**
4. ⚠️ `description` 含"只用整数"→ 值必须是整数 → **未做**
5. ⚠️ `max_value_count>0` → 值的数量不能超过 → **未做**
6. ⚠️ `category_dependent=true` → 换类目需重新取值 → **未做**

### 2.2 属性 schema 不能告诉我们的

- `min_value` / `max_value` → **不在 schema 中**，Ozon 服务端校验
- `min_length` / `max_length` → **不在 schema 中**
- 类目的 min/max weight/dimension → **无 API 返回**

**结论**: 属性 schema 可以做类型和格式层面的预检，但**数值范围、长度限制是 Ozon 服务端独有的**，无法本地预检。

---

## 3. 执行计划

| 顺序 | Fix | 影响 | 改动量 |
|------|-----|------|--------|
| **1** | `/v4/product/info/limit` 上传前检查 | 防止超配额 | 30行 |
| **2** | attributes/update + pictures/import 替代全量 re-import | retry 更快更安全 | 40行 |
| **3** | attribute schema type/max_value_count 预检 | 预检更多属性错误 | 30行 |
| **4** | wrong-volume 主动扫描 | 发现 ML 问题 | 20行 |
| **5** | archive 自动清理失败产品 | 释放配额 | 30行 |

## 4. 验收标准

- [ ] 上传前检查 `/v4/product/info/limit`，接近上限时警告
- [ ] retry 修复属性时用 attributes/update 而非全量 re-import
- [ ] 图片修复用 pictures/import 而非全量 re-import
- [ ] 属性 schema type 字段用于值类型预检
- [ ] 自动归档失败产品释放配额
