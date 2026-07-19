# PRD v3：SKU 变体类型感知 + SKU 数量控制 + 图片顺序 + 多店铺轮询

版本 3.0 | 2026-07-17 | 已实施

---

## 一、问题陈述

### 1.1 已修复的三个核心缺陷

| # | 缺陷 | 修复 |
|---|------|------|
| 1 | 所有变体被当作颜色 | 新增类型检测：颜色/尺码/规格/数量/未知 |
| 2 | SKU 组合爆炸无控制 | 默认 max_skus=15，多维采样+单维截断 |
| 3 | base item 的 primary_image 不在 images 首位 | `images = [chosen_primary] + remaining_images[:28]` |

### 1.2 1688 SKU 销量数据审计结论

**不可用。** 审计了全部数据源（搜索 API、详情 API、CDP 探针 DOM/React Fiber、35 个真实探针 JSON），均无 SKU 级销量字段。替代方案：利用 1688 自身的 SKU 排序（已按热度排列）取前 N 个。

---

## 二、实现内容

### Phase 1 — 图片顺序修复
- **文件**: `worker/src/graphs/nodes/prepare_ozon_upload_node.py:1281`
- **修改**: `images = [chosen_primary] + remaining_images[:28]`
- **影响**: 确保主图永远在 images 数组第一位

### Phase 2 — Skill 端变体类型检测
- **文件**: `skill/scripts/cloud_probe.py`
- **新增函数**:
  - `_detect_variant_type(name) -> str` — 对单个 SKU 名称做类型分类
  - `_parse_variant_attributes(name) -> dict` — 解析属性字典
  - `_detect_group_variant_type(values) -> str` — 对 option_group 投票检测类型
- **新增模式**: `_SIZE_PATTERN`, `_QUANTITY_PATTERN`, `_SPEC_KEYWORDS`
- **改造**: 变体组装逻辑（行 1057-1260）— 遍历所有 option_groups，笛卡尔积，标记 variant_type
- **新增**: SKU 数量截断（多维采样 + 单维截断），默认 max_skus=15

### Phase 3 — Worker 端分类路由 + 数量拆分
- **文件**: `worker/src/graphs/nodes/prepare_ozon_upload_node.py`
- **新增**: variant_type 检测和数量拆分逻辑
- **新增**: 尺寸属性映射到 Ozon 尺码属性（调用 `size_mapper.map_size_to_russian()`）
- **新增**: 规格变体 9048 值 per-variant 区分
- **数量拆分**: 每个数量变体 → 独立 offer_id，标题追 `"N шт."`，不绑 8292

### Phase 4 — 校验升级
- **文件**: `worker/src/graphs/nodes/ozon_validate_node.py`
- **改造**: Step 3 从"只检查颜色唯一"改为按 variant_type 分类校验
  - 颜色变体 → 颜色唯一性
  - 尺码变体 → 尺码唯一性
  - 规格变体 → 9048 唯一性
  - 数量变体 → 跳过校验

### Phase 5 — 多店铺轮询
- **文件**: `skill/scripts/cli.py`
- **新增**: `--max-skus N` 覆盖默认上限
- **新增**: `--strategy round-robin|even` 多店铺分配策略
- **新增**: `build_graph_envelope` 接受 `max_skus` 参数

---

## 三、新增数据结构

### 3.1 Variant Dict（向后兼容）

```json
{
  "sku_id": "1688_sku_001",
  "name": "5只装3cm【白色】USB款",
  "color": "白色",
  "model": "USB款",
  "size": "3cm",
  "attributes": {"颜色": "白色", "数量": "5只装", "尺寸": "3cm", "规格": "USB款"},
  "variant_type": "color_spec"
}
```

### 3.2 Extensions

```json
{
  "extensions": {
    "max_skus": 15,
    "dropped_skus": 12,
    "drop_reason": "multi-dim sampling: 3 dims, total=288 > max_skus=15"
  }
}
```

---

## 四、文件变更清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `skill/scripts/cloud_probe.py` | 重构 | 类型检测 + 属性解析 + 数量截断 + 变体组装改造 |
| `skill/scripts/cli.py` | 增强 | `--max-skus`、`--strategy`、round-robin |
| `worker/src/graphs/nodes/prepare_ozon_upload_node.py` | 修复+重构 | 图片顺序；分类路由；数量拆分；size_mapper 集成 |
| `worker/src/graphs/nodes/ozon_validate_node.py` | 增强 | 按类型校验 |
| `docs/PRD-v3.md` | 新增 | 本文档 |

---

## 五、测试结果

| 测试 | 结果 |
|------|------|
| 颜色检测: `["白色","黑色","红色"]` | ✅ `color` |
| 尺码检测: `["S","M","L","XL"]` | ✅ `size` |
| 数量检测: `["1PIC","2PIC","3PIC"]` | ✅ `quantity` |
| 规格检测: `["USB款","Type-C款"]` | ✅ `spec` |
| 混合检测: `"5只装3cm【白色】USB款"` | ✅ `quantity`（数量优先） |
| 属性解析: `"5只装3cm【白色】USB款"` | ✅ `{颜色:白色, 数量:5只装, 尺寸:3cm, 规格:USB款}` |
| 1688 万能筐: `["1片装+水泵棉","10片装+水泵棉"]` | ✅ `quantity` |
| 所有文件语法检查 | ✅ 4/4 通过 |
