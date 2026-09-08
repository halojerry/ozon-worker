# PRD: Worker v7 — 双管线 + 跟卖 + 定价策略

> **版本**: v7.0
> **日期**: 2026-07-22
> **核心**: 完整管线(1688) + 跟卖管线(Ozon) + 定价策略 + 1688以图搜款

---

## 1. 双管线架构

```
Skill 接收 URL
    │
    ├── 1688 URL → 完整管线（现有）
    │     CDP探针 → GraphInput → Worker: 类目+属性+生图+定价+上传
    │
    └── Ozon URL → 跟卖管线（新增）
          import-by-sku → 卡片模板 + slug翻译 → 1688搜索 → 生图+定价+上传
```

---

## 2. 跟卖管线详细流程

```
Ozon URL
    │
    ▼
Step 1: 解析 URL
    product_id = 4397048531
    slug = "sushilka-dlya-salata-zeleni-1-yarus-3-l"
    │
    ▼
Step 2: import-by-sku(product_id) → 复制卡片模板
    获取: 标题 + 类目 + 属性(21个) + barcode
    不获取: 图片(防复制)
    │
    ▼
Step 3: LLM 翻译卡片标题 RU→CN
    "Сушилка для салата" → "沙拉脱水器 蔬菜甩干机"
    │
    ├──→ Step 4a: 1688 AK 文字搜索 → Top 10 匹配
    │         │
    │         ▼
    │    Step 4b: (可选) 1688 AK 以图搜款 → 更精准匹配
    │         │
    │         ▼
    │    Step 5: CDP 探针抓取最佳匹配 → 1688图片+成本+属性
    │
    ▼
Step 6: AI 生图
    白底图(1688商品图参考) → 9张营销图
    │
    ▼
Step 7: 定价
    采购成本 + 物流(真实费率) + 佣金(真实数据) + 利润率
    参考竞品价(/v5/product/info/prices) 确保有竞争力
    │
    ▼
Step 8: 上传
    /v3/product/import 带卡片模板的类目+属性+新图片
    → 出现在同一商品卡片下(同barcode)
```

---

## 3. 定价策略集成

### 3.1 用真实佣金率

**当前**: `commission_rate = 0.10`(硬编码)
**改进**: 从 `/v5/product/info/prices` 读取 rFBS 佣金率

```python
commissions = price_data.get("commissions", {})
# rFBS = 12%, FBO = 17%, FBS = 20.5%
commission_rate = commissions.get("sales_percent_rfbs", 12) / 100
first_mile = commissions.get("fbs_first_mile_min_amount", 0)
```

### 3.2 竞品价格参考

从 `price_indexes.external_index_data.minimal_price` 获取外部竞品最低价，确保定价有竞争力。

### 3.3 定价策略自动分配

为新上传产品调用 `/v1/pricing-strategy/product/info`，加入已有策略(2个已配置)。

---

## 4. 1688 以图搜款集成

### 4.1 工具位置

`/Users/halo/Downloads/1688-product-find-1.7.0/cli.py`

### 4.2 核心命令

```bash
# 以图搜款
python3 cli.py image_search --image "<1688图片URL>" --limit 10

# 智能对比(销量最高+价格最低+综合最优)
python3 cli.py compare --image "<URL>" --query "规格关键词"
```

### 4.3 Skill 集成方式

**方式: 子进程调用** (推荐 MVP)

```python
# skill/scripts/capabilities/image_search/service.py
def search_by_image(image_url: str, limit: int = 10):
    result = subprocess.run([
        "python3", "cli.py", "image_search",
        "--image", image_url, "--limit", str(limit)
    ], capture_output=True, text=True, cwd=PRODUCT_FIND_DIR)
    return json.loads(result.stdout)
```

### 4.4 使用场景

- **跟卖管线**: Ozon卡片标题翻译后 → 1688文字搜索 → 取搜索结果首图 → 以图搜款 → 找更精准的同款
- **选品管线**: 用户提供图片 → 直接以图搜款 → 找1688货源

---

## 5. 代码改动

### Skill 层

| 文件 | 内容 | 行数 |
|------|------|------|
| `cli.py` | `follow_sell` 命令 | ~40 |
| `cloud_probe.py` | `parse_ozon_url()` 增强 + `follow_sell_cloud()` | ~80 |
| `capabilities/image_search/service.py` | 1688 以图搜款封装 | ~50 |
| `capabilities/follow_sell/service.py` | 跟卖编排器 | ~150 |

### Worker 层

| 文件 | 内容 | 行数 |
|------|------|------|
| `pricing_node.py` | 真实佣金率 + 竞品价参考 | ~30 |
| `follow_sell_node.py` | 跟卖 payload 组装 | ~60 |

### 总计: ~410 行

---

## 6. 前置条件

| 条件 | 状态 |
|------|------|
| 1688 AK 配置 | ✅ 已配置 |
| 1688-product-find 部署 | ⚠️ 需部署依赖 |
| 物流费率表 | ✅ 已导入(141条) |
| 定价策略 | ✅ 已有2个策略 |

---

## 7. 验收标准

- [ ] `cli.py follow --ozon-url <url>` 可用
- [ ] import-by-sku 成功复制卡片模板
- [ ] LLM 翻译 + 1688 搜索找到匹配产品
- [ ] 1688 CDP 抓取成功
- [ ] AI 生图成功
- [ ] 定价使用真实佣金率
- [ ] 产品上传成功

## 8. 未包含(后续版本)

- 定价策略自动设置
- 1688 以图搜款(Phase 2，需要1688-product-find部署)
- wrong-volume 自动扫描修复
- 自动归档清理配额
