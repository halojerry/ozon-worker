# PRD: skill ↔ worker 契约统一与对齐

> 版本: v1.0 | 日期: 2026-07-15 | 状态: 待评审

---

## 1. 背景

`ozon-worker` 是一个两段式 Ozon 商品上架系统:

```
skill (本地, pounding-ozon-probe)          worker (云端, vibe-coding)
  1688 CDP 抓取                                  FastAPI HTTP 服务
  ↓                                               ↓
  组装 GraphInput 信封(envelope)                   Supabase 队列消费
  ↓                                               ↓
  只采集,不上架                                    LangGraph 全流程:
                                                  auth→ingest→类目→定价→属性
                                                  →AI生图→校验→上传→状态→自学习
```

本次审计发现 skill 产出的信封与 worker 的消费期望存在 3 项不一致,需要统一修正确保全流程顺畅。

---

## 2. 审计方法与验证

### 2.1 逐字段对比

对 skill 端 (`cloud_probe.py` 的 `build_graph_envelope`) 和 worker 端 (所有 28 个节点对 `draft/source/extensions` 的读取) 做了完整的逐字段对比审计。

### 2.2 1688 原始数据验证

直接抓取 1688 页面源码,提取 `productPackInfo` JSON 确认:

```
表头: 颜色 | 长(cm) | 宽(cm) | 高(cm) | 体积(cm³) | 重量(g)
数据: D8银色 | 6.5 | 4.3 | 1.5 | 41.925 | 50
```

**结论: 1688 包装表尺寸单位为 cm,重量单位为 g。skill 的 cm→mm ×10 转换正确。**

### 2.3 50 封蓝海信封验证

读取 `worker/assets/50_blue_ocean_products.json` (50 个已组装信封),dimensions 数值范围 10~480,全部符合 mm 语义,与 1688 cm 原始值 ×10 一致。

---

## 3. 契约差异清单

### 🔴 P0: variant.price 语义冲突

| | skill 端 | worker 端 |
|---|---|---|
| 文件:行 | `cloud_probe.py:803` | `pricing_node.py:232-233` |
| 当前行为 | `"price": round(vp * 3.5)` — **售价** | `var_cost_cny = var.get("price")` — 当**采购成本** |
| 后果 | 1688 SKU 成本 5.5 CNY → skill 输出 price=19 → worker 把 19 当成本再加价 → Ozon 价格是正确值的 ~3.5 倍 |
| 影响范围 | 仅多 SKU 商品 (单 SKU 走 `purchase_cost` 路径) |

**根因**: skill 做了定价职责,worker 也做了定价职责,两层加价叠加。

### 🟡 P1: 信封结构不统一

skill 输出**扁平结构** `{item_id, title, ...}`, worker 优先匹配**三层结构** `{draft:{...}, source:{...}, extensions:{...}}`。

虽然 worker 的 `ingest_node:60-65` 兼容扁平结构,但扁平结构下 `extensions={}`,用户无法通过信封透传定价参数:

| 参数 | worker 默认值 | 扁平结构 | 三层结构 |
|---|---|---|---|
| `margin_rate` | 0.25 (25%) | ❌ 不可覆盖 | ✅ extensions.margin_rate |
| `commission_rate` | 0.10 (10%) | ❌ | ✅ extensions.commission_rate |
| `fx_buffer` | 0.05 (5%) | ❌ | ✅ extensions.fx_buffer |

### 🟡 P2: variant.model 字段不一致

- skill 颜色分支 (`cloud_probe.py:800`) 写 `model`, sku_details 分支和兜底分支不写
- worker 不读取 `variant.model` → 不影响功能,但结构不统一

### 🟢 P3: variant.name 字段缺失

- skill 不输出 `variant.name`
- worker `variant_primary_loop_node:64` 读 `variant.name`,缺失时 fallback `f"variant_{idx}"`
- 不影响功能,仅日志可读性

### 🟢 P4: description 硬编码空串

- skill 永远写 `description=""` (`cloud_probe.py:834`)
- worker `prepare_ozon_upload_node:501` 回退到 `title`; `scene_generation_llm_node:40` 读到空串
- 影响较小,后续单独迭代

### ✅ 已验证对齐的字段

| 字段 | skill | worker 消费方式 | 对齐 |
|---|---|---|---|
| `title` | str | 10+ 节点读取 | ✅ |
| `images` | `list[str]` URL 数组 | ingest 只保留 str 元素 | ✅ |
| `attributes` | `dict[中文名→值]` | category_lookup 消费 | ✅ |
| `weight` | int, 克 | 两节点都当克用 | ✅ |
| `dimensions` | `{length,width,height}`, mm | 两节点都读 length/depth 别名 | ✅ |
| `purchase_cost` | float, CNY | pricing: `cost_cny or purchase_cost`; prepare: `purchase_cost` | ✅ |
| `purchase_url` | str | prepare 读, 回退 source.purchase_url | ✅ |
| `sku_id`/`offer_id` | str | prepare: `sku_id or offer_id` | ✅ |
| `ozon_category` | dict (graph 子命令恒缺) | category_lookup 回退 LLM | ✅ |
| `currency` | "CNY" | pricing 读 state.currency_code | ✅ |

---

## 4. 目标状态

### 4.1 统一三层信封格式

```json
{
  "token": "sk-...",
  "ozon_client_id": "4718259",
  "ozon_api_key": "cd1d0a10-...",
  "envelope": {
    "draft": {
      "item_id": "980815374096",
      "title": "宠物自动饮水器",
      "description": "",
      "category": "поилка",
      "ozon_category": {"description_category_id": "17028929", "type_id": "504866264"},
      "images": ["https://..."],
      "attributes": {"材质": "塑料"},
      "weight": 227,
      "dimensions": {"length": 140, "width": 80, "height": 10},
      "purchase_url": "https://detail.1688.com/...",
      "purchase_cost": 5.5,
      "supplier": "义乌市阔折塑料制品厂",
      "stock": 100,
      "shipping": {"origin": "浙江金华", "freightCny": 3, "carrier": "中通"},
      "currency": "CNY",
      "variants": [
        {"sku_id": "xxx_0", "name": "白色", "color": "白色", "model": "",
         "image": "https://...", "price": 5.5, "original_price": 5.5,
         "size": "one size", "stock": 100}
      ],
      "sku_id": "xxx_0",
      "price": 5.5,
      "original_price": 5.5
    },
    "source": {
      "purchase_url": "https://detail.1688.com/...",
      "purchase_cost": 5.5
    },
    "extensions": {
      "margin_rate": 0.25,
      "commission_rate": 0.10,
      "fx_buffer": 0.05
    }
  }
}
```

### 4.2 关键变更对照

| 变更项 | 当前 (扁平) | 目标 (三层) |
|---|---|---|
| draft 在哪 | `envelope` 本身 | `envelope.draft` |
| source | ingest 自动合成 | skill 显式提供 `envelope.source` |
| extensions | 空 `{}` | skill 显式提供 (定价参数透传) |
| **`variant.price`** | **售价 `round(cost × 3.5)`** | **采购成本 `vp` (1688 SKU 原始价, float)** |
| `variant.original_price` | 划线价 `round(cost × 4.5)` | 采购成本 `vp` (与 price 同值) |
| `variant.name` | 不存在 | 颜色名/SKU 名 |
| `variant.model` | 仅颜色分支有 | 所有分支统一写 (无值时 `""`) |
| 单 SKU `price`/`original_price` | 售价 `round(cost × 3.5/4.5)` | 采购成本 `vp` |

> **核心原则: skill 只负责采集,定价全权交给 worker `pricing_node`。skill 不再做任何 ×3.5 或 ×4.5 计算。**

---

## 5. 实施计划

### Phase 1: 改 skill (`cloud_probe.py`)

**文件**: `skill/scripts/cloud_probe.py`

#### 5.1.1 variant.price 语义修正 (行 790-828, 854-858)

三个 variant 构建分支 + 单 SKU 平铺:

```python
# 颜色分支: 原来是 round(vp * 3.5) / round(vp * 4.5)
"price": vp,
"original_price": vp,

# sku_details 分支: 原来是 round(float(sd.get("price", cost_cny)) * 3.5)
"price": float(sd.get("price", cost_cny)),
"original_price": float(sd.get("price", cost_cny)),

# 兜底分支: 原来是 round(cost_cny * 3.5)
"price": cost_cny,
"original_price": cost_cny,

# 单 SKU 平铺 (行 854-858):
envelope["sku_id"] = v0["sku_id"]
envelope["price"] = v0["price"]
envelope["original_price"] = v0["original_price"]
```

#### 5.1.2 添加 variant.name 字段

所有三个分支统一写:

```python
# 颜色分支:
"name": color,

# sku_details 分支:
"name": str(sd.get("name", "default")),

# 兜底分支:
"name": "default",
```

#### 5.1.3 统一 variant.model 字段

sku_details 和兜底分支补上缺失的 `model`:

```python
"model": "",  # 统一写,无值时为空字符串
```

#### 5.1.4 envelope 改为三层结构 (行 831-867)

将当前 `envelope: dict = {item_id, title, ...}` 改为 `{draft, source, extensions}`。

主要改动: 原 `envelope` 内容 → `draft`; 新增 `source` 和 `extensions`。

### Phase 1.5: 搜索 cloud_probe.py 其他读写点

需要检查的文件内函数:
- `build_envelope` (旧格式, 行 ~348) — 如还在用需同步
- `build_variant_envelope` (行 ~1295) — 同上
- `_graph_envelope_to_ctx` (行 ~995) — 读 envelope 字段的逻辑需兼容三层
- `submit_envelope` / `submit_task` — payload 组装

### Phase 2: worker 端验证 (不改代码)

worker 已完全就绪,无需修改。验证:

1. `ingest_node:52` — `"draft" in envelope` 优先匹配三层 ✅
2. `pricing_node:122-124` — 从 `state.extensions` 读定价参数 ✅
3. `pricing_node:232` — `variant.get("price")` 当成本,skill 改传成本后语义对齐 ✅

### Phase 3: 回归测试

1. **单 SKU**: `python3 scripts/cli.py graph --item-id <id> --category-query "<ru>"` → 检查三层结构 + price 为成本
2. **多 SKU**: 带颜色 SKU 商品 → 检查 variants 数组 price 为成本
3. **worker 端到端**: 提交到 `/submit_task`,确认全流程跑通, Ozon 价格合理
4. **蓝海信封**: 重新组装 1-2 个蓝海商品,确认格式正确

### Phase 4: 文档更新

1. `skill/README.md` — 更新输出格式示例
2. `AGENTS.md` — 更新信封契约描述
3. 团队沟通: variant.price 语义变更

---

## 6. 风险评估

| 风险 | 等级 | 缓解措施 |
|---|---|---|
| `cloud_probe.py` 其他函数还在读旧扁平结构 | 中 | Phase 1.5 全文件搜索 |
| 云端队列有旧格式任务 | 低 | worker ingest 已兼容扁平,新旧格式都能跑 |
| `50_blue_ocean_products.json` 等固定数据对不上新格式 | 低 | 这些是快照,不用改;新组装的信封自动用新格式 |
| variant.price 从售价改成本后,worker 定价可能偏低 | 低 | 这正是预期行为 — worker 统一加价,price 不应预加价 |

---

## 7. 不在本次范围的改进

| 项目 | 原因 |
|---|---|
| worker prepare_node `<50` 启发式 | 极小型商品概率极低,且 1688 已验证为 cm |
| `ozon_category` 在 graph 子命令补上 | worker LLM 匹配已够用,且需额外 Ozon API 调用 |
| `description` 从 CDP 提取 | 需修改 CDP 探针 JS,涉及面大 |
| `stock` 从 1688 取真实库存 | 1688 API 不暴露实时库存 |
