# PRD v2：Ozon 上架错误修复

版本 2.0 | 2026-07-17 | 基于 5 产品实测 + Ozon API 返回

---

## 一、实测错误全景

5 个产品上架到 Ozon，全部有 BLOCKING 或 WARNING 错误，无一可正常售卖。

| # | 产品 | product_id | BLOCK | WARN | 核心问题 |
|---|------|-----------|-------|------|---------|
| 1 | 猫薄荷玩具 | 5536034067 | 0 | 2 | 包装重量 "25g" 带单位后缀 |
| 2 | 糖果喷壶 | 5536035155 | 1 | 0 | 尺寸/重量 ML 异常（默认 100×100×50mm 不真实） |
| 3 | EVA泡沫球 | 5536040818 | 1 | 0 | 标题含中文（翻译失败回退） |
| 4 | 逗猫棒 | 5536184394 | 1 | 2 | 单位数量为空 + 重量 "19g" 带单位 |
| 5 | 狗狗绳结 | 5536254672 | 1 | 0 | 标题含拉丁字母 "PET REMBER" |

---

## 二、根因分析

### 2.1 拉丁字母标题 — `DESCRIPTION_DECLINE` attr=4180

**根因**：1688 标题含英文品牌名（如 "PET REMBER"），LLM 翻译时保留了它。Ozon 要求标题 **100% 西里尔字母**。

```
输入：PET REMBER跨境狗狗拆解绳结玩具马克龙色ins风狗狗啃咬解闷玩具
翻译：PET REMBER ins  ← LLM 保留了英文
Ozon：❌ Название товара не может быть на латинице
```

**修复**：翻译后增加拉丁字符检测 → 转写为西里尔字母或移除。

### 2.2 中文标题 — `DESCRIPTION_DECLINE` attr=22508

**根因**：LLM 翻译返回非西里尔文字时，代码回退到中文原文。Ozon 拒绝。

```
LLM 输出：EVA泡沫球宠物猫咪玩具球...  （非西里尔）
当前行为：return text  → 传中文给 Ozon
Ozon：❌ В карточке товара содержатся иероглифы
```

**修复**（已部署）：翻译失败时用简化 prompt 重试一次。

### 2.3 重量属性非数字 — `VALUE_MUST_BE_DECIMAL` attr=4383/4497

**根因**：1688 属性值如 "25g"、"19g" 直接传给 Ozon。Ozon 期望纯数字。

```
1688 属性：重量 = "25g"
当前行为：直接 set attr 4497 = "25g"
Ozon：❌ Заполните атрибут числом
```

**修复**（已部署）：去除 `g/kg/克/斤/г/кг` 等后缀，只保留数字。

### 2.4 单位数量为空 — `error_attribute_values_empty` attr=8962

**根因**：确定性组装时添加了 attr 8962 但 values 为空数组 `[]`，兜底逻辑只判断属性是否存在，不判断值是否为空。

```
当前：attr 8962 存在 → 跳过兜底 → values=[] 
Ozon：❌ Это обязательное поле
```

**修复**（已部署）：不仅检查属性是否存在，还检查 values 是否为空。

### 2.5 体积重量 ML 异常 — `ML_INCORRECT_VOLUME_WEIGHT`

**根因**：1688 CDP 未获取到真实尺寸时，代码使用默认值 `100×100×50mm`。200g 产品用这个尺寸导致密度计算异常。

```
产品：糖果喷壶 200g
尺寸：100×100×50mm（默认值，非真实）
密度：200g / 0.0005m³ = 400 kg/m³
Ozon ML：❌ Габариты или вес сильно отличаются от похожих товаров
```

**修复**：CDP 增加尺寸提取优先级；或根据重量反推合理默认尺寸。

### 2.6 图片优先级 — 主图用变体图、变体用 1688 原图

**修复**（已部署）：
- 主产品 primary_image：`main_image` 优先（统一主图）
- 变体 primary_image：`variant_primary_images[i]`（白底图）优先

### 2.7 Ozon 轮询超时 — `moderate_status HTTP 400`

**根因**：Ozon 异步分配 product_id，worker 10 轮询（~30s）不够。期间 product_id=0，查询 moderate_status 返回 400。

**修复**：增加轮询次数 10→20，或延长轮询间隔 3s→5s。

---

## 三、修复清单

| # | 修改 | 文件 | 状态 |
|---|------|------|------|
| 1 | 拉丁字母标题 → 转写/清除 | prepare_ozon_upload_node.py | 🔴 待修复 |
| 2 | 翻译失败 → 简化 prompt 重试 | prepare_ozon_upload_node.py | ✅ 已部署 |
| 3 | 重量值去单位后缀 | prepare_ozon_upload_node.py | ✅ 已部署 |
| 4 | attr 8962 空值兜底 | prepare_ozon_upload_node.py | ✅ 已部署 |
| 5 | CDP 尺寸提取 + 合理默认值 | cloud_probe.py / CDP | 🔴 待修复 |
| 6 | 主产品 primary_image → main_image | prepare_ozon_upload_node.py | ✅ 已部署 |
| 7 | 变体 primary_image → 白底图 | prepare_ozon_upload_node.py | ✅ 已部署 |
| 8 | 轮询次数 10→20 | ozon_status_node.py | 🔴 待修复 |

---

## 四、详细实现

### 4.1 拉丁字符转写（`_sanitize_title` 后新增）

```python
def _transliterate_latin_to_cyrillic(title: str) -> str:
    """检测并转写标题中的拉丁字符为西里尔字母。
    
    Ozon 要求标题 100% 西里尔 — 连一个拉丁字母都不能有。
    策略：检测到拉丁单词 → LLM 转写 → 兜底移除。
    """
    import re
    latin_words = re.findall(r'[a-zA-Z]{2,}', title)
    if not latin_words:
        return title
    
    # LLM 转写（保留品牌发音）
    ...
    # 兜底：直接移除拉丁词
    for w in latin_words:
        title = title.replace(w, '').replace('  ', ' ').strip()
    return title
```

### 4.2 CDP 尺寸提取增强

`cloud_probe.py` 中 `enrich_product_with_cdp` 增加尺寸提取优先级：
1. 页面 JSON-LD 结构化数据 → dimensions
2. 属性表格中 "长/宽/高" 行
3. 包装信息中 "尺寸" 行
4. 根据重量 + 品类反推默认尺寸（如 200g 喷壶 ≈ 80×80×220mm）

### 4.3 轮询优化

`ozon_status_node.py`：
- 轮询次数: 10 → 20
- 轮询间隔: 3s → 5s
- 总超时: ~30s → ~100s
