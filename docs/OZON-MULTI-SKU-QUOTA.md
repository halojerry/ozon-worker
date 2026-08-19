# Ozon 多 SKU 上传与商品配额机制（调研结论）

> 生成：2026-08-20 · 基于 Ozon 官方 API 文档 + 竞品源码（毛子插件 maozier-plugin-3.2.2 / 上品帮插件 V3.2.2 + shopbang 客户端）+ 我们 worker 实现

---

## 一、核心结论：为什么多规格上传不占配额

**Ozon 官方机制**（`docs/ozon-api-docs-2026-07-05.json` 确认）：

> 9048 在 attributes 中。这些卡片除了大小或颜色外的所有属性都必须匹配。

**原理**：多个 SKU（变体）用**相同的 `model_id`（属性 9048 = 型号名称）**绑定，除颜色/尺寸外所有属性一致 → Ozon 自动合并为**一个商品卡片**（一个 product_id，下挂多个变体）→ **1 个商品卡 = 1 个商品配额**，与变体数量无关。

## 二、竞品的关键开关（配额消耗的真正控制点）

### 毛子插件（maozier-plugin-full.md:313）
```
合并变体 Switch（merge，默认 1 = 默认合并变体）
tooltip：「默认合并变体，如果选择否，则每个变体会单独上架」
```
- **merge=1（默认）**：合并 → 1 卡 = 1 配额
- **merge=0**：每个变体单独上架 → N 个 SKU = N 个配额（**配额爆炸**）
- 毛子 model_id：`mz-{随机15位}` 或用户填写（`maozier-plugin-full.md:239`）
- 变体上限：**30 条**

### 上品帮（shangpinbang-full.md:672-674）
- 变体特性校验：`至少应有一个与原始商品不同的特征，否则无法成功创建新的商品！`
- 变体上限：**80 个**

## 三、我们 worker 已对齐的实现

`worker/src/graphs/nodes/prepare_ozon_upload_node.py` 三种变体路由：

| 变体类型 | 判定 | 行为 | 配额 |
|---|---|---|---|
| **颜色/尺寸变体** | `variant_type` 非 quantity | 多个 items + **9048=item_id 绑定** → 合并 1 卡 | **1 配额** |
| **数量变体** | `variant_type="quantity"` | 每个 SKU 独立产品（不绑 8292） | N 配额 |
| 单 SKU | 无 variants | 普通 | 1 配额 |

关键实现：
- `prepare_ozon_upload_node.py:2273-2293`：9048 = item_id（确定性，重试不变，可溯源）
- `prepare_ozon_upload_node.py:2801+`：多 variant → 多 items，颜色属性（10096/10097 等）每个变体独立，共享属性移除颜色
- `prepare_ozon_upload_node.py:2731+`：数量变体走独立产品分支

## 四、我们 vs 竞品的差异（潜在优化点）

| 项 | 毛子 | 上品帮 | 我们 |
|---|---|---|---|
| 合并开关 | ✅ `merge` 显式开关 | ✅ 变体特性 | ❌ **无开关（默认恒合并）** |
| 变体上限 | 30 | 80 | 无上限（但受生图成本限制） |
| model_id | mz-随机/用户填 | 变体特性 | item_id（可溯源，更优） |

**潜在优化**：
1. **merge 开关**：我们恒合并，若用户要「每个 SKU 单独上架」（独立 listing 拿更多曝光），无控制项。可加 `extensions.merge_variants`（默认 true 合并）。
2. **变体上限校验**：竞品有 30/80 上限防 Ozon 拒单，我们无显式上限（Ozon 单请求 items ≤ 100 隐含限制）。

## 五、多 SKU 上传要点（Ozon 官方约束）

- 变体间只能**颜色或尺寸**不同，其他属性必须一致
- 每个变体独立 `offer_id` / `price` / `primary_image`
- 颜色属性 ID 动态（10096/10097/10098/10099），按类目检测
- 单请求 items 上限 100
